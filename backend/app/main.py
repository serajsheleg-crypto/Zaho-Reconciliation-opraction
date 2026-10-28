"""FastAPI application: API routers + the built React app, served from one port."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, db, jobs, logs
from .config import ROOT, settings
from .routers import admin, auth, companies, datasets, exceptions, recon_templates, runs, settings as settings_router
from .security import seed_admin

logs.setup()
log = logging.getLogger("zaho")
DIST = ROOT / "frontend" / "dist"


def _prepare_database() -> None:
    version = db.init_schema()
    seed_admin()
    jobs.recover_interrupted()
    log.info("Zaho Reconcile %s ready: schema v%s, data in %s, upload limit %s MB",
             __version__, version, settings.data_dir, settings.max_upload_mb)


def _wait_for_database() -> None:
    """Started without MySQL: keep the web app up (it shows a banner) and finish setup once MySQL starts."""
    while True:
        time.sleep(5)
        try:
            _prepare_database()
            return
        except DBAPIError as exc:
            if not db.is_down(exc):
                log.exception("Database setup failed")
                return


@asynccontextmanager
async def lifespan(app: FastAPI):
    if jobs.pool._shutdown:
        jobs.restart_pool()
    try:
        _prepare_database()
    except DBAPIError as exc:
        if not db.is_down(exc):
            raise
        log.error("MySQL is not running. Start MySQL in the XAMPP Control Panel; the app connects by itself.")
        threading.Thread(target=_wait_for_database, name="wait-for-mysql", daemon=True).start()
    yield
    jobs.pool.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Zaho Reconcile", version=__version__, lifespan=lifespan, docs_url="/api/docs",
              redoc_url=None, openapi_url="/api/openapi.json")

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def guard(request: Request, call_next):
    # Cross-site forms cannot add custom headers, so requiring one on every write blocks CSRF
    # without tokens (the session cookie is also SameSite=Lax).
    if request.method in MUTATING and request.url.path.startswith("/api/") \
            and request.headers.get("x-requested-with") != "zaho":
        return JSONResponse({"detail": {"code": "bad_request", "message": "Missing request header."}}, status_code=400)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if not request.url.path.startswith("/api/docs"):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'",
        )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:])
    return JSONResponse(status_code=422, content={"detail": {
        "code": "invalid_input", "message": f"Invalid value for {field or 'request'}: {first.get('msg', '')}",
        "field": field}})


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": {"code": "server_error",
                                                             "message": "Something went wrong on the server."}})


@app.exception_handler(DBAPIError)
async def database_error(request: Request, exc: DBAPIError):
    if not db.is_down(exc):
        return await unexpected(request, exc)
    # The UI shows a "database stopped" banner for this code and checks /api/ping until MySQL is back.
    log.error("MySQL is not reachable (%s %s): %s", request.method, request.url.path, str(exc.orig)[:200])
    return JSONResponse(status_code=503, content={"detail": {
        "code": "db_unavailable",
        "message": "The database (MySQL) is not running. Start MySQL in the XAMPP Control Panel, then try again."}})


for r in (auth.router, datasets.router, runs.router, exceptions.router, settings_router.router, companies.router,
          recon_templates.router, admin.router):
    app.include_router(r)


if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": {"code": "not_found", "message": "Not found."}})
    target = (DIST / path).resolve()
    if path and target.is_file() and DIST.resolve() in target.parents:
        return FileResponse(target)
    index = DIST / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "Frontend not built. Run: cd frontend && npm install && npm run build"},
                        status_code=503)


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level="info",
                timeout_keep_alive=30, limit_concurrency=200)


if __name__ == "__main__":
    run()
