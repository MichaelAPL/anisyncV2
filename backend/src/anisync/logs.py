"""Keep credentials out of logs.

Log lines and exception messages can contain URLs with OAuth codes, tokens in JSON error bodies, or whole bearer
headers. `redact` masks those patterns, and `RedactingFilter` applies it to everything that is logged (uvicorn's
access log included). The filter changes message text only; it keeps the record's structure, because uvicorn's own
formatter reads the arguments of its access records.
"""

import logging
import re

_NAMES = (
    r"code|state|token|access_token|refresh_token|id_token|password|passwd|secret|client_secret|api_key|apikey|"
    r"sig|signature|jwt|authorization|session|sessionid"
)
_QUERY = re.compile(rf"(?<![\w.-])({_NAMES})=([^&\s\"'#]+)", re.IGNORECASE)
_JSON = re.compile(rf'("(?:{_NAMES})"\s*:\s*")[^"]*(")', re.IGNORECASE)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_FERNET = re.compile(r"gAAAAA[A-Za-z0-9_=-]{20,}")  # our own encrypted tokens


def redact(text: str) -> str:
    text = _QUERY.sub(r"\1=[redacted]", text)
    text = _JSON.sub(r"\1[redacted]\2", text)
    text = _BEARER.sub(r"\1 [redacted]", text)
    return _FERNET.sub("[redacted]", text)


def _clean(value: object) -> object:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, BaseException):  # formatted later with %s: mask its text now
        return redact(str(value))
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_clean(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _clean(v) for k, v in record.args.items()}
        if record.exc_info and record.exc_info[0] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))  # cached by the formatter
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


_FILTER = RedactingFilter()
LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def install() -> None:
    """Attach the filter to the root handlers and to uvicorn's loggers. Safe to call repeatedly."""
    for handler in logging.getLogger().handlers:
        if _FILTER not in handler.filters:
            handler.addFilter(_FILTER)
    for name in LOGGERS:
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)


def configure(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # it logs every request URL
    install()
