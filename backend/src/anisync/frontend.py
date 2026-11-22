"""Serve the built frontend (frontend/dist) from the same origin as the API.

Same origin matters: the session cookie (SameSite=Lax) and the CSRF origin check both assume the browser talks to one
address. Hashed assets are served by StaticFiles (which refuses paths outside its directory) and cached for good;
every other GET outside /api and /auth gets index.html, so client-side routes work on reload. No file path is ever
built from the request: the few top-level files (favicon and the like) are listed once at startup.
"""

from pathlib import Path

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_RESERVED = ("api", "auth")  # whole first path segments the frontend never answers
_FOREVER = "public, max-age=31536000, immutable"


class _ImmutableAssets(StaticFiles):
    """Vite puts a content hash in every asset name, so a cached copy never goes stale."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = _FOREVER
        return response


def mount_frontend(app: FastAPI, dist: Path) -> bool:
    """Add the frontend routes last, after every API route. Returns False (and adds nothing) without a build."""
    index = dist / "index.html"
    if not index.is_file():
        return False
    top_level = {f.name: f for f in dist.iterdir() if f.is_file() and f.name != "index.html"}

    async def frontend(request: Request) -> Response:
        path = request.path_params["path"]
        if path.split("/", 1)[0] in _RESERVED:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if path in top_level:
            return FileResponse(top_level[path])
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    if (dist / "assets").is_dir():
        app.mount("/assets", _ImmutableAssets(directory=dist / "assets"), name="assets")
    app.router.routes.append(Route("/{path:path}", frontend, methods=["GET", "HEAD"]))
    return True
