"""approved segments 导出 (训练侧消费): JSONL manifest + stats。

仅 reviewer/admin 可访问。manifest 是 StreamingResponse, 单次共享一个 s3
client 上下文批量签 URL, 避免 N 段 = N 次 client 开关销。
"""

import asyncio
import io
import json
import random
import re
import uuid
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Literal

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import storage
from app.config import settings
from app.db import SessionLocal
from app.deps import SessionDep, require_role
from app.models import ContentCategory, Recording, Segment, SegmentStatus, User, UserRole
from app.schemas import ExportCategoryStats, ExportStats

router = APIRouter(prefix="/export", tags=["export"])

StaffOnly = Annotated[User, Depends(require_role(UserRole.reviewer, UserRole.admin))]

DEFAULT_URL_TTL_HOURS = 24
MAX_URL_TTL_HOURS = 24 * 7  # 7 天


@router.get("/manifest.jsonl")
async def export_manifest(
    me: StaffOnly,
    session: SessionDep,
    status: Annotated[
        list[SegmentStatus] | None,
        Query(description="包含哪些状态;默认仅 approved"),
    ] = None,
    content_category: Annotated[ContentCategory | None, Query()] = None,
    speaker: Annotated[
        str | None, Query(max_length=128, description="按声音/说话人筛选(精确匹配)")
    ] = None,
    url_ttl_hours: Annotated[
        int, Query(ge=1, le=MAX_URL_TTL_HOURS, description="预签名 URL 有效期(小时)")
    ] = DEFAULT_URL_TTL_HOURS,
) -> StreamingResponse:
    statuses = [s.value for s in (status or [SegmentStatus.approved])]
    expires_in = url_ttl_hours * 3600

    stmt = (
        select(Segment, Recording.content_category, Recording.speaker, Recording.language)
        .join(Recording, Segment.recording_id == Recording.id)
        .where(Segment.status.in_(statuses))
        .order_by(Segment.created_at)
    )
    if content_category is not None:
        stmt = stmt.where(Recording.content_category == content_category.value)
    if speaker is not None:
        stmt = stmt.where(Recording.speaker == speaker)
    rows = (await session.execute(stmt)).all()

    async def stream() -> AsyncIterator[bytes]:
        # 共享一个 R2 client, 不要 N 段 N 个 client
        async with storage._client() as s3:
            for seg, category, spk, lang in rows:
                url = await s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": settings.r2_bucket, "Key": seg.audio_key},
                    ExpiresIn=expires_in,
                )
                line = {
                    "id": str(seg.id),
                    "text": seg.text,
                    "audio_url": url,
                    "audio_key": seg.audio_key,
                    "duration_ms": seg.duration_ms,
                    "sample_rate": settings.seg_sample_rate,
                    "speaker": spk,
                    "language": lang,
                    "content_category": category,
                    "status": seg.status,
                    "reviewed_at": seg.reviewed_at.isoformat() if seg.reviewed_at else None,
                }
                yield (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="ko-tts-manifest-{datetime.now(UTC).date()}.jsonl"',
            "X-Export-Statuses": ",".join(statuses),
            "X-Export-URL-TTL-Hours": str(url_ttl_hours),
        },
    )


_README = (
    "ko-tts 数据集 (GPT-SoVITS)\n\n"
    "  wavs/<id>.wav   切片音频\n"
    "  train.list      标注表, 每行: wavs/<id>.wav|speaker|语言|文本\n\n"
    "用法: 解压后把 train.list 填进 GPT-SoVITS WebUI 的标注表路径。\n"
    "路径是相对的(相对本文件夹), 若 GPT-SoVITS 要绝对路径, 在解压目录里跑:\n"
    "  python - <<'PY'\n"
    "  import os\n"
    "  d=os.getcwd()\n"
    "  ls=[l for l in open('train.list',encoding='utf-8') if l.strip()]\n"
    "  open('train.list','w',encoding='utf-8').write(''.join(\n"
    "    os.path.join(d,l.split('|',1)[0])+'|'+l.split('|',1)[1] for l in ls))\n"
    "  PY\n"
)


