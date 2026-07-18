#!/usr/bin/env python3
"""Exercise one engine through yuyin's async clone-task service."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


async def run(args) -> None:
    sys.path.insert(0, str(args.backend))
    from service.tts_service import create_clone_task, get_clone_task

    result = await create_clone_task(
        text=args.text,
        language=args.language,
        speed=1.0,
        emotion="neutral",
        prompt_text=None,
        prompt_language=None,
        reference_audio=None,
        engine=args.engine,
        sovits_weights=args.sovits_weights,
        gpt_weights=args.gpt_weights,
    )
    task_id = result["task_id"]
    for _ in range(150):
        await asyncio.sleep(1)
        task = get_clone_task(task_id)
        if task and task["status"] in {"success", "error"}:
            print(json.dumps(task, ensure_ascii=False))
            if task["status"] != "success":
                raise SystemExit(1)
            return
    raise TimeoutError(f"task {task_id} did not finish")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test a yuyin clone engine")
    parser.add_argument("--backend", type=Path, default=Path("/www/yuyin/backend"))
    parser.add_argument("--engine", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--language", default="ko")
    parser.add_argument("--sovits-weights", required=True)
    parser.add_argument("--gpt-weights", required=True)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
