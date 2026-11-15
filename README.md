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
