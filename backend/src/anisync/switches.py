"""Admin-controlled switches: paused providers, paused scheduled syncs, maintenance mode and the site banner."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from anisync.models import ProviderSetting, SiteSettings


async def site(session: AsyncSession) -> SiteSettings:
    """The site settings row, created with the defaults on first use."""
    row = await session.get(SiteSettings, 1)
    if row is None:
        await session.execute(pg_insert(SiteSettings).values(id=1).on_conflict_do_nothing())
        await session.commit()
        row = await session.get(SiteSettings, 1)
    if row is None:  # can't happen: we just inserted it (or someone else did)
        raise RuntimeError("site settings row is missing")
    return row


async def paused_providers(session: AsyncSession) -> dict[str, str | None]:
    """{provider id: reason} for every paused provider."""
    rows = await session.execute(
        select(ProviderSetting.provider, ProviderSetting.paused_reason).where(ProviderSetting.paused.is_(True))
    )
    return dict(rows.all())


async def background_work_allowed(session: AsyncSession) -> bool:
    """False while scheduled syncs are paused or the site is in maintenance (jobs then skip their work)."""
    settings = await site(session)
    return not (settings.scheduled_syncs_paused or settings.maintenance)
