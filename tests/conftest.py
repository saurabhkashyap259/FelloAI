"""Test harness: a dedicated `worklist_test` database, migrated once per session, wiped per test."""

import os
import secrets
from datetime import UTC, date, datetime, timedelta

import pytest

TEST_DB = "worklist_test"
_HOST = os.environ.get("TEST_DB_HOST", "localhost:55432")
os.environ["DATABASE_URL_OWNER"] = f"postgresql+psycopg://postgres:postgres@{_HOST}/{TEST_DB}"
os.environ["DATABASE_URL_WEB"] = f"postgresql+psycopg://worklist_web:worklist_web@{_HOST}/{TEST_DB}"
os.environ["DATABASE_URL_JOB"] = f"postgresql+psycopg://worklist_job:worklist_job@{_HOST}/{TEST_DB}"
os.environ["DEMO_MODE"] = "true"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["SESSION_SECRET"] = "test-secret"

from sqlalchemy import create_engine, text  # noqa: E402

from worklist.db import tables as t  # noqa: E402
from worklist.db.engine import operator_conn  # noqa: E402

PASSWORD = "correct-horse"


@pytest.fixture(scope="session", autouse=True)
def database():
    admin = create_engine(f"postgresql+psycopg://postgres:postgres@{_HOST}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {TEST_DB}"))
    admin.dispose()
    from worklist.db.migrate import upgrade

    upgrade()
    yield


ALL_TABLES = ["outreach_draft", "alert", "csm_action", "account_score", "tenant_calibrator", "signal_snapshot",
              "model_version", "pipeline_run", "quarantined_event", "event_account", "raw_event",
              "renewal_outcome", "account_fact_conflict", "account_source_id", "account", "session",
              "app_user", "tenant_settings", "tenant", "sim_clock", "sim.pending_feed_event", "access_audit"]


@pytest.fixture(autouse=True)
def clean_db():
    with operator_conn() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(ALL_TABLES) + " CASCADE"))
    from worklist.web.auth import login_limiter

    login_limiter.reset()
    yield


# --- factories ---------------------------------------------------------------------------

_hash_cache: dict[str, str] = {}


def _password_hash() -> str:
    if PASSWORD not in _hash_cache:
        from worklist.web.auth import hash_password

        _hash_cache[PASSWORD] = hash_password(PASSWORD)
    return _hash_cache[PASSWORD]


def make_tenant(name: str | None = None, **settings) -> dict:
    tenant_id = t.new_id()
    name = name or f"Tenant {secrets.token_hex(3)}"
    with operator_conn() as conn:
        conn.execute(t.tenant.insert().values(id=tenant_id, name=name))
        conn.execute(t.tenant_settings.insert().values(tenant_id=tenant_id, **settings))
    return {"id": tenant_id, "name": name}


def make_user(tenant: dict, role: str = "csm", email: str | None = None, name: str = "Test User") -> dict:
    user_id = t.new_id()
    email = email or f"{secrets.token_hex(4)}@example.test"
    with operator_conn() as conn:
        conn.execute(t.app_user.insert().values(id=user_id, tenant_id=tenant["id"], email=email,
                                                display_name=name, role=role, password_hash=_password_hash()))
    return {"id": user_id, "tenant_id": tenant["id"], "email": email, "role": role}


def make_account(tenant: dict, owner: dict | None = None, **values) -> dict:
    account_id = t.new_id()
    row = {"id": account_id, "tenant_id": tenant["id"], "name": values.pop("name", f"Acct {secrets.token_hex(3)}"),
           "owner_user_id": owner["id"] if owner else None, "contract_type": "term",
           "renewal_date": date(2026, 12, 1), "arr": 50_000.0, "contract_start": date(2024, 1, 1)}
    row.update(values)
    with operator_conn() as conn:
        conn.execute(t.account.insert().values(**row))
    return row


def make_session(user: dict, expires_in_hours: float = 12) -> tuple[str, str]:
    session_id, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(16)
    with operator_conn() as conn:
        conn.execute(t.session.insert().values(
            id=session_id, user_id=user["id"], tenant_id=user["tenant_id"], role=user["role"],
            csrf_token=csrf, expires_at=datetime.now(UTC) + timedelta(hours=expires_in_hours)))
    return session_id, csrf


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from worklist.web.app import app

    return TestClient(app, follow_redirects=False)


@pytest.fixture
def client_as():
    """client_as(user) -> a TestClient signed in as `user`, with `.csrf` set."""
    from fastapi.testclient import TestClient

    from worklist.web.app import app
    from worklist.web.auth import COOKIE_NAME, sign

    def _make(user: dict):
        session_id, csrf = make_session(user)
        client = TestClient(app, follow_redirects=False)
        client.cookies.set(COOKIE_NAME, sign(session_id))
        client.csrf = csrf  # type: ignore[attr-defined]
        return client

    return _make
