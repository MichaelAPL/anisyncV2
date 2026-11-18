# Roadmap / status

Update this file at the end of every working session. Work the phases in order; each task lists what "done" means.

## Done

**2026-11-16**
- Backend scaffold: models, Alembic initial migration, MAL/AniList/Kitsu clients, normalized model,
  diff engine, ID mapper, sync orchestration, Procrastinate jobs (nightly + per-edit push), FastAPI auth + API.
- Verified: 14 unit tests pass; migrations + job schema apply on real Postgres; API boots with in-process worker;
  ID-mapper merge logic against the DB; live public lookups (AniList MAL→AniList, Kitsu MAL→Kitsu);
  job queue + per-user queueing lock.
- Frontend: Vite + React + TS scaffold only (`react-router`, `@tanstack/react-query` installed).
- UI flows designed on the Claude Design canvas (see AGENTS.md → Design).

**2026-11-17**
- Reviewed security, capacity and extensibility, and agreed the plan below.
- Answered the open questions: anime only, admin requirements, and donations-only funding (see Decisions).
- Redesigned the canvas in Material 3 (dark mode, phone layouts, admin screens). Roles → permissions model (4.0).
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

## Phase 1: Provider-agnostic data model  ← next

Everything else builds on this, so do it before the frontend. One Alembic migration; the dev DB holds no
real data yet, so dropping the old tables is fine.

- [ ] **1.0 Test infrastructure first.** Add Hypothesis, pytest-cov; DB fixtures (`anisync_test`, migrate once
      per session, rollback per test); API client fixture; move existing respx payloads into `tests/fixtures/`.
      (Git repo, first commit and CI are done; add a Postgres service to CI with the first DB tests.)
- [ ] **1.1 Provider registry.** `providers/` package with a `Provider` descriptor per service: `id` (str),
      display name, monogram, chip colors, `auth_kind` (`oauth_code` | `oauth_pkce_plain` | `password` | `api_key`),
      `score_step`, `rate_limit`, supported media types, client class, auth hooks (authorize URL / exchange / refresh / password login).
      `CLIENTS` dict and `sync.REFRESHERS` go away. *Done when* nothing outside `providers/` names MAL/AniList/Kitsu.
- [ ] **1.2 Provider column becomes a string.** Replace the Postgres `service` enum with a `String` validated
      against the registry.
- [ ] **1.3 Canonical media + external IDs.** Replace `anime_ids` with `media(id, title, episodes, cover_url, cover_color,
      cover_url, …)` and `media_external_ids(media_id, provider, external_id, unique(provider, external_id))`.
- [ ] **1.4 `ListEntry` generalised.** `media_id: str` (some providers use string IDs), `external_ids: dict[str, str]`
      instead of `mal_id/anilist_id/kitsu_id`. `progress` is episodes.
      Update the engine's prune-safety hints to use `external_ids`.
- [ ] **1.5 Resolver through canonical media.** `mapping.py` resolves provider→canonical→provider. Each client may
      implement an optional `lookup_external_ids(ids)`; no pairwise code paths.
- [ ] **1.6 List snapshots table.** `list_snapshots(account_id, media_id, status, progress, score, rewatching,
      started_at, finished_at, remote_updated_at, last_written_hash)`: what we last read from or wrote to each
      account (logic arrives in Phase 3).
- [ ] **1.7 Seed IDs from offline datasets.** Import job for manami-project `anime-offline-database`
      (ODbL 1.0: requires attribution, so add it to the site footer/README) and optionally Fribb `anime-lists`
      (check its license). Run on deploy, then weekly as a periodic task. *Done when* mapping a typical list
      needs ~0 remote lookups.
- [ ] Tests: registry, resolver, seeding parser, migration up/down; all existing tests still pass.

## Phase 2: Security hardening

- [ ] **2.1 Server-side sessions.** `sessions(id, token_hash sha256, user_id, created_at, last_seen_at, expires_at,
      user_agent)`. The cookie holds a random token; rotate on login; `POST /auth/logout-all`. The OAuth state/PKCE
      verifier can stay in a short-lived signed cookie. Replaces Starlette `SessionMiddleware` for auth.
