# Implementation Checklist

Status: **v0.1.1 Source Integration Pushed / Main Merge Pending / Production Artifact Unchanged**

Repository state and recovery sequence: [v0.1.1 Source Integration Gate](v0.1.1-source-integration-gate.md).

This checklist translates the accepted design into executable work. It is an
execution tracker, not a new source of product truth. If it conflicts with a
higher-authority document, fix this checklist before implementation.

Rules:

- Complete phases strictly in order unless a section explicitly says it may run
  in parallel.
- A checked implementation item is not accepted until its evidence items are
  also checked.
- Within one authorized BFF slice, P0-P4 are serial internal checkpoints and
  continue automatically only after their evidence passes.
- Stop for D0, sibling-frontend work, v0.2 start, production deployment, or an
  unresolved cross-repository/product boundary.
- Do not commit, push, deploy, mutate production data, or edit sibling
  repositories unless that action is explicitly authorized.
- Preserve the exact status language defined in `AGENTS.md`.

## D0 — Documentation Freeze

### Product and architecture

- [x] Freeze v0.1 goals and non-goals.
- [x] Freeze the `travel-web -> travel-web-api -> hermes-travel` boundary.
- [x] Freeze invitation-gated passwordless email registration and login.
- [x] Freeze one single-use Invitation per registration.
- [x] Freeze three beta-lifetime successful generation credits per account.
- [x] Freeze seven-day absolute User and Administrator sessions.
- [x] Freeze seven-day user-visible Trip History.
- [x] Freeze permanent de-identified Content Archive retention.
- [x] Freeze Account Closure: delete identity, preserve trip content, and block
  closure while a Trip Attempt is non-terminal.
- [x] Freeze the separate static `travel-admin` frontend and shared BFF
  Administrator API boundary.
- [x] Record the accepted Python/FastAPI/PostgreSQL stack.

### Documentation evidence

- [x] `CONTEXT.md` contains canonical domain language.
- [x] Product, architecture, API, database, and implementation documents agree.
- [x] Architecture decisions that meet the ADR threshold are recorded.
- [x] Markdown fences and JSON examples parse successfully.
- [x] User explicitly accepts v0.1 D0.

### D0 gate

- [x] Set status to `v0.1 Documentation Accepted / Implementation Pending`.
- [x] Record v0.2 and later work separately from v0.1 implementation.
- [x] User explicitly resumes v0.1 implementation after frontend D0 acceptance.

## A0 — travel-admin Product Freeze

A0 is documentation-only. It is accepted and opens P4.0-P4.5.

### Decisions to resolve

- [x] Freeze default seven-day plus filterable permanent Content Archive Trip
  inspection.
- [x] Freeze Dashboard User/Trip/Invitation metrics, rolling windows, exception
  feed, server `as_of`, and terminal-success denominator.
- [x] Freeze paginated User search, masked list email, audited/no-store full
  email reveal, and no bulk email export.
- [x] Freeze disable/restore behavior, required reason, immediate session
  revocation, and no automatic restoration of role/quota/history/session.
- [x] Replace positive-only manual grants with immutable signed adjustments,
  non-negative post-balance, and one linked reversal.
- [x] Freeze 1-200 short-code Invitation batches, 1-90 day expiry, HMAC-only
  storage, one-time disclosure, lookup, and irreversible code/batch disable.
- [x] Freeze Trip exception taxonomy, failed Writer diagnostic boundary, and
  READY/EXPIRED read-only Artifact boundary.
- [x] Freeze no recent re-authentication, MFA, or second-confirmation protocol;
  reason, explicit result-labelled control, authorization, idempotency,
  transaction, and audit remain mandatory.
- [x] Keep database roles `USER`/`ADMIN`; derive one OWNER product identity from
  configured immutable `app_user.id`.
- [x] Freeze permanent append-only redacted audit visibility for OWNER/ADMIN
  with no bulk export.
- [x] Record page grouping from the endpoint inventory; visual page map,
  responsive behavior, and template selection remain the separately gated P5B
  frontend concern.
- [x] Freeze Dashboard, Trip-generation, and structured preference report
  metrics and privacy thresholds.

### A0 evidence and gate

- [x] Update BFF Administrator API contracts after each accepted decision.
- [x] Record OWNER as product identity, signed adjustment, one-time disclosure,
  and failed diagnostic draft in canonical documentation.
