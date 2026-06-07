"""录音样品集成测试: from-script 建/续录、逐行 record-url/complete/pass/rerecord、样品状态。"""

import pytest
from httpx import AsyncClient

from app import storage
from app.models import Script, ScriptLine, ScriptStatus


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _make_script(db_session, owner, lines, *, status=ScriptStatus.finalized):
    script = Script(
        created_by=owner.id,
        title="창세기 1장",
        language="ko",
        content_category="sermon",
        status=status.value,
    )
    db_session.add(script)
    await db_session.flush()
    for i, t in enumerate(lines):
        db_session.add(ScriptLine(script_id=script.id, line_index=i, text=t))
    await db_session.commit()
    await db_session.refresh(script)
    return script


@pytest.fixture
def mock_storage(monkeypatch):
    calls = {"put_url": [], "delete": []}

    async def _put_url(key, *, expires_in=3600, content_type=None):
        calls["put_url"].append(key)
        return f"https://r2.example/{key}?sig=x"

    async def _head(key):
        return {"ContentLength": 2048, "ContentType": "audio/webm"}

    async def _delete(key):
        calls["delete"].append(key)

    monkeypatch.setattr(storage, "presigned_put_url", _put_url)
    monkeypatch.setattr(storage, "head_object", _head)
    monkeypatch.setattr(storage, "delete_object", _delete)
    return calls


async def _start(client, token, script_id):
    return await client.post(f"/recordings/from-script/{script_id}", headers=_auth(token))


async def _seg_ids(client, token, rec_id):
    r = await client.get(f"/recordings/{rec_id}/segments", headers=_auth(token))
    return r.json()


