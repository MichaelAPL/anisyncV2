# Nightly encrypted Postgres dump to Cloudflare R2. Same major version as the database, so pg_dump matches.
# Built on the server once (docs/deploy.md): docker build -t anisync-backup:1 deploy/backup
FROM postgres:17-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends age rclone curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY backup.sh schedule.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/backup.sh /usr/local/bin/schedule.sh

USER postgres
ENTRYPOINT ["/usr/local/bin/schedule.sh"]
