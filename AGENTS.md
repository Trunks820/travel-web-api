# AGENTS.md

## Project Overview

`travel-web-api` is the private Backend for Frontend (BFF) for the hosted
YunTu travel product. It sits between the React/Vite `travel-web` frontend and
the open-source-oriented `hermes-travel` generation service.

Accepted stack:

- Python 3.12
- FastAPI and Uvicorn
- PostgreSQL
- SQLAlchemy 2 with asyncpg
- Alembic migrations
- httpx for internal service calls
- Pydantic Settings
- pytest

Current repository gate: **Source Integration Complete on Main / Production
Artifact Unchanged / Owner Live UAT Accepted**.

Production runs a verified v0.1.1 artifact with Alembic `0009`. Repository
`main` now contains the recovered, artifact-matched Profile implementation and
has passed the source-integration Gate. No redeployment was required for source
recovery. Follow `docs/v0.1.1-source-integration-gate.md`; any later production
operation still requires explicit authorization and fresh runtime verification.

The user accepted Hermes P4.4-H1 and the BFF P4.0-P4.5 implementation and
internal acceptance checkpoints are complete. v0.2, sibling-repository edits,
production databases, deployment, commit, push, and remote-repository creation
remain separately gated.

## Authority Order

When documents conflict, use this order:

1. `docs/product-scope.md`
2. `docs/release-roadmap.md`
3. `docs/architecture-and-security.md`
4. `docs/api-contract.md`
5. `docs/database-and-quota.md`
6. `docs/implementation-plan.md`
7. `docs/implementation-checklist.md`
8. `README.md`

Unknown product choices must remain marked `[ASK USER]`. Do not silently turn
an unresolved choice into implementation behavior.

## Service Boundary

This repository owns:

- hosted-product authentication integration
- email OTP and invitation-gated registration
- v0.2 Linux.do OAuth and L1 Community Admission after its separate gate
- server-side sessions
- user and administrator authorization
- user-to-trip ownership
- generation quota reservation, consumption, and release
- user trip history
- permanent de-identified trip Content Archive after seven-day visibility or
  Account Closure
- Account Closure that deletes identity and severs all retained trip ownership
- same-origin frontend API
- authenticated proxying to `hermes-travel`
- administrator APIs and audit logs used by `travel-admin`
- server-configured `OWNER` product identity projected from an immutable
  `app_user.id` without adding an `OWNER` database role
- a globally unique mutable Display Name that never replaces immutable User or
  Login Identity identifiers
- immutable signed quota adjustments, short-code Invitation batches,
  operational reports, and permanent archive administration

This repository does not own:

- city data or POI facts
- retrieval, route planning, Writer, Review, or Publish Gate
- crawl and extraction pipelines
- model provider pools
- frontend rendering
- ordinary-User account, quota, history, failure, or PDF user interfaces
- a separate administrator backend service
- arbitrary SQL, table-name, column-name, raw-query, or generic BI endpoints
- payments or subscription billing in v0.1
- payments, subscriptions, Google OIDC, or public community features in v0.2

Never import code directly from the sibling `hermes-travel` repository. The
integration boundary is versioned internal HTTP.

## Security Requirements

- Browser authentication uses an opaque server-side session.
- The browser stores only an `HttpOnly`, `Secure` cookie; never store bearer
  tokens in `localStorage` or `sessionStorage`.
- Store only a hash of the raw session token in PostgreSQL.
- Enforce trip ownership on job status, SSE, result, artifact, and history
  access.
- Quota is enforced transactionally in PostgreSQL. Redis must not be the
  source of truth.
- All Administrator writes require UUID idempotency scoped to
  `(actor_user_id, idempotency_key)` and re-check current authorization before
  replaying a stored success.
- `admin_audit_log` is permanent, append-only, and redacted. Raw Invitation
  codes, full emails, failed Writer drafts, prompts, tokens, artifact bytes,
  SQL, and stack traces must never enter it.
- Full-email reveal, failed-draft inspection, and Artifact download are
  authenticated, audited, and returned with `Cache-Control: no-store`.
