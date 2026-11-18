import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI

from anisync import jobs
from anisync.api import auth, routes
from anisync.config import get_settings
from anisync.security import CsrfMiddleware, RequestIdMiddleware

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs can carry credentials
settings = get_settings()


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
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


app = FastAPI(title="AniSync", lifespan=lifespan)
# Added last = outermost: every response (including a CSRF refusal) carries a request ID.
app.add_middleware(CsrfMiddleware, allowed_origin=settings.public_url)
app.add_middleware(RequestIdMiddleware)
app.include_router(auth.router)
app.include_router(routes.router)
