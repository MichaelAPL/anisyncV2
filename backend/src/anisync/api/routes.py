from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from anisync import jobs, sessions, sync
from anisync.api.deps import CurrentUser, Session
from anisync.api.schemas import OkOut
from anisync.models import LinkedAccount, SyncRun, User, UserSession
from anisync.providers.base import ListEntry, ReauthRequired, Status

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


class MeOut(BaseModel):
    id: int
    display_name: str
    master_account_id: int | None
    accounts: list[AccountOut]


def _me(user: User) -> MeOut:
    return MeOut(
        id=user.id,
        display_name=user.display_name,
        master_account_id=user.master_account_id,
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
async def health() -> dict:
    return {"ok": True}


@router.get("/me")
async def me(user: CurrentUser) -> MeOut:
    return _me(user)


class MasterIn(BaseModel):
    account_id: int


@router.put("/me/master")
async def set_master(body: MasterIn, user: CurrentUser, session: Session) -> MeOut:
    user.master_account_id = _account(user, body.account_id).id
    await session.commit()
    return _me(user)


class AccountPatch(BaseModel):
    sync_enabled: bool | None = None
    prune_missing: bool | None = None


@router.patch("/accounts/{account_id}")
async def update_account(account_id: int, body: AccountPatch, user: CurrentUser, session: Session) -> MeOut:
    account = _account(user, account_id)
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(account, key, value)
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
    try:
        return await sync.client_for(user.master, session)
    except ReauthRequired as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/list")
async def get_list(user: CurrentUser, session: Session) -> list[dict]:
    client = await _master_client(user, session)
    entries = await client.fetch_list()
    return [e.to_dict() for e in sorted(entries, key=lambda e: e.title.lower())]


class EntryIn(BaseModel):
    status: Status
    progress: int = Field(0, ge=0)
    score: int | None = Field(None, ge=0, le=100)
    rewatching: bool = False
    started_at: date | None = None
    finished_at: date | None = None


@router.put("/list/{media_id}")
async def save_entry(media_id: str, body: EntryIn, user: CurrentUser, session: Session) -> dict:
    """Edit the master list, then push the change to every mirror in the background."""
    client = await _master_client(user, session)
    entry = ListEntry(media_id=media_id, external_ids={user.master.provider: media_id}, **body.model_dump())
    await client.save_entry(entry)
    for mirror in user.mirrors:
        await jobs.push_entry.defer_async(
            account_id=mirror.id,
            master_provider=user.master.provider,
            master_media_id=media_id,
            entry=entry.to_dict(),
        )
    return {"ok": True, "queued_mirrors": len(user.mirrors)}


@router.delete("/list/{media_id}")
async def delete_entry(media_id: str, user: CurrentUser, session: Session) -> dict:
    client = await _master_client(user, session)
    await client.delete_entry(media_id)
    for mirror in user.mirrors:
        await jobs.push_entry.defer_async(
            account_id=mirror.id, master_provider=user.master.provider, master_media_id=media_id, entry=None
        )
    return {"ok": True}


# ---- sync ---------------------------------------------------------------------------------


@router.post("/sync")
async def trigger_sync(user: CurrentUser) -> dict:
    if user.master is None:
        raise HTTPException(400, "Pick a master list first")
    queued = await jobs.enqueue_user_sync(user.id, "manual")
    return {"queued": queued}


@router.get("/sync/preview")
async def preview_sync(user: CurrentUser, session: Session) -> dict:
    """Dry run: what a sync would change on each mirror, without writing anything."""
    if user.master is None:
        raise HTTPException(400, "Pick a master list first")
    try:
        return await sync.run_sync(session, user.id, "preview", dry_run=True)
    except ReauthRequired as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/sync/runs")
async def sync_runs(user: CurrentUser, session: Session) -> list[dict]:
    runs = await session.scalars(
        select(SyncRun).where(SyncRun.user_id == user.id).order_by(SyncRun.started_at.desc()).limit(10)
    )
    return [
        {
            "id": r.id,
            "trigger": r.trigger,
            "status": r.status,
            "summary": r.summary,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
        }
        for r in runs
    ]
