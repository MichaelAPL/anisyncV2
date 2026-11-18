# AniSync v2 — agent context

AniSync keeps a user's anime lists on **MyAnimeList (MAL)**, **AniList** and **Kitsu** in sync.
Public multi-user site. Rebuild of a 2022 Rails app (`~/Code/anisync`, deprecated, reference only:
it got as far as MAL/AniList OAuth and importing a MAL list; it never synced anything).

Current progress and next steps: **`docs/ROADMAP.md`** (read it first, keep it updated).

## Product model (decided — don't re-litigate without the user)

- **Master + mirrors.** The user picks one linked account as the *master* (source of truth).
  Every other linked account is a *mirror* that is overwritten to match it.
- **Two sync paths:**
  1. Nightly job (`DAILY_SYNC_CRON`, default 04:00 UTC): full diff master → each mirror.
  2. Editing the list *in AniSync* writes to the master service immediately, then queues one
     `push_entry` job per mirror.
- Edits made directly on a mirror's website are overwritten at the next nightly sync (shown to users).
- No AniSync passwords: users sign in with MAL or AniList OAuth; the first linked account creates the
  user and becomes master. Kitsu is linked afterwards (password grant, see below).
- Mirror options: `sync_enabled`, `prune_missing` (delete mirror entries not on master; off by default).
- Dry-run preview (`GET /api/sync/preview`) before applying the first sync.
- **Anime only** (manga out of scope).
- **Funding: ad-free for now (ads possible later, see ROADMAP), one-off voluntary donations only.** No monthly/recurring donations, tiers or funding goals
  (MAL API agreement treats those as commercial); donations never unlock features. Never present AniSync as
  official or affiliated with MAL/AniList/Kitsu. Details: ROADMAP → Decisions.
- **Roles → permissions:** `users.role` (`user` | `admin`, more later) maps to permission sets in code.
  Endpoints check permissions with `require_permission("users.ban")`, never role names; `/api/me` returns the
  permissions for the UI. `ADMIN_USER_IDS` only bootstraps the first admins. Details: ROADMAP 4.0.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 (async, psycopg3), Alembic, managed with **uv** |
| Jobs | **Procrastinate** (Postgres-backed queue; Sidekiq equivalent, no Redis). Worker runs in-process with the API by default (`RUN_WORKER_IN_PROCESS`) for cheap single-container hosting |
| DB | Postgres 17 (docker-compose, host port **5434**: 5432/5433 are taken on the dev machine) |
| Frontend | React + Vite + TypeScript, `react-router`, `@tanstack/react-query`. Vite dev server proxies `/api` and `/auth` to FastAPI so cookies are same-origin |
| Hosting plan | Cheap/free: Railway (~$5/mo) or Cloudflare Pages + Neon + Render/Koyeb. Everything stays Dockerizable |

## Backend layout (`backend/src/anisync/`)

> **Planned refactor, see `docs/ROADMAP.md` Phase 1:** the hardcoded three-provider pieces below (`Service` enum,
> `AnimeIds` columns, `ListEntry.mal_id/anilist_id/kitsu_id`, pairwise lookups in `mapping.py`, per-provider
> branches in `api/auth.py`) are being replaced by a provider registry and canonical media IDs. Don't extend them.

- `config.py`: pydantic-settings, reads `backend/.env` (template: `.env.example`).
- `models.py`: `User` (has `master_account_id`), `LinkedAccount` (Fernet-encrypted tokens via properties),
  `AnimeIds` (cross-service ID cache), `SyncRun` (history + JSON summary).
- `services/base.py`: **normalized model**: `ListEntry`, `Status`, `ServiceClient` ABC, `quantize_score`,
  errors `RateLimited` / `ReauthRequired`. `services/{mal,anilist,kitsu}.py`: one client + OAuth helpers each.
- `engine.py`: **pure** diff (`plan_sync`): no I/O, fully unit tested.
- `mapping.py`: `IdMapper` translates IDs between services (entry hints → `anime_ids` table → remote lookups).
- `sync.py`: orchestration (token refresh, run plans, `push_entry`). `jobs.py`: Procrastinate tasks.
- `api/auth.py`: OAuth login/callback, Kitsu link, logout. `api/routes.py`: `/api/*` JSON endpoints.

## Service quirks (hard-won, keep these in mind)

- **Normalized values:** score is 0–100 everywhere internally. MAL stores 0–10 (step 10), Kitsu
  `ratingTwenty` 2–20 (step 5), AniList `scoreRaw` 0–100. Diffs compare at the *mirror's* precision.
  Status: MAL `plan_to_watch` / AniList `PLANNING` / Kitsu `planned` → `planning`; AniList `REPEATING` =
  watching + `rewatching=True`; MAL `is_rewatching`; Kitsu `reconsuming`.
