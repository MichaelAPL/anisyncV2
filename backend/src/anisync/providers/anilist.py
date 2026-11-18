"""AniList GraphQL API. https://docs.anilist.co

OAuth2 authorization code grant. Tokens last one year and cannot be refreshed,
so the user has to re-link when it expires. Rate limit: 90 req/min (sometimes degraded to 30).
"""

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

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

GRAPHQL_URL = "https://graphql.anilist.co"
AUTHORIZE_URL = "https://anilist.co/api/v2/oauth/authorize"
TOKEN_URL = "https://anilist.co/api/v2/oauth/token"  # noqa: S105 (a URL, not a secret)

TO_STATUS = {
    "CURRENT": Status.WATCHING,
    "REPEATING": Status.WATCHING,
    "COMPLETED": Status.COMPLETED,
    "PAUSED": Status.ON_HOLD,
    "DROPPED": Status.DROPPED,
    "PLANNING": Status.PLANNING,
}
FROM_STATUS = {
    Status.WATCHING: "CURRENT",
    Status.COMPLETED: "COMPLETED",
    Status.ON_HOLD: "PAUSED",
    Status.DROPPED: "DROPPED",
    Status.PLANNING: "PLANNING",
}

LIST_QUERY = """
query ($userId: Int) {
  MediaListCollection(userId: $userId, type: ANIME) {
    lists {
      entries {
        id mediaId status progress updatedAt
        score(format: POINT_100)
        startedAt { year month day }
        completedAt { year month day }
        media { idMal episodes title { userPreferred } coverImage { medium } }
      }
    }
  }
}
"""

SAVE_MUTATION = """
mutation ($mediaId: Int, $status: MediaListStatus, $scoreRaw: Int, $progress: Int,
          $startedAt: FuzzyDateInput, $completedAt: FuzzyDateInput) {
  SaveMediaListEntry(mediaId: $mediaId, status: $status, scoreRaw: $scoreRaw, progress: $progress,
                     startedAt: $startedAt, completedAt: $completedAt) { id }
}
"""

# Flat list sorted by update time (MediaListCollection is grouped by status, so it can't be read newest-first).
RECENT_QUERY = """
query ($userId: Int, $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    mediaList(userId: $userId, type: ANIME, sort: UPDATED_TIME_DESC) {
      id mediaId status progress updatedAt hiddenFromStatusLists customLists
      score(format: POINT_100)
      startedAt { year month day }
      completedAt { year month day }
      media { idMal episodes title { userPreferred } coverImage { medium } }
    }
  }
}
"""

ENTRY_ID_QUERY = "query ($userId: Int, $mediaId: Int) { MediaList(userId: $userId, mediaId: $mediaId) { id } }"
DELETE_MUTATION = "mutation ($id: Int) { DeleteMediaListEntry(id: $id) { deleted } }"
VIEWER_QUERY = "{ Viewer { id name } }"
# Batch ID lookups (used by the ID resolver). perPage max is 50.
BY_MAL_IDS = "query ($ids: [Int]) { Page(perPage: 50) { media(idMal_in: $ids, type: ANIME) { id idMal } } }"
BY_IDS = "query ($ids: [Int]) { Page(perPage: 50) { media(id_in: $ids, type: ANIME) { id idMal } } }"


def _fuzzy_to_date(fd: dict | None) -> date | None:
    if not fd or not (fd.get("year") and fd.get("month") and fd.get("day")):
        return None
    return date(fd["year"], fd["month"], fd["day"])


def _date_to_fuzzy(d: date | None) -> dict | None:
    return {"year": d.year, "month": d.month, "day": d.day} if d else None


def authorize_url(state: str, code_verifier: str | None, redirect_uri: str) -> str:
    # Plain authorization-code flow: `code_verifier` is unused.
    params = {
        "client_id": credentials("anilist").client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(code: str, code_verifier: str | None, redirect_uri: str) -> TokenSet:
    s = credentials("anilist")
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": s.client_id,
                "client_secret": s.client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=None,
        expires_at=datetime.now(UTC) + timedelta(seconds=data.get("expires_in", 31_536_000)),
    )


class AniListError(Exception):
    pass


