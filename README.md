# Dorm Laundry

A Django application for a shared dormitory laundry room: approved residents
register, verify their email, automatically receive a unique offline-lockbox
PIN, and book one of three washer/dryer sets in fixed daily slots.

See `REQUIREMENTS.md` for the full specification and `CLAUDE.md` for the
implementation phases.

## Stack

- Python 3.12+, Django 5.2 (server-rendered templates, Bootstrap 5)
- SQLite locally, PostgreSQL in production
- Amazon SES for email, AWS Lightsail + Nginx + Gunicorn for hosting (Phase 6)

## Local setup

```bash
git clone <repository-url>
cd dorm-laundry

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements/dev.txt

cp .env.example .env   # defaults are fine for local development (DEBUG, SQLite)

python manage.py migrate
python manage.py runserver
```

The site is then available at <http://127.0.0.1:8000/> and Django Admin at
<http://127.0.0.1:8000/admin/>.

In local development (`DJANGO_DEBUG=true`) emails are printed to the console
and a built-in development secret key is used; in production
`DJANGO_SECRET_KEY` is required and SMTP (Amazon SES) is used.

## Creating the two initial administrators

Credentials are never hardcoded. Either run interactively:

```bash
python manage.py init_admins
```

and answer the prompts (passwords are read without echo), or supply
`ADMIN1_EMAIL`, `ADMIN1_PASSWORD`, `ADMIN1_FIRST_NAME`, `ADMIN1_LAST_NAME`
and the matching `ADMIN2_*` variables in the environment (see
`.env.example`) and run the same command non-interactively. The command is
idempotent: re-running it skips administrators that already exist.

## Trying the registration flow locally

1. Log into Django Admin and add an approved resident (or use
   *Approved residents → Import from CSV*).
2. Open <http://127.0.0.1:8000/accounts/register/> and register with that
   exact first/last name (case and extra spaces don't matter).
3. The verification email is printed to the `runserver` console; open the
   link it contains to activate the account.
4. Log in at <http://127.0.0.1:8000/accounts/login/> with the email and
   password. Password reset works the same way — the email appears in the
   console.

## Lockbox PINs

### Encryption key

PINs are stored encrypted (Fernet authenticated encryption) and deduplicated
via keyed HMAC fingerprints. Both require `PIN_ENCRYPTION_KEY` in the
environment:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the output in `.env` (or the production environment). The key is
independent of `DJANGO_SECRET_KEY`, must never be committed, and must be
backed up securely — **losing it makes every stored PIN unreadable**.
Rotating it requires re-importing the PIN inventory. PIN features fail with a
clear error if the key is missing or wrong; nothing is ever stored in
plaintext. The accepted PIN format defaults to 4–8 digits
(`PIN_FORMAT_REGEX`); adjust it to match the lockbox hardware.

### Inventory setup and physical programming

1. In Django Admin → *Lockbox PINs*, add PINs individually (the input is not
   echoed back) or use *Import from CSV* — a UTF-8 file with **one PIN per
   line**, optional `pin` header row. Duplicates are skipped; invalid rows
   are reported by line number only.
2. New PINs start as **UNPROGRAMMED**. Program each code into the physical
   lockbox and test it on the keypad.
3. Select the programmed PINs and run **"Confirm programmed into lockbox"**.
   After an explicit confirmation page they become **AVAILABLE** — only then
   can they be assigned to residents.

### Assignment and email delivery

When a resident verifies their email, one AVAILABLE PIN is assigned
atomically (exactly one PIN per resident, one resident per PIN) and emailed
to them via a persistent delivery record. If the inventory is empty,
verification still succeeds and the resident sees a waiting message; the
Users list in Admin shows an **"Awaiting PIN"** filter and an **"Allocate
lockbox PINs"** action for assigning codes later (also used after
reactivation). Failed emails are kept as FAILED delivery records — retry via
the **"Retry failed/pending deliveries"** action on *PIN deliveries*.
Residents can also reveal their own PIN on *My Account* (masked by default).

### Deactivation and manual revocation (IMPORTANT)

The lockbox is **offline**: this application can never disable a code on the
device. Deactivating a resident only marks their PIN **REVOKE_PENDING**.
The mandatory manual procedure is:

1. Deactivate the resident (Users → "Deactivate selected users").
2. Open *Lockbox PINs*, filter by status REVOKE_PENDING.
3. Physically delete each listed code from the lockbox keypad and verify it
   no longer opens the box.
4. Select the PINs and run **"Confirm physical removal"** — they become
   REVOKED (terminal; revoked PINs are never reused automatically).

Until step 3 is done, **the code still opens the lockbox**, whatever the
application says. A reactivated resident never gets their old PIN back; use
"Allocate lockbox PINs" to give them a fresh one.

All sensitive PIN operations (import, programming, assignment, delivery
attempts, retries, revocation) are recorded in the *PIN audit entries* log,
which never contains PIN values.

### Known hardware limitations

Before launch, confirm the chosen lockbox supports at least 80
simultaneously active distinct PINs, individual code deletion, and has
acceptable battery/emergency behaviour (REQUIREMENTS §16.3). The 4–8 digit
format default is an assumption to verify against the actual device.

## Checks and tests

```bash
python manage.py check
python manage.py test
ruff check .
```

GitHub Actions (`.github/workflows/ci.yml`) runs linting, Django system
checks, a migration consistency check, and the test suite on every push and
pull request.

## Project layout

```text
config/        Django project (settings, urls, wsgi)
accounts/      custom email-login user model, registration (Phase 2)
laundry/       machine sets, reservations, schedule (Phase 3)
access_codes/  encrypted lockbox PIN inventory and allocation (Phase 4)
templates/     server-rendered templates (Bootstrap 5)
static/        static assets
requirements/  base / dev / production dependency sets
```

## Security notes

- Never commit `.env`, database files, credentials, encryption keys, or PINs.
- Lockbox PINs are offline: deactivating a user only marks their PIN
  `REVOKE_PENDING` in the app — an administrator must physically remove the
  code from the lockbox. The manual revocation procedure will be documented
  before launch (Phase 6).
