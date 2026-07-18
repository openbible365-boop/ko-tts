import os
import json
import asyncio
import urllib.request
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Header
from schema.tts_schema import (
    TTSRequest,
    TTSResponse,
    TaskCreateResponse,
    TaskStatusResponse,
    PresetItem,
)
from service.tts_service import (
    generate_tts,
    create_clone_task,
    get_clone_task,
    list_voice_models,
)
from service.preset_service import (
    create_preset,
    delete_preset,
    list_presets,
)

router = APIRouter()

# 公网后端鉴权: 前端带 X-API-Key; 服务器间(ko-tts -> /train)带 X-Train-Token。
# 未配置 API_KEY 时不强制(便于灰度切换); 配上即对所有 /api 路由(除静态挂载)生效。
API_KEY = os.environ.get("API_KEY", "")


async def require_api_key(
    x_api_key: Optional[str] = Header(None),
    x_train_token: Optional[str] = Header(None),
) -> None:
    if not API_KEY:
        return
    if x_api_key == API_KEY:
        return
    from service.train_service import TRAIN_TOKEN
    if TRAIN_TOKEN and x_train_token == TRAIN_TOKEN:
        return
    raise HTTPException(status_code=401, detail="missing or invalid API key")

# 获取项目根目录 (yuyin/)
# routes.py 在 backend/api/ 下，所以需向上退三级
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@router.post("/tts/generate", response_model=TTSResponse)
async def api_generate_tts(request: TTSRequest):
    """
    接收前端请求，调用标准 TTS 服务
    """
    return await generate_tts(request)

@router.post("/tts/generate_clone", response_model=TaskCreateResponse)
async def generate_clone(
    text: str = Form(...),
    language: str = Form(...),
    speed: float = Form(1.0),
    emotion: str = Form("neutral"),
    prompt_text: Optional[str] = Form(None),
    prompt_language: Optional[str] = Form(None),
    reference_audio: Optional[UploadFile] = File(None),
    verses_json: Optional[str] = Form(None),
    preset_id: Optional[str] = Form(None),
    engine: str = Form("sovits"),
    voice_exp: Optional[str] = Form(None),
    effect: Optional[str] = Form(None),
    sovits_weights: Optional[str] = Form(None),
    gpt_weights: Optional[str] = Form(None),
):
    """
    创建声音克隆任务（异步）。立即返回 task_id，前端轮询 /tts/task/{task_id} 拿进度与结果。
    传 preset_id 时复用预设的参考音频与文本，prompt_text / prompt_language / reference_audio 可不传。
    传 sovits_weights + gpt_weights(微调音色权重)时，免建预设直接合成——从该音色训练数据自动取参考。
    engine: "sovits"、"f5"、"cosyvoice"、"cosyvoice3" 或 "cosyvoice3_sft";
    effect: 合成后音色效果(bright/warm/reverb/hoarse, 可空)
    """
    try:
        result = await create_clone_task(
            text, language, speed, emotion,
            prompt_text, prompt_language, reference_audio, verses_json, preset_id,
            engine=engine, voice_exp=voice_exp, effect=effect,
            sovits_weights=sovits_weights, gpt_weights=gpt_weights,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return TaskCreateResponse(**result)


@router.get("/voice-models")
async def api_list_voice_models():
    """列出 GPU 上的微调权重和独立零样本音色。"""
    return list_voice_models()


@router.post("/zero-shot-voices")
async def api_create_zero_shot_voice(
    exp: str = Form(...),
    prompt_text: str = Form(...),
    prompt_language: str = Form(...),
    reference_audio: UploadFile = File(...),
    x_train_token: Optional[str] = Header(None),
):
    """保存一个独立零样本音色的参考 WAV 与文本。"""
    from service.train_service import TRAIN_TOKEN
    from service.zero_shot_service import save_zero_shot_voice

    if not TRAIN_TOKEN or x_train_token != TRAIN_TOKEN:
        raise HTTPException(status_code=401, detail="invalid train token")
    audio = await reference_audio.read()
    try:
        metadata = save_zero_shot_voice(
            exp,
            audio,
            prompt_text,
            prompt_language,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", **metadata}


@router.post("/train")
async def api_train(
    exp: str = Form(...),
    dataset: Optional[UploadFile] = File(None),
    dataset_url: Optional[str] = Form(None),
    trainer: str = Form("sovits"),
    batch: int = Form(4),
    sovits_ep: int = Form(8),
    gpt_ep: int = Form(15),
    cosyvoice3_ep: int = Form(10),
    save_every: int = Form(4),
    x_train_token: Optional[str] = Header(None),
):
    """一键微调: 收数据集 zip(wavs/+train.list), 后台跑 format+train。需 X-Train-Token 鉴权。

    数据集两种传法(二选一):
    - dataset: 直接 multipart 上传 zip(小数据集)。
    - dataset_url: 预签名下载 URL(ko-tts 走 R2 中转, 绕开 Cloudflare 隧道 100MB 请求体上限)。
    """
    from service.train_service import TRAIN_TOKEN, active_job_info, start_training
    if not TRAIN_TOKEN or x_train_token != TRAIN_TOKEN:
        raise HTTPException(status_code=401, detail="invalid train token")
    if dataset_url:
        def _download() -> bytes:
            with urllib.request.urlopen(dataset_url, timeout=300) as resp:
                return resp.read()
        try:
            zip_bytes = await asyncio.to_thread(_download)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"下载数据集失败: {e}")
    elif dataset is not None:
        zip_bytes = await dataset.read()
    else:
        raise HTTPException(status_code=400, detail="缺少数据集: 需提供 dataset 或 dataset_url")
    try:
        job_id = await start_training(
            exp,
            zip_bytes,
            trainer=trainer,
            batch=batch,
            sovits_ep=sovits_ep,
            gpt_ep=gpt_ep,
            cosyvoice3_ep=cosyvoice3_ep,
            save_every=save_every,
        )
    except RuntimeError as e:  # busy
        active = active_job_info()
        if (
            active
            and active.get("exp") == exp
            and active.get("trainer", "sovits") == trainer
        ):
            return {"job_id": active["job_id"], "attached": True}
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"job_id": job_id}


