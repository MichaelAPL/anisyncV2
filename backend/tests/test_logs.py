"""Credentials must not reach the logs."""

import io
import logging

import pytest

from anisync import logs, sync
from anisync.providers.base import ListEntry, Status


@pytest.mark.parametrize(
    ("raw", "gone"),
    [
        ("GET /auth/mal/callback?code=SECRETCODE&state=STATE123 HTTP/1.1", ["SECRETCODE", "STATE123"]),
        ("https://x.example/cb?a=1&access_token=abc123xyz&b=2", ["abc123xyz"]),
        ("failed: refresh_token=r-123456", ["r-123456"]),
        ("password=hunter2 for user", ["hunter2"]),
        ('{"error": "bad", "access_token": "tok-999", "refresh_token": "ref-888"}', ["tok-999", "ref-888"]),
        ("Authorization: Bearer abcdefghij123456", ["abcdefghij123456"]),
        ("stored gAAAAABq_hckTME_6ZlPsuxJ48xNjnS04zM4tjfZrjHZoXxRkxI4VVAg", ["gAAAAABq_hckTME"]),
        ("https://cdn.example/f?X-Amz=1&sig=ABCDEF&jwt=eyJhbGciOi", ["ABCDEF", "eyJhbGciOi"]),
    ],
)
def test_redact(raw, gone):
    out = logs.redact(raw)
    assert all(secret not in out for secret in gone), out
    assert "[redacted]" in out


@pytest.mark.parametrize(
    "harmless", ["GET /api/me HTTP/1.1", "barcode=12345 decoded", "status=running", "user 42 signed in"]
)
def test_ordinary_text_is_untouched(harmless):
    assert logs.redact(harmless) == harmless


def capture(logger_name: str):
    logs.install()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(logs._FILTER)
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger, handler, stream


def test_uvicorn_access_records_keep_their_structure_and_lose_secrets():
    logger, handler, stream = capture("uvicorn.access")
    try:
        # Same shape uvicorn logs: (client, method, path, http version, status)
        logger.info(
            '%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET", "/auth/mal/callback?code=SECRETCODE&state=S1", "1.1", 307
        )
    finally:
        logger.removeHandler(handler)
    text = stream.getvalue()
    assert "SECRETCODE" not in text and "S1" not in text.replace("S1.", "")
    assert text.startswith('1.2.3.4:5 - "GET /auth/mal/callback?code=[redacted]') and text.strip().endswith("307")


def test_exception_messages_and_tracebacks_are_redacted():
    logger, handler, stream = capture("anisync.test")
    try:
        try:
            raise RuntimeError("401 for https://api.example/v1?access_token=LEAKEDTOKEN")
        except RuntimeError as exc:
            logger.warning("provider call failed: %s", exc)
            logger.exception("again")
    finally:
        logger.removeHandler(handler)
    assert "LEAKEDTOKEN" not in stream.getvalue() and "Traceback" in stream.getvalue()


def test_install_is_idempotent():
    logs.install()
    logs.install()
    assert logging.getLogger("uvicorn.access").filters.count(logs._FILTER) == 1


async def test_errors_stored_in_a_sync_summary_are_redacted(session, fakes):
    from anisync.providers import get_provider
    from tests.test_sync import make_user

    user = await make_user(session)
    mirror = user.mirrors[0]
    client = get_provider(mirror.provider).client("t", "u")

    async def save(entry):
        raise RuntimeError("403 https://api.example/save?access_token=SECRET9&x=1")

    client.save_entry = save
    entry = ListEntry(media_id="a1", status=Status.WATCHING, external_ids={"fa": "a1", "fb": "b1"})
    plan = sync.plan_sync([entry], [], {"a1": "b1"}, 1)
    errors = await sync.apply_plan(session, mirror, client, plan)
    assert len(errors) == 1 and "SECRET9" not in errors[0] and "[redacted]" in errors[0]
