"""AniList GraphQL API. https://docs.anilist.co

OAuth2 authorization code grant. Tokens last one year and cannot be refreshed,
so the user has to re-link when it expires. Rate limit: 90 req/min (sometimes degraded to 30).
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from anisync.config import get_settings
from anisync.models import Service
from anisync.services.base import ListEntry, RemoteUser, ServiceClient, Status, TokenSet, quantize_score

GRAPHQL_URL = "https://graphql.anilist.co"
AUTHORIZE_URL = "https://anilist.co/api/v2/oauth/authorize"
TOKEN_URL = "https://anilist.co/api/v2/oauth/token"

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

ENTRY_ID_QUERY = "query ($userId: Int, $mediaId: Int) { MediaList(userId: $userId, mediaId: $mediaId) { id } }"
DELETE_MUTATION = "mutation ($id: Int) { DeleteMediaListEntry(id: $id) { deleted } }"
VIEWER_QUERY = "{ Viewer { id name } }"
# Batch ID lookups (used by the ID mapper). perPage max is 50.
BY_MAL_IDS = "query ($ids: [Int]) { Page(perPage: 50) { media(idMal_in: $ids, type: ANIME) { id idMal } } }"
BY_IDS = "query ($ids: [Int]) { Page(perPage: 50) { media(id_in: $ids, type: ANIME) { id idMal } } }"


def _fuzzy_to_date(fd: dict | None) -> date | None:
    if not fd or not (fd.get("year") and fd.get("month") and fd.get("day")):
        return None
    return date(fd["year"], fd["month"], fd["day"])


def _date_to_fuzzy(d: date | None) -> dict | None:
    return {"year": d.year, "month": d.month, "day": d.day} if d else None


def authorize_url(state: str, redirect_uri: str) -> str:
    params = {
        "client_id": get_settings().anilist_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(code: str, redirect_uri: str) -> TokenSet:
    s = get_settings()
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": s.anilist_client_id,
                "client_secret": s.anilist_client_secret,
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


class AniListClient(ServiceClient):
    service = Service.ANILIST
    score_step = 1
    rate_limit = (25, 60)  # stay under the degraded 30/min limit

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

    async def fetch_list(self) -> list[ListEntry]:
        data = await self.gql(LIST_QUERY, {"userId": int(self.remote_user_id)})
        seen: dict[int, ListEntry] = {}  # entries can appear in several custom lists
        for lst in data["MediaListCollection"]["lists"]:
            for e in lst["entries"]:
                media = e["media"]
                seen[e["id"]] = ListEntry(
                    media_id=e["mediaId"],
                    anilist_id=e["mediaId"],
                    mal_id=media.get("idMal"),
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
        return list(seen.values())

    async def save_entry(self, entry: ListEntry) -> None:
        status = "REPEATING" if entry.rewatching and entry.status == Status.WATCHING else FROM_STATUS[entry.status]
        variables: dict[str, Any] = {
            "mediaId": entry.media_id,
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

    async def delete_entry(self, media_id: int, remote_entry_id: str | None = None) -> None:
        if remote_entry_id is None:
            found = await self.gql(ENTRY_ID_QUERY, {"userId": int(self.remote_user_id), "mediaId": media_id})
            if not found.get("MediaList"):
                return
            remote_entry_id = str(found["MediaList"]["id"])
        await self.gql(DELETE_MUTATION, {"id": int(remote_entry_id)})

    async def media_by_mal_ids(self, mal_ids: list[int]) -> dict[int, int]:
        """{mal_id: anilist_id} for up to 50 MAL IDs."""
        data = await self.gql(BY_MAL_IDS, {"ids": mal_ids})
        return {m["idMal"]: m["id"] for m in data["Page"]["media"] if m.get("idMal")}

    async def media_by_ids(self, anilist_ids: list[int]) -> dict[int, int | None]:
        """{anilist_id: mal_id or None} for up to 50 AniList IDs."""
        data = await self.gql(BY_IDS, {"ids": anilist_ids})
        return {m["id"]: m.get("idMal") for m in data["Page"]["media"]}
