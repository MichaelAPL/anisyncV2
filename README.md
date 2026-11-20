# AniSync

[![CI](https://github.com/MichaelAPL/anisyncV2/actions/workflows/ci.yml/badge.svg)](https://github.com/MichaelAPL/anisyncV2/actions/workflows/ci.yml)

Keep your MyAnimeList, AniList and Kitsu anime lists in sync. Pick one list as the master; the others
mirror it daily and whenever you edit your list in AniSync.

**Status:** built and tested locally, not deployed. A public launch waits on written answers from the providers about
their API terms (see [docs/ROADMAP.md](docs/ROADMAP.md), Phase 6). Free and ad-free; one-off voluntary donations are the
only planned funding.

## What it does

- You sign in with MyAnimeList or AniList (OAuth) and link the others (Kitsu uses its username and password grant).
- You choose one linked list as the **master**. Every other linked list is a **mirror** that is updated to match it.
- A scheduled sync runs about once a day per user. Edits made inside AniSync are also pushed to the mirrors straight away.
- Anime only. Titles are matched across sites with an open ID dataset (see Data credits).

## Data we keep

Reviewers (providers, users) can check each claim in the code.

| What | Where | Notes |
|---|---|---|
| OAuth tokens | `LinkedAccount` (`models.py`) | Encrypted at rest (Fernet, rotatable keys, `crypto.py`). Never logged. |
| Provider passwords | nowhere | Kitsu's password is exchanged for tokens and discarded. AniSync has no passwords of its own. |
| A copy of each linked list | `ListSnapshot` (`snapshots.py`) | Per entry: title ID and title, status, progress, score, rewatching, start and finish dates, cover URL, adult flag. Used only to work out what changed, so a sync with no changes costs one request. It is not offered as a backup or export. |
| Sessions | `UserSession` (`sessions.py`) | Only a hash of the session token is stored. |
| Sync history and logs | `SyncRun`, `SyncRunEvent` | Kept at most 30 days or 50 runs per user. |

**Deletion.** Unlinking an account removes its tokens, list copy, sync events and unmatched-title rows
(`deletion.forget_account`). Deleting an AniSync account removes everything about the user (`deletion.delete_user`).
Both are covered by tests. Cover images and avatars are hotlinked from the providers, never stored.

## Layout

- `backend/`: FastAPI + Postgres + Procrastinate background jobs (Python, uv). Provider code lives only in
  `backend/src/anisync/providers/`; the diff engine (`engine.py`) is pure and property-tested.
- `frontend/`: React + Vite + TypeScript, Material 3.

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

Tests: `cd backend && uv run pytest -q` (backend, needs the Postgres container), `cd frontend && npm test` (Vitest) and
`npm run e2e` (Playwright).

Architecture, decisions and API quirks: [AGENTS.md](AGENTS.md). Status and next steps: [docs/ROADMAP.md](docs/ROADMAP.md).

## Data credits

Anime ID mappings between sites are seeded from
[cedya77/anime-offline-database](https://github.com/cedya77/anime-offline-database), the maintained continuation of
[manami-project/anime-offline-database](https://github.com/manami-project/anime-offline-database), both licensed under the
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1.0/) with contents under the
[DbCL 1.0](https://opendatacommons.org/licenses/dbcl/1.0/). Load it with `uv run python -m anisync.seed`.
AniSync is not affiliated with MyAnimeList, AniList or Kitsu.
