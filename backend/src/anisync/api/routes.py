import unicodedata
from datetime import date, datetime, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select

from anisync import deletion, jobs, moderation, permissions, ratelimit, sessions, snapshots, switches, sync
from anisync.api.deps import CurrentUser, Session
from anisync.api.schemas import (
    ListEntryOut,
    MirrorResultOut,
    OkOut,
    QueuedOut,
    RefreshOut,
    SavedEntryOut,
    SyncRunOut,
)
from anisync.models import LinkedAccount, SyncRun, User, UserSession
from anisync.providers import get_provider
from anisync.providers.base import ListEntry, RateLimited, ReauthRequired, Status

router = APIRouter(prefix="/api", tags=["api"])


# ---- account / settings -------------------------------------------------------------------


class AccountOut(BaseModel):
    id: int
    provider: str
    remote_username: str
    is_master: bool
    sync_enabled: bool
    prune_missing: bool
    last_synced_at: datetime | None
    expires_at: datetime | None
    needs_relink: bool  # the connection expired and can't be renewed: the user must link it again
    expires_soon: bool  # it can't be renewed and expires within 30 days (the UI warns before it does)
    avatar_url: str | None  # this account's picture as last seen, for the "use this picture" choice
    hides_adult: bool  # the account hides adult titles from other apps: tell the user (see ProviderOut.adult_help)
    skip_adult: bool  # mirror setting: adult titles are left out of the sync


class MeOut(BaseModel):
    id: int
    display_name: str
    avatar_url: str | None  # None: the UI shows the initials of the name
    avatar_provider: str | None  # which provider the picture came from
    theme: Literal["system", "light", "dark"]
    show_adult_covers: bool  # false: covers of adult titles are blurred
    ignore_adult_titles: bool  # adult titles on the master list are not stored, shown or copied anywhere
    master_account_id: int | None
    role: str
    permissions: list[str]  # what the UI may show; the server enforces it on every request
    next_sync_at: datetime | None  # when the next scheduled sync of the master list runs (None without a master)
    accounts: list[AccountOut]


def _me(user: User) -> MeOut:
    return MeOut(
        id=user.id,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        avatar_provider=user.avatar_provider,
        theme=user.theme,  # type: ignore[arg-type]
        show_adult_covers=user.show_adult_covers,
        ignore_adult_titles=user.ignore_adult_titles,
        master_account_id=user.master_account_id,
        role=user.role,
        permissions=sorted(user.permissions),
        next_sync_at=sync.next_sync_at(user.id) if user.master_account_id else None,
        accounts=[
            AccountOut(
                id=a.id,
                provider=a.provider,
                remote_username=a.remote_username,
                is_master=a.id == user.master_account_id,
                sync_enabled=a.sync_enabled,
                prune_missing=a.prune_missing,
                last_synced_at=a.last_synced_at,
                expires_at=a.expires_at,
                needs_relink=a.needs_relink(),
                expires_soon=a.expires_soon(),
                avatar_url=a.avatar_url,
                hides_adult=a.hides_adult,
                skip_adult=a.skip_adult,
            )
            for a in sorted(user.accounts, key=lambda a: a.id)
        ],
    )


def _account(user: User, account_id: int) -> LinkedAccount:
    account = next((a for a in user.accounts if a.id == account_id), None)
    if account is None:
        raise HTTPException(404, "Account not found")
    return account


@router.get("/health")
async def health() -> OkOut:
    return OkOut()


@router.get("/me")
async def me(user: CurrentUser) -> MeOut:
    return _me(user)


class MasterIn(BaseModel):
    account_id: int


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=40)
    theme: Literal["system", "light", "dark"] | None = None
    show_adult_covers: bool | None = None
    ignore_adult_titles: bool | None = None

    @field_validator("display_name")
    @classmethod
    def _plain_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(unicodedata.category(ch) == "Cc" for ch in value):  # tabs, newlines and other control characters
            raise ValueError("Use letters, numbers and punctuation only")
        value = " ".join(value.split())  # trims and collapses spaces
        if not value or not value.isprintable():
            raise ValueError("Use letters, numbers and punctuation only")
        return value


@router.patch("/me", dependencies=[ratelimit.per_user("profile", limit=20, window=timedelta(hours=1))])
async def update_profile(body: ProfileIn, user: CurrentUser, session: Session) -> MeOut:
    if all(v is None for v in (body.display_name, body.theme, body.show_adult_covers, body.ignore_adult_titles)):
        raise HTTPException(422, "Nothing to change")
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.theme is not None:
        user.theme = body.theme
    if body.show_adult_covers is not None:
        user.show_adult_covers = body.show_adult_covers
    if body.ignore_adult_titles is not None and body.ignore_adult_titles != user.ignore_adult_titles:
        user.ignore_adult_titles = body.ignore_adult_titles
        if user.master is not None:
            if body.ignore_adult_titles:
                await snapshots.remove_adult(session, user.master.id)  # effective at once: gone from My list too
            else:
                user.master.last_full_read_at = None  # the next read is a full one, which brings them back
    await session.commit()
    return _me(user)