- [x] Keep the accepted decisions in the ordered authority documents; no
  additional ADR is required for this implementation slice.
- [x] Verify no Administrator screen requires direct PostgreSQL or
  `hermes-travel` access.
- [x] User explicitly freezes A0 and authorizes P4.0-P4.5 serial execution.

## P0 — Service Skeleton and Security Boundary

### Repository foundation

- [x] Create `pyproject.toml` for Python 3.12.
- [x] Lock runtime and development dependencies.
- [x] Create the planned capability-oriented `src/` layout.
- [x] Create unit and PostgreSQL integration test directories.
- [x] Configure Ruff formatting and linting.
- [x] Configure pytest and asynchronous test support.
- [x] Add a local environment example containing names but no secrets.
- [x] Add container ignore rules and verify secrets cannot enter the build
  context.
- [x] Add a non-root production Dockerfile without deploying it.

### Application foundation

- [x] Create the FastAPI application factory and version metadata.
- [x] Implement request/correlation IDs.
- [x] Implement the stable public error envelope.
- [x] Add `/health` for process liveness.
- [x] Add `/ready` for required dependency readiness.
- [x] Configure generated OpenAPI as the frontend type-contract source.
- [x] Enforce request-size and JSON content-type rules where applicable.

### Configuration and secrets

- [x] Define typed settings for PostgreSQL, Hermes, DirectMail, cookies, origins,
  timeouts, and logging.
- [x] Fail startup on missing production-required settings.
- [x] Redact database credentials, internal credentials, OTPs, cookies, and
  personal fields from logs.
- [x] Keep development defaults unusable as production secrets.

### PostgreSQL and migrations

- [x] Configure SQLAlchemy 2 asynchronous sessions with bounded pooling.
- [x] Create Alembic configuration and an empty baseline migration.
- [x] Define transaction helpers without a generic repository abstraction.
- [x] Verify upgrade from an empty database.
- [x] Verify downgrade behavior and document any intentionally irreversible
  migration.
- [x] Document creation of the dedicated `travel_web` database and role without
  executing it in production.

### Hermes integration foundation

- [x] Implement one typed `httpx` client boundary.
- [x] Configure connect/read/write/pool timeouts.
- [x] Validate upstream JSON before returning or persisting it.
- [x] Normalize upstream transport/protocol errors into stable internal errors.
- [x] Propagate correlation ID and a configured internal credential.
- [x] Confirm whether existing Hermes endpoints already satisfy service-auth and
  idempotency requirements.
- [x] If Hermes needs a contract change, stop and open a separately authorized
  minimal sibling-repository slice.

### P0 acceptance evidence

- [x] `uv sync` succeeds from a clean environment.
- [x] `uv run ruff check .` passes.
- [x] `uv run ruff format --check .` passes.
- [x] `uv run pytest` passes.
- [x] `uv run alembic upgrade head` succeeds on an isolated empty PostgreSQL
  database.
- [x] Readiness becomes unhealthy when PostgreSQL or another required dependency
  is unavailable.
- [x] Log-capture tests prove configured secrets are redacted.
- [x] No public route, production database mutation, or frontend change occurs.
- [x] P0 internal checkpoint evidence is recorded before continuing to P1.

## P1 — Invitation, Email OTP, User, and Session

### Schema and migrations

- [x] Create `app_user`.
- [x] Create `user_identity`.
- [x] Create `email_otp_challenge`.
- [x] Create `invitation` and optional batch/source representation.
- [x] Create `invitation_redemption` with one-to-one uniqueness.
- [x] Create `user_session` with only hashed session tokens.
- [x] Create `quota_grant` so verified registration can atomically grant the
  initial three beta credits; quota enforcement remains P2.
- [x] Add required unique constraints, foreign keys, and cleanup indexes.

### Invitation lifecycle

- [x] Generate cryptographically random Invitation secrets.
- [x] Store only Invitation secret hashes.
- [x] Validate unused, enabled, and unexpired state.
- [x] Consume the Invitation only inside successful registration.
- [x] Make concurrent redemption of one Invitation succeed exactly once.
- [x] Never create a shared multi-redemption campaign code.

### Email OTP lifecycle