async def _build_dataset_zip(
    session,
    *,
    status=None,
    content_category=None,
    speaker=None,
    language=None,
    limit=None,
) -> tuple[bytes, int]:
    """组 GPT-SoVITS 数据集 zip(wavs/ + train.list + README), 返回 (字节, 段数)。

    供 /dataset.zip 下载与 /train 转发共用。语言码默认取每条录音的 language。
    limit: 只取 N 条时**从全部素材随机抽 N 条**(而非取开头), 便于用不同条数对比训练效果。
    """
    statuses = [s.value for s in (status or [SegmentStatus.approved])]
    stmt = (
        select(Segment, Recording.speaker, Recording.language)
        .join(Recording, Segment.recording_id == Recording.id)
        .where(Segment.status.in_(statuses))
        .where(Segment.text.isnot(None))
        .where(Segment.audio_key.isnot(None))
        .order_by(Segment.created_at)
    )
    if content_category is not None:
        cat = content_category.value if hasattr(content_category, "value") else content_category
        stmt = stmt.where(Recording.content_category == cat)
    if speaker is not None:
        stmt = stmt.where(Recording.speaker == speaker)
    rows = (await session.execute(stmt)).all()

    # 只取 N 条: 从全体里**随机抽 N 条**(而非取开头), 让子集覆盖全部录音/内容;
    # 每次训练随机取样, 用不同条数(50/200/…)对比效果时更有代表性。
    if limit and 0 < limit < len(rows):
        idx = sorted(random.sample(range(len(rows)), limit))
        rows = [rows[i] for i in idx]

    # 并发从 R2 拉取音频: 串行 500 段约 2 分钟(会拖垮同步请求), 共享一个 s3 client、
    # 信号量限并发到 16, 整体降到十几秒。拉完再在内存里顺序打 zip。
    sem = asyncio.Semaphore(16)
    async with storage._client() as s3:
        async def _fetch(key: str) -> bytes:
            async with sem:
                resp = await s3.get_object(Bucket=settings.r2_bucket, Key=key)
                async with resp["Body"] as body:
                    return await body.read()

        blobs = await asyncio.gather(*(_fetch(seg.audio_key) for seg, _spk, _lang in rows))

    buf = io.BytesIO()
    lines: list[str] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for (seg, spk, rec_lang), data in zip(rows, blobs):
            arc = f"wavs/{seg.id}.wav"
            zf.writestr(arc, data)
            spk_name = spk or speaker or "speaker"
            lang = language or rec_lang or "ko"
            lines.append(f"{arc}|{spk_name}|{lang}|{seg.text.strip()}")
        zf.writestr("train.list", "\n".join(lines) + ("\n" if lines else ""))
        zf.writestr("README.txt", _README)
    return buf.getvalue(), len(lines)


@router.get("/dataset.zip")
async def export_dataset_zip(
    me: StaffOnly,
    session: SessionDep,
    status: Annotated[list[SegmentStatus] | None, Query()] = None,
    content_category: Annotated[ContentCategory | None, Query()] = None,
    speaker: Annotated[str | None, Query(max_length=128)] = None,
    language: Annotated[
        str | None,
        Query(
            max_length=8,
            description="GPT-SoVITS 语言码覆盖; 留空则按每条录音自己的 language(推荐)",
        ),
    ] = None,
) -> StreamingResponse:
    """把(默认 approved)切片打包成 GPT-SoVITS 数据集 zip: wavs/ + train.list。

    train.list 每行的语言码默认取该切片所属录音的 language(en/zh/ko), 不再
    一刀切写死 —— 否则中文录音被标成 ko 会让 GPT-SoVITS 选错 g2p。需要时仍可用
    language 查询参数强制覆盖全部。

    一次性在内存里组 zip(数据集通常几十~几百段、几十 MB, 够用); 真到上万段
    再改流式。
    """
    payload, n = await _build_dataset_zip(
        session, status=status, content_category=content_category,
        speaker=speaker, language=language,
    )
    fname = f"ko-tts-dataset-{datetime.now(UTC).date()}.zip"
    return StreamingResponse(
        iter([payload]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Export-Segments": str(n),
        },
    )


