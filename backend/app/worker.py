"""worker: 轮询 Postgres 处理 (1) 录音切分 与 (2) segment ASR。

每次循环优先处理 ASR (清空 pending_transcription 队列), 再处理切分;
都没活则 sleep。并发安全靠 SELECT ... FOR UPDATE SKIP LOCKED。

启动时同步 warm_up ASR 模型 (首次会从 HF 下载到 /models named volume)。
"""

import asyncio
import logging
import os
import tempfile
import uuid

from sqlalchemy import delete, select

from app import asr, segmentation, storage
from app.config import settings
from app.db import SessionLocal
from app.models import Recording, RecordingStatus, Segment, SegmentStatus

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [worker] %(message)s")
log = logging.getLogger("worker")


# ---------- 录音切分 ----------
async def _claim_recording() -> uuid.UUID | None:
    async with SessionLocal() as s, s.begin():
        rec = await s.scalar(
            select(Recording)
            .where(Recording.status == RecordingStatus.uploaded.value)
            .order_by(Recording.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if rec is None:
            return None
        rec.status = RecordingStatus.segmenting.value
        return rec.id


async def _process_recording(rec_id: uuid.UUID) -> None:
    async with SessionLocal() as s:
        rec = await s.get(Recording, rec_id)
        audio_key = rec.audio_key

    data = await storage.get_bytes(audio_key)

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "input")
        with open(src, "wb") as f:
            f.write(data)

        meta = await segmentation.probe(src)
        silences = await segmentation.detect_silences(
            src, settings.seg_silence_noise_db, settings.seg_min_silence_sec
        )
        spans = segmentation.compute_segments(
            meta["duration_ms"] / 1000.0,
            silences,
            settings.seg_min_segment_sec,
            settings.seg_max_segment_sec,
        )
        log.info(
            "rec %s: duration=%sms silences=%d -> %d segments",
            rec_id, meta["duration_ms"], len(silences), len(spans),
        )

        new_rows: list[Segment] = []
        for idx, (start, end) in enumerate(spans):
            seg_id = uuid.uuid4()
            out = os.path.join(tmp, f"{seg_id}.wav")
            await segmentation.cut_clip(src, start, end, out, settings.seg_sample_rate)
            with open(out, "rb") as f:
                clip = f.read()
            key = storage.segment_key(seg_id)
            await storage.put_bytes(key, clip, content_type="audio/wav")
            new_rows.append(
                Segment(
                    id=seg_id,
                    recording_id=rec_id,
                    segment_index=idx,
                    start_ms=int(start * 1000),
                    end_ms=int(end * 1000),
                    duration_ms=int((end - start) * 1000),
                    audio_key=key,
                    status=SegmentStatus.pending_transcription.value,
                )
            )

    async with SessionLocal() as s, s.begin():
        old_keys = list(
            await s.scalars(select(Segment.audio_key).where(Segment.recording_id == rec_id))
        )
        await s.execute(delete(Segment).where(Segment.recording_id == rec_id))
        s.add_all(new_rows)
        rec = await s.get(Recording, rec_id)
        rec.duration_ms = meta["duration_ms"]
        rec.sample_rate = meta["sample_rate"]
        rec.channels = meta["channels"]
        rec.codec = meta["codec"]
        rec.status = RecordingStatus.segmented.value

    for key in old_keys:
        if key:
            try:
                await storage.delete_object(key)
            except Exception as e:  # noqa: BLE001
                log.warning("failed to delete old clip %s: %r", key, e)


async def _mark_recording_failed(rec_id: uuid.UUID) -> None:
    try:
        async with SessionLocal() as s, s.begin():
            rec = await s.get(Recording, rec_id)
            if rec is not None:
                rec.status = RecordingStatus.failed.value
    except Exception as e:  # noqa: BLE001
        log.error("failed to mark rec %s failed: %r", rec_id, e)


# ---------- segment ASR ----------
async def _claim_segment() -> uuid.UUID | None:
    async with SessionLocal() as s, s.begin():
        seg = await s.scalar(
            select(Segment)
            .where(Segment.status == SegmentStatus.pending_transcription.value)
            .order_by(Segment.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if seg is None:
            return None
        seg.status = SegmentStatus.transcribing.value
        return seg.id


async def _process_segment(seg_id: uuid.UUID) -> None:
    async with SessionLocal() as s:
        seg = await s.get(Segment, seg_id)
        audio_key = seg.audio_key

    if not audio_key:
        raise RuntimeError(f"segment {seg_id} has no audio_key")

    data = await storage.get_bytes(audio_key)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "clip.wav")
        with open(path, "wb") as f:
            f.write(data)
        text = await asr.transcribe(path)

    async with SessionLocal() as s, s.begin():
        seg = await s.get(Segment, seg_id)
        seg.asr_text = text
        seg.status = SegmentStatus.pending_correction.value


async def _mark_segment_failed(seg_id: uuid.UUID) -> None:
    try:
        async with SessionLocal() as s, s.begin():
            seg = await s.get(Segment, seg_id)
            if seg is not None:
                seg.status = SegmentStatus.transcription_failed.value
    except Exception as e:  # noqa: BLE001
        log.error("failed to mark seg %s failed: %r", seg_id, e)


# ---------- loop ----------
async def _try_transcribe_one() -> bool:
    seg_id = await _claim_segment()
    if seg_id is None:
        return False
    log.info("ASR claim %s", seg_id)
    try:
        await _process_segment(seg_id)
        log.info("ASR done %s", seg_id)
    except Exception as e:  # noqa: BLE001
        log.exception("ASR failed for %s: %r", seg_id, e)
        await _mark_segment_failed(seg_id)
    return True


async def _try_segment_one() -> bool:
    rec_id = await _claim_recording()
    if rec_id is None:
        return False
    log.info("segment claim %s", rec_id)
    try:
        await _process_recording(rec_id)
        log.info("segment done %s", rec_id)
    except Exception as e:  # noqa: BLE001
        log.exception("segmentation failed for %s: %r", rec_id, e)
        await _mark_recording_failed(rec_id)
    return True


async def run() -> None:
    log.info("worker starting; poll interval=%ss", settings.worker_poll_interval_sec)
    try:
        await asyncio.to_thread(asr.warm_up)
    except Exception as e:  # noqa: BLE001  下载失败时延迟到首段再试, 不阻塞切分
        log.exception("ASR warm-up failed (will retry lazily): %r", e)

    while True:
        try:
            if await _try_transcribe_one():
                continue
            if await _try_segment_one():
                continue
            await asyncio.sleep(settings.worker_poll_interval_sec)
        except Exception as e:  # noqa: BLE001
            log.exception("worker loop error: %r", e)
            await asyncio.sleep(settings.worker_poll_interval_sec)


if __name__ == "__main__":
    asyncio.run(run())