- [x] Generate and store only hashed, purpose-bound OTP challenges.
- [x] Enforce expiry, single use, attempt limit, and resend cooldown.
- [x] Enforce per-email, per-IP, and global delivery limits.
- [x] Return non-enumerating responses for account existence.
- [x] Integrate Alibaba Cloud DirectMail API in East China 1.
- [x] Send from `no-reply@notify.kakarot8.com`.
- [x] Keep provider response bodies and identifiers out of browser responses.
- [x] Ensure registration and Account Closure challenges cannot be interchanged.

### Registration and login

- [x] Implement `POST /api/auth/email/send-code`.
- [x] Implement `POST /api/auth/email/verify`.
- [x] Bind `mode`, normalized email, purpose, and Invitation reference to the
  server-side OTP challenge; verification accepts only challenge ID and code.
- [x] Keep send-code responses identical for registered and unregistered email
  addresses.
- [x] Return `REGISTRATION_REQUIRED` or `LOGIN_REQUIRED` only after successful
  OTP proof, and never consume an Invitation during mode correction.
- [x] In one verified registration transaction, create the User and identity,
  redeem one Invitation, grant three beta credits, and create the first session.
- [x] Allow returning login without a new Invitation.
- [x] Reject login for a disabled User without revealing account state to an
  unauthenticated caller.

### Session lifecycle

- [x] Generate at least 256 bits of session-token entropy.
- [x] Store only a one-way token hash.
- [x] Issue a host-only `HttpOnly`, `Secure`, `SameSite=Lax` cookie.
- [x] Enforce fixed `created_at + 7 days` expiry without sliding renewal.
- [x] Rotate sessions on login and privilege-sensitive identity changes.
- [x] Implement `GET /api/me`.
- [x] Implement idempotent `POST /api/auth/logout`.
- [x] Revoke all affected sessions on disablement or role change.
- [x] Validate canonical user/admin Origins on mutations.

### P1 acceptance evidence

- [x] Registration success creates exactly one User, identity, redemption,
  initial grant, and session.
- [x] Account existence cannot be inferred from send-code response shape,
  timing class, or public error code.
- [x] Verified login/register mode correction preserves the proven email and
  creates no User, session, grant, or Invitation redemption prematurely.
- [x] Repeated or concurrent Invitation redemption creates at most one User.
- [x] Invalid, expired, over-attempt, reused, or wrong-purpose OTP fails safely.
- [x] Raw OTP, Invitation, and session tokens are absent from DB and logs.
- [x] Forged, expired, revoked, or disabled-User sessions are rejected.
- [x] Logout replay remains successful and creates no new state.
- [x] Origin/CSRF negative tests pass.
- [x] PostgreSQL migration and integration tests pass.
- [x] P1 internal checkpoint evidence is recorded before continuing to P2.

## P2 — Trip Ownership, Quota, and Hermes Job Proxy

### Schema and migrations

- [x] Create `user_trip`.
- [x] Create `trip_quota_entry`.
- [x] Add unique `(user_id, client_request_id)`.
- [x] Add unique upstream job and result-reference constraints where required.
- [x] Add indexes for ownership reads, history ordering, reconciliation, and
  quota settlement.
- [x] Preserve only opaque Hermes job/result references across the database
  boundary.

### Submission and quota reservation

- [x] Implement normalized request hashing.
- [x] Derive User only from the validated session.
- [x] Lock the User/period quota source before computing remaining units.
- [x] Return an existing Trip Attempt for an identical idempotent retry.
- [x] Return `409 REQUEST_ID_CONFLICT` for a reused key with different input.
- [x] Enforce the partial unique one-active-Trip constraint per User.
- [x] Return the owned active-trip projection for `409 ACTIVE_TRIP_EXISTS`.
- [x] Create `user_trip=SUBMITTING` and `trip_quota_entry=RESERVED` atomically.
- [x] Commit before network I/O.
- [x] Submit to Hermes with one stable opaque upstream idempotency key.
- [x] Persist the returned Hermes job ID safely after upstream acceptance.
- [x] Release the reservation only when pre-acceptance failure is known.

### Job, result, SSE, and artifact ownership

- [x] Implement authenticated `POST /api/trip/async`.
- [x] Implement owned job-status polling.
- [x] Implement owned SSE relay with polling fallback.
- [x] Implement owned result projection.
- [x] Implement owned artifact create/status/download paths.
- [x] Implement authenticated place list/detail proxy paths without treating
  place IDs as User-owned objects.
