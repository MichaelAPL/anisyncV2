"""Error reporting to Sentry, scrubbed of everything that must never leave the server.

Off unless SENTRY_DSN is set. Request bodies, cookies, headers, local variables and personal data are never sent:
the OAuth callback's locals hold authorization codes and tokens, and provider objects hold client secrets. Sentry's
logging integration reads log records before our redacting log filter runs, so every message, exception value,
breadcrumb and URL is passed through `logs.redact` here as well.
"""

from typing import Any

import sentry_sdk

from anisync.logs import redact

_DROPPED_REQUEST_FIELDS = ("data", "cookies", "headers", "env")


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):  # log arguments arrive as a tuple
        return [_clean(item) for item in value]
    if isinstance(value, BaseException):
        return redact(str(value))
    return value


def scrub_breadcrumb(crumb: dict, _hint: dict) -> dict:
    return _clean(crumb)


def scrub_event(event: dict, _hint: dict) -> dict:
    request = event.get("request")
    if isinstance(request, dict):
        for field in _DROPPED_REQUEST_FIELDS:
            request.pop(field, None)
        url = request.get("url")
        if isinstance(url, str) and "/auth/" in url:
            request["url"] = url.split("?", 1)[0]  # OAuth callbacks carry the code and state in the query
            request.pop("query_string", None)
    return _clean(event)


def init(dsn: str) -> bool:
    """Start error reporting when a DSN is configured. Returns whether it was started."""
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0,
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
    )
    return True
