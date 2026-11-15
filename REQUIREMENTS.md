# Dorm Laundry — Software Requirements Specification

**Version:** 1.0  
**Status:** MVP specification; lockbox hardware compatibility must be verified  
**Stack:** Django, server-rendered templates, PostgreSQL in production, AWS, GitHub

## 1. Overview

Dorm Laundry manages access-code assignment and bookings for three washer/dryer sets shared by approximately 80 authorized residents in a 250-person dormitory.

### Goals
- Allow only residents on an administrator-managed approved-name list to register.
- Verify email addresses and automatically assign one unique, preconfigured offline-lockbox PIN per eligible user.
- Provide a mobile-friendly weekly reservation schedule.
- Enforce reservation rules and enable self-service cancellations.
- Allow equal-permission administrators to manage approved residents, users, PINs, and reservations.
- Keep manual work minimal, with no payments, social integration, or machine telemetry.

### Out of scope
Payment processing, Facebook/Messenger integration, mobile apps, lockbox API integration, machine monitoring, reminders, waitlists, analytics dashboards, and recurring bookings.

## 2. Stack and architecture

- Python 3.12+ and a supported Django release.
- Django templates, HTML, CSS, Bootstrap 5; no SPA or REST API unless later required.
- Django authentication, forms, ORM, and Admin.
- SQLite locally; PostgreSQL in production.
- Amazon SES for verification, PIN delivery, and password-reset emails.
- AWS Lightsail (or similarly small AWS VM), Nginx, Gunicorn, HTTPS.
- GitHub Actions for automated checks and tests.
- Configured local timezone, initially `Europe/Sofia` if that is the dormitory's timezone.

## 3. Roles

### Resident
Can register, verify email, log in/out, reset password, see the weekly schedule and booked residents' first/last names, create/cancel their own reservations, and view their own assigned PIN. Cannot access admin functions, others' PINs, or others' booking modifications.

### Administrator
All admins have equal permissions. They can manage approved names, user activation, administrator privileges, PIN inventory/status, and reservations. Create two initial administrator accounts securely using a management command and credentials supplied at deployment, not hardcoded. Prevent deactivation/demotion/deletion of the last active administrator. Use Django staff/superuser permissions as appropriate; protect these safeguards in every administrative path.

## 4. Registration and authentication

### UC-01 — Add eligible residents
Admin adds first and last names individually or by CSV. Normalize whitespace and case for matching. Reject duplicate normalized name pairs. Store eligibility and whether the approved entry has been claimed.

### UC-02 — Register
1. Applicant supplies first name, last name, email, password, and password confirmation.
2. Server validates inputs, password strength, and unique email.
3. Name must match one eligible, unclaimed approved entry.
4. Create a pending, inactive account associated with that entry; send a time-limited email verification link.
5. On verification, activate the account and allocate one available preconfigured PIN atomically; send the PIN to the verified email.
6. If no PIN is available, retain a pending-PIN state and surface it to admins; do not pretend physical access exists.
7. Retry failed email delivery without allocating a second PIN. Verification and allocation must be idempotent.

**Security limitation:** A name match plus email verification does not establish that the applicant is the named resident. Anyone who knows an eligible name could claim it with their own email. This risk must be explicitly accepted or mitigated (e.g., invitation tokens or manual identity check) before launch. Identical-name residents cannot register until admins resolve the ambiguity.

### UC-03 — Login and logout
Use email and password; allow only active, verified accounts with appropriate status. Apply rate limiting.

### UC-04 — Password reset
Use Django's expiring reset-token flow. Resetting a password must not change PIN allocation or reservations.

## 5. Offline lockbox PINs

### UC-05 — Import PINs
Admin physically programs distinct PINs into the chosen offline lockbox, then imports the corresponding PIN pool into Django. Validate format and duplicates. Store PINs encrypted at application level and use keyed fingerprints for duplicate detection. Do not log or commit PINs.

### UC-06 — Allocate PIN
On successful verification, atomically select an `AVAILABLE` PIN, assign it to exactly one user, mark `ASSIGNED`, and queue/send email. Repeated requests must not allocate additional PINs. A PIN belongs to at most one user; a user has at most one active assigned PIN.

