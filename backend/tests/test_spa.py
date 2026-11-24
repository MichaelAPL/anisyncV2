"""Serving the built frontend from the same origin as the API, and the headers every response carries.

These tests build a small app around a fake `dist` folder, so they catch route shadowing and path handling; they don't
prove the real image build (the Dockerfile is checked by building and running it).
"""

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from anisync.frontend import mount_frontend
from anisync.main import app as real_app
from anisync.security import SecurityHeadersMiddleware


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>AniSyncHub</title>")
    (tmp_path / "assets" / "app-1a2b3c.js").write_text("console.log('app')")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    (tmp_path.parent / "secret.txt").write_text("outside dist")
    return tmp_path


@pytest.fixture
def client(dist):
    app = FastAPI()
    api = APIRouter(prefix="/api")

    @api.get("/ping")
    async def ping() -> dict:
        return {"ok": True}

    app.include_router(api)
    app.add_middleware(SecurityHeadersMiddleware)
    mount_frontend(app, dist)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_client_side_routes_get_the_app_and_hashed_assets_are_cached_for_good(client):
    page = await client.get("/settings")
    assert page.status_code == 200 and "<title>AniSyncHub</title>" in page.text
    assert page.headers["cache-control"] == "no-cache"
    asset = await client.get("/assets/app-1a2b3c.js")
    assert asset.status_code == 200 and asset.text == "console.log('app')"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert (await client.get("/favicon.svg")).text == "<svg/>"
    assert (await client.get("/")).text.startswith("<!doctype html>")


async def test_api_and_auth_paths_are_never_answered_with_the_app(client):
    missing = await client.get("/api/does-not-exist")
    assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}
    assert (await client.get("/api")).status_code == 404
    assert (await client.get("/auth")).status_code == 404
    assert (await client.get("/auth/mal/whatever")).status_code == 404
    assert (await client.get("/api/ping")).json() == {"ok": True}
    assert "<title>" in (await client.get("/apiary")).text  # only whole path segments count


async def test_only_get_is_answered_by_the_app(client):
    assert (await client.post("/some-page")).status_code == 405


@pytest.mark.parametrize("path", ["/..%2F..%2Fsecret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt"])
async def test_no_path_reaches_a_file_outside_the_build(client, path):
    response = await client.get(path)
    assert "outside dist" not in response.text


async def test_every_response_refuses_framing_and_sniffing(client):
    for path in ("/settings", "/api/ping", "/api/does-not-exist", "/assets/app-1a2b3c.js"):
        headers = (await client.get(path)).headers
        assert headers["x-frame-options"] == "DENY"
        assert headers["content-security-policy"] == "frame-ancestors 'none'"
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "strict-origin-when-cross-origin"


async def test_the_real_app_sends_the_headers_too():
    transport = httpx.ASGITransport(app=real_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        headers = (await c.get("/api/providers")).headers
    assert headers["x-frame-options"] == "DENY" and headers["x-content-type-options"] == "nosniff"


def test_without_a_build_nothing_is_mounted(tmp_path):
    app = FastAPI()
    assert mount_frontend(app, tmp_path / "missing") is False
    assert len(app.routes) == len(FastAPI().routes)
