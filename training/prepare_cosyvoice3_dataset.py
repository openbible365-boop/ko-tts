#!/usr/bin/env python3
"""Convert a ko-tts train.list into CosyVoice3 train/dev metadata."""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


DEFAULT_INSTRUCT = "You are a helpful assistant.<|endofprompt|>"


@dataclass(frozen=True)
class Item:
    utt: str
    wav: Path
    speaker: str
    text: str


def safe_id(value: str, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("_.-")
    return value or fallback


def read_items(list_path: Path, data_root: Path) -> list[Item]:
    items: list[Item] = []
    seen: set[str] = set()
    for line_no, raw in enumerate(list_path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        parts = raw.split("|", 3)
        if len(parts) != 4:
            raise ValueError(f"{list_path}:{line_no}: expected wav|speaker|language|text")
        wav_raw, speaker_raw, _language, text_raw = parts
        wav = Path(wav_raw)
        if not wav.is_absolute():
            wav = data_root / wav
        wav = wav.resolve()
        if not wav.is_file():
            raise FileNotFoundError(f"{list_path}:{line_no}: audio not found: {wav}")
        text = " ".join(text_raw.split())
        if not text:
            raise ValueError(f"{list_path}:{line_no}: text is empty")
        speaker = safe_id(speaker_raw, "speaker")
        base = safe_id(wav.stem, f"utt_{line_no:06d}")
        utt = f"{speaker}_{base}"
        suffix = 2
        while utt in seen:
            utt = f"{speaker}_{base}_{suffix}"
            suffix += 1
        seen.add(utt)
        items.append(Item(utt=utt, wav=wav, speaker=speaker, text=text))
    if len(items) < 3:
        raise ValueError("CosyVoice3 training requires at least 3 usable segments")
    return items


def write_split(out_dir: Path, items: list[Item], instruct: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    spk2utt: dict[str, list[str]] = defaultdict(list)
    for item in items:
        spk2utt[item.speaker].append(item.utt)

    files = {
        "wav.scp": [f"{item.utt} {item.wav}" for item in items],
        "text": [f"{item.utt} {item.text}" for item in items],
        "utt2spk": [f"{item.utt} {item.speaker}" for item in items],
        "spk2utt": [
            f"{speaker} {' '.join(utts)}" for speaker, utts in sorted(spk2utt.items())
        ],
        "instruct": [f"{item.utt} {instruct}" for item in items],
    }
    for name, lines in files.items():
        (out_dir / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert ko-tts train.list to CosyVoice3 train/dev metadata"
    )
    parser.add_argument("--train-list", required=True, type=Path)
    parser.add_argument(
        "--data-root",
        type=Path,
        help="Base directory for relative wav paths; defaults to train.list parent",
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--dev-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=1986)
    parser.add_argument("--instruct", default=DEFAULT_INSTRUCT)
    args = parser.parse_args()

    if not 0 < args.dev_ratio < 0.5:
        parser.error("--dev-ratio must be between 0 and 0.5")

    list_path = args.train_list.resolve()
    data_root = (args.data_root or list_path.parent).resolve()
    items = read_items(list_path, data_root)
    shuffled = list(items)
    random.Random(args.seed).shuffle(shuffled)
    dev_count = max(1, min(len(shuffled) - 2, round(len(shuffled) * args.dev_ratio)))
    dev_items = shuffled[:dev_count]
    train_items = shuffled[dev_count:]

    out = args.out.resolve()
    write_split(out / "train", train_items, args.instruct)
    write_split(out / "dev", dev_items, args.instruct)
    summary = {
        "source": str(list_path),
        "seed": args.seed,
        "instruct": args.instruct,
        "segments": len(items),
        "train_segments": len(train_items),
        "dev_segments": len(dev_items),
        "speakers": sorted({item.speaker for item in items}),
    }
    (out / "dataset.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