### UC-07 — Show PIN
Authenticated user can reveal only their own PIN on their account page; mask by default. Admins can manage PIN status through protected Django Admin.

### UC-08 — Deactivate user
Prevent future logins and bookings, cancel future reservations, and mark their PIN `REVOKE_PENDING`. **An offline lockbox does not automatically revoke a code.** Never reassign a code until an admin has physically removed/changed it and confirmed its state. Prefer retiring old codes rather than reusing them. A written manual revocation procedure is mandatory.

**Hardware prerequisite:** Confirm the selected lockbox supports at least 80 *simultaneously active, distinct* PINs and individually deleting/changing them. Check its battery/power behavior, emergency access, and installation requirements. The web app cannot enforce entry or revoke PINs on the device.

## 6. Reservation rules

Three independently bookable machine sets, each consisting of one washer and one dryer: Set 1, Set 2, Set 3.

Five fixed slots per booking date:

| Slot | Local start | Local end |
|---|---|---|
| 1 | 06:00 | 10:00 |
| 2 | 10:00 | 14:00 |
| 3 | 14:00 | 18:00 |
| 4 | 18:00 | 22:00 |
| 5 | 22:00 | 02:00 next day |

- Booking date means the local date the slot **starts**; slot 5 ends the next day.
- Weeks run Monday–Sunday, with counts based on booking start date.
- At most **two active reservations per resident per week**.
- One resident cannot reserve multiple sets for the same date and slot.
- One machine set cannot have multiple active reservations for the same date and slot.
- **Consecutive slot numbers on the same booking date** are forbidden for the same resident across all sets (e.g., slots 1+2). Nonadjacent slots (e.g., 1+3) are allowed if weekly capacity permits.
- Different booking dates do not trigger the consecutive-slot rule, even across midnight.
- Residents may book only the **current and following calendar week**.
- A slot cannot be booked once its actual start time has passed.
- Residents may cancel their own reservations **at any time**, including after a slot starts or ends.
- Cancelled reservations remain in history. **Open policy decision:** whether cancellation after a slot starts restores the weekly quota; recommended: no, to prevent users from exceeding the practical twice-per-week limit.
- Admins can cancel any booking; admins should not bypass booking validation when creating bookings.
- Timezone-aware datetime calculations must handle daylight-saving transitions and midnight correctly.

### UC-09 — View schedule
Authenticated users can switch between current and next week. Display seven days, five slots/day, and three sets, with available/occupied status. Occupied cells display the booking resident's first and last name, not email or PIN. Mobile view can show one day at a time.

### UC-10 — Book
Resident selects a free machine/date/slot. Server validates authorization, date range, start time, weekly quota, same-user collision, same-day adjacency, and machine availability. Persist atomically; display confirmation or clear error.

### UC-11 — Cancel own booking
Resident can cancel their own reservation after confirmation, at any time. Mark cancelled rather than delete. Do not allow cancellation of another user's reservation.

### UC-12 — Admin booking management
Admin can view and cancel all reservations and see basic change history.

## 7. Functional requirements

| ID | Requirement |
|---|---|
| FR-01 | Admin CRUD for approved resident names; optional CSV import |
| FR-02 | Registration restricted to unclaimed eligible normalized names |
| FR-03 | Reject duplicate name claims and duplicate email addresses |
| FR-04 | Require name, email, password, and password confirmation |
| FR-05 | Verify email before account activation and PIN delivery |
| FR-06 | Login, logout, password reset |
| FR-07 | Admin user activation/deactivation |
| FR-08 | Equal-permission admin grant/revoke; preserve last active admin |
| FR-09 | Initialize two admins without hardcoded credentials |
| FR-10 | Import, encrypt, and manage preconfigured lockbox PINs |
| FR-11 | Atomic unique PIN allocation and idempotent email delivery |
| FR-12 | Authenticated resident can reveal only their own PIN |
| FR-13 | Revocation-pending PINs are never automatically reused |
| FR-14 | Show three machine sets and five fixed daily slots |
| FR-15 | Enforce max two weekly bookings per resident |
| FR-16 | Reject same-day consecutive slots |
| FR-17 | Reject same-user simultaneous bookings |
| FR-18 | Reject double booking of a machine slot |
| FR-19 | Restrict bookings to current and next week and future slot starts |
| FR-20 | Residents can cancel own bookings anytime |
| FR-21 | Admins can cancel any booking |
| FR-22 | Display booked residents' first and last names |
| FR-23 | Responsive desktop/mobile interface |
| FR-24 | Use database constraints/transactions to prevent concurrency conflicts |

