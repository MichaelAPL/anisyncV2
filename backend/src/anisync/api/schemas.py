"""Response schemas shared by several routers."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from anisync.providers.base import Status


class OkOut(BaseModel):
    ok: bool = True


# ---- list ----------------------------------------------------------------------------------------------------


class ListEntryOut(BaseModel):
    """One title on the master list. Exposes display and synced fields only, never provider internals."""

    model_config = ConfigDict(from_attributes=True)

    media_id: str
    status: Status
    progress: int
    score: int | None
    rewatching: bool
    started_at: date | None
    finished_at: date | None
    updated_at: datetime | None
    title: str
    total_episodes: int | None
    image_url: str | None
    adult: bool  # the provider marks it adult: the UI blurs the cover unless the user chose to see them
    external_ids: dict[str, str]


class RefreshOut(BaseModel):
    mode: Literal["full", "incremental", "snapshot"]
    changed: int


class SavedEntryOut(OkOut):
    queued_mirrors: int


# ---- sync ----------------------------------------------------------------------------------------------------


class QueuedOut(BaseModel):
    queued: bool


class ChangeOut(BaseModel):
    op: Literal["create", "update", "delete"]
    media_id: str
    fields: list[str]
    title: str


class MirrorResultOut(BaseModel):
    """What a sync did (or, for a preview, would do) on one mirror. Fields that don't apply are left out."""

    provider: str = ""
    username: str = ""
    create: int = 0
    update: int = 0
    delete: int = 0
    unmapped: int = 0
    unmapped_titles: list[str] = []
    changes: list[ChangeOut] | None = None  # previews only
    errors: list[str] | None = None  # applied syncs only
    skipped: int | None = None  # changes the provider hides from us (not failures), see `skipped_reason`
    adult_skipped: int | None = None  # adult titles left out because this mirror's "skip adult titles" is on
    skipped_reason: str | None = None
    error: str | None = None
    reauth_required: bool | None = None
    read: Literal["full", "incremental", "snapshot"] | None = None  # how the mirror's current state was learned
    drifted: int | None = None  # full reads: entries edited directly on the mirror's site since our last write


class SyncRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trigger: str
    status: str
    summary: dict[str, MirrorResultOut]
    started_at: datetime
    finished_at: datetime | None
    heartbeat_at: datetime | None = None  # last sign of progress; the server treats a run as dead 15 min after it
    planned: int = 0  # while running: how many changes it set out to make so far...
    processed: int = 0  # ...and how many of them are done (or failed)
