#!/usr/bin/env python3
"""Apply conservative single-GPU SFT settings to the official CosyVoice3 YAML."""

from __future__ import annotations

import argparse
from pathlib import Path

from ruamel.yaml import YAML


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure CosyVoice3 LLM SFT")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--max-frames", type=int, default=1200)
    parser.add_argument("--log-interval", type=int, default=1)
    args = parser.parse_args()
    if args.epochs < 1:
        parser.error("--epochs must be positive")
    if args.max_frames < 200:
        parser.error("--max-frames must be at least 200")

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    with args.input.open(encoding="utf-8") as handle:
        config = yaml.load(handle)

    config["padding"]["use_spk_embedding"] = True
    config["batch"]["max_frames_in_batch"] = args.max_frames
    config["train_conf"]["optim_conf"]["lr"] = 1e-5
    config["train_conf"]["scheduler"] = "constantlr"
    config["train_conf"]["max_epoch"] = args.epochs
    config["train_conf"]["accum_grad"] = 2
    config["train_conf"]["log_interval"] = args.log_interval

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        yaml.dump(config, handle)


if __name__ == "__main__":
    main()
