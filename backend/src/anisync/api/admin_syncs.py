"""Admin: sync status, run history and event log (`/api/admin/syncs/*`)."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from anisync import jobs, permissions, sync
from anisync.api.deps import Session, require_any_permission, require_permission
from anisync.models import LinkedAccount, SyncRun, SyncRunEvent, User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/syncs", tags=["admin"], dependencies=[Depends(require_any_permission)])
Reader = Annotated[User, Depends(require_permission(permissions.SYNCS_READ))]

Level = Literal["info", "warning", "error"]
Kind = Literal["change_failed", "rate_limited", "reauth", "unmapped", "drifted"]
Status = Literal["running", "ok", "error"]


class RunOut(BaseModel):
    id: int
    user_id: int
    display_name: str | None
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    changes: int
    mirrors: int  # how many mirror accounts the run looked at
    errors: int  # error events
    warnings: int  # warning events


class EventOut(BaseModel):
    id: int
    run_id: int
    user_id: int | None
    display_name: str | None
    account_id: int | None
    provider: str | None
    level: str
    kind: str
    message: str
    media_ref: str | None
    created_at: datetime


class RunDetailOut(BaseModel):
    run: RunOut
    summary: dict  # per mirror: counts, `read` mode, `drifted`, error texts (already redacted), unmapped titles
    events: list[EventOut]


class QueueOut(BaseModel):
    queue: str
    todo: int
    doing: int
    failed: int


class BatchOut(BaseModel):
    hour: datetime  # the UTC hour the batch belongs to
    runs: int
    ok: int
    error: int
    changes: int


class SyncStatusOut(BaseModel):
    queue_available: bool  # false when the job queue couldn't be read; the rest of the page still works
    queues: list[QueueOut]
    stalled_jobs: int  # running jobs whose worker stopped reporting
    last_batch: BatchOut | None  # the latest hour with scheduled syncs
    next_batch_at: datetime
    users_in_next_batch: int
    full_reads_overdue: int  # accounts of active users not fully read for more than 8 days


def _run_query():
    events = (
        select(
            SyncRunEvent.run_id,
            func.count().filter(SyncRunEvent.level == "error").label("errors"),
            func.count().filter(SyncRunEvent.level == "warning").label("warnings"),
        )
        .group_by(SyncRunEvent.run_id)
        .subquery()
    )
    return (
        select(SyncRun, User.display_name, func.coalesce(events.c.errors, 0), func.coalesce(events.c.warnings, 0))
        .join(User, User.id == SyncRun.user_id)
        .join(events, events.c.run_id == SyncRun.id, isouter=True)
    )


def _run_out(run: SyncRun, name: str | None, errors: int, warnings: int) -> RunOut:
    return RunOut(
        id=run.id,
        user_id=run.user_id,
        display_name=name,
        trigger=run.trigger,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        changes=run.changes,
        mirrors=len(run.summary or {}),
        errors=errors,
        warnings=warnings,
    )


def _event_out(event: SyncRunEvent, user_id: int | None, name: str | None) -> EventOut:
    return EventOut(
        id=event.id,
        run_id=event.run_id,
        user_id=user_id,
        display_name=name,
        account_id=event.account_id,
        provider=event.provider,
        level=event.level,
        kind=event.kind,
        message=event.message,
        media_ref=event.media_ref,
        created_at=event.created_at,
    )


@dataclass
class QueueCounts:
    queue: str
    todo: int = 0
    doing: int = 0
    failed: int = 0


async def queue_counts() -> list[QueueCounts]:
    """Jobs per queue and state, through Procrastinate's own API."""
    out: dict[str, QueueCounts] = {}
    for row in await jobs.app.job_manager.list_queues_async():
        counts = out.setdefault(row["name"], QueueCounts(row["name"]))
        counts.todo += row.get("todo", 0)  # the job manager flattens the per-status counts into each row
        counts.doing += row.get("doing", 0)
        counts.failed += row.get("failed", 0)
    return sorted(out.values(), key=lambda c: c.queue)


