"""Admin: titles that couldn't be translated between providers, and linking them by hand (`/api/admin/titles`)."""

import csv
import io
import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select

from anisync import conflicts, moderation, permissions, unmatched
from anisync.api.deps import Session, require_any_permission, require_permission
from anisync.api.schemas import OkOut
from anisync.mapping import IdMapper, existing_pairs
from anisync.models import IdConflict, MediaExternalId, UnmatchedTitle, UnmatchedTitleUser, User
from anisync.providers import UnknownProvider, get_provider, is_registered

router = APIRouter(prefix="/api/admin/titles", tags=["admin"], dependencies=[Depends(require_any_permission)])
Linker = Annotated[User, Depends(require_permission(permissions.TITLES_LINK))]
ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class UnmatchedOut(BaseModel):
    source: str
    source_id: str
    target: str
    title: str
    users: int  # how many users have it on their master list right now
    first_seen_at: datetime
    last_seen_at: datetime


class LinkIn(BaseModel):
    source: str
    source_id: str
    target: str
    target_id: str

    @field_validator("source_id", "target_id")
    @classmethod
    def _id(cls, value: str) -> str:
        value = value.strip()
        if not ID.match(value):
            raise ValueError("A title id is letters, digits, - and _ only")
        return value


def _check_provider(provider_id: str) -> None:
    try:
        get_provider(provider_id)
    except UnknownProvider:
        raise HTTPException(422, f"Unknown provider {provider_id!r}") from None


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get("/unmatched")
async def list_unmatched(
    session: Session,
    admin: Linker,
    source: Annotated[str | None, Query(max_length=32)] = None,
    target: Annotated[str | None, Query(max_length=32)] = None,
    q: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[UnmatchedOut]:
    """Most affected users first. Titles that have been mapped since (by anyone) are left out."""
    users = func.count(UnmatchedTitleUser.user_id)
    query = (
        select(UnmatchedTitle, users)
        .join(
            UnmatchedTitleUser,
            (UnmatchedTitleUser.source == UnmatchedTitle.source)
            & (UnmatchedTitleUser.source_id == UnmatchedTitle.source_id)
            & (UnmatchedTitleUser.target == UnmatchedTitle.target),
        )
        .where(~unmatched.resolved())
        .group_by(UnmatchedTitle.source, UnmatchedTitle.source_id, UnmatchedTitle.target)
        .order_by(users.desc(), UnmatchedTitle.last_seen_at.desc(), UnmatchedTitle.source_id)
        .limit(limit)
        .offset(offset)
    )
    if source:
        query = query.where(UnmatchedTitle.source == source)
    if target:
        query = query.where(UnmatchedTitle.target == target)
    if q.strip():
        query = query.where(UnmatchedTitle.title.ilike(_like(q.strip()), escape="\\"))
    return [
        UnmatchedOut(
            source=t.source,
            source_id=t.source_id,
            target=t.target,
            title=t.title,
            users=count,
            first_seen_at=t.first_seen_at,
            last_seen_at=t.last_seen_at,
        )
        for t, count in (await session.execute(query)).all()
    ]


@router.post("/link")
async def link_title(body: LinkIn, session: Session, admin: Linker) -> OkOut:
    """Say that `source`'s title is `target`'s title. Manual links are never overwritten by imports or syncs."""
    _check_provider(body.source)
    _check_provider(body.target)
    if body.source == body.target:
        raise HTTPException(422, "Pick two different providers")
    known = await existing_pairs(session, [(body.source, body.source_id), (body.target, body.target_id)])
    source_media, target_media = known.get((body.source, body.source_id)), known.get((body.target, body.target_id))
    if source_media is not None and target_media is not None and source_media != target_media:
        raise HTTPException(409, f"{body.target} {body.target_id} is already linked to a different title")
    if source_media is not None:
        other = await session.scalar(
            select(MediaExternalId.external_id).where(
                MediaExternalId.media_id == source_media, MediaExternalId.provider == body.target
            )
        )
        if other is not None and other != body.target_id:
            raise HTTPException(409, f"That title already has the {body.target} id {other}")
    unmatched_title = await session.get(UnmatchedTitle, (body.source, body.source_id, body.target))
    async with IdMapper(session) as mapper:
        await mapper.remember(
            {body.source: body.source_id, body.target: body.target_id},
            title=unmatched_title.title if unmatched_title else "",
            source="manual",
        )
    await unmatched.forget(session, body.source, body.source_id, body.target)
    await moderation.record(
        session,
        admin,
        "title.link",
        source_provider=body.source,  # (not `target=`: that name is record()'s own argument)
        source_id=body.source_id,
        target_provider=body.target,
        target_id=body.target_id,
    )
    await session.commit()
    return OkOut()


# ---- cross-reference conflicts ------------------------------------------------------------------------------------


class ConflictOut(BaseModel):
    id: int
    kind: Literal["already_has", "disagree"]
    detail: str
    ids: dict[str, str]  # what the list read claimed: provider -> title id
    links: dict[str, str]  # provider -> the title's page there, to check the claim
    titles: list[str]  # the titles (media rows) involved, as the dataset knows them
    seen: int
    first_seen_at: datetime
    last_seen_at: datetime
    status: Literal["open", "kept"]
    note: str | None
    resolved_at: datetime | None


class ConflictCounts(BaseModel):
    open: int
    kept: int


class ConflictsOut(BaseModel):
    counts: ConflictCounts
    items: list[ConflictOut]


class ConflictDecision(BaseModel):
    status: Literal["open", "kept"]
    note: str | None = None

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        value = " ".join((value or "").split())[:500]
        return value or None


def _links(ids: dict[str, str]) -> dict[str, str]:
    out = {}
    for provider_id, title_id in ids.items():
        if is_registered(provider_id) and (template := get_provider(provider_id).title_url):
            out[provider_id] = template.format(id=title_id)
    return out


async def _conflict_rows(session, status: str, limit: int, offset: int) -> list[ConflictOut]:
    stmt = select(IdConflict).order_by(IdConflict.last_seen_at.desc(), IdConflict.id.desc()).limit(limit).offset(offset)
    if status != "all":
        stmt = stmt.where(IdConflict.status == status)
    rows = (await session.scalars(stmt)).all()
    names = await conflicts.titles(session, rows)
    return [
        ConflictOut(
            id=r.id,
            kind=r.kind,  # type: ignore[arg-type]
            detail=r.detail,
            ids=r.ids,
            links=_links(r.ids),
            titles=[names[m] for m in r.media_ids if m in names],
            seen=r.seen,
            first_seen_at=r.first_seen_at,
            last_seen_at=r.last_seen_at,
            status=r.status,  # type: ignore[arg-type]
            note=r.note,
            resolved_at=r.resolved_at,
        )
        for r in rows
    ]


@router.get("/conflicts")
async def list_conflicts(
    session: Session,
    admin: Linker,
    status: Literal["open", "kept", "all"] = "open",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ConflictsOut:
    """Cross-references that disagree with the ID dataset. The dataset's answer is used meanwhile."""
    counted = await conflicts.counts(session)
    return ConflictsOut(
        counts=ConflictCounts(open=counted.get("open", 0), kept=counted.get("kept", 0)),
        items=await _conflict_rows(session, status, limit, offset),
    )


@router.put("/conflicts/{conflict_id}")
async def decide_conflict(conflict_id: int, body: ConflictDecision, session: Session, admin: Linker) -> ConflictOut:
    """ "Keep the dataset's answer" (`kept`, with an optional note) or reopen it. Nothing about the mapping changes."""
    row = await session.get(IdConflict, conflict_id)
    if row is None:
        raise HTTPException(404, "No such conflict")
    row.status = body.status
    row.note = body.note
    row.resolved_by, row.resolved_at = (admin.id, datetime.now(UTC)) if body.status == "kept" else (None, None)
    await moderation.record(
        session,
        admin,
        "id_conflict." + ("keep" if body.status == "kept" else "reopen"),
        note=body.note,
        conflict=row.id,
    )
    await session.commit()
    [out] = [c for c in await _conflict_rows(session, "all", 200, 0) if c.id == row.id] or [None]
    if out is None:  # beyond the first page of a very long list
        raise HTTPException(500, "Conflict saved but could not be read back")
    return out


def _csv_cell(value: object) -> str:
    """A cell that a spreadsheet can't mistake for a formula."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


class CsvResponse(Response):
    """A spreadsheet download. Its rows are built from the same `ConflictOut` objects the JSON route returns."""

    media_type = "text/csv"


@router.get("/conflicts/report.csv", response_class=CsvResponse)
async def conflicts_report(
    session: Session, admin: Linker, status: Literal["open", "kept", "all"] = "open"
) -> CsvResponse:
    """The conflicts as a spreadsheet, with a link per title id, ready to send to whoever owns the wrong data."""
    items = await _conflict_rows(session, status, 200, 0)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        ["kind", "status", "seen", "first seen", "last seen", "titles in the dataset", "claimed ids", "links", "note"]
    )
    for c in items:
        writer.writerow(
            [
                _csv_cell(v)
                for v in (
                    c.kind,
                    c.status,
                    c.seen,
                    c.first_seen_at.date().isoformat(),
                    c.last_seen_at.date().isoformat(),
                    " | ".join(c.titles),
                    ", ".join(f"{p}={i}" for p, i in sorted(c.ids.items())),
                    "  ".join(c.links[p] for p in sorted(c.links)),
                    c.note or "",
                )
            ]
        )
    await moderation.record(session, admin, "id_conflict.report", note=status, rows=len(items))
    await session.commit()
    return CsvResponse(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="anisync-id-conflicts.csv"'},
    )
