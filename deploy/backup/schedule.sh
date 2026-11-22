#!/usr/bin/env bash
# Keeps the backup container alive and runs backup.sh once a day at BACKUP_AT (HH:MM, UTC).
# A failed run is reported by backup.sh itself; this loop just waits for the next day.
set -uo pipefail

at="${BACKUP_AT:-03:30}"

while true; do
  now=$(date -u +%s)
  next=$(date -u -d "today ${at}" +%s)
  if [ "$next" -le "$now" ]; then
    next=$(date -u -d "tomorrow ${at}" +%s)
  fi
  echo "next backup at $(date -u -d "@${next}" '+%Y-%m-%d %H:%M UTC')"
  sleep $((next - now))
  /usr/local/bin/backup.sh || echo "backup failed (exit $?)"
done
