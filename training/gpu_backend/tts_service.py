import asyncio
import io
import json
import os
import subprocess
import time
import uuid
import wave
import re
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import edge_tts
from fastapi import UploadFile

from schema.tts_schema import TTSRequest, TTSResponse
from service.text_chunking import split_tts_text
from service.zero_shot_service import (
    get_zero_shot_reference,
    list_zero_shot_voices,
)


# ------------------------- 标准 Edge-TTS（保持原逻辑） -------------------------

async def generate_tts(request: TTSRequest) -> TTSResponse:
    """
    使用 Edge-TTS 生成语音
    """
    if request.language == "ko":
        normalized_text = f"[朝鲜语规范化]: {request.text}"
    elif request.language == "zh":
        normalized_text = f"[中文规范化]: {request.text}"
    elif request.language == "en":
        normalized_text = f"[英文规范化]: {request.text}"
    else:
        normalized_text = f"[{request.language} 规范化]: {request.text}"

    voice = request.voice_id
    rate_pct = int((request.speed - 1.0) * 100)
    rate_str = f"+{rate_pct}%" if rate_pct >= 0 else f"{rate_pct}%"

    filename = f"{uuid.uuid4().hex}.mp3"
    filepath = os.path.join("static", filename)

    communicate = edge_tts.Communicate(request.text, voice, rate=rate_str)

    timestamps = []
    with open(filepath, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "SentenceBoundary" or chunk["type"] == "WordBoundary":
                start_sec = chunk["offset"] / 10000000.0
                end_sec = (chunk["offset"] + chunk["duration"]) / 10000000.0
                text = chunk.get("text", "")
                timestamps.append({
                    "start": start_sec,
                    "end": end_sec,
                    "text": text,
                })

    return TTSResponse(
        status="success",
        audio_url=f"/api/static/{filename}",
        normalized_text=normalized_text,
        message="音频生成成功",
        timestamps=timestamps,
    )


# ------------------------- 声音克隆：异步任务模式 -------------------------

# 内存任务表。结构：
# {
#   "<task_id>": {
#     "status": "pending" | "running" | "success" | "error",
#     "total": int, "completed": int,
#     "audio_url": str?, "normalized_text": str?, "message": str?,
#     "timestamps": list?, "created_at": float,
#   }
# }
TASKS: Dict[str, Dict[str, Any]] = {}

# 任务保留时长（成功/失败后保留，方便前端补轮询）
TASK_TTL_SECONDS = 60 * 60  # 1 小时

# 单节请求超时（秒）。一节几十秒到一两分钟，给宽点。
SOVITS_SINGLE_TIMEOUT = 300

# 推理引擎的端点，前端按任务选择
SOVITS_URL = os.environ.get("SOVITS_URL", "http://127.0.0.1:9880/tts")
F5_URL = os.environ.get("F5_URL", "http://127.0.0.1:9881/tts")
COSYVOICE_URL = os.environ.get("COSYVOICE_URL", "http://127.0.0.1:9882/tts")
COSYVOICE3_URL = os.environ.get("COSYVOICE3_URL", "http://127.0.0.1:9883/tts")
COSYVOICE3_SFT_URL = os.environ.get(
    "COSYVOICE3_SFT_URL", "http://127.0.0.1:9884/tts"
)
ENGINE_URLS = {
    "sovits": SOVITS_URL,
    "f5": F5_URL,
    "cosyvoice": COSYVOICE_URL,
    "cosyvoice3": COSYVOICE3_URL,
    "cosyvoice3_sft": COSYVOICE3_SFT_URL,
}
# 引擎显示名(错误信息里用); cosyvoice*/f5 为零样本引擎, 不加载微调权重
ENGINE_NAMES = {
    "sovits": "GPT-SoVITS",
    "f5": "F5-TTS",
    "cosyvoice": "CosyVoice2",
    "cosyvoice3": "CosyVoice3",
    "cosyvoice3_sft": "CosyVoice3 微调",
}

# 微调音色用: 不指定权重的音色(零样本克隆)回落到这对 V2 底模, 保证用过微调音色后不串音。
BASE_SOVITS_WEIGHTS = os.environ.get(
    "BASE_SOVITS_WEIGHTS",
    "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth",
)
BASE_GPT_WEIGHTS = os.environ.get(
    "BASE_GPT_WEIGHTS",
    "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
)


class SovitsError(Exception):
    """GPT-SoVITS 调用失败。message 形如 'XXX' 或 'timeout'。"""


def _engine_base(engine: str = "sovits") -> str:
    """从 .../tts 端点推出引擎根地址 http://host:port。"""
    return ENGINE_URLS.get(engine, SOVITS_URL).rsplit("/tts", 1)[0]


# 训练产出的微调权重所在的 GPT-SoVITS 项目目录(引擎主机上)。用于前端下拉选择, 免手填路径。
GPT_SOVITS_DIR = os.environ.get("GPT_SOVITS_DIR", "/www/yuyin/GPT-SoVITS")


def list_voice_models() -> Dict[str, List[str]]:
    """扫描已训练的微调权重, 返回相对 GPT-SoVITS 目录的路径(set_*_weights 直接可用)。

    sovits: SoVITS_weights*/*.pth   gpt: GPT_weights*/*.ckpt
    """
    import glob

    def _scan(subglob: str, ext: str) -> List[str]:
        hits = glob.glob(os.path.join(GPT_SOVITS_DIR, subglob, f"*{ext}"))
        rels = [os.path.relpath(h, GPT_SOVITS_DIR) for h in hits]
        return sorted(rels)

    cosyvoice3_sft = []
    for path in glob.glob(
        "/opt/tts/CosyVoice/pretrained_models/*-CosyVoice3-SFT"
    ):
        if os.path.isfile(os.path.join(path, "llm.pt")):
            cosyvoice3_sft.append(
                os.path.basename(path).removesuffix("-CosyVoice3-SFT")
            )

    return {
        "sovits": _scan("SoVITS_weights*", ".pth"),
        "gpt": _scan("GPT_weights*", ".ckpt"),
        "cosyvoice3_sft": sorted(cosyvoice3_sft),
        "zero_shot": list_zero_shot_voices(),
    }


# 微调音色训练数据所在目录(bridge.sh 上传到这里)。用于自动取参考音频, 免用户上传。
YUYIN_DATA_DIR = os.environ.get("YUYIN_DATA_DIR", "/www/yuyin/data")


def _wav_duration(path: str) -> Optional[float]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=20,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def _exp_from_weights(sovits_weights: str) -> Optional[str]:
    """从权重文件名 <exp>_e<N>[_s<M>].pth 还原实验名 exp。"""
    m = re.match(r"^(.+?)_e\d+(?:_s\d+)?\.pth$", os.path.basename(sovits_weights))
    return m.group(1) if m else None


def make_reference_wav(src_path: str, out_path: str, max_sec: float = 9.0) -> bool:
    """把训练样本转成参考 wav(16k 单声道); 超过 max_sec 则截到 max_sec(满足 3~10s 限制)。"""
    dur = _wav_duration(src_path)
    cmd = ["ffmpeg", "-y", "-i", src_path]
    if dur and dur > max_sec:
        cmd += ["-t", str(max_sec)]
    cmd += ["-ac", "1", "-ar", "16000", "-sample_fmt", "s16", out_path]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def auto_reference_for_weights(sovits_weights: str) -> Optional[Tuple[str, str, str]]:
    """微调音色自动取参考: 从该音色训练数据里挑一条合适切片。

    返回 (源wav绝对路径, 参考文本, 语种); 找不到返回 None。优先 3~10s、最接近 6s 的切片;
    若被迫用超长切片(会被截断), 参考文本置空(走无参考文本模式, 避免文音不符)。
    """
    exp = _exp_from_weights(sovits_weights)
    if not exp:
        return None
    return auto_reference_for_exp(exp)


def auto_reference_for_exp(exp: str) -> Optional[Tuple[str, str, str]]:
    """Pick a representative training reference by experiment name."""
    zero_shot_reference = get_zero_shot_reference(exp)
    if zero_shot_reference is not None:
        return zero_shot_reference
    list_path = os.path.join(YUYIN_DATA_DIR, exp, "train_ready.list")
    if not os.path.exists(list_path):
        return None
    best = None  # (score, wav, text, lang, dur)
    with open(list_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) != 4:
                continue
            wav, _spk, lang, text = parts
            if not os.path.exists(wav):
                continue
            dur = _wav_duration(wav)
            if not dur:
                continue
            score = (0 if 3.0 <= dur <= 10.0 else 1, abs(dur - 6.0))
            if best is None or score < best[0]:
                best = (score, wav, text, lang, dur)
    if best is None:
        return None
    _, wav, text, lang, dur = best
    if dur > 10.0:
        text = ""  # 将被截断, 文本不再匹配 -> 无参考文本模式
    return (os.path.abspath(wav), text, lang)


# set_*_weights 是 GPT-SoVITS 引擎的【全局状态】, 多音色并发会串音 ->
# 用锁串行化整个克隆任务; 并缓存当前已加载权重, 一致则跳过切换(切权重要数秒)。
_weights_lock = asyncio.Lock()
_loaded_weights: Dict[str, Optional[str]] = {"sovits": None, "gpt": None}


async def apply_voice_weights(
    sovits_weights: Optional[str], gpt_weights: Optional[str]
) -> None:
    """把指定微调权重加载进引擎; 任一为空则回落到底模。调用方须持有 _weights_lock。

    幂等: 与当前已加载一致则不重复切换。失败抛 SovitsError。
    """
    target_sovits = sovits_weights or BASE_SOVITS_WEIGHTS
    target_gpt = gpt_weights or BASE_GPT_WEIGHTS
    base = _engine_base("sovits")
    timeout = aiohttp.ClientTimeout(total=180)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        if _loaded_weights["sovits"] != target_sovits:
            async with s.get(
                f"{base}/set_sovits_weights", params={"weights_path": target_sovits}
            ) as r:
                if r.status != 200:
                    raise SovitsError(f"加载 SoVITS 权重失败: {await r.text()}")
            _loaded_weights["sovits"] = target_sovits
        if _loaded_weights["gpt"] != target_gpt:
            async with s.get(
                f"{base}/set_gpt_weights", params={"weights_path": target_gpt}
            ) as r:
                if r.status != 200:
                    raise SovitsError(f"加载 GPT 权重失败: {await r.text()}")
            _loaded_weights["gpt"] = target_gpt


async def sovits_call(
    text: str,
    text_lang: str,
    ref_audio_path: str,
    prompt_text: str,
    prompt_lang: str,
    speed: float = 1.0,
    session: Optional[aiohttp.ClientSession] = None,
    engine: str = "sovits",
    model_exp: Optional[str] = None,
) -> bytes:
    """单次调用 TTS 推理引擎，返回 wav 字节。失败抛 SovitsError。

    engine: "sovits"、"f5"、"cosyvoice"、"cosyvoice3" 或 "cosyvoice3_sft"
    """
    url = ENGINE_URLS.get(engine, SOVITS_URL)
    payload = {
        "text": text,
        "text_lang": text_lang,
        "ref_audio_path": ref_audio_path,
        "prompt_text": prompt_text,
        "prompt_lang": prompt_lang,
        "speed_factor": speed,
        "model_exp": model_exp,
    }
    timeout = aiohttp.ClientTimeout(total=SOVITS_SINGLE_TIMEOUT)

    async def _do(s: aiohttp.ClientSession) -> bytes:
        try:
            async with s.post(url, json=payload) as response:
                if response.status != 200:
                    error_msg = await response.text()
                    try:
                        err_json = json.loads(error_msg)
                        clean = err_json.get(
                            "Exception", err_json.get("message", error_msg)
                        )
                    except Exception:
                        clean = error_msg
                    raise SovitsError(str(clean))
                return await response.read()
        except asyncio.TimeoutError:
            raise SovitsError(f"timeout (>{SOVITS_SINGLE_TIMEOUT}s)")

    if session is not None:
        return await _do(session)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        return await _do(s)


def ffmpeg_clean(src_path: str, out_path: str) -> bool:
    """用 ffmpeg 将参考音频转为 16k/单声道/16-bit PCM WAV。失败返回 False。"""
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", src_path,
                "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
                out_path,
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


# 合成后"音色效果"(对生成音频做 ffmpeg 滤镜后处理)。沙哑为近似实验效果。
AUDIO_EFFECTS = {
    "bright": "treble=g=6:f=3500",                       # 亮/清亮: 高频提升
    "warm": "bass=g=5:f=120,treble=g=-2:f=4000",         # 暖/浑厚: 低频提升+收高频
    "reverb": "aecho=0.8:0.85:50|90:0.25|0.18",          # 混响
    "hoarse": "highpass=f=110,tremolo=f=65:d=0.4,acrusher=bits=10:mode=log:aa=0.6",  # 沙哑(近似/实验)
}


def apply_audio_effect(path: str, effect: Optional[str]) -> None:
    """对 wav 原地应用音色效果。effect 为空/未知则跳过; 失败不影响主流程。"""
    af = AUDIO_EFFECTS.get((effect or "").strip())
    if not af:
        return
    tmp = path + ".fx.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-af", af, tmp],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        try:
            os.remove(tmp)
        except OSError:
            pass


