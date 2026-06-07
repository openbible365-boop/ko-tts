import os
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import storage
from app.deps import CurrentUser, SessionDep
from app.models import (
    Recording,
    RecordingStatus,
    Script,
    ScriptLine,
    ScriptStatus,
    Segment,
    SegmentStatus,
    User,
    UserRole,
)
from app.schemas import (
    PresignedUrlResponse,
    RecordingCreate,
    RecordingCreateResponse,
    RecordingProgress,
    RecordingRead,
    RecordingSpeaker,
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
        language=data.language.value,
        title=data.title,
        notes=data.notes,
        remove_music=data.remove_music,
        speaker=data.speaker,
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


@router.post("/from-script/{script_id}", response_model=RecordingRead)
async def start_recording_from_script(
    script_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Recording:
    """取或建当前用户对该定稿范文的录音样品(每人一份, 可续录)。

    新建时按范文行预生成 segments(text=范文行快照, status=未录)。
    """
    script = await session.get(Script, script_id)
    if script is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found")
    if script.status != ScriptStatus.finalized.value:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Script is not finalized")

    existing = await session.scalar(
        select(Recording).where(
            Recording.script_id == script_id, Recording.uploaded_by == user.id
        )
    )
    if existing is not None:
        return existing  # 续录

    lines = list(
        await session.scalars(
            select(ScriptLine)
            .where(ScriptLine.script_id == script_id)
            .order_by(ScriptLine.line_index)
        )
    )
    if not lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Script has no lines")

    rec = Recording(
        uploaded_by=user.id,
        script_id=script_id,
        audio_key="",  # 录音样品无单一父文件, 逐行各自存
        content_category=script.content_category,
        language=script.language,
        title=script.title,
        status=RecordingStatus.recording.value,
    )
    session.add(rec)
    await session.flush()
    for ln in lines:
        session.add(
            Segment(
                recording_id=rec.id,
                segment_index=ln.line_index,
                start_ms=0,
                end_ms=0,
                duration_ms=0,
                text=ln.text,  # 期望文字(范文行快照)
                status=SegmentStatus.pending_recording.value,
            )
        )
    try:
        await session.commit()
    except IntegrityError:
        # 并发下另一请求已建(唯一约束 script_id+uploaded_by), 回滚取已存在的
        await session.rollback()
        existing = await session.scalar(
            select(Recording).where(
                Recording.script_id == script_id, Recording.uploaded_by == user.id
            )
        )
        if existing is None:
            raise
        return existing
    await session.refresh(rec)
    return rec


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
    # 勾了去背景音乐 -> 先排队做人声分离(自动); 否则直接就绪等用户手动「开始切分」。
    rec.status = (
        RecordingStatus.pending_separation.value
        if rec.remove_music
        else RecordingStatus.uploaded.value
    )
    await session.commit()
    await session.refresh(rec)
    return rec


@router.get("", response_model=list[RecordingRead])
async def list_recordings(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RecordingRead]:
    # join User 把上传者邮箱/昵称带出来, 供 staff 在「全部采集」里分辨归属。
    stmt = (
        select(Recording, User.email, User.display_name)
        .join(User, Recording.uploaded_by == User.id)
        .order_by(Recording.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if not _is_staff(user):
        stmt = stmt.where(Recording.uploaded_by == user.id)
    rows = (await session.execute(stmt)).all()
    return [
        RecordingRead.model_validate(rec).model_copy(
            update={"uploader_email": email, "uploader_name": name}
        )
        for rec, email, name in rows
    ]


@router.post("/{recording_id}/speaker", response_model=RecordingRead)
async def set_recording_speaker(
    recording_id: uuid.UUID, data: RecordingSpeaker, user: CurrentUser, session: SessionDep
) -> Recording:
    """设置/修改录音样品的声音昵称(训练分组用; 录音前必填)。owner 或 admin。"""
    rec = await session.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    if not (rec.uploaded_by == user.id or user.role == UserRole.admin.value):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    rec.speaker = data.speaker.strip()
    await session.commit()
    await session.refresh(rec)
    return rec


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


@router.get("/{recording_id}/progress", response_model=RecordingProgress)
async def recording_progress(
    recording_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> RecordingProgress:
    """处理进度: 切分/转写阶段的细粒度信息(列表轮询用, 比裸 status 更可读)。"""
    rec = await session.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    if not _can_access(user, rec):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    result = await session.execute(
        select(Segment.status, func.count())
        .where(Segment.recording_id == recording_id)
        .group_by(Segment.status)
    )
    counts = {s: n for s, n in result.all()}
    total = sum(counts.values())
    in_tx = counts.get(SegmentStatus.pending_transcription.value, 0) + counts.get(
        SegmentStatus.transcribing.value, 0
    )
    elapsed = (
        (datetime.now(UTC) - rec.updated_at).total_seconds() if rec.updated_at else 0.0
    )
    return RecordingProgress(
        status=rec.status,
        remove_music=rec.remove_music,
        segment_total=total,
        transcribed=total - in_tx,
        in_transcription=in_tx,
        phase_elapsed_sec=int(max(0.0, elapsed)),
    )


@router.delete("/{recording_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recording(
    recording_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> None:
    """删除录音及其所有切片(DB 行 + R2 对象)。owner 或 admin 可删。"""
    rec = await session.get(Recording, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    if not (rec.uploaded_by == user.id or user.role == UserRole.admin.value):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your recording")

    # 先删 R2 对象(切片 wav + 录音原文件); DB 级联负责删 segment 行。
    seg_keys = list(
        await session.scalars(
            select(Segment.audio_key).where(Segment.recording_id == recording_id)
        )
    )
    for key in [*seg_keys, rec.audio_key]:
        if key:
            try:
                await storage.delete_object(key)
            except Exception:  # noqa: BLE001  R2 删除失败不应阻断 DB 清理(对象孤儿无害)
                pass

    await session.delete(rec)  # relationship cascade 删除 segments 行
    await session.commit()


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


# 「开始切分」: 用户手动触发。只能从就绪态(uploaded, 已去过音乐)或重切(segmented/
# failed)发起; worker 轮询领取 pending_segmentation 后切分。
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
    rec.status = RecordingStatus.pending_segmentation.value
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