class AniListClient(ProviderClient):
    provider_id = "anilist"

    async def gql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
            headers={"Accept": "application/json"},
        )
        body = resp.json()
        if body.get("errors"):
            raise AniListError(body["errors"])
        return body["data"]

    async def whoami(self) -> RemoteUser:
        viewer = (await self.gql(VIEWER_QUERY))["Viewer"]
        return RemoteUser(id=str(viewer["id"]), username=viewer["name"])

    @staticmethod
    def _entry(e: dict[str, Any]) -> ListEntry:
        media = e["media"]
        ids = {"anilist": str(e["mediaId"])}
        if media.get("idMal"):
            ids["mal"] = str(media["idMal"])
        return ListEntry(
            media_id=str(e["mediaId"]),
            external_ids=ids,
            remote_entry_id=str(e["id"]),
            title=media["title"]["userPreferred"],
            total_episodes=media.get("episodes"),
            image_url=(media.get("coverImage") or {}).get("medium"),
            status=TO_STATUS[e["status"]],
            rewatching=e["status"] == "REPEATING",
            progress=e.get("progress") or 0,
            score=e.get("score") or None,
            started_at=_fuzzy_to_date(e.get("startedAt")),
            finished_at=_fuzzy_to_date(e.get("completedAt")),
            updated_at=datetime.fromtimestamp(e["updatedAt"], UTC) if e.get("updatedAt") else None,
        )

    async def fetch_list(self, since: datetime | None = None) -> list[ListEntry]:
        if since is not None:
            return await self._fetch_recent(since)
        data = await self.gql(LIST_QUERY, {"userId": int(self.remote_user_id)})
        seen: dict[int, ListEntry] = {}  # entries can appear in several custom lists
        for lst in data["MediaListCollection"]["lists"]:
            for e in lst["entries"]:
                seen[e["id"]] = self._entry(e)
        return list(seen.values())

    @staticmethod
    def _on_the_list(e: dict[str, Any]) -> bool:
        """False for entries `MediaListCollection` doesn't return: hidden from the status lists and in no custom list.

        The full read uses that collection and the incremental read the flat `mediaList`, which does return them;
        without this check an incremental read would add such an entry and the next full read would remove it again.
        """
        if not e.get("hiddenFromStatusLists"):
            return True
        custom = e.get("customLists") or {}
        return any(custom.values()) if isinstance(custom, dict) else bool(custom)

    async def _fetch_recent(self, since: datetime) -> list[ListEntry]:
        """Entries updated after `since`, newest first, stopping at the first older one."""
        found: dict[int, ListEntry] = {}
        page = 1
        while True:
            data = await self.gql(RECENT_QUERY, {"userId": int(self.remote_user_id), "page": page})
            for e in data["Page"]["mediaList"]:
                if e.get("updatedAt") and datetime.fromtimestamp(e["updatedAt"], UTC) <= since:
                    return list(found.values())
                if self._on_the_list(e):
                    found[e["id"]] = self._entry(e)
            if not data["Page"]["pageInfo"]["hasNextPage"]:
                return list(found.values())
            page += 1

    async def save_entry(self, entry: ListEntry) -> None:
        status = "REPEATING" if entry.rewatching and entry.status == Status.WATCHING else FROM_STATUS[entry.status]
        variables: dict[str, Any] = {
            "mediaId": int(entry.media_id),
            "status": status,
            "progress": entry.progress,
            "scoreRaw": quantize_score(entry.score, self.score_step) or 0,
        }
        # Only send dates we know; never clear dates the user set on the mirror.
        if entry.started_at:
            variables["startedAt"] = _date_to_fuzzy(entry.started_at)
        if entry.finished_at:
            variables["completedAt"] = _date_to_fuzzy(entry.finished_at)
        await self.gql(SAVE_MUTATION, variables)

    async def delete_entry(self, media_id: str, remote_entry_id: str | None = None) -> None:
        if remote_entry_id is None:
            found = await self.gql(ENTRY_ID_QUERY, {"userId": int(self.remote_user_id), "mediaId": int(media_id)})
            if not found.get("MediaList"):
                return
            remote_entry_id = str(found["MediaList"]["id"])
        await self.gql(DELETE_MUTATION, {"id": int(remote_entry_id)})

    async def lookup_external_ids(self, provider_id: str, ids: list[str]) -> list[dict[str, str]]:
        """Titles by MAL ID (`idMal`) or by AniList ID, 50 per request. AniList knows only the MAL cross-reference."""
        if provider_id not in ("mal", "anilist"):
            raise NotImplementedError(f"anilist cannot look up IDs from {provider_id}")
        query = BY_MAL_IDS if provider_id == "mal" else BY_IDS
        found: list[dict[str, str]] = []
        for start in range(0, len(ids), 50):
            data = await self.gql(query, {"ids": [int(i) for i in ids[start : start + 50]]})
            for m in data["Page"]["media"]:
                mapping = {"anilist": str(m["id"])}
                if m.get("idMal"):
                    mapping["mal"] = str(m["idMal"])
                found.append(mapping)
        return found


PROVIDER = register(
    Provider(
        id="anilist",
        name="AniList",
        monogram="AL",
        colors=ProviderColors(bg="#DDF3FF", fg="#0369A1", dark_bg="#0B3550", dark_fg="#A8DDFF"),
        auth_kind=AuthKind.OAUTH_CODE,
        incremental_reads=True,
        score_step=1,
        rate_limit=(25, 60),  # AniList allows ~90/min but is sometimes degraded to 30/min: stay under that
        client=AniListClient,
        url_pattern=re.compile(r"^https?://anilist\.co/anime/(\d+)"),
        lookup_from=frozenset({"mal", "anilist"}),
        authorize_url=authorize_url,
        exchange_code=exchange_code,
        # No refresh: AniList tokens last a year and the user has to re-link afterwards.
    )
)
