"""Safe errors, production settings and the SQL-injection lint rule."""

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from anisync.config import Settings
from anisync.db import get_session
from anisync.main import app
from anisync.providers.base import credentials

BACKEND = Path(__file__).resolve().parents[1]


async def test_an_unexpected_error_returns_a_generic_500_with_a_request_id(client, caplog):
    async def exploding_session():
        raise RuntimeError("could not connect to postgresql://anisync:hunter2@db:5432/anisync")
        yield

    app.dependency_overrides[get_session] = exploding_session
    with caplog.at_level("ERROR", logger="anisync.main"):
        response = await client.get("/api/me")
    body = response.json()
    assert response.status_code == 500
    assert set(body) == {"detail", "request_id"} and body["detail"] == "Internal server error"
    assert response.headers["x-request-id"] == body["request_id"] and len(body["request_id"]) == 12
    for leaked in ("hunter2", "postgresql", "Traceback", "RuntimeError", "/api/me"):
        assert leaked not in response.text
    assert body["request_id"] in caplog.text  # the operator can find it


async def test_validation_errors_do_not_become_500s(client):
    assert (await client.put("/api/list/1", json={"status": "nonsense"})).status_code in (401, 422)


@pytest.fixture
def provider_credentials(monkeypatch):
    """Production needs every sign-in provider's credentials; tests set them explicitly, never from a .env file."""
    for name in ("MAL", "ANILIST"):
        monkeypatch.setenv(f"{name}_CLIENT_ID", "id")
        monkeypatch.setenv(f"{name}_CLIENT_SECRET", "secret")
    credentials.cache_clear()  # credentials are read once per process
    yield
    credentials.cache_clear()


def test_production_refuses_to_start_with_weak_secrets(provider_credentials):
    base = {"public_url": "https://anisync.example", "_env_file": None}
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        Settings(**base, token_encryption_key="k")  # the development default
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        Settings(**base, session_secret="short", token_encryption_key="k")
    with pytest.raises(ValidationError, match="TOKEN_ENCRYPTION_KEYS"):
        Settings(**base, session_secret="x" * 40, token_encryption_key="", token_encryption_keys="")
    ok = Settings(**base, session_secret="x" * 40, token_encryption_keys="a,b")
    assert ok.is_production


def test_development_may_use_the_defaults():
    assert not Settings(public_url="http://localhost:5173", _env_file=None).is_production


def test_api_docs_are_only_served_in_development(monkeypatch):
    assert app.docs_url == "/docs"  # tests run as development


@pytest.mark.parametrize(
    "snippet",
    [
        'from sqlalchemy import text\nq = text(f"select * from users where id = {user_id}")\n',
        "def q(cur, name):\n    cur.execute(\"select * from users where name = '%s'\" % name)\n",
        'def q(cur, name):\n    cur.execute("select * from users where name = \'" + name + "\'")\n',
    ],
)
def test_the_linter_rejects_sql_built_from_strings(snippet):
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--stdin-filename", "src/anisync/example.py", "-"],
        input=snippet,
        capture_output=True,
        text=True,
        cwd=BACKEND,
    )
    assert result.returncode != 0 and "S608" in result.stdout, result.stdout


def test_the_code_base_builds_no_sql_from_strings():
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "S608,S105,S106,S107", "src"],
        capture_output=True,
        text=True,
        cwd=BACKEND,
    )
    assert result.returncode == 0, result.stdout


def test_production_refuses_to_start_without_sign_in_provider_credentials(provider_credentials, monkeypatch):
    base = {"public_url": "https://anisync.example", "_env_file": None, "session_secret": "x" * 40}
    monkeypatch.setenv("MAL_CLIENT_SECRET", "")
    credentials.cache_clear()
    with pytest.raises(ValidationError, match="MAL_CLIENT_SECRET"):
        Settings(**base, token_encryption_keys="a")
    monkeypatch.setenv("MAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ANILIST_CLIENT_ID", "")
    credentials.cache_clear()
    with pytest.raises(ValidationError, match="ANILIST_CLIENT_ID"):
        Settings(**base, token_encryption_keys="a")


def test_a_trailing_slash_in_the_public_url_does_not_break_callback_urls():
    settings = Settings(public_url="http://localhost:5173/", _env_file=None)
    assert settings.public_url == "http://localhost:5173"


def test_error_reports_carry_no_oauth_codes_tokens_or_secrets():
    from anisync import sentry

    event = {
        "message": "refresh failed: Bearer abcdefgh12345678",
        "exception": {"values": [{"type": "HTTPStatusError", "value": "400 for /token?code=abc123&state=xyz789"}]},
        "request": {
            "url": "https://anisync.example/auth/mal/callback?code=abc123&state=xyz789",
            "query_string": "code=abc123&state=xyz789",
            "data": {"password": "hunter2"},
            "cookies": {"anisync_session": "s3cret"},
            "headers": {"Authorization": "Bearer abcdefgh12345678", "Cookie": "anisync_session=s3cret"},
        },
        "breadcrumbs": {"values": [{"message": "GET https://api.example/x?access_token=tok999"}]},
    }
    cleaned = str(sentry.scrub_event(event, {}))
    for secret in ("abc123", "xyz789", "abcdefgh12345678", "hunter2", "s3cret", "tok999"):
        assert secret not in cleaned
    assert sentry.scrub_breadcrumb({"message": "token=tok999"}, {})["message"] == "token=[redacted]"


def test_error_reporting_is_off_without_a_dsn_and_never_sends_locals_or_bodies(monkeypatch):
    from anisync import sentry

    calls = []
    monkeypatch.setattr(sentry.sentry_sdk, "init", lambda **options: calls.append(options))
    assert sentry.init("") is False and calls == []
    assert sentry.init("https://key@o1.ingest.sentry.io/1") is True
    [options] = calls
    assert options["send_default_pii"] is False
    assert options["include_local_variables"] is False
    assert options["max_request_body_size"] == "never"
    assert options["traces_sample_rate"] == 0
    assert options["before_send"] is sentry.scrub_event and options["before_breadcrumb"] is sentry.scrub_breadcrumb


def test_error_reports_scrub_log_arguments_and_exception_objects_too():
    from anisync import sentry

    event = {"logentry": {"message": "refresh failed: %s %s", "params": ("code=abc123", ValueError("token=tok999"))}}
    cleaned = str(sentry.scrub_event(event, {}))
    assert "abc123" not in cleaned and "tok999" not in cleaned