# 本地训练任务表: 打包 500 段(~100MB)要 2 分钟, 同步做会拖垮前端请求(Failed to
# fetch)。故 POST /train 立即返回本地 job_id, 打包+上传 GPU 在后台协程里跑, 前端轮询。
# 状态只在内存(后端重启即丢, 与 GPU 侧一致); 单机部署下够用。
_TRAIN_JOBS: dict[str, dict] = {}
_TRAIN_TASKS: set = set()  # 持有后台任务引用, 防止被 GC 中途回收


async def _run_train_job(
    local_id: str, speaker: str, exp: str, limit: int | None,
    sovits_ep: int, gpt_ep: int, batch: int
) -> None:
    """后台: 打包该说话人 approved 切片(limit 时随机抽 N 条)-> 上传 GPU -> 记下 GPU job_id。"""
    job = _TRAIN_JOBS[local_id]
    dataset_key = f"train-datasets/{exp}-{local_id}.zip"
    staged = False
    try:
        job.update(status="queued", message="打包已通过切片中…")
        async with SessionLocal() as session:
            payload, n = await _build_dataset_zip(session, speaker=speaker, limit=limit)
        if n == 0:
            job.update(status="error", message=f"说话人“{speaker}”没有可训练的已通过切片")
            return
        job.update(segments=n, status="queued", message=f"上传 {n} 段到 GPU 中…")
        # 经 R2 中转: GPU 走 cloudflared 隧道, Cloudflare 有 100MB 请求体上限,
        # 直传 ~100MB 的 zip 会 413。改为传到 R2 取预签名 URL, 只把 URL 发给 GPU,
        # GPU 自己从 R2 拉(下载不受该请求体限制)。
        await storage.put_bytes(dataset_key, payload, content_type="application/zip")
        staged = True
        dataset_url = await storage.presigned_get_url(dataset_key, expires_in=3600)
        form = aiohttp.FormData()
        form.add_field("exp", exp)
        form.add_field("sovits_ep", str(sovits_ep))
        form.add_field("gpt_ep", str(gpt_ep))
        form.add_field("batch", str(batch))
        form.add_field("dataset_url", dataset_url)
        timeout = aiohttp.ClientTimeout(total=600)  # 后台跑, 给足 GPU 下载+受理时间
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(
                settings.gpu_train_url, data=form,
                headers={"X-Train-Token": settings.train_token},
            ) as r:
                text = await r.text()
                if r.status != 200:
                    detail = text[:300]
                    try:
                        detail = json.loads(text).get("detail", detail)
                    except Exception:
                        pass
                    job.update(status="error", message=detail)
                    return
                gpu_job = json.loads(text)
        job.update(status="queued", gpu_job_id=gpu_job.get("job_id"), message="已提交 GPU")
    except aiohttp.ClientError as e:
        job.update(status="error", message=f"连接 GPU 训练后端失败: {e}")
    except Exception as e:  # noqa: BLE001
        job.update(status="error", message=f"打包/上传失败: {e}")
    finally:
        # GPU 在 /train 请求内同步下载完 zip 才返回, 故此处可安全清理 R2 中转文件
        if staged:
            try:
                await storage.delete_object(dataset_key)
            except Exception:  # noqa: BLE001
                pass


