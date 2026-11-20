"""Kitsu JSON:API. https://kitsu.docs.apiary.io

Kitsu only supports the OAuth2 *password* grant: the user enters their Kitsu
credentials once, we exchange them for tokens and never store the password.
Kitsu moved from kitsu.io to kitsu.app in 2024.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from anisync.http import client as http_client
from anisync.providers.base import (
    AuthKind,
    ChangeSkipped,
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
TOKEN_URL = "https://kitsu.app/api/oauth/token"  # noqa: S105 (a URL, not a secret)
JSONAPI = "application/vnd.api+json"
PAGE_SIZE = 100  # about a second per page on a 1000-entry list; Kitsu allows up to 500
MIN_PAGE_SIZE = 25

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
    async with http_client() as http:
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
        avatar = user["attributes"].get("avatar") or {}
        return RemoteUser(
            id=user["id"],
            username=user["attributes"]["name"],
            avatar_url=avatar.get("large") or avatar.get("original"),
            hides_adult=bool(user["attributes"].get("sfwFilter")),
        )

    async def fetch_list(self, since: datetime | None = None) -> list[ListEntry]:
        # No incremental reads for Kitsu (`incremental_reads` is off): `since` is ignored and the whole list is read.
        # Pages are small (a request that takes seconds is a request that rarely times out) and shrink when Kitsu is
        # slow: a page that times out is asked again smaller, from the same place, so no work is lost.
        entries: list[ListEntry] = []
        size, offset = PAGE_SIZE, 0
        base = {
            "filter[userId]": self.remote_user_id,
            "filter[kind]": "anime",
            "include": "anime,anime.mappings",
            "fields[anime]": "canonicalTitle,episodeCount,posterImage,mappings,nsfw",
            "fields[mappings]": "externalSite,externalId",
        }
        while True:
            try:
                data = await self._jsonapi(
                    "GET", f"{API}/library-entries", params=base | {"page[limit]": size, "page[offset]": offset}
                )
            except httpx.TimeoutException:
                if size <= MIN_PAGE_SIZE:
                    raise
                size = max(MIN_PAGE_SIZE, size // 2)
                continue
            included = {(i["type"], i["id"]): i for i in data.get("included", [])}
            for item in data["data"]:
                entries.extend(self._entry(item, included))
            offset += len(data["data"])
            if not data["data"] or not data.get("links", {}).get("next"):
                return entries

    def _entry(self, item: dict, included: dict) -> list[ListEntry]:
        attrs = item["attributes"]
        anime_ref = item["relationships"]["anime"]["data"]
        if anime_ref is None:
            return []
        anime = included.get(("anime", anime_ref["id"]), {"attributes": {}, "relationships": {}})
        ids = {"kitsu": anime_ref["id"]} | self._mapping_ids(anime, included)
        rating = attrs.get("ratingTwenty")
        return [
            ListEntry(
                media_id=anime_ref["id"],
                external_ids=ids,
                remote_entry_id=item["id"],
                title=anime["attributes"].get("canonicalTitle", ""),
                total_episodes=anime["attributes"].get("episodeCount"),
                image_url=(anime["attributes"].get("posterImage") or {}).get("small"),
                adult=bool(anime["attributes"].get("nsfw")),
                status=TO_STATUS[attrs["status"]],
                progress=attrs.get("progress") or 0,
                score=rating * 5 if rating else None,
                rewatching=bool(attrs.get("reconsuming")),
                started_at=(dt.date() if (dt := _parse_dt(attrs.get("startedAt"))) else None),
                finished_at=(dt.date() if (dt := _parse_dt(attrs.get("finishedAt"))) else None),
                updated_at=_parse_dt(attrs.get("updatedAt")),
            )
        ]

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

    async def save_entry(self, entry: ListEntry) -> str | None:
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

        # An entry we know (from the last read or write) is updated directly. A new one is simply created: asking Kitsu
        # first whether it exists would double the requests of every create, and the answer is almost always no.
        return await self._write(entry.remote_entry_id, entry.media_id, attributes)

    async def _write(self, entry_id: str | None, media_id: str, attributes: dict[str, Any]) -> str | None:
        try:
            return await self._send(entry_id, media_id, attributes)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 422:
                raise
            reason = str(exc)
            if "already been taken" in reason and entry_id is None:
                # The title is on the list already (added on Kitsu's site since we last looked, or hidden from us):
                # find it and update it. If Kitsu doesn't show it to us, there is nothing we can update.
                found = await self._find_entry_id(media_id)
                if found is None:
                    # Adult titles are hidden while the account's "SFW filter" is on: we can neither find the entry
                    # to update nor create a second one.
                    raise ChangeSkipped(
                        "Kitsu has this title on your list but hides it from other apps (adult titles are hidden "
                        "while your Kitsu SFW filter is on)"
                    ) from exc
                return await self._write(found, media_id, attributes)
            if "progress" in reason and "exceed" in reason:
                # Kitsu counts fewer episodes for this title than the master list does: cap at Kitsu's count.
                count = await self._episode_count(media_id)
                if count is not None and count < attributes["progress"]:
                    return await self._send(entry_id, media_id, attributes | {"progress": count})
            raise

    async def _send(self, entry_id: str | None, media_id: str, attributes: dict[str, Any]) -> str | None:
        if entry_id:
            body = {"data": {"type": "libraryEntries", "id": entry_id, "attributes": attributes}}
            await self._jsonapi("PATCH", f"{API}/library-entries/{entry_id}", json=body)
            return entry_id
        body = {
            "data": {
                "type": "libraryEntries",
                "attributes": attributes,
                "relationships": {
                    "user": {"data": {"type": "users", "id": self.remote_user_id}},
                    "anime": {"data": {"type": "anime", "id": media_id}},
                },
            }
        }
        created = await self._jsonapi("POST", f"{API}/library-entries", json=body)
        return (created.get("data") or {}).get("id")

    async def _episode_count(self, media_id: str) -> int | None:
        try:
            data = await self._jsonapi("GET", f"{API}/anime/{media_id}", params={"fields[anime]": "episodeCount"})
        except httpx.HTTPStatusError:
            return None
        count = data.get("data", {}).get("attributes", {}).get("episodeCount")
        return int(count) if count else None

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
        image_hosts=("kitsu.app",),
        adult_help="In Kitsu, open Settings → Adult Content and allow adult titles",
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
