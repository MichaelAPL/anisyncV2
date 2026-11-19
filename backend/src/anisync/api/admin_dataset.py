"""Admin: the offline ID dataset that fills the title mapping (`/api/admin/dataset`)."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from procrastinate.exceptions import AlreadyEnqueued
from pydantic import BaseModel
from sqlalchemy import func, select

from anisync import jobs, moderation, permissions
from anisync.api.deps import Session, require_any_permission, require_permission
from anisync.api.schemas import QueuedOut
from anisync.config import get_settings
from anisync.models import DatasetImport, Media, MediaExternalId, User

router = APIRouter(prefix="/api/admin/dataset", tags=["admin"], dependencies=[Depends(require_any_permission)])


class ImportOut(BaseModel):
    id: int
    dataset: str
    version: str | None
    status: str  # running | ok | unchanged | error
    stats: dict
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class CoverageOut(BaseModel):
    provider: str
    ids: int
    seed: int  # from the offline dataset
    learned: int  # learned from list entries and provider lookups while syncing
    manual: int  # linked by an admin; imports never touch these


class DatasetOut(BaseModel):
    source_url: str
    media: int  # distinct titles in the mapping
    coverage: list[CoverageOut]
    imports: list[ImportOut]  # newest first


class ImportIn(BaseModel):
    force: bool = False


@router.get("")
async def dataset_status(
    session: Session, admin: Annotated[User, Depends(require_permission(permissions.STATS_READ))]
) -> DatasetOut:
    by_provider: dict[str, dict[str, int]] = {}
    counts = await session.execute(
        select(MediaExternalId.provider, MediaExternalId.source, func.count()).group_by(
            MediaExternalId.provider, MediaExternalId.source
        )
    )
    for provider, source, count in counts:
        by_provider.setdefault(provider, {})[source] = count
    imports = await session.scalars(select(DatasetImport).order_by(DatasetImport.id.desc()).limit(10))
    return DatasetOut(
        source_url=get_settings().id_dataset_url,
        media=await session.scalar(select(func.count()).select_from(Media)) or 0,
        coverage=[
            CoverageOut(
                provider=provider,
                ids=sum(sources.values()),
                seed=sources.get("seed", 0),
                learned=sources.get("learned", 0),
                manual=sources.get("manual", 0),
            )
            for provider, sources in sorted(by_provider.items())
        ],
        imports=[
            ImportOut(
                id=i.id,
                dataset=i.dataset,
                version=i.version,
                status=i.status,
                stats=i.stats or {},
                error=i.error,
                started_at=i.started_at,
                finished_at=i.finished_at,
            )
            for i in imports
        ],
    )


@router.post("/import")
async def start_import(
    body: ImportIn,
    session: Session,
    admin: Annotated[User, Depends(require_permission(permissions.PROVIDERS_MANAGE))],
) -> QueuedOut:
    """Queue an import now (it checks the dataset's version first, unless `force`)."""
    running = await session.scalar(
        select(func.count()).select_from(DatasetImport).where(DatasetImport.status == "running")
    )
    if running:
        raise HTTPException(409, "An import is already running")
    try:
        await jobs.import_dataset.defer_async(force=body.force)
        queued = True
    except AlreadyEnqueued:
        queued = False
    await moderation.record(session, admin, "dataset.import", force=body.force, queued=queued)
    await session.commit()
    return QueuedOut(queued=queued)