- [ ] **2.2 Key rotation.** `TOKEN_ENCRYPTION_KEYS=new,old` → `MultiFernet`; CLI command `anisync rotate-keys`
      re-encrypts all tokens. Keep accepting single `TOKEN_ENCRYPTION_KEY` for dev.
- [ ] **2.3 CSRF.** Middleware rejecting state-changing requests whose `Origin`/`Referer` isn't `PUBLIC_URL`.
- [ ] **2.4 Generic auth routes** driven by `auth_kind` (`/auth/{provider}/login|callback`, `/auth/{provider}/password`).
      Evaluate Authlib for the OAuth client (it must support MAL's PKCE `plain`); adopt it only if it shrinks the code.
- [ ] **2.5 SQL injection guard.** Already safe: every query uses SQLAlchemy bound parameters and there is no raw SQL
      (checked 2026-11-18). Lock it in: enable ruff's `S` (flake8-bandit) rules, including S608, so CI fails on
      SQL built from strings. Rule: never `text()` / `exec_driver_sql` with interpolated values.
- [ ] **2.6 Keep secrets out of logs.** Uvicorn's access log records full URLs, including OAuth callbacks
      (`?code=…&state=…`): add a logging filter that redacts `code`, `state`, `token`, `access_token`, `password`
      query values. One `redact()` helper for exception text logged from provider calls (`sync.py`); never log
      request headers or bodies. Test: a callback request and a failing provider call leave no secrets in captured logs.
- [ ] **2.7 Rate-limit credential endpoints.** `POST /auth/kitsu` (could otherwise be used to test stolen Kitsu
      passwords) and the OAuth login/callback routes: per IP and per user, e.g. 5 attempts / 15 min, in Postgres so it
      works across processes. Return 429 with `Retry-After`.
- [ ] **2.8 Explicit response schemas.** Every endpoint declares a pydantic `response_model` (list, preview, runs,
      entry endpoints still return raw dicts). Never return ORM objects. Mandatory for `/api/admin/*`, which handles
      other users' data. Token fields never appear in any schema.
- [ ] **2.9 Safe errors.** Debug off in production; one exception handler turns unexpected errors into a generic 500
      with a request ID (also logged, and sent to Sentry in Phase 6), never a stack trace or SQL in the response.
- [ ] **2.10 Ownership tests.** For every user-scoped endpoint, a second user tries to read/change the first user's
      accounts, list entries, runs and preview and gets 404. A test walks the router so new endpoints can't be added
      without an ownership or permission check (pairs with the 4.0 permission test).
- [ ] Tests for session lifecycle, revocation, key rotation, CSRF rejection, log redaction, rate limits.

## Phase 3: Sync efficiency (provider request budget)

Rough budget: AniList allows ~90 req/min (we use 25); MAL and Kitsu don't publish limits. The current design
(full refetch of master + mirrors nightly, all at 04:00) tops out around 1–2k users.

- [ ] **3.1 Serve `/api/list` from snapshots**, not live provider calls; refresh snapshots during syncs and after edits.
- [ ] **3.2 Incremental master reads.** MAL list sorted by `list_updated_at`, AniList `mediaList` sorted
      `UPDATED_TIME_DESC`: page until older than the account's sync cursor.
- [ ] **3.3 Diff mirrors against snapshots** (what we last wrote) instead of refetching; **weekly full reconcile**
      catches edits made directly on mirror sites.
- [ ] **3.4 Stagger the nightly sync.** Hourly periodic task syncs the users whose `hash(user_id) % 24` matches the hour.
- [ ] **3.5 `sync_runs` retention** (e.g. keep 30 days / last 50 per user).
- [ ] *Later, when running >1 worker:* shared Postgres token-bucket rate limiter, a queue per provider, and the
      worker as its own process (`RUN_WORKER_IN_PROCESS=false`).
- *Done when:* a no-change nightly sync costs ≤2 requests per linked account, measured in tests via respx call counts.

## Phase 4: Admin

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

- [ ] **4.0 Roles and permissions.** Check *permissions*, never role names, so new roles never touch endpoints.
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
- [ ] **4.1 Ban + user status model.** `users.banned_at`, `ban_reason`, `banned_by`; `admin_actions` audit table
      (who, what, target, when, note). Banning revokes all the user's sessions (Phase 2.1), skips them in sync fan-out,
      cancels their queued jobs, and blocks sign-in and re-linking (also by provider account, so a new AniSync user
      can't be created from the same MAL/AniList/Kitsu account). Unban reverses it. Tokens stay encrypted; never shown.
- [ ] **4.2 Sync logs.** `sync_runs` gets `trigger` values `nightly` | `weekly_reconcile` | `manual` | `edit`, and a
      `sync_run_events` table (run_id, account_id, level, message, media ref, created_at) for per-entry failures,
      re-auth errors and rate-limit hits. Retention ties into 3.5 (keep ~30 days).
- [ ] **4.3 Stats endpoints.** Users (total, new per day/week, active in last 7/30 days, banned); linked accounts per
      provider; accounts needing re-auth; master choice per provider; per day: runs ok/failed, entries changed,
      provider requests and 429s per provider (needs request counting in `ServiceClient._request`).
- [ ] **4.4 Sync status page.** Last nightly and weekly runs: started/finished, users processed, failures, queue
      depth and stuck jobs (Procrastinate jobs table); drill into a run's events.
- [ ] **4.5 User lookup page.** Search by AniSync id, display name, or provider username; show linked accounts
      (provider, username, token expiry/re-auth state, sync flags), recent runs with events, ban state and admin
      action history; buttons: ban/unban (with reason), trigger sync, view preview.
- [ ] **4.6 Provider health + kill switch.** Per provider: error rate, latency, 429s (from 4.3 counters). A
      `provider_settings` table (`paused`, `paused_reason`, optional rate-limit override) read at runtime: paused
      providers are skipped by sync and `push_entry` (jobs deferred, not failed) and the UI says so.
- [ ] **4.7 Job controls.** Pause/resume the nightly and weekly syncs globally (flag in DB, checked by the periodic
      tasks); retry failed jobs, cancel stuck jobs (Procrastinate job manager).
- [ ] **4.8 Unmatched titles queue.** Entries the resolver couldn't map (recorded during sync, with counts of how
      many users have them). Admin links IDs by hand → writes `media_external_ids` rows marked `source=manual`, which
      the dataset import (1.7) never overwrites. Affected users get it at their next sync.
- [ ] **4.9 Site banner + maintenance mode.** Banner text/level stored in DB, shown by the frontend
      (`GET /api/site`). Maintenance mode returns 503 on writes and pauses jobs.
- [ ] **4.10 User actions** on the lookup page: force full reconcile, unlink a broken account, show sync preview,
      revoke tokens (forces re-link), **delete user and all their data** (also needed for privacy requests and
      MAL §3(c); reuse for self-service account deletion).
- [ ] **4.11 Expiring AniList tokens.** List accounts whose token expires within 30 days, and show users an
      on-site banner before it does (we have no emails: MAL/AniList sign-in doesn't give us one).
- [ ] **4.12 Dataset import status.** Last run of the 1.7 import, rows added/changed, coverage per provider, errors;
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

## Phase 5: Frontend

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
- [ ] `GET /api/providers` (name, monogram, colors, auth kind) so the UI has no hardcoded providers.
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
- [ ] Donation link (one-off only, see Decisions) and footer: non-affiliation line + manami ODbL attribution.

## Possible future providers (verify each before committing)

Simkl, Shikimori, Annict: real list APIs with OAuth, likely good fits. AniDB: unusual API, strict bans.
Trakt: anime tracked as general TV shows, episodes/seasons don't map cleanly. Anime-Planet: no public API.

## Open questions

- Who are the admins? Decided: `users.role`; the user's own account is bootstrapped via `ADMIN_USER_IDS`.
- Donation platform (Ko-fi one-time, GitHub Sponsors one-time, …): decide in Phase 6. Must support one-off only
  without showing monthly goals.
