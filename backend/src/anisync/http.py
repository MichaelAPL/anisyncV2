"""HTTP clients for outgoing requests. Every request says who it is: some providers sit behind bot protection that
answers 403 to the default library User-Agent (a login endpoint of one of them does) while letting a named
client through."""

import httpx

USER_AGENT = "AniSync/0.1"


def client(**kwargs) -> httpx.AsyncClient:
    headers = {"User-Agent": USER_AGENT} | dict(kwargs.pop("headers", {}) or {})
    kwargs.setdefault("timeout", 30)
    return httpx.AsyncClient(headers=headers, **kwargs)