@router.post("/train")
async def export_train(
    me: StaffOnly,
    session: SessionDep,
    speaker: Annotated[str, Query(min_length=1, max_length=128, description="按说话人训练")],
    count: Annotated[
        int | None,
        Query(ge=1, le=100000, description="只用 N 条(随机抽样)训练; 留空=全部。用于不同条数对比"),
    ] = None,
    sovits_ep: Annotated[int, Query(ge=1, le=50)] = 8,
    gpt_ep: Annotated[int, Query(ge=1, le=50)] = 15,
    batch: Annotated[int, Query(ge=1, le=16)] = 4,
) -> dict:
    """一键微调: 立即返回本地 job_id; 打包该说话人 approved 切片 + 上传 GPU 在后台进行。

    count: 只用 N 条(从全部素材随机抽样)训练, 音色名带 _N 后缀独立成一个音色,
    便于用 50/200/500 等不同条数分别训练、在合成页对比效果。
    """
    if not settings.gpu_train_url or not settings.train_token:
        raise HTTPException(503, "训练功能未配置 (缺 gpu_train_url / train_token)")
    # 先快速 count(不下载音频)校验有无可训练切片, 空则立即报错
    n_all = await session.scalar(
        select(func.count())
        .select_from(Segment)
        .join(Recording, Segment.recording_id == Recording.id)
        .where(Segment.status == SegmentStatus.approved.value)
        .where(Segment.text.isnot(None))
        .where(Segment.audio_key.isnot(None))
        .where(Recording.speaker == speaker)
    )
    if not n_all:
        raise HTTPException(400, f"说话人“{speaker}”没有可训练的已通过切片")
    # 按条数训练: 音色名带 _N 后缀独立成音色; 留空/超过总数则用全部
    subset = count is not None and count < n_all
    use_n = count if subset else n_all
    limit = use_n if subset else None
    # exp 名: 去空白与路径分隔(GPU 端用 exec 跑脚本, 但仍做防御)
    exp = re.sub(r"[/\\]", "", re.sub(r"\s+", "_", speaker.strip())) or "voice"
    if subset:
        exp = f"{exp}_{use_n}"

    local_id = uuid.uuid4().hex
    _TRAIN_JOBS[local_id] = {
        "status": "queued", "exp": exp, "segments": use_n,
        "message": "已受理, 打包中…", "gpu_job_id": None,
    }
    task = asyncio.create_task(
        _run_train_job(local_id, speaker, exp, limit, sovits_ep, gpt_ep, batch)
    )
    _TRAIN_TASKS.add(task)
    task.add_done_callback(_TRAIN_TASKS.discard)
    return {"job_id": local_id, "exp": exp, "segments": use_n}


@router.get("/train/{job_id}")
async def export_train_status(me: StaffOnly, job_id: str) -> dict:
    """查训练进度: 本地打包/上传阶段读内存任务表; 已转发 GPU 后代理到 GPU。"""
    if not settings.gpu_train_url:
        raise HTTPException(503, "训练功能未配置")

    local = _TRAIN_JOBS.get(job_id)
    if local is not None:
        gpu_id = local.get("gpu_job_id")
        # 还在本地打包/上传, 或已出错: 直接返回本地状态(不代理 GPU)
        if gpu_id is None or local.get("status") == "error":
            return {
                "job_id": job_id, "exp": local.get("exp"),
                "status": local.get("status"), "segments": local.get("segments"),
                "message": local.get("message"), "weights": None, "log_tail": "",
            }
        job_id = gpu_id  # 已提交 GPU: 用 GPU 的 job_id 继续代理查询

    base = settings.gpu_train_url.rsplit("/train", 1)[0]  # -> .../api
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.get(
                f"{base}/train/{job_id}",
                headers={"X-Train-Token": settings.train_token},
            ) as r:
                if r.status != 200:
                    raise HTTPException(r.status, f"GPU: {(await r.text())[:200]}")
                gpu_status = await r.json()
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"连接 GPU 训练后端失败: {e}")
    # 带上本地记录的 exp/segments(GPU 侧字段齐全时以 GPU 为准)
    if local is not None:
        gpu_status.setdefault("exp", local.get("exp"))
        if gpu_status.get("segments") is None:
            gpu_status["segments"] = local.get("segments")
    return gpu_status


@router.get("/voices")
async def list_voices(me: StaffOnly) -> dict:
    """列出 GPU 上训练好的音色权重(代理 GPU GET /api/voice-models)。"""
    if not settings.gpu_train_url or not settings.train_token:
        raise HTTPException(503, "训练功能未配置 (缺 gpu_train_url / train_token)")
    base = settings.gpu_train_url.rsplit("/train", 1)[0]  # -> .../api
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.get(
                f"{base}/voice-models",
                headers={"X-Train-Token": settings.train_token},
            ) as r:
                if r.status != 200:
                    raise HTTPException(502, f"GPU: {(await r.text())[:200]}")
                return await r.json()
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"连接 GPU 失败: {e}")


