"""范文管理 (admin-only)。

上传 Word(.docx) 范文 -> 按段落拆成待录脚本行 -> 管理员编辑 -> 定稿(供采集)。
原始 .docx 留一份到 R2 备查; 正文逐行落 script_lines。整页保存用 replace-all。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app import storage
from app.deps import CurrentUser, SessionDep, require_role
from app.docx_parse import DocxParseError, parse_docx_lines
from app.models import ContentCategory, Language, Script, ScriptLine, ScriptStatus, User, UserRole
from app.schemas import ScriptDetail, ScriptLinesSave, ScriptRead, ScriptUpdate

router = APIRouter(prefix="/scripts", tags=["scripts"])

AdminOnly = Annotated[User, Depends(require_role(UserRole.admin))]

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_MAX_DOCX_BYTES = 10 * 1024 * 1024  # 10 MB, 范文 docx 不该比这大


# ---- helpers ----
async def _get_with_lines(session: SessionDep, script_id: uuid.UUID) -> Script | None:
    stmt = select(Script).options(selectinload(Script.lines)).where(Script.id == script_id)
    return await session.scalar(stmt)


def _to_detail(script: Script) -> ScriptDetail:
    d = ScriptDetail.model_validate(script)  # lines 必须已 eager 加载
    d.line_count = len(script.lines)
    return d


async def _detail_or_404(session: SessionDep, script_id: uuid.UUID) -> ScriptDetail:
    script = await _get_with_lines(session, script_id)
    if script is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found")
    return _to_detail(script)


# ---- routes ----
@router.post("", response_model=ScriptDetail, status_code=status.HTTP_201_CREATED)
async def upload_script(
    me: AdminOnly,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1, max_length=512)],
    language: Annotated[Language, Form()],
    content_category: Annotated[ContentCategory, Form()],
    notes: Annotated[str | None, Form()] = None,
) -> ScriptDetail:
    filename = file.filename or ""
    if not filename.lower().endswith(".docx"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只支持 .docx 格式的 Word 文件")

    data = await file.read()
    if len(data) > _MAX_DOCX_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件过大 (上限 10MB)")

    try:
        lines = parse_docx_lines(data)
    except DocxParseError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if not lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文档中没有可用的文本行")

    script = Script(
        created_by=me.id,
        title=title,
        language=language.value,
        content_category=content_category.value,
        notes=notes,
        status=ScriptStatus.draft.value,
        original_filename=filename,
    )
    session.add(script)
    await session.flush()  # 拿到 script.id

    docx_key = storage.script_docx_key(script.id)
    await storage.put_bytes(docx_key, data, content_type=_DOCX_MIME)
    script.source_docx_key = docx_key

    for i, text in enumerate(lines):
        session.add(ScriptLine(script_id=script.id, line_index=i, text=text))

    await session.commit()
    return await _detail_or_404(session, script.id)


@router.get("", response_model=list[ScriptRead])
async def list_scripts(
    me: AdminOnly,
    session: SessionDep,
    status_: Annotated[ScriptStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ScriptRead]:
    stmt = select(Script).order_by(Script.created_at.desc()).limit(limit).offset(offset)
    if status_ is not None:
        stmt = stmt.where(Script.status == status_.value)
    scripts = list(await session.scalars(stmt))

    count_map: dict[uuid.UUID, int] = {}
    ids = [s.id for s in scripts]
    if ids:
        rows = await session.execute(
            select(ScriptLine.script_id, func.count())
            .where(ScriptLine.script_id.in_(ids))
            .group_by(ScriptLine.script_id)
        )
        count_map = {sid: cnt for sid, cnt in rows}

    out: list[ScriptRead] = []
    for s in scripts:
        r = ScriptRead.model_validate(s)
        r.line_count = count_map.get(s.id, 0)
        out.append(r)
    return out


# 注意: 必须在 /{script_id} 之前声明, 否则 "recordable" 会被当作 script_id。
@router.get("/recordable", response_model=list[ScriptRead])
async def list_recordable_scripts(me: CurrentUser, session: SessionDep) -> list[ScriptRead]:
    """所有登录用户: 列已定稿范文(供录音页面选择)。"""
    stmt = (
        select(Script)
        .where(Script.status == ScriptStatus.finalized.value)
        .order_by(Script.created_at.desc())
    )
    scripts = list(await session.scalars(stmt))

    count_map: dict[uuid.UUID, int] = {}
    ids = [s.id for s in scripts]
    if ids:
        rows = await session.execute(
            select(ScriptLine.script_id, func.count())
            .where(ScriptLine.script_id.in_(ids))
            .group_by(ScriptLine.script_id)
        )
        count_map = {sid: cnt for sid, cnt in rows}

    out: list[ScriptRead] = []
    for s in scripts:
        r = ScriptRead.model_validate(s)
        r.line_count = count_map.get(s.id, 0)
        out.append(r)
    return out


@router.get("/{script_id}", response_model=ScriptDetail)
async def get_script(
    script_id: uuid.UUID, me: AdminOnly, session: SessionDep
) -> ScriptDetail:
    return await _detail_or_404(session, script_id)


@router.patch("/{script_id}", response_model=ScriptDetail)
async def update_script(
    script_id: uuid.UUID,
    data: ScriptUpdate,
    me: AdminOnly,
    session: SessionDep,
) -> ScriptDetail:
    script = await session.get(Script, script_id)
    if script is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found")

    if data.title is not None:
        script.title = data.title
    if data.language is not None:
        script.language = data.language.value
    if data.content_category is not None:
        script.content_category = data.content_category.value
    if data.notes is not None:
        script.notes = data.notes
    if data.status is not None:
        script.status = data.status.value

    await session.commit()
    return await _detail_or_404(session, script_id)


@router.put("/{script_id}/lines", response_model=ScriptDetail)
async def save_lines(
    script_id: uuid.UUID,
    data: ScriptLinesSave,
    me: AdminOnly,
    session: SessionDep,
) -> ScriptDetail:
    script = await session.get(Script, script_id)
    if script is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found")

    # replace-all: 删旧行 -> 按数组顺序重排 line_index 插入 (空白行 trim 后跳过)
    await session.execute(delete(ScriptLine).where(ScriptLine.script_id == script_id))
    await session.flush()
    idx = 0
    for item in data.lines:
        text = item.text.strip()
        if not text:
            continue
        session.add(ScriptLine(script_id=script_id, line_index=idx, text=text))
        idx += 1

    await session.commit()
    return await _detail_or_404(session, script_id)


@router.delete("/{script_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_script(
    script_id: uuid.UUID, me: AdminOnly, session: SessionDep
) -> None:
    # eager-load lines: delete-orphan 级联在 async 下不能隐式懒加载集合
    script = await _get_with_lines(session, script_id)
    if script is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found")

    docx_key = script.source_docx_key
    await session.delete(script)  # script_lines 随 ORM 级联 + FK CASCADE 删
    await session.commit()

    # R2 原始文件 best-effort 删, 失败不影响主流程(留个孤儿对象无害)
    if docx_key:
        try:
            await storage.delete_object(docx_key)
        except Exception:  # noqa: BLE001
            pass
