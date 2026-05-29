import os
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app import storage
from app.deps import CurrentUser, SessionDep
from app.models import Recording, RecordingStatus, Segment, User, UserRole
from app.schemas import (
    PresignedUrlResponse,
    RecordingCreate,
    RecordingCreateResponse,
    RecordingRead,
    SegmentRead,
)

router = APIRouter(prefix="/recordings", tags=["recordings"])

UPLOAD_URL_TTL = 3600  # 预签名 URL 有效期(秒)
_EXT_RE = re.compile(r"\.[a-z0-9]{1,10}")


def _safe_ext(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return ext if _EXT_RE.fullmatch(ext) else ""


def _is_staff(user: User) -> bool:
    return user.role in (UserRole.reviewer.value, UserRole.admin.value)


def _can_access(user: User, rec: Recording) -> bool:
    return _is_staff(user) or rec.uploaded_by == user.id


@router.post("", response_model=RecordingCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_recording(
    data: RecordingCreate, user: CurrentUser, session: SessionDep
) -> RecordingCreateResponse:
    rec_id = uuid.uuid4()
    key = storage.recording_key(rec_id, _safe_ext(data.original_filename))
    rec = Recording(
        id=rec_id,
        uploaded_by=user.id,
        audio_key=key,
        original_filename=data.original_filename,
        mime_type=data.mime_type,
        content_category=data.content_category.value,
        title=data.title,
        notes=data.notes,
        status=RecordingStatus.pending_upload.value,
    )
    session.add(rec)
    await session.commit()
    await session.refresh(rec)

    upload_url = await storage.presigned_put_url(key, expires_in=UPLOAD_URL_TTL)
    return RecordingCreateResponse(
        recording=RecordingRead.model_validate(rec),
        upload_url=upload_url,
        upload_expires_in=UPLOAD_URL_TTL,
    )


@router.post("/{recording_id}/complete", response_model=RecordingRead)
async def complete_upload(
    recording_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Recording:
    rec = await session.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    if not (rec.uploaded_by == user.id or user.role == UserRole.admin.value):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your recording")
    if rec.status != RecordingStatus.pending_upload.value:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Recording is not awaiting upload (status={rec.status})"
        )

    head = await storage.head_object(rec.audio_key)
    if head is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload not found in storage")

    rec.file_size_bytes = head["ContentLength"]
    rec.status = RecordingStatus.uploaded.value
    await session.commit()
    await session.refresh(rec)
    return rec


@router.get("", response_model=list[RecordingRead])
async def list_recordings(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Recording]:
    stmt = select(Recording).order_by(Recording.created_at.desc()).limit(limit).offset(offset)
    if not _is_staff(user):
        stmt = stmt.where(Recording.uploaded_by == user.id)
    return list(await session.scalars(stmt))


@router.get("/{recording_id}", response_model=RecordingRead)
async def get_recording(
    recording_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Recording:
    rec = await session.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    if not _can_access(user, rec):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    return rec


@router.get("/{recording_id}/download-url", response_model=PresignedUrlResponse)
async def download_url(
    recording_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> PresignedUrlResponse:
    rec = await session.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    if not _can_access(user, rec):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    url = await storage.presigned_get_url(rec.audio_key, expires_in=UPLOAD_URL_TTL)
    return PresignedUrlResponse(url=url, expires_in=UPLOAD_URL_TTL)


# 切分由 worker 异步处理: 把录音置回 uploaded, worker 轮询领取后(重新)切分
_SEGMENTABLE = {
    RecordingStatus.uploaded.value,
    RecordingStatus.segmented.value,
    RecordingStatus.failed.value,
}


@router.post("/{recording_id}/segment", response_model=RecordingRead)
async def trigger_segmentation(
    recording_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Recording:
    rec = await session.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    if not (rec.uploaded_by == user.id or user.role == UserRole.admin.value):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your recording")
    if rec.status not in _SEGMENTABLE:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Cannot segment in status={rec.status}"
        )
    rec.status = RecordingStatus.uploaded.value
    await session.commit()
    await session.refresh(rec)
    return rec


@router.get("/{recording_id}/segments", response_model=list[SegmentRead])
async def list_segments(
    recording_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> list[Segment]:
    rec = await session.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    if not _can_access(user, rec):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    stmt = (
        select(Segment)
        .where(Segment.recording_id == recording_id)
        .order_by(Segment.segment_index)
    )
    return list(await session.scalars(stmt))
