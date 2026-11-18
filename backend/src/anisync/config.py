from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # postgresql://user:pass@host:5432/db — plain libpq URL, shared by SQLAlchemy and Procrastinate.
    database_url: str = "postgresql://anisync:anisync@localhost:5434/anisync"

    # Public origin the browser talks to (the Vite dev server proxies /api and /auth to FastAPI).
    # OAuth redirect URIs are built from it: {public_url}/auth/{service}/callback
    public_url: str = "http://localhost:5173"

    session_secret: str = "dev-only-change-me"
    # Fernet key used to encrypt provider tokens at rest. Generate with:
    #   uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: str = ""

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
    # Cron expression (UTC) for the daily master -> mirrors sync.
    daily_sync_cron: str = "0 4 * * *"

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
