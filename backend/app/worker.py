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


# ---------- 阶段一: 人声分离(仅 remove_music) ----------
async def _claim_separation() -> uuid.UUID | None:
    async with SessionLocal() as s, s.begin():
        rec = await s.scalar(
            select(Recording)
            .where(Recording.status == RecordingStatus.pending_separation.value)
            .order_by(Recording.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if rec is None:
            return None
        rec.status = RecordingStatus.separating.value
        return rec.id


async def _process_separation(rec_id: uuid.UUID) -> None:
    """去背景音乐: 分离人声, 存回 R2, 录音转 uploaded(就绪, 等用户手动开始切分)。"""
    async with SessionLocal() as s:
        rec = await s.get(Recording, rec_id)
        audio_key = rec.audio_key

    data = await storage.get_bytes(audio_key)
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "input")
        with open(src, "wb") as f:
            f.write(data)
        log.info("rec %s: removing background music (vocal separation)…", rec_id)
        vocals = await separation.separate_vocals(src, os.path.join(tmp, "sep"))
        with open(vocals, "rb") as f:
            vocal_bytes = f.read()

    key = storage.processed_key(rec_id)
    await storage.put_bytes(key, vocal_bytes, content_type="audio/wav")

    async with SessionLocal() as s, s.begin():
        rec = await s.get(Recording, rec_id)
        rec.processed_audio_key = key
        rec.status = RecordingStatus.uploaded.value


# ---------- 阶段二: 录音切分(用户手动触发后) ----------
async def _claim_recording() -> uuid.UUID | None:
    async with SessionLocal() as s, s.begin():
        rec = await s.scalar(
            select(Recording)
            .where(Recording.status == RecordingStatus.pending_segmentation.value)
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
        processed_key = rec.processed_audio_key

    data = await storage.get_bytes(audio_key)

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "input")
        with open(src, "wb") as f:
            f.write(data)

        meta = await segmentation.probe(src)

        # 元数据取原始文件; 但切分与切片用的音频源: 若已做过人声分离(processed_key
        # 有值), 用存好的人声 wav(分离不改变时长), 否则用原文件。
        seg_src = src
        if processed_key:
            seg_src = os.path.join(tmp, "vocals.wav")
            with open(seg_src, "wb") as f:
                f.write(await storage.get_bytes(processed_key))

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
        expected = seg.text  # 录音样品: 范文期望文字
        # 按录音自身语种转写, 而非全局默认; 兜底回退在 asr.transcribe 内。
        rec = await s.get(Recording, seg.recording_id)
        language = rec.language if rec else None
        is_sample = rec is not None and rec.script_id is not None

    if not audio_key:
        raise RuntimeError(f"segment {seg_id} has no audio_key")

    data = await storage.get_bytes(audio_key)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "clip.wav")
        with open(path, "wb") as f:
            f.write(data)
        # 录音样品: 用范文行做提词偏置, 减少专有/文语词被识别成常见词的误差
        text = await asr.transcribe(
            path, language=language, initial_prompt=expected if is_sample else None
        )

    async with SessionLocal() as s, s.begin():
        seg = await s.get(Segment, seg_id)
        seg.asr_text = text
        if is_sample:
            # 录音样品: 不再按识别自动通过, 一律进"待审核"由人工逐条确认(通过/重录/退回)。
            # asr_text 仍保留, 用于录音页/审核页把与范文不一致的词标红, 辅助人工判断。
            seg.status = SegmentStatus.pending_review.value
        else:
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
        # 卡死的 segmenting 退回 pending_segmentation(重新排队切分);
        # 卡死的 separating 退回 pending_separation(重新排队去音乐)。
        seg_res = await s.execute(
            update(Recording)
            .where(
                Recording.status == RecordingStatus.segmenting.value,
                Recording.updated_at < cutoff,
            )
            .values(
                status=RecordingStatus.pending_segmentation.value, updated_at=func.now()
            )
            .returning(Recording.id)
        )
        sep_res = await s.execute(
            update(Recording)
            .where(
                Recording.status == RecordingStatus.separating.value,
                Recording.updated_at < cutoff,
            )
            .values(
                status=RecordingStatus.pending_separation.value, updated_at=func.now()
            )
            .returning(Recording.id)
        )
        r_count = len(seg_res.scalars().all()) + len(sep_res.scalars().all())

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


async def _try_separate_one() -> bool:
    rec_id = await _claim_separation()
    if rec_id is None:
        return False
    log.info("separation claim %s", rec_id)
    try:
        await _process_separation(rec_id)
        log.info("separation done %s", rec_id)
    except Exception as e:  # noqa: BLE001
        log.exception("separation failed for %s: %r", rec_id, e)
        await _mark_recording_failed(rec_id)
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
            if await _try_separate_one():
                continue
            await asyncio.sleep(settings.worker_poll_interval_sec)
        except Exception as e:  # noqa: BLE001
            log.exception("worker loop error: %r", e)
            await asyncio.sleep(settings.worker_poll_interval_sec)


if __name__ == "__main__":
    asyncio.run(run())