- [x] Return 404 for unknown and other-User objects.
- [x] Never treat possession of `job_id` or `result_record_id` as authorization.
- [x] Validate and allowlist every upstream field exposed publicly.

### Settlement and reconciliation

- [x] Consume one reserved unit on `SUCCESS`.
- [x] Release one reserved unit on `FAILED`, `TIMEOUT`, or `REJECTED`.
- [x] Guard settlement with `WHERE status = 'RESERVED'`.
- [x] Implement bounded reconciliation for stale `SUBMITTING`, `PENDING`,
  `RUNNING`, and `RESERVED` states.
- [x] Use stable upstream idempotency to recover an accepted-but-unsaved job.
- [x] Never release solely because a local timer elapsed.
- [x] Cap reconciliation retry count and expose unresolved invariants to
  operations.

### P2 acceptance evidence

- [x] Two concurrent last-unit reservations produce exactly one success.
- [x] Two different concurrent submissions for one User produce one active Trip
  Attempt and one `ACTIVE_TRIP_EXISTS` response.
- [x] Duplicate identical submit produces one trip, one upstream job, and one
  quota reservation.
- [x] Duplicate conflicting submit returns 409 with no extra state.
- [x] Repeated terminal events settle quota exactly once.
- [x] Success/failure races produce one legal terminal state.
- [x] Cross-User job, result, SSE, and artifact access fails without existence
  disclosure.
- [x] Restart/reconciliation tests recover accepted uncertain submissions.
- [x] Hermes transport bodies and credentials never enter public errors.
- [x] P2 internal checkpoint evidence is recorded before continuing to P3.

## P3 — Trip History, Content Archive, and Account Closure

### Trip History

- [x] Implement cursor-based `GET /api/me/trips`.
- [x] Use stable `(created_at, id)` ordering.
- [x] Filter all rows by the session User.
- [x] Return only rows whose seven-day visibility has not expired.
- [x] Include safe failure code, message, and retryability only.
- [x] Include a bounded `retry_input` structured projection without browser
  identity fields, prompts, or provider payloads.
- [x] Preserve navigation to an owned successful result.

### Content Archive

- [x] Implement an idempotent transition that marks expired rows archived.
- [x] Retain structured non-identifying request data, result reference/projection,
  terminal outcome, safe failure category, latency, and quality telemetry.
- [x] Remove or redact personal information from free-text archive fields.
- [x] Keep archived content unavailable from the User history endpoint.
- [x] Define and test the authorized internal archive read boundary before
  exposing it to `travel-admin`.

### Account Closure

- [x] Implement `POST /api/me/closure/send-code`.
- [x] Implement `POST /api/me/closure/confirm`.
- [x] Require an opaque challenge ID plus a fresh, single-use closure-purpose
  OTP.
- [x] Lock the User during closure.
- [x] Return `409 ACTIVE_TRIP_IN_PROGRESS` with no mutation when an owned Trip
  Attempt is `SUBMITTING`, `PENDING`, or `RUNNING`.
- [x] Revoke and delete all sessions.
- [x] Delete email identity and other direct personal identifiers.
- [x] Set every retained terminal `user_trip.user_id` to null.
- [x] Set `identity_erased_at` and archive rows still inside the visibility
  window.
- [x] Remove/redact personal free text.
- [x] Delete or irreversibly de-identify Invitation, quota, and audit references
  that could reconnect the User.
- [x] Delete the `app_user` row after dependent cleanup.
- [x] Never call Hermes to delete retained generated content.
- [x] Expose no per-trip delete endpoint or button in v0.1.

### P3 acceptance evidence

- [x] History pagination remains stable while new rows arrive.
- [x] History returns only the current User's last seven days.
- [x] Expired rows disappear from User history but remain in the Content Archive.
- [x] Failed rows expose no raw provider or stack-trace details.
- [x] Closure with a non-terminal trip changes no state.
- [x] Closure deletes identity/session data and leaves retained content with no
  reversible owner mapping.
- [x] Closed identity cannot use an old session.
- [x] P3 internal checkpoint passes; P4 also waits for accepted Administrator A0.

