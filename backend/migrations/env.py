import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from anisync import models  # noqa: F401  (registers tables)
from anisync.config import get_settings
from anisync.db import Base

config = context.config
target_metadata = Base.metadata


def include_name(name, type_, parent_names):
    # Procrastinate manages its own tables (procrastinate schema --apply).
    return not (type_ == "table" and name and name.startswith("procrastinate"))


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata, include_name=include_name)
    with context.begin_transaction():
        context.run_migrations()


def database_url() -> str:
    # `-x`-less override used by tests: config.set_main_option("sqlalchemy.url", ...).
    return config.get_main_option("sqlalchemy.url") or get_settings().sqlalchemy_url


async def run_async_migrations():
    engine = create_async_engine(database_url())
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_async_migrations())
