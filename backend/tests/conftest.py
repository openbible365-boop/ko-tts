"""pytest 公用 fixtures。

策略:
- 单元测试不依赖 DB, 直接跑。
- 集成测试需要真 Postgres, 由 KOTTS_TEST_DATABASE_URL 提供 (本地未设则 skip,
  CI 在 GitHub Actions 的 postgres 服务容器里设置)。
- 启动前必须把环境变量塞好再 import app —— pydantic-settings 在
  settings = get_settings() 那一刻就读 env, 所以 conftest 顶部 setdefault。
"""

import os
from collections.abc import AsyncIterator

import pytest

# ---- env 默认值, 必须在 import app 之前 ----
_TEST_DB_URL = os.environ.get("KOTTS_TEST_DATABASE_URL")
# 即使没设 KOTTS_TEST_DATABASE_URL, 也要给 DATABASE_URL 一个伪值, 否则 app 模块 import 都跑不起来
os.environ.setdefault(
    "DATABASE_URL",
    _TEST_DB_URL or "postgresql+asyncpg://x:x@localhost/no_test_db",
)
os.environ.setdefault("JWT_SECRET", "0" * 64)
os.environ.setdefault("R2_ENDPOINT_URL", "https://test.r2.example.com")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("R2_BUCKET", "ob-tts-data-test")

# 现在 import app 安全
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.db import Base, SessionLocal, engine, get_session  # noqa: E402
from app.models import User, UserRole  # noqa: E402
from app.security import hash_password  # noqa: E402


# -------- 跳过 / 标志 --------
@pytest.fixture
def need_db() -> None:
    if not _TEST_DB_URL:
        pytest.skip("integration test requires KOTTS_TEST_DATABASE_URL")


# -------- schema 一次性建立 --------
@pytest.fixture(scope="session", autouse=True)
async def _setup_schema() -> AsyncIterator[None]:
    if not _TEST_DB_URL:
        yield
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


# -------- 每个测试一个 session, 跑完 truncate 所有表 --------
@pytest.fixture
async def db_session(need_db: None) -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
    async with engine.begin() as conn:
        # FK CASCADE 在 segments→recordings→users 路径上是 RESTRICT, 用 TRUNCATE CASCADE 强清
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))


# -------- httpx ASGITransport client (整个请求生命周期共用 db_session) --------
@pytest.fixture
async def client(db_session: AsyncSession):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# -------- 用户工厂 --------
async def _make_user(
    db_session: AsyncSession,
    email: str,
    password: str = "test-password-12345",
    role: UserRole = UserRole.contributor,
) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        role=role.value,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def contributor(db_session: AsyncSession) -> User:
    return await _make_user(db_session, "contrib@test.example", role=UserRole.contributor)


@pytest.fixture
async def reviewer(db_session: AsyncSession) -> User:
    return await _make_user(db_session, "reviewer@test.example", role=UserRole.reviewer)


@pytest.fixture
async def admin(db_session: AsyncSession) -> User:
    return await _make_user(db_session, "admin@test.example", role=UserRole.admin)


# -------- 给某用户拿一个 access token (绕过 /auth/login, 直接由 security 模块签发) --------
@pytest.fixture
def make_token():
    from app.security import create_access_token

    def _make(user: User) -> str:
        return create_access_token(str(user.id))

    return _make
