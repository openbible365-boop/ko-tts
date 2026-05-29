"""备份工具 CLI: 流式上传 pg_dump 输出到 R2, 以及清理 R2 上的旧备份。

用法 (在 backend 容器里调用, 由 deploy/backup.sh 编排):
    python -m app.backup upload <r2-key>   # stdin -> R2
    python -m app.backup prune <days>      # 删除 backups/ 前缀下 >N 天的对象
"""

import asyncio
import datetime as dt
import sys

from app import storage
from app.config import settings


async def _upload(key: str) -> None:
    data = sys.stdin.buffer.read()
    if not data:
        sys.exit("stdin is empty; refusing zero-byte backup")
    await storage.put_bytes(key, data, content_type="application/octet-stream")
    print(f"uploaded {key} ({len(data):,} bytes)")


async def _prune(days: int) -> None:
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    deleted = 0
    kept = 0
    async with storage._client() as s3:
        paginator = s3.get_paginator("list_objects_v2")
        async for page in paginator.paginate(
            Bucket=settings.r2_bucket, Prefix="backups/"
        ):
            for obj in page.get("Contents", []) or []:
                if obj["LastModified"] < cutoff:
                    await s3.delete_object(Bucket=settings.r2_bucket, Key=obj["Key"])
                    print(f"deleted {obj['Key']} (modified {obj['LastModified']})")
                    deleted += 1
                else:
                    kept += 1
    print(f"prune: deleted={deleted} kept={kept} (cutoff={cutoff.isoformat()})")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python -m app.backup {upload <key> | prune <days>}")
    cmd = sys.argv[1]
    if cmd == "upload" and len(sys.argv) == 3:
        asyncio.run(_upload(sys.argv[2]))
    elif cmd == "prune" and len(sys.argv) == 3:
        try:
            days = int(sys.argv[2])
        except ValueError:
            sys.exit("prune <days>: days must be an integer")
        asyncio.run(_prune(days))
    else:
        sys.exit(f"unknown command or wrong args: {sys.argv[1:]}")


if __name__ == "__main__":
    main()
