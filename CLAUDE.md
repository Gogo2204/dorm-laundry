# CLAUDE.md — Development Instructions

You are implementing **Dorm Laundry**, a minimal Django application for approximately 80 authorized residents sharing three washer/dryer sets.

**Read `REQUIREMENTS.md` before writing or changing code.** Follow it as the source of truth. If a requirement is ambiguous or conflicts with security or physical lockbox limitations, ask before proceeding.

## Principles

1. Build the simplest solution that meets the requirements.
2. Use server-rendered Django templates and Bootstrap; no React, Vue, or separate API unless justified and approved.
3. Use Django authentication, forms, ORM, and Django Admin where practical.
4. Do not introduce FastAPI, Celery, Redis, microservices, or unnecessary dependencies.
5. Do not add features outside the MVP specification.
6. Test all booking rules, authorization paths, and PIN lifecycle transitions.
7. Use transactions, uniqueness constraints, and appropriate locking for concurrency; production uses PostgreSQL.
8. Never commit secrets, production PINs, resident data, or database dumps.
9. Do not claim offline lockbox PINs are revoked when only the app record has changed.
10. Ask before increasing infrastructure complexity or recurring AWS costs.

## Coding conventions

- Use Python type hints where helpful and follow PEP 8.
- Prefer small, explicit functions and thin Django views.
- Put booking business rules in `laundry/services.py`.
- Put PIN allocation and revocation workflows in `access_codes/services.py`.
- Use Django forms for validation and ORM for persistence.
- Configure the custom user model **before initial migrations**.
- Use migrations and avoid ad hoc database changes.
- Implement clear, actionable error messages.
- Prefer built-in Django Admin over custom administration pages.
- Make email verification, activation, and PIN allocation idempotent.
- Avoid revealing plaintext PINs in logs or administrative list views.

## Implementation phases

### Phase 1 — Foundation

- Initialize Django project and applications.
- Configure custom email-login user model.
- Configure SQLite locally and PostgreSQL in production.
- Set up environment-based configuration and `.env.example`.
- Create base Django templates and Bootstrap layout.
- Add `.gitignore`, `README.md`, and GitHub Actions CI.
- Create secure management command to initialize two admin accounts.
- Run Django checks, migrations, and tests.

### Phase 2 — Accounts

- Approved-name management with normalized matching.
- Registration and duplicate-claim prevention.
- Email verification and account activation.
- Login/logout and password reset.
- Account deactivation and admin permission management.
- Tests for authentication, permissions, and name-claim concurrency.

### Phase 3 — Scheduling

- MachineSet and Reservation models and seed data.
- Five slots daily; slot 5 crosses midnight.
- Current/next-week schedule with resident names.
- Booking service enforcing every restriction.
- Cancellation workflow and booking history.
- Transaction/concurrency protection and tests.

### Phase 4 — PINs

- Encrypted PIN inventory and duplicate detection.
- Admin import and status management.
- Atomic PIN allocation after verification.
- PIN email delivery with retry-safe behavior.
- Authenticated user's own masked/reveal PIN view.
- Deactivation marks `REVOKE_PENDING`; no unsafe reassignment.
- Tests for PIN allocation concurrency, email failure, and revocation.

### Phase 5 — Administration

- Django Admin for approved residents, users, permissions, PINs, and bookings.
- Enforce last-active-admin safeguard.
- Add CSV import if needed.
- Test all admin actions and access control.

### Phase 6 — Deployment

- Configure production Django settings and PostgreSQL.
- Deploy to AWS Lightsail with Nginx and Gunicorn.
- Configure Amazon SES and HTTPS.
- Set up service restart, logs, backups, and restore instructions.
- Document deployment and recovery in README.

## Working agreement

For **each phase**:

1. Briefly explain the implementation plan.
2. Implement only that phase.
3. Add/update automated tests.
4. Run tests and Django system checks.
5. Fix failures.
6. Summarize files changed and any unresolved decisions.
7. **Stop and wait for approval** before starting the next phase.

Do not skip tests or silently expand the scope.

## Definition of done

A feature is complete only if it follows `REQUIREMENTS.md`, passes tests, handles failures, enforces permissions, avoids unnecessary dependencies, and is understandable to another Python developer.