# ---- from-script ----
async def test_from_script_creates_sample_and_segments(
    client: AsyncClient, contributor, make_token, db_session
):
    script = await _make_script(db_session, contributor, ["시초에 하나님이", "하나님이 보셨다"])
    r = await _start(client, make_token(contributor), script.id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["script_id"] == str(script.id)
    assert body["status"] == "recording"
    assert body["audio_key"] == ""

    segs = await _seg_ids(client, make_token(contributor), body["id"])
    assert [s["text"] for s in segs] == ["시초에 하나님이", "하나님이 보셨다"]
    assert all(s["status"] == "pending_recording" for s in segs)
    assert [s["segment_index"] for s in segs] == [0, 1]


async def test_from_script_resume_returns_same(
    client: AsyncClient, contributor, make_token, db_session
):
    script = await _make_script(db_session, contributor, ["a"])
    r1 = await _start(client, make_token(contributor), script.id)
    r2 = await _start(client, make_token(contributor), script.id)
    assert r1.json()["id"] == r2.json()["id"]


async def test_from_script_rejects_non_finalized(
    client: AsyncClient, contributor, make_token, db_session
):
    script = await _make_script(db_session, contributor, ["a"], status=ScriptStatus.draft)
    r = await _start(client, make_token(contributor), script.id)
    assert r.status_code == 400


async def test_each_user_gets_own_sample(
    client: AsyncClient, contributor, reviewer, make_token, db_session
):
    script = await _make_script(db_session, contributor, ["a"])
    r1 = await _start(client, make_token(contributor), script.id)
    r2 = await _start(client, make_token(reviewer), script.id)
    assert r1.json()["id"] != r2.json()["id"]


# ---- record-url / complete ----
async def test_record_url_and_complete(
    client: AsyncClient, contributor, make_token, db_session, mock_storage
):
    script = await _make_script(db_session, contributor, ["줄1", "줄2"])
    rec = (await _start(client, make_token(contributor), script.id)).json()
    seg0 = (await _seg_ids(client, make_token(contributor), rec["id"]))[0]["id"]

    r = await client.post(f"/segments/{seg0}/record-url", headers=_auth(make_token(contributor)))
    assert r.status_code == 200 and r.json()["url"]
    assert mock_storage["put_url"]

    r = await client.post(
        f"/segments/{seg0}/record-complete",
        headers=_auth(make_token(contributor)),
        json={"duration_ms": 3200},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "pending_transcription"
    assert r.json()["duration_ms"] == 3200

    # 还有一行没录 -> 样品仍 recording
    rec2 = await client.get(f"/recordings/{rec['id']}", headers=_auth(make_token(contributor)))
    assert rec2.json()["status"] == "recording"


async def test_sample_recorded_when_all_lines_recorded(
    client: AsyncClient, contributor, make_token, db_session, mock_storage
):
    script = await _make_script(db_session, contributor, ["唯一一行"])
    rec = (await _start(client, make_token(contributor), script.id)).json()
    seg0 = (await _seg_ids(client, make_token(contributor), rec["id"]))[0]["id"]
    await client.post(f"/segments/{seg0}/record-url", headers=_auth(make_token(contributor)))
    await client.post(
        f"/segments/{seg0}/record-complete",
        headers=_auth(make_token(contributor)),
        json={},
    )
    rec2 = await client.get(f"/recordings/{rec['id']}", headers=_auth(make_token(contributor)))
    assert rec2.json()["status"] == "recorded"


# ---- pass / rerecord ----
async def test_pass_from_pending_review(
    client: AsyncClient, contributor, make_token, db_session, mock_storage
):
    from app.models import Segment, SegmentStatus

    script = await _make_script(db_session, contributor, ["줄"])
    rec = (await _start(client, make_token(contributor), script.id)).json()
    seg0 = (await _seg_ids(client, make_token(contributor), rec["id"]))[0]["id"]
    # 模拟 worker: 置为标红待判
    seg = await db_session.get(Segment, __import__("uuid").UUID(seg0))
    seg.status = SegmentStatus.pending_review.value
    seg.asr_text = "识别错的"
    await db_session.commit()

    r = await client.post(f"/segments/{seg0}/pass", headers=_auth(make_token(contributor)))
    assert r.status_code == 200 and r.json()["status"] == "approved"


async def test_rerecord_resets_line_and_sample(
    client: AsyncClient, contributor, make_token, db_session, mock_storage
):
    script = await _make_script(db_session, contributor, ["줄"])
    rec = (await _start(client, make_token(contributor), script.id)).json()
    seg0 = (await _seg_ids(client, make_token(contributor), rec["id"]))[0]["id"]
    await client.post(f"/segments/{seg0}/record-url", headers=_auth(make_token(contributor)))
    await client.post(
        f"/segments/{seg0}/record-complete", headers=_auth(make_token(contributor)), json={}
    )
    # 此刻样品 recorded(唯一行已录)
    r = await client.post(f"/segments/{seg0}/rerecord", headers=_auth(make_token(contributor)))
    assert r.status_code == 200
    assert r.json()["status"] == "pending_recording"
    assert r.json()["audio_key"] is None
    assert mock_storage["delete"]  # 旧 clip 被删
    rec2 = await client.get(f"/recordings/{rec['id']}", headers=_auth(make_token(contributor)))
    assert rec2.json()["status"] == "recording"


# ---- 声音昵称 ----
async def test_set_and_edit_speaker(
    client: AsyncClient, contributor, reviewer, make_token, db_session
):
    script = await _make_script(db_session, contributor, ["줄"])
    rec = (await _start(client, make_token(contributor), script.id)).json()
    assert rec["speaker"] is None  # 新样品无声音昵称

    r = await client.post(
        f"/recordings/{rec['id']}/speaker",
        headers=_auth(make_token(contributor)),
        json={"speaker": "  평양남성1  "},
    )
    assert r.status_code == 200 and r.json()["speaker"] == "평양남성1"  # 已 trim

    # 空 -> 422
    r2 = await client.post(
        f"/recordings/{rec['id']}/speaker",
        headers=_auth(make_token(contributor)),
        json={"speaker": ""},
    )
    assert r2.status_code == 422

    # 非 owner 非 admin -> 403
    r3 = await client.post(
        f"/recordings/{rec['id']}/speaker",
        headers=_auth(make_token(reviewer)),
        json={"speaker": "x"},
    )
    assert r3.status_code == 403


# ---- 访问控制 ----
async def test_record_ops_forbidden_for_non_owner(
    client: AsyncClient, contributor, reviewer, make_token, db_session, mock_storage
):
    script = await _make_script(db_session, contributor, ["줄"])
    rec = (await _start(client, make_token(contributor), script.id)).json()
    seg0 = (await _seg_ids(client, make_token(contributor), rec["id"]))[0]["id"]
    # reviewer 非 owner 非 admin -> 403
    r = await client.post(f"/segments/{seg0}/record-url", headers=_auth(make_token(reviewer)))
    assert r.status_code == 403


async def test_record_url_on_non_sample_segment_400(
    client: AsyncClient,
    contributor,
    make_token,
    make_recording_factory,
    make_segment_factory,
    mock_storage,
):
    rec = await make_recording_factory(contributor)  # 普通上传录音(script_id=None)
    seg = await make_segment_factory(rec)
    r = await client.post(f"/segments/{seg.id}/record-url", headers=_auth(make_token(contributor)))
    assert r.status_code == 400


# ---- recordable ----
async def test_recordable_lists_finalized_only(
    client: AsyncClient, contributor, admin, make_token, db_session
):
    fin = await _make_script(db_session, admin, ["a"], status=ScriptStatus.finalized)
    await _make_script(db_session, admin, ["b"], status=ScriptStatus.draft)
    r = await client.get("/scripts/recordable", headers=_auth(make_token(contributor)))
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()]
    assert ids == [str(fin.id)]


async def test_recordable_excludes_already_started(
    client: AsyncClient, contributor, reviewer, admin, make_token, db_session
):
    s1 = await _make_script(db_session, admin, ["a"])
    s2 = await _make_script(db_session, admin, ["b"])
    # contributor 开始录 s1 -> s1 不再出现在其可选列表
    await _start(client, make_token(contributor), s1.id)
    r = await client.get("/scripts/recordable", headers=_auth(make_token(contributor)))
    ids = [s["id"] for s in r.json()]
    assert str(s1.id) not in ids
    assert str(s2.id) in ids
    # 但对没开始的 reviewer, s1 仍可选(每人独立)
    r2 = await client.get("/scripts/recordable", headers=_auth(make_token(reviewer)))
    assert str(s1.id) in [s["id"] for s in r2.json()]