## J0.5 — BFF Joint-Integration Preparation

### SSE interruption semantics

- [x] Inspect the current BFF SSE producer and frozen `travel-web` consumer
  without editing the sibling repository.
- [x] Emit a transport-only `interrupted` event with no business terminal
  `status`.
- [x] Preserve the active Trip Attempt and `RESERVED` quota on stream transport
  failure.
- [x] Settle only after a true Hermes terminal SSE event or authenticated polling
  response.
- [x] Document the exact frontend polling-fallback contract delta.

### Local joint-integration harness

- [x] Reuse only the protected `.local-postgres` asset on `127.0.0.1:55432`.
- [x] Guard every helper with `APP_ENV`, loopback, `travel_web_test*`, Origin,
  Cookie, Hermes-port, and generated-secret checks.
- [x] Provide graceful PostgreSQL start/status/stop and guarded Alembic reset
  commands.
- [x] Provide one independently single-use local Invitation seed that stores
  only a hash.
- [x] Provide a console OTP harness outside the normal runtime app, with no OTP
  query endpoint and production fail-closed behavior.

### J0.5 acceptance evidence

- [x] SSE interruption tests prove no Trip/quota settlement and retained
  `/api/me.active_trip`.
- [x] Later polling SUCCESS and FAILED tests prove exactly-once consume/release.
- [x] Ruff, unit, PostgreSQL integration, full pytest, and Alembic drift checks
  pass.
- [x] Local `/health`, `/ready`, unauthenticated `/api/me`, and Origin negative
  checks are recorded.
- [x] Disposable PostgreSQL is stopped gracefully after testing.
- [x] No P4, v0.2, sibling edit, production connection, deploy, commit, or push
  occurs.

## J1 — SSE Non-Terminal EOF Repair

### Stream termination semantics

- [x] Confirm the real 16.265-second stream ended while the upstream job was
  non-terminal.
- [x] Confirm BFF's default read timeout is 90 seconds and Hermes keepalive is
  15 seconds.
- [x] Emit exactly one `interrupted` after clean EOF without `complete` or
  `failed`.
- [x] Emit exactly one `interrupted` after normalized timeout/network errors.
- [x] Keep the Trip active, quota `RESERVED`, and `/api/me.active_trip`
  present after interruption.
- [x] Append no `interrupted` after a true `complete` or `failed`.
- [x] Keep the frozen payload free of any business terminal `status`.
- [x] Enforce a 45-second minimum read-timeout margin in the guarded local
  startup path and document the 90-second default.

### J1 acceptance evidence

- [x] Clean-EOF and transport-exception tests cover later SUCCESS and FAILED
  polling with exactly-once quota settlement.
- [x] True complete and failed tests prove normal consume/release with no
  appended interruption.
- [x] Ruff, unit, PostgreSQL integration, full pytest, reversible Alembic, and
  `git diff --check` pass on isolated `travel_web_test_j1`.
- [x] Existing joint BFF, Hermes tunnel, Web process, `.local-postgres/`, and
  `travel_web_test_joint` remain untouched.
- [x] No P4, v0.2, sibling edit, production connection, deploy, commit, or push
  occurs.

## P4 — Administrator API

Entry requirement:

- [x] A0 travel-admin product decisions accepted.
- [x] P3 internal checkpoint passed.

### P4.0 — Migration, OWNER, idempotency, and audit foundation

- [x] Add an Alembic migration compatible with empty and existing v0.1 schemas.
- [x] Add OWNER id configuration and ADMIN/OWNER capability projection without
  adding an OWNER database role or email matching.
- [x] Add permanent actor-scoped UUID idempotency with canonical request hashes,
  replay-safe results, conflict detection, and concurrency protection.
- [x] Add permanent append-only `admin_audit_log` with redacted before/after,
  result/error, request/idempotency ids, IP digest, and bounded client data.
- [x] Add immutable signed `quota_adjustment` and reversal linkage without
  rewriting existing `quota_grant` balances.
- [x] Extend Invitation persistence for batch, sequence, required expiry, and
  keyed HMAC while retaining legacy redeemed rows.
- [x] Prove raw codes, full email, Writer drafts, prompts, tokens, Artifact
  bytes/paths, SQL, and stack traces cannot enter idempotency/audit payloads.
