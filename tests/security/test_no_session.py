"""Requests without a valid session are rejected on every protected route (FR-026)."""

import pytest

from tests.conftest import make_session, make_tenant, make_user
from tests.security.route_inventory import INVENTORY
from tests.security.test_cross_tenant import url_for
from worklist.web.app import route_table
from worklist.web.auth import COOKIE_NAME, sign

PROTECTED = [(m, p) for (m, p), spec in INVENTORY.items() if spec["rule"] != "public"]
FAKE = {k: "00000000-0000-0000-0000-000000000000" for k in ("account", "score", "alert", "draft")}


def _call(client, method, path):
    url = url_for(path, INVENTORY[(method, path)].get("ids", {}), FAKE)
    return client.get(url) if method == "GET" else client.post(url)


@pytest.mark.parametrize("method,path", PROTECTED)
def test_no_session_redirects_pages_and_401s_api(client, method, path):
    if (method, path) not in route_table():
        pytest.skip("route not registered yet")
    response = _call(client, method, path)
    if path.startswith("/api/"):
        assert response.status_code == 401
    else:
        assert response.status_code == 303 and response.headers["location"] == "/login"


@pytest.mark.parametrize("method,path", [r for r in PROTECTED if r[0] == "GET"])
def test_expired_session_is_rejected(client, method, path):
    if (method, path) not in route_table():
        pytest.skip("route not registered yet")
    session_id, _ = make_session(make_user(make_tenant()), expires_in_hours=-1)
    client.cookies.set(COOKIE_NAME, sign(session_id))
    assert _call(client, method, path).status_code in (303, 401)
