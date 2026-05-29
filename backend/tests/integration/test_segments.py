"""segments router 集成测试 (校对/审核状态机 + 权限)。"""

import uuid

from httpx import AsyncClient

from app.models import SegmentStatus


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---- 读 ----
async def test_list_segments_contributor_own_only(
    client: AsyncClient, contributor, reviewer, make_token,
    make_recording_factory, make_segment_factory,
):
    own_rec = await make_recording_factory(contributor)
    own_seg = await make_segment_factory(own_rec)
    other_rec = await make_recording_factory(reviewer)
    await make_segment_factory(other_rec)
    r = await client.get("/segments", headers=_auth(make_token(contributor)))
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()}
    assert ids == {str(own_seg.id)}


async def test_list_segments_reviewer_sees_all(
    client: AsyncClient, contributor, reviewer, make_token,
    make_recording_factory, make_segment_factory,
):
    rec_a = await make_recording_factory(contributor)
    a = await make_segment_factory(rec_a)
    rec_b = await make_recording_factory(reviewer)
    b = await make_segment_factory(rec_b)
    r = await client.get("/segments", headers=_auth(make_token(reviewer)))
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()}
    assert {str(a.id), str(b.id)} <= ids


async def test_list_segments_filter_by_status(
    client: AsyncClient, contributor, reviewer, make_token,
    make_recording_factory, make_segment_factory,
):
    rec = await make_recording_factory(contributor)
    await make_segment_factory(rec, index=0, status=SegmentStatus.approved)
    await make_segment_factory(rec, index=1, status=SegmentStatus.rejected)
    r = await client.get(
        "/segments?status=approved", headers=_auth(make_token(reviewer))
    )
    assert r.status_code == 200
    assert {x["status"] for x in r.json()} == {"approved"}


async def test_get_segment_other_contributor_forbidden(
    client: AsyncClient, contributor, make_token, admin, db_session,
    make_recording_factory, make_segment_factory,
):
    """另一个 contributor 看不到不是自己上传的 segment。"""
    other_rec = await make_recording_factory(admin)  # admin 作为 uploader
    other_seg = await make_segment_factory(other_rec)
    r = await client.get(
        f"/segments/{other_seg.id}", headers=_auth(make_token(contributor))
    )
    assert r.status_code == 403


async def test_download_url_segment(
    client: AsyncClient, contributor, make_token,
    make_recording_factory, make_segment_factory,
):
    rec = await make_recording_factory(contributor)
    seg = await make_segment_factory(rec)
    r = await client.get(
        f"/segments/{seg.id}/download-url", headers=_auth(make_token(contributor))
    )
    assert r.status_code == 200
    assert r.json()["url"].startswith("https://test.r2.example.com/")


