from datetime import UTC, date, datetime, timedelta

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from anisync import permissions
from anisync.crypto import decrypt, encrypt
from anisync.db import Base
from anisync.providers.registry import get_provider


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100))
    # The linked account whose list is the source of truth. Mirrors are overwritten to match it.
    master_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("linked_accounts.id", ondelete="SET NULL", use_alter=True, name="fk_users_master_account_id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # What the user may do is decided by `permissions.ROLE_PERMISSIONS`; the column only names the role.
    role: Mapped[str] = mapped_column(String(32), default=permissions.DEFAULT_ROLE, server_default="user")
    banned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ban_reason: Mapped[str | None] = mapped_column(String(500))
    banned_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Profile picture: a provider's image URL (hotlinked, https on that provider's domains), chosen by the user.
    # It does not follow master changes. None = show initials.
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    avatar_provider: Mapped[str | None] = mapped_column(String(32))

    accounts: Mapped[list["LinkedAccount"]] = relationship(
        back_populates="user", foreign_keys="LinkedAccount.user_id", cascade="all, delete-orphan", lazy="selectin"
    )

    @validates("role")
    def _known_role(self, _key: str, role: str) -> str:
        if not permissions.is_known_role(role):
            raise ValueError(f"unknown role {role!r}")
        return role

    @property
    def permissions(self) -> frozenset[str]:
        return permissions.permissions_for(self.role)

    @property
    def is_banned(self) -> bool:
        return self.banned_at is not None

    @property
    def master(self) -> "LinkedAccount | None":
        return next((a for a in self.accounts if a.id == self.master_account_id), None)

    @property
    def mirrors(self) -> list["LinkedAccount"]:
        return [a for a in self.accounts if a.id != self.master_account_id and a.sync_enabled]


EXPIRY_WARNING = timedelta(days=30)


