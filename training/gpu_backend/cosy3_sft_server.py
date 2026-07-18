"""Dynamic Fun-CosyVoice3 fine-tuned inference service.

Only one fine-tuned model stays on the GPU. Requests identify the experiment
with ``model_exp``; switching voices releases the previous model before loading
the requested published model directory.
"""

from __future__ import annotations

import gc
import io
import os
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, "/opt/tts/CosyVoice")
sys.path.insert(0, "/opt/tts/CosyVoice/third_party/Matcha-TTS")

import torch
import torchaudio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from cosyvoice.cli.cosyvoice import AutoModel


app = FastAPI()
MODEL = None
CURRENT_EXP: str | None = None
MODEL_ROOT = Path(
    os.environ.get(
        "COSYVOICE3_SFT_MODEL_ROOT", "/opt/tts/CosyVoice/pretrained_models"
    )
).resolve()
DEFAULT_EXP = os.environ.get("COSYVOICE3_SFT_DEFAULT_EXP", "").strip()
SYSTEM_PROMPT = "You are a helpful assistant.<|endofprompt|>"
INFERENCE_LOCK = threading.Lock()


def _model_dir(exp: str) -> Path:
    if not exp or len(exp) > 64 or not re.fullmatch(r"[^/\\\s]+", exp):
        raise ValueError("invalid model_exp")
    model_dir = (MODEL_ROOT / f"{exp}-CosyVoice3-SFT").resolve()
    if model_dir.parent != MODEL_ROOT or not (model_dir / "llm.pt").is_file():
        raise ValueError(f"CosyVoice3 fine-tuned model not found: {exp}")
    return model_dir


def _load_model(exp: str) -> None:
    global MODEL, CURRENT_EXP
    if MODEL is not None and CURRENT_EXP == exp:
        return
    model_dir = _model_dir(exp)
    old_model = MODEL
    MODEL = None
    CURRENT_EXP = None
    if old_model is not None:
        del old_model
    gc.collect()
    torch.cuda.empty_cache()
    MODEL = AutoModel(model_dir=str(model_dir), fp16=True)
    CURRENT_EXP = exp
    print(
        f"[cosyvoice3-sft] loaded exp={exp}, "
        f"model={type(MODEL).__name__}, sr={MODEL.sample_rate}",
        flush=True,
    )


@app.on_event("startup")
def load_default_model() -> None:
    if DEFAULT_EXP:
        try:
            _load_model(DEFAULT_EXP)
        except Exception as exc:
            print(f"[cosyvoice3-sft] default model not loaded: {exc}", flush=True)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "loaded": MODEL is not None,
        "engine": "cosyvoice3_sft",
        "model_exp": CURRENT_EXP,
        "sample_rate": MODEL.sample_rate if MODEL is not None else None,
    }


@app.post("/tts")
async def tts(req: Request):
    body = await req.json()
    text = (body.get("text") or "").strip()
    ref = body.get("ref_audio_path")
    prompt_text = (body.get("prompt_text") or "").strip()
    model_exp = (body.get("model_exp") or "").strip()
    speed = float(body.get("speed_factor", 1.0) or 1.0)

    if not text or not ref or not model_exp:
        return JSONResponse(
            {"message": "missing text, ref_audio_path or model_exp"},
            status_code=400,
        )
    if not os.path.isfile(ref):
        return JSONResponse({"message": "reference audio not found"}, status_code=400)

    try:
        with INFERENCE_LOCK, torch.inference_mode():
            _load_model(model_exp)
            if prompt_text:
                output = MODEL.inference_zero_shot(
                    text,
                    SYSTEM_PROMPT + prompt_text,
                    ref,
                    stream=False,
                    speed=speed,
                )
            else:
                output = MODEL.inference_cross_lingual(
                    SYSTEM_PROMPT + text,
                    ref,
                    stream=False,
                    speed=speed,
                )
            chunks = [item["tts_speech"].detach().cpu() for item in output]

        if not chunks:
            return JSONResponse(
                {"message": "cosyvoice3 fine-tuned model returned no audio"},
                status_code=500,
            )
        wav = torch.cat(chunks, dim=1) if len(chunks) > 1 else chunks[0]
        buf = io.BytesIO()
        torchaudio.save(buf, wav, MODEL.sample_rate, format="wav")
        return Response(content=buf.getvalue(), media_type="audio/wav")
    except Exception as exc:
        return JSONResponse(
            {"message": f"cosyvoice3 fine-tuned error: {exc}"},
            status_code=500,
        )
