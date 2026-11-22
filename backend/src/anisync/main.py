import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from anisync import frontend, jobs, logs, metrics, moderation, sentry, sync
from anisync.api import admin, admin_dataset, admin_site, admin_syncs, admin_titles, admin_users, auth, public, routes
from anisync.config import get_settings
from anisync.db import SessionLocal
from anisync.providers import get_provider, is_registered
from anisync.providers.base import RateLimited
from anisync.security import CsrfMiddleware, RequestIdMiddleware, SecurityHeadersMiddleware

logs.configure()
log = logging.getLogger(__name__)
settings = get_settings()
sentry.init(settings.sentry_dsn)  # off unless SENTRY_DSN is set; scrubbed, see sentry.py


# How long a stopping app lets running jobs finish before cancelling them. The container is killed some seconds after
# being asked to stop (`drain_timeout` in config/deploy.yml, which must stay above this); a cancelled sync then still
# has time to close its own run as `error` (see `sync.run_sync`). Without a limit the worker waits forever and the
# run is left "running".
WORKER_SHUTDOWN_GRACE = 20.0


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with SessionLocal() as session:
        promoted = await moderation.bootstrap_admins(session)
        if settings.run_worker_in_process:
            # Only runs that stopped making progress: in a rolling deploy the old container may still be syncing.
            stopped = await sync.close_interrupted_runs(session)
            if stopped:
                log.warning("closed %s sync run(s) interrupted by the last shutdown", stopped)
    if promoted:
        log.info("promoted %s user(s) to admin from ADMIN_USER_IDS", promoted)
    async with jobs.app.open_async():
        worker = None
        if settings.run_worker_in_process:
            worker = asyncio.create_task(
                jobs.app.run_worker_async(
                    install_signal_handlers=False, shutdown_graceful_timeout=WORKER_SHUTDOWN_GRACE
                )
            )
        app.state.worker = worker  # the health check reports the app unhealthy once this task has ended
        try:
            yield
        finally:
            async with SessionLocal() as session:
                with contextlib.suppress(Exception):
                    await metrics.flush(session)
            if worker:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker


# No interactive API docs in production.
docs = {} if not settings.is_production else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="AniSync", lifespan=lifespan, dependencies=[Depends(public.maintenance_guard)], **docs)


@app.exception_handler(RateLimited)
async def provider_busy(request: Request, exc: RateLimited) -> JSONResponse:
    """A list site told us to slow down: say so (and for how long) instead of a generic server error."""
    name = get_provider(exc.provider_id).name if is_registered(exc.provider_id) else "A list site"
    return JSONResponse({"detail": f"{name} is busy right now. Try again in a minute"}, status_code=429)


@app.exception_handler(httpx.HTTPError)
async def provider_unreachable(request: Request, exc: httpx.HTTPError) -> JSONResponse:
    """A list site failed or timed out. Our server is fine, so this is a 502 with a message the user can act on."""
    log.warning("provider request failed on %s %s: %s", request.method, request.url.path, type(exc).__name__)
    return JSONResponse({"detail": "A list site isn't responding right now. Try again in a moment"}, status_code=502)


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
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CsrfMiddleware, allowed_origin=settings.public_url)
app.add_middleware(RequestIdMiddleware)
app.include_router(auth.router)
app.include_router(routes.router)
app.include_router(admin.router)
app.include_router(admin_syncs.router)
app.include_router(admin_users.router)
app.include_router(admin_site.router)
app.include_router(admin_dataset.router)
app.include_router(admin_titles.router)
app.include_router(public.router)

# The built frontend, served from this same origin (production image). Added after every API route.
if settings.frontend_dist:
    frontend.mount_frontend(app, Path(settings.frontend_dist))
