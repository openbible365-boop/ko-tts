"""approved segments 导出 (训练侧消费): JSONL manifest + stats。

仅 reviewer/admin 可访问。manifest 是 StreamingResponse, 单次共享一个 s3
client 上下文批量签 URL, 避免 N 段 = N 次 client 开关销。
"""

import io
import json
import re
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app import storage
from app.config import settings
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
) -> tuple[bytes, int]:
    """组 GPT-SoVITS 数据集 zip(wavs/ + train.list + README), 返回 (字节, 段数)。

    供 /dataset.zip 下载与 /train 转发共用。语言码默认取每条录音的 language。
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

    buf = io.BytesIO()
    lines: list[str] = []
    async with storage._client() as s3:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for seg, spk, rec_lang in rows:
                resp = await s3.get_object(Bucket=settings.r2_bucket, Key=seg.audio_key)
                async with resp["Body"] as body:
                    data = await body.read()
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


@router.post("/train")
async def export_train(
    me: StaffOnly,
    session: SessionDep,
    speaker: Annotated[str, Query(min_length=1, max_length=128, description="按说话人训练")],
    sovits_ep: Annotated[int, Query(ge=1, le=50)] = 8,
    gpt_ep: Annotated[int, Query(ge=1, le=50)] = 15,
    batch: Annotated[int, Query(ge=1, le=16)] = 4,
) -> dict:
    """一键微调: 把该说话人的 approved 切片打包发到 GPU 训练后端, 返回 GPU 任务 id。"""
    if not settings.gpu_train_url or not settings.train_token:
        raise HTTPException(503, "训练功能未配置 (缺 gpu_train_url / train_token)")
    payload, n = await _build_dataset_zip(session, speaker=speaker)
    if n == 0:
        raise HTTPException(400, f"说话人“{speaker}”没有可训练的已通过切片")
    # exp 名: 去空白与路径分隔(GPU 端用 exec 跑脚本, 但仍做防御)
    exp = re.sub(r"[/\\]", "", re.sub(r"\s+", "_", speaker.strip())) or "voice"

    form = aiohttp.FormData()
    form.add_field("exp", exp)
    form.add_field("sovits_ep", str(sovits_ep))
    form.add_field("gpt_ep", str(gpt_ep))
    form.add_field("batch", str(batch))
    form.add_field("dataset", payload, filename="dataset.zip", content_type="application/zip")
    timeout = aiohttp.ClientTimeout(total=180)
    try:
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
                    # 409(已有训练在跑)原样透传给前端; 其它算上游网关错误
                    raise HTTPException(409 if r.status == 409 else 502, detail)
                job = json.loads(text)
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"连接 GPU 训练后端失败: {e}")
    return {"job_id": job.get("job_id"), "exp": exp, "segments": n}


@router.get("/train/{job_id}")
async def export_train_status(me: StaffOnly, job_id: str) -> dict:
    """查 GPU 训练任务进度(转发到 GPU /api/train/{job_id})。"""
    if not settings.gpu_train_url:
        raise HTTPException(503, "训练功能未配置")
    base = settings.gpu_train_url.rsplit("/train", 1)[0]  # -> .../api
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.get(
                f"{base}/train/{job_id}",
                headers={"X-Train-Token": settings.train_token},
            ) as r:
                if r.status != 200:
                    raise HTTPException(r.status, f"GPU: {(await r.text())[:200]}")
                return await r.json()
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"连接 GPU 训练后端失败: {e}")


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
