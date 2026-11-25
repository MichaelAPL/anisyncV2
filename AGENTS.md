# AniSyncHub v2 — agent context

AniSyncHub keeps a user's anime lists on **MyAnimeList (MAL)**, **AniList** and **Kitsu** in sync.
Public multi-user site. Rebuild of a 2022 Rails app (`~/Code/anisync`, deprecated, reference only:
it got as far as MAL/AniList OAuth and importing a MAL list; it never synced anything).

Current progress and next steps: **`docs/ROADMAP.md`** (read it first, keep it updated).
Documented solutions to past problems (bugs, best practices, workflow patterns): `docs/solutions/`, organized by
category with YAML frontmatter (`module`, `tags`, `problem_type`); relevant when implementing or debugging in an area it covers.
Coding standards: **`CODING_STANDARDS.md`** (read before writing or reviewing code; update it when a change establishes
or retires a rule).

## Product model (decided — don't re-litigate without the user)

- **Master + mirrors.** The user picks one linked account as the *master* (source of truth).
  Every other linked account is a *mirror* that is overwritten to match it.
- **Two sync paths:**
  1. Scheduled sync: an hourly tick (`SYNC_TICK_CRON`) syncs the users whose `user_id % 24` is the current hour, so
     each user once a day. It reads the master incrementally, plans against **snapshots**, writes only differences.
  2. Editing the list *in AniSyncHub* writes to the master service immediately, then queues one
     `push_entry` job per mirror.
- Edits made directly on a mirror's website are overwritten at the next **full read** of that mirror (at least
  weekly), not at every scheduled sync (shown to users).
- No AniSyncHub passwords: users sign in with a redirect-flow provider (OAuth); the first linked account creates the
  user and becomes master. Credential-only providers (Kitsu) are linked afterwards, once signed in.
- **Profile:** `users.display_name` is editable (1-40 chars, not unique: nobody but the user and admins sees it). The
  profile picture is a hotlinked provider image the user picks from their linked accounts (`avatar_url`); it never follows
  master changes, and stored URLs must be https on the provider's `image_hosts`. No uploads (decided 2026-11-19).
- Mirror options: `sync_enabled`, `prune_missing` (delete mirror entries not on master; off by default).
- Dry-run preview (`GET /api/sync/preview`) before applying the first sync.
- **Anime only** (manga out of scope).
- **Funding: ad-free for now (ads possible later, see ROADMAP), one-off voluntary donations only.** No monthly/recurring donations, tiers or funding goals
  (MAL API agreement treats those as commercial); donations never unlock features. Never present AniSyncHub as
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
| Hosting | One VPS deployed with **Kamal 2**; the only public way in is a **Cloudflare Tunnel**, SSH only over **Tailscale**; Postgres 17 as a Kamal accessory; nightly age-encrypted backups to R2. One image (`Dockerfile`) serves the API, the in-process worker and the built frontend from one origin. Walkthrough: `docs/deploy.md` |

## Backend layout (`backend/src/anisync/`)

- `providers/`: **the only place that names a specific provider** (a test enforces it).
  `base.py`: `ListEntry` (provider-agnostic; `media_id` is a string, `external_ids` maps provider → title ID),
  `Provider` descriptor (id, name, monogram, colors, `auth_kind`, `score_step`, `rate_limit`, url pattern, auth hooks,
  `lookup_from`), `ProviderClient` ABC, `Status`, `quantize_score`, errors, `credentials()`.
  `registry.py`: `get_provider` / `all_providers`. `{mal,anilist,kitsu}.py`: one client + OAuth helpers + a registered
  `Provider` each. **Adding a provider = one module + one import line in `providers/__init__.py` + tests**; no migration,
  route or frontend change (proved by the fake `acme` provider in the tests).
- `models.py`: `User` (`master_account_id`), `LinkedAccount` (`provider` is a plain string validated against the registry;
  Fernet-encrypted tokens via properties), `Media` + `MediaExternalId` (canonical title and each provider's ID for it;
  `source` = seed | learned | manual, manual links are never overwritten), `ListSnapshot` (what we last read/wrote per
  account; used from Phase 3), `SyncRun`.