def _gc_tasks() -> None:
    """简单 GC：清掉超过 TTL 的已结束任务。"""
    now = time.time()
    expired = [
        tid for tid, t in TASKS.items()
        if t["status"] in ("success", "error")
        and now - t.get("created_at", now) > TASK_TTL_SECONDS
    ]
    for tid in expired:
        TASKS.pop(tid, None)


async def _prepare_reference_audio(reference_audio: UploadFile) -> str:
    """保存上传的参考音频并用 ffmpeg 标准化，返回绝对路径。"""
    raw_filename = f"raw_{uuid.uuid4().hex}_{reference_audio.filename}"
    raw_filepath = os.path.join("static", raw_filename)
    with open(raw_filepath, "wb") as buffer:
        content = await reference_audio.read()
        buffer.write(content)

    clean_filename = f"clean_{uuid.uuid4().hex}.wav"
    clean_filepath = os.path.join("static", clean_filename)
    if not ffmpeg_clean(raw_filepath, clean_filepath):
        clean_filepath = raw_filepath

    return os.path.abspath(clean_filepath)


def _parse_verses(
    text: str,
    verses_json: Optional[str],
    max_chars: int = 80,
) -> List[str]:
    raw_verses = [text]
    if verses_json:
        try:
            verses_data = json.loads(verses_json)
            raw_verses = [v.get("text", "") for v in verses_data]
        except Exception:
            pass
    return [
        chunk
        for verse in raw_verses
        for chunk in split_tts_text(verse, max_chars=max_chars)
    ]


