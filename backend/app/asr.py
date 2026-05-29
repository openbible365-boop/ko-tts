"""faster-whisper ASR 封装 (CPU + int8 量化)。

模型懒加载, 由 worker 在启动时 warm_up;首次会从 HuggingFace 下载到
settings.asr_model_cache_dir(/models, named volume)。
"""

import asyncio
import logging

from faster_whisper import WhisperModel

from app.config import settings

log = logging.getLogger("asr")

_MODEL: WhisperModel | None = None


def _load_model() -> WhisperModel:
    global _MODEL
    if _MODEL is None:
        log.info(
            "loading faster-whisper model=%s compute_type=%s cache=%s",
            settings.asr_model_size, settings.asr_compute_type, settings.asr_model_cache_dir,
        )
        _MODEL = WhisperModel(
            settings.asr_model_size,
            device="cpu",
            compute_type=settings.asr_compute_type,
            download_root=settings.asr_model_cache_dir,
        )
        log.info("faster-whisper ready")
    return _MODEL


def warm_up() -> None:
    """同步预加载模型 (避免首段转写时的下载/初始化延迟)。"""
    _load_model()


def _transcribe_sync(path: str) -> str:
    model = _load_model()
    segments, _info = model.transcribe(
        path,
        language=settings.asr_language,
        beam_size=settings.asr_beam_size,
    )
    return " ".join(s.text.strip() for s in segments).strip()


async def transcribe(path: str) -> str:
    """faster-whisper 是同步 CPU 密集型, 放线程池避免阻塞事件循环。"""
    return await asyncio.to_thread(_transcribe_sync, path)
