"""Removing a user and everything we hold about them. Used by the admin and by account self-service.

The database cascades do the work: linked accounts (and with them tokens, list snapshots), sessions and sync runs
(and their events) go with the user. Admin audit rows stay but lose their link to the user, so the details of what
was done are written *before* the delete by the caller. Provider tokens are not revoked at the provider (none of the
three has an API for that); they are gone from our database, and users can revoke AniSyncHub in their provider settings.
"""

from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from anisync.models import LinkedAccount, SyncRunEvent, UnmatchedTitleUser, User


async def delete_user(session: AsyncSession, user: User) -> None:
    await session.delete(user)
    await session.commit()


async def forget_account(session: AsyncSession, user: User, account: LinkedAccount) -> None:
    """Everything we hold about one unlinked account: the account row cascades its tokens and list snapshots; this also
    drops what is keyed by user and provider rather than by account (unmatched-title rows) and the account's sync log
    events (they name titles). The caller commits."""
    await session.execute(delete(SyncRunEvent).where(SyncRunEvent.account_id == account.id))
    await session.execute(
        delete(UnmatchedTitleUser).where(
            UnmatchedTitleUser.user_id == user.id,
            or_(UnmatchedTitleUser.source == account.provider, UnmatchedTitleUser.target == account.provider),
        )
    )