async def _run_clone_task(
    task_id: str,
    text: str,
    language: str,
    speed: float,
    prompt_text: str,
    prompt_language: str,
    abs_ref_path: str,
    verse_texts: List[str],
    engine: str = "sovits",
    voice_exp: Optional[str] = None,
    sovits_weights: Optional[str] = None,
    gpt_weights: Optional[str] = None,
    effect: Optional[str] = None,
) -> None:
    """后台任务：逐节调 GPT-SoVITS，结束后写入 TASKS[task_id]。

    微调音色(传了 sovits_weights/gpt_weights)会先在引擎上加载对应权重; 整个任务持有
    _weights_lock 串行执行, 避免并发切权重串音。零样本音色回落到底模。
    """
    task = TASKS[task_id]
    task["status"] = "running"

    timestamps: List[Dict[str, Any]] = []
    audio_chunks: List[bytes] = []
    current_time = 0.0

    timeout = aiohttp.ClientTimeout(total=SOVITS_SINGLE_TIMEOUT)
    holding_lock = False
    try:
        if engine == "sovits":
            await _weights_lock.acquire()
            holding_lock = True
            try:
                await apply_voice_weights(sovits_weights, gpt_weights)
            except SovitsError as e:
                task.update({"status": "error", "message": f"加载音色权重失败: {e}"})
                return
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for v_text in verse_texts:
                if not v_text.strip():
                    continue

                try:
                    audio_data = await sovits_call(
                        text=v_text,
                        text_lang=language,
                        ref_audio_path=abs_ref_path,
                        prompt_text=prompt_text,
                        prompt_lang=prompt_language,
                        speed=speed,
                        session=session,
                        engine=engine,
                        model_exp=voice_exp,
                    )
                except SovitsError as e:
                    engine_name = ENGINE_NAMES.get(engine, "GPT-SoVITS")
                    task.update({
                        "status": "error",
                        "message": (
                            f"{engine_name} 报错（节 {task['completed'] + 1}/"
                            f"{task['total']}）: {e}"
                        ),
                    })
                    return

                with wave.open(io.BytesIO(audio_data), "rb") as w:
                    frames = w.getnframes()
                    rate = w.getframerate()
                    duration = frames / float(rate)

                timestamps.append({
                    "start": current_time,
                    "end": current_time + duration,
                    "text": v_text,
                })
                current_time += duration
                audio_chunks.append(audio_data)
                task["completed"] += 1

        if not audio_chunks:
            task.update({"status": "error", "message": "未生成任何音频块"})
            return

        out_filename = f"clone_{uuid.uuid4().hex}.wav"
        out_filepath = os.path.join("static", out_filename)
        with wave.open(io.BytesIO(audio_chunks[0]), "rb") as w_first:
            params = w_first.getparams()
        with wave.open(out_filepath, "wb") as out_w:
            out_w.setparams(params)
            for chunk in audio_chunks:
                with wave.open(io.BytesIO(chunk), "rb") as w:
                    out_w.writeframes(w.readframes(w.getnframes()))

        apply_audio_effect(out_filepath, effect)  # 音色效果后处理(可选)

        engine_name = ENGINE_NAMES.get(engine, "GPT-SoVITS")
        task.update({
            "status": "success",
            "audio_url": f"/api/static/{out_filename}",
            "normalized_text": f"[{engine_name} {language} 分节克隆完成]",
            "message": "声音克隆成功",
            "timestamps": timestamps,
        })

    except Exception as e:
        engine_name = ENGINE_NAMES.get(engine, "GPT-SoVITS")
        task.update({
            "status": "error",
            "message": f"连接 {engine_name} 失败: {e}",
        })
    finally:
        if holding_lock:
            _weights_lock.release()