class LinkedAccount(Base):
    """A user's account on one list provider, with its (encrypted) OAuth tokens."""

    __tablename__ = "linked_accounts"
    __table_args__ = (UniqueConstraint("provider", "remote_user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Provider ID, validated against the provider registry (a plain string, so new providers need no migration).
    provider: Mapped[str] = mapped_column(String(32))
    remote_user_id: Mapped[str] = mapped_column(String(64))
    remote_username: Mapped[str] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(String(500))  # this account's profile picture as last seen
    _access_token: Mapped[str] = mapped_column("access_token", Text)
    _refresh_token: Mapped[str | None] = mapped_column("refresh_token", Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Mirror-only settings.
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Remove entries from this mirror that are not on the master list.
    prune_missing: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Reading the list: `sync_cursor` = the newest provider-side update time we have seen (incremental reads ask only
    # for entries newer than it); `last_full_read_at` = when the whole list was last read (a full read is also what
    # notices removals and edits made directly on the provider's site, so it happens at least weekly).
    sync_cursor: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_full_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="accounts", foreign_keys=[user_id])

    @validates("provider")
    def _validate_provider(self, _key: str, value: str) -> str:
        get_provider(value)  # raises UnknownProvider for unregistered IDs
        return value

    @property
    def access_token(self) -> str:
        return decrypt(self._access_token)  # type: ignore[return-value]

    @access_token.setter
    def access_token(self, value: str) -> None:
        self._access_token = encrypt(value)  # type: ignore[assignment]

    @property
    def refresh_token(self) -> str | None:
        return decrypt(self._refresh_token)

    @refresh_token.setter
    def refresh_token(self, value: str | None) -> None:
        self._refresh_token = encrypt(value)

    @property
    def cannot_renew(self) -> bool:
        """True when an expired connection can't be refreshed by us (no refresh token, or the provider has none)."""
        return self.refresh_token is None or get_provider(self.provider).refresh is None

    def needs_relink(self, now: datetime | None = None) -> bool:
        return bool(self.expires_at and self.expires_at < (now or datetime.now(UTC)) and self.cannot_renew)

    def expires_soon(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        return bool(self.expires_at and now <= self.expires_at < now + EXPIRY_WARNING and self.cannot_renew)


class Media(Base):
    """One anime, independent of any provider. Provider IDs hang off it via `MediaExternalId`."""

    __tablename__ = "media"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    episodes: Mapped[int | None] = mapped_column(Integer)
    cover_url: Mapped[str | None] = mapped_column(Text)
    cover_color: Mapped[str | None] = mapped_column(String(9))  # "#RRGGBB" tint to show while the cover loads
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MediaExternalId(Base):
    """A provider's ID for a `Media`. Every provider's ID is unique, and a media has at most one ID per provider."""

    __tablename__ = "media_external_ids"
    __table_args__ = (UniqueConstraint("media_id", "provider"),)

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"))
    # Where the link came from: "seed" (offline dataset), "learned" (a provider told us) or "manual" (an admin).
    # Imports never overwrite or remove manual links.
    source: Mapped[str] = mapped_column(String(16), default="learned")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @validates("provider")
    def _validate_provider(self, _key: str, value: str) -> str:
        get_provider(value)
        return value


class LookupMiss(Base):
    """A title ID that no provider could translate recently, so we don't ask again until `checked_at` is old."""

    __tablename__ = "lookup_misses"

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target: Mapped[str] = mapped_column(String(32), primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class ListSnapshot(Base):
    """What we last read from, or wrote to, one linked account for one title (used from Phase 3 on)."""

    __tablename__ = "list_snapshots"

    account_id: Mapped[int] = mapped_column(ForeignKey("linked_accounts.id", ondelete="CASCADE"), primary_key=True)
    # The title's ID on the account's provider. Always set; `media_id` is only set once the title is mapped.
    external_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"), index=True)
    remote_entry_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[int | None] = mapped_column(Integer)  # 0-100
    rewatching: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[date | None] = mapped_column(Date)
    finished_at: Mapped[date | None] = mapped_column(Date)
    remote_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Hash of the synced fields as last read from or written to the provider; a different hash at the next full read
    # means the entry was edited directly on the provider's site.
    last_written_hash: Mapped[str | None] = mapped_column(String(64))
    # Display data and ID hints, so the list can be served (and prune safety checked) without asking the provider.
    title: Mapped[str] = mapped_column(Text, default="")
    total_episodes: Mapped[int | None] = mapped_column(Integer)
    image_url: Mapped[str | None] = mapped_column(Text)
    external_ids: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DatasetImport(Base):
    """One run of an offline ID dataset import (also feeds the admin "dataset status" view)."""

    __tablename__ = "dataset_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str | None] = mapped_column(String(64))  # the dataset's own "last update" marker
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | unchanged | error
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(20))  # scheduled | manual | edit
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | ok | error
    # {"<account_id>": {"created": n, "updated": n, "deleted": n, "unmapped": [...], "errors": [...]}}
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    changes: Mapped[int] = mapped_column(default=0, server_default="0")  # entries created, updated or deleted


class SyncRunEvent(Base):
    """A notable thing that happened in a sync run: a failed change, a re-auth request, a rate limit, unmapped titles.
    Never one row per synced entry, so the table stays small. Deleted with its run (retention follows `purge_runs`)."""

    __tablename__ = "sync_run_events"
    __table_args__ = (Index("ix_sync_run_events_kind_created", "kind", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("sync_runs.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("linked_accounts.id", ondelete="SET NULL"))
    provider: Mapped[str | None] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(10))  # info | warning | error
    kind: Mapped[str] = mapped_column(String(24))  # change_failed | rate_limited | reauth | unmapped | drifted
    message: Mapped[str] = mapped_column(String(500))
    media_ref: Mapped[str | None] = mapped_column(String(64))  # the title id on the provider it concerns
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class UserSession(Base):
    """A signed-in browser. The cookie holds a random token; only its SHA-256 is stored, so a database leak
    doesn't hand out working sessions."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    user_agent: Mapped[str] = mapped_column(String(255), default="")


class RateLimitHit(Base):
    """One attempt at a rate-limited action. `key` names the action and who did it (see `ratelimit.py`)."""

    __tablename__ = "rate_limit_hits"
    __table_args__ = (Index("ix_rate_limit_hits_key_created_at", "key", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class AdminAction(Base):
    """Audit trail of what admins did (bans, role changes, ...). Never edited; survives the deletion of its users."""

    __tablename__ = "admin_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(64))
    target_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    note: Mapped[str | None] = mapped_column(String(500))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProviderStat(Base):
    """Requests we made to a provider on one UTC day (see `metrics.py`)."""

    __tablename__ = "provider_stats"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    requests: Mapped[int] = mapped_column(default=0)
    rate_limited: Mapped[int] = mapped_column(default=0)  # answered with HTTP 429
    errors: Mapped[int] = mapped_column(default=0)  # HTTP 4xx/5xx other than 429, or no answer at all
    total_ms: Mapped[int] = mapped_column(default=0)  # sum of response times, for the average


class SiteSettings(Base):
    """Switches an admin can flip while the site runs. One row (id 1), created on first use."""

    __tablename__ = "site_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    scheduled_syncs_paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    maintenance: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    maintenance_message: Mapped[str | None] = mapped_column(String(500))
    banner_text: Mapped[str | None] = mapped_column(String(500))  # plain text; the UI must never render it as HTML
    banner_level: Mapped[str] = mapped_column(String(10), default="info", server_default="info")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(UTC)
    )
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ProviderSetting(Base):
    """Per-provider kill switch: a paused provider is skipped by syncs and edit pushes, and users are told."""

    __tablename__ = "provider_settings"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    paused_reason: Mapped[str | None] = mapped_column(String(500))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    changed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
