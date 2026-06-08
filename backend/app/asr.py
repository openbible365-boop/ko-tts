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


def _transcribe_sync(path: str, language: str, initial_prompt: str | None = None) -> str:
    model = _load_model()
    segments, _info = model.transcribe(
        path,
        language=language,
        beam_size=settings.asr_beam_size,
        initial_prompt=initial_prompt,
    )
    return " ".join(s.text.strip() for s in segments).strip()


async def transcribe(
    path: str, language: str | None = None, initial_prompt: str | None = None
) -> str:
    """faster-whisper 是同步 CPU 密集型, 放线程池避免阻塞事件循环。

    language: 录音自身的转写语种 (en/zh/ko); None 时回退到 settings 默认。
    initial_prompt: 提词偏置(录音样品用范文行喂入), 让模型优先识别成范文里的
        专有/文语词(如圣经词 궁창), 而非滑向常见词。不增加耗时。
    """
    lang = language or settings.asr_language
    return await asyncio.to_thread(_transcribe_sync, path, lang, initial_prompt)
