from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from anisync.crypto import decrypt, encrypt
from anisync.db import Base


class Service(StrEnum):
    MAL = "mal"
    ANILIST = "anilist"
    KITSU = "kitsu"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100))
    # The linked account whose list is the source of truth. Mirrors are overwritten to match it.
    master_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("linked_accounts.id", ondelete="SET NULL", use_alter=True)
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
    """A user's account on one anime list service, with its (encrypted) OAuth tokens."""

    __tablename__ = "linked_accounts"
    __table_args__ = (UniqueConstraint("service", "remote_user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    service: Mapped[Service] = mapped_column(
        Enum(Service, name="service", values_callable=lambda e: [m.value for m in e])
    )
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


class AnimeIds(Base):
    """Cross-service ID mapping cache. Filled lazily from AniList idMal / Kitsu mappings."""

    __tablename__ = "anime_ids"

    id: Mapped[int] = mapped_column(primary_key=True)
    mal_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    anilist_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    kitsu_id: Mapped[int | None] = mapped_column(Integer, unique=True)
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
