"""approved segments 导出 (训练侧消费): JSONL manifest + stats。

仅 reviewer/admin 可访问。manifest 是 StreamingResponse, 单次共享一个 s3
client 上下文批量签 URL, 避免 N 段 = N 次 client 开关销。
"""

import io
import json
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
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
        select(Segment, Recording.content_category, Recording.speaker)
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
            for seg, category, spk in rows:
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


@router.get("/dataset.zip")
async def export_dataset_zip(
    me: StaffOnly,
    session: SessionDep,
    status: Annotated[list[SegmentStatus] | None, Query()] = None,
    content_category: Annotated[ContentCategory | None, Query()] = None,
    speaker: Annotated[str | None, Query(max_length=128)] = None,
    language: Annotated[str, Query(max_length=8, description="GPT-SoVITS 语言码")] = "ko",
) -> StreamingResponse:
    """把(默认 approved)切片打包成 GPT-SoVITS 数据集 zip: wavs/ + train.list。

    一次性在内存里组 zip(数据集通常几十~几百段、几十 MB, 够用); 真到上万段
    再改流式。
    """
    statuses = [s.value for s in (status or [SegmentStatus.approved])]
    stmt = (
        select(Segment, Recording.speaker)
        .join(Recording, Segment.recording_id == Recording.id)
        .where(Segment.status.in_(statuses))
        .where(Segment.text.isnot(None))
        .where(Segment.audio_key.isnot(None))
        .order_by(Segment.created_at)
    )
    if content_category is not None:
        stmt = stmt.where(Recording.content_category == content_category.value)
    if speaker is not None:
        stmt = stmt.where(Recording.speaker == speaker)
    rows = (await session.execute(stmt)).all()

    buf = io.BytesIO()
    lines: list[str] = []
    async with storage._client() as s3:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for seg, spk in rows:
                resp = await s3.get_object(Bucket=settings.r2_bucket, Key=seg.audio_key)
                async with resp["Body"] as body:
                    data = await body.read()
                arc = f"wavs/{seg.id}.wav"
                zf.writestr(arc, data)
                spk_name = spk or speaker or "speaker"
                lines.append(f"{arc}|{spk_name}|{language}|{seg.text.strip()}")
            zf.writestr("train.list", "\n".join(lines) + ("\n" if lines else ""))
            zf.writestr("README.txt", _README)

    payload = buf.getvalue()
    fname = f"ko-tts-dataset-{datetime.now(UTC).date()}.zip"
    return StreamingResponse(
        iter([payload]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Export-Segments": str(len(lines)),
        },
    )


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
