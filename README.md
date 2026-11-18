# AniSync

[![CI](https://github.com/MichaelAPL/anisyncV2/actions/workflows/ci.yml/badge.svg)](https://github.com/MichaelAPL/anisyncV2/actions/workflows/ci.yml)

Keep your MyAnimeList, AniList and Kitsu anime lists in sync. Pick one list as the master; the others
mirror it nightly and whenever you edit your list in AniSync.

- `backend/`: FastAPI + Postgres + Procrastinate background jobs (Python, uv)
- `frontend/`: React + Vite + TypeScript

## Quick start

```bash
docker compose up -d --wait            # Postgres on localhost:5434

cd backend
uv sync
cp .env.example .env                   # fill in the keys (see comments in the file)
uv run alembic upgrade head
uv run procrastinate --app=anisync.jobs.app schema --apply
uv run uvicorn anisync.main:app --reload --port 8000

cd ../frontend
npm install
npm run dev                            # http://localhost:5173
```

Tests: `cd backend && uv run pytest -q`

Architecture, decisions and API quirks: [AGENTS.md](AGENTS.md). Status and next steps: [docs/ROADMAP.md](docs/ROADMAP.md).
