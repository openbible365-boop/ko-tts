"""人声分离: 切分前把背景音乐/伴奏剥掉, 只留人声。

用 audio-separator(UVR MDX-Net 模型)。它拖了 torch, 故装在**独立 venv**
(/opt/sepvenv, 见 deploy/Dockerfile), 通过 CLI **子进程**调用 —— torch 不被
import 进常驻 whisper 的 worker 进程, 子进程跑完即退、释放内存。

模型缓存在 /models/audio-separator(worker_models named volume), 首次用时下载。
"""

import asyncio
import glob
import os

from app.config import settings

SEP_BIN = "/opt/sepvenv/bin/audio-separator"


async def separate_vocals(src_path: str, out_dir: str) -> str:
    """对 src_path 做人声分离, 返回只含人声的 wav 路径。"""
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(settings.sep_model_dir, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        SEP_BIN,
        src_path,
        "--model_filename", settings.sep_model_filename,
        "--model_file_dir", settings.sep_model_dir,
        "--output_dir", out_dir,
        "--output_format", "WAV",
        "--single_stem", "Vocals",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        tail = out.decode("utf-8", "replace")[-800:]
        raise RuntimeError(f"vocal separation failed (rc={proc.returncode}): {tail}")
    vocals = [f for f in glob.glob(os.path.join(out_dir, "*")) if "vocal" in f.lower()]
    if not vocals:
        raise RuntimeError("vocal separation produced no vocal stem")
    return vocals[0]
