#!/usr/bin/env python3
"""Call a CosyVoice3 JSON service and save the returned WAV."""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test a CosyVoice3 service")
    parser.add_argument("--url", default="http://127.0.0.1:9884/tts")
    parser.add_argument("--text", required=True)
    parser.add_argument("--prompt-text", required=True)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--model-exp")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    payload = json.dumps(
        {
            "text": args.text,
            "prompt_text": args.prompt_text,
            "ref_audio_path": str(args.reference.resolve()),
            "speed_factor": 1.0,
            "model_exp": args.model_exp,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        args.url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
        audio = response.read()
    args.out.write_bytes(audio)
    print(f"saved {len(audio)} bytes to {args.out}")


if __name__ == "__main__":
    main()
