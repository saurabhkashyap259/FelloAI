"""Database engines and the tenant-scoped connection.

Every read or write of tenant data goes through `tenant_scope`, which opens a transaction
and sets `app.tenant_id` with SET LOCAL, so RLS confines the connection to one tenant.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, text

from worklist.config import get_settings


@lru_cache
def web_engine() -> Engine:
    return create_engine(get_settings().database_url_web, pool_pre_ping=True, pool_size=5)


@lru_cache
def job_engine() -> Engine:
    return create_engine(get_settings().database_url_job, pool_pre_ping=True)


@lru_cache
def owner_engine() -> Engine:
    return create_engine(get_settings().database_url_owner, pool_pre_ping=True)


def set_tenant(conn: Connection, tenant_id: UUID | str) -> None:
    conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(UUID(str(tenant_id)))})


@contextmanager
def tenant_scope(engine: Engine, tenant_id: UUID | str) -> Iterator[Connection]:
    """A transaction in which only `tenant_id`'s rows are visible (SET LOCAL semantics)."""
    with engine.begin() as conn:
        set_tenant(conn, tenant_id)
        yield conn


@contextmanager
def operator_conn() -> Iterator[Connection]:
    """Owner connection for migrations, seeding and operator CLI code only. Never used by web routes."""
    with owner_engine().begin() as conn:
        yield conn


def list_tenants(engine: Engine | None = None) -> list[dict]:
    with (engine or job_engine()).connect() as conn:
        rows = conn.execute(text("SELECT id, name FROM tenant ORDER BY name")).mappings().all()
    return [dict(r) for r in rows]
