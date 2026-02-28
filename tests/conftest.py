import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import hash_token
from app.database import get_db
from app.models import Base, ClientToken, User

TEST_DB_URL = "sqlite+aiosqlite://"

engine = create_async_engine(TEST_DB_URL, echo=False)
test_session = async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def setup_db() -> AsyncGenerator[None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def override_get_db() -> AsyncGenerator[AsyncSession]:
    async with test_session() as session:
        yield session


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession]:
    async with test_session() as session:
        yield session


@pytest.fixture
async def test_user(db: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        sub="test-sub-123",
        email="test@example.com",
        name="Test User",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


TEST_TOKEN_PLAINTEXT = "test-token-for-testing-only-1234"


@pytest.fixture
async def test_token(db: AsyncSession, test_user: User) -> ClientToken:
    token = ClientToken(
        id=str(uuid.uuid4()),
        user_id=test_user.id,
        token_hash=hash_token(TEST_TOKEN_PLAINTEXT),
        name="Test Device",
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return token


@pytest.fixture
async def session_cookie(test_user: User) -> str:
    from app.auth import SessionManager
    from app.config import get_settings

    sm = SessionManager(get_settings().SECRET_KEY)
    return sm.create_session(test_user.id)


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    from app.main import app

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
