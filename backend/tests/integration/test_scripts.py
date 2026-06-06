"""范文管理路由集成测试 (admin-only: 上传解析/列表/详情/改属性+定稿/整页存行/删除)。"""

import io

import pytest
from docx import Document
from httpx import AsyncClient

from app import storage

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_docx(paragraphs: list[str]) -> bytes:
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _files(paragraphs: list[str], name: str = "sample.docx"):
    return {"file": (name, _make_docx(paragraphs), _DOCX_MIME)}


@pytest.fixture
def mock_storage_rw(monkeypatch):
    """put_bytes/delete_object 不打真 R2; 记录调用便于断言。"""
    calls = {"put": [], "delete": []}

    async def _put(key, data, content_type=None):
        calls["put"].append(key)

    async def _delete(key):
        calls["delete"].append(key)

    monkeypatch.setattr(storage, "put_bytes", _put)
    monkeypatch.setattr(storage, "delete_object", _delete)
    return calls


async def _upload(client, token, paragraphs, **fields):
    data = {"title": "주일 설교", "language": "ko", "content_category": "sermon", **fields}
    return await client.post(
        "/scripts", headers=_auth(token), files=_files(paragraphs), data=data
    )


# ---- 上传解析 ----
async def test_upload_creates_script_and_lines(
    client: AsyncClient, admin, make_token, mock_storage_rw
):
    r = await _upload(client, make_token(admin), ["첫째 줄", "  ", "둘째 줄", "셋째 줄"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "draft"
    assert body["language"] == "ko"
    assert body["content_category"] == "sermon"
    assert body["created_by"] == str(admin.id)
    assert body["original_filename"] == "sample.docx"
    assert body["line_count"] == 3  # 空白行被跳过
    texts = [ln["text"] for ln in body["lines"]]
    assert texts == ["첫째 줄", "둘째 줄", "셋째 줄"]
    assert [ln["line_index"] for ln in body["lines"]] == [0, 1, 2]
    assert mock_storage_rw["put"]  # 原始 docx 上传了一次


async def test_upload_rejects_non_docx(client: AsyncClient, admin, make_token, mock_storage_rw):
    r = await client.post(
        "/scripts",
        headers=_auth(make_token(admin)),
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"title": "t", "language": "ko", "content_category": "sermon"},
    )
    assert r.status_code == 400


async def test_upload_rejects_empty_docx(client: AsyncClient, admin, make_token, mock_storage_rw):
    r = await _upload(client, make_token(admin), ["", "   ", "\t"])
    assert r.status_code == 400


async def test_upload_rejects_garbage_bytes(client: AsyncClient, admin, make_token, mock_storage_rw):
    r = await client.post(
        "/scripts",
        headers=_auth(make_token(admin)),
        files={"file": ("fake.docx", b"not a real docx", _DOCX_MIME)},
        data={"title": "t", "language": "ko", "content_category": "sermon"},
    )
    assert r.status_code == 400


async def test_upload_bad_enum_422(client: AsyncClient, admin, make_token, mock_storage_rw):
    r = await _upload(client, make_token(admin), ["줄"], language="jp")
    assert r.status_code == 422


# ---- 列表 / 详情 ----
async def test_list_and_line_counts(client: AsyncClient, admin, make_token, mock_storage_rw):
    await _upload(client, make_token(admin), ["a", "b"])
    await _upload(client, make_token(admin), ["c", "d", "e"], title="시편 낭독")
    r = await client.get("/scripts", headers=_auth(make_token(admin)))
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    # 按 created_at 倒序: 后传的在前
    assert items[0]["title"] == "시편 낭독"
    assert items[0]["line_count"] == 3
    assert items[1]["line_count"] == 2
    assert "lines" not in items[0]  # 列表不含逐行内容


async def test_list_filter_by_status(client: AsyncClient, admin, make_token, mock_storage_rw):
    r1 = await _upload(client, make_token(admin), ["a"])
    sid = r1.json()["id"]
    await client.patch(
        f"/scripts/{sid}", headers=_auth(make_token(admin)), json={"status": "finalized"}
    )
    await _upload(client, make_token(admin), ["b"])  # 仍是 draft
    r = await client.get("/scripts?status=finalized", headers=_auth(make_token(admin)))
    assert r.status_code == 200
    assert [s["id"] for s in r.json()] == [sid]


async def test_get_detail_and_404(client: AsyncClient, admin, make_token, mock_storage_rw):
    import uuid

    r1 = await _upload(client, make_token(admin), ["줄1", "줄2"])
    sid = r1.json()["id"]
    r = await client.get(f"/scripts/{sid}", headers=_auth(make_token(admin)))
    assert r.status_code == 200 and len(r.json()["lines"]) == 2
    r = await client.get(f"/scripts/{uuid.uuid4()}", headers=_auth(make_token(admin)))
    assert r.status_code == 404


# ---- 改属性 + 定稿 ----
async def test_patch_title_and_finalize(client: AsyncClient, admin, make_token, mock_storage_rw):
    sid = (await _upload(client, make_token(admin), ["줄"])).json()["id"]
    r = await client.patch(
        f"/scripts/{sid}",
        headers=_auth(make_token(admin)),
        json={"title": "새 제목", "status": "finalized"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "새 제목" and r.json()["status"] == "finalized"


async def test_patch_empty_422(client: AsyncClient, admin, make_token, mock_storage_rw):
    sid = (await _upload(client, make_token(admin), ["줄"])).json()["id"]
    r = await client.patch(f"/scripts/{sid}", headers=_auth(make_token(admin)), json={})
    assert r.status_code == 422


# ---- 整页存行 (replace-all) ----
async def test_save_lines_replace_all(client: AsyncClient, admin, make_token, mock_storage_rw):
    sid = (await _upload(client, make_token(admin), ["원래1", "원래2", "원래3"])).json()["id"]
    r = await client.put(
        f"/scripts/{sid}/lines",
        headers=_auth(make_token(admin)),
        json={"lines": [{"text": "수정된 줄"}, {"text": "  공백 트림  "}, {"text": "추가된 줄"}]},
    )
    assert r.status_code == 200
    lines = r.json()["lines"]
    assert [ln["text"] for ln in lines] == ["수정된 줄", "공백 트림", "추가된 줄"]
    assert [ln["line_index"] for ln in lines] == [0, 1, 2]


async def test_save_lines_empty_clears(client: AsyncClient, admin, make_token, mock_storage_rw):
    sid = (await _upload(client, make_token(admin), ["a", "b"])).json()["id"]
    r = await client.put(
        f"/scripts/{sid}/lines", headers=_auth(make_token(admin)), json={"lines": []}
    )
    assert r.status_code == 200 and r.json()["line_count"] == 0


# ---- 删除 ----
async def test_delete_script(client: AsyncClient, admin, make_token, mock_storage_rw):
    sid = (await _upload(client, make_token(admin), ["줄"])).json()["id"]
    r = await client.delete(f"/scripts/{sid}", headers=_auth(make_token(admin)))
    assert r.status_code == 204
    assert (await client.get(f"/scripts/{sid}", headers=_auth(make_token(admin)))).status_code == 404
    assert mock_storage_rw["delete"]  # 原始 docx 被删


# ---- 权限 ----
async def test_contributor_forbidden(client: AsyncClient, contributor, make_token, mock_storage_rw):
    assert (await client.get("/scripts", headers=_auth(make_token(contributor)))).status_code == 403
    r = await _upload(client, make_token(contributor), ["줄"])
    assert r.status_code == 403


async def test_no_auth_401(client: AsyncClient):
    assert (await client.get("/scripts")).status_code == 401
