#!/usr/bin/env bash
# One encrypted dump of the AniSync database, streamed straight to R2: no plaintext copy ever touches a disk.
#
# Environment:
#   PGHOST PGDATABASE PGUSER PGPASSWORD    the read-only anisync_backup role
#   AGE_RECIPIENT                          age public key; the private key never touches the server
#   R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET
#                                          an R2 token scoped to this one bucket (object read & write)
#   BACKUP_HEARTBEAT_URL                   dead-man's switch (Better Stack / UptimeRobot / healthchecks-style):
#                                          pinged on success, <url>/fail on failure; it alerts when a night is missed
set -euo pipefail

: "${AGE_RECIPIENT:?}" "${R2_ACCOUNT_ID:?}" "${R2_ACCESS_KEY_ID:?}" "${R2_SECRET_ACCESS_KEY:?}" "${R2_BUCKET:?}"

export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true  # the token can't list buckets, only use this one

ping() {
  [ -n "${BACKUP_HEARTBEAT_URL:-}" ] && curl -fsS -m 10 --retry 3 -o /dev/null "$1" || true
}

object="db/anisync-$(date -u +%Y-%m-%dT%H%MZ).dump.age"

if pg_dump --format=custom --no-password \
    | age --encrypt --recipient "$AGE_RECIPIENT" \
    | rclone rcat "r2:${R2_BUCKET}/${object}"; then
  size=$(rclone size --json "r2:${R2_BUCKET}/${object}" | sed -n 's/.*"bytes":\([0-9]*\).*/\1/p')
  if [ -z "$size" ] || [ "$size" -lt 1024 ]; then
    echo "backup: ${object} is suspiciously small (${size:-unknown} bytes)" >&2
    ping "${BACKUP_HEARTBEAT_URL:-}/fail"
    exit 1
  fi
  echo "backup: wrote ${object} (${size} bytes)"
  ping "${BACKUP_HEARTBEAT_URL:-}"
else
  echo "backup: FAILED writing ${object}" >&2
  ping "${BACKUP_HEARTBEAT_URL:-}/fail"
  exit 1
fi
