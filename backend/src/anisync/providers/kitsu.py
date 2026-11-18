"""Kitsu JSON:API. https://kitsu.docs.apiary.io

Kitsu only supports the OAuth2 *password* grant: the user enters their Kitsu
credentials once, we exchange them for tokens and never store the password.
Kitsu moved from kitsu.io to kitsu.app in 2024.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

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

API = "https://kitsu.app/api/edge"
TOKEN_URL = "https://kitsu.app/api/oauth/token"
JSONAPI = "application/vnd.api+json"

TO_STATUS = {
    "current": Status.WATCHING,
    "completed": Status.COMPLETED,
    "on_hold": Status.ON_HOLD,
    "dropped": Status.DROPPED,
    "planned": Status.PLANNING,
}
FROM_STATUS = {v: k for k, v in TO_STATUS.items()}
# Kitsu's `mappings` use its own site names for other providers.
SITE_TO_PROVIDER = {"myanimelist/anime": "mal", "anilist/anime": "anilist"}
PROVIDER_TO_SITE = {v: k for k, v in SITE_TO_PROVIDER.items()}


def _token_set(data: dict) -> TokenSet:
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=datetime.now(UTC) + timedelta(seconds=data["expires_in"]) if data.get("expires_in") else None,
    )


async def _token_request(form: dict[str, str]) -> TokenSet:
    s = credentials("kitsu")
    if s.client_id:
        form |= {"client_id": s.client_id, "client_secret": s.client_secret}
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(TOKEN_URL, data=form)
        resp.raise_for_status()
        return _token_set(resp.json())


async def password_login(username: str, password: str) -> TokenSet:
    return await _token_request({"grant_type": "password", "username": username, "password": password})


async def refresh(refresh_token: str) -> TokenSet:
    return await _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class KitsuClient(ProviderClient):
    provider_id = "kitsu"

    async def _jsonapi(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Accept": JSONAPI, "Content-Type": JSONAPI}
        resp = await self._request(method, url, headers=headers, **kwargs)
        return resp.json() if resp.content else {}

    async def whoami(self) -> RemoteUser:
        data = await self._jsonapi("GET", f"{API}/users", params={"filter[self]": "true"})
        user = data["data"][0]
        return RemoteUser(id=user["id"], username=user["attributes"]["name"])

    async def fetch_list(self) -> list[ListEntry]:
        entries: list[ListEntry] = []
        url: str | None = f"{API}/library-entries"
        params: dict | None = {
            "filter[userId]": self.remote_user_id,
            "filter[kind]": "anime",
            "include": "anime,anime.mappings",
            "fields[anime]": "canonicalTitle,episodeCount,posterImage,mappings",
            "fields[mappings]": "externalSite,externalId",
            "page[limit]": 500,
        }
        while url:
            data = await self._jsonapi("GET", url, params=params)
            included = {(i["type"], i["id"]): i for i in data.get("included", [])}
            for item in data["data"]:
                attrs = item["attributes"]
                anime_ref = item["relationships"]["anime"]["data"]
                if anime_ref is None:
                    continue
                anime = included.get(("anime", anime_ref["id"]), {"attributes": {}, "relationships": {}})
                ids = {"kitsu": anime_ref["id"]} | self._mapping_ids(anime, included)
                rating = attrs.get("ratingTwenty")
                entries.append(
                    ListEntry(
                        media_id=anime_ref["id"],
                        external_ids=ids,
                        remote_entry_id=item["id"],
                        title=anime["attributes"].get("canonicalTitle", ""),
                        total_episodes=anime["attributes"].get("episodeCount"),
                        image_url=(anime["attributes"].get("posterImage") or {}).get("small"),
                        status=TO_STATUS[attrs["status"]],
                        progress=attrs.get("progress") or 0,
                        score=rating * 5 if rating else None,
                        rewatching=bool(attrs.get("reconsuming")),
                        started_at=(dt.date() if (dt := _parse_dt(attrs.get("startedAt"))) else None),
                        finished_at=(dt.date() if (dt := _parse_dt(attrs.get("finishedAt"))) else None),
                        updated_at=_parse_dt(attrs.get("updatedAt")),
                    )
                )
            url = data.get("links", {}).get("next")
            params = None
        return entries

    @staticmethod
    def _mapping_ids(anime: dict, included: dict) -> dict[str, str]:
        """{provider id: title id} from the anime's `mappings` (only providers we know)."""
        ids: dict[str, str] = {}
        refs = ((anime.get("relationships") or {}).get("mappings") or {}).get("data") or []
        for ref in refs:
            m = included.get(("mappings", ref["id"]))
            if m and m["attributes"]["externalId"].isdigit() and m["attributes"]["externalSite"] in SITE_TO_PROVIDER:
                ids[SITE_TO_PROVIDER[m["attributes"]["externalSite"]]] = m["attributes"]["externalId"]
        return ids

    async def _find_entry_id(self, media_id: str) -> str | None:
        data = await self._jsonapi(
            "GET",
            f"{API}/library-entries",
            params={
                "filter[userId]": self.remote_user_id,
                "filter[animeId]": media_id,
                "fields[libraryEntries]": "id",
            },
        )
        return data["data"][0]["id"] if data["data"] else None

    async def save_entry(self, entry: ListEntry) -> None:
        score = quantize_score(entry.score, self.score_step)
        attributes: dict[str, Any] = {
            "status": FROM_STATUS[entry.status],
            "progress": entry.progress,
            "ratingTwenty": score // 5 if score else None,
            "reconsuming": entry.rewatching,
        }
        if entry.started_at:
            attributes["startedAt"] = entry.started_at.isoformat()
        if entry.finished_at:
            attributes["finishedAt"] = entry.finished_at.isoformat()

        entry_id = entry.remote_entry_id or await self._find_entry_id(entry.media_id)
        if entry_id:
            body = {"data": {"type": "libraryEntries", "id": entry_id, "attributes": attributes}}
            await self._jsonapi("PATCH", f"{API}/library-entries/{entry_id}", json=body)
        else:
            body = {
                "data": {
                    "type": "libraryEntries",
                    "attributes": attributes,
                    "relationships": {
                        "user": {"data": {"type": "users", "id": self.remote_user_id}},
                        "anime": {"data": {"type": "anime", "id": entry.media_id}},
                    },
                }
            }
            await self._jsonapi("POST", f"{API}/library-entries", json=body)

    async def delete_entry(self, media_id: str, remote_entry_id: str | None = None) -> None:
        entry_id = remote_entry_id or await self._find_entry_id(media_id)
        if entry_id:
            await self._jsonapi("DELETE", f"{API}/library-entries/{entry_id}")

    async def lookup_external_ids(self, provider_id: str, ids: list[str]) -> list[dict[str, str]]:
        """Kitsu anime for MAL or AniList IDs, via Kitsu's /mappings (one request per ID)."""
        if provider_id not in PROVIDER_TO_SITE:
            raise NotImplementedError(f"kitsu cannot look up IDs from {provider_id}")
        found: list[dict[str, str]] = []
        for external_id in ids:
            data = await self._jsonapi(
                "GET",
                f"{API}/mappings",
                params={
                    "filter[externalSite]": PROVIDER_TO_SITE[provider_id],
                    "filter[externalId]": external_id,
                    "include": "item",
                },
            )
            for m in data.get("data", []):
                item = (m.get("relationships", {}).get("item") or {}).get("data")
                if item and item["type"] == "anime":
                    found.append({provider_id: external_id, "kitsu": item["id"]})
                    break
        return found


PROVIDER = register(
    Provider(
        id="kitsu",
        name="Kitsu",
        monogram="K",
        colors=ProviderColors(bg="#FDE6E2", fg="#B42318", dark_bg="#5A1A12", dark_fg="#FFB4A8"),
        auth_kind=AuthKind.PASSWORD,
        score_step=5,
        rate_limit=(2, 1),
        client=KitsuClient,
        url_pattern=re.compile(r"^https?://kitsu\.(?:app|io)/anime/(\d+)"),
        lookup_from=frozenset({"mal", "anilist"}),
        password_login=password_login,
        refresh=refresh,
    )
)
