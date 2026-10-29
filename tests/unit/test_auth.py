"""Sign-in, sessions, CSRF and rate limiting (FR-027a)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from tests.conftest import PASSWORD, make_session, make_tenant, make_user
from worklist.db.engine import operator_conn
from worklist.web.auth import COOKIE_NAME, hash_password, sign, verify_password


def test_password_hash_round_trip():
    hashed = hash_password("s3cret")
    assert verify_password(hashed, "s3cret") and not verify_password(hashed, "wrong")


def test_login_success_sets_session_with_12h_expiry(client):
    user = make_user(make_tenant())
    response = client.post("/login", data={"email": user["email"], "password": PASSWORD})
    assert response.status_code == 303 and COOKIE_NAME in response.cookies
    with operator_conn() as conn:
        expires = conn.execute(text("SELECT expires_at FROM session WHERE user_id = :u"), {"u": user["id"]}).scalar()
    assert timedelta(hours=11, minutes=59) < expires - datetime.now(UTC) <= timedelta(hours=12)


def test_same_error_for_unknown_email_and_wrong_password(client):
    user = make_user(make_tenant())
    unknown = client.post("/login", data={"email": "nobody@example.test", "password": PASSWORD})
    wrong = client.post("/login", data={"email": user["email"], "password": "nope"})
    assert unknown.status_code == wrong.status_code == 401
    assert "Email or password is incorrect." in unknown.text and "Email or password is incorrect." in wrong.text


def test_expired_session_is_rejected(client):
    user = make_user(make_tenant())
    session_id, _ = make_session(user, expires_in_hours=-1)
    client.cookies.set(COOKIE_NAME, sign(session_id))
    assert client.get("/").status_code == 303


def test_tampered_cookie_is_rejected(client):
    user = make_user(make_tenant())
    session_id, _ = make_session(user)
    client.cookies.set(COOKIE_NAME, session_id + ".forged")
    assert client.get("/").status_code == 303


def test_logout_invalidates_session_server_side(client_as):
    user = make_user(make_tenant())
    client = client_as(user)
    assert client.get("/").status_code == 200
    assert client.post("/logout", data={"csrf_token": client.csrf}).status_code == 303
    assert client.get("/").status_code == 303


def test_post_without_csrf_is_forbidden(client_as):
    client = client_as(make_user(make_tenant()))
    assert client.post("/logout").status_code == 403


def test_eleventh_attempt_in_a_minute_is_rate_limited(client):
    user = make_user(make_tenant())
    codes = [client.post("/login", data={"email": user["email"], "password": "bad"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_demo_accounts_listed_only_in_demo_mode(client, monkeypatch):
    make_user(make_tenant("Demo Co"), email="csm01@demo.test")
    with operator_conn() as conn:
        conn.execute(text("UPDATE app_user SET is_demo = true"))
    assert "Demo Co" in client.get("/login").text
    from worklist.config import get_settings

    monkeypatch.setattr(get_settings(), "demo_mode", False)
    assert "Demo Co" not in client.get("/login").text
