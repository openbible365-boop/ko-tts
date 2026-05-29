"""auth 流程集成测试 (需要真 Postgres)。

未设置 KOTTS_TEST_DATABASE_URL 时, db_session 上游 need_db fixture 会 skip。
"""

from httpx import AsyncClient


async def test_register_login_me_happy_path(client: AsyncClient):
    # 注册
    r = await client.post(
        "/auth/register",
        json={"email": "h1@test.example", "password": "good-password-12345"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "h1@test.example"
    assert body["role"] == "contributor"
    assert body["is_active"] is True

    # 登录 (OAuth2 password flow = form data)
    r = await client.post(
        "/auth/login",
        data={"username": "h1@test.example", "password": "good-password-12345"},
    )
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert isinstance(token, str) and len(token) > 50

    # /auth/me
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "h1@test.example"


async def test_register_duplicate_email_409(client: AsyncClient):
    payload = {"email": "dup@test.example", "password": "good-password-12345"}
    r = await client.post("/auth/register", json=payload)
    assert r.status_code == 201
    r = await client.post("/auth/register", json=payload)
    assert r.status_code == 409


async def test_register_short_password_422(client: AsyncClient):
    r = await client.post(
        "/auth/register",
        json={"email": "short@test.example", "password": "abc"},
    )
    assert r.status_code == 422


async def test_login_wrong_password_401(client: AsyncClient):
    await client.post(
        "/auth/register",
        json={"email": "wp@test.example", "password": "good-password-12345"},
    )
    r = await client.post(
        "/auth/login",
        data={"username": "wp@test.example", "password": "wrong-password-12345"},
    )
    assert r.status_code == 401


async def test_login_unknown_email_401(client: AsyncClient):
    r = await client.post(
        "/auth/login",
        data={"username": "nobody@test.example", "password": "irrelevant-12345"},
    )
    assert r.status_code == 401


async def test_me_without_token_401(client: AsyncClient):
    r = await client.get("/auth/me")
    assert r.status_code == 401


async def test_me_with_bad_token_401(client: AsyncClient):
    r = await client.get("/auth/me", headers={"Authorization": "Bearer not.a.real.token"})
    assert r.status_code == 401


async def test_deactivated_user_token_rejected_401(
    client: AsyncClient, contributor, make_token, db_session
):
    """停用用户 -> 旧 token 应当被 get_current_user 当场拒。"""
    token = make_token(contributor)
    # 正常先用一次
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

    # 停用
    contributor.is_active = False
    await db_session.commit()

    # 同一 token 再调 -> 401
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_role_gate_403_for_contributor(
    client: AsyncClient, contributor, make_token
):
    """contributor 调 admin-only 端点 -> 403。"""
    token = make_token(contributor)
    r = await client.get("/admin/users", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


async def test_role_gate_200_for_admin(client: AsyncClient, admin, make_token):
    token = make_token(admin)
    r = await client.get("/admin/users", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    # 至少能看到 admin 自己
    emails = {u["email"] for u in r.json()}
    assert "admin@test.example" in emails
