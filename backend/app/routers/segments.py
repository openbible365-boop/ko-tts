import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app import storage
from app.deps import CurrentUser, SessionDep, require_role
from app.models import Recording, Segment, SegmentStatus, User, UserRole
from app.schemas import (
    PresignedUrlResponse,
    SegmentCorrect,
    SegmentRead,
    SegmentReject,
)

router = APIRouter(prefix="/segments", tags=["segments"])

DOWNLOAD_TTL = 3600

# 审核/删除要求 reviewer 或 admin; 校对放开给 contributor(但限本人录音, 见
# correct_segment 里的 _load_with_access)。
StaffOnly = Annotated[User, Depends(require_role(UserRole.reviewer, UserRole.admin))]


def _is_staff(user: User) -> bool:
    return user.role in (UserRole.reviewer.value, UserRole.admin.value)


async def _load_with_access(segment_id: uuid.UUID, user: User, session) -> Segment:
    seg = await session.get(Segment, segment_id)
    if seg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segment not found")
    if _is_staff(user):
        return seg
    # contributor 只能看自己上传的录音的 segments
    rec = await session.get(Recording, seg.recording_id)
    if rec is None or rec.uploaded_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    return seg


# ---- 读 ----
@router.get("", response_model=list[SegmentRead])
async def list_segments(
    user: CurrentUser,
    session: SessionDep,
    seg_status: Annotated[str | None, Query(alias="status", max_length=32)] = None,
    recording_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Segment]:
    stmt = (
        select(Segment)
        .order_by(Segment.created_at)
        .limit(limit)
        .offset(offset)
    )
    if seg_status:
        stmt = stmt.where(Segment.status == seg_status)
    if recording_id:
        stmt = stmt.where(Segment.recording_id == recording_id)
    if not _is_staff(user):
        stmt = stmt.join(Recording, Segment.recording_id == Recording.id).where(
            Recording.uploaded_by == user.id
        )
    return list(await session.scalars(stmt))


@router.get("/{segment_id}", response_model=SegmentRead)
async def get_segment(
    segment_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Segment:
    return await _load_with_access(segment_id, user, session)


@router.get("/{segment_id}/download-url", response_model=PresignedUrlResponse)
async def segment_download_url(
    segment_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> PresignedUrlResponse:
    seg = await _load_with_access(segment_id, user, session)
    if not seg.audio_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segment has no audio clip")
    url = await storage.presigned_get_url(seg.audio_key, expires_in=DOWNLOAD_TTL)
    return PresignedUrlResponse(url=url, expires_in=DOWNLOAD_TTL)


# ---- 校对 (pending_correction|rejected -> pending_review) ----
_CORRECTABLE = {
    SegmentStatus.pending_correction.value,
    SegmentStatus.rejected.value,
}


@router.post("/{segment_id}/correct", response_model=SegmentRead)
async def correct_segment(
    segment_id: uuid.UUID,
    data: SegmentCorrect,
    user: CurrentUser,
    session: SessionDep,
) -> Segment:
    # 校对放开给 contributor, 但 _load_with_access 保证 contributor 只能改
    # 自己上传录音的切片(reviewer/admin 不受限)。审核/导出仍是 StaffOnly。
    seg = await _load_with_access(segment_id, user, session)
    if seg.status not in _CORRECTABLE:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Cannot correct in status={seg.status}"
        )
    seg.text = data.text
    seg.corrected_by = user.id
    seg.corrected_at = datetime.now(UTC)
    seg.status = SegmentStatus.pending_review.value
    await session.commit()
    await session.refresh(seg)
    return seg


# ---- 审核 (pending_review -> approved|rejected) ----
_REVIEWABLE = {SegmentStatus.pending_review.value}
# 退回还可作用于已通过的段(退回到 rejected 以便再改)
_REJECTABLE = {SegmentStatus.pending_review.value, SegmentStatus.approved.value}


@router.post("/{segment_id}/approve", response_model=SegmentRead)
async def approve_segment(
    segment_id: uuid.UUID, user: StaffOnly, session: SessionDep
) -> Segment:
    seg = await session.get(Segment, segment_id)
    if seg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segment not found")
    if seg.status not in _REVIEWABLE:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Cannot approve in status={seg.status}"
        )
    seg.reviewed_by = user.id
    seg.reviewed_at = datetime.now(UTC)
    seg.rejection_reason = None  # 审过又退回再 approve 时, 清空旧的拒绝理由
    seg.status = SegmentStatus.approved.value
    await session.commit()
    await session.refresh(seg)
    return seg


@router.post("/{segment_id}/reject", response_model=SegmentRead)
async def reject_segment(
    segment_id: uuid.UUID,
    data: SegmentReject,
    user: StaffOnly,
    session: SessionDep,
) -> Segment:
    seg = await session.get(Segment, segment_id)
    if seg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segment not found")
    if seg.status not in _REJECTABLE:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Cannot reject in status={seg.status}"
        )
    seg.reviewed_by = user.id
    seg.reviewed_at = datetime.now(UTC)
    seg.rejection_reason = data.rejection_reason
    seg.status = SegmentStatus.rejected.value
    await session.commit()
    await session.refresh(seg)
    return seg


@router.delete("/{segment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_segment(
    segment_id: uuid.UUID, user: StaffOnly, session: SessionDep
) -> None:
    """删除单个切片(R2 clip + DB 行)。reviewer/admin 可删。"""
    seg = await session.get(Segment, segment_id)
    if seg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segment not found")
    if seg.audio_key:
        try:
            await storage.delete_object(seg.audio_key)
        except Exception:  # noqa: BLE001  R2 删除失败不阻断 DB 清理
            pass
    await session.delete(seg)
    await session.commit()
