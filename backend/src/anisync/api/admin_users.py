"""Admin: find a user and see how they are doing (`/api/admin/users`). Never exposes tokens."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import aliased

from anisync import deletion, jobs, moderation, permissions, sync
from anisync.api.admin_syncs import RunOut, _run_out, _run_query
from anisync.api.deps import Session, require_any_permission, require_permission
from anisync.api.schemas import MirrorResultOut, OkOut, QueuedOut
from anisync.models import AdminAction, LinkedAccount, ListSnapshot, SyncRun, User, UserSession
from anisync.providers.base import ReauthRequired

router = APIRouter(prefix="/api/admin/users", tags=["admin"], dependencies=[Depends(require_any_permission)])
Reader = Annotated[User, Depends(require_permission(permissions.USERS_READ))]


class UserRowOut(BaseModel):
    id: int
    display_name: str
    avatar_url: str | None
    role: str
    created_at: datetime
    banned_at: datetime | None
    master_provider: str | None
    providers: list[str]
    last_seen_at: datetime | None


class AdminAccountOut(BaseModel):
    id: int
    provider: str
    remote_username: str
    is_master: bool
    sync_enabled: bool
    prune_missing: bool
    expires_at: datetime | None
    needs_relink: bool  # the token expired and we can't renew it
    last_synced_at: datetime | None
    last_full_read_at: datetime | None
    entries: int  # titles in our copy of this account's list


class ActionOut(BaseModel):
    id: int
    actor_id: int | None
    actor_name: str | None
    action: str
    note: str | None
    details: dict
    created_at: datetime


class UserDetailOut(BaseModel):
    id: int
    display_name: str
    avatar_url: str | None
    avatar_provider: str | None
    role: str
    created_at: datetime
    banned_at: datetime | None
    ban_reason: str | None
    banned_by: int | None
    banned_by_name: str | None
    sessions: int
    last_seen_at: datetime | None
    manual_syncs_7d: int
    accounts: list[AdminAccountOut]
    recent_runs: list[RunOut]
    actions: list[ActionOut]  # what admins did to this user, newest first


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get("")
async def search_users(
    session: Session,
    admin: Reader,
    q: Annotated[str, Query(max_length=100)] = "",
    banned: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[UserRowOut]:
    """Search by AniSync id, display name or a linked provider username. No query lists the newest users."""
    query = select(User).order_by(User.id.desc()).limit(limit).offset(offset)
    q = q.strip()
    if q:
        text = _like(q)
        matches = [
            User.display_name.ilike(text, escape="\\"),
            exists().where(LinkedAccount.user_id == User.id, LinkedAccount.remote_username.ilike(text, escape="\\")),
        ]
        if q.isdecimal() and len(q) < 10:
            matches.append(User.id == int(q))
        query = query.where(or_(*matches))
    if banned is not None:
        query = query.where(User.banned_at.is_not(None) if banned else User.banned_at.is_(None))
    users = list((await session.scalars(query)).all())
    seen = dict(
        (
            await session.execute(
                select(UserSession.user_id, func.max(UserSession.last_seen_at))
                .where(UserSession.user_id.in_([u.id for u in users]))
                .group_by(UserSession.user_id)
            )
        ).all()
    )
    return [
        UserRowOut(
            id=u.id,
            display_name=u.display_name,
            avatar_url=u.avatar_url,
            role=u.role,
            created_at=u.created_at,
            banned_at=u.banned_at,
            master_provider=u.master.provider if u.master else None,
            providers=sorted({a.provider for a in u.accounts}),
            last_seen_at=seen.get(u.id),
        )
        for u in users
    ]


@router.get("/{user_id}")
async def get_user(user_id: int, session: Session, admin: Reader) -> UserDetailOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    now = datetime.now(UTC)
    account_ids = [a.id for a in user.accounts]
    entries = dict(
        (
            await session.execute(
                select(ListSnapshot.account_id, func.count())
                .where(ListSnapshot.account_id.in_(account_ids))
                .group_by(ListSnapshot.account_id)
            )
        ).all()
    )
    sessions_count, last_seen = (
        await session.execute(
            select(func.count(), func.max(UserSession.last_seen_at)).where(UserSession.user_id == user.id)
        )
    ).one()
    manual = await session.scalar(
        select(func.count()).where(
            SyncRun.user_id == user.id,
            SyncRun.trigger == "manual",
            SyncRun.started_at >= now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6),
        )
    )
    runs = (
        await session.execute(
            _run_query()
            .where(SyncRun.user_id == user.id)
            .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
            .limit(10)
        )
    ).all()
    actor = aliased(User)
    actions = (
        await session.execute(
            select(AdminAction, actor.display_name)
            .join(actor, actor.id == AdminAction.actor_id, isouter=True)
            .where(AdminAction.target_user_id == user.id)
            .order_by(AdminAction.id.desc())
            .limit(30)
        )
    ).all()
    banned_by_name = (
        await session.scalar(select(User.display_name).where(User.id == user.banned_by)) if user.banned_by else None
    )
    return UserDetailOut(
        id=user.id,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        avatar_provider=user.avatar_provider,
        role=user.role,
        created_at=user.created_at,
        banned_at=user.banned_at,
        ban_reason=user.ban_reason,
        banned_by=user.banned_by,
        banned_by_name=banned_by_name,
        sessions=sessions_count,
        last_seen_at=last_seen,
        manual_syncs_7d=manual or 0,
        accounts=[
            AdminAccountOut(
                id=a.id,
                provider=a.provider,
                remote_username=a.remote_username,
                is_master=a.id == user.master_account_id,
                sync_enabled=a.sync_enabled,
                prune_missing=a.prune_missing,
                expires_at=a.expires_at,
                needs_relink=a.needs_relink(now),
                last_synced_at=a.last_synced_at,
                last_full_read_at=a.last_full_read_at,
                entries=entries.get(a.id, 0),
            )
            for a in sorted(user.accounts, key=lambda a: a.id)
        ],
        recent_runs=[_run_out(*row) for row in runs],
        actions=[
            ActionOut(
                id=act.id,
                actor_id=act.actor_id,
                actor_name=name,
                action=act.action,
                note=act.note,
                details=act.details or {},
                created_at=act.created_at,
            )
            for act, name in actions
        ],
    )


@router.post("/{user_id}/sync")
async def sync_user_now(
    user_id: int,
    session: Session,
    admin: Annotated[User, Depends(require_permission(permissions.SYNCS_RETRY))],
) -> QueuedOut:
    """Queue a sync for this user (the same as their own "Sync now"). Audited."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if user.is_banned:
        raise HTTPException(409, "This user is banned")
    if user.master is None:
        raise HTTPException(409, "This user has no master list yet")
    queued = await jobs.enqueue_user_sync(user.id, "manual")
    await moderation.record(session, admin, "user.sync", user, queued=queued)
    await session.commit()
    return QueuedOut(queued=queued)


