"""Test configuration and fixtures.

Uses aiosqlite for in-process DB tests where possible.
Note: Some PostgreSQL-specific features (FOR UPDATE SKIP LOCKED, gen_random_uuid, etc.)
are not available in SQLite, so integration tests requiring those are marked to skip
without PostgreSQL.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Test DB URL. Defaults to in-process aiosqlite so local/dev runs need no DB.
# CI can override via SCUD_TEST_DATABASE_URL to point the engine at PostgreSQL
# (e.g. postgresql+asyncpg://scud:scud@localhost:5432/scud_test) so that
# Postgres-only behaviour (FOR UPDATE SKIP LOCKED, row-locks, JSONB) is exercised.
# Must be set BEFORE any scud imports because scud.config reads DATABASE_URL at import.
TEST_DATABASE_URL = os.environ.get(
    "SCUD_TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:"
)
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Auto-skip @pytest.mark.postgres tests unless the engine targets Postgres.

    This keeps the default sqlite suite green: concurrency tests that rely on
    Postgres-only semantics (FOR UPDATE SKIP LOCKED, row-locks, JSONB) are
    collected but SKIPPED — not failed/errored — when SCUD_TEST_DATABASE_URL is
    sqlite (the default). In CI the `backend-postgres` job points the URL at a
    real Postgres, so they actually run there.
    """
    if item.get_closest_marker("postgres") is not None and not _is_postgres(
        TEST_DATABASE_URL
    ):
        pytest.skip("requires PostgreSQL (set SCUD_TEST_DATABASE_URL=postgresql+...)")


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """The DB URL the test engine is bound to (sqlite by default, PG in CI)."""
    return TEST_DATABASE_URL


@pytest_asyncio.fixture(scope="session")
async def engine():
    """Create test database engine.

    SQLite uses an in-memory StaticPool DB whose schema is created from the ORM
    metadata. PostgreSQL points at an already-migrated DB (CI runs
    `alembic upgrade head` first), so we do NOT touch the schema there.
    """
    from scud.db.models import Base

    if _is_sqlite(TEST_DATABASE_URL):
        from sqlalchemy.pool import StaticPool
        eng = create_async_engine(
            TEST_DATABASE_URL,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield eng
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await eng.dispose()
    else:
        # PostgreSQL: schema is provisioned by `alembic upgrade head` in CI.
        # NullPool: движок session-scoped, но каждый тест бежит в своём loop;
        # пулованное соединение из loop теста N переиспользовалось бы в loop
        # теста N+1, и pool_pre_ping.do_ping() ждал бы в закрытом loop
        # ("Future attached to a different loop"). NullPool открывает свежее
        # соединение на каждый checkout в текущем loop — и даёт конкурентным
        # тестам независимые соединения для SKIP LOCKED.
        from sqlalchemy.pool import NullPool

        eng = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        yield eng
        await eng.dispose()


@pytest_asyncio.fixture
async def sessionmaker(engine) -> async_sessionmaker[AsyncSession]:
    """A session factory bound to the test engine.

    Concurrency tests use this to open multiple *independent* sessions (each on
    its own DB connection) so real row-locks / SKIP LOCKED are exercised.
    """
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Provide a clean session for each test with rollback isolation."""
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def app(engine):
    """Create FastAPI test app with overridden DB session."""
    from scud.db.session import get_session
    from scud.main import create_app

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    application = create_app()

    async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ---- Helper fixtures ----

@pytest_asyncio.fixture
async def admin_api_key(db_session: AsyncSession) -> str:
    """Create an admin API key and return its plaintext."""
    import hashlib
    import secrets
    from scud.db.models import ApiKey

    plaintext = "sk_admin_" + secrets.token_urlsafe(36)
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    api_key = ApiKey(
        key_hash=key_hash,
        key_prefix=plaintext[:8],
        kind="admin",
        name="test_admin",
    )
    db_session.add(api_key)
    await db_session.commit()
    return plaintext


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> dict:
    """Create a test user and return user data."""
    import secrets
    import uuid
    from scud.domain.auth import hash_password
    from scud.db.models import User

    # Use unique login per test to avoid UNIQUE constraint violations across tests
    # since engine is session-scoped (shared DB) and commits are permanent.
    suffix = secrets.token_hex(4)
    login = f"testuser_{suffix}"
    password = "testpass123"
    user = User(
        login=login,
        password_hash=hash_password(password),
        display_name="Test User",
        user_group_id=uuid.uuid4(),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return {
        "user_id": user.user_id,
        "login": login,
        "password": password,
        "user_group_id": user.user_group_id,
    }
