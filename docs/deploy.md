# Deploying AniSync

How to go from an empty VPS to a tested production deployment, and how to keep it healthy. The deployment is described in `config/deploy.yml`, `.kamal/secrets`, `.kamal/hooks/pre-deploy`, `deploy/postgres-init/` and `deploy/backup/`.

## Shape

- One VPS. It accepts no inbound connections from the internet: the firewall denies everything except Tailscale.
- The public reaches the app only through a **Cloudflare Tunnel**: Cloudflare's edge (WAF, rate limiting) → `cloudflared` (a Kamal accessory, outbound connection) → `kamal-proxy` → the app container, all on the private `kamal` Docker network. No container publishes a port on the host.
- You reach the server over **Tailscale** (OpenSSH, key-only). Kamal deploys over that SSH connection.
- **Postgres 17** runs as a Kamal accessory on the same VPS with three roles:
  - `anisync_owner` owns the schema and is used only by migrations.
  - `anisync_app` is what the app connects as: data only, no schema changes.
  - `anisync_backup` is read-only.
- A nightly **backup** is encrypted with age and stored in Cloudflare R2.
- **Errors** go to Sentry (scrubbed of tokens, codes and request bodies). An **uptime monitor** checks `/api/health` through Cloudflare and watches the backup heartbeat.

## 1. Accounts and keys (once)

