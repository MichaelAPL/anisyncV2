"""Provider request counters for the admin stats: how many calls we made to each provider, how many were rate
limited or failed, and how long they took.

Counting happens in memory (`record`, called by every provider request) and is written to `provider_stats` in
batches (`flush`, every few minutes and at shutdown), so a request never waits for the database. Rows are added to,
not replaced, so several processes can flush into the same day.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class Counts:
    requests: int = 0
    rate_limited: int = 0
    errors: int = 0
    total_ms: int = 0


_pending: dict[tuple[date, str], Counts] = {}


def record(provider: str, status: int | None, seconds: float) -> None:
    """Count one provider request. `status` is the HTTP status, or None when the request never got an answer."""
    counts = _pending.setdefault((datetime.now(UTC).date(), provider), Counts())
    counts.requests += 1
    counts.total_ms += round(seconds * 1000)
    if status == 429:
        counts.rate_limited += 1
    elif status is None or status >= 400:
        counts.errors += 1


def pending() -> dict[tuple[date, str], Counts]:
    return dict(_pending)


def reset() -> None:
    _pending.clear()


async def flush(session: AsyncSession) -> int:
    """Write the counters collected so far to the database. Returns how many requests were stored."""
    from anisync.models import (
        ProviderStat,
    )  # here, not at the top: providers import this module, models import providers

    batch = dict(_pending)
    _pending.clear()
    stored = 0
    try:
        for (day, provider), c in batch.items():
            stmt = pg_insert(ProviderStat).values(
                day=day,
                provider=provider,
                requests=c.requests,
                rate_limited=c.rate_limited,
                errors=c.errors,
                total_ms=c.total_ms,
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["day", "provider"],
                    set_={
                        "requests": ProviderStat.requests + stmt.excluded.requests,
                        "rate_limited": ProviderStat.rate_limited + stmt.excluded.rate_limited,
                        "errors": ProviderStat.errors + stmt.excluded.errors,
                        "total_ms": ProviderStat.total_ms + stmt.excluded.total_ms,
                    },
                )
            )
            stored += c.requests
        await session.commit()
    except Exception:
        await session.rollback()
        for key, c in batch.items():  # keep what we counted; the next flush tries again
            back = _pending.setdefault(key, Counts())
            back.requests += c.requests
            back.rate_limited += c.rate_limited
            back.errors += c.errors
            back.total_ms += c.total_ms
        raise
    return stored
