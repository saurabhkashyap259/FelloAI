"""Tenant A users cannot reach tenant B resources by any route; the response is indistinguishable
from a non-existent ID and the attempt is audited (US6, FR-026, SC-008)."""

import uuid

import pytest
from sqlalchemy import text

from tests.security.route_inventory import INVENTORY
from worklist.db.engine import operator_conn
from worklist.web.app import route_table

ID_ROUTES = [(m, p) for (m, p), spec in INVENTORY.items() if spec.get("ids")]


def url_for(path: str, ids: dict, resources: dict) -> str:
    out = path
    for param, kind in ids.items():
        out = out.replace("{" + param + "}", str(resources[kind]))
    return out.replace("{signal_name}", "tickets_30d")


def _request(client, method, url):
    if method == "GET":
        return client.get(url)
    return client.post(url, data={"csrf_token": client.csrf, "action": "contacted", "final_text": "x"})


def _audit_count(outcome: str) -> int:
    with operator_conn() as conn:
        return conn.execute(text("SELECT count(*) FROM access_audit WHERE outcome = :o"), {"o": outcome}).scalar()


@pytest.mark.parametrize("method,path", ID_ROUTES)
def test_cross_tenant_ids_are_not_found_and_audited(worlds, client_as, method, path):
    if (method, path) not in route_table():
        pytest.skip("route not registered yet")
    acme, bravo = worlds
    client = client_as(acme["manager"])  # a manager sees everything in their own tenant
    ids = INVENTORY[(method, path)]["ids"]
    foreign = url_for(path, ids, bravo["owned"])
    missing = url_for(path, ids, {k: uuid.uuid4() for k in bravo["owned"]})
    before = _audit_count("denied_cross_tenant")
    r_foreign, r_missing = _request(client, method, foreign), _request(client, method, missing)
    assert r_foreign.status_code == r_missing.status_code == 404
    assert r_foreign.content == r_missing.content
    assert _audit_count("denied_cross_tenant") == before + 1


@pytest.mark.parametrize("method,path", ID_ROUTES)
def test_own_tenant_ids_work_for_owner(worlds, client_as, method, path):
    if (method, path) not in route_table():
        pytest.skip("route not registered yet")
    acme, _ = worlds
    client = client_as(acme["csms"][0])
    url = url_for(path, INVENTORY[(method, path)]["ids"], acme["owned"])
    assert _request(client, method, url).status_code in (200, 303)
