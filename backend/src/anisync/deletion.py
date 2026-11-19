"""Removing a user and everything we hold about them. Used by the admin and by account self-service.

The database cascades do the work: linked accounts (and with them tokens, list snapshots), sessions and sync runs
(and their events) go with the user. Admin audit rows stay but lose their link to the user, so the details of what
was done are written *before* the delete by the caller. Provider tokens are not revoked at the provider (none of the
three has an API for that); they are gone from our database, and users can revoke AniSync in their provider settings.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from anisync.models import User


async def delete_user(session: AsyncSession, user: User) -> None:
    await session.delete(user)
    await session.commit()