- **Dates** only sync when the master has a full date; we never clear mirror dates (MAL allows partial dates).
- **MAL:** OAuth2 + PKCE, **`plain` challenge only**. Tokens ~31 days, refreshable. No published rate limit (~1 req/s).
- **AniList:** GraphQL. Tokens last 1 year and **cannot be refreshed**: user must re-link (UI banner).
  ~90 req/min, sometimes degraded to 30; we throttle to 25/min. `Media.idMal` gives free ID mapping;
  batch lookups via `Page(media(idMal_in:…))`, 50 per call. Entries can appear in several custom lists: dedupe.
- **Kitsu:** JSON:API at **kitsu.app** (moved from kitsu.io). Only the OAuth **password grant**: exchange
  credentials for tokens, never store the password. `/mappings` maps MAL/AniList IDs → Kitsu IDs.
- **Prune safety:** `plan_sync` never deletes a mirror entry that could be the twin of an unmapped master entry.
- Rate limiters are per process/event loop; switch to a shared limiter before running multiple workers.

## Commands

```bash
docker compose up -d --wait                       # Postgres on localhost:5434
cd backend
uv sync
cp .env.example .env                              # then fill TOKEN_ENCRYPTION_KEY, OAuth client ids/secrets
uv run alembic upgrade head                       # app tables
uv run procrastinate --app=anisync.jobs.app schema --apply   # job tables (once per DB)
uv run uvicorn anisync.main:app --reload --port 8000         # API + in-process worker
uv run pytest -q                                  # tests (respx mocks HTTP; no DB needed)
uv run ruff check --fix src tests && uv run ruff format src tests
uv run alembic revision --autogenerate -m "..."   # after model changes (ignores procrastinate_* tables)

cd ../frontend && npm install && npm run dev      # http://localhost:5173
```

OAuth redirect URIs (register with MAL/AniList): `http://localhost:5173/auth/{mal|anilist}/callback`.

## Conventions

- Keep `engine.py` pure; put I/O in `sync.py` / clients. New service = new `ServiceClient` + entry in `services/__init__.py:CLIENTS`.
- Tests: see ROADMAP → Testing strategy. Every client parse/serialize change gets a respx test (prefer recorded
  fixtures), every engine rule a unit or Hypothesis test, every DB/API behaviour a test against the `anisync_test`
  DB. Never hit real provider APIs from the test suite.
- Ruff, line length 120. Alembic for every schema change.
- Never log or persist provider passwords or raw tokens; tokens go through `LinkedAccount` properties (encrypted).

## Design

UI flows are designed on a Claude Design canvas (private to the owner):
https://claude.ai/artifact/6SB43GGnqxC4VLuY16KeRs. Redesigned 2026-11-17 in **Material 3** (user asked for a less plain,
Material-style look and mobile support).

- Screens: public (Sign in/landing, Privacy, Maintenance), onboarding (Link lists, Choose master, Review first sync),
  app (Dashboard, My list + edit sheet, Settings), admin (Overview, Syncs + logs, User lookup, User detail, Ban dialog,
  Unmatched titles, Providers + ID dataset, Site banner/maintenance). Phone-width previews sit under each section.
- Theme: M3 tonal scheme from an indigo seed: primary `#4355B9`, primary-container `#DEE0FF`, secondary-container
  `#E0E1F9`, tertiary-container `#FFD7F1`, surface `#FBF8FF`, surface containers `#FFFFFF/#F5F2FA/#EFEDF4/#E9E7EF/#E4E1E9`,
  outline `#767680`/`#C6C5D0`, error `#BA1A1A`; custom success `#1F6B43`/`#ADF2C4`, warning `#7A5900`/`#FFDF9E`.
  Font **Roboto Flex** with the M3 type scale. Service monogram chips unchanged (MAL `#E3E9F7/#2E51A2`,
  AniList `#DDF3FF/#0369A1`, Kitsu `#FDE6E2/#B42318`).
- Layout: navigation rail at ≥600 px, bottom navigation bar below 600 px (M3 compact window class); pill buttons,
  outlined text fields, switches, filter chips, tabs, snackbar, bottom sheet on phones / side sheet on desktop.
- The canvas's shared stylesheet (all tokens and components) is the source for the frontend theme; generator scripts
  for the artboards live in the session scratchpad only, so treat the canvas as the source of truth.
- Dark mode: M3 dark scheme (`.theme-dark`, `.theme-auto` follows the OS). Logged-in users get a top-right
  light/dark toggle; the choice is saved on their account (`users.theme`) and in localStorage (ROADMAP Phase 5). Logo: an original ring + sync-arrows mark
  (not based on any existing franchise artwork). Name is written "AniSync" everywhere, including the header wordmark (user, 2026-11-17).
- Provider identity = colored monogram chips, **not logos** (trademark terms; see ROADMAP Phase 6).
- Anime covers: hotlinked from the master provider's API image URL; designs show a placeholder tile.
- Frontend implementation choice (Phase 5): MUI (Material UI v6+, closest to M3) or Material Web components; decide then.
