from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return
    if not url.database or url.database == ":memory:":
        return
    Path(url.database).parent.mkdir(parents=True, exist_ok=True)


settings = get_settings()
_ensure_sqlite_parent_dir(settings.database_url)
engine: AsyncEngine = create_async_engine(settings.database_url, future=True)
_session_factory = async_sessionmaker(engine, expire_on_commit=False)


class _SessionLocalProxy:
    def __call__(self, *args, **kwargs) -> AsyncSession:
        return _session_factory(*args, **kwargs)


SessionLocal = _SessionLocalProxy()


async def reconfigure_database(database_url: str) -> None:
    global engine, _session_factory
    await engine.dispose()
    _ensure_sqlite_parent_dir(database_url)
    engine = create_async_engine(database_url, future=True)
    _session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def create_all() -> None:
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        )
        result = await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
        if result.scalar_one_or_none() is None:
            await conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0001_baseline')"))
