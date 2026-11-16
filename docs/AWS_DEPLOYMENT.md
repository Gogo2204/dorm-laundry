# AWS Deployment Runbook — Dorm Laundry

Single-server, budget-friendly production deployment. Written for a first
Django deployment; every command is copyable. Replace `<PLACEHOLDERS>`.

**Command locations:** `[local]` = your computer, `[server]` = the Lightsail
server over SSH.

## Architecture

```
Residents ──HTTPS──▶ AWS Lightsail Ubuntu server
                        ├── Nginx          (public: 80/443, rate-limits auth endpoints)
                        ├── Gunicorn       (unix socket only, systemd-managed)
                        ├── Django         (dedicated non-root user)
                        └── PostgreSQL 16  (localhost/socket only, never public)
External: Amazon SES (email) · private S3 bucket (encrypted nightly backups)
```

Assumptions: one server hosts everything (explicit budget decision — no
managed database, no load balancer, no Docker, no Redis/Celery); ~80 active
users and ~20 concurrent sessions, far below this stack's capacity; a domain
is strongly recommended (see §Domain).

---

## 1. Prerequisites `[local]`

- An AWS account with billing alerts enabled (Billing → Budgets → create a
  monthly budget, e.g. $15, with an email alert).
- An SSH key pair: `ssh-keygen -t ed25519 -C "dorm-laundry-deploy"` if you
  don't have one.
- A domain, or a plan to register one (§Domain).

## 2. Create the Lightsail instance

In the Lightsail console: **Create instance → Linux/Unix → OS only →
Ubuntu 24.04 LTS**.

- Plan: the **1 GB RAM / 2 vCPU / 40 GB SSD** plan is the recommended
  minimum for Django + PostgreSQL on one box. The 512 MB plan can work but
  leaves little headroom for PostgreSQL.
- SSH key: upload your public key (Account → SSH keys) instead of using the
  default Lightsail key.
- Create a **static IP** (Networking → Create static IP) and attach it —
  free while attached to a running instance.
- **Firewall** (instance → Networking): allow only
  `SSH (22)`, `HTTP (80)`, `HTTPS (443)`. Delete everything else. Port 5432
  must NOT be opened — PostgreSQL stays local.
- Optionally restrict port 22 to your own IP range.

```bash
# [local] First login
ssh -i ~/.ssh/<YOUR_KEY> ubuntu@<STATIC_IP>
```

## 3. Server basics `[server]`

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install nginx postgresql python3.12-venv python3-dev build-essential \
                    libpq-dev git gpg unattended-upgrades awscli
# Automatic security updates:
sudo dpkg-reconfigure -plow unattended-upgrades

# SSH hardening: key-only auth
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart ssh

# Host firewall as a second layer behind the Lightsail firewall:
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
```

Create the dedicated application user (no login shell, no sudo):

```bash
sudo adduser --system --group --home /srv/dorm-laundry --shell /usr/sbin/nologin laundry
```

Throughout this document `<APP_USER>` = `laundry`,
`<APP_DIR>` = `/srv/dorm-laundry/app`.

## 4. Domain and DNS

Register a domain (any registrar; Route 53 also works) and create an **A
record** pointing `<DOMAIN>` at the static IP. DNS must resolve before the
Let's Encrypt step.

**Without a domain** (bare IP): HTTPS with Let's Encrypt is effectively
unavailable (no certificates for bare IPs), browsers will warn, secure
cookies still work but HSTS is meaningless, and SES "verified domain"
convenience is lost. A ~10–15 USD/year domain is strongly recommended; treat
IP-only operation as temporary testing at most, with
`DJANGO_SECURE_SSL_REDIRECT=false` and `DJANGO_HSTS_SECONDS=0`.

## 5. PostgreSQL `[server]`

Ubuntu's PostgreSQL listens only on localhost by default — keep it that way
(`listen_addresses = 'localhost'` in `/etc/postgresql/16/main/postgresql.conf`;
do not change it, and never open port 5432 in any firewall).

Create a least-privilege role and database:

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE dorm_laundry LOGIN PASSWORD '<STRONG_DB_PASSWORD>';
CREATE DATABASE dorm_laundry OWNER dorm_laundry;
REVOKE ALL ON DATABASE dorm_laundry FROM PUBLIC;
SQL
```

The app role owns only its own database — it cannot touch `postgres` or
`template1`, create roles, or install extensions. Health check:

```bash
systemctl status postgresql
sudo -u postgres pg_isready          # expects "accepting connections"
```

## 6. Application code and Python `[server]`