- Never pass email, phone number, login-provider subject, or raw session data
  to `hermes-travel`.
- Display Name is not a login credential, authorization or ownership key,
  account-linking signal, or Hermes field.
- Seven-day Trip History expiry is an archive transition, not deletion.
- Account Closure deletes identity/session data but preserves only
  de-identified trip content; never retain a reversible owner mapping.
- Public traffic must not be able to bypass this BFF and call
  `hermes-travel` directly.
- Secrets belong in environment variables or a secret manager and must never
  be committed.
- Database, Redis, and internal service ports must not be publicly reachable.

## Project Layout

The capability-oriented layout includes:

```text
src/
  api/
  auth/
  invitations/
  admin/
  trips/
  quota/
  integrations/
  db/
  config.py
  app.py
alembic/
tests/
  unit/
  integration/
```

Keep modules organized by product capability. Avoid generic service/repository
layers unless they express a real transaction or integration boundary.

## Setup and Development Commands

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn src.app:app --host 127.0.0.1 --port 6670 --reload
```

Do not claim they pass unless they were run in the current evidence slice.

## Testing Requirements

P0 must establish these commands:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Security- and money-like state transitions require integration tests against
PostgreSQL. In-memory substitutes are not sufficient for:

- concurrent quota reservation
- idempotent duplicate submission
- exactly-once quota release
- session revocation
- trip ownership enforcement

v0.1.1 Display Name state transitions additionally require PostgreSQL
integration tests for concurrent normalized-name claims, rename cooldown,
15-day former-name quarantine, Account Closure de-identification, and
disable/restore retention.

Every mutating endpoint must have authorization and negative ownership tests
where an owned object exists. Trip, quota, and Administrator mutations also
require explicit idempotency tests; authentication and Account Closure flows
require operation-appropriate replay/one-time-token tests.

## Database Rules

- Use Alembic for every schema change.
- Do not create tables manually in production.
- The BFF uses a dedicated database and database role, even when it shares the
  same PostgreSQL server with `hermes-travel`.
- Do not create cross-database foreign keys or direct joins to
  `hermes-travel` tables.
- Persist only opaque `hermes_job_id` and `result_record_id` references.
- Quota settlement must be safe under retries, worker restarts, and duplicate
  callbacks/polls.

## Build and Deployment

P0 will define the container and health/readiness contracts. The intended
runtime shape is:

```text
Nginx /api/*
  -> travel-web-api
       -> private hermes-travel:6666
       -> dedicated PostgreSQL database
```

Do not expose `hermes-travel:6666` publicly after the BFF cutover. Do not
deploy or alter Nginx without an explicit deployment authorization and a
rollback plan.

## Cross-Repository Rules

`travel-web` and `hermes-travel` are sibling repositories with user-owned
history and may contain unrelated changes.

- Do not edit either sibling repository from a BFF documentation or
  implementation slice.
- Administrator frontend integration belongs to P4 and user frontend
  integration belongs to P5; each requires its own reviewed diff.
- `hermes-travel` changes must be limited to an explicitly approved internal
  service-auth or network-boundary contract.
- Never copy production data, credentials, or private user records into any
  repository.

## Review and Gate Style

Treat D0, cross-repository frontend integration, and production deployment as
hard user-authorization gates. Within an explicitly authorized BFF
implementation slice, phases are strict serial internal checkpoints: run and
record each checkpoint's evidence, and continue automatically only when it
passes. The Administrator A0 product freeze is accepted. P4.0-P4.5 remain
strict serial checkpoints, and P4.4 must stop if Hermes lacks the required
versioned internal-admin HTTP contract. Report:

1. Conclusion
2. Blocking Issues
3. Non-blocking Improvements
4. Evidence
5. Final Gate

Use exact status language:

- `Documentation Draft / Implementation Not Started`
- `Documentation Accepted / Implementation Pending`
- `Implementation Complete / Acceptance Pending`
- `Acceptance Passed / Deployment Pending`
- `Deployment Accepted`

Do not commit, push, deploy, edit another repository, begin v0.2, or start a
production action without explicit user authorization.
