"""admin router 集成测试 (用户管理 + 自我保护)。"""

import uuid

from httpx import AsyncClient


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_list_users(
    client: AsyncClient, admin, contributor, reviewer, make_token
):
    r = await client.get("/admin/users", headers=_auth(make_token(admin)))
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()}
    assert {"admin@test.example", "contrib@test.example", "reviewer@test.example"} <= emails


async def test_list_users_filter_by_role(
    client: AsyncClient, admin, contributor, reviewer, make_token
):
    r = await client.get(
        "/admin/users?role=contributor", headers=_auth(make_token(admin))
    )
    assert r.status_code == 200
    roles = {u["role"] for u in r.json()}
    assert roles == {"contributor"}


async def test_list_users_email_like(
    client: AsyncClient, admin, contributor, make_token
):
    r = await client.get(
        "/admin/users?email_like=contrib", headers=_auth(make_token(admin))
    )
    assert r.status_code == 200
    emails = [u["email"] for u in r.json()]
    assert emails == ["contrib@test.example"]


async def test_get_user(client: AsyncClient, admin, contributor, make_token):
    r = await client.get(
        f"/admin/users/{contributor.id}", headers=_auth(make_token(admin))
    )
    assert r.status_code == 200
    assert r.json()["email"] == "contrib@test.example"


async def test_get_user_404(client: AsyncClient, admin, make_token):
    r = await client.get(
        f"/admin/users/{uuid.uuid4()}", headers=_auth(make_token(admin))
    )
    assert r.status_code == 404


async def test_patch_role_promote(
    client: AsyncClient, admin, contributor, make_token
):
    r = await client.patch(
        f"/admin/users/{contributor.id}",
        headers=_auth(make_token(admin)),
        json={"role": "reviewer"},
    )
    assert r.status_code == 200
    assert r.json()["role"] == "reviewer"


async def test_patch_role_demote_back(
    client: AsyncClient, admin, reviewer, make_token
):
    r = await client.patch(
        f"/admin/users/{reviewer.id}",
        headers=_auth(make_token(admin)),
        json={"role": "contributor"},
    )
    assert r.status_code == 200
    assert r.json()["role"] == "contributor"


async def test_patch_deactivate_and_token_invalidated(
    client: AsyncClient, admin, contributor, make_token
):
    """停用用户后, 该用户旧 token 调 /auth/me 应被 get_current_user 当场拒。"""
    contrib_token = make_token(contributor)
    r = await client.get("/auth/me", headers=_auth(contrib_token))
    assert r.status_code == 200

    r = await client.patch(
        f"/admin/users/{contributor.id}",
        headers=_auth(make_token(admin)),
        json={"is_active": False},
    )
    assert r.status_code == 200 and r.json()["is_active"] is False

    r = await client.get("/auth/me", headers=_auth(contrib_token))
    assert r.status_code == 401


async def test_patch_empty_422(client: AsyncClient, admin, contributor, make_token):
    r = await client.patch(
        f"/admin/users/{contributor.id}",
        headers=_auth(make_token(admin)),
        json={},
    )
    assert r.status_code == 422


async def test_patch_bad_role_422(client: AsyncClient, admin, contributor, make_token):
    r = await client.patch(
        f"/admin/users/{contributor.id}",
        headers=_auth(make_token(admin)),
        json={"role": "superuser"},
    )
    assert r.status_code == 422


async def test_patch_self_demote_400(client: AsyncClient, admin, make_token):
    r = await client.patch(
        f"/admin/users/{admin.id}",
        headers=_auth(make_token(admin)),
        json={"role": "reviewer"},
    )
    assert r.status_code == 400


async def test_patch_self_deactivate_400(client: AsyncClient, admin, make_token):
    r = await client.patch(
        f"/admin/users/{admin.id}",
        headers=_auth(make_token(admin)),
        json={"is_active": False},
    )
    assert r.status_code == 400


async def test_patch_self_set_admin_idempotent(client: AsyncClient, admin, make_token):
    """admin 把自己 role 设回 admin (no-op) 应 200, 自我保护只拦真的降级。"""
    r = await client.patch(
        f"/admin/users/{admin.id}",
        headers=_auth(make_token(admin)),
        json={"role": "admin"},
    )
    assert r.status_code == 200


async def test_patch_random_uuid_404(client: AsyncClient, admin, make_token):
    r = await client.patch(
        f"/admin/users/{uuid.uuid4()}",
        headers=_auth(make_token(admin)),
        json={"role": "reviewer"},
    )
    assert r.status_code == 404


async def test_contributor_forbidden(client: AsyncClient, contributor, make_token):
    r = await client.get("/admin/users", headers=_auth(make_token(contributor)))
    assert r.status_code == 403
    r = await client.patch(
        f"/admin/users/{contributor.id}",
        headers=_auth(make_token(contributor)),
        json={"role": "admin"},
    )
    assert r.status_code == 403


async def test_admin_no_auth_401(client: AsyncClient):
    r = await client.get("/admin/users")
    assert r.status_code == 401