@router.delete("/voices/{exp}")
async def delete_voice(me: StaffOnly, exp: str) -> dict:
    """删除 GPU 上一个训练好的音色(权重+缓存+数据)(代理 GPU DELETE /api/voice/{exp})。"""
    if not settings.gpu_train_url or not settings.train_token:
        raise HTTPException(503, "训练功能未配置 (缺 gpu_train_url / train_token)")
    base = settings.gpu_train_url.rsplit("/train", 1)[0]
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
            async with s.delete(
                f"{base}/voice/{exp}",
                headers={"X-Train-Token": settings.train_token},
            ) as r:
                text = await r.text()
                if r.status != 200:
                    # 409(音色正被训练/使用)等原样透传
                    raise HTTPException(r.status if r.status in (400, 404, 409) else 502, f"GPU: {text[:200]}")
                try:
                    return json.loads(text)
                except Exception:
                    return {"status": "ok"}
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"连接 GPU 失败: {e}")


class VoiceTTSReq(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    sovits_weights: str = Field(min_length=1, max_length=300)
    gpt_weights: str = Field(min_length=1, max_length=300)
    language: str = Field(default="zh", max_length=8)
    # 零样本引擎使用同一条训练切片作参考，保证可直接比较 v2/v3。
    engine: Literal["sovits", "cosyvoice", "cosyvoice3"] = "sovits"


def _gpu_api_base() -> tuple[str, str]:
    """返回 (GPU /api 基址, GPU 站点根)。用于代理合成与拼接音频完整 URL。"""
    base = settings.gpu_train_url.rsplit("/train", 1)[0]  # -> https://host/api
    origin = base[:-4] if base.endswith("/api") else base
    return base, origin


@router.post("/tts")
async def export_tts(me: StaffOnly, req: VoiceTTSReq) -> dict:
    """用训练好的音色合成一段文本(代理 GPU generate_clone, 免建预设直接传权重)。"""
    if not settings.gpu_train_url or not settings.train_token:
        raise HTTPException(503, "训练功能未配置")
    base, _ = _gpu_api_base()
    form = aiohttp.FormData()
    form.add_field("text", req.text)
    form.add_field("language", req.language)
    form.add_field("engine", req.engine or "sovits")
    form.add_field("sovits_weights", req.sovits_weights)
    form.add_field("gpt_weights", req.gpt_weights)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(
                f"{base}/tts/generate_clone", data=form,
                headers={"X-Train-Token": settings.train_token},
            ) as r:
                text = await r.text()
                if r.status != 200:
                    raise HTTPException(502, f"GPU: {text[:200]}")
                return json.loads(text)
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"连接 GPU 失败: {e}")


@router.get("/tts/{task_id}")
async def export_tts_status(me: StaffOnly, task_id: str) -> dict:
    """查合成任务进度(代理 GPU /api/tts/task/{id}); audio_url 补成完整地址。"""
    if not settings.gpu_train_url or not settings.train_token:
        raise HTTPException(503, "训练功能未配置")
    base, origin = _gpu_api_base()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.get(
                f"{base}/tts/task/{task_id}",
                headers={"X-Train-Token": settings.train_token},
            ) as r:
                if r.status != 200:
                    raise HTTPException(502, f"GPU: {(await r.text())[:200]}")
                data = await r.json()
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"连接 GPU 失败: {e}")
    au = data.get("audio_url")
    if isinstance(au, str) and au.startswith("/"):
        data["audio_url"] = origin + au  # 拼成完整可播放地址(/api/static 公开免鉴权)
    return data


@router.get("/stats", response_model=ExportStats)
async def export_stats(me: StaffOnly, session: SessionDep) -> ExportStats:
    total_row = (
        await session.execute(
            select(func.count(Segment.id), func.coalesce(func.sum(Segment.duration_ms), 0))
        )
    ).one()
    total, total_duration = total_row

    by_status_rows = (
        await session.execute(
            select(Segment.status, func.count(Segment.id)).group_by(Segment.status)
        )
    ).all()
    by_status = {st: count for st, count in by_status_rows}

    by_cat_rows = (
        await session.execute(
            select(
                Recording.content_category,
                func.count(Segment.id),
                func.coalesce(func.sum(Segment.duration_ms), 0),
            )
            .join(Recording, Segment.recording_id == Recording.id)
            .group_by(Recording.content_category)
        )
    ).all()
    by_category = {
        cat: ExportCategoryStats(count=count, duration_ms=dur)
        for cat, count, dur in by_cat_rows
    }

    return ExportStats(
        total=total,
        total_duration_ms=total_duration,
        by_status=by_status,
        by_category=by_category,
    )
