#!/usr/bin/env bash
# Restore drill: proves the latest backup can be restored and read. Run it on YOUR machine, never on the server.
#
# It downloads the newest dump from R2, decrypts it, restores it into a throwaway Postgres, prints row counts, checks
# that a stored provider token decrypts with the escrowed key, then boots the app image against the copy and checks
# /api/health. The app runs with a localhost PUBLIC_URL (development settings), no worker, no scheduler and blank
# provider credentials, so the copy can never sync a real list or refresh a real token.
#
# Load the two keys from your password manager for the drill only, and unset them afterwards: together they expose
# every stored token.
#   AGE_IDENTITY_FILE        path to the age private key file
#   TOKEN_ENCRYPTION_KEYS    the production token key(s)
#   R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET   (a read token is enough)
#   APP_IMAGE                the app image to boot, e.g. anisync:<version> built locally with `docker build .`
# Optional: OBJECT=db/anisync-...dump.age to restore a specific dump instead of the newest.
set -euo pipefail

: "${AGE_IDENTITY_FILE:?}" "${TOKEN_ENCRYPTION_KEYS:?}" "${APP_IMAGE:?}"
: "${R2_ACCOUNT_ID:?}" "${R2_ACCESS_KEY_ID:?}" "${R2_SECRET_ACCESS_KEY:?}" "${R2_BUCKET:?}"

here="$(cd "$(dirname "$0")" && pwd)"
init_dir="$(cd "$here/../postgres-init" && pwd)"
work="$(mktemp -d)"
net="anisync-drill-$$"
db="anisync-drill-db-$$"
app="anisync-drill-app-$$"
pw="drill-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"

cleanup() {
  docker rm -f "$app" "$db" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

export RCLONE_CONFIG_R2_TYPE=s3 RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true

object="${OBJECT:-db/$(rclone lsf "r2:${R2_BUCKET}/db/" | sort | tail -n 1)}"
echo "drill: restoring ${object}"
rclone copyto "r2:${R2_BUCKET}/${object}" "$work/dump.age"
age --decrypt --identity "$AGE_IDENTITY_FILE" --output "$work/dump" "$work/dump.age"
rm "$work/dump.age"

echo "drill: starting a throwaway Postgres (same init scripts as production, throwaway passwords)"
docker network create "$net" >/dev/null
docker run -d --name "$db" --network "$net" \
  -e POSTGRES_DB=anisync -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD="$pw" \
  -e ANISYNC_OWNER_PASSWORD="$pw" -e ANISYNC_APP_PASSWORD="$pw" -e ANISYNC_BACKUP_PASSWORD="$pw" \
  -v "$init_dir/01-roles.sh:/docker-entrypoint-initdb.d/01-roles.sh:ro" \
  -v "$init_dir/02-grants.sql:/docker-entrypoint-initdb.d/02-grants.sql:ro" \
  -v "$work/dump:/tmp/dump:ro" \
  postgres:17-bookworm >/dev/null
for _ in $(seq 1 60); do
  docker exec "$db" pg_isready -U postgres -d anisync >/dev/null 2>&1 && break
  sleep 1
done
sleep 2  # the image restarts the server once after running init scripts

sql() { docker exec -i "$db" psql -v ON_ERROR_STOP=1 -U postgres -d anisync "$@"; }

echo "drill: pg_restore"
docker exec "$db" pg_restore --no-owner --role=anisync_owner -U postgres -d anisync /tmp/dump
sql < "$init_dir/02-grants.sql" >/dev/null

echo "drill: row counts"
sql -c "SELECT 'users' AS table, count(*) FROM users
        UNION ALL SELECT 'linked_accounts', count(*) FROM linked_accounts
        UNION ALL SELECT 'list_snapshots', count(*) FROM list_snapshots
        UNION ALL SELECT 'sync_runs', count(*) FROM sync_runs
        UNION ALL SELECT 'media', count(*) FROM media
        UNION ALL SELECT 'procrastinate_jobs', count(*) FROM procrastinate_jobs"
sql -c "SELECT version_num AS alembic_version FROM alembic_version"

app_url="postgresql://anisync_app:${pw}@${db}:5432/anisync"
common=(--network "$net" -e DATABASE_URL="$app_url" -e TOKEN_ENCRYPTION_KEYS="$TOKEN_ENCRYPTION_KEYS"
        -e PUBLIC_URL=http://localhost:8000 -e RUN_WORKER_IN_PROCESS=false)

echo "drill: checking that a stored token decrypts with the escrowed key"
# -i: without it docker never hands the script below to python, which then "succeeds" having done nothing.
decrypt_check=$(docker run --rm -i "${common[@]}" "$APP_IMAGE" python - <<'PY'
import asyncio

from sqlalchemy import select

from anisync.db import SessionLocal
from anisync.models import LinkedAccount


async def main() -> None:
    async with SessionLocal() as session:
        account = await session.scalar(select(LinkedAccount).limit(1))
        if account is None:
            print("no linked accounts in this dump: nothing to decrypt")
            return
        token = account.access_token  # raises if the key is wrong
        print(f"token of linked account {account.id} decrypts ({len(token)} characters)")


asyncio.run(main())
PY
)
echo "$decrypt_check"
case "$decrypt_check" in
  *decrypts*|*"no linked accounts"*) ;;
  *) echo "drill: the token check produced no verdict" >&2; exit 1 ;;
esac

echo "drill: booting the app against the copy (no worker, development settings)"
docker run -d --name "$app" "${common[@]}" "$APP_IMAGE" >/dev/null
for _ in $(seq 1 30); do
  if docker exec "$app" python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)" \
      >/dev/null 2>&1; then
    echo "drill: /api/health is 200 on the restored copy"
    echo "drill: PASSED for ${object}. Record the date and object in docs/deploy.md."
    exit 0
  fi
  sleep 2
done
echo "drill: the app never became healthy on the restored copy" >&2
docker logs "$app" | tail -n 40 >&2
exit 1
