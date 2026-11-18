from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

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

    accounts: Mapped[list["LinkedAccount"]] = relationship(
        back_populates="user", foreign_keys="LinkedAccount.user_id", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def master(self) -> "LinkedAccount | None":
        return next((a for a in self.accounts if a.id == self.master_account_id), None)

    @property
    def mirrors(self) -> list["LinkedAccount"]:
        return [a for a in self.accounts if a.id != self.master_account_id and a.sync_enabled]


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
    _access_token: Mapped[str] = mapped_column("access_token", Text)
    _refresh_token: Mapped[str | None] = mapped_column("refresh_token", Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Mirror-only settings.
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Remove entries from this mirror that are not on the master list.
    prune_missing: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    # Hash of the synced fields at our last write; if the provider's copy differs, it was edited elsewhere.
    last_written_hash: Mapped[str | None] = mapped_column(String(64))
    # Display data and ID hints, so the list can be served (and prune safety checked) without asking the provider.
    title: Mapped[str] = mapped_column(Text, default="")
    total_episodes: Mapped[int | None] = mapped_column(Integer)
    image_url: Mapped[str | None] = mapped_column(Text)
    external_ids: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(20))  # "daily" | "manual"
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | ok | error
    # {"<account_id>": {"created": n, "updated": n, "deleted": n, "unmapped": [...], "errors": [...]}}
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
