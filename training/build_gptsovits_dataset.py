#!/usr/bin/env python3
"""从 ko-tts 采集服务导出 GPT-SoVITS 训练集(本仓库唯一的训练侧桥接脚本)。

流程: 调 `/export/manifest.jsonl`(默认 approved 段)-> 下载每段 wav ->
写 GPT-SoVITS 标注表(每行 `wav_path|speaker|language|text`)。

只用标准库, 无需 pip 安装。

先拿 token(reviewer/admin):
    curl -s -X POST https://kr-tts.openbible.live/api/auth/login \\
      -d 'username=YOU@example.com' -d 'password=YOURPASS' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'

再导出某个声音:
    python training/build_gptsovits_dataset.py \\
      --token "$TOKEN" --speaker 남성1 --out ./dataset_male

输出:
    <out>/wavs/<segment_id>.wav      切片音频
    <out>/train.list                 GPT-SoVITS 标注表(填进 WebUI 的标注路径)
"""

from __future__ import annotations  # 兼容旧版 python3 的 `X | None` 注解

import argparse
import json
import os
import urllib.parse
import urllib.request


def fetch_manifest(api: str, token: str, speaker: str | None, status: str):
    qs: dict[str, str] = {"status": status}
    if speaker:
        qs["speaker"] = speaker
    url = api.rstrip("/") + "/api/export/manifest.jsonl?" + urllib.parse.urlencode(qs)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (trusted, user-supplied host)
        body = resp.read().decode("utf-8")
    for raw in body.splitlines():
        if raw.strip():
            yield json.loads(raw)


def main() -> None:
    ap = argparse.ArgumentParser(description="导出 GPT-SoVITS 训练集")
    ap.add_argument("--api", default="https://kr-tts.openbible.live")
    ap.add_argument("--token", required=True, help="reviewer/admin 的 access_token")
    ap.add_argument("--speaker", default=None, help="按声音/说话人筛选(精确)")
    ap.add_argument("--status", default="approved", help="段状态, 默认 approved")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--language", default="ko", help="GPT-SoVITS 语言代码, 朝鲜语=ko")
    args = ap.parse_args()

    wav_dir = os.path.join(args.out, "wavs")
    os.makedirs(wav_dir, exist_ok=True)

    lines: list[str] = []
    n = 0
    total_ms = 0
    for item in fetch_manifest(args.api, args.token, args.speaker, args.status):
        text = (item.get("text") or "").strip()
        if not text:
            continue  # 未校对(text 为空)的段跳过
        speaker = item.get("speaker") or args.speaker or "speaker"
        wav_path = os.path.abspath(os.path.join(wav_dir, f"{item['id']}.wav"))
        with urllib.request.urlopen(item["audio_url"]) as r, open(wav_path, "wb") as f:  # noqa: S310
            f.write(r.read())
        lines.append(f"{wav_path}|{speaker}|{args.language}|{text}")
        n += 1
        total_ms += item.get("duration_ms") or 0
        print(f"  [{n:>3}] {speaker:<10} {(item.get('duration_ms') or 0)/1000:>5.1f}s  {text[:36]}")

    list_path = os.path.join(args.out, "train.list")
    with open(list_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        if lines:
            f.write("\n")

    print(f"\n完成: {n} 段, 总时长 {total_ms / 1000 / 60:.1f} 分钟")
    print(f"  wav  -> {wav_dir}")
    print(f"  list -> {list_path}")
    if n == 0:
        print("  (0 段 —— 确认 speaker 拼写, 以及这些段是否已 approve)")
    else:
        print(f"  把 {list_path} 填进 GPT-SoVITS WebUI 的标注表路径即可开练。")


if __name__ == "__main__":
    main()
