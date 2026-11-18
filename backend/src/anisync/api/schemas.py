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
    external_ids: dict[str, str]


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
    error: str | None = None
    reauth_required: bool | None = None


class SyncRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trigger: str
    status: str
    summary: dict[str, MirrorResultOut]
    started_at: datetime
    finished_at: datetime | None
