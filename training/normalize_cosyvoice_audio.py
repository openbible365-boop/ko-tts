#!/usr/bin/env python3
"""Normalize ko-tts training clips to PCM WAV before CosyVoice extraction."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import wave
from pathlib import Path


def audio_paths(train_list: Path) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for line_no, raw in enumerate(train_list.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        parts = raw.split("|", 3)
        if len(parts) != 4:
            raise ValueError(f"{train_list}:{line_no}: expected wav|speaker|language|text")
        path = Path(parts[0]).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"{train_list}:{line_no}: audio not found: {path}")
        if path not in seen:
            paths.append(path)
            seen.add(path)
    if not paths:
        raise ValueError(f"{train_list}: no audio files")
    return paths


def normalize_audio(path: Path, sample_rate: int, ffmpeg: str = "ffmpeg") -> None:
    temp = path.with_name(f".{path.name}.normalized.wav")
    try:
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-c:a",
                "pcm_s16le",
                str(temp),
            ],
            check=True,
        )
        with wave.open(str(temp), "rb") as wav:
            if (
                wav.getframerate() != sample_rate
                or wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or wav.getnframes() < 1
            ):
                raise ValueError(f"unexpected normalized WAV format: {path}")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-list", required=True, type=Path)
    parser.add_argument("--sample-rate", type=int, default=24000)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()

    paths = audio_paths(args.train_list.resolve())
    for index, path in enumerate(paths, 1):
        try:
            normalize_audio(path, args.sample_rate, args.ffmpeg)
        except Exception as exc:
            raise RuntimeError(f"audio normalization failed: {path}: {exc}") from exc
        if index % 25 == 0 or index == len(paths):
            print(f"normalized {index}/{len(paths)}", flush=True)
    print(
        json.dumps(
            {"normalized": len(paths), "sample_rate": args.sample_rate},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
