"""Orchestration: load accounts, build clients (refreshing tokens), run plans against mirrors."""

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anisync.engine import Plan, plan_sync
from anisync.mapping import IdMapper
from anisync.models import LinkedAccount, SyncRun, User
from anisync.providers import get_provider
from anisync.providers.base import ListEntry, ProviderClient, ReauthRequired

log = logging.getLogger(__name__)


async def client_for(account: LinkedAccount, session: AsyncSession) -> ProviderClient:
    provider = get_provider(account.provider)
    if account.expires_at and account.expires_at < datetime.now(UTC) + timedelta(minutes=5):
        # Providers without a refresh hook (or an account without a refresh token) need the user to re-link.
        if provider.refresh is None or not account.refresh_token:
            raise ReauthRequired(f"{provider.name} token for {account.remote_username} expired; re-link needed")
        tokens = await provider.refresh(account.refresh_token)
        account.access_token = tokens.access_token
        account.refresh_token = tokens.refresh_token or account.refresh_token
        account.expires_at = tokens.expires_at
        await session.commit()
    return provider.client(account.access_token, account.remote_user_id)


async def plan_for_mirror(
    session: AsyncSession, master: LinkedAccount, master_list: list[ListEntry], mirror: LinkedAccount
) -> tuple[ProviderClient, Plan]:
    client = await client_for(mirror, session)
    mirror_list = await client.fetch_list()
    async with IdMapper(session) as mapper:
        await mapper.learn_from(master_list)
        await mapper.learn_from(mirror_list)
        id_map = await mapper.resolve(master.provider, mirror.provider, [e.media_id for e in master_list])
    await session.commit()
    return client, plan_sync(master_list, mirror_list, id_map, client.score_step, mirror.prune_missing)


async def apply_plan(client: ProviderClient, plan: Plan) -> list[str]:
    errors = []
    for change in plan.changes:
        try:
            if change.op == "delete":
                await client.delete_entry(change.media_id, change.current.remote_entry_id if change.current else None)
            else:
                await client.save_entry(change.desired)
        except ReauthRequired:
            raise
        except Exception as exc:  # keep going; one bad entry shouldn't block the rest
            log.warning("sync change failed: %s %s: %s", change.op, change.media_id, exc)
            errors.append(f"{change.op} {change.media_id}: {exc}")
    return errors


async def run_sync(session: AsyncSession, user_id: int, trigger: str, dry_run: bool = False) -> dict:
    user = await session.get(User, user_id)
    if user is None or user.master is None:
        return {"skipped": "no master list selected"}
    run = None
    if not dry_run:
        run = SyncRun(user_id=user.id, trigger=trigger, summary={})
        session.add(run)
        await session.commit()

    master = user.master
    summary: dict = {}
    master_list = await (await client_for(master, session)).fetch_list()
    for mirror in user.mirrors:
        try:
            client, plan = await plan_for_mirror(session, master, master_list, mirror)
            result: dict = plan.counts() | {"unmapped_titles": [e.title for e in plan.unmapped][:50]}
            if dry_run:
                result["changes"] = [
                    {"op": c.op, "media_id": c.media_id, "fields": c.fields, "title": (c.desired or c.current).title}
                    for c in plan.changes
                ]
            else:
                result["errors"] = await apply_plan(client, plan)
                mirror.last_synced_at = datetime.now(UTC)
        except ReauthRequired as exc:
            result = {"error": str(exc), "reauth_required": True}
        summary[str(mirror.id)] = {"provider": mirror.provider, "username": mirror.remote_username} | result

    if run is not None:
        run.summary = summary
        run.status = "error" if any("error" in s for s in summary.values()) else "ok"
        run.finished_at = datetime.now(UTC)
        await session.commit()
    return summary


async def push_entry(
    session: AsyncSession, account_id: int, master_provider: str, master_media_id: str, entry: ListEntry | None
) -> None:
    """Propagate one master-list edit to one mirror. entry=None means it was removed from the master."""
    account = await session.get(LinkedAccount, account_id)
    if account is None or not account.sync_enabled:
        return
    async with IdMapper(session) as mapper:
        if entry is not None:
            await mapper.learn_from([entry])
        target_id = (await mapper.resolve(master_provider, account.provider, [master_media_id]))[master_media_id]
    await session.commit()
    if target_id is None:
        log.info("no %s ID for %s title %s; skipping push", account.provider, master_provider, master_media_id)
        return
    client = await client_for(account, session)
    if entry is None:
        if account.prune_missing:
            await client.delete_entry(target_id)
    else:
        await client.save_entry(replace(entry, media_id=target_id, remote_entry_id=None))


async def users_with_master(session: AsyncSession) -> list[int]:
    return list(await session.scalars(select(User.id).where(User.master_account_id.is_not(None))))
