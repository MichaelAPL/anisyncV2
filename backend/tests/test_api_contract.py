"""Rules every route must follow. These walk the router, so a new endpoint can't skip them."""

import inspect
import re
from collections.abc import Iterator

from fastapi.responses import RedirectResponse
from fastapi.routing import APIRoute
from starlette.routing import Route

from anisync.api.deps import require_user
from anisync.main import app

# Routes that deliberately work without being signed in (everything else must depend on `require_user`).
PUBLIC = {
    ("GET", "/api/health"),
    ("GET", "/auth/{provider_id}/login"),
    ("GET", "/auth/{provider_id}/callback"),
    ("POST", "/auth/logout"),
    ("GET", "/api/providers"),
    ("GET", "/api/site"),
}


def all_api_routes() -> list:
    """Every API route with its full path. Newer FastAPI keeps included routers as wrappers, so look inside them."""
    found = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            found.append(route)
        elif hasattr(route, "effective_route_contexts"):
            found.extend(route.effective_route_contexts())
    return found


API_ROUTES = all_api_routes()


def test_the_walk_really_finds_the_routes():
    """Guards the other tests in this file against passing because they looked at nothing."""
    paths = {r.path for r in API_ROUTES}
    assert len(API_ROUTES) >= 18 and {"/api/me", "/api/list", "/auth/logout-all", "/api/sessions/{session_id}"} <= paths


def dependency_calls(dependant) -> Iterator:
    yield dependant.call
    for sub in dependant.dependencies:
        yield from dependency_calls(sub)


def test_there_are_no_unexpected_routes():
    stray = [r.path for r in API_ROUTES if not r.path.startswith(("/api/", "/auth/"))]
    docs = [r.path for r in app.routes if isinstance(r, Route) and not isinstance(r, APIRoute)]
    assert not stray, f"routes outside /api and /auth need a deliberate decision: {stray}"
    assert set(docs) <= {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def test_every_route_requires_a_user_unless_it_is_deliberately_public():
    open_routes = [
        (method, route.path)
        for route in API_ROUTES
        for method in route.methods
        if (method, route.path) not in PUBLIC and require_user not in set(dependency_calls(route.dependant))
    ]
    assert not open_routes, f"these routes don't require a signed-in user: {open_routes}"


def test_the_public_list_has_no_stale_entries():
    existing = {(m, r.path) for r in API_ROUTES for m in r.methods}
    assert PUBLIC <= existing


def test_every_route_declares_what_it_returns():
    undeclared = [
        r.path
        for r in API_ROUTES
        if r.response_model is None
        and not (inspect.isclass(r.response_class) and issubclass(r.response_class, RedirectResponse))
        # A spreadsheet download is allowed to have no JSON schema: its rows come from a schema-checked model.
        and getattr(r.response_class, "media_type", None) != "text/csv"
    ]
    assert not undeclared, f"routes without a response schema: {undeclared}"


def test_no_response_schema_exposes_tokens_secrets_or_hashes():
    spec = app.openapi()
    schemas = spec["components"]["schemas"]

    def refs(node) -> Iterator[str]:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref":
                    yield value.rsplit("/", 1)[-1]
                else:
                    yield from refs(value)
        elif isinstance(node, list):
            for item in node:
                yield from refs(item)

    pending = {
        name for path in spec["paths"].values() for op in path.values() for name in refs(op.get("responses", {}))
    }
    seen: set[str] = set()
    fields: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        schema = schemas[name]
        fields |= set(schema.get("properties", {}))
        pending |= set(refs(schema)) - seen
    assert seen, "found no response schemas to check"
    assert not {f for f in fields if re.search(r"token|secret|password|hash|cookie", f, re.IGNORECASE)}, fields
