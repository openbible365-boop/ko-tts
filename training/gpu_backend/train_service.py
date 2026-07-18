"""一键微调训练: 收 ko-tts 导出的数据集 zip → 落盘 → 后台跑 format.sh + train.sh。

数据集 zip 结构(ko-tts /export/dataset.zip): wavs/<id>.wav + train.list
  train.list 每行: wavs/<id>.wav|说话人|语种|文本
落盘到 YUYIN_DATA_DIR/<exp>/, 重写成绝对路径 train_ready.list, 再调脚本训练。

并发: GPU 单卡, 同时只跑一个训练任务(_train_lock)。任务状态在内存 JOBS。
鉴权: 由路由层校验 X-Train-Token == TRAIN_TOKEN。
"""

import asyncio
import io
import os
import shutil
import time
import uuid
import zipfile
from typing import Any, Dict, Optional

GPT_SOVITS_DIR = os.environ.get("GPT_SOVITS_DIR", "/www/yuyin/GPT-SoVITS")
YUYIN_DATA_DIR = os.environ.get("YUYIN_DATA_DIR", "/www/yuyin/data")
TRAIN_TOKEN = os.environ.get("TRAIN_TOKEN", "")
COSYVOICE_DIR = os.environ.get("COSYVOICE_DIR", "/opt/tts/CosyVoice")
COSYVOICE_TRAINING_DIR = os.path.join(COSYVOICE_DIR, "training_ko_tts")
COSYVOICE_DATASET_DIR = os.environ.get(
    "COSYVOICE_DATASET_DIR", "/opt/tts/cosyvoice3-datasets"
)
COSYVOICE_WORK_DIR = os.environ.get(
    "COSYVOICE_WORK_DIR", "/opt/tts/cosyvoice3-sft"
)
COSYVOICE_MODEL_ROOT = os.environ.get(
    "COSYVOICE_MODEL_ROOT", os.path.join(COSYVOICE_DIR, "pretrained_models")
)
COSYVOICE_PYTHON = os.environ.get(
    "COSYVOICE_PYTHON", "/opt/tts/cosyvoice3_venv/bin/python"
)
INFERENCE_SERVICES = (
    "yuyin-sovits.service",
    "yuyin-f5.service",
    "yuyin-cosyvoice.service",
    "yuyin-cosyvoice3.service",
    "yuyin-cosyvoice3-sft-engine.service",
)

JOBS: Dict[str, Dict[str, Any]] = {}
_train_lock = asyncio.Lock()
_LOG_TAIL = 6000  # 每个任务保留的日志尾长度

# 单卡: 全局同时只允许一个训练。用一个同步占位变量(在 start_training 里无 await 地
# 检查并置位)杜绝并发提交的竞态; _train_lock 仅作运行期的二次保险。
_active_job: Optional[str] = None

# 超时看门狗: 卡死的脚本会被杀掉, 避免 _active_job 永久占用导致之后全部 409。
FORMAT_TIMEOUT = float(os.environ.get("FORMAT_TIMEOUT", "1800"))   # 30 min
TRAIN_TIMEOUT = float(os.environ.get("TRAIN_TIMEOUT", "7200"))     # 2 h
_JOBS_KEEP = 50  # 内存里最多保留多少条历史任务


def _gc_jobs() -> None:
    """只保留最近 _JOBS_KEEP 条已结束任务, 防 JOBS 无限增长。"""
    done = [(j.get("created_at", 0), jid) for jid, j in JOBS.items()
            if jid != _active_job and j.get("status") in ("success", "error")]
    if len(done) > _JOBS_KEEP:
        for _, jid in sorted(done)[:-_JOBS_KEEP]:
            JOBS.pop(jid, None)


