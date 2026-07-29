# J1 SSE Non-Terminal EOF Repair Evidence

Date: 2026-07-29

Status: **J1 BFF SSE Repair Complete / Joint Retest Pending**

This evidence is limited to `travel-web-api`. It is not frontend, Hermes,
P4, v0.2, deployment, production, or release acceptance.

## Initial protected state

```text
## No commits yet on main
?? .dockerignore
?? .env.example
?? .gitignore
?? .idea/
?? AGENTS.md
?? CONTEXT.md
?? Dockerfile
?? README.md
?? alembic.ini
?? alembic/
?? docs/
?? pyproject.toml
?? scripts/
?? src/
?? tests/
?? uv.lock
```

The existing PostgreSQL `127.0.0.1:55432`, BFF `127.0.0.1:6670`, Hermes
tunnel `127.0.0.1:6666`, and Web `localhost:3000` processes were already
running. They were not restarted or stopped. Verification used the separate
disposable database `travel_web_test_j1`, not the active
`travel_web_test_joint`.

## Root cause

The real request completed in `16265ms` while Hermes was still
`RUNNING / FINAL_WRITER`. BFF's configured default read timeout is 90 seconds;
the current Hermes source uses a 15-second SSE keepalive. The elapsed time was
therefore not evidence that the BFF's 90-second default had expired.

`HermesClient.stream_job()` naturally ends its async iterator when upstream
HTTP returns clean EOF. `_owned_sse()` previously emitted `interrupted` only
when that iterator raised `HermesIntegrationError`. It had no terminal-event
flag and no post-loop branch, so non-terminal clean EOF silently ended the
browser stream.

## Repaired contract

```text
Hermes timeout/network error
or clean EOF before complete/failed
  -> exactly one event: interrupted
  -> stream_state=INTERRUPTED
  -> job_status_known=false
  -> fallback=POLLING
  -> error.code=GENERATION_STREAM_INTERRUPTED
  -> no business terminal status
  -> Trip stays active
  -> quota stays RESERVED
```

Once a true `complete` or `failed` event has been processed, normal EOF or a
later transport error does not append `interrupted`. Terminal settlement
semantics remain unchanged.

The guarded local startup path now rejects
`HERMES_READ_TIMEOUT_SECONDS < 45`, which leaves three Hermes keepalive
intervals of margin. The recommended local value and production default remain
90 seconds. Clean EOF handling is independent of timeout duration; no infinite
timeout was introduced.

## PostgreSQL state and exactly-once proof

The integration matrix covers:

```text
clean EOF             -> later SUCCESS -> CONSUMED
clean EOF             -> later FAILED  -> RELEASED
transport exception   -> later SUCCESS -> CONSUMED
transport exception   -> later FAILED  -> RELEASED
```

Before polling, every case proves:

- one `interrupted` event
- no `FAILED`, `TIMEOUT`, or `REJECTED` payload
- local Trip remains `RUNNING`
- quota remains `RESERVED` with no settlement timestamp or reason
- `/api/me.active_trip` remains present

The first authenticated poll settles the quota. A repeated poll preserves the
same `settled_at` timestamp and the database still contains exactly one quota
entry. True `complete` and `failed` SSE cases append no `interrupted`.

## Verification

```text
ruff check .
  All checks passed

ruff format --check .
  all files already formatted

pytest tests/unit -q
  35 passed, 1 dependency deprecation warning

pytest tests/integration -q
  34 passed

pytest -q
  69 passed, 1 dependency deprecation warning

pytest tests/integration/test_p2_trip_quota.py -q \
  -k "complete_sse_event or failed_sse_event or sse_non_terminal_end"
  6 passed, 13 deselected

pytest tests/unit/test_hermes.py -q -k sse_timeout_and_network_errors
  2 passed, 6 deselected

alembic downgrade base
alembic upgrade head
alembic check
  upgraded through 0005
  No new upgrade operations detected

git diff --check
  passed

Settings() resolution in the repository runtime environment
  HERMES_READ_TIMEOUT_SECONDS=90
```

The Starlette/httpx warning is an existing dependency deprecation warning, not
a failed test.

## Frontend contract delta

There is no payload-shape delta from J0.5. J1 extends the server condition that
emits the already frozen `interrupted` event to include clean EOF without a
terminal event. Read-only inspection confirmed the current `travel-web`
consumer closes EventSource and immediately enables authenticated polling on
`interrupted`.

## Untouched scope

- no `travel-web` or `hermes-travel` edit
- no P4/Administrator or v0.2/Linux.do work
- no production database or deployment action
- no Nginx, firewall, commit, or push
- no change to true terminal quota semantics

Hermes enforcement of `X-Internal-Credential` remains a separate P6
pre-deployment blocker and is not represented as solved.