class AvatarIn(BaseModel):
    account_id: int


@router.put("/me/avatar", dependencies=[ratelimit.per_user("avatar", limit=10, window=timedelta(minutes=5))])
async def set_avatar(body: AvatarIn, user: CurrentUser, session: Session) -> MeOut:
    """Use the profile picture of one of your linked accounts, read from the provider right now. Choosing your master
    again is how you update the picture after changing it there; nothing here follows a master change by itself."""
    account = _account(user, body.account_id)
    provider = get_provider(account.provider)
    try:
        remote = await (await sync.client_for(account, session)).whoami()
    except ReauthRequired as exc:
        raise HTTPException(409, str(exc)) from exc
    except RateLimited as exc:
        raise HTTPException(429, f"{provider.name} is busy, try again in a minute") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Couldn't reach {provider.name}") from exc
    url = provider.safe_image_url(remote.avatar_url)
    account.avatar_url = url
    if url is None:
        await session.commit()
        raise HTTPException(422, f"{provider.name} has no profile picture for this account")
    user.avatar_url, user.avatar_provider = url, provider.id
    await session.commit()
    return _me(user)


@router.delete("/me/avatar")
async def remove_avatar(user: CurrentUser, session: Session) -> MeOut:
    user.avatar_url = user.avatar_provider = None
    await session.commit()
    return _me(user)


class DeleteAccountIn(BaseModel):
    confirm: str


@router.post(
    "/me/delete",
    dependencies=[ratelimit.per_user("delete-account", limit=3, window=timedelta(hours=1))],
)
async def delete_account(body: DeleteAccountIn, response: Response, user: CurrentUser, session: Session) -> OkOut:
    """Delete your AniSync account and everything we hold about you. Your lists on the providers are not touched."""
    if body.confirm.strip().lower() != "delete my account":
        raise HTTPException(422, 'Type "delete my account" to confirm')
    if permissions.ROLES_MANAGE in user.permissions and await moderation.role_managers(session) <= 1:
        raise HTTPException(409, "You are the last admin who can manage roles; hand that over first")
    await deletion.delete_user(session, user)
    sessions.clear_cookie(response)
    return OkOut()


@router.put("/me/master")
async def set_master(body: MasterIn, user: CurrentUser, session: Session) -> MeOut:
    account = _account(user, body.account_id)
    user.master_account_id = account.id
    if user.ignore_adult_titles:
        await snapshots.remove_adult(session, account.id)  # it may have been read as a mirror before
    await session.commit()
    return _me(user)


class AccountPatch(BaseModel):
    sync_enabled: bool | None = None
    prune_missing: bool | None = None
    skip_adult: bool | None = None


@router.patch("/accounts/{account_id}")
async def update_account(account_id: int, body: AccountPatch, user: CurrentUser, session: Session) -> MeOut:
    account = _account(user, account_id)
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(account, key, value)
    await session.commit()
    return _me(user)


@router.post(
    "/accounts/{account_id}/recheck-adult",
    dependencies=[ratelimit.per_user("adult-recheck", limit=10, window=timedelta(hours=1))],
)
async def recheck_adult(account_id: int, user: CurrentUser, session: Session) -> MeOut:
    """ "I changed the adult-content setting on the provider": look at the account again (one request)."""
    account = _account(user, account_id)
    provider = get_provider(account.provider)
    if provider.adult_help is None:
        raise HTTPException(400, f"{provider.name} has no adult-content setting that affects AniSync")
    try:
        await sync.check_adult_filter(account, await sync.client_for(account, session))
    except ReauthRequired as exc:
        raise HTTPException(409, str(exc)) from exc
    await session.commit()
    return _me(user)


@router.delete("/accounts/{account_id}")
async def unlink_account(account_id: int, user: CurrentUser, session: Session) -> MeOut:
    account = _account(user, account_id)
    if len(user.accounts) == 1:
        raise HTTPException(400, "Can't unlink your only account (it's how you sign in)")
    if user.master_account_id == account.id:
        user.master_account_id = None
    user.accounts.remove(account)
    await session.commit()
    return _me(user)


# ---- sessions -----------------------------------------------------------------------------------


class SessionOut(BaseModel):
    id: int
    created_at: datetime
    last_seen_at: datetime
    user_agent: str
    current: bool


@router.get("/sessions")
async def list_sessions(request: Request, user: CurrentUser, session: Session) -> list[SessionOut]:
    """The devices signed in to this account (the session token itself is never exposed)."""
    token = request.cookies.get(sessions.COOKIE)
    current = sessions.hash_token(token) if token else None
    rows = await session.scalars(
        select(UserSession).where(UserSession.user_id == user.id).order_by(UserSession.last_seen_at.desc())
    )
    return [
        SessionOut(
            id=r.id,
            created_at=r.created_at,
            last_seen_at=r.last_seen_at,
            user_agent=r.user_agent,
            current=r.token_hash == current,
        )
        for r in rows
    ]


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: int, user: CurrentUser, session: Session) -> OkOut:
    result = await session.execute(
        delete(UserSession).where(UserSession.id == session_id, UserSession.user_id == user.id)
    )
    if not result.rowcount:
        raise HTTPException(404, "Session not found")
    await session.commit()
    return OkOut()


