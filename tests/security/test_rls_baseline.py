"""Isolation is enforced by the database itself (FR-025, research R2)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import make_account, make_tenant, make_user
from worklist.db.engine import job_engine, tenant_scope, web_engine
from worklist.db.tables import TENANT_TABLES


@pytest.fixture
def two_tenants():
    a, b = make_tenant("A"), make_tenant("B")
    ua, ub = make_user(a), make_user(b)
    make_account(a, ua)
    make_account(b, ub)
    return a, b


@pytest.mark.parametrize("engine_fn", [web_engine, job_engine], ids=["web", "job"])
def test_no_tenant_setting_returns_no_rows(two_tenants, engine_fn):
    with engine_fn().connect() as conn:
        for table in TENANT_TABLES:
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table


@pytest.mark.parametrize("engine_fn", [web_engine, job_engine], ids=["web", "job"])
def test_tenant_scope_hides_other_tenant(two_tenants, engine_fn):
    a, b = two_tenants
    with tenant_scope(engine_fn(), a["id"]) as conn:
        tenant_ids = set(conn.execute(text("SELECT DISTINCT tenant_id FROM account")).scalars())
        users = set(conn.execute(text("SELECT DISTINCT tenant_id FROM app_user")).scalars())
    assert tenant_ids == {a["id"]} and users == {a["id"]}


def test_job_cannot_write_into_other_tenant(two_tenants):
    a, b = two_tenants
    with pytest.raises(ProgrammingError):
        with tenant_scope(job_engine(), a["id"]) as conn:
            conn.execute(text("INSERT INTO account (id, tenant_id, name, contract_type) "
                              "VALUES (gen_random_uuid(), :b, 'x', 'term')"), {"b": b["id"]})


@pytest.mark.parametrize("table", ["raw_event", "event_account", "signal_snapshot", "account_score"])
@pytest.mark.parametrize("engine_fn", [web_engine, job_engine], ids=["web", "job"])
def test_append_only_tables_reject_update_and_delete(two_tenants, engine_fn, table):
    a, _ = two_tenants
    for statement in (f"UPDATE {table} SET tenant_id = tenant_id", f"DELETE FROM {table}"):
        with pytest.raises(ProgrammingError):
            with tenant_scope(engine_fn(), a["id"]) as conn:
                conn.execute(text(statement))


def test_web_role_cannot_read_audit_or_sim_feed(two_tenants):
    a, _ = two_tenants
    for statement in ("SELECT * FROM access_audit", "SELECT * FROM sim.pending_feed_event"):
        with pytest.raises(ProgrammingError):
            with tenant_scope(web_engine(), a["id"]) as conn:
                conn.execute(text(statement))


def test_web_role_cannot_modify_accounts(two_tenants):
    a, _ = two_tenants
    with pytest.raises(ProgrammingError):
        with tenant_scope(web_engine(), a["id"]) as conn:
            conn.execute(text("UPDATE account SET name = 'x'"))
