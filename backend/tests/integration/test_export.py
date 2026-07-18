"""导出 manifest + stats 集成测试。"""

import json
import uuid

from httpx import AsyncClient

from app.routers import export as export_router
from app.models import (
    Recording,
    RecordingStatus,
    Segment,
    SegmentStatus,
)


async def _make_seg(
    db_session,
    user,
    *,
    text,
    status,
    category="sermon",
    duration_ms=2000,
    speaker=None,
):
    """造一条 (recording, segment) 对供导出消费。"""
    rec = Recording(
        uploaded_by=user.id,
        audio_key=f"recordings/{uuid.uuid4()}/original.wav",
        content_category=category,
        speaker=speaker,
        status=RecordingStatus.segmented.value,
    )
    db_session.add(rec)
    await db_session.flush()
    seg = Segment(
        recording_id=rec.id,
        segment_index=0,
        start_ms=0,
        end_ms=duration_ms,
        duration_ms=duration_ms,
        audio_key=f"segments/{uuid.uuid4()}.wav",
        text=text,
        status=status.value,
    )
    db_session.add(seg)
    await db_session.commit()
    await db_session.refresh(seg)
    return seg


async def test_manifest_only_returns_approved(
    client: AsyncClient, contributor, reviewer, make_token, db_session
):
    approved = await _make_seg(
        db_session, contributor, text="안녕하세요", status=SegmentStatus.approved
    )
    await _make_seg(
        db_session,
        contributor,
        text="should not appear",
        status=SegmentStatus.rejected,
        category="hymn",
    )

    r = await client.get(
        "/export/manifest.jsonl",
        headers={"Authorization": f"Bearer {make_token(reviewer)}"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")

    lines = [json.loads(row) for row in r.text.strip().splitlines()]
    assert len(lines) == 1, lines
    line = lines[0]
    assert line["id"] == str(approved.id)
    assert line["text"] == "안녕하세요"
    assert line["status"] == "approved"
    assert line["content_category"] == "sermon"
    assert line["duration_ms"] == 2000
    assert line["sample_rate"] == 24000  # = settings.seg_sample_rate
    # 预签名 URL 应指向 test R2 endpoint, 含签名参数
    assert line["audio_url"].startswith("https://test.r2.example.com/")
    assert "X-Amz-Signature" in line["audio_url"]


async def test_manifest_filter_by_status_and_category(
    client: AsyncClient, contributor, reviewer, make_token, db_session
):
    s1 = await _make_seg(
        db_session, contributor, text="A", status=SegmentStatus.approved, category="sermon"
    )
    s2 = await _make_seg(
        db_session, contributor, text="B", status=SegmentStatus.pending_review, category="hymn"
    )

    token = make_token(reviewer)

    # status=pending_review 只命中 s2
    r = await client.get(
        "/export/manifest.jsonl?status=pending_review",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    lines = [json.loads(row) for row in r.text.strip().splitlines()]
    assert {ln["id"] for ln in lines} == {str(s2.id)}

    # content_category=sermon 在默认 approved 里只命中 s1
    r = await client.get(
        "/export/manifest.jsonl?content_category=sermon",
        headers={"Authorization": f"Bearer {token}"},
    )
    lines = [json.loads(row) for row in r.text.strip().splitlines()]
    assert {ln["id"] for ln in lines} == {str(s1.id)}


async def test_manifest_url_ttl_out_of_range_422(
    client: AsyncClient, reviewer, make_token
):
    token = make_token(reviewer)
    r = await client.get(
        "/export/manifest.jsonl?url_ttl_hours=0", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 422
    r = await client.get(
        "/export/manifest.jsonl?url_ttl_hours=999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


async def test_stats(client: AsyncClient, contributor, reviewer, make_token, db_session):
    await _make_seg(
        db_session, contributor, text="A", status=SegmentStatus.approved,
        category="sermon", duration_ms=2000,
    )
    await _make_seg(
        db_session, contributor, text="B", status=SegmentStatus.approved,
        category="sermon", duration_ms=3000,
    )
    await _make_seg(
        db_session, contributor, text="C", status=SegmentStatus.rejected,
        category="hymn", duration_ms=1500,
    )

    r = await client.get(
        "/export/stats", headers={"Authorization": f"Bearer {make_token(reviewer)}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["total_duration_ms"] == 2000 + 3000 + 1500
    assert body["by_status"] == {"approved": 2, "rejected": 1}
    assert body["by_category"]["sermon"] == {"count": 2, "duration_ms": 5000}
    assert body["by_category"]["hymn"] == {"count": 1, "duration_ms": 1500}


async def test_contributor_forbidden(client: AsyncClient, contributor, make_token):
    token = make_token(contributor)
    r = await client.get(
        "/export/manifest.jsonl", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403
    r = await client.get("/export/stats", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


async def test_train_recording_id_takes_priority_over_speaker(
    client: AsyncClient,
    contributor,
    reviewer,
    make_token,
    db_session,
    monkeypatch,
):
    seg = await _make_seg(
        db_session,
        contributor,
        text="훈련할 문장",
        status=SegmentStatus.approved,
        speaker="database-name",
    )

    async def _noop_train_job(*_args, **_kwargs):
        return None

    monkeypatch.setattr(export_router.settings, "gpu_train_url", "https://gpu.example/api/train")
    monkeypatch.setattr(export_router.settings, "train_token", "test-token")
    monkeypatch.setattr(export_router, "_run_train_job", _noop_train_job)
    headers = {"Authorization": f"Bearer {make_token(reviewer)}"}

    no_recording = await client.post(
        "/export/train?speaker=kr-f3",
        headers=headers,
    )
    assert no_recording.status_code == 400

    current_recording = await client.post(
        f"/export/train?speaker=kr-f3&recording_id={seg.recording_id}",
        headers=headers,
    )
    assert current_recording.status_code == 200, current_recording.text
    assert current_recording.json()["segments"] == 1
    assert current_recording.json()["exp"] == "kr-f3-SV2"


async def test_create_zero_shot_uses_approved_clip_closest_to_six_seconds(
    client: AsyncClient,
    contributor,
    reviewer,
    make_token,
    db_session,
    monkeypatch,
):
    first = await _make_seg(
        db_session,
        contributor,
        text="四秒参考",
        status=SegmentStatus.approved,
        duration_ms=4000,
        speaker="kr f4",
    )
    recording = await db_session.get(Recording, first.recording_id)
    recording.language = "ko"
    closest = Segment(
        recording_id=recording.id,
        segment_index=1,
        start_ms=4000,
        end_ms=10100,
        duration_ms=6100,
        audio_key=f"segments/{uuid.uuid4()}.wav",
        text="最接近六秒的参考",
        status=SegmentStatus.approved.value,
    )
    db_session.add(closest)
    await db_session.commit()

    captured = {}

    async def _get_bytes(key: str) -> bytes:
        captured["audio_key"] = key
        return b"reference-audio"

    async def _register(**kwargs):
        captured.update(kwargs)
        return {"status": "success", "duration_sec": 6.1}

    monkeypatch.setattr(export_router.settings, "gpu_train_url", "https://gpu.example/api/train")
    monkeypatch.setattr(export_router.settings, "train_token", "test-token")
    monkeypatch.setattr(export_router.storage, "get_bytes", _get_bytes)
    monkeypatch.setattr(export_router, "_register_gpu_zero_shot", _register)

    response = await client.post(
        f"/export/zero-shot?recording_id={recording.id}",
        headers={"Authorization": f"Bearer {make_token(reviewer)}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["exp"] == "kr_f4-零样本"
    assert response.json()["source_segment_id"] == str(closest.id)
    assert response.json()["source_duration_ms"] == 6100
    assert captured["audio_key"] == closest.audio_key
    assert captured["audio"] == b"reference-audio"
    assert captured["prompt_text"] == "最接近六秒的参考"
    assert captured["prompt_language"] == "ko"


async def test_create_zero_shot_requires_approved_three_to_ten_second_clip(
    client: AsyncClient,
    contributor,
    reviewer,
    make_token,
    db_session,
    monkeypatch,
):
    segment = await _make_seg(
        db_session,
        contributor,
        text="太短",
        status=SegmentStatus.approved,
        duration_ms=2000,
        speaker="kr-f5",
    )
    monkeypatch.setattr(export_router.settings, "gpu_train_url", "https://gpu.example/api/train")
    monkeypatch.setattr(export_router.settings, "train_token", "test-token")

    response = await client.post(
        f"/export/zero-shot?recording_id={segment.recording_id}",
        headers={"Authorization": f"Bearer {make_token(reviewer)}"},
    )

    assert response.status_code == 400
    assert "3–9 秒" in response.json()["detail"]


async def test_no_auth_401(client: AsyncClient):
    r = await client.get("/export/manifest.jsonl")
    assert r.status_code == 401
    r = await client.get("/export/stats")
    assert r.status_code == 401