- [x] Record and test the controlled `SYSTEM_BOOTSTRAP` OWNER procedure.

### P4.1 — Identity, Users, roles, and signed quota

- [x] Implement `GET /api/admin/me` with product identity/capabilities.
- [x] Implement paginated User list/detail with fuzzy `q`, exact structured
  filters, stable sorting, and masked list email.
- [x] Implement audited/no-store full-email reveal.
- [x] Implement disable/restore with ADMIN/OWNER matrix and immediate session
  revocation; restoration creates no old session.
- [x] Implement OWNER-only ADMIN grant/revoke and final-OWNER protection.
- [x] Implement signed add/subtract, non-negative balance rejection, immutable
  ledger, and exactly-once linked reversal.
- [x] Test ACTIVE/DISABLED eligibility and missing/closed/de-identified target
  rejection.

### P4.2 — Invitation short-code batches

- [x] Implement list/create/detail and irreversible whole-batch disable.
- [x] Generate exact `YT-XXXX-XXXX` codes with secure randomness and the
  non-ambiguous alphabet.
- [x] Default to 50/30 days and enforce count 1-200 and expiry 1-90 days.
- [x] Store keyed HMAC only; retry digest collisions safely.
- [x] Disclose raw codes only in the first successful response; idempotent replay
  returns the batch without codes.
- [x] Implement JSON-body full-code lookup without URL/log/trace/audit leakage.
- [x] Implement irreversible one-code disable and distinct
  ACTIVE/EXPIRED/DISABLED/EXHAUSTED status.
- [x] Preserve exactly-once registration redemption and legacy registration
  audit compatibility.

### P4.3 — Dashboard, reports, and audit query

- [x] Implement frozen Dashboard metrics and server `as_of`.
- [x] Implement Trip-generation trends, formulas, distributions, P50/P95, and
  over-180-second metrics with explicit zero-denominator semantics.
- [x] Implement structured preference aggregates, request-level multi-select
  dedupe, canonical-place preference, aggregate-only distinct Users, and
  `<3 -> OTHER`.
- [x] Exclude raw notes, email, prompts, Writer text, generic BI, word cloud,
  arbitrary SQL, and user-level preference drill-down.
- [x] Implement allowlisted paginated audit-event query without bulk export.

### P4.4 — Trip/archive/failure-draft/Artifact projection

- [x] Inspect existing versioned Hermes internal HTTP for global jobs/steps,
  failed drafts, structured results, and Artifact metadata/binary.
- [x] If any required contract is absent, stop P4.4 and provide the minimal
  service-authenticated contract; do not connect to Hermes databases or edit
  the sibling repository.
- [x] Implement permanent archive filters and 180-second/degraded exception
  classification only if the Hermes contract exists.
- [x] Implement audited/no-store unpublished failed-draft read with no
  publish/success/share/export/Artifact/delete action.
- [x] Implement read-only READY/EXPIRED `pdf`/`share_image` metadata and audited
  download without storage-path exposure or a BFF truth source.

### P4.5 — Contract and acceptance evidence

- [x] OpenAPI contains every frozen endpoint, schema, filter, capability, and
  stable error code.
- [x] Visitor 401, USER 403, and complete ADMIN/OWNER matrix tests pass.
- [x] Every mutation has authorization, UUID idempotency, concurrency,
  transaction, failure-audit, and secret-redaction evidence.
- [x] Session revocation, one-time code disclosure, concurrent redemption,
  signed quota/reversal, de-identification, and Artifact read-only tests pass.
- [x] Empty and existing-baseline Alembic upgrades pass on disposable
  PostgreSQL.
- [x] `uv run pytest` passes.
- [x] `uv run ruff check .` passes.
- [x] `uv run ruff format --check .` passes.
- [x] `git diff --check` passes.

### P4 acceptance evidence

- [x] P4.0-P4.5 each pass serially with PostgreSQL/security evidence.
- [x] No generic SQL/table/field/query endpoint, sibling edit, production
  connection, deployment, commit, or push occurs.
- [x] Set status to `Implementation Complete / Acceptance Pending`.

## P5 — Frontend Integration Workstreams

### P5A — travel-web

