"""Nothing leaks across tenants through health checks, the model page or the sign-in page (FR-025a, FR-027c)."""

from sqlalchemy import text

from worklist.db.engine import operator_conn


def test_health_endpoints_reveal_no_tenant_details(worlds, client):
    acme, bravo = worlds
    for path in ("/healthz", "/healthz/pipeline"):
        body = client.get(path).text
        for w in (acme, bravo):
            assert w["tenant"]["name"] not in body and str(w["tenant"]["id"]) not in body
        assert set(client.get(path).json()) <= {"status", "stale_tenants"}


def test_model_page_shows_only_own_tenant_metrics(worlds, client_as):
    acme, bravo = worlds
    with operator_conn() as conn:  # give each tenant distinctive per-tenant evaluation numbers
        for w, marker in ((acme, 11), (bravo, 977)):
            conn.execute(text("UPDATE tenant_calibrator SET params = jsonb_set(params, '{evaluation}', "
                              "jsonb_build_object('churned_accounts', :n)) WHERE tenant_id = :t"),
                         {"n": marker, "t": w["tenant"]["id"]})
    html = client_as(acme["csms"][0]).get("/model").text
    assert ">11<" in html and "977" not in html and bravo["tenant"]["name"] not in html


def test_demo_list_hidden_when_demo_mode_off(worlds, client, monkeypatch):
    from worklist.config import get_settings

    with operator_conn() as conn:
        conn.execute(text("UPDATE app_user SET is_demo = true"))
    monkeypatch.setattr(get_settings(), "demo_mode", False)
    assert "Demo accounts" not in client.get("/login").text