- `seed.py`: bulk import of the offline ID dataset (`uv run python -m anisync.seed [--force]`; weekly job in `jobs.py`;
  runs recorded in `dataset_imports`). Run it after migrating a new database. Upstream manami-project is archived: the
  default source is the `cedya77` continuation (same format and licence; credit both).
- `mapping.py`: `IdMapper` resolves provider → canonical media → provider. Order: database, hints on list entries
  (`learn_from`), then remote lookups by providers that declare `lookup_from`. Use as `async with IdMapper(session)`.
- `engine.py`: **pure** diff (`plan_sync`): no I/O, unit + property tested.
- `snapshots.py`: what we last read/wrote per account (`list_snapshots`). Syncs plan from snapshots; a no-change sync
  costs one request (the master's incremental read). Full reads (first sync, weekly, forced) replace the snapshot and
  are the only way to notice removals and direct edits (`drifted`). Snapshots of an account not fully read for 60 days are purged by the daily housekeeping (`purge_stale`). `mapping.py` also keeps a 7-day negative lookup cache.
- `sync.py`: orchestration (token refresh via the provider's `refresh` hook, run plans, `push_entry`). `jobs.py`: Procrastinate tasks.
- `permissions.py` (roles → permissions), `moderation.py` (ban/unban/role changes + `admin_actions` audit), `api/admin.py`
  (`/api/admin/*`, split into `admin.py`, `admin_syncs.py`, `admin_users.py`, `admin_site.py`, `admin_dataset.py`, `admin_titles.py`; every route uses `Depends(require_permission(...))`, a test enforces it). Banned users count as signed out.
- `unmatched.py` (queue of untranslatable titles for manual linking), `deletion.py` (delete a user and all their data; used by the
  admin and by `POST /api/me/delete`).
- `switches.py`: admin switches read at runtime (paused providers, paused scheduled syncs, maintenance mode, banner). Jobs and
  syncs check them; `api/public.py` serves `/api/providers` and `/api/site` and the maintenance guard (503 on writes).
- `stats.py` (admin overview/daily numbers), `metrics.py` (in-memory provider request counters, flushed to `provider_stats`).
- `sessions.py` (server-side browser sessions, token hash only), `security.py` (CSRF + request-ID middleware),
  `ratelimit.py` (Postgres-backed), `logs.py` (secret redaction), `crypto.py` (`TOKEN_ENCRYPTION_KEYS`, rotation),
  `cli.py` (`uv run anisync generate-key | rotate-keys | seed`).
- `api/auth.py`: generic routes driven by `auth_kind`: `GET /auth/{provider}/login|callback` (redirect providers),
  `POST /auth/{provider}/password` (credential providers, needs a signed-in user), `POST /auth/logout`.
  `api/routes.py`: `/api/*` JSON endpoints.
- `config.py`: pydantic-settings (`backend/.env`, template `.env.example`). Provider credentials are
  `<PROVIDER>_CLIENT_ID` / `<PROVIDER>_CLIENT_SECRET`, read by `providers.base.credentials`. Production refuses to start
  without them for every sign-in provider. `SIGNUP_ALLOWLIST_ENABLED` / `SIGNUP_ALLOWLIST` (`provider:account id`,
  never usernames) close sign-up until the providers answer (on by default in production).
- `frontend.py` (serves `FRONTEND_DIST` from the API's origin in the production image), `sentry.py` (optional error
  reporting, scrubbed: no bodies, cookies, headers or locals). Sync runs carry a `heartbeat_at`; a run is stale only
  after 15 minutes without one, so a rolling deploy never closes or doubles a live sync.

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
uv run python -m anisync.seed                                # fill the ID mapping from the offline dataset (~10 s)
uv run uvicorn anisync.main:app --reload --port 8000         # API + in-process worker
uv run pytest -q                                  # tests: respx mocks HTTP; DB tests use `anisync_test` on the compose Postgres
                                                  # (skipped locally if it isn't running, failing in CI)
uv run ruff check --fix src tests && uv run ruff format src tests
uv run alembic revision --autogenerate -m "..."   # after model changes (ignores procrastinate_* tables)

cd ../frontend && npm install && npm run dev      # http://localhost:5173
cd ../frontend && npm test                       # Vitest + Testing Library (jsdom), fetch stubbed per test (src/test/harness.tsx)
cd ../frontend && npm run e2e                  # Playwright in your installed Chrome: desktop/phone x light/dark, against a throwaway
                                              # backend (backend/e2e/server.py: DB anisync_e2e, fake providers, /__e2e/* seeding routes)
```

OAuth redirect URIs (register with each redirect-flow provider): `http://localhost:5173/auth/<provider id>/callback`
(e.g. `mal`, `anilist`).

Production: `kamal deploy` (setup, secrets, checks and backups: `docs/deploy.md`). Migrations run before the new
container takes traffic, as the database owner role, from `.kamal/hooks/pre-deploy`; the app's own role can't change the
schema. **Migrations are expand-only**: add columns and tables, and drop or rename only in a later deploy once no running
version uses them, because old and new containers overlap during a deploy and a rollback runs no migration.

## Security rules (Phase 2; tests enforce most of them)

- Every route needs `CurrentUser` unless it is on the public list in `tests/test_api_contract.py`; every route declares a
  pydantic response schema (`api/schemas.py`), never raw dicts or ORM objects, and no schema may carry token/secret/
  password/hash/cookie fields. Scope every query to `user.id`; another user's resource is a 404.
- No SQL built from strings (ruff `S608`). Never log request bodies/headers; logs pass through `logs.redact`.
- Browser auth = `anisync_session` cookie (random token, hash stored, HttpOnly, SameSite=Lax). State-changing requests
  must carry an `Origin` equal to `PUBLIC_URL` (the CSRF middleware); the test client sends one.
- Rate-limit anything that accepts credentials (`ratelimit.per_ip` / `per_user`). Production (HTTPS `PUBLIC_URL`) refuses
  weak `SESSION_SECRET` or missing encryption keys. Key rotation procedure: ROADMAP Phase 2.2.
- If the route walk in tests finds no routes, the test fails on purpose (FastAPI hides included routes in wrappers).

## Conventions

- Keep `engine.py` pure; put I/O in `sync.py` / clients. New provider = a module in `providers/` that registers a `Provider`
  (see above); never branch on a provider's name outside `providers/`: add a descriptor field or a client method instead.
- Tests: see ROADMAP → Testing strategy. Every client parse/serialize change gets a respx test (prefer recorded
  fixtures), every engine rule a unit or Hypothesis test, every DB/API behaviour a test against the `anisync_test`
  DB. Never hit real provider APIs from the test suite.
- Ruff, line length 120. Alembic for every schema change.
- Never log or persist provider passwords or raw tokens; tokens go through `LinkedAccount` properties (encrypted).
- After a solved, verified problem, offer once to invoke the `ce-compound` skill at the completion checkpoint when the work corrected a wrong belief about how something behaves: an approach that failed, a review finding that exposed a real defect, or an assumption, document, or first diagnosis that turned out wrong. A naming, style, or preference change does not count. Work that went as first planned does not need the offer. Do not judge whether the lesson deserves a document: `ce-compound` decides whether it qualifies and what holds it (a test, a lint rule, a coding standard, or a learning), and writes nothing when nothing qualifies. Offer at the checkpoint so what it writes can ship in the PR that produced it, and only where the repository treats captured learnings as tracked, committed knowledge.
- Write every report, summary, or handoff to the user through the `ce-noslop` skill. This applies when you are the top-level agent writing to the user, not when you are a subagent reporting to its caller. Do not apply it to code, config, verbatim quotes, or text the user asked to post as written.

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
  (not based on any existing franchise artwork). Name is written "AniSyncHub" everywhere, including the header wordmark (user, 2026-11-17; renamed from "AniSync" on 2026-11-22 after finding an unrelated add-on called anisync.qzz.io). Only the visible brand changed: the repo (`anisyncV2`, kept so the providers can audit it), the Python package `anisync`, database and role names, env vars (`ANISYNC_*`), the `anisync_session` cookie and image names keep the old spelling on purpose.
- Provider identity = colored monogram chips, **not logos** (trademark terms; see ROADMAP Phase 6).
- Anime covers: hotlinked from the master provider's API image URL; designs show a placeholder tile.
- Frontend implementation choice (Phase 5): MUI (Material UI v6+, closest to M3) or Material Web components; decide then.
