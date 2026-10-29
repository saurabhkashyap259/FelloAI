"""Sign-in and sign-out (FR-027a, FR-027b)."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from worklist.config import get_settings
from worklist.db.engine import web_engine
from worklist.web.auth import COOKIE_NAME, authenticate, create_session, delete_session, login_limiter, sign
from worklist.web.deps import CurrentUser, csrf_protect, current_user
from worklist.web.render import render

router = APIRouter()
GENERIC_ERROR = "Email or password is incorrect."


def _demo_accounts() -> list[dict]:
    if not get_settings().demo_mode:
        return []
    with web_engine().connect() as conn:
        return [dict(r) for r in conn.execute(text("SELECT * FROM demo_accounts()")).mappings()]


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html", demo_accounts=_demo_accounts(),
                  demo_password=get_settings().demo_password if get_settings().demo_mode else None)


@router.post("/login")
def login(request: Request, email: str = Form(""), password: str = Form("")):
    ip = request.client.host if request.client else "unknown"
    if not login_limiter.hit(f"ip:{ip}", f"email:{email.strip().lower()}"):
        return render(request, "login.html", status_code=429, error="Too many attempts. Try again in a minute.",
                      demo_accounts=_demo_accounts(), demo_password=None)
    with web_engine().begin() as conn:
        user = authenticate(conn, email, password)
        if user is None:
            return render(request, "login.html", status_code=401, error=GENERIC_ERROR,
                          demo_accounts=_demo_accounts(),
                          demo_password=get_settings().demo_password if get_settings().demo_mode else None)
        session_id, _csrf = create_session(conn, user)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(COOKIE_NAME, sign(session_id), httponly=True, samesite="lax",
                        secure=get_settings().cookie_secure, max_age=get_settings().session_hours * 3600)
    return response


@router.post("/logout", dependencies=[Depends(csrf_protect)])
def logout(user: CurrentUser = Depends(current_user)):
    with web_engine().begin() as conn:
        delete_session(conn, user.session_id)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response
