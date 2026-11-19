import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from anisync import jobs, logs, moderation
from anisync.api import admin, auth, routes
from anisync.config import get_settings
from anisync.db import SessionLocal
from anisync.security import CsrfMiddleware, RequestIdMiddleware

logs.configure()
log = logging.getLogger(__name__)
settings = get_settings()


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with SessionLocal() as session:
        promoted = await moderation.bootstrap_admins(session)
    if promoted:
        log.info("promoted %s user(s) to admin from ADMIN_USER_IDS", promoted)
    async with jobs.app.open_async():
        worker = None
        if settings.run_worker_in_process:
            worker = asyncio.create_task(jobs.app.run_worker_async(install_signal_handlers=False))
        try:
            yield
        finally:
            if worker:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker


# No interactive API docs in production.
docs = {} if not settings.is_production else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="AniSync", lifespan=lifespan, **docs)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Never show a stack trace, SQL or settings to the client: a generic message and an ID to find it in the logs."""
    request_id = request.scope.get("state", {}).get("request_id", "-")
    log.error("unhandled error on %s %s [request %s]", request.method, request.url.path, request_id, exc_info=exc)
    return JSONResponse(
        {"detail": "Internal server error", "request_id": request_id},
        status_code=500,
        headers={"X-Request-ID": request_id},
    )


# Added last = outermost: every response (including a CSRF refusal) carries a request ID.
app.add_middleware(CsrfMiddleware, allowed_origin=settings.public_url)
app.add_middleware(RequestIdMiddleware)
app.include_router(auth.router)
app.include_router(routes.router)
app.include_router(admin.router)
