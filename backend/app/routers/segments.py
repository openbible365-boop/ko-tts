import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app import storage
from app.deps import CurrentUser, SessionDep, require_role
from app.models import Recording, RecordingStatus, Segment, SegmentStatus, User, UserRole
from app.schemas import (
    PresignedUrlResponse,
    RecordComplete,
    SegmentCorrect,
    SegmentRead,
    SegmentReject,
)

router = APIRouter(prefix="/segments", tags=["segments"])

DOWNLOAD_TTL = 3600
RECORD_URL_TTL = 3600  # 录音逐行上传的预签名 PUT 有效期(秒)

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
    stmt = select(Segment)
    if seg_status:
        stmt = stmt.where(Segment.status == seg_status)
    if recording_id:
        stmt = stmt.where(Segment.recording_id == recording_id)
    if not _is_staff(user):
        stmt = stmt.join(Recording, Segment.recording_id == Recording.id).where(
            Recording.uploaded_by == user.id
        )
    if recording_id:
        # 单条录音的所有段是一个有界工作集(=范文行数), 录音/详情页需要全量、
        # 且要按行号稳定排序 —— 不能用 created_at(批量插入时并列, 排序不确定)
        # 再套 limit=50, 否则几百行的范文只会随机显示其中 50 行, 录完还会"时隐时现"。
        stmt = stmt.order_by(Segment.segment_index)
    else:
        # 审核/校对列表: 分页返回
        stmt = stmt.order_by(Segment.created_at).limit(limit).offset(offset)
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


# ==================== 录音样品: 逐行录音 (owner 或 admin) ====================
async def _load_sample_segment(
    segment_id: uuid.UUID, user: User, session
) -> tuple[Segment, Recording]:
    """加载录音样品的切片 + 父录音; 校验是样品且 owner/admin 可访问。"""
    seg = await session.get(Segment, segment_id)
    if seg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segment not found")
    rec = await session.get(Recording, seg.recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    if rec.script_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not a recording sample")
    if not (rec.uploaded_by == user.id or user.role == UserRole.admin.value):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    return seg, rec


async def _refresh_sample_status(session, rec: Recording) -> None:
    """有未录的行 -> recording(录音中); 否则 recorded(录音完毕)。"""
    pending = await session.scalar(
        select(func.count())
        .select_from(Segment)
        .where(
            Segment.recording_id == rec.id,
            Segment.status == SegmentStatus.pending_recording.value,
        )
    )
    rec.status = (
        RecordingStatus.recording.value if pending else RecordingStatus.recorded.value
    )


@router.post("/{segment_id}/record-url", response_model=PresignedUrlResponse)
async def record_url(
    segment_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> PresignedUrlResponse:
    """发逐行录音的预签名 PUT URL; 客户端 PUT 音频后再调 record-complete。"""
    seg, _rec = await _load_sample_segment(segment_id, user, session)
    if seg.status == SegmentStatus.transcribing.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "Segment is transcribing")
    key = storage.recording_line_key(seg.id)
    seg.audio_key = key
    await session.commit()
    url = await storage.presigned_put_url(key, expires_in=RECORD_URL_TTL)
    return PresignedUrlResponse(url=url, expires_in=RECORD_URL_TTL)


@router.post("/{segment_id}/record-complete", response_model=SegmentRead)
async def record_complete(
    segment_id: uuid.UUID, data: RecordComplete, user: CurrentUser, session: SessionDep
) -> Segment:
    """确认逐行音频已入 R2 -> 置 pending_transcription(worker 转写+比对范文)。"""
    seg, rec = await _load_sample_segment(segment_id, user, session)
    if not seg.audio_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Call record-url first")
    head = await storage.head_object(seg.audio_key)
    if head is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload not found in storage")
    if data.duration_ms is not None:
        seg.duration_ms = data.duration_ms
        seg.end_ms = data.duration_ms
    # 重置上一轮的识别/审核痕迹, 重新进入转写队列
    seg.asr_text = None
    seg.reviewed_by = None
    seg.reviewed_at = None
    seg.rejection_reason = None
    seg.status = SegmentStatus.pending_transcription.value
    await session.flush()
    await _refresh_sample_status(session, rec)
    await session.commit()
    await session.refresh(seg)
    return seg


@router.post("/{segment_id}/pass", response_model=SegmentRead)
async def pass_segment(
    segment_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Segment:
    """录音样品: 人工「通过」(从标红待判的 pending_review 强制通过)。"""
    seg, _rec = await _load_sample_segment(segment_id, user, session)
    if seg.status not in {
        SegmentStatus.pending_review.value,
        SegmentStatus.transcription_failed.value,
    }:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot pass in status={seg.status}")
    seg.reviewed_by = user.id
    seg.reviewed_at = datetime.now(UTC)
    seg.rejection_reason = None
    seg.status = SegmentStatus.approved.value
    await session.commit()
    await session.refresh(seg)
    return seg


@router.post("/{segment_id}/rerecord", response_model=SegmentRead)
async def rerecord_segment(
    segment_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Segment:
    """录音样品: 「重录」——清掉该行音频与识别结果, 退回未录(样品退回录音中)。"""
    seg, rec = await _load_sample_segment(segment_id, user, session)
    if seg.status == SegmentStatus.transcribing.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "Segment is transcribing")
    old_key = seg.audio_key
    seg.audio_key = None  # text(范文期望文字)保留, 只清音频与识别
    seg.asr_text = None
    seg.duration_ms = 0
    seg.end_ms = 0
    seg.reviewed_by = None
    seg.reviewed_at = None
    seg.rejection_reason = None
    seg.status = SegmentStatus.pending_recording.value
    await session.flush()
    await _refresh_sample_status(session, rec)
    await session.commit()
    if old_key:
        try:
            await storage.delete_object(old_key)
        except Exception:  # noqa: BLE001
            pass
    await session.refresh(seg)
    return seg
