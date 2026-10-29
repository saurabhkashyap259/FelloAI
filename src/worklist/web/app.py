"""FastAPI application factory."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from worklist.web.deps import NotAuthenticated, NotFound, audit
from worklist.web.render import render

NOT_FOUND_JSON = {"detail": "Not found"}


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith("/api/")


def create_app() -> FastAPI:
    app = FastAPI(title="Renewal Risk Worklist", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    @app.exception_handler(NotAuthenticated)
    async def _not_authenticated(request: Request, exc: NotAuthenticated):
        audit(request, None, None, None, "denied_no_session")
        if _wants_json(request):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound):
        return _not_found_response(request)

    @app.exception_handler(404)
    async def _plain_404(request: Request, exc):
        return _not_found_response(request)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException):
        if exc.status_code == 404:
            return _not_found_response(request)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    from worklist.web.routes import accounts, alerts, auth, drafts, health, model, runs, worklist

    for module in (auth, health, worklist, accounts, alerts, drafts, model, runs):
        app.include_router(module.router)
    return app


def _not_found_response(request: Request):
    """Byte-identical for missing and forbidden resources, so existence can't be probed."""
    if _wants_json(request):
        return JSONResponse(NOT_FOUND_JSON, status_code=404)
    request.state.user = None  # the 404 page must not vary by who asked
    return render(request, "404.html", status_code=404)


app = create_app()


def route_table(application: FastAPI | None = None) -> list[tuple[str, str]]:
    """Every (METHOD, path) the app serves, used by the route-inventory meta-test (SC-008)."""
    from fastapi.routing import APIRoute

    found: list[tuple[str, str]] = []

    def walk(routes) -> None:
        for route in routes:
            if isinstance(route, APIRoute):
                found.extend((m, route.path) for m in sorted(route.methods))
            elif hasattr(route, "original_router"):
                walk(route.original_router.routes)

    walk((application or app).routes)
    return sorted(set(found))