def cleanup_orphans() -> None:
    """后端启动时调用: 杀掉上一个进程残留的孤儿训练子进程(否则会和新训练抢 GPU)。

    后端被重启(部署/崩溃)时, 它启动的 format/train 子进程可能被 init 收养继续跑;
    新进程的 _active_job 为空, 不知道它们存在 -> 启动时清掉。
    """
    import subprocess
    for pat in (
        "GPT_SoVITS/s2_train.py",
        "GPT_SoVITS/s1_train.py",
        "GPT_SoVITS/s2_train_v3_lora.py",
        "cosyvoice/bin/train.py",
        "run_cosyvoice3_sft.sh",
    ):
        try:
            subprocess.run(["pkill", "-9", "-f", pat], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            pass
    subprocess.run(
        ["systemctl", "--no-block", "start", *INFERENCE_SERVICES],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def valid_exp(name: str) -> bool:
    """exp 名: 非空、无路径分隔/穿越/空白; 用 exec 形式调脚本, 不走 shell, 故只防路径与空白。"""
    if not name or len(name) > 64:
        return False
    if any(c in name for c in "/\\\t\n\r ") or ".." in name:
        return False
    return True


def is_busy() -> bool:
    return _active_job is not None


def active_job_info() -> Optional[Dict[str, Any]]:
    """当前占用训练的任务(供前端显示"谁在训"); 空闲则 None。"""
    return JOBS.get(_active_job) if _active_job else None


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return JOBS.get(job_id)


def _abs_list(data_dir: str) -> Optional[str]:
    """把 zip 里的 train.list(相对路径) 重写成 train_ready.list(绝对路径)。"""
    src = os.path.join(data_dir, "train.list")
    if not os.path.exists(src):
        return None
    out = []
    with open(src, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) != 4:
                continue
            rel, spk, lang, text = parts
            name = os.path.basename(rel)
            out.append(f"{os.path.join(data_dir, 'wavs', name)}|{spk}|{lang}|{text}")
    if not out:
        return None
    dst = os.path.join(data_dir, "train_ready.list")
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return dst


async def start_training(
    exp: str,
    zip_bytes: bytes,
    trainer: str = "sovits",
    batch: int = 4,
    sovits_ep: int = 8,
    gpt_ep: int = 15,
    cosyvoice3_ep: int = 10,
    save_every: int = 4,
) -> str:
    """解包数据集并发起后台训练, 返回 job_id。busy/非法入参抛异常。

    并发安全: 本函数从占位到 return 之间没有 await, 故在 asyncio 单线程下是原子的;
    并发的两次提交里只有第一个能置位 _active_job, 第二个会被 busy 拒绝(上层返回 409)。
    """
    global _active_job
    if not valid_exp(exp):
        raise ValueError("非法的 exp 名(不能含 / 空格 等)")
    if trainer not in ("sovits", "cosyvoice3"):
        raise ValueError(f"不支持的训练引擎: {trainer}")
    if _active_job is not None:
        busy_exp = (JOBS.get(_active_job) or {}).get("exp", "?")
        raise RuntimeError(f"已有训练任务在进行(音色「{busy_exp}」), 请等它结束再试")

    if trainer == "sovits":
        save_every = max(1, min(save_every, sovits_ep, gpt_ep))
    cosyvoice3_ep = max(1, min(cosyvoice3_ep, 50))
    job_id = uuid.uuid4().hex
    _active_job = job_id  # ← 同步占位; 此后到 return 无 await, 杜绝并发

    try:
        # 重训同名音色: 清旧数据/格式化缓存/旧权重, 保证用最新切片干净重训
        import glob
        data_dir = os.path.join(YUYIN_DATA_DIR, exp)
        shutil.rmtree(data_dir, ignore_errors=True)
        if trainer == "sovits":
            shutil.rmtree(os.path.join(GPT_SOVITS_DIR, "logs", exp), ignore_errors=True)
            esc = glob.escape(exp)
            for old in (
                glob.glob(os.path.join(GPT_SOVITS_DIR, "SoVITS_weights_v2", f"{esc}_e*.pth"))
                + glob.glob(os.path.join(GPT_SOVITS_DIR, "GPT_weights_v2", f"{esc}-e*.ckpt"))
            ):
                try:
                    os.remove(old)
                except OSError:
                    pass
        else:
            shutil.rmtree(os.path.join(COSYVOICE_DATASET_DIR, exp), ignore_errors=True)
            shutil.rmtree(os.path.join(COSYVOICE_WORK_DIR, exp), ignore_errors=True)
        os.makedirs(os.path.join(data_dir, "wavs"), exist_ok=True)
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for m in zf.namelist():  # 只解 wavs/*.wav 与 train.list, 防路径穿越
                    if m.endswith("/"):
                        continue
                    base = os.path.basename(m)
                    if m == "train.list":
                        zf.extract(m, data_dir)
                    elif m.startswith("wavs/") and base.endswith(".wav"):
                        with zf.open(m) as src, open(os.path.join(data_dir, "wavs", base), "wb") as dst:
                            shutil.copyfileobj(src, dst)
        except zipfile.BadZipFile:
            raise ValueError("上传的不是有效 zip")

        list_path = _abs_list(data_dir)
        if list_path is None:
            raise ValueError("数据集为空或 train.list 缺失")
        n = sum(1 for _ in open(list_path, encoding="utf-8"))
    except Exception:
        _active_job = None  # 准备阶段失败 -> 释放占位, 否则会永久 busy
        raise

    JOBS[job_id] = {
        "exp": exp, "trainer": trainer, "status": "queued", "segments": n,
        "created_at": time.time(), "log": "", "weights": None,
    }
    _gc_jobs()
    if trainer == "cosyvoice3":
        asyncio.create_task(_run_cosyvoice3(job_id, exp, list_path, cosyvoice3_ep))
    else:
        asyncio.create_task(
            _run_sovits(job_id, exp, list_path, batch, sovits_ep, gpt_ep, save_every)
        )
    return job_id


def delete_voice(exp: str) -> Dict[str, Any]:
    """删除一个训练好的音色(权重 + 格式化缓存 + 数据)。不能删正在训练的。"""
    import glob
    if not valid_exp(exp):
        raise ValueError("非法 exp 名")
    if _active_job and (JOBS.get(_active_job) or {}).get("exp") == exp:
        raise RuntimeError("该音色正在训练, 不能删除")
    esc = glob.escape(exp)
    removed = []
    for p in (
        glob.glob(os.path.join(GPT_SOVITS_DIR, "SoVITS_weights_v2", f"{esc}_e*.pth"))
        + glob.glob(os.path.join(GPT_SOVITS_DIR, "GPT_weights_v2", f"{esc}-e*.ckpt"))
    ):
        try:
            os.remove(p)
            removed.append(os.path.basename(p))
        except OSError:
            pass
    cosyvoice_model = os.path.join(
        COSYVOICE_MODEL_ROOT, f"{exp}-CosyVoice3-SFT"
    )
    if os.path.isdir(cosyvoice_model):
        shutil.rmtree(cosyvoice_model, ignore_errors=True)
        removed.append(f"{exp}-CosyVoice3-SFT")
    shutil.rmtree(os.path.join(COSYVOICE_DATASET_DIR, exp), ignore_errors=True)
    shutil.rmtree(os.path.join(COSYVOICE_WORK_DIR, exp), ignore_errors=True)
    shutil.rmtree(os.path.join(GPT_SOVITS_DIR, "logs", exp), ignore_errors=True)
    shutil.rmtree(os.path.join(YUYIN_DATA_DIR, exp), ignore_errors=True)
    return {"exp": exp, "removed": removed}


async def _sh(
    job: Dict[str, Any], args: list, timeout: float, cwd: Optional[str] = None
) -> bool:
    """exec 形式跑脚本(不走 shell, 无注入), 输出尾部喂进 job['log']; 超时则杀掉。返回是否成功。"""
    env = dict(os.environ)
    # 缓解显存碎片导致的 OOM(与常驻推理共用一张卡)
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd or GPT_SOVITS_DIR, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None

    async def _pump() -> int:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            job["log"] = (job["log"] + line.decode("utf-8", "replace"))[-_LOG_TAIL:]
        return await proc.wait()

    try:
        rc = await asyncio.wait_for(_pump(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass
        job["message"] = f"超时(>{int(timeout)}s)被终止"
        return False
    if rc != 0 and "out of memory" in job.get("log", "").lower():
        job["message"] = "显存不足(OOM): 请减小 batch 或减少切片/重切短一些"
    return rc == 0


async def _run_sovits(
    job_id, exp, list_path, batch, sovits_ep, gpt_ep, save_every
) -> None:
    global _active_job
    job = JOBS[job_id]
    async with _train_lock:
        try:
            job["status"] = "formatting"
            if not await _sh(job, ["./format.sh", exp, list_path], FORMAT_TIMEOUT):
                job["status"] = "error"; job.setdefault("message", "格式化失败"); return
            job["status"] = "training"
            if not await _sh(
                job, ["./train.sh", exp, str(batch), str(sovits_ep), str(gpt_ep), str(save_every)],
                TRAIN_TIMEOUT,
            ):
                job["status"] = "error"; job.setdefault("message", "训练失败"); return
            # 收集产出的权重(相对 GPT-SoVITS 目录)
            import glob
            sov = sorted(os.path.basename(p) for p in glob.glob(os.path.join(GPT_SOVITS_DIR, "SoVITS_weights_v2", f"{exp}_e*.pth")))
            gpt = sorted(os.path.basename(p) for p in glob.glob(os.path.join(GPT_SOVITS_DIR, "GPT_weights_v2", f"{exp}-e*.ckpt")))
            job["weights"] = {"sovits": sov, "gpt": gpt}
            job["status"] = "success"
            job["message"] = f"训练完成: SoVITS {len(sov)} 份 / GPT {len(gpt)} 份"
        except Exception as e:  # noqa: BLE001
            job["status"] = "error"
            job["message"] = f"异常: {e}"
        finally:
            _active_job = None  # 释放占位, 允许下一个训练


async def _set_inference_services(action: str, job: Dict[str, Any]) -> bool:
    return await _sh(
        job,
        ["systemctl", action, *INFERENCE_SERVICES],
        300,
        cwd="/",
    )


async def _run_cosyvoice3(
    job_id: str, exp: str, list_path: str, epochs: int
) -> None:
    global _active_job
    job = JOBS[job_id]
    dataset_dir = os.path.join(COSYVOICE_DATASET_DIR, exp)
    run_script = os.path.join(COSYVOICE_TRAINING_DIR, "run_cosyvoice3_sft.sh")
    prepare_script = os.path.join(
        COSYVOICE_TRAINING_DIR, "prepare_cosyvoice3_dataset.py"
    )
    services_stopped = False

    async with _train_lock:
        try:
            job.update(
                status="preparing",
                message="拆分训练/验证集并生成 CosyVoice3 元数据…",
            )
            if not await _sh(
                job,
                [
                    COSYVOICE_PYTHON,
                    prepare_script,
                    "--train-list",
                    list_path,
                    "--data-root",
                    os.path.dirname(list_path),
                    "--out",
                    dataset_dir,
                    "--dev-ratio",
                    "0.15",
                ],
                FORMAT_TIMEOUT,
                cwd=COSYVOICE_DIR,
            ):
                job["status"] = "error"
                job.setdefault("message", "CosyVoice3 数据拆分失败")
                return

            job.update(
                status="extracting",
                message="提取说话人嵌入、语音 token 并生成 parquet…",
            )
            if not await _sh(
                job,
                [
                    "/usr/bin/env",
                    "COSYVOICE_STAGE=prepare",
                    run_script,
                    dataset_dir,
                    exp,
                    str(epochs),
                ],
                FORMAT_TIMEOUT,
                cwd=COSYVOICE_DIR,
            ):
                job["status"] = "error"
                job.setdefault("message", "CosyVoice3 特征提取失败")
                return

            job.update(status="training", message=f"CosyVoice3 LLM 微调 {epochs} 轮…")
            if not await _set_inference_services("stop", job):
                job["status"] = "error"
                job.setdefault("message", "暂停 GPU 推理服务失败")
                return
            services_stopped = True
            if not await _sh(
                job,
                [run_script, dataset_dir, exp, str(epochs)],
                TRAIN_TIMEOUT,
                cwd=COSYVOICE_DIR,
            ):
                job["status"] = "error"
                job.setdefault("message", "CosyVoice3 LLM 微调失败")
                return

            job.update(status="publishing", message="平均最佳检查点并发布微调模型…")
            model_dir = os.path.join(
                COSYVOICE_MODEL_ROOT, f"{exp}-CosyVoice3-SFT"
            )
            if not os.path.isfile(os.path.join(model_dir, "llm.pt")):
                job["status"] = "error"
                job["message"] = "训练完成但未找到发布后的 llm.pt"
                return
            job["weights"] = {"cosyvoice3_sft": [exp]}
            job["status"] = "success"
            job["message"] = f"CosyVoice3 微调完成: {exp}"
        except Exception as exc:  # noqa: BLE001
            job["status"] = "error"
            job["message"] = f"CosyVoice3 异常: {exc}"
        finally:
            if services_stopped:
                await _set_inference_services("start", job)
            _active_job = None
