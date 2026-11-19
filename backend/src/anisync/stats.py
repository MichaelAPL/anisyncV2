"""Numbers for the admin overview: users, linked accounts, sync outcomes and provider request counts.

Everything is computed from indexed tables on request. Sync runs are kept for 30 days (`purge_runs`), so the daily
series stays cheap; add a rollup table only if the overview ever gets slow.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Date, case, cast, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anisync.models import LinkedAccount, ProviderStat, SyncRun, User, UserSession

EXPIRING_SOON = timedelta(days=30)


@dataclass
class ProviderAccounts:
    provider: str
    accounts: int
    masters: int
    needs_relink: int  # token expired and it can't be refreshed
    expiring_soon: int  # token expires within 30 days and it can't be refreshed


@dataclass
class Overview:
    users_total: int
    users_with_master: int
    new_today: int
    new_7d: int
    active_7d: int
    active_30d: int
    banned: int
    providers: list[ProviderAccounts]


@dataclass
class Day:
    day: date
    new_users: int = 0
    runs_ok: int = 0
    runs_error: int = 0
    changes: int = 0


@dataclass
class DayProviderRequests:
    day: date
    provider: str
    requests: int
    rate_limited: int
    errors: int
    avg_ms: int


def utc_day(column):
    return cast(func.timezone("UTC", column), Date)


async def overview(session: AsyncSession, now: datetime | None = None) -> Overview:
    now = now or datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async def users_where(*conditions) -> int:
        return await session.scalar(select(func.count()).select_from(User).where(*conditions)) or 0

    async def active_since(days: int) -> int:
        return (
            await session.scalar(
                select(func.count(distinct(UserSession.user_id))).where(
                    UserSession.last_seen_at >= now - timedelta(days=days)
                )
            )
            or 0
        )

    cannot_refresh = LinkedAccount._refresh_token.is_(None)  # noqa: SLF001 (the encrypted column; the value is never read)
    rows = await session.execute(
        select(
            LinkedAccount.provider,
            func.count(),
            func.count(User.id),
            func.count(case((cannot_refresh & (LinkedAccount.expires_at < now), 1))),
            func.count(
                case(
                    (
                        cannot_refresh
                        & (LinkedAccount.expires_at >= now)
                        & (LinkedAccount.expires_at < now + EXPIRING_SOON),
                        1,
                    )
                )
            ),
        )
        .select_from(LinkedAccount)
        .join(User, User.master_account_id == LinkedAccount.id, isouter=True)
        .group_by(LinkedAccount.provider)
        .order_by(LinkedAccount.provider)
    )
    return Overview(
        users_total=await users_where(),
        users_with_master=await users_where(User.master_account_id.is_not(None)),
        new_today=await users_where(User.created_at >= today),
        new_7d=await users_where(User.created_at >= today - timedelta(days=6)),
        active_7d=await active_since(7),
        active_30d=await active_since(30),
        banned=await users_where(User.banned_at.is_not(None)),
        providers=[ProviderAccounts(*row) for row in rows],
    )


async def daily(
    session: AsyncSession, days: int = 14, now: datetime | None = None
) -> tuple[list[Day], list[DayProviderRequests]]:
    """The last `days` UTC days, oldest first, with days that had no activity included as zeros."""
    now = now or datetime.now(UTC)
    first = now.date() - timedelta(days=days - 1)
    series = {first + timedelta(days=i): Day(first + timedelta(days=i)) for i in range(days)}
    start = datetime.combine(first, datetime.min.time(), tzinfo=UTC)

    created = utc_day(User.created_at)
    signups = await session.execute(select(created, func.count()).where(User.created_at >= start).group_by(created))
    for day, count in signups:
        series[day].new_users = count
    started = utc_day(SyncRun.started_at)
    runs = await session.execute(
        select(
            started,
            func.count(case((SyncRun.status == "ok", 1))),
            func.count(case((SyncRun.status == "error", 1))),
            func.coalesce(func.sum(SyncRun.changes), 0),
        )
        .where(SyncRun.started_at >= start)
        .group_by(started)
    )
    for day, ok, error, changes in runs:
        series[day].runs_ok, series[day].runs_error, series[day].changes = ok, error, int(changes)

    requests = await session.scalars(
        select(ProviderStat).where(ProviderStat.day >= first).order_by(ProviderStat.day, ProviderStat.provider)
    )
    return list(series.values()), [
        DayProviderRequests(
            r.day, r.provider, r.requests, r.rate_limited, r.errors, round(r.total_ms / r.requests) if r.requests else 0
        )
        for r in requests
    ]