```bash
sudo mkdir -p /srv/dorm-laundry && sudo chown laundry:laundry /srv/dorm-laundry
sudo -u laundry git clone https://github.com/<OWNER>/dorm-laundry.git /srv/dorm-laundry/app
cd /srv/dorm-laundry/app
sudo -u laundry python3.12 -m venv .venv
sudo -u laundry .venv/bin/pip install -r requirements/production.txt
```

## 7. Production environment `[server]`

Secrets live in `/etc/dorm-laundry/env` — outside the git checkout,
root-owned, readable only by root and the app group:

```bash
sudo mkdir -p /etc/dorm-laundry
sudo touch /etc/dorm-laundry/env
sudo chown root:laundry /etc/dorm-laundry/env
sudo chmod 640 /etc/dorm-laundry/env
sudoedit /etc/dorm-laundry/env
```

Contents (generate the two keys fresh — never reuse development values):

```bash
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<RUN: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())">
PIN_ENCRYPTION_KEY=<RUN: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
DJANGO_ALLOWED_HOSTS=<DOMAIN>
DJANGO_CSRF_TRUSTED_ORIGINS=https://<DOMAIN>
DJANGO_TIME_ZONE=Europe/Sofia
# Bootstrap values — flip after HTTPS works (§9):
DJANGO_SECURE_SSL_REDIRECT=false
DJANGO_HSTS_SECONDS=0

DB_ENGINE=postgresql
POSTGRES_DB=dorm_laundry
POSTGRES_USER=dorm_laundry
POSTGRES_PASSWORD=<STRONG_DB_PASSWORD>
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432

# SES SMTP (§11)
EMAIL_HOST=email-smtp.<SES_REGION>.amazonaws.com
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=<SES_SMTP_USERNAME>
EMAIL_HOST_PASSWORD=<SES_SMTP_PASSWORD>
DEFAULT_FROM_EMAIL=laundry@<DOMAIN>

# Admin bootstrap — REMOVE these lines after running init_admins:
ADMIN1_EMAIL=...   ADMIN1_PASSWORD=...   ADMIN1_FIRST_NAME=...   ADMIN1_LAST_NAME=...
ADMIN2_EMAIL=...   ADMIN2_PASSWORD=...   ADMIN2_FIRST_NAME=...   ADMIN2_LAST_NAME=...
```

**Back up `DJANGO_SECRET_KEY`, `PIN_ENCRYPTION_KEY`, and this whole file in a
password manager now.** The PIN key is irreplaceable: without it every stored
lockbox PIN is unreadable, including in backups. Keep it separate from the
database backups themselves (§12).

## 8. Migrations, static files, admins `[server]`

```bash
cd /srv/dorm-laundry/app
sudo -u laundry env $(sudo cat /etc/dorm-laundry/env | xargs) .venv/bin/python manage.py check --deploy
sudo -u laundry env $(sudo cat /etc/dorm-laundry/env | xargs) .venv/bin/python manage.py migrate
sudo -u laundry env $(sudo cat /etc/dorm-laundry/env | xargs) .venv/bin/python manage.py collectstatic --noinput
sudo -u laundry env $(sudo cat /etc/dorm-laundry/env | xargs) .venv/bin/python manage.py init_admins
```

Then delete the `ADMIN*` lines from `/etc/dorm-laundry/env` (idempotent to
re-run; existing admins are skipped). Never copy the development SQLite data
to production — production starts from migrations plus `init_admins`.

## 9. Gunicorn (systemd) and Nginx `[server]`

```bash
sudo cp deploy/gunicorn.service /etc/systemd/system/dorm-laundry.service
sudoedit /etc/systemd/system/dorm-laundry.service   # replace <APP_USER>/<APP_DIR>
sudo systemd-analyze verify /etc/systemd/system/dorm-laundry.service
sudo systemctl daemon-reload
sudo systemctl enable --now dorm-laundry
systemctl status dorm-laundry        # expect "active (running)"

sudo mkdir -p /var/www/letsencrypt
sudo cp deploy/nginx-dorm-laundry.conf /etc/nginx/sites-available/dorm-laundry
sudoedit /etc/nginx/sites-available/dorm-laundry    # replace <DOMAIN>, <APP_DIR>
sudo ln -s /etc/nginx/sites-available/dorm-laundry /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
curl -s http://<DOMAIN>/health/      # expect {"status": "ok"}
```

**HTTPS (after DNS resolves):**

```bash
sudo apt -y install certbot python3-certbot-nginx
sudo certbot --nginx -d <DOMAIN>     # obtains cert, adds 443 block + redirect
sudo certbot renew --dry-run         # verifies automatic renewal works
```

Certbot installs a systemd timer for renewal (`systemctl list-timers | grep
certbot`). Once `https://<DOMAIN>/health/` works, finish hardening:

