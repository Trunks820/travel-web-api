# v0.1 P0-P3 Local Acceptance Evidence

Date: 2026-07-28

Status: **P0-P3 Acceptance Passed / P4 Not Started**

This record covers only the authorized local `travel-web-api` implementation.
It is not production, frontend, P4, deployment, or release acceptance.

## Environment

- Python: 3.12.11
- PostgreSQL: 17.10
- isolated database: `travel_web_test`
- listener: `127.0.0.1:55432`
- migration head: `0005`
- Docker: unavailable on this workstation; the non-root Dockerfile and build
  context rules were inspected but an image build was not claimed

The local database helper refuses non-loopback hosts and database names that do
not start with `travel_web_test`. No production database was used.

## P0

Commands and results:

```text
uv sync --locked
  resolved 67 packages; audited 66 packages

uv run ruff check .
  All checks passed

uv run ruff format --check .
  72 files already formatted

uv run pytest tests/unit -q
  17 passed

uv run alembic downgrade base
uv run alembic upgrade head
uv run alembic check
  0001 -> 0002 -> 0003 -> 0004 -> 0005
  No new upgrade operations detected
```

Real Uvicorn HTTP checks on `127.0.0.1:6670`:

```text
GET  /health                       -> 200
GET  /ready (Hermes unavailable)  -> 503 NOT_READY
GET  /api/me                      -> 401 AUTH_REQUIRED
POST /api/auth/logout no Origin   -> 403 ORIGIN_REJECTED
POST with text/plain              -> 415 JSON_REQUIRED
POST with Content-Length 70000    -> 413 REQUEST_TOO_LARGE
```

Read-only current-upstream inspection found that `hermes-travel` already
provides stable request-id idempotency, but its current `/trip/async` handler
does not validate the BFF internal credential. The BFF sends
`X-Internal-Credential`; no sibling repository was edited. A separately
authorized minimal upstream service-auth slice remains required before P6.

## P1

Command:

```text
uv run pytest tests/integration/test_p1_auth.py -q
  10 passed
```

Evidence covers:

- atomic verified registration: one User, identity, Invitation redemption,
  three-credit grant, and Session
- hashed OTP, Invitation, and Session values only
- non-enumerating send-code response and post-proof mode correction
- concurrent single-use Invitation redemption
- concurrent same-email registration serialized into one success and one
  post-proof `LOGIN_REQUIRED` correction
- expiry, attempt limit, replay, and purpose isolation
- returning login and fixed seven-day secure host-only cookie
- forged, expired, revoked, and disabled-User Session rejection
- idempotent logout, Origin rejection, delivery failure normalization, and OTP
  delivery rate limits
- capability-level session revocation for disablement or role changes

## P2

Command:

```text
uv run pytest tests/integration/test_p2_trip_quota.py -q
  14 passed
```

PostgreSQL concurrency and settlement evidence covers:

- concurrent last-unit reservation: one Trip Attempt and one reservation
- two different concurrent requests: one accepted and one
  `ACTIVE_TRIP_EXISTS`
- identical duplicate request: one Trip Attempt, one quota lifecycle, and one
  idempotent upstream job
- conflicting duplicate request: `REQUEST_ID_CONFLICT` with no extra state
- repeated SUCCESS settlement: one consumption
- repeated FAILED, TIMEOUT, and REJECTED settlement: one release
- SUCCESS/failure race: one legal terminal Trip and one legal quota transition
- quota exhaustion: no Trip, reservation, or upstream call
- pre-acceptance failure: terminal failure and exactly-once release
- uncertain acceptance: remains `SUBMITTING/RESERVED`, then reconciliation
  recovers the same upstream idempotency key

HTTP and security evidence covers:

- browser `source`, `conversation_id`, identity, and provider payload fields are
  removed; the BFF creates trusted opaque upstream fields
- refresh-safe `/api/me.active_trip` and live quota projection
- owner-only job polling, SSE, result, artifact, and download
- unknown and cross-User object access uniformly returns `404 TRIP_NOT_FOUND`
  before an upstream call
- upstream fields, raw errors, internal URLs, storage paths, credentials, and
  Content-Disposition values are not projected publicly
- nested result objects are projected through explicit allowlist models, and
  non-identifying stage, latency, quality, and schema telemetry is persisted
- authenticated place list/detail proxying

## P3

Command:

```text
uv run pytest tests/integration/test_p3_history_closure.py -q
  5 passed
```

Evidence covers:

- stable signed cursor pagination while newer rows are inserted
- current-User-only, terminal, seven-day history
- bounded retry input without request IDs, identity, source, conversation,
  prompts, or provider payloads
- stable safe failure projection
- expired rows absent from history, retained as archived content, and stripped
  of free-text notes
- no public archive, Administrator, or per-Trip deletion endpoint
- fresh purpose-bound closure OTP
- active Trip conflict changes no User, Session, Trip, quota, or challenge state
- terminal closure deletes identity, Sessions, grants, quota entries,
  Invitation redemption, and OTP rows
- retained terminal Trips have null ownership, null quota linkage,
  `identity_erased_at`, `archived_at`, erased client request IDs, and redacted
  free text while successful result references and bounded telemetry remain
- old Session replay fails after closure and no Hermes deletion call occurs

## Full Gate

```text
uv run pytest -q
  46 passed, 1 dependency deprecation warning
```

The warning is Starlette's TestClient compatibility warning from the installed
FastAPI dependency and is not a failed application assertion.

P4 remains unopened because every A0 decision and the explicit A0 acceptance
gate are still unchecked. No `/api/admin/*` route was implemented.