async def create_clone_task(
    text: str,
    language: str,
    speed: float,
    emotion: str,  # 保留参数兼容路由，当前 GPT-SoVITS 调用未使用
    prompt_text: Optional[str],
    prompt_language: Optional[str],
    reference_audio: Optional[UploadFile],
    verses_json: Optional[str] = None,
    preset_id: Optional[str] = None,
    engine: str = "sovits",
    voice_exp: Optional[str] = None,
    effect: Optional[str] = None,
    sovits_weights: Optional[str] = None,
    gpt_weights: Optional[str] = None,
) -> Dict[str, Any]:
    """创建一个声音克隆任务，立即返回 task_id 与节数。

    参考来源三选一(优先级从上到下):
    - preset_id: 复用预设里的参考音频/文本(及其绑定的微调权重)。
    - voice_exp: 按音色名从训练数据自动取参考，供零样本与 CosyVoice3 微调共用。
    - sovits_weights + gpt_weights: GPT-SoVITS 微调音色兼容路径。
    - reference_audio + prompt_text + prompt_language: 零样本克隆。
    engine: "sovits"、"f5"、"cosyvoice"、"cosyvoice3" 或 "cosyvoice3_sft"
    """
    _gc_tasks()
    if engine not in ENGINE_URLS:
        raise ValueError(f"unknown engine: {engine}")

    if preset_id:
        # 延迟 import 避免循环
        from service.preset_service import get_preset
        preset = get_preset(preset_id)
        if preset is None:
            raise ValueError(f"preset not found: {preset_id}")
        abs_ref_path = os.path.abspath(preset["ref_audio_path"])
        prompt_text = preset["prompt_text"]
        prompt_language = preset["prompt_lang"]
        sovits_weights = preset.get("sovits_weights")  # 微调音色: 绑定的权重
        gpt_weights = preset.get("gpt_weights")
    elif voice_exp:
        ref = auto_reference_for_exp(voice_exp)
        if ref is None:
            raise ValueError("找不到该音色的训练参考样本, 无法自动取参考")
        raw_wav, ref_text, ref_lang = ref
        clean_ref = os.path.join("static", f"autoref_{uuid.uuid4().hex}.wav")
        if not make_reference_wav(raw_wav, clean_ref, max_sec=9.0):
            raise ValueError("参考音频转换失败")
        abs_ref_path = os.path.abspath(clean_ref)
        prompt_text = ref_text or ""
        prompt_language = ref_lang or prompt_language or language
    elif sovits_weights and gpt_weights:
        # 微调音色直接合成: 从训练数据自动取一条参考(免上传/免建预设)。
        # 训练切片可能是逐行录音的 webm/opus(命名成 .wav), 或时长超 10s -> 统一用 ffmpeg
        # 转成干净的 16k 单声道 wav 并截到 <=9s, 满足 3~10s 且各引擎都能读(CosyVoice2 用
        # soundfile 严格, 直接读原切片会 Format not recognised / 时长超范围)。
        ref = auto_reference_for_weights(sovits_weights)
        if ref is None:
            raise ValueError("找不到该音色的训练参考样本, 无法自动取参考")
        raw_wav, ref_text, ref_lang = ref
        clean_ref = os.path.join("static", f"autoref_{uuid.uuid4().hex}.wav")
        if not make_reference_wav(raw_wav, clean_ref, max_sec=9.0):
            raise ValueError("参考音频转换失败")
        abs_ref_path = os.path.abspath(clean_ref)
        prompt_text = ref_text or ""
        prompt_language = ref_lang or prompt_language or language
    else:
        if reference_audio is None or not prompt_text or not prompt_language:
            raise ValueError("missing reference_audio / prompt_text / prompt_language")
        abs_ref_path = await _prepare_reference_audio(reference_audio)

    verse_texts = _parse_verses(text, verses_json)
    total = sum(1 for v in verse_texts if v.strip())

    task_id = uuid.uuid4().hex
    TASKS[task_id] = {
        "status": "pending",
        "engine": engine,
        "total": total,
        "completed": 0,
        "created_at": time.time(),
    }

    asyncio.create_task(_run_clone_task(
        task_id=task_id,
        text=text,
        language=language,
        speed=speed,
        prompt_text=prompt_text,
        prompt_language=prompt_language,
        abs_ref_path=abs_ref_path,
        verse_texts=verse_texts,
        engine=engine,
        voice_exp=voice_exp,
        sovits_weights=sovits_weights,
        gpt_weights=gpt_weights,
        effect=effect,
    ))

    return {"task_id": task_id, "total": total}


def get_clone_task(task_id: str) -> Optional[Dict[str, Any]]:
    return TASKS.get(task_id)
