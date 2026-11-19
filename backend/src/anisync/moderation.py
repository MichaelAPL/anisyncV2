"""What admins do to users: bans and role changes. Every action is written to `admin_actions`.

Raises `ModerationError` with the HTTP status to answer with; the routes only translate it.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anisync import permissions, sessions
from anisync.config import get_settings
from anisync.models import AdminAction, LinkedAccount, User


class ModerationError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


async def record(
    session: AsyncSession,
    actor: User,
    action: str,
    target: User | None = None,
    note: str | None = None,
    **details: object,
) -> None:
    session.add(
        AdminAction(
            actor_id=actor.id, action=action, target_user_id=target.id if target else None, note=note, details=details
        )
    )


async def _target(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise ModerationError(404, "User not found")
    return user


async def ban(session: AsyncSession, actor: User, user_id: int, reason: str) -> User:
    """Ban a user: they are signed out everywhere and can't sign in again; syncs skip them. Their data stays."""
    target = await _target(session, user_id)
    if target.id == actor.id:
        raise ModerationError(400, "You can't ban yourself")
    if permissions.ROLES_MANAGE in target.permissions:
        raise ModerationError(409, "Change this user's role before banning them")
    if target.is_banned:
        raise ModerationError(409, "Already banned")
    target.banned_at = datetime.now(UTC)
    target.ban_reason = reason
    target.banned_by = actor.id
    await record(session, actor, "user.ban", target, reason)
    await session.commit()
    await sessions.end_all(session, target.id)
    return target


async def unban(session: AsyncSession, actor: User, user_id: int, note: str | None = None) -> User:
    target = await _target(session, user_id)
    if not target.is_banned:
        raise ModerationError(409, "Not banned")
    target.banned_at = target.ban_reason = target.banned_by = None
    await record(session, actor, "user.unban", target, note)
    await session.commit()
    return target


async def role_managers(session: AsyncSession) -> int:
    roles = [r for r, perms in permissions.ROLE_PERMISSIONS.items() if permissions.ROLES_MANAGE in perms]
    return (
        await session.scalar(
            select(func.count()).select_from(User).where(User.role.in_(roles), User.banned_at.is_(None))
        )
        or 0
    )


async def set_role(session: AsyncSession, actor: User, user_id: int, role: str) -> User:
    if not permissions.is_known_role(role):
        raise ModerationError(422, f"Unknown role {role!r}")
    target = await _target(session, user_id)
    if target.role == role:
        return target
    loses_it = (
        permissions.ROLES_MANAGE in target.permissions
        and permissions.ROLES_MANAGE not in permissions.permissions_for(role)
    )
    if loses_it and await role_managers(session) <= 1:
        raise ModerationError(409, "The last user who can manage roles can't lose that permission")
    if target.is_banned and permissions.permissions_for(role):
        raise ModerationError(409, "Unban this user before giving them a role")
    previous = target.role
    target.role = role
    await record(session, actor, "user.role", target, previous=previous, new=role)
    await session.commit()
    return target


async def bootstrap_admins(session: AsyncSession) -> int:
    """Make the users named in `ADMIN_USER_IDS` admins (those that exist and aren't banned). Never demotes."""
    ids = get_settings().admin_ids
    if not ids:
        return 0
    users = await session.scalars(select(User).where(User.id.in_(ids), User.role != "admin", User.banned_at.is_(None)))
    promoted = 0
    for user in users:
        user.role = "admin"
        session.add(
            AdminAction(
                actor_id=None,
                action="user.role",
                target_user_id=user.id,
                details={"previous": "user", "new": "admin", "via": "ADMIN_USER_IDS"},
            )
        )
        promoted += 1
    if promoted:
        await session.commit()
    return promoted


def revoke_tokens(account: LinkedAccount) -> None:
    """Throw away the stored tokens so the next use asks the user to link the account again."""
    account.access_token = "revoked"  # noqa: S105 (a placeholder, not a credential)
    account.refresh_token = None
    account.expires_at = datetime.now(UTC) - timedelta(seconds=1)


async def unlink(session: AsyncSession, user: User, account: LinkedAccount) -> None:
    """Remove a linked account (tokens and our copy of its list). A master that goes is replaced by another account."""
    if user.is_banned:
        # Unlinking would free the provider account to sign up again as a new, unbanned user.
        raise ModerationError(409, "Unban this user first: their accounts stay linked while they are banned")
    if user.master_account_id == account.id:
        others = sorted((a for a in user.accounts if a.id != account.id), key=lambda a: a.id)
        user.master_account_id = others[0].id if others else None
        await session.flush()
    await session.delete(account)
    await session.commit()
    await session.refresh(user, ["accounts"])