# ---- master list --------------------------------------------------------------------------


async def _master_client(user: User, session: Session):
    if user.master is None:
        raise HTTPException(400, "Pick a master list first")
    if user.master.provider in await switches.paused_providers(session):
        raise HTTPException(503, f"{get_provider(user.master.provider).name} is paused for now, try again later")
    try:
        return await sync.client_for(user.master, session)
    except ReauthRequired as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/list")
async def get_list(user: CurrentUser, session: Session) -> list[ListEntryOut]:
    """The master list, from our snapshot: no provider request unless it has never been read."""
    if user.master is None:
        raise HTTPException(400, "Pick a master list first")
    if user.master.last_full_read_at is None:  # only the first read needs the provider (and may be refused if paused)
        client = await _master_client(user, session)
        await sync.refresh_snapshot(
            session, user.master, client, is_master=True, full=True, drop_adult=user.ignore_adult_titles
        )
    entries = await snapshots.load(session, user.master.id)
    return [ListEntryOut.model_validate(e) for e in sorted(entries, key=lambda e: e.title.lower())]


@router.post(
    "/list/refresh",
    dependencies=[ratelimit.per_user("list-refresh", limit=6, window=timedelta(minutes=5))],
)
async def refresh_list(user: CurrentUser, session: Session) -> RefreshOut:
    """Pick up changes made on the master's own site now (one small request; the scheduled sync does it daily)."""
    client = await _master_client(user, session)
    read = await sync.refresh_snapshot(
        session, user.master, client, is_master=True, drop_adult=user.ignore_adult_titles
    )
    return RefreshOut(mode=read.mode, changed=len(read.received))


class EntryIn(BaseModel):
    status: Status
    progress: int = Field(0, ge=0)
    score: int | None = Field(None, ge=0, le=100)
    rewatching: bool = False
    started_at: date | None = None
    finished_at: date | None = None


@router.put("/list/{media_id}")
async def save_entry(media_id: str, body: EntryIn, user: CurrentUser, session: Session) -> SavedEntryOut:
    """Edit the master list, then push the change to every mirror in the background."""
    client = await _master_client(user, session)
    master = user.master
    known = await snapshots.get(session, master.id, media_id)
    entry = ListEntry(
        media_id=media_id,
        external_ids=known.external_ids if known else {master.provider: media_id},
        title=known.title if known else "",
        total_episodes=known.total_episodes if known else None,
        image_url=known.image_url if known else None,
        remote_entry_id=known.remote_entry_id if known else None,
        **body.model_dump(),
    )
    await client.save_entry(entry)
    await snapshots.upsert(session, master.id, [entry], get_provider(master.provider).score_step)
    await session.commit()
    for mirror in user.mirrors:
        await jobs.push_entry.defer_async(
            account_id=mirror.id,
            master_provider=master.provider,
            master_media_id=media_id,
            entry=entry.to_dict(),
        )
    return SavedEntryOut(queued_mirrors=len(user.mirrors))


@router.delete("/list/{media_id}")
async def delete_entry(media_id: str, user: CurrentUser, session: Session) -> OkOut:
    client = await _master_client(user, session)
    known = await snapshots.get(session, user.master.id, media_id)
    await client.delete_entry(media_id, known.remote_entry_id if known else None)
    await snapshots.remove(session, user.master.id, [media_id])
    await session.commit()
    for mirror in user.mirrors:
        await jobs.push_entry.defer_async(
            account_id=mirror.id, master_provider=user.master.provider, master_media_id=media_id, entry=None
        )
    return OkOut()


# ---- sync ---------------------------------------------------------------------------------


@router.post("/sync")
async def trigger_sync(user: CurrentUser, session: Session) -> QueuedOut:
    if user.master is None:
        raise HTTPException(400, "Pick a master list first")
    if await sync.running_run(session, user.id) is not None:
        raise HTTPException(409, "A sync is already running. It will show up under recent activity when it's done")
    queued = await jobs.enqueue_user_sync(user.id, "manual")
    return QueuedOut(queued=queued)


@router.get("/sync/preview", response_model_exclude_none=True)
async def preview_sync(user: CurrentUser, session: Session) -> dict[str, MirrorResultOut]:
    """Dry run: what a sync would change on each mirror, without writing anything."""
    if user.master is None:
        raise HTTPException(400, "Pick a master list first")
    try:
        return await sync.run_sync(session, user.id, "preview", dry_run=True)
    except ReauthRequired as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/sync/runs", response_model_exclude_none=True)
async def sync_runs(user: CurrentUser, session: Session) -> list[SyncRunOut]:
    runs = await session.scalars(
        select(SyncRun).where(SyncRun.user_id == user.id).order_by(SyncRun.started_at.desc()).limit(10)
    )
    return [SyncRunOut.model_validate(r) for r in runs]
