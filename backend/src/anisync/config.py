from functools import lru_cache

from pydantic import PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SESSION_SECRET = "dev-only-change-me"  # noqa: S105 (a development default; production refuses it)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # postgresql://user:pass@host:5432/db — plain libpq URL, shared by SQLAlchemy and Procrastinate.
    database_url: str = "postgresql://anisync:anisync@localhost:5434/anisync"

    # Public origin the browser talks to (the Vite dev server proxies /api and /auth to FastAPI).
    # OAuth redirect URIs are built from it: {public_url}/auth/{service}/callback
    public_url: str = "http://localhost:5173"

    session_secret: str = DEV_SESSION_SECRET  # signs the OAuth state cookie and salts rate-limit keys
    # Fernet key used to encrypt provider tokens at rest. Generate with:
    #   uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: str = ""
    # Comma-separated Fernet keys for rotation: the first encrypts, all decrypt (see crypto.py). Wins over the above.
    token_encryption_keys: str = ""

    # Provider credentials (<PROVIDER>_CLIENT_ID / <PROVIDER>_CLIENT_SECRET) are read by the providers themselves,
    # see `providers.base.credentials`.

    # Offline dataset used to pre-fill the ID mapping (see seed.py), and when to check it for a new version (UTC cron).
    id_dataset_url: str = (
        "https://github.com/cedya77/anime-offline-database/releases/latest/download/anime-offline-database.jsonl"
    )
    id_dataset_cron: str = "0 3 * * 0"

    # Run the Procrastinate worker inside the API process (cheap single-container deploys).
    # Set to false and run `anisync-worker` separately once you need to scale.
    run_worker_in_process: bool = True
    # Cron expression (UTC) of the scheduler tick. It should fire hourly: each tick syncs the users whose hour it is
    # (user id modulo 24), so every user is synced once a day and the load is spread over the day.
    sync_tick_cron: str = "0 * * * *"

    # Comma-separated AniSync user ids that are made admins when they exist (startup and sign-in). Never demotes.
    admin_user_ids: str = ""

    # Closed access: while enabled, only the provider accounts listed in SIGNUP_ALLOWLIST may create an AniSync user.
    # Signed-in users still link more accounts, and accounts already linked still sign in. Unset = on in production.
    signup_allowlist_enabled: bool | None = None
    # Comma-separated `provider:account id` (the provider's stable account id, never the username: usernames change).
    # A refused sign-in logs the `provider:account id` to add here.
    signup_allowlist: str = ""
    _signup_allowlist_ids: frozenset[tuple[str, str]] = PrivateAttr(default=frozenset())

    # One-off donation page linked from the footer; no link while empty. One-off only: no monthly tiers or goals
    # (see ROADMAP → Decisions, funding).
    donation_url: str = ""

    # Error reporting (Sentry); off while empty.
    sentry_dsn: str = ""

    # Built frontend (frontend/dist) to serve from this origin; empty in development, where Vite serves it.
    frontend_dist: str = ""

    @field_validator("public_url")
    @classmethod
    def _no_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")  # callback URLs are built as {public_url}/auth/...

    @property
    def admin_ids(self) -> set[int]:
        return {int(part) for part in self.admin_user_ids.split(",") if part.strip().isdigit()}

    @property
    def signup_allowlist_ids(self) -> frozenset[tuple[str, str]]:
        """SIGNUP_ALLOWLIST as (provider id, account id) pairs."""
        return self._signup_allowlist_ids

    @property
    def is_production(self) -> bool:
        """Served over HTTPS = a real deployment (local development runs on http://localhost)."""
        return self.public_url.startswith("https://")

    @model_validator(mode="after")
    def _production_needs_real_secrets(self) -> "Settings":
        if self.is_production:
            problems = []
            if self.session_secret == DEV_SESSION_SECRET or len(self.session_secret) < 32:
                problems.append("SESSION_SECRET must be a random value of at least 32 characters")
            if not (self.token_encryption_keys.strip() or self.token_encryption_key):
                problems.append("TOKEN_ENCRYPTION_KEYS must be set (`anisync generate-key`)")
            problems += _missing_sign_in_credentials()
            if problems:
                raise ValueError("; ".join(problems))
        return self

    @model_validator(mode="after")
    def _donation_url_is_https(self) -> "Settings":
        if self.donation_url and not self.donation_url.startswith("https://"):
            raise ValueError("DONATION_URL must be an https:// address")
        return self

    @model_validator(mode="after")
    def _parse_signup_allowlist(self) -> "Settings":
        from anisync.providers import is_registered

        if self.signup_allowlist_enabled is None:
            self.signup_allowlist_enabled = self.is_production
        entries = set()
        for raw in self.signup_allowlist.split(","):
            if not (entry := raw.strip()):
                continue
            provider, _, remote_id = (part.strip() for part in entry.partition(":"))
            if not remote_id or not is_registered(provider):
                raise ValueError(
                    f"SIGNUP_ALLOWLIST entry {entry!r} must be `provider:account id` with a known provider"
                )
            entries.add((provider, remote_id))
        self._signup_allowlist_ids = frozenset(entries)
        return self

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url


def _missing_sign_in_credentials() -> list[str]:
    """Every provider people sign in with needs its OAuth app credentials, or sign-in fails only once someone tries."""
    from anisync.providers import all_providers
    from anisync.providers.base import credentials

    problems = []
    for provider in all_providers():
        if provider.can_sign_in:
            creds = credentials(provider.id)
            prefix = provider.id.upper()
            if not creds.client_id:
                problems.append(f"{prefix}_CLIENT_ID must be set")
            if not creds.client_secret:
                problems.append(f"{prefix}_CLIENT_SECRET must be set")
    return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