| What | Notes |
|---|---|
| Domain on Cloudflare | The zone that will serve `ANISYNC_DOMAIN`. |
| VPS | 2 GB RAM or more (Postgres, the app, and the ID dataset import's ~120 MB peak). Debian/Ubuntu LTS. |
| Tailscale | Your machine and the VPS on the same tailnet. |
| Cloudflare Tunnel | Zero Trust → Networks → Tunnels → create a tunnel (remotely managed). Copy its token. Public hostname: `ANISYNC_DOMAIN` → service `http://kamal-proxy:80`. Add nothing else (no SSH, no database). |
| R2 bucket | One bucket for backups. Lifecycle rules: delete `db/` objects after 30 days, and `incoming/` objects after 2 days (uploads land there and are moved into `db/` only once complete). Bucket Lock on `db/` for 30 days, so a compromised server cannot delete recent backups. An API token scoped to this bucket only (object read & write) for the server, and a read-only one for restore drills. |
| age key pair | `age-keygen -o anisync-backup.key`. The public key (`age1…`) goes to the server; the private key goes to your password manager and never to the server. |
| Sentry | A project for the backend; copy the DSN. |
| Uptime monitor | Better Stack or UptimeRobot: (1) an HTTP check on `https://ANISYNC_DOMAIN/api/health`; (2) a heartbeat (cron) check expecting one ping a day with about 26 hours of grace. Its URL is `BACKUP_HEARTBEAT_URL`. |
| Kamal | `gem install kamal` (2.12 or later), or run Kamal from its Docker image. |

Turn on two-factor authentication for every one of these accounts (Cloudflare, the VPS provider, Tailscale, Sentry, the uptime monitor): each of them can reach the server or its data.

Generate the app secrets locally:

```bash
openssl rand -hex 32                      # SESSION_SECRET
cd backend && uv run anisync generate-key # TOKEN_ENCRYPTION_KEYS: store this in the password manager too
openssl rand -hex 24                      # one each for the postgres, owner, app and backup database passwords
```

Keep every value in your password manager. `.kamal/secrets` reads them from environment variables named `ANISYNC_…` (see that file), so export them in the shell you deploy from, or change the lines to password-manager lookups.

## 2. Harden the VPS

As root on the provider's console, then over Tailscale:

1. Create a `deploy` user with your SSH key, in the `docker` group (that group is root-equivalent; it is the only user that needs it).
2. Install Tailscale and join the tailnet. Note the machine's MagicDNS name: that is `ANISYNC_HOST`.
3. SSH: `PasswordAuthentication no`, `PermitRootLogin no`. Keep OpenSSH listening normally; the firewall decides who reaches it.
4. Firewall: `ufw default deny incoming`, `ufw default allow outgoing`, `ufw allow in on tailscale0`, `ufw enable`. Also deny all inbound in the provider's cloud firewall (optionally allow UDP 41641 for direct Tailscale connections).
5. `unattended-upgrades` for security updates.
6. Install Docker (or let `kamal setup` do it), then create the network with a fixed subnet **before** the first `kamal setup`, because the app trusts forwarded headers only from this subnet (`FORWARDED_ALLOW_IPS` in `config/deploy.yml`):

   ```bash
   docker network create --subnet 172.30.0.0/16 kamal
   ```

7. Migration credentials, readable only by `deploy`:

   ```bash
   sudo install -d -o deploy -m 0700 /etc/anisync
   printf 'DATABASE_URL=postgresql://anisync_owner:%s@anisync-db:5432/anisync\n' '<owner password>' \
     | sudo -u deploy tee /etc/anisync/migrator.env >/dev/null
   sudo chmod 0600 /etc/anisync/migrator.env
   ```

8. Build the backup image on the server (copy `deploy/backup/` over first, e.g. with `scp -r`):

   ```bash
   docker build -t anisync-backup:1 deploy/backup
   ```

   Bump the tag (and `config/deploy.yml`) when `deploy/backup` changes.

From your machine, check that nothing is reachable from the internet: `nmap -Pn -p- <public IPv4>` (and the IPv6 address) should show no open port.

## 3. Cloudflare settings

- SSL/TLS: **Full (strict)**, Always Use HTTPS on, minimum TLS 1.2. HSTS once everything works: start with `max-age=86400`, no preload, raise later.
- **Bot Fight Mode: off.** On the free plan nothing can exempt `/api` or the OAuth callbacks from it, and a challenge answered to the app's own `fetch()` calls breaks the app. Don't use "I'm Under Attack" or Rocket Loader either.
- Rate limiting (one rule on the free plan): path starts with `/auth/`, per IP, block. The app keeps its own per-IP limits on top.
- Cache rule: bypass cache for `/api/*` and `/auth/*`.
- WAF custom rules (five on the free plan): block scanner paths such as `/wp-admin`, `/.env`, `/.git`. Never challenge `/api` or `/auth`.

## 4. Provider apps

Register production apps with these exact redirect URLs (no trailing slash in `PUBLIC_URL`):

- MAL (https://myanimelist.net/apiconfig): `https://ANISYNC_DOMAIN/auth/mal/callback`
- AniList (https://anilist.co/settings/developer): `https://ANISYNC_DOMAIN/auth/anilist/callback`

Name them so they are clearly unofficial (see ROADMAP → Decisions, branding).

## 5. First deploy

```bash
export ANISYNC_HOST=<tailnet name> ANISYNC_DOMAIN=<public hostname>
# …and every ANISYNC_* secret from .kamal/secrets
kamal setup
```

`kamal setup` installs Docker if needed, starts the accessories (the database creates its roles from `deploy/postgres-init/` on first start), builds and delivers the image, and runs `.kamal/hooks/pre-deploy`. That hook:

1. migrates as `anisync_owner`;
2. applies the Procrastinate schema if the database doesn't have it;
3. seeds the ID mapping on an empty database;
4. re-applies the app role's grants.

Sign-in starts **closed**: only provider accounts listed in `SIGNUP_ALLOWLIST` can create a user. To let yourself in:

1. Sign in with MAL or AniList. You will see "not open yet".
2. `kamal app logs | grep -i 'sign-up refused'` shows your account as `provider:id` (e.g. `Sign-up refused for mal:1234567`).
3. Add it to `ANISYNC_SIGNUP_ALLOWLIST` (comma-separated) and run `kamal deploy`.
4. Sign in again. Find your AniSync user id (Settings, or `/api/me`).
5. Only now set `ANISYNC_ADMIN_USER_IDS` to that id and deploy. Setting it earlier would make whoever signs in first an admin.

Add testers the same way, always by `provider:id`: usernames can be renamed, so they are never matched.

## 6. Checks after the first deploy

- **No open ports**: on the server, `ss -tlnp` and `docker ps --format '{{.Names}} {{.Ports}}'` show no `0.0.0.0` or `[::]` listener except Tailscale.
- **Real visitor IPs**: run `curl -H 'X-Forwarded-For: 1.2.3.4' https://ANISYNC_DOMAIN/api/providers` and find the request in `kamal app logs`. The access line must show your real public IP, never `1.2.3.4` and never a `172.30.x.x` address.
- **Headers**: `curl -sI https://ANISYNC_DOMAIN/` shows `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`.
- **Health alerting**: `kamal app stop`, confirm the uptime monitor alerts, then `kamal app boot`.
- **Sentry scrubbing**: open `https://ANISYNC_DOMAIN/auth/mal/callback?code=abc&state=xyz` (it fails: the state is fake). Any event or breadcrumb it produces in Sentry must show neither value, and no event ever carries cookies, headers or request bodies.
- **Database roles**: `docker exec -it anisync-db psql -U anisync_app -d anisync -c 'CREATE TABLE x (id int)'` must fail. `docker inspect anisync-web-<version>` and the backup container must show no `anisync_owner` credentials.
- **Rollback**: `kamal rollback <previous version>` must succeed; the hook skips migrations on rollback.

## 7. Everyday operations

- **Deploy**: `kamal deploy`. The new container starts, passes `/api/health`, takes the traffic, then the old one stops. A sync running in the old container gets about 20 seconds, then it is cancelled and recorded as an error. The next scheduled sync picks it up from the stored list copies.
- **Migrations are expand-only.** Add columns and tables. Drop or rename only in a later deploy, after no running version uses them. A rollback runs no migration, so the previous image must work against the newest schema.
- **Logs**: `kamal app logs`, `kamal accessory logs db|cloudflared|backup`.
- **Database shell**: `ssh deploy@ANISYNC_HOST docker exec -it anisync-db psql -U postgres -d anisync`.
- **Key rotation** (token encryption): ROADMAP Phase 2.2.

## 8. Backups and the restore drill

The backup accessory dumps the database every night at 03:30 UTC, encrypts it with the age public key and streams it to R2 as `db/anisync-<timestamp>.dump.age`. On success it pings the heartbeat monitor, and on failure it pings `<url>/fail`. A night without a ping (container down, server down) alerts after the grace period.

**Restore drill** (after the first backup, then at least every three months), on your machine:

```bash
docker build -t anisync:drill .            # the app image at the deployed commit
export APP_IMAGE=anisync:drill AGE_IDENTITY_FILE=/path/to/anisync-backup.key
export TOKEN_ENCRYPTION_KEYS=<production key> R2_ACCOUNT_ID=… R2_ACCESS_KEY_ID=… R2_SECRET_ACCESS_KEY=… R2_BUCKET=…
deploy/backup/restore-drill.sh
unset TOKEN_ENCRYPTION_KEYS AGE_IDENTITY_FILE   # these two together expose every stored token
```

The drill restores into a throwaway Postgres, prints row counts, decrypts one stored token and boots the app against the copy. The app runs with a localhost address, no worker and no provider credentials, so it can never touch a real list. Record each run below.

| Date | Dump | Result |
|---|---|---|
| | | |

## 9. Real-world checklists

Record the results in `docs/ROADMAP.md` (Phase 6).

**Production sign-in.** Sign in with MAL and with AniList through the production apps. Link Kitsu.

**MAL as a mirror** (writes to MAL have never run on a real account):

1. Use a throwaway MAL account, or export the list first (MAL → Export).
2. Choose another service as master and run the preview.
3. Apply. Check one create, one update and one delete on MAL's site.

**Large lists** (2000+ entries on each provider). Record:

- the first full sync's duration and request count;
- Kitsu page shrinking after timeouts;
- AniList and MAL read times at that size;
- any rate-limit waits;
- whether the dashboard progress kept moving.

## 10. Provider terms (launch gate)

Public sign-in stays closed until MAL and AniList answer in writing. What each message must ask is in ROADMAP → Phase 6 → Provider terms.

| Provider | Sent | Answer | Blocks if "no" |
|---|---|---|---|
| MAL (support form) | | | Server-side list copy, writes to MAL, syncing with other sites |
| AniList (contact@anilist.co) | | | Running as a complementary sync tool, the list copy |
| Kitsu (Discord `#api`) | | | Password-grant sign-in |

When both MAL and AniList agree, set `SIGNUP_ALLOWLIST_ENABLED: "false"` in `config/deploy.yml` and deploy.
