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

Current phase: **v0.1 Documentation Accepted / Implementation Pending**.

The user explicitly resumed **v0.1 BFF implementation** after accepting the
`travel-web` D0 contract. P0-P4 may proceed through their serial internal
checkpoints. v0.2, sibling-repository edits, production databases, deployment,
commit, push, and remote-repository creation remain separately gated.

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

This repository does not own:

- city data or POI facts
- retrieval, route planning, Writer, Review, or Publish Gate
- crawl and extraction pipelines
- model provider pools
- frontend rendering
- ordinary-User account, quota, history, failure, or PDF user interfaces
- a separate administrator backend service
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
- Never pass email, phone number, login-provider subject, or raw session data
  to `hermes-travel`.
- Seven-day Trip History expiry is an archive transition, not deletion.
- Account Closure deletes identity/session data but preserves only
  de-identified trip content; never retain a reversible owner mapping.
- Public traffic must not be able to bypass this BFF and call
  `hermes-travel` directly.
- Secrets belong in environment variables or a secret manager and must never
  be committed.
- Database, Redis, and internal service ports must not be publicly reachable.

## Planned Project Layout

P0 may create the following structure after D0 acceptance:

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

## Planned Setup and Development Commands

These commands are planned contracts for P0 and do not exist yet:

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn src.app:app --host 127.0.0.1 --port 6670 --reload
```

Do not claim they pass until P0 creates and verifies the required files.

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
implementation slice, P0-P4 are strict serial internal checkpoints: run and
record each checkpoint's evidence, and continue automatically only when it
passes. P4 also requires the Administrator A0 product freeze. Report:

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