- [ ] Open a separately reviewed change in the sibling `travel-web` repository.
- [ ] Route production API calls through same-origin `/api`.
- [ ] Add registration/login/logout and session restoration.
- [ ] Add Header identity, quota, and history entry.
- [ ] Add three-credit display and exhausted state.
- [ ] Submit trips through the BFF without browser-trusted identity fields.
- [ ] Use authenticated SSE with polling fallback.
- [ ] Add seven-day Trip History and safe failed-attempt states.
- [ ] Preserve and ownership-protect PDF export.
- [ ] Add fresh-OTP Account Closure flow.
- [ ] Preserve existing trip form, map, and result rendering.
- [ ] Keep React/Vite architecture; do not introduce a framework migration.

### P5 acceptance evidence

- [ ] Registration, returning login, logout, and seven-day session restoration
  pass.
- [ ] Expired and revoked sessions recover safely.
- [ ] Quota reservation, exhaustion, success consumption, and failure release
  are visible correctly.
- [ ] Duplicate browser submission does not duplicate a job or quota charge.
- [ ] History and result navigation pass on desktop and mobile.
- [ ] Another User's IDs cannot be opened through edited URLs.
- [ ] Network inspection shows no direct browser call to Hermes.

### P5B — travel-admin

- [ ] Create the separate `travel-admin` repository only after authorization.
- [ ] Select and license-check the accepted reusable React admin template.
- [ ] Remove unused template features and dependencies.
- [ ] Use same-origin `/api/admin/*` only.
- [ ] Generate or validate TypeScript contracts from BFF OpenAPI.
- [ ] Implement session bootstrap and ADMIN_REQUIRED handling.
- [ ] Implement the accepted page map and loading/empty/error states.
- [ ] Add explicit result-labelled action controls and reason capture without a
  separate confirmation, recent re-authentication, or MFA protocol.
- [ ] Ensure no database, DirectMail, Hermes, or internal credentials enter the
  browser bundle.
- [ ] Verify no ordinary-User account/quota/history surface exists.
- [ ] Browser bundle secret scan passes.
- [ ] Desktop flow passes; mobile behavior matches the A0 boundary.
- [ ] No direct PostgreSQL or Hermes request exists.

### P5 gate

- [ ] User accepts both frontend workstreams before deployment.

## P6 — Deployment and Live Acceptance

### Pre-deployment

- [ ] `hermes-travel` reliability gate is accepted separately.
- [ ] P0-P4 internal checkpoints and both frontend workstreams are accepted.
- [ ] Production database backup and restore rehearsal pass.
- [ ] Migration plan and downgrade/forward-repair plan are reviewed.
- [ ] PostgreSQL, Redis, MySQL, and internal API exposure is audited.
- [ ] Nginx configuration and rollback diff are reviewed.
- [ ] Required DNS, TLS, DirectMail domain, and sender status are verified.
- [ ] Production secrets are installed outside version control.

### Deployment order

- [ ] Create the dedicated database and least-privilege role.
- [ ] Run reviewed migrations.
- [ ] Deploy BFF privately with no public route.
- [ ] Verify BFF-to-Hermes connectivity and internal authentication.
- [ ] Deploy compatible `travel-admin` and `travel-web` assets.
- [ ] Route user and Administrator same-origin `/api` paths to the BFF.
- [ ] Remove or restrict direct public Hermes access.
- [ ] Verify rollback can restore the previous frontend/routing without
  reopening unauthenticated generation.

### Live acceptance

- [ ] Registration, returning login, logout, and session expiry pass.
- [ ] Invitation replay fails.
- [ ] Authenticated generation and result retrieval pass.
- [ ] Duplicate submission is idempotent.
- [ ] Three-credit exhaustion passes.
- [ ] FAILED, TIMEOUT, and REJECTED each release quota exactly once.
- [ ] Concurrent last-unit reservation produces exactly one success.
- [ ] Cross-User job, SSE, result, artifact, and history access fails.
- [ ] SSE and polling fallback both pass.
- [ ] Restart/reconciliation recovers in-flight work.
- [ ] History expiry and archive transition pass.
- [ ] Account Closure deletes identity and retains de-identified content.
- [ ] Administrator authorization and audit pass.
- [ ] Direct public Hermes bypass fails.
- [ ] Production logs contain no OTP, raw session, Invitation, provider secret,
  or personal free-text leakage.

### P6 gate

