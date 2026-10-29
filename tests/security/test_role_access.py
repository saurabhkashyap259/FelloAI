"""CSMs cannot reach other CSMs' accounts, alerts or drafts in the same tenant; managers can (FR-026, FR-027)."""

import pytest
from sqlalchemy import text

from tests.security.route_inventory import INVENTORY
from tests.security.test_cross_tenant import _request, url_for
from worklist.db.engine import operator_conn
from worklist.web.app import route_table

CSM_OWN = [(m, p) for (m, p), spec in INVENTORY.items() if spec["rule"] == "csm_own"]


@pytest.mark.parametrize("method,path", CSM_OWN)
def test_other_csm_gets_404_and_audit(worlds, client_as, method, path):
    if (method, path) not in route_table():
        pytest.skip("route not registered yet")
    acme, _ = worlds
    other = client_as(acme["csms"][1])
    url = url_for(path, INVENTORY[(method, path)]["ids"], acme["owned"])
    with operator_conn() as conn:
        before = conn.execute(text("SELECT count(*) FROM access_audit WHERE outcome = 'denied_role'")).scalar()
    assert _request(other, method, url).status_code == 404
    with operator_conn() as conn:
        after = conn.execute(text("SELECT count(*) FROM access_audit WHERE outcome = 'denied_role'")).scalar()
    assert after == before + 1


@pytest.mark.parametrize("method,path", CSM_OWN)
def test_manager_can_access(worlds, client_as, method, path):
    if (method, path) not in route_table():
        pytest.skip("route not registered yet")
    acme, _ = worlds
    url = url_for(path, INVENTORY[(method, path)]["ids"], acme["owned"])
    assert _request(client_as(acme["manager"]), method, url).status_code in (200, 303)


def test_runs_page_is_manager_only(worlds, client_as):
    acme, _ = worlds
    if ("GET", "/runs") not in route_table():
        pytest.skip("route not registered yet")
    assert client_as(acme["csms"][0]).get("/runs").status_code == 404
    assert client_as(acme["manager"]).get("/runs").status_code == 200
