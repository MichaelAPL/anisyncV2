"""MyAnimeList API v2. https://myanimelist.net/apiconfig/references/api/v2

OAuth2 authorization code + PKCE (MAL only supports the `plain` challenge method).
Access tokens last ~31 days and can be refreshed.
"""

import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode

from anisync.http import client as http_client
from anisync.providers.base import (
    AuthKind,
    ListEntry,
    Provider,
    ProviderClient,
    ProviderColors,
    RemoteUser,
    Status,
    TokenSet,
    credentials,
    quantize_score,
)
from anisync.providers.registry import register

API = "https://api.myanimelist.net/v2"
AUTHORIZE_URL = "https://myanimelist.net/v1/oauth2/authorize"
TOKEN_URL = "https://myanimelist.net/v1/oauth2/token"  # noqa: S105 (a URL, not a secret)

TO_STATUS = {
    "watching": Status.WATCHING,
    "completed": Status.COMPLETED,
    "on_hold": Status.ON_HOLD,
    "dropped": Status.DROPPED,
    "plan_to_watch": Status.PLANNING,
}
FROM_STATUS = {v: k for k, v in TO_STATUS.items()}


def _parse_date(value: str | None) -> date | None:
    # MAL allows partial dates ("2021", "2021-04"); only full dates are synced.
    try:
        return date.fromisoformat(value) if value and len(value) == 10 else None
    except ValueError:
        return None


def authorize_url(state: str, code_verifier: str | None, redirect_uri: str) -> str:
    params = {
        "response_type": "code",
        "client_id": credentials("mal").client_id,
        "state": state,
        "redirect_uri": redirect_uri,
        "code_challenge": code_verifier,
        "code_challenge_method": "plain",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _token_set(data: dict) -> TokenSet:
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=datetime.now(UTC) + timedelta(seconds=data["expires_in"]),
    )


async def exchange_code(code: str, code_verifier: str | None, redirect_uri: str) -> TokenSet:
    s = credentials("mal")
    async with http_client() as http:
        resp = await http.post(
            TOKEN_URL,
            data={
                "client_id": s.client_id,
                "client_secret": s.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
            },
        )
        resp.raise_for_status()
        return _token_set(resp.json())


async def refresh(refresh_token: str) -> TokenSet:
    s = credentials("mal")
    async with http_client() as http:
        resp = await http.post(
            TOKEN_URL,
            data={
                "client_id": s.client_id,
                "client_secret": s.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        return _token_set(resp.json())


class MalClient(ProviderClient):
    provider_id = "mal"

    async def whoami(self) -> RemoteUser:
        data = (await self._request("GET", f"{API}/users/@me")).json()
        return RemoteUser(id=str(data["id"]), username=data["name"], avatar_url=data.get("picture"))

    async def fetch_list(self, since: datetime | None = None) -> list[ListEntry]:
        entries: list[ListEntry] = []
        url: str | None = f"{API}/users/@me/animelist"
        params: dict | None = {"fields": "list_status,num_episodes,nsfw", "limit": 1000, "nsfw": "true"}
        if since is not None:  # newest first, small pages: a list with nothing new costs one tiny request
            params |= {"sort": "list_updated_at", "limit": 100}
        while url:
            data = (await self._request("GET", url, params=params)).json()
            for item in data["data"]:
                node, ls = item["node"], item["list_status"]
                updated = datetime.fromisoformat(ls["updated_at"]) if ls.get("updated_at") else None
                if since is not None and updated is not None and updated <= since:
                    return entries  # everything after this is older still
                entries.append(
                    ListEntry(
                        media_id=str(node["id"]),
                        external_ids={"mal": str(node["id"])},
                        title=node["title"],
                        total_episodes=node.get("num_episodes") or None,
                        image_url=(node.get("main_picture") or {}).get("medium"),
                        adult=node.get("nsfw") == "black",  # "gray" is borderline (ecchi), not adult
                        status=TO_STATUS[ls["status"]],
                        progress=ls.get("num_episodes_watched", 0),
                        score=(ls.get("score") or 0) * 10 or None,
                        rewatching=ls.get("is_rewatching", False),
                        started_at=_parse_date(ls.get("start_date")),
                        finished_at=_parse_date(ls.get("finish_date")),
                        updated_at=updated,
                    )
                )
            url = data.get("paging", {}).get("next")
            params = None  # `next` already carries the query string
        return entries

    async def save_entry(self, entry: ListEntry) -> None:
        score = quantize_score(entry.score, self.score_step)
        form = {
            "status": FROM_STATUS[entry.status],
            "num_watched_episodes": entry.progress,
            "score": (score or 0) // 10,
            "is_rewatching": "true" if entry.rewatching else "false",
        }
        if entry.started_at:
            form["start_date"] = entry.started_at.isoformat()
        if entry.finished_at:
            form["finish_date"] = entry.finished_at.isoformat()
        await self._request("PATCH", f"{API}/anime/{entry.media_id}/my_list_status", data=form)

    async def delete_entry(self, media_id: str, remote_entry_id: str | None = None) -> None:
        await self._request("DELETE", f"{API}/anime/{media_id}/my_list_status")


PROVIDER = register(
    Provider(
        id="mal",
        name="MyAnimeList",
        monogram="MAL",
        colors=ProviderColors(bg="#E3E9F7", fg="#2E51A2", dark_bg="#1F2F5C", dark_fg="#C9D6FF"),
        auth_kind=AuthKind.OAUTH_PKCE_PLAIN,
        incremental_reads=True,
        image_hosts=("myanimelist.net",),
        score_step=10,
        rate_limit=(1, 1),  # MAL doesn't publish limits; ~1 req/s is safe.
        client=MalClient,
        url_pattern=re.compile(r"^https?://myanimelist\.net/anime/(\d+)"),
        authorize_url=authorize_url,
        exchange_code=exchange_code,
        refresh=refresh,
    )
)
