"""worker: 轮询 Postgres 处理 (1) 录音切分 与 (2) segment ASR。

每次循环优先处理 ASR (清空 pending_transcription 队列), 再处理切分;
都没活则 sleep。并发安全靠 SELECT ... FOR UPDATE SKIP LOCKED。

启动时同步 warm_up ASR 模型 (首次会从 HF 下载到 /models named volume)。
"""

import asyncio
import datetime as dt
import logging
import os
import tempfile
import time
import uuid

from sqlalchemy import delete, func, select, update

from app import asr, segmentation, separation, storage
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
        remove_music = rec.remove_music

    data = await storage.get_bytes(audio_key)

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "input")
        with open(src, "wb") as f:
            f.write(data)

        meta = await segmentation.probe(src)

        # 元数据(时长/采样率/声道/编码)取原始文件; 但切分与切片用的音频源
        # 在开了 remove_music 时换成"剥掉音乐后的人声"(分离不改变时长)。
        seg_src = src
        if remove_music:
            log.info("rec %s: removing background music (vocal separation)…", rec_id)
            seg_src = await separation.separate_vocals(src, os.path.join(tmp, "sep"))

        silences = await segmentation.detect_silences(
            seg_src, settings.seg_silence_noise_db, settings.seg_min_silence_sec
        )
        spans = segmentation.compute_segments(
            meta["duration_ms"] / 1000.0,
            silences,
            settings.seg_min_segment_sec,
            settings.seg_target_segment_sec,
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
            await segmentation.cut_clip(seg_src, start, end, out, settings.seg_sample_rate)
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


# ---------- reaper: 回收陈旧 claim ----------
async def _reap_stale(timeout_min: int) -> tuple[int, int]:
    """重置卡死的中间状态: 把 updated_at 早于 cutoff 的 segmenting/transcribing 退回队列。

    timeout_min=0 表示"所有当前在中间状态的都算陈旧"(启动时用,
    单 worker 假设下 boot 时残留的 in-progress 一定是上一个进程死前的)。
    """
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=timeout_min)
    async with SessionLocal() as s, s.begin():
        r_res = await s.execute(
            update(Recording)
            .where(
                Recording.status == RecordingStatus.segmenting.value,
                Recording.updated_at < cutoff,
            )
            .values(status=RecordingStatus.uploaded.value, updated_at=func.now())
            .returning(Recording.id)
        )
        r_count = len(r_res.scalars().all())

        s_res = await s.execute(
            update(Segment)
            .where(
                Segment.status == SegmentStatus.transcribing.value,
                Segment.updated_at < cutoff,
            )
            .values(
                status=SegmentStatus.pending_transcription.value,
                updated_at=func.now(),
            )
            .returning(Segment.id)
        )
        s_count = len(s_res.scalars().all())

    if r_count or s_count:
        log.info(
            "reaper: %d recording(s) segmenting->uploaded, "
            "%d segment(s) transcribing->pending_transcription (timeout=%dm)",
            r_count, s_count, timeout_min,
        )
    return r_count, s_count


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
    log.info(
        "worker starting; poll=%ss reap_every=%ss claim_timeout=%dm",
        settings.worker_poll_interval_sec,
        settings.worker_reap_interval_sec,
        settings.worker_claim_timeout_min,
    )
    try:
        await asyncio.to_thread(asr.warm_up)
    except Exception as e:  # noqa: BLE001  下载失败时延迟到首段再试, 不阻塞切分
        log.exception("ASR warm-up failed (will retry lazily): %r", e)

    # 启动时 aggressive reap: 上一个 worker 进程死前留下的中间状态一律视为陈旧
    try:
        await _reap_stale(timeout_min=0)
    except Exception as e:  # noqa: BLE001
        log.exception("startup reap failed (continuing): %r", e)
    last_reap = time.monotonic()

    while True:
        try:
            now = time.monotonic()
            if now - last_reap >= settings.worker_reap_interval_sec:
                try:
                    await _reap_stale(timeout_min=settings.worker_claim_timeout_min)
                except Exception as e:  # noqa: BLE001
                    log.exception("periodic reap failed (continuing): %r", e)
                last_reap = now

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