- [ ] Record exact deployed versions and migration revision.
- [ ] Record live acceptance evidence and unresolved risks.
- [ ] Record rollback point and owner.
- [ ] User accepts deployment.
- [ ] Set status to `Deployment Accepted`.

## v0.1.1 — Unique Display Name

### V011-D0 — Documentation Freeze

- [x] Freeze the single-capability product boundary and non-goals.
- [x] Freeze the canonical Display Name term and its separation from Login
  Identity, User id, authorization, ownership, account linking, and Hermes.
- [x] Freeze the 2-24 character policy, normalization, reserved names,
  seven-day cooldown, and 15-day quarantine.
- [x] Freeze API, database, closure, Administrator-search, migration, and
  acceptance contracts.
- [x] User accepts `Documentation Accepted / Implementation Pending` before any
  v0.1.1 code change.

### V011-P0 — Schema and Domain Foundation

- [x] Add an Alembic migration from the existing v0.1 head.
- [x] Add and backfill `display_name_normalized` before enforcing non-null and
  unique constraints.
- [x] Add nullable `display_name_changed_at`.
- [x] Add `display_name_quarantine` with digest/expiry only and no User mapping.
- [x] Add one canonical validator, normalizer, default generator, and former-name
  digest implementation.
- [x] Prove empty and existing-v0.1 upgrades on disposable PostgreSQL.

### V011-P1 — Registration and Backfill

- [x] Generate a unique `user_`-prefixed default in each new-User transaction.
- [x] Preserve Invitation consumption, initial-credit exactly-once behavior, and
  first Session creation.
- [x] Preserve valid unique existing names; for a pre-existing normalized
  collision keep the earliest `(created_at, id)` owner and assign generated
  defaults to later Users.
- [x] Backfill every null, invalid, or reserved Display Name with no duplicate
  normalized key.
- [x] Return non-null Display Name through `/api/me`.

### V011-P2 — Self-Service Rename

- [x] Implement `PATCH /api/me/profile` for the authenticated User only.
- [x] Enforce format, normalization, reserved names, uniqueness, cooldown, and
  quarantine in one transaction.
- [x] Make exact replay a no-op and handle same-key presentation changes.
- [x] Return stable unavailable, invalid, reserved, and cooldown errors.
- [x] Add response models and OpenAPI assertions.

### V011-P3 — Closure and Administrator Projection

- [x] Account Closure inserts the no-owner 15-day quarantine digest before
  deleting `app_user`.
- [x] Disable/restore retains the Display Name.
- [x] Administrator `q` search includes Display Name.
- [x] No Administrator Display Name write endpoint is added.
- [x] Audit and authorization continue to use immutable User ids.

### V011-P4 — Acceptance Evidence

- [x] Concurrent claims for one normalized name produce exactly one owner.
- [x] Case and full-width variants collide.
- [x] Reserved and invalid names fail without changing the User.
- [x] First manual rename succeeds; a later rename inside seven days fails.
- [x] Exact replay is a no-op.
- [x] Former names are unavailable for 15 days and claimable after expiry.
- [x] Account Closure retains no plaintext former name or User mapping.
- [x] Disabled User restoration retains the same Display Name.
- [x] Cross-User and unauthenticated mutation tests fail safely.
- [x] User identity, ownership, quota, history, Administrator audit, and Hermes
  integration regression tests pass unchanged.
- [x] `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  Alembic checks, and `git diff --check` pass.
- [x] No sibling edit, production connection, deployment, commit, or push occurs.
- [x] Set status to `Implementation Complete / Acceptance Pending`.

### V011-F1 — Separate `travel-web` Gate

- [ ] Open only after explicit sibling-repository authorization.
- [ ] Display the current Display Name in the authenticated account surface.
- [ ] Add edit, unavailable, reserved, validation, cooldown, loading, and error
  states against the frozen BFF contract.
- [ ] Preserve the existing login, quota, generation, history, PDF, and Account
  Closure flows.

## Phase Handoff Template

Use this exact evidence order at every implementation gate:

1. **Status**
2. **Modified files**
3. **Implemented scope**
4. **Explicitly untouched scope**
5. **Commands and results**
6. **Database/migration evidence**
7. **HTTP/runtime evidence**
8. **Security and negative-path evidence**
9. **Unresolved blockers**
10. **Requested next authorization**