# ---- 校对 ----
async def test_correct_happy(
    client: AsyncClient, reviewer, contributor, make_token,
    make_recording_factory, make_segment_factory,
):
    rec = await make_recording_factory(contributor)
    seg = await make_segment_factory(
        rec, status=SegmentStatus.pending_correction, asr_text="raw"
    )
    r = await client.post(
        f"/segments/{seg.id}/correct",
        headers=_auth(make_token(reviewer)),
        json={"text": "corrected text"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending_review"
    assert body["text"] == "corrected text"
    assert body["corrected_by"] == str(reviewer.id)
    assert body["corrected_at"] is not None


async def test_correct_contributor_forbidden(
    client: AsyncClient, contributor, make_token,
    make_recording_factory, make_segment_factory,
):
    rec = await make_recording_factory(contributor)
    seg = await make_segment_factory(rec, status=SegmentStatus.pending_correction)
    r = await client.post(
        f"/segments/{seg.id}/correct",
        headers=_auth(make_token(contributor)),
        json={"text": "x"},
    )
    assert r.status_code == 403


async def test_correct_409_if_approved(
    client: AsyncClient, reviewer, contributor, make_token,
    make_recording_factory, make_segment_factory,
):
    rec = await make_recording_factory(contributor)
    seg = await make_segment_factory(rec, status=SegmentStatus.approved, text="ok")
    r = await client.post(
        f"/segments/{seg.id}/correct",
        headers=_auth(make_token(reviewer)),
        json={"text": "no"},
    )
    assert r.status_code == 409


async def test_correct_from_rejected_ok(
    client: AsyncClient, reviewer, contributor, make_token,
    make_recording_factory, make_segment_factory,
):
    rec = await make_recording_factory(contributor)
    seg = await make_segment_factory(rec, status=SegmentStatus.rejected, text="bad")
    r = await client.post(
        f"/segments/{seg.id}/correct",
        headers=_auth(make_token(reviewer)),
        json={"text": "fixed"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "pending_review"


# ---- 审核 ----
async def test_approve_happy(
    client: AsyncClient, reviewer, contributor, make_token,
    make_recording_factory, make_segment_factory,
):
    rec = await make_recording_factory(contributor)
    seg = await make_segment_factory(rec, status=SegmentStatus.pending_review, text="ok")
    r = await client.post(
        f"/segments/{seg.id}/approve", headers=_auth(make_token(reviewer))
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"
    assert body["reviewed_by"] == str(reviewer.id)
    assert body["reviewed_at"] is not None
    assert body["rejection_reason"] is None


async def test_approve_clears_rejection_reason(
    client: AsyncClient, reviewer, contributor, make_token,
    make_recording_factory, make_segment_factory, db_session,
):
    """先 reject 再 correct 再 approve, rejection_reason 应被清空。"""
    rec = await make_recording_factory(contributor)
    seg = await make_segment_factory(rec, status=SegmentStatus.pending_review, text="ok")
    token = make_token(reviewer)

    r = await client.post(
        f"/segments/{seg.id}/reject",
        headers=_auth(token),
        json={"rejection_reason": "not good"},
    )
    assert r.status_code == 200 and r.json()["rejection_reason"] == "not good"

    r = await client.post(
        f"/segments/{seg.id}/correct",
        headers=_auth(token),
        json={"text": "fixed"},
    )
    assert r.status_code == 200 and r.json()["status"] == "pending_review"

    r = await client.post(f"/segments/{seg.id}/approve", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["rejection_reason"] is None
    assert r.json()["status"] == "approved"


async def test_approve_409_if_pending_correction(
    client: AsyncClient, reviewer, contributor, make_token,
    make_recording_factory, make_segment_factory,
):
    rec = await make_recording_factory(contributor)
    seg = await make_segment_factory(rec, status=SegmentStatus.pending_correction)
    r = await client.post(
        f"/segments/{seg.id}/approve", headers=_auth(make_token(reviewer))
    )
    assert r.status_code == 409


async def test_reject_happy(
    client: AsyncClient, reviewer, contributor, make_token,
    make_recording_factory, make_segment_factory,
):
    rec = await make_recording_factory(contributor)
    seg = await make_segment_factory(rec, status=SegmentStatus.pending_review, text="ok")
    r = await client.post(
        f"/segments/{seg.id}/reject",
        headers=_auth(make_token(reviewer)),
        json={"rejection_reason": "punctuation off"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "rejected"
    assert body["rejection_reason"] == "punctuation off"


async def test_reject_empty_reason_422(
    client: AsyncClient, reviewer, contributor, make_token,
    make_recording_factory, make_segment_factory,
):
    rec = await make_recording_factory(contributor)
    seg = await make_segment_factory(rec, status=SegmentStatus.pending_review, text="ok")
    r = await client.post(
        f"/segments/{seg.id}/reject",
        headers=_auth(make_token(reviewer)),
        json={"rejection_reason": ""},
    )
    assert r.status_code == 422


async def test_reject_contributor_forbidden(
    client: AsyncClient, contributor, make_token,
    make_recording_factory, make_segment_factory,
):
    rec = await make_recording_factory(contributor)
    seg = await make_segment_factory(rec, status=SegmentStatus.pending_review, text="ok")
    r = await client.post(
        f"/segments/{seg.id}/reject",
        headers=_auth(make_token(contributor)),
        json={"rejection_reason": "x"},
    )
    assert r.status_code == 403


async def test_random_segment_404(
    client: AsyncClient, reviewer, make_token
):
    r = await client.post(
        f"/segments/{uuid.uuid4()}/correct",
        headers=_auth(make_token(reviewer)),
        json={"text": "x"},
    )
    assert r.status_code == 404
