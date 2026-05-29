"""集成测试公用 helpers + storage mock。

storage.head_object 在 prod 里要打 R2 (会真去找对象). 在测试里默认
mock 成"存在,大小=1234". 个别测试需要"不存在"时再覆盖。
"""

import uuid
from collections.abc import Awaitable, Callable

import pytest

from app import storage
from app.models import (
    Recording,
    RecordingStatus,
    Segment,
    SegmentStatus,
    User,
)


@pytest.fixture
def mock_head_object(monkeypatch):
    """默认 mock: 对象存在, ContentLength=1234。"""

    async def _head(key: str) -> dict:
        return {"ContentLength": 1234, "ContentType": "audio/wav"}

    monkeypatch.setattr(storage, "head_object", _head)
    return _head


@pytest.fixture
def mock_head_object_missing(monkeypatch):
    """覆盖: 对象不存在。"""

    async def _head(key: str) -> None:
        return None

    monkeypatch.setattr(storage, "head_object", _head)


# ---- 工厂 ----
async def make_recording(
    db_session,
    user: User,
    *,
    status: RecordingStatus = RecordingStatus.pending_upload,
    category: str = "sermon",
    audio_key: str | None = None,
) -> Recording:
    rec = Recording(
        uploaded_by=user.id,
        audio_key=audio_key or f"recordings/{uuid.uuid4()}/original.wav",
        content_category=category,
        status=status.value,
    )
    db_session.add(rec)
    await db_session.commit()
    await db_session.refresh(rec)
    return rec


async def make_segment(
    db_session,
    recording: Recording,
    *,
    status: SegmentStatus = SegmentStatus.pending_transcription,
    text: str | None = None,
    asr_text: str | None = None,
    index: int = 0,
    duration_ms: int = 2000,
) -> Segment:
    seg = Segment(
        recording_id=recording.id,
        segment_index=index,
        start_ms=index * duration_ms,
        end_ms=(index + 1) * duration_ms,
        duration_ms=duration_ms,
        audio_key=f"segments/{uuid.uuid4()}.wav",
        asr_text=asr_text,
        text=text,
        status=status.value,
    )
    db_session.add(seg)
    await db_session.commit()
    await db_session.refresh(seg)
    return seg


@pytest.fixture
def make_recording_factory(db_session) -> Callable[..., Awaitable[Recording]]:
    async def _f(user, **kw):
        return await make_recording(db_session, user, **kw)

    return _f


@pytest.fixture
def make_segment_factory(db_session) -> Callable[..., Awaitable[Segment]]:
    async def _f(recording, **kw):
        return await make_segment(db_session, recording, **kw)

    return _f
