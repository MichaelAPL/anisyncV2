from functools import lru_cache

from pydantic import model_validator
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
            if problems:
                raise ValueError("; ".join(problems))
        return self

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
