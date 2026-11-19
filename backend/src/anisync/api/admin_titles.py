"""Admin: titles that couldn't be translated between providers, and linking them by hand (`/api/admin/titles`)."""

import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select

from anisync import moderation, permissions, unmatched
from anisync.api.deps import Session, require_any_permission, require_permission
from anisync.api.schemas import OkOut
from anisync.mapping import IdMapper, existing_pairs
from anisync.models import MediaExternalId, UnmatchedTitle, UnmatchedTitleUser, User
from anisync.providers import UnknownProvider, get_provider

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
