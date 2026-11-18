import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from anisync import jobs
from anisync.api import auth, routes
from anisync.config import get_settings

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
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=settings.public_url.startswith("https://"),
    max_age=60 * 60 * 24 * 30,
)
app.include_router(auth.router)
app.include_router(routes.router)
