"""approved segments 导出 (训练侧消费): JSONL manifest + stats。

仅 reviewer/admin 可访问。manifest 是 StreamingResponse, 单次共享一个 s3
client 上下文批量签 URL, 避免 N 段 = N 次 client 开关销。
"""

import json
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
