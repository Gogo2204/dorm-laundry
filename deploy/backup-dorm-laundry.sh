#!/usr/bin/env bash
# Nightly encrypted PostgreSQL backup to a private S3 bucket.
#
# Install on the Lightsail server as /usr/local/bin/backup-dorm-laundry
# (root:root, mode 700) and schedule via root's crontab:
#   30 3 * * * /usr/local/bin/backup-dorm-laundry || logger -t dorm-backup "FAILED"
#
# Requirements on the server (see docs/AWS_DEPLOYMENT.md §Backups):
#   - /etc/dorm-laundry/backup.env   root-only (mode 600), containing:
#       AWS_ACCESS_KEY_ID=...        # IAM user allowed ONLY s3:PutObject
#       AWS_SECRET_ACCESS_KEY=...    #   on s3://<BUCKET>/db/*
#       BACKUP_BUCKET=<BUCKET>
#   - /etc/dorm-laundry/backup.passphrase  root-only (mode 600): the GPG
#       symmetric passphrase. Stored OFF the server too (password manager);
#       without it backups are unreadable — that is the point.
#   - Postgres "peer" auth for the postgres system user (default on Ubuntu),
#     so no database password appears anywhere in this script.
#
# Security properties:
#   - no credentials in process arguments (everything via env file / files)
#   - dump never touches disk unencrypted (streamed through gpg)
#   - bucket must be private with Block Public Access enabled
#   - retention is enforced by an S3 lifecycle rule (30 days), so backups
#     cannot accumulate unbounded cost

set -euo pipefail
umask 077

ENV_FILE=/etc/dorm-laundry/backup.env
PASSPHRASE_FILE=/etc/dorm-laundry/backup.passphrase
DB_NAME=dorm_laundry

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
KEY="db/dorm_laundry_${STAMP}.sql.gz.gpg"

# Dump -> compress -> symmetric-encrypt -> stream to S3. No temp files.
sudo -u postgres pg_dump --no-owner "$DB_NAME" \
  | gzip \
  | gpg --batch --yes --symmetric --cipher-algo AES256 \
        --passphrase-file "$PASSPHRASE_FILE" \
  | aws s3 cp - "s3://${BACKUP_BUCKET}/${KEY}" --only-show-errors

# Record success for monitoring; absence/staleness of this file means the
# backup is failing (checked by the health routine in the runbook).
date -u +%Y-%m-%dT%H:%M:%SZ > /var/lib/dorm-laundry-last-backup
logger -t dorm-backup "OK ${KEY}"
