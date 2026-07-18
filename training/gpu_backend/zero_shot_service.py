"""Persistent reference profiles for standalone zero-shot voices."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any


ZERO_SHOT_VOICE_DIR = Path(
    os.environ.get("ZERO_SHOT_VOICE_DIR", "/www/yuyin/zero-shot-voices")
)
MAX_REFERENCE_BYTES = 20 * 1024 * 1024
VALID_LANGUAGES = {"en", "ko", "zh"}


def _voice_dir(exp: str) -> Path:
    if not exp or len(exp) > 64 or not re.fullmatch(r"[^/\\\s]+", exp):
        raise ValueError("非法的零样本音色名")
    root = ZERO_SHOT_VOICE_DIR.resolve()
    voice_dir = (root / exp).resolve()
    if voice_dir.parent != root:
        raise ValueError("非法的零样本音色路径")
    return voice_dir


def _duration(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ValueError("无法读取参考音频时长") from exc


def save_zero_shot_voice(
    exp: str,
    audio: bytes,
    prompt_text: str,
    prompt_language: str,
) -> dict[str, Any]:
    """Normalize and atomically publish one zero-shot reference profile."""
    voice_dir = _voice_dir(exp)
    prompt_text = prompt_text.strip()
    prompt_language = prompt_language.strip().lower()
    if not prompt_text:
        raise ValueError("参考文本不能为空")
    if len(prompt_text) > 2000:
        raise ValueError("参考文本过长")
    if prompt_language not in VALID_LANGUAGES:
        raise ValueError("参考语言必须是 en、ko 或 zh")
    if not audio:
        raise ValueError("参考音频为空")
    if len(audio) > MAX_REFERENCE_BYTES:
        raise ValueError("参考音频不能超过 20 MB")

    voice_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    source = voice_dir / f".source-{token}"
    reference_tmp = voice_dir / f".reference-{token}.wav"
    metadata_tmp = voice_dir / f".reference-{token}.json"
    try:
        source.write_bytes(audio)
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-t",
                    "9",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-sample_fmt",
                    "s16",
                    str(reference_tmp),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("参考音频转换失败") from exc
        if result.returncode != 0 or not reference_tmp.is_file():
            raise ValueError(f"参考音频转换失败: {result.stderr[-200:]}")
        duration_sec = _duration(reference_tmp)
        if not 3.0 <= duration_sec <= 9.1:
            raise ValueError("参考音频转换后必须为 3–9 秒")

        metadata = {
            "exp": exp,
            "prompt_text": prompt_text,
            "prompt_language": prompt_language,
            "duration_sec": round(duration_sec, 3),
        }
        metadata_tmp.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(reference_tmp, voice_dir / "reference.wav")
        os.replace(metadata_tmp, voice_dir / "reference.json")
        return metadata
    finally:
        for path in (source, reference_tmp, metadata_tmp):
            path.unlink(missing_ok=True)


def get_zero_shot_reference(exp: str) -> tuple[str, str, str] | None:
    """Return (wav path, prompt text, prompt language) for a registered voice."""
    try:
        voice_dir = _voice_dir(exp)
    except ValueError:
        return None
    reference = voice_dir / "reference.wav"
    metadata_path = voice_dir / "reference.json"
    if not reference.is_file() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        prompt_text = str(metadata["prompt_text"]).strip()
        prompt_language = str(metadata["prompt_language"]).strip()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not prompt_text or prompt_language not in VALID_LANGUAGES:
        return None
    return str(reference), prompt_text, prompt_language


def list_zero_shot_voices() -> list[str]:
    root = ZERO_SHOT_VOICE_DIR
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and get_zero_shot_reference(path.name) is not None
    )


def delete_zero_shot_voice(exp: str) -> bool:
    voice_dir = _voice_dir(exp)
    if not voice_dir.is_dir():
        return False
    shutil.rmtree(voice_dir)
    return True