```bash
sudoedit /etc/dorm-laundry/env   # DJANGO_SECURE_SSL_REDIRECT=true, DJANGO_HSTS_SECONDS=2592000
sudo systemctl restart dorm-laundry
```

## 10. Smoke tests

- `https://<DOMAIN>/health/` → `{"status": "ok"}`; plain HTTP redirects to HTTPS.
- Log in as both administrators; Django Admin loads in Bulgarian.
- Add an approved test name, register with it, verify via the emailed link
  (requires §11), book and cancel a slot, reveal a test PIN.
- `curl -I https://<DOMAIN>` shows `Strict-Transport-Security` once enabled.

## 11. Amazon SES

Setup (console, region of your choice — note it):

1. **Verify an identity**: ideally the domain (SES → Identities → Create →
   Domain; add the shown DKIM CNAME records at your DNS). A single email
   address works for testing but domain verification is required for a
   clean long-term setup.
2. **Sandbox**: new SES accounts can only send TO verified addresses. Request
   production access (SES → Account dashboard → Request production access;
   describe the transactional use: registration confirmations, password
   resets, PIN delivery to ~80 dormitory residents). Until approved, test
   with your own verified address.
3. **SMTP credentials**: SES → SMTP settings → Create SMTP credentials. This
   creates a scoped IAM user; put the generated username/password in
   `/etc/dorm-laundry/env` (§7). The app already speaks SMTP+TLS — no code
   changes and no new dependencies.
4. **Test**: register a test resident; check delivery. All subjects/bodies
   are Bulgarian (UTF-8 — SES handles Cyrillic fine).

Temporary failures: verification/password-reset emails can be re-requested
by the user; PIN emails are protected by the persistent `PINDelivery` outbox
— failed sends stay recorded and administrators retry them from Django Admin
(ПИН кодове → изпращания → „Опитай отново…“). Tokens and PINs are never
logged.

## 12. Backups (mandatory — single server!)

Design: nightly `pg_dump`, gzipped, **GPG-encrypted (AES-256) before leaving
the server**, streamed to a **private S3 bucket** (cheapest durable
off-server option; Block Public Access ON), with a **30-day lifecycle
expiry** so storage can never grow unbounded. The IAM backup user can ONLY
`s3:PutObject` into `s3://<BUCKET>/db/*` — it cannot read, list, or delete
backups, so a compromised server cannot destroy them.

Setup (when authorized — Phase 6B):

1. Create bucket `<BUCKET>` (same region as the server), Block Public
   Access: all four ON, default SSE-S3 encryption, lifecycle rule: expire
   `db/` objects after 30 days.
2. Create IAM user `dorm-laundry-backup` with exactly:
   `{"Effect":"Allow","Action":"s3:PutObject","Resource":"arn:aws:s3:::<BUCKET>/db/*"}`
3. On the server:

```bash
sudo cp deploy/backup-dorm-laundry.sh /usr/local/bin/backup-dorm-laundry
sudo chmod 700 /usr/local/bin/backup-dorm-laundry
sudoedit /etc/dorm-laundry/backup.env           # AWS creds + BACKUP_BUCKET; chmod 600
sudoedit /etc/dorm-laundry/backup.passphrase    # long random passphrase; chmod 600
sudo crontab -e    # add: 30 3 * * * /usr/local/bin/backup-dorm-laundry || logger -t dorm-backup "FAILED"
sudo /usr/local/bin/backup-dorm-laundry         # first run, by hand
```

Store the GPG passphrase in the password manager alongside the PIN key.
Backups alone are useless without the passphrase; the passphrase + PIN key
alone are useless without the backups — keep both backed up, separately from
each other's storage.

**Failure detection:** the script stamps `/var/lib/dorm-laundry-last-backup`
on success and writes `dorm-backup: FAILED` to the journal on failure. Check
weekly (or wire into any free uptime monitor):

```bash
cat /var/lib/dorm-laundry-last-backup            # must be < 48h old
journalctl -t dorm-backup --since "7 days ago"
```

**Restore** (also the monthly test-restore, using a disposable database):

```bash
aws s3 cp s3://<BUCKET>/db/<CHOSEN_BACKUP>.sql.gz.gpg - \
  | gpg --batch --decrypt --passphrase-file /etc/dorm-laundry/backup.passphrase \
  | gunzip > /tmp/restore.sql
sudo -u postgres createdb restore_test
sudo -u postgres psql restore_test < /tmp/restore.sql
sudo -u postgres psql restore_test -c "SELECT count(*) FROM accounts_user;"  # sanity
sudo -u postgres dropdb restore_test && rm /tmp/restore.sql
```

