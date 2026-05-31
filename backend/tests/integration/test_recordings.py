"""recordings router 集成测试。"""

import uuid

from httpx import AsyncClient

from app.models import RecordingStatus


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_create_recording_201(client: AsyncClient, contributor, make_token):
    r = await client.post(
        "/recordings",
        headers=_auth(make_token(contributor)),
        json={
            "content_category": "sermon",
            "original_filename": "sermon.mp3",
            "mime_type": "audio/mpeg",
            "title": "Test",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["recording"]["status"] == "pending_upload"
    assert body["recording"]["content_category"] == "sermon"
    assert body["upload_url"].startswith("https://test.r2.example.com/")
    assert body["upload_expires_in"] == 3600
    # audio_key 模式: recordings/{uuid}/original.mp3
    assert body["recording"]["audio_key"].endswith(".mp3")


async def test_create_recording_no_auth_401(client: AsyncClient):
    r = await client.post(
        "/recordings",
        json={"content_category": "sermon", "original_filename": "x.mp3"},
    )
    assert r.status_code == 401


async def test_create_recording_bad_category_422(
    client: AsyncClient, contributor, make_token
):
    r = await client.post(
        "/recordings",
        headers=_auth(make_token(contributor)),
        json={"content_category": "podcast", "original_filename": "x.mp3"},
    )
    assert r.status_code == 422


async def test_complete_recording_happy(
    client: AsyncClient, contributor, make_token,
    make_recording_factory, mock_head_object,
):
    rec = await make_recording_factory(contributor)
    r = await client.post(
        f"/recordings/{rec.id}/complete", headers=_auth(make_token(contributor))
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "uploaded"
    assert body["file_size_bytes"] == 1234


async def test_complete_recording_file_missing_400(
    client: AsyncClient, contributor, make_token,
    make_recording_factory, mock_head_object_missing,
):
    rec = await make_recording_factory(contributor)
    r = await client.post(
        f"/recordings/{rec.id}/complete", headers=_auth(make_token(contributor))
    )
    assert r.status_code == 400


async def test_complete_recording_wrong_status_409(
    client: AsyncClient, contributor, make_token,
    make_recording_factory, mock_head_object,
):
    rec = await make_recording_factory(contributor, status=RecordingStatus.segmented)
    r = await client.post(
        f"/recordings/{rec.id}/complete", headers=_auth(make_token(contributor))
    )
    assert r.status_code == 409


async def test_complete_recording_other_user_forbidden(
    client: AsyncClient, contributor, reviewer, make_token,
    make_recording_factory, mock_head_object,
):
    """reviewer 不是 admin 且非上传者 → 403 (complete 仅上传者或 admin)。"""
    rec = await make_recording_factory(contributor)
    r = await client.post(
        f"/recordings/{rec.id}/complete", headers=_auth(make_token(reviewer))
    )
    assert r.status_code == 403


async def test_get_recording_owner(
    client: AsyncClient, contributor, make_token, make_recording_factory
):
    rec = await make_recording_factory(contributor)
    r = await client.get(f"/recordings/{rec.id}", headers=_auth(make_token(contributor)))
    assert r.status_code == 200
    assert r.json()["id"] == str(rec.id)


async def test_get_recording_reviewer_sees_others(
    client: AsyncClient, contributor, reviewer, make_token, make_recording_factory
):
    rec = await make_recording_factory(contributor)
    r = await client.get(f"/recordings/{rec.id}", headers=_auth(make_token(reviewer)))
    assert r.status_code == 200


async def test_get_recording_random_404(
    client: AsyncClient, contributor, make_token
):
    r = await client.get(
        f"/recordings/{uuid.uuid4()}", headers=_auth(make_token(contributor))
    )
    assert r.status_code == 404


async def test_list_recordings_contributor_sees_only_own(
    client: AsyncClient, contributor, reviewer, make_token, make_recording_factory
):
    own = await make_recording_factory(contributor)
    await make_recording_factory(reviewer)
    r = await client.get("/recordings", headers=_auth(make_token(contributor)))
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()}
    assert ids == {str(own.id)}


async def test_list_recordings_reviewer_sees_all(
    client: AsyncClient, contributor, reviewer, make_token, make_recording_factory
):
    a = await make_recording_factory(contributor)
    b = await make_recording_factory(reviewer)
    r = await client.get("/recordings", headers=_auth(make_token(reviewer)))
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()}
    assert {str(a.id), str(b.id)} <= ids


async def test_list_recordings_staff_sees_uploader(
    client: AsyncClient, contributor, reviewer, make_token, make_recording_factory
):
    """staff 列表里每条采集带上传者邮箱(供审核分辨归属)。"""
    rec = await make_recording_factory(contributor)
    r = await client.get("/recordings", headers=_auth(make_token(reviewer)))
    assert r.status_code == 200
    row = next(x for x in r.json() if x["id"] == str(rec.id))
    assert row["uploader_email"] == "contrib@test.example"


async def test_download_url(
    client: AsyncClient, contributor, make_token, make_recording_factory
):
    rec = await make_recording_factory(contributor)
    r = await client.get(
        f"/recordings/{rec.id}/download-url", headers=_auth(make_token(contributor))
    )
    assert r.status_code == 200
    body = r.json()
    assert body["url"].startswith("https://test.r2.example.com/")
    assert body["expires_in"] == 3600


async def test_trigger_segmentation_resets_to_uploaded(
    client: AsyncClient, contributor, make_token, make_recording_factory
):
    rec = await make_recording_factory(contributor, status=RecordingStatus.segmented)
    r = await client.post(
        f"/recordings/{rec.id}/segment", headers=_auth(make_token(contributor))
    )
    assert r.status_code == 200
    assert r.json()["status"] == "uploaded"


async def test_trigger_segmentation_409_if_in_progress(
    client: AsyncClient, contributor, make_token, make_recording_factory
):
    rec = await make_recording_factory(contributor, status=RecordingStatus.segmenting)
    r = await client.post(
        f"/recordings/{rec.id}/segment", headers=_auth(make_token(contributor))
    )
    assert r.status_code == 409


async def test_list_segments_under_recording(
    client: AsyncClient, contributor, make_token,
    make_recording_factory, make_segment_factory,
):
    rec = await make_recording_factory(contributor)
    s0 = await make_segment_factory(rec, index=0)
    s1 = await make_segment_factory(rec, index=1)
    r = await client.get(
        f"/recordings/{rec.id}/segments", headers=_auth(make_token(contributor))
    )
    assert r.status_code == 200
    ids = [x["id"] for x in r.json()]
    assert ids == [str(s0.id), str(s1.id)]
