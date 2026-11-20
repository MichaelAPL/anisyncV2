# Roadmap / status

Update this file at the end of every working session. Work the phases in order; each task lists what "done" means.

## Done

**2026-11-16**
- Backend scaffold: models, Alembic initial migration, MAL/AniList/Kitsu clients, normalized model,
  diff engine, ID mapper, sync orchestration, Procrastinate jobs (daily + per-edit push), FastAPI auth + API.
- Verified: 14 unit tests pass; migrations + job schema apply on real Postgres; API boots with in-process worker;
  ID-mapper merge logic against the DB; live public lookups (AniList MAL→AniList, Kitsu MAL→Kitsu);
  job queue + per-user queueing lock.
- Frontend: Vite + React + TS scaffold only (`react-router`, `@tanstack/react-query` installed).
- UI flows designed on the Claude Design canvas (see AGENTS.md → Design).

**2026-11-17**
- Reviewed security, capacity and extensibility, and agreed the plan below.
- Answered the open questions: anime only, admin requirements, and donations-only funding (see Decisions).
- Redesigned the canvas in Material 3 (dark mode, phone layouts, admin screens). Roles → permissions model (4.0).
- **Phase 1 done** (tasks 1.0–1.7). Migrations `3f1a7c2b9d40`, `8c4d1e6a2b57`.
- **Phase 2 done** (security hardening, see below). Migration `a7d3c9e15f02`.
- **Phase 3 done** (sync efficiency, see below). Migration `c5e2b8f94a13`. 268 backend tests.
- **Profile (display name + picture) done 2026-11-19** (migration `f2c6e9a41b78`, 332 backend tests): `PATCH /api/me`, `PUT|DELETE /api/me/avatar`, see Phase 5 for the screens.
- **Phase 4 done (backend):** 4.0-4.12, the whole Phase 4 backend (roles, bans, sync events, stats, sync status, user lookup, provider kill switch, job controls, unmatched titles, banner and maintenance, user actions, expiring connections, dataset status; migrations `d6f3a9b7c201`, `e8b4c1d92a65`, `a9e1d4c7b630`, `b4f7a2c9d815`, `c8d2e5a1f947`; 443 backend tests). The admin screens are built in Phase 5.
- Repo published: https://github.com/MichaelAPL/anisyncV2 (public, `main`). CI (`.github/workflows/ci.yml`):
  ruff, pytest, oxlint, frontend build; first run green.

## Not verified yet

- Real OAuth round-trips (needs MAL + AniList client registrations in `backend/.env`).
- Writes to real accounts (MAL PATCH, AniList SaveMediaListEntry, Kitsu library-entries). Test on throwaway accounts first.

## Decisions (2026-11-17)

- **Stay on FastAPI.** Django was considered: its built-in sessions, CSRF and admin don't cover the hard parts
  (provider OAuth quirks, token encryption, sync engine), and the frontend is a React SPA. Close the gaps ourselves (Phase 2).
- **API versions:** MAL API v2 (OAuth endpoints live under `/v1/oauth2`), AniList API v2 (GraphQL), Kitsu "edge"
  JSON:API on kitsu.app. Kitsu still has **no authorization-code OAuth** and no scopes (official docs, checked
  2026-11-17), so the password grant stays.
- **Provider-agnostic core:** adding a provider must mean one client module + tests + a registry entry, with
  no migration, route or frontend changes.
- **Capacity target:** tens of thousands of users on a $5–20/mo setup. The limit is provider rate limits, not our
  stack, so the design minimises provider requests. Never get around provider limits by adding IPs.
- **Anime only.** Manga is out of scope. Don't add `media_type` or manga fields; the provider registry is enough
  if that ever changes.