@router.delete("/voice/{exp}")
async def api_delete_voice(exp: str):
    """删除一个音色的零样本参考、权重、缓存与数据。"""
    from service.train_service import delete_voice
    try:
        return delete_voice(exp)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/train/{job_id}")
async def api_train_status(job_id: str):
    from service.train_service import get_job
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": job_id,
        "exp": job["exp"],
        "trainer": job.get("trainer", "sovits"),
        "status": job["status"],
        "segments": job.get("segments"),
        "message": job.get("message"),
        "weights": job.get("weights"),
        "log_tail": (job.get("log", "") or "")[-1500:],
    }


@router.get("/presets", response_model=List[PresetItem])
async def api_list_presets():
    """列出所有已保存的声音预设。"""
    return [PresetItem(**p) for p in list_presets()]


@router.post("/presets", response_model=PresetItem)
async def api_create_preset(
    language: str = Form(...),
    prompt_text: Optional[str] = Form(None),
    prompt_language: Optional[str] = Form(None),
    reference_audio: Optional[UploadFile] = File(None),
    name: Optional[str] = Form(None),
    sovits_weights: Optional[str] = Form(None),
    gpt_weights: Optional[str] = Form(None),
):
    """创建一个声音预设，并立即用它生成一段固定文案的试听音频。

    传 sovits_weights + gpt_weights 即为"微调音色"(引擎主机上相对 GPT-SoVITS 目录的
    权重路径); 此时**无需上传 reference_audio / prompt_text** —— 后端会自动从该音色的
    训练样本里取一条作参考。零样本克隆则必须上传 reference_audio + prompt_text。
    """
    try:
        entry = await create_preset(
            name=name,
            language=language,
            prompt_text=prompt_text,
            prompt_language=prompt_language,
            reference_audio=reference_audio,
            sovits_weights=(sovits_weights or None),
            gpt_weights=(gpt_weights or None),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return PresetItem(**entry)


@router.delete("/presets/{preset_id}")
async def api_delete_preset(preset_id: str):
    ok = await delete_preset(preset_id)
    if not ok:
        raise HTTPException(status_code=404, detail="preset not found")
    return {"status": "ok"}


@router.get("/tts/task/{task_id}", response_model=TaskStatusResponse)
async def get_clone_task_status(task_id: str):
    """
    查询声音克隆任务状态 / 进度 / 最终音频。
    """
    task = get_clone_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskStatusResponse(
        task_id=task_id,
        status=task["status"],
        total=task.get("total", 0),
        completed=task.get("completed", 0),
        engine=task.get("engine"),
        audio_url=task.get("audio_url"),
        normalized_text=task.get("normalized_text"),
        message=task.get("message"),
        timestamps=task.get("timestamps"),
    )

@router.get("/data/books")
async def get_books(lang: str = "zh", data_type: str = "cnvs"):
    dir_path = os.path.join(PROJECT_ROOT, "data", lang, data_type)
    if not os.path.exists(dir_path):
        return []
    files = os.listdir(dir_path)
    books = [f.split(".")[0] for f in files if f.endswith(".json")]
    return sorted(books)

@router.get("/data/book_info")
async def get_book_info(book: str, lang: str = "zh", data_type: str = "cnvs"):
    file_path = os.path.join(PROJECT_ROOT, "data", lang, data_type, f"{book}.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Book not found")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    chapters = []
    if data_type == "summaries":
        chapters = sorted([int(k) for k in data.keys() if k.isdigit()])
    else:
        if "chapters" in data:
            chapters = sorted([ch["chapter"] for ch in data["chapters"]])

    return {"book": book, "chapters": chapters}

@router.get("/data/chapter")
async def get_chapter_text(book: str, chapter: int, lang: str = "zh", data_type: str = "cnvs"):
    file_path = os.path.join(PROJECT_ROOT, "data", lang, data_type, f"{book}.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Book not found")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data_type == "summaries":
        chapter_str = str(chapter)
        if chapter_str in data and "content" in data[chapter_str]:
            return {"verses": [{"verse": 1, "text": data[chapter_str]["content"]}]}
    else:
        if "chapters" in data:
            for ch in data["chapters"]:
                if ch["chapter"] == chapter:
                    # Return the raw array of verses
                    return {"verses": ch.get("verses", [])}

    raise HTTPException(status_code=404, detail="Chapter not found")