For a real disaster: restore into a fresh `dorm_laundry` database the same
way (after `dropdb`/`createdb` — only with explicit confirmation), set the
original `PIN_ENCRYPTION_KEY` in the environment, and restart the service.
Do the test-restore monthly; a backup that has never been restored is a hope,
not a backup.

## 13. Operations

```bash
# Health
curl -s https://<DOMAIN>/health/                  # {"status": "ok"}
systemctl status dorm-laundry nginx postgresql
sudo -u postgres pg_isready
df -h /                                           # disk space
journalctl -u dorm-laundry -e                     # app logs (journald, log-rotated)
sudo tail -f /var/log/nginx/access.log

# Safe restarts
sudo systemctl reload dorm-laundry                # graceful, no dropped requests
sudo systemctl restart dorm-laundry               # full restart
sudo nginx -t && sudo systemctl reload nginx
```

**Deploying an update after a merge to `main`:**

```bash
cd /srv/dorm-laundry/app
sudo -u laundry git fetch origin
sudo -u laundry git log --oneline HEAD..origin/main     # review what's coming
PREVIOUS=$(git rev-parse HEAD); echo "rollback point: $PREVIOUS"
sudo -u laundry git merge --ff-only origin/main
sudo -u laundry .venv/bin/pip install -r requirements/production.txt
sudo -u laundry env $(sudo cat /etc/dorm-laundry/env | xargs) .venv/bin/python manage.py migrate
sudo -u laundry env $(sudo cat /etc/dorm-laundry/env | xargs) .venv/bin/python manage.py collectstatic --noinput
sudo systemctl reload dorm-laundry
curl -s https://<DOMAIN>/health/
```

**Rollback of a failed deployment:**

```bash
cd /srv/dorm-laundry/app
sudo -u laundry git checkout <PREVIOUS>           # the recorded rollback point
sudo -u laundry .venv/bin/pip install -r requirements/production.txt
sudo systemctl reload dorm-laundry
```

Migrations are additive in this project so far; if a future migration must
be reversed, test `manage.py migrate <app> <previous_migration>` on a copy
first — and remember the nightly backup is the ultimate rollback.

## 14. Security summary

- SSH: keys only, no root login, optional source-IP restriction on port 22.
- Two firewall layers (Lightsail + ufw): only 22/80/443.
- PostgreSQL and Gunicorn are unreachable from the network (localhost/socket).
- Secrets in root-owned files outside git; never in logs or process args.
- Django: HTTPS redirect, HSTS, secure/HttpOnly cookies, CSRF, security
  headers, hashed passwords, encrypted PINs, no secrets in error pages
  (DEBUG=false).
- Rate limiting: Nginx `limit_req` on login, password reset, PIN reveal, and
  admin login — deliberately generous per-IP (30 r/min, burst 15) because
  residents share the dormitory NAT IP; this stops scripted brute force
  without locking out a corridor of humans. Django's slow password hashing
  and the lockbox PIN never appearing in URLs complete the picture. (A
  per-account lockout package such as django-axes can be added later if logs
  show targeted attacks; not included now to avoid a new dependency.)
- Unattended security updates; journald handles log rotation for the app,
  logrotate (preinstalled) for Nginx.

## 15. Estimated monthly cost

| Item | Estimate | Notes |
|---|---|---|
| Lightsail 1 GB instance | ~$5/mo (older gen) – $7/mo (newer plans) | the dominant cost; 512 MB is a few dollars less but tight |
| Static IP | $0 while attached | billed (~$0.005/h) only if left unattached |
| S3 backups | ≪ $1/mo | ~30 compressed dumps of a tiny DB = well under 1 GB; pennies for storage+PUTs |
| SES | ~$0.10 per 1,000 emails | this app sends a handful per day — effectively ~$0 |
| Domain | ~$10–15/year | registrar-dependent; paid yearly |
| **Total** | **≈ $6–8/month + domain** | |

Variations: region pricing differs slightly; Lightsail plans include a
generous data-transfer allowance that this app will never approach; costs
only grow if backups/lifecycle are misconfigured (prevented by the 30-day
expiry) or the instance size is increased. Lightsail sometimes offers the
first months of the smallest bundles free — treat that as a bonus, not a
plan. No other paid AWS services are used.

## 16. What remains before going live (owner decisions)

1. Approve the Lightsail plan (recommendation: 1 GB) and region.
2. Register/choose the domain.
3. REQUIREMENTS §16 decisions: identity-verification risk acceptance and
   lockbox hardware confirmation (≥80 simultaneous PINs, per-PIN deletion).
4. Create the AWS resources (Phase 6B): instance, static IP, S3 bucket, IAM
   backup user, SES identity + production access.
5. Generate real production secrets during setup (never committed).
