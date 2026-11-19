"""Roles and permissions.

Endpoints and the UI check *permissions*, never role names, so a new role is one more entry in `ROLE_PERMISSIONS`
and touches no endpoint. A user has one role (`users.role`); an unknown role grants nothing.
"""

STATS_READ = "stats.read"
SYNCS_READ = "syncs.read"
SYNCS_CONTROL = "syncs.control"  # pause/resume scheduled syncs, maintenance mode
SYNCS_RETRY = "syncs.retry"
USERS_READ = "users.read"
USERS_BAN = "users.ban"
USERS_DELETE = "users.delete"
USERS_MANAGE_TOKENS = "users.manage_tokens"
ROLES_MANAGE = "roles.manage"
TITLES_LINK = "titles.link"
PROVIDERS_MANAGE = "providers.manage"
SITE_MANAGE = "site.manage"

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        STATS_READ,
        SYNCS_READ,
        SYNCS_CONTROL,
        SYNCS_RETRY,
        USERS_READ,
        USERS_BAN,
        USERS_DELETE,
        USERS_MANAGE_TOKENS,
        ROLES_MANAGE,
        TITLES_LINK,
        PROVIDERS_MANAGE,
        SITE_MANAGE,
    }
)

DEFAULT_ROLE = "user"
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    DEFAULT_ROLE: frozenset(),
    "admin": ALL_PERMISSIONS,
}


def permissions_for(role: str) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def is_known_role(role: str) -> bool:
    return role in ROLE_PERMISSIONS