## 8. Nonfunctional requirements

- **Simplicity:** Django monolith; no microservices, Celery, Redis, SPA, or unneeded integrations.
- **Performance targets:** typical page load <2 seconds, reservation operation <3 seconds, at least 100 accounts and approximately 20 concurrent sessions; verify with tests.
- **Availability target:** 99% monthly excluding planned maintenance.
- **Security:** HTTPS; CSRF; secure, HttpOnly cookies; Django password hashing; server-side validation; rate limiting; least privilege; expiring email tokens; encryption of PINs; no secrets in Git or logs.
- **Privacy:** retain minimum personal information; show names only on authenticated schedule; no email/PIN disclosure; support appropriate deletion/anonymization policies.
- **Reliability:** daily automated database backup and tested restore; safe concurrent bookings; no duplicate PIN allocation or delivery-induced reassignment.
- **Maintainability:** migrations, automated tests, documented setup/deployment, thin views, business rules in services.
- **Usability:** mobile-friendly schedule, clear errors, confirmation before cancellation, accessible labels and controls.
- **Timezone:** configure local IANA timezone and use timezone-aware timestamps.

## 9. Data model

### ApprovedResident
`id`, `first_name`, `last_name`, `normalized_first_name`, `normalized_last_name`, `registered_user` (nullable one-to-one), `is_eligible`, `created_at`. Unique normalized name pair for MVP; duplicates require manual resolution.

### User
Custom Django user model derived from `AbstractUser`, configured **before the first migration**, using unique email for login. Fields: `id`, `first_name`, `last_name`, `email`, `password` (hash), `is_active`, `is_staff`, `is_superuser`, `email_verified`, `created_at` (or `date_joined`). Consider separate pending-PIN status where necessary.

### LockboxPIN
`id`, `encrypted_pin`, `pin_fingerprint` (unique keyed hash), `assigned_user` (nullable unique relation), `status` (`AVAILABLE`, `ASSIGNED`, `REVOKE_PENDING`, `RETIRED`), `assigned_at`, `created_at`. No PIN plaintext in admin list pages, logs, or exports.

### MachineSet
`id`, `name`, `is_active`. Seed exactly three active machine sets.

### Reservation
`id`, `user` FK, `machine_set` FK, `booking_date`, `slot_number` (1–5), `status` (`ACTIVE`, `CANCELLED`), `created_at`, `cancelled_at`, `cancelled_by` nullable FK. Add a conditional unique constraint on `(machine_set, booking_date, slot_number)` for `ACTIVE` rows. Add a conditional unique constraint on `(user, booking_date, slot_number)` for `ACTIVE` rows. Enforce weekly and adjacency restrictions inside transaction-safe service logic.

### Email verification
Use signed expiring single-use-in-effect tokens or a small verification model. Keep activation and PIN allocation idempotent.

## 10. Suggested repository layout

```text
dorm-laundry/
├── .github/workflows/ci.yml
├── config/{settings.py,urls.py,wsgi.py}
├── accounts/{models.py,forms.py,views.py,services.py,tests/}
├── laundry/{models.py,forms.py,views.py,services.py,tests/}
├── access_codes/{models.py,services.py,tests/}
├── templates/
├── static/
├── requirements/
├── .env.example
├── .gitignore
├── manage.py
├── README.md
├── REQUIREMENTS.md
└── CLAUDE.md
```

## 11. UI pages