async def _active_user(session, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return user


@router.post("/{user_id}/full-check")
async def full_check(
    user_id: int,
    session: Session,
    admin: Annotated[User, Depends(require_permission(permissions.SYNCS_RETRY))],
) -> QueuedOut:
    """Queue a sync that also re-reads every account in full (notices removals and edits made on the providers)."""
    user = await _active_user(session, user_id)
    if user.is_banned or user.master is None:
        raise HTTPException(409, "This user is banned or has no master list yet")
    queued = await jobs.enqueue_user_sync(user.id, "manual", full=True)
    await moderation.record(session, admin, "user.full_check", user, queued=queued)
    await session.commit()
    return QueuedOut(queued=queued)


@router.post("/{user_id}/preview", response_model_exclude_none=True)
async def preview_sync(
    user_id: int,
    session: Session,
    admin: Annotated[User, Depends(require_permission(permissions.SYNCS_RETRY))],
) -> dict[str, MirrorResultOut]:
    """What the user's next sync would change on each mirror. Reads from the providers; writes nothing."""
    user = await _active_user(session, user_id)
    if user.master is None:
        raise HTTPException(409, "This user has no master list yet")
    try:
        return await sync.run_sync(session, user.id, "preview", dry_run=True)
    except ReauthRequired as exc:
        raise HTTPException(409, str(exc)) from exc


def _account(user: User, account_id: int):
    account = next((a for a in user.accounts if a.id == account_id), None)
    if account is None:
        raise HTTPException(404, "Account not found")
    return account


@router.post("/{user_id}/accounts/{account_id}/revoke-tokens")
async def revoke_account_tokens(
    user_id: int,
    account_id: int,
    session: Session,
    admin: Annotated[User, Depends(require_permission(permissions.USERS_MANAGE_TOKENS))],
) -> OkOut:
    """Forget the stored tokens of one account: the user has to link it again (the fix for a login loop)."""
    user = await _active_user(session, user_id)
    account = _account(user, account_id)
    moderation.revoke_tokens(account)
    await moderation.record(
        session, admin, "account.revoke_tokens", user, provider=account.provider, account_id=account.id
    )
    await session.commit()
    return OkOut()


@router.delete("/{user_id}/accounts/{account_id}")
async def unlink_account(
    user_id: int,
    account_id: int,
    session: Session,
    admin: Annotated[User, Depends(require_permission(permissions.USERS_MANAGE_TOKENS))],
) -> OkOut:
    """Remove a broken linked account: its tokens and our copy of its list go; the provider's list is untouched."""
    user = await _active_user(session, user_id)
    account = _account(user, account_id)
    provider, was_master = account.provider, user.master_account_id == account.id
    account_id = account.id
    try:
        await moderation.unlink(session, user, account)
    except moderation.ModerationError as exc:
        raise HTTPException(exc.status, exc.message) from None
    await moderation.record(
        session, admin, "account.unlink", user, provider=provider, account_id=account_id, was_master=was_master
    )
    await session.commit()
    return OkOut()


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    confirm: Annotated[int, Query(description="Must equal the user id")],
    session: Session,
    admin: Annotated[User, Depends(require_permission(permissions.USERS_DELETE))],
) -> OkOut:
    """Delete the user and all their data. Irreversible, so the id has to be repeated as `confirm`."""
    if confirm != user_id:
        raise HTTPException(422, "confirm must repeat the user id")
    user = await _active_user(session, user_id)
    if user.id == admin.id:
        raise HTTPException(400, "Delete your own account from your settings")
    if user.is_banned:
        raise HTTPException(409, "Banned users are kept (otherwise they could sign up again); unban first")
    if permissions.ROLES_MANAGE in user.permissions:
        raise HTTPException(409, "Change this user's role before deleting them")
    await moderation.record(
        session, admin, "user.delete", None, user_id=user.id, display_name=user.display_name, role=user.role
    )
    await deletion.delete_user(session, user)
    return OkOut()