@router.get("/status")
async def sync_status(session: Session, admin: Reader) -> SyncStatusOut:
    now = datetime.now(UTC)
    hour = func.date_trunc("hour", func.timezone("UTC", SyncRun.started_at))
    latest = (
        await session.execute(
            select(
                hour.label("hour"),
                func.count(),
                func.count().filter(SyncRun.status == "ok"),
                func.count().filter(SyncRun.status == "error"),
                func.coalesce(func.sum(SyncRun.changes), 0),
            )
            .where(SyncRun.trigger == "scheduled")
            .group_by(hour)
            .order_by(hour.desc())
            .limit(1)
        )
    ).first()
    next_batch = sync.next_batch_at(now)
    overdue = await session.scalar(
        select(func.count())
        .select_from(LinkedAccount)
        .join(User, User.id == LinkedAccount.user_id)
        .where(
            User.master_account_id.is_not(None),
            User.banned_at.is_(None),
            (LinkedAccount.last_full_read_at.is_(None) & (User.created_at < now - timedelta(days=1)))
            | (LinkedAccount.last_full_read_at < now - sync.FULL_READ_EVERY - timedelta(days=1)),
        )
    )
    try:
        queues = [QueueOut(**vars(c)) for c in await queue_counts()]
        stalled = len(list(await jobs.app.job_manager.get_stalled_jobs(seconds_since_heartbeat=300)))
        queue_available = True
    except Exception:
        log.exception("couldn't read the job queue for the admin status page")
        queues, stalled, queue_available = [], 0, False
    return SyncStatusOut(
        queue_available=queue_available,
        queues=queues,
        stalled_jobs=stalled,
        last_batch=(
            BatchOut(
                hour=latest[0].replace(tzinfo=UTC),
                runs=latest[1],
                ok=latest[2],
                error=latest[3],
                changes=int(latest[4]),
            )
            if latest
            else None
        ),
        next_batch_at=next_batch,
        users_in_next_batch=len(await sync.users_in_slot(session, next_batch.hour)),
        full_reads_overdue=overdue or 0,
    )


@router.get("/runs")
async def list_runs(
    session: Session,
    admin: Reader,
    status: Status | None = None,
    trigger: Literal["scheduled", "manual", "edit"] | None = None,
    user_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before_id: int | None = None,
) -> list[RunOut]:
    """Newest first. Pass the last id you got as `before_id` for the next page."""
    query = _run_query().order_by(SyncRun.id.desc()).limit(limit)
    if status:
        query = query.where(SyncRun.status == status)
    if trigger:
        query = query.where(SyncRun.trigger == trigger)
    if user_id is not None:
        query = query.where(SyncRun.user_id == user_id)
    if before_id is not None:
        query = query.where(SyncRun.id < before_id)
    return [_run_out(*row) for row in (await session.execute(query)).all()]


@router.get("/runs/{run_id}")
async def get_run(run_id: int, session: Session, admin: Reader) -> RunDetailOut:
    row = (await session.execute(_run_query().where(SyncRun.id == run_id))).first()
    if row is None:
        raise HTTPException(404, "Run not found")
    run = row[0]
    events = await session.scalars(select(SyncRunEvent).where(SyncRunEvent.run_id == run_id).order_by(SyncRunEvent.id))
    return RunDetailOut(
        run=_run_out(*row),
        summary=run.summary or {},
        events=[_event_out(e, run.user_id, row[1]) for e in events],
    )


@router.get("/events")
async def list_events(
    session: Session,
    admin: Reader,
    level: Level | None = None,
    kind: Kind | None = None,
    provider: str | None = Query(default=None, max_length=32),
    user_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    before_id: int | None = None,
) -> list[EventOut]:
    """The log across all runs, newest first."""
    query = (
        select(SyncRunEvent, SyncRun.user_id, User.display_name)
        .join(SyncRun, SyncRun.id == SyncRunEvent.run_id)
        .join(User, User.id == SyncRun.user_id)
        .order_by(SyncRunEvent.id.desc())
        .limit(limit)
    )
    if level:
        query = query.where(SyncRunEvent.level == level)
    if kind:
        query = query.where(SyncRunEvent.kind == kind)
    if provider:
        query = query.where(SyncRunEvent.provider == provider)
    if user_id is not None:
        query = query.where(SyncRun.user_id == user_id)
    if before_id is not None:
        query = query.where(SyncRunEvent.id < before_id)
    return [_event_out(*row) for row in (await session.execute(query)).all()]
