"""音频切分: ffprobe 探测 + ffmpeg silencedetect 按静音分段 + 切片。

纯函数 compute_segments 不依赖 ffmpeg, 便于单测。
"""

import asyncio
import json
import re

_SIL_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[0-9.]+)")


async def _run(*args: str) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, out, err


async def probe(path: str) -> dict:
    rc, out, err = await _run(
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    )
    if rc != 0:
        raise RuntimeError(f"ffprobe failed: {err.decode('utf-8', 'replace')[:200]}")
    info = json.loads(out or b"{}")
    fmt = info.get("format", {})
    astream = next(
        (s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {}
    )
    duration = float(fmt.get("duration") or astream.get("duration") or 0.0)
    sample_rate = astream.get("sample_rate")
    return {
        "duration_ms": int(duration * 1000),
        "sample_rate": int(sample_rate) if sample_rate else None,
        "channels": astream.get("channels"),
        "codec": astream.get("codec_name"),
    }


async def detect_silences(
    path: str, noise_db: float, min_silence: float
) -> list[tuple[float, float | None]]:
    af = f"silencedetect=noise={noise_db}dB:d={min_silence}"
    rc, _, err = await _run("ffmpeg", "-i", path, "-af", af, "-f", "null", "-")
    if rc != 0:
        raise RuntimeError(f"ffmpeg silencedetect failed: {err.decode('utf-8', 'replace')[:200]}")
    text = err.decode("utf-8", "replace")
    starts = [float(x) for x in _SIL_START.findall(text)]
    ends = [float(x) for x in _SIL_END.findall(text)]
    return [(st, ends[i] if i < len(ends) else None) for i, st in enumerate(starts)]


def compute_segments(
    duration: float,
    silences: list[tuple[float, float | None]],
    min_len: float,
    max_len: float,
) -> list[tuple[float, float]]:
    """静音区间取补集得到语音段, 丢弃过短、切分过长。end 为 None 视为延伸到末尾。"""
    cleaned: list[tuple[float, float]] = []
    for st, en in silences:
        st = max(0.0, st)
        en = duration if en is None else min(en, duration)
        if en > st:
            cleaned.append((st, en))
    cleaned.sort()

    speech: list[tuple[float, float]] = []
    cursor = 0.0
    for st, en in cleaned:
        if st > cursor:
            speech.append((cursor, st))
        cursor = max(cursor, en)
    if cursor < duration:
        speech.append((cursor, duration))

    out: list[tuple[float, float]] = []
    for start, end in speech:
        if end - start < min_len:
            continue
        s = start
        while end - s > max_len:
            out.append((round(s, 3), round(s + max_len, 3)))
            s += max_len
        if end - s >= min_len:
            out.append((round(s, 3), round(end, 3)))
    return out


async def cut_clip(src: str, start: float, end: float, out_path: str, sample_rate: int) -> None:
    rc, _, err = await _run(
        "ffmpeg", "-y", "-i", src,
        "-ss", f"{start}", "-to", f"{end}",
        "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le",
        out_path,
    )
    if rc != 0:
        raise RuntimeError(f"ffmpeg cut failed: {err.decode('utf-8', 'replace')[:200]}")
