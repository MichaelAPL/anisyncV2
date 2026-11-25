"""HTTP clients for outgoing requests. Every request says who it is: some providers sit behind bot protection that
answers 403 to the default library User-Agent (a login endpoint of one of them does) while letting a named
client through."""

import httpx

USER_AGENT = "AniSyncHub/0.1 (+https://github.com/MichaelAPL/anisyncV2)"


def client(**kwargs) -> httpx.AsyncClient:
    headers = {"User-Agent": USER_AGENT} | dict(kwargs.pop("headers", {}) or {})
    # Big list pages can take a while to build on the provider's side: wait longer for an answer than for a connection.
    kwargs.setdefault("timeout", httpx.Timeout(30, read=90))
    return httpx.AsyncClient(headers=headers, **kwargs)