- **Funding: no ads, one-off voluntary donations only** (e.g. Ko-fi one-time tips) to cover server costs. Why:
  - MAL API agreement §1(h) counts "crowdfunding and soliciting donations via quotas (e.g. monthly donations)" as
    *commercial* (needs MAL's written authorization); §1(i) counts "applications accepting donations without any quotas"
    as *non-commercial*. So: no monthly tiers, subscriptions, Patreon-style memberships or funding goals/progress bars.
  - No ads at launch. **Ads are a possible future revenue source** (user, 2026-11-17); before adding them:
    - MAL §1(i) allows "pay per click or pay per view" ads in non-commercial apps if they don't disrupt the user
      experience; adding ads is a change MAL must review first (§4(b)); ads must not target minors (§4(h)).
    - AniList: once total revenue (ads + donations) passes $150/month, we need their commercial license.
    - Ad networks set cookies: needs a privacy policy and a consent banner for EU/UK visitors.
    - Track monthly revenue in the admin panel so we see the AniList threshold coming.
  - AniList allows commercial use under $150/month revenue; we stay well under it.
  - Donations never unlock features (that would make it a paid service).
- **Branding rules:** never present AniSync as official or affiliated with MAL, AniList or Kitsu. Use provider names
  only to say where data comes from (MAL §3(a)(xi), (xiii), §17; AniList: name apps "unofficial" if they include "AniList").
  Footer line: "AniSync is not affiliated with MyAnimeList, AniList or Kitsu."

## Testing strategy (applies to every phase)

Today: 14 backend unit tests (pure engine + respx-mocked clients), no DB tests, no frontend tests. CI runs on every push.

| Layer | Tools | What it covers |
|---|---|---|
| Pure logic | pytest, **Hypothesis** (property-based) | engine rules, score quantization, status mapping, prune safety (e.g. "a plan never deletes a possible twin", "applying a plan twice is a no-op") |
| Provider clients | pytest + respx, **recorded fixtures** | parse/serialize against real responses saved as sanitized JSON in `tests/fixtures/<provider>/`; request counts for the Phase 3 budget |
| DB + API | pytest with a real Postgres (`anisync_test` DB on the compose server), transaction rolled back per test; `httpx.AsyncClient(ASGITransport)` | migrations up/down, auth/sessions, bans, admin authorization (403s), stats queries, user deletion |
| Jobs | Procrastinate `InMemoryConnector` | fan-out, queueing locks, paused providers, retries |
| Frontend | **Vitest + React Testing Library + MSW** (mocked API) | components and flows per screen |
| End-to-end | **Playwright**, a few flows only, fake providers | sign in → link → preview → apply; admin ban |

Rules:
- Every task's "done" includes its tests; bugs get a regression test first.
- Never call real provider APIs in the test suite; live checks are manual scripts, run on purpose.
- Coverage via `pytest-cov`: aim high on `engine.py`, `mapping.py`, providers, auth and admin permissions; no
  global vanity target.
- CI (GitHub Actions, once the repo is pushed): ruff, pytest with a Postgres service, frontend lint + typecheck +
  Vitest on every push; Playwright on main.

## Phase 1: Provider-agnostic data model  ✔ done 2026-11-18

Everything else builds on this, so do it before the frontend. One Alembic migration; the dev DB holds no
real data yet, so dropping the old tables is fine.

- [x] **1.0 Test infrastructure first.** (Sample payloads in `tests/fixtures/` are hand-written to the providers' documented
      shapes, not recorded: replace them with sanitized real responses during Phase 6 testing.) Add Hypothesis, pytest-cov; DB fixtures (`anisync_test`, migrate once
      per session, rollback per test); API client fixture; move existing respx payloads into `tests/fixtures/`.
      (Git repo, first commit and CI are done; add a Postgres service to CI with the first DB tests.)
- [x] **1.1 Provider registry.** `providers/` package with a `Provider` descriptor per service: `id` (str),
      display name, monogram, chip colors, `auth_kind` (`oauth_code` | `oauth_pkce_plain` | `password` | `api_key`),
      `score_step`, `rate_limit`, supported media types, client class, auth hooks (authorize URL / exchange / refresh / password login).
      `CLIENTS` dict and `sync.REFRESHERS` go away. *Done when* nothing outside `providers/` names MAL/AniList/Kitsu.
      **Done** (enforced by `test_registry.py::test_nothing_outside_the_providers_package_names_a_provider`). The generic
      auth routes of 2.4 were done here too: `/auth/{provider}/login|callback|password`; Kitsu's route moved from
      `POST /auth/kitsu` to `POST /auth/kitsu/password`. Provider credentials are `<PROVIDER>_CLIENT_ID/_SECRET`.
- [x] **1.2 Provider column becomes a string.** Replace the Postgres `service` enum with a `String` validated
      against the registry.
- [x] **1.3 Canonical media + external IDs.** Replace `anime_ids` with `media(id, title, episodes, cover_url, cover_color,
      cover_url, …)` and `media_external_ids(media_id, provider, external_id, unique(provider, external_id))`.
- [x] **1.4 `ListEntry` generalised.** `media_id: str` (some providers use string IDs), `external_ids: dict[str, str]`
      instead of `mal_id/anilist_id/kitsu_id`. `progress` is episodes.
      Update the engine's prune-safety hints to use `external_ids`.
- [x] **1.5 Resolver through canonical media.** `mapping.py` resolves provider→canonical→provider. Each client may
      implement an optional `lookup_external_ids(ids)`; no pairwise code paths.
- [x] **1.6 List snapshots table.** `list_snapshots(account_id, media_id, status, progress, score, rewatching,
      started_at, finished_at, remote_updated_at, last_written_hash)`: what we last read from or wrote to each
      account (logic arrives in Phase 3). Differences from the sketch: keyed by `(account_id, external_id)` (the title's ID
      on that provider, so unmapped titles fit) with a nullable `media_id`; also stores `remote_entry_id`, display data
      (`title`, `total_episodes`, `image_url`) and `external_ids` hints, so the list can be served and prune safety checked
      without calling the provider.
- [x] **1.7 Seed IDs from offline datasets.** `seed.py`: streams the `.jsonl` release asset (62 MB, ~120 MB peak memory,
      ~8 s), bulk-imports in batches, never changes or removes existing links (manual links and earlier learning win),
      skips a version it already imported (reads only the first line), records each run in `dataset_imports`.
      Run once: `uv run python -m anisync.seed [--force]`; the worker also checks weekly (`ID_DATASET_CRON`).
      Run it after deploying migrations, and with `--force` after registering a new provider (backfills its IDs).
      - **Source:** the upstream manami-project repo is **archived** (last release 2026-27, July 2026). The default is
        now `cedya77/anime-offline-database`, a maintained continuation (release 2026-46 on 2026-11-15) with the same
        format and the same licence (ODbL 1.0 + DbCL 1.0). Set `ID_DATASET_URL` to change it. Risk: it is a one-person
        project; if it stops, new titles fall back to remote lookups (learned once, shared by all users).
      - **Attribution (required by the licence):** name both projects in the README (done) and the site footer (Phase 5).
      - **Fribb `anime-lists`: not used.** Actively maintained, but it has no licence (all rights reserved by default) and
        is itself generated from the dataset above. Ask the author for a licence if it is ever needed.
      - The dataset's `picture` URLs are ignored: they point at a provider's image servers, and covers come from the
        provider APIs (see Phase 5).
      - **Measured on real data (dev DB, 2026-11-18):** 23,268 titles with two or more provider IDs (16.5k on all
        three). The 200 most popular titles resolve 100% from the database (0 remote lookups). The 182 newest releasing
        titles: 97% MAL↔AniList, 75% to Kitsu (Kitsu's own mappings lag), the rest are looked up once and learned.
- [x] Tests: registry, resolver, seeding parser and import, models, migrations up/down, auth/API flows, engine properties.

## Phase 2: Security hardening  ✔ done 2026-11-18

- [x] **2.1 Server-side sessions.** `sessions` table (SHA-256 of the token only), 30-day sliding expiry, new session on
      every sign-in with the old one revoked, `POST /auth/logout` and `/auth/logout-all`, `GET /api/sessions`,
      `DELETE /api/sessions/{id}`, daily purge job. The OAuth state/PKCE verifier live in a short-lived (10 min) signed
      cookie scoped to `/auth`. Starlette's `SessionMiddleware` is gone. Cookies: HttpOnly, SameSite=Lax, Secure on HTTPS.
- [x] **2.2 Key rotation.** `TOKEN_ENCRYPTION_KEYS=new,old` (`MultiFernet`; a single `TOKEN_ENCRYPTION_KEY` still works).
      `uv run anisync generate-key` / `rotate-keys` (idempotent; exits 1 and keeps going if some tokens are unreadable).
      Procedure: put the new key first, deploy, run `rotate-keys`, then drop the old key.
- [x] **2.3 CSRF.** Middleware: unsafe methods need an `Origin` (else `Referer`) equal to `PUBLIC_URL`; a request with a
      session cookie and no origin information is refused too. SameSite=Lax is the second layer.
- [x] **2.4 Generic auth routes** (Phase 1.1) and the **Authlib evaluation: not adopted.** Its Starlette client keeps the
      OAuth state in `request.session` (it needs `SessionMiddleware`, which 2.1 removed), and I found nothing showing
      support for MAL's PKCE `plain` (examples use S256; secondary sources only, the official docs page moved).
      Our flow is ~60 lines for any number of providers; revisit only if a provider needs something we don't have.
- [x] **2.5 SQL injection guard.** ruff `S` rules on (S608 and friends); tests prove the linter rejects SQL built with
      f-strings, `%` and `+`, and that `src/` has none. Rule: never interpolate values into `text()` / `execute()`.
- [x] **2.6 Secrets out of logs.** `logs.py`: redacts `code`, `state`, `*token`, `password`, `secret`, `sig`, `jwt`,
      bearer headers, JSON token fields and our own encrypted tokens, on uvicorn's access log and every log handler
      (including exception text). Errors stored in sync summaries (visible to users/admins) are redacted too.
      Verified against a real running uvicorn. httpx's own request logging is off.
- [x] **2.7 Rate limits.** Postgres-backed (`rate_limit_hits`, advisory lock per key): password linking 5 / 15 min per
      hashed IP and per user, OAuth login/callback 30 / 15 min per IP; 429 with `Retry-After`; refused attempts aren't
      recorded; purged daily. Behind a proxy run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy>`.
- [x] **2.8 Explicit response schemas.** Every route declares one (`api/schemas.py`); a test walks the router and fails
      for any route without a schema, and checks that no response schema has a token/secret/password/hash/cookie field.
- [x] **2.9 Safe errors.** Unexpected errors return `{"detail": "Internal server error", "request_id": …}` (also the
      `X-Request-ID` header and the log line; Sentry joins in Phase 6). No `/docs` or `/openapi.json` in production.
      Production (HTTPS `PUBLIC_URL`) refuses to start without a strong `SESSION_SECRET` and an encryption key.
- [x] **2.10 Ownership tests.** Two real users: every cross-user read/write on accounts, sessions, runs, preview and list
      is 404 or empty and changes nothing. A router walk requires `require_user` on every route that isn't on an explicit
      public list (and fails if the walk finds too few routes; FastAPI 0.163 hides included routes behind
      `_IncludedRouter`, which an earlier version of this test missed). Pair with the 4.0 permission test.
- [x] Tests: sessions, CSRF, rate limits, crypto/rotation, log redaction, hardening, contract, ownership, API routes,
      sign-in failure paths. 211 backend tests, 91% coverage.

Not done on purpose (Phase 6): TLS, secure headers (HSTS/CSP) at the host or proxy, Sentry, dependency audit in CI
(`pip-audit` / `npm audit`), and a ban check in `sessions.user_for` (arrives with 4.1).

## Phase 3: Sync efficiency (provider request budget)  ✔ done 2026-11-18

Before: every sync read the master and every mirror in full, all users at 04:00 (tops out around 1–2k users against
AniList's ~90 req/min). Now a sync works on snapshots and a run where nothing changed costs **one small request**.

- [x] **3.1 `/api/list` from snapshots.** The first read of a master fills its snapshot (one full read); after that the
      list is served from the database. Edits through AniSync update the snapshot immediately (no read-back), removals
      too, and `push_entry` updates the mirror's snapshot. `POST /api/list/refresh` (6 per 5 min per user) pulls in
      edits made on the master's own site with one incremental request.
- [x] **3.2 Incremental reads.** `fetch_list(since=cursor)` on providers with `incremental_reads` (MAL:
      `sort=list_updated_at&limit=100`; AniList: flat `Page.mediaList` sorted `UPDATED_TIME_DESC`, 50 per page), stopping
      at the first entry not **strictly** newer than the cursor. Strict, because a real AniList user has 5,000 entries
      sharing one bulk-import timestamp; "at or after" would re-read all of them every night. Kitsu reads in full (not
      verified to sort by update time; it is normally a mirror). `LinkedAccount.sync_cursor` = newest provider-side
      update seen.
- [x] **3.3 Plan from snapshots.** `plan_sync(master snapshot, mirror snapshot)`: mirrors aren't asked between full
      reads, and a change that failed stays missing from the mirror's snapshot, so the next run retries it with no
      extra bookkeeping. **Full reads** (`last_full_read_at`) happen on the first sync, weekly, when the master's provider
      can't read incrementally, or when forced (`run_sync(full=True)`); they are the only way to notice removals and edits
      made directly on a mirror's site (reported as `drifted` in the run summary). Also a **negative lookup cache**
      (`lookup_misses`, 7 days) so titles no provider can translate aren't looked up again every night.
- [x] **3.4 Staggered schedule.** `sync_tick` runs hourly (`SYNC_TICK_CRON`, replaces `DAILY_SYNC_CRON`) and syncs the
      users whose `user_id % 24` equals the hour (UTC); `/api/me` returns `next_sync_at`. The trigger is now `scheduled`.
- [x] **3.5 Retention.** The daily housekeeping job deletes sync runs older than 30 days and any beyond a user's newest 50.
- [x] **Done when, measured** (`tests/test_request_budget.py`, real MAL/AniList/Kitsu clients, respx counts): a sync with
      nothing changed = **1 request in total** (the bound was at most 2 per linked account); one edit = the read plus
      1 write per mirror (Kitsu 2: it first looks for an existing entry); repeated unmappable titles = 0 extra requests.
- [ ] *Later, when running more than one worker:* shared Postgres token-bucket rate limiter (limits are per process and
      event loop now), a queue per provider, and the worker as its own process (`RUN_WORKER_IN_PROCESS=false`).

Verified against the real AniList API (public lists, no key): the flat query is sorted newest first with `hasNextPage`,
and on two real lists the incremental read returns exactly the same entries as the full read. That check found a real
mismatch: entries **hidden from the status lists and in no custom list** appear in the flat query but not in
`MediaListCollection`, so the incremental read now skips them (otherwise a full read would "remove" them every week).
**Not verified:** MAL's `sort=list_updated_at` (needs a Client ID), paging on large real lists (mocks only), any write.
Known limits: removals on the master's site are noticed only by a full read (weekly); an edit made on the master's site
in the same second as the cursor can be missed until the next full read.

## Phase 4: Admin  ✔ backend done 2026-11-19 (screens in Phase 5)

Requirements (user, 2026-11-17): site and user statistics; status and logs of the daily/weekly syncs; look up a
specific user, check their status, and ban them.

Approach: an admin section in the React app (`/admin`) backed by `/api/admin/*` endpoints. Dashboards and the ban
flow are custom screens anyway, so SQLAdmin isn't the main tool; it can be added later for raw table browsing.
Access: `users.role` mapped to permissions in code; every `/api/admin` endpoint declares the permission it needs
via `require_permission(...)`, plus the Phase 2 session and CSRF protections. Design the screens on the canvas first.

Stack notes (checked 2026-11-17, no stack change needed):
- Stats come from a `daily_stats` rollup table filled by a periodic task, not live `COUNT(*)` over big tables.
- `sync_run_events` stores only notable events (failures, re-auth, 429s, unmatched), never one row per entry,
  so it stays small at tens of thousands of users. Partition by month later if it still grows.
- Job retry/cancel use Procrastinate's job manager (verify the exact 3.x API when building 4.7); its built-in
  admin UI is Django-only, so we build ours over its tables.
- Charts: a small React chart library (e.g. Recharts); pick it in Phase 5.

- [x] **4.0 Roles and permissions.** *(done; `permissions.py`, `require_permission` / `require_any_permission` in `api/deps.py`, `moderation.set_role`, `ADMIN_USER_IDS` bootstrap on startup and at sign-in, tests in `tests/test_admin.py`)* Check *permissions*, never role names, so new roles never touch endpoints.
      - `users.role`: string validated in code (`user` | `admin` now; no Postgres enum), default `user`.
      - `permissions.py`: permission constants (`stats.read`, `syncs.read`, `syncs.control`, `syncs.retry`,
        `users.read`, `users.ban`, `users.delete`, `users.manage_tokens`, `roles.manage`, `titles.link`,
        `providers.manage`, `site.manage`, …) and `ROLE_PERMISSIONS = {"user": set(), "admin": ALL_PERMISSIONS}`.
      - `require_permission(*perms)` FastAPI dependency (also rejects banned users); every `/api/admin` route
        declares what it needs, e.g. `dependencies=[Depends(require_permission("users.ban"))]`. The admin router
        additionally requires *any* admin permission, as a safety net for routes that forget one.
      - `/api/me` returns `role` and `permissions`; the UI shows/hides each nav item and button by permission
        (hiding only; the server enforces).
      - Granting/revoking roles needs `roles.manage`, is written to `admin_actions`, and the last holder of
        `roles.manage` can't remove it from themselves.
      - Bootstrap: `ADMIN_USER_IDS` promotes those users to `admin` on startup (never demotes).
      - Growth path, no endpoint changes at any step: new fixed roles = new `ROLE_PERMISSIONS` entries; several
        roles per user = `user_roles` table; roles editable in the admin UI = `roles` + `role_permissions` tables.
- [x] **4.1 Ban + user status model.** *(done: `users.banned_at/ban_reason/banned_by`, `admin_actions`, `moderation.ban/unban`, `POST /api/admin/users/{id}/ban|unban`, `PUT .../role`. Queued jobs are not deleted: `run_sync` and `push_entry` check the ban when they run. An admin can't ban themselves or anyone who can manage roles. Migration `d6f3a9b7c201`.)* `users.banned_at`, `ban_reason`, `banned_by`; `admin_actions` audit table
      (who, what, target, when, note). Banning revokes all the user's sessions (Phase 2.1), skips them in sync fan-out,
      cancels their queued jobs, and blocks sign-in and re-linking (also by provider account, so a new AniSync user
      can't be created from the same MAL/AniList/Kitsu account). Unban reverses it. Tokens stay encrypted; never shown.
- [x] **4.2 Sync logs.** *(done: `sync_run_events` with `level`, `kind` = change_failed | rate_limited | reauth | unmapped | drifted, at most 50 per mirror per run with `events_dropped` in the summary, deleted with their run; migration `e8b4c1d92a65`. The admin screens that read them are 4.4/4.5.)* `sync_runs` `trigger` values become `scheduled` | `manual` | `edit` (a run's `read`/`drifted` fields already say whether it did a full reconcile), and a
      `sync_run_events` table (run_id, account_id, level, message, media ref, created_at) for per-entry failures,
      re-auth errors and rate-limit hits. Retention ties into 3.5 (keep ~30 days).
- [x] **4.3 Stats endpoints.** *(done: `GET /api/admin/stats/overview` and `/stats/daily?days=` (1-60), `stats.read`. Deviation from the stack note: no `daily_stats` rollup yet; users, accounts and sync outcomes are live queries on indexed columns (sync runs are kept 30 days), and only provider request counts are stored (`provider_stats`, filled from in-memory counters in `metrics.py`, flushed every 5 minutes and at shutdown). "Active" = users with a browser session seen in the last 7/30 days. `sync_runs.changes` counts entries written per run. Migration `a9e1d4c7b630`.)*  Original spec:  Users (total, new per day/week, active in last 7/30 days, banned); linked accounts per
      provider; accounts needing re-auth; master choice per provider; per day: runs ok/failed, entries changed,
      provider requests and 429s per provider (needs request counting in `ServiceClient._request`).
- [x] **4.4 Sync status page.** *(done: `GET /api/admin/syncs/status` (queues by state via Procrastinate's job manager, stalled jobs = no worker heartbeat for 5 min, the last hourly batch, next batch and its size, accounts whose full read is overdue; degrades to `queue_available: false` if the queue can't be read), `/syncs/runs` (filters status/trigger/user, `before_id` paging), `/syncs/runs/{id}` (summary + events), `/syncs/events` (level/kind/provider/user filters); `syncs.read`.)*  Original spec:  The last scheduled runs and the weekly full reads: started/finished, users processed, failures, queue
      depth and stuck jobs (Procrastinate jobs table); drill into a run's events.
- [x] **4.5 User lookup page.** *(done: `GET /api/admin/users?q=&banned=&limit=&offset=` (id, name or provider username; wildcards are escaped), `GET /api/admin/users/{id}` (accounts with token state and list size, sessions, recent runs, admin action history; no tokens), `POST /api/admin/users/{id}/sync` (`syncs.retry`, audited); `users.read`. Ban/unban and role are in 4.1. "View preview", force full re-check, unlink and delete are 4.10.)*  Original spec:  Search by AniSync id, display name, or provider username; show linked accounts
      (provider, username, token expiry/re-auth state, sync flags), recent runs with events, ban state and admin
      action history; buttons: ban/unban (with reason), trigger sync, view preview.
- [x] **4.6 Provider health + kill switch.** *(done: `provider_settings`; `GET /api/admin/providers` (accounts, requests/429s/errors/avg ms today and over 7 days from `provider_stats`; `stats.read`), `PUT /api/admin/providers/{id}` `{paused, reason}` (`providers.manage`, reason required, audited on change). A paused provider: a paused mirror is skipped by syncs (shown as `paused` in the run summary, not an error) and by edit pushes, and catches up after resume because its snapshot lacks the change; a paused master stops its user's sync, refuses list edits and refresh with 503 (the list is still served from our copy). Public `GET /api/providers` lists every provider with `paused` and the reason, for the sign-in page and UI. Per-provider rate-limit override: not built.)*  Original spec:  Per provider: error rate, latency, 429s (from 4.3 counters). A
      `provider_settings` table (`paused`, `paused_reason`, optional rate-limit override) read at runtime: paused
      providers are skipped by sync and `push_entry` (jobs deferred, not failed) and the UI says so.
- [x] **4.7 Job controls.** *(done: `PUT /api/admin/syncs/scheduled {paused}` (`syncs.control`) stops the hourly tick and skips queued sync/push jobs, which is safe because the next sync catches up from snapshots; `GET /api/admin/syncs/jobs?status=todo|doing|failed`, `POST .../jobs/{id}/cancel` (`syncs.control`, `abort=true` for running ones) and `.../retry` (`syncs.retry`, queues a copy of a failed or cancelled job). Job lists show ids only, never list data. All audited. Not verified against a real Procrastinate database: the tests use the in-memory connector.)*  Original spec:  Pause/resume scheduled syncs globally (flag in DB, checked by the periodic
      tasks); retry failed jobs, cancel stuck jobs (Procrastinate job manager).
- [x] **4.8 Unmatched titles queue.** *(done: every applied sync records the titles it couldn't translate (`unmatched_titles`, plus `unmatched_title_users` for the affected-user count; a user's set is replaced on each sync); `GET /api/admin/titles/unmatched?source=&target=&q=` (most affected first, titles mapped since are left out), `POST /api/admin/titles/link {source, source_id, target, target_id}` (`titles.link`; stored as `source=manual`, 409 on conflicts, audited; affected users get it at their next sync). Daily housekeeping purges resolved rows and rows unseen for 30 days. Migration `c8d2e5a1f947`.)*  Original spec:  Entries the resolver couldn't map (recorded during sync, with counts of how
      many users have them). Admin links IDs by hand → writes `media_external_ids` rows marked `source=manual`, which
      the dataset import (1.7) never overwrites. Affected users get it at their next sync.
- [x] **4.9 Site banner + maintenance mode.** *(done: `site_settings`; public `GET /api/site` (banner text and level, maintenance flag and message); `GET|PUT /api/admin/site` (`site.manage`, plain text only, `<`/`>` refused, audited by changed field). Maintenance mode answers every write with 503 + `Retry-After` through an app-wide dependency, except `/api/admin/*` and sign-out, and pauses the background jobs. The UI must render banner text as text. Migration `b4f7a2c9d815`.)*  Original spec:  Banner text/level stored in DB, shown by the frontend
      (`GET /api/site`). Maintenance mode returns 503 on writes and pauses jobs.
- [x] **4.10 User actions** *(done: `POST /api/admin/users/{id}/full-check` and `/preview` (`syncs.retry`); `POST .../accounts/{aid}/revoke-tokens` and `DELETE .../accounts/{aid}` (`users.manage_tokens`; a banned user's accounts can't be unlinked, or they could sign up again as a new user); `DELETE /api/admin/users/{id}?confirm={id}` (`users.delete`; refused for yourself, banned users and role managers) and self-service `POST /api/me/delete {confirm: "delete my account"}` through `deletion.py`. Deleting removes accounts, tokens, list copies, sessions, runs and events through the database cascades; admin audit rows stay without the user link and carry the deleted user's id and name in `details`. Provider-side tokens are not revoked (no provider API for it). Known gap: a banned user can't be erased without unbanning first, because we keep no separate ban list.)*  Original spec:  on the lookup page: force full reconcile, unlink a broken account, show sync preview,
      revoke tokens (forces re-link), **delete user and all their data** (also needed for privacy requests and
      MAL §3(c); reuse for self-service account deletion).
- [x] **4.11 Expiring AniList tokens.** *(done: `GET /api/admin/accounts/expiring?days=30` (`users.read`, soonest first, `expired` vs `expiring`, only connections we can't renew themselves); `/api/me` accounts now carry `needs_relink` and `expires_soon` for the on-site banner.)*  Original spec:  List accounts whose token expires within 30 days, and show users an
      on-site banner before it does (we have no emails: MAL/AniList sign-in doesn't give us one).
- [x] **4.12 Dataset import status.** *(done: `GET /api/admin/dataset` (last 10 imports, titles in the mapping, ids per provider split seed/learned/manual; `stats.read`) and `POST /api/admin/dataset/import {force}` (`providers.manage`, refused while one runs, audited).)*  Original spec:  Last run of the 1.7 import, rows added/changed, coverage per provider, errors;
      button to re-run.
- [ ] Tests: authorization (every `/api/admin` route declares a permission, checked by a test that walks the router;
      users without it and banned users get 403; bootstrap promotes but never demotes; last `roles.manage` holder
      can't drop it), ban effects (sessions revoked, excluded from sync, can't
      sign in or re-link), stats queries, paused-provider behaviour, manual ID links surviving re-import,
      user deletion removes everything.

### Phase 4b: Admin, later (once there are real users)

- [ ] **4.13 Read-only "view as user".** Admin sees a user's dashboard/list exactly as they do. Strictly read-only
      (enforced server-side, not just hidden buttons), always written to `admin_actions`, visible banner while active.
- [ ] **4.14 Revenue and costs.** Monthly donations and (future) ad revenue entered or imported, compared against
      AniList's $150/month threshold; DB size and table growth.
- [ ] **4.15 Admin audit log viewer + more roles.** Browse/filter `admin_actions`. Add `viewer` (only the `*.read`
      permissions) and, if needed, `support` (reads + `users.ban` + `syncs.retry`) as `ROLE_PERMISSIONS` entries.
- [ ] **4.16 Abuse limits.** Cooldown on manual "sync now" per user (e.g. once per 15 min) and on list edits per
      minute; admin list of users who hit limits often.

### Later, only if users ask: email + password sign-in

Decided 2026-11-18 to stay with provider OAuth for now. Every user needs a provider account to sync anything, so an
email login adds a step without removing one, and Kitsu users already sign in with their Kitsu credentials. Costs:
email delivery (SPF/DKIM, deliverability), holding emails and password hashes (privacy liability), fake registrations
and "no link arrived" support. Revisit if users ask for it or a provider's OAuth becomes unreliable.
Design if we do it (nothing built now blocks it; a user with no linked accounts is already valid):
- [ ] `users.email` (unique on lower()), `users.password_hash` (nullable, argon2id, min length 10), `email_verified_at`;
      `email_tokens` (user, SHA-256 token hash, purpose verify|reset, expiry, single use).
- [ ] `POST /auth/register` (always "check your email"; an existing address gets a notice mail instead), verify link,
      `POST /auth/login` (generic error, unverified = wrong password, dummy hash for unknown emails), resend, password
      reset (ends other sessions). Rate limits per IP and per email. Unverified users can't sign in.
- [ ] A mail interface (log in dev/tests, SMTP in production); daily purge of expired tokens and unverified
      registrations older than 7 days; admin lookup shows email and verified status; frontend onboarding state for
      "no linked accounts yet".

## Phase 5: Frontend  ← in progress

**5.1 done 2026-11-19 (foundation):** the canvas stylesheet is the app stylesheet (`src/styles/anisync-m3.css`, `html.as.theme-*`),
theme switch with a pre-React script (localStorage only for now; saving it per user needs a `users.theme` column and
`PATCH /api/me`, not built yet), API client + `useMe`/`useProviders`/`useSite`, router, Vite proxy for `/api` and `/auth`,
landing page. Build and lint are clean. **5.2 done 2026-11-19 (setup flow):** Link lists (`/link`, OAuth buttons and the Kitsu password form), Choose master (`/master`, master radio, per-mirror switches), Review first sync (`/review`, dry run with per-mirror tabs, filters, unmatched list, Apply). Checked in Chrome against the real backend with a seeded user: master change and switches persist, the preview error path shows the server's message, and Apply posts once and goes to the dashboard; the preview itself was a mocked response because the dev accounts have no real tokens. **Not verified:** real OAuth round trips, a real preview. **5.3-5.7 done 2026-11-19 (every screen):** app shell (side rail, bottom bar on phones, site banner), Dashboard, My list with the entry editor
(side panel, bottom sheet on phones, undo, "check master" refresh), Settings (profile name and picture, accounts, sync switches,
theme saved per user with new `users.theme` + `PATCH /api/me {theme}`, sessions, delete account), Privacy, maintenance gate,
404, and the admin area (Overview, Syncs and logs, Users and user detail, Unmatched titles, Providers and dataset, Banner and
maintenance) under `/admin`. Checked in Chrome against the real backend with a seeded admin user: the shell, dashboard, settings
and all admin pages load and the admin pages show live data. **Not verified:** phone layouts and the bottom sheet (the browser
window couldn't be resized), light mode, anything needing real provider tokens (list load, sync, previews), the admin write
actions (ban, role, pause, import) in the UI, real OAuth. **Not built:** "Add anime" (needs a title search endpoint that doesn't
exist), donation card (no platform chosen), a Playwright browser smoke test (needs a browser download), a chart on the admin overview (a table for now).
*Fixed 2026-11-19:* `/api/list` and the other endpoints answer 502 ("a list site isn't responding") or 429 (rate limited, with the provider's name) instead of 500 when a provider fails.
**One sync at a time (2026-11-19, backend 455 tests):** `sync.start_run` opens a run under a per-user advisory lock and refuses when one is unfinished and under 15 minutes old (`run_sync` answers `skipped`, `POST /api/sync` answers 409); a run that raises is now closed as `error` instead of staying "running" forever (this is what could leave the dashboard stuck). The lock itself is not covered by a test (needs two real connections).
**Long syncs (2026-11-19, backend 466 tests, frontend 50; migration `e5b8c2d94f17` adds `sync_runs.planned/processed`):** a change refused with "rate limited" now waits out the provider's retry time (capped at 60 s, up to 3 retries) instead of failing the next dozens of changes; changes are committed in batches of 25, so the dashboard shows "Running · 40 of 300" and an interrupted sync keeps what it wrote; runs that stopped halfway become `interrupted` (all open runs at startup when the worker runs in-process, runs older than 15 minutes in the daily housekeeping) and show as "Interrupted". Not verified against a real provider's rate limiting: the pacing is tested with fakes only; the ~25/min AniList limiter is unchanged.
**Kitsu quirks found with the first real sync (2026-11-19):** (1) adult titles are hidden from API listings while the Kitsu account's `sfwFilter` is on, so the owner can't see the existing entry and a create answers 422 "animeId has already been taken"; the change is now *skipped* with the reason (`ChangeSkipped`, `skipped`/`skipped_reason` in the run summary, an info event, not an error) and shown on the dashboard. The same filter means a full read of the Kitsu mirror misses those entries. Turning the filter off in the Kitsu account settings fixes it; AniSync does not change that setting itself. The skipped changes are still planned again on every sync (about 2 requests each); a negative cache would stop that. (2) Kitsu counts fewer episodes than the master for some titles (422 "progress cannot exceed length of media"): progress is now capped at Kitsu's `episodeCount` and retried once. (3) The Kitsu token endpoint refuses the default HTTP User-Agent (fixed earlier).
**Adult content (2026-11-19, migration `f7c3d9a2e158` `linked_accounts.hides_adult`, backend 483 tests, frontend 56):** how each provider treats adult titles, checked against a real account: MAL leaves them out of the list API unless `nsfw=true` is sent (the client does: 897 vs 962 entries); AniList returns them to the owner even with "Display adult content" off (957 entries, 26 adult); Kitsu hides them while the account's SFW filter is on (Kitsu Settings → Adult Content). Kitsu's filter is read from the profile at link time, on every full read and on manual syncs and previews (one request, only for providers with an `adult_help`); a skipped title also sets the flag. The user is told on the link page, the dashboard and Settings, with the provider's own instructions (`Provider.adult_help`, served by `/api/providers`). A master that hides titles never causes deletions on the mirrors (full sync and push), because hidden is not the same as removed. *Open question:* an option to leave adult titles out of the sync, and blurring adult covers in the UI; needs an adult flag per title from each provider (MAL `nsfw` field, AniList `isAdult`, Kitsu `nsfw`).
**5.8 frontend tests done 2026-11-19:** Vitest + Testing Library, 45 tests (`npm test`): image host allow-list, relative times, theme, landing (paused provider, maintenance), link lists (Kitsu form errors), choose master, review (loading view, filters, apply once, errors), dashboard (setup redirect, expiry warning, sync), my list (filters, score on the 0-100 scale, undo, remove confirm), settings (name, theme, delete phrase, unlink), admin (permission gating, ban reason, delete confirm, site, provider pause). `fetch` is stubbed per test and an unmatched request fails the test. Six deliberate breaks each failed a test. The tests found a real bug (provider ids flashed in lowercase before names loaded); screens now wait for the providers list.
*Real-account fixes after the first sync test:* Kitsu's login endpoint returned 403 (bot protection) to the default HTTP User-Agent, now every provider request sends `User-Agent: AniSync/0.1` (`anisync/http.py`); the setup flow now continues until the first applied sync or an explicit skip.
Settings, profile screens, admin screens, Privacy page, maintenance page, banner, tests (Vitest + a Playwright smoke test).


- [x] **Landing hero: drifting poster wall, built 2026-11-19** (`ui/PosterWall.tsx`, posters in `src/data/posters.json`,
      refreshed with `node scripts/update-posters.mjs`; 48 popular titles hotlinked from AniList's CDN; design: canvas
      "1 · Sign in"). The mascot is deferred until a graphic designer delivers one. Full-bleed wall of 15 columns of posters drifting
      vertically (pure CSS `translateY` keyframes, alternating direction, 150-300 s per loop, posters listed twice for a
      seamless loop), under a surface-coloured scrim (works in light and dark) with one M3 card (headline, provider
      chips, sign-in buttons). A few posters carry provider monograms as a sync hint. `prefers-reduced-motion` stops the
      drift; phone crops to about 3 columns; wall is `aria-hidden`, images `alt=""`, lazy-loaded, about 40 titles.
      Posters come from a small fixed list we store (cover URLs checked against provider `image_hosts`), so the page
      works signed out. **Before launch:** add "posters on a public page" to the Phase 6 provider-terms check.
- [ ] **Profile screens (backend done).** Header avatar (initials tile when `avatar_url` is null) and a Settings "Profile"
      card: a name field (`PATCH /api/me`, 1-40 chars) and, per linked account, "use this picture"
      (`PUT /api/me/avatar {account_id}`, reads the provider live; choosing the master again is the "update from master"
      action, nothing follows a master change by itself) plus "remove" (`DELETE /api/me/avatar`). Add these to the canvas
      first (Settings, header, admin user detail). Pictures are hotlinked and only stored when https on the provider's
      `image_hosts`; apply the same check to anime covers. MAL's `picture` field is unverified until a Client ID exists.

- [ ] Public pages (landing, about, privacy) prerendered to static HTML at build time for SEO and future ads;
      the logged-in app stays a plain SPA. No Next.js needed.
- [ ] Theme: Material 3 light + dark, tokens from the canvas stylesheet.
      - Logged-in pages: moon/sun icon button at the top right of the top bar toggles light/dark; Settings →
        Appearance offers Match system / Light / Dark.
      - Remembered per user: `users.theme` (`system` | `light` | `dark`, default `system`), saved with
        `PATCH /api/me`, returned by `GET /api/me`, so it follows the user across devices.
      - Also mirrored to `localStorage` and applied by a tiny inline script in `index.html` before React loads,
        so there's no light flash on page load. On sign-in the account value wins.
      - Logged-out pages follow the OS setting (or the last `localStorage` value).
- [ ] Anime cover images: hotlink the master provider's image URL (AniList `coverImage.large` + `coverImage.color`
      as the loading tint, MAL `main_picture`, Kitsu `posterImage`); never re-host or edit them (MAL §3(a)(xii)).
      Store the URLs on `media` during sync; lazy-load; fall back to the placeholder tile.
- [x] `GET /api/providers` consumed by `useProviders` (names, monograms, sign-in buttons, paused state). The one place that maps provider ids to colour classes is `ui/Provider.tsx`.
- [ ] Build the canvas screens: Sign in, Link lists, Choose master, Review first sync, Dashboard, My list.
      API client + types, routes, Vite proxy for `/api` and `/auth`.

## Phase 6: Real-world test and deploy

- [ ] Register MAL + AniList apps; end-to-end test sign-in, linking, preview, apply on throwaway accounts.
- [ ] Dockerfile; Railway (or Cloudflare Pages + Neon + Render) config; production `PUBLIC_URL`, secure cookies,
      secrets in the host's secret store.
- [ ] Production data protection: Postgres reachable only from the app (private network, no public port), TLS to
      the DB if the host offers it, encrypted automated backups with tested restore, least-privilege DB user for the
      app (no superuser), and access to the hosting dashboard limited to 2FA-protected accounts.
- [ ] Re-check provider docs/changelogs and API terms before launch (MAL API agreement, AniList terms of use, Kitsu).
      Specific checks:
      - MAL §3(c) forbids storing MAL content "that consists of personal information". List snapshots (Phase 1.6/3)
        hold users' list data server-side; confirm that's acceptable, or limit what we keep, and delete everything
        when a user unlinks an account or deletes their AniSync account. Add account deletion if it's missing.
      - AniList forbids using its API "as a backup or data storage service" and mass data collection: snapshots are
        a sync cache only; never offer list export/backup features based on them.
      - MAL §4(b): changes that significantly increase load on their servers (or adding ads) need review.
- [ ] Monitoring with existing tools, not built in-house: **Sentry** (free tier) for errors with stack traces and
      alerts; **Better Stack or UptimeRobot** (free) for uptime checks on `/api/health`. Link both from the admin panel.
- [ ] Provider logos: MAL §17 bars its marks "in or as part of" apps beyond source attribution, and AniList/Kitsu
      publish no logo guidelines. Use the colored monogram chips; ask each provider for written permission before
      ever showing their logos.
- [ ] Donation link (one-off only, see Decisions) and footer: non-affiliation line + dataset attribution naming both anime-offline-database projects (ODbL 1.0).

## Possible future providers (verify each before committing)

Simkl, Shikimori, Annict: real list APIs with OAuth, likely good fits. AniDB: unusual API, strict bans.
Trakt: anime tracked as general TV shows, episodes/seasons don't map cleanly. Anime-Planet: no public API.

## Open questions

- Who are the admins? Decided: `users.role`; the user's own account is bootstrapped via `ADMIN_USER_IDS`.
- Donation platform (Ko-fi one-time, GitHub Sponsors one-time, …): decide in Phase 6. Must support one-off only
  without showing monthly goals.