Public: Login, Register, Email verification result, Forgot password, Reset password.

Resident: Weekly Schedule, My Reservations, My Account / Show PIN.

Administrator: Prefer built-in Django Admin for approved residents, users, admin privileges, PIN inventory/status, and reservation management. Do not build a separate dashboard for MVP.

## 12. AWS deployment

```text
Browser --HTTPS--> AWS Lightsail VM
                      ├── Nginx
                      ├── Gunicorn / Django
                      └── PostgreSQL
Django --SMTP--> Amazon SES
```

A small instance with PostgreSQL on the VM is acceptable for MVP if backed up; managed PostgreSQL is an optional higher-cost alternative. Production requires HTTPS, `DEBUG=False`, configured hosts, secure secret storage, migrations, collected static assets, restart-on-boot, error logs, daily backups, and a restore procedure. Confirm SES sender verification and sending limits before launch.

## 13. GitHub / CI

Private repository initially. `main` for stable code; `feature/*` branches for work. GitHub Actions installs dependencies, runs Django system checks, migration checks, linting, and tests. Never commit `.env`, production DB dumps, credentials, encryption keys, or real PINs. Manual deployment until production credentials/process are verified.

## 14. Acceptance criteria

| ID | Expected behavior |
|---|---|
| AC-01 | Eligible unclaimed name can register |
| AC-02 | Unapproved name is rejected |
| AC-03 | Approved entry cannot be claimed twice |
| AC-04 | Duplicate email is rejected |
| AC-05 | Unverified account receives no PIN |
| AC-06 | Verified eligible account receives exactly one unique PIN |
| AC-07 | Two initial admins can log in |
| AC-08 | Admin can grant admin permissions |
| AC-09 | Admin can revoke another admin's permissions |
| AC-10 | Last active admin cannot be removed |
| AC-11 | Resident cannot access admin functions |
| AC-12 | One PIN cannot be assigned to two users |
| AC-13 | Unavailable PIN cannot be allocated |
| AC-14 | Email retry does not allocate another PIN |
| AC-15 | Deactivation marks PIN for physical revocation |
| AC-16 | Revocation-pending PIN is not reassigned |
| AC-17 | Resident can book a free slot |
| AC-18 | Same machine slot cannot be booked twice |
| AC-19 | Resident cannot book two machines in same slot |
| AC-20 | Third weekly booking is rejected |
| AC-21 | Same-day adjacent slot is rejected |
| AC-22 | Same-day nonadjacent slots can be booked within quota |
| AC-23 | Adjacent slot numbers on different dates are allowed |
| AC-24 | Slot that has started cannot be booked |
| AC-25 | Beyond-next-week booking is rejected |
| AC-26 | Resident can cancel own booking anytime |
| AC-27 | Resident cannot cancel someone else's booking |
| AC-28 | Cancelled bookings follow the finalized quota-restoration policy |
| AC-29 | Slot 5 ends at 02:00 the following date |
| AC-30 | Concurrent attempts cannot both book same machine slot |
| AC-31 | Schedule is usable on mobile and desktop |
| AC-32 | Occupied slots display resident's name |
| AC-33 | Schedule never shows emails or PINs |
| AC-34 | Invalid operations produce clear errors |

## 15. Definition of done

- Both initial admins can administer approved names, users, and PINs.
- Eligible residents can register, verify email, and receive unique preconfigured PINs.
- Booking and cancellation work with all specified rules.
- Admin deactivation triggers PIN revocation-pending state.
- Tests, CI, and security checks pass.
- App is deployed to AWS with HTTPS, configured SES, and backed-up database.
- README documents local setup, deployment, recovery, and manual lockbox revocation.

## 16. Decisions required before launch

1. **Identity risk:** Accept name-only matching or add an invitation/identity verification step.
2. **Late cancellations:** Decide whether cancelling after a slot begins restores weekly quota (recommend no).
3. **Hardware:** Verify at least 80 simultaneous PINs, individual PIN deletion, emergency operation, and safe installation.

Do not silently assume the software can revoke offline lockbox PINs.
