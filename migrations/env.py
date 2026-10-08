"""迁移角色独立配置；离线 SQL 不需要数据库或真实凭据。"""
import os
from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url
from storage.schema import metadata


def run():
    offline = context.is_offline_mode()
    url = os.environ.get("MIGRATION_DATABASE_URL")
    if not url and not offline:
        raise RuntimeError("迁移必须显式提供 MIGRATION_DATABASE_URL")
    parsed = make_url(url or "postgresql+psycopg://offline/offline")
    if parsed.get_backend_name() != "postgresql":
        raise RuntimeError("迁移仅支持 PostgreSQL/PostGIS")
    if offline:
        context.configure(url=parsed, target_metadata=metadata, literal_binds=True,
                          dialect_opts={"paramstyle": "named"})
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(parsed.set(drivername="postgresql+psycopg"), poolclass=pool.NullPool,
                           hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=metadata,
                              version_table_schema=connection.dialect.default_schema_name,
                              compare_type=True, transaction_per_migration=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run()
