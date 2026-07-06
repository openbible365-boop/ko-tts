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
    target_len: float,
    max_len: float,
) -> list[tuple[float, float]]:
    """把语音切成 ~target_len 的片段, 并尽量在**句末停顿**(较长的静音)处下刀。

    1. 静音区间取补集 -> 语音块(块之间就是停顿, 停顿越长=越像句末)。
    2. 从当前起点向后, 在"段长 ∈ [pref_lo, max_len]"的所有候选断点里, **挑停顿最长的那个**
       下刀(并列时取最接近 target_len 的)。这样句中换气(短停顿, 如 0.25s)不会被选中,
       只在真正的句末长停顿处断开。pref_lo 取 0.6*target, 保证段不至于太碎。
    3. 兜底: 整块连续语音超过 max_len 仍无停顿才硬切; 短于 min_len 的尾巴丢弃。

    end 为 None 的静音(常见于文件结尾)视为延伸到末尾。
    """
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
    if not speech:
        return []

    n = len(speech)

    def gap_after(k: int) -> float:
        # 语音块 k 之后的停顿时长(= 与下一块之间的静音长度); 末块记 0
        return speech[k + 1][0] - speech[k][1] if k + 1 < n else 0.0

    pref_lo = max(min_len, target_len * 0.6)

    # 只在语音块端点(= 静音边界)处断开; 选点偏好"更长的停顿"
    merged: list[tuple[float, float]] = []
    i = 0
    while i < n:
        seg_start = speech[i][0]
        # 能纳入而不超 max_len 的最远块 j
        j = i
        while j + 1 < n and (speech[j + 1][1] - seg_start) <= max_len:
            j += 1
        # 段长落在 [min_len, max_len] 的候选收口块
        cands = [
            k for k in range(i, j + 1)
            if min_len <= (speech[k][1] - seg_start) <= max_len
        ]
        if cands:
            # 优先"够长(>=pref_lo)"的候选; 其中挑停顿最长者, 并列取最接近 target
            strong = [k for k in cands if (speech[k][1] - seg_start) >= pref_lo]
            pool = strong or cands
            best = max(
                pool,
                key=lambda k: (
                    round(gap_after(k), 3),
                    -abs((speech[k][1] - seg_start) - target_len),
                ),
            )
            merged.append((seg_start, speech[best][1]))
            i = best + 1
        else:
            # 无合规断点: 要么单块连续语音 > max_len(交给下面硬切), 要么只剩短尾
            merged.append((seg_start, speech[j][1]))
            i = j + 1

    # 兜底硬切 + 丢短
    out: list[tuple[float, float]] = []
    for start, end in merged:
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
