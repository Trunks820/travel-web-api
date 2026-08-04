# Implementation and Acceptance Plan

Status: **v0.1.1 Verified Implementation Artifact Deployed / Source Integration Complete on Main / Owner Live UAT Accepted**

Repository state and recovery sequence: [v0.1.1 Source Integration Gate](v0.1.1-source-integration-gate.md).

The project advances strictly through D0 and P0-P6. D0, sibling-frontend work,
and P6 deployment are hard user-authorization gates. Within one explicitly
authorized BFF implementation slice, P0-P4 are serial internal checkpoints:
each checkpoint must pass its evidence before work continues, but does not
require a separate user reply.

## D0 — Documentation Freeze

Deliverables:

- project scope and non-goals
- ownership matrix
- target topology and security boundary
- session and service-auth principles
- API compatibility contract
- database and quota state machine
- invitation, email OTP, Administrator role, and audit contracts
- phased implementation and acceptance plan
- executable phase-by-phase implementation checklist
- v0.1/v0.2 release roadmap
- root `AGENTS.md`

Acceptance:

- no unresolved contradiction between documents
- every unresolved product choice is marked `[ASK USER]`
- `travel-web`, `travel-web-api`, and `hermes-travel` ownership is explicit
- quota reserve/consume/release semantics are deterministic
- direct public Hermes bypass is forbidden
- no implementation, deployment, or database mutation has occurred

Accepted status:

`Documentation Accepted / Implementation Pending`

## P0 — Service Skeleton and Security Boundary

Scope:

- Python 3.12/FastAPI project skeleton
- locked dependency management
- configuration and secret contract
- health and readiness endpoints
- SQLAlchemy/Alembic foundation
- dedicated PostgreSQL database/role deployment plan
- internal Hermes client with timeouts and response validation
- Nginx same-origin `/api` design and rollback
- structured logging with redaction
- test/lint/format commands

Not in P0:

- production login
- quota enforcement
- user history
- frontend UI changes
- live Nginx cutover

Acceptance:

- unit checks pass
- empty-schema migration upgrade works on an isolated database
- readiness fails when required dependencies are unavailable
- secrets do not appear in logs or committed files
- no public deployment

## P1 — Authentication and Server-side Session

Entry requirement:

- D0 login-method decision accepted
- P0 internal checkpoint passed

Scope:

- one login provider only
- invitation redemption and email OTP challenge lifecycle
- `app_user`, `user_identity`, `user_session`
- secure cookie lifecycle
- `/api/me` and logout
- origin/CSRF enforcement
- session expiry, rotation, and revocation

Acceptance:

- login success and safe failure
- forged/expired/revoked session rejected
- raw session token absent from DB and logs
- logout is idempotent
- disabled user cannot create a new session
- an Invitation is consumed only by a successful verified registration
- frontend integration is still deferred

## P2 — Trip Ownership and Quota

Entry requirement:

- quota policy decisions accepted
- P1 internal checkpoint passed

Scope:

- `user_trip` and `trip_quota_entry`
- authenticated trip submission
- idempotent upstream job creation
- one active Trip Attempt per User with refresh-safe restoration
- transactional reserve/consume/release
- ownership enforcement for job, SSE, result, and artifacts
- bounded reconciliation

Acceptance:

- all database acceptance cases in `database-and-quota.md` pass
- no cross-user object access
- no duplicate reservation under retry/concurrency
- failure/timeout/rejection releases exactly once
- successful generation consumes exactly once
- upstream uncertainty does not silently double-submit or release

## P3 — User Trip History

Scope:

- cursor-based own-history API
- successful result navigation
- approved failed-history behavior
- seven-day visibility and permanent Content Archive transition
- fresh-OTP Account Closure with identity erasure and archive de-identification
- Account Closure conflict handling for non-terminal Trip Attempts

Acceptance:

- only the authenticated user's rows are returned
- stable pagination under new inserts
- closed/disabled-user behavior matches policy
- history does not query `hermes-travel` tables directly
- Account Closure removes identity without deleting de-identified trip content
- Account Closure cannot orphan a non-terminal Trip Attempt

## J0.5 — BFF Joint-Integration Preparation

Entry requirement:

- P0-P3 local acceptance passed
- the current `travel-web` J0 transport contract is inspected read-only

Scope:

- distinguish SSE transport interruption from a Hermes business terminal state
- preserve active Trip and reserved quota until a true terminal result arrives
- define the frontend polling-fallback delta
- provide guarded disposable PostgreSQL lifecycle, schema reset, one-person
  Invitation seed, and local OTP harness procedures
- verify local BFF HTTP/security behavior without production or sibling writes

Acceptance:

- transport interruption emits no business `failed` event or terminal `status`
- Trip and quota remain active/reserved after stream interruption
- later SUCCESS/FAILED polling settles exactly once
- the browser-facing delta is documented precisely
- every local helper refuses production, non-loopback, or non-test database
  configuration
- the local PostgreSQL server is stopped gracefully after evidence collection

J0.5 does not open P4, P5, P6, v0.2, deployment, or sibling-repository edits.

## J1 — SSE Non-Terminal EOF Repair

Entry evidence:

- real joint integration observed upstream SSE ending after about 16 seconds
  while the Hermes job was still `RUNNING`
- BFF Trip remained `RUNNING`, quota remained `RESERVED`, and later true
  terminal reconciliation settled correctly

Scope:

- treat clean upstream EOF without `complete` or `failed` exactly like a
  non-terminal stream interruption
- emit the frozen `interrupted` payload exactly once
- preserve Trip and quota state until authenticated polling or a later SSE
  returns a true terminal status
- retain safe timeout margin over Hermes's 15-second keepalive

Acceptance:

- clean EOF after progress and timeout/network errors each emit one
  `interrupted` and no false business terminal status
- true `complete` and `failed` events settle normally and append no
  `interrupted`
- polling after interruption settles SUCCESS/FAILED exactly once
- PostgreSQL integration, unit, Ruff, migration, full-suite, and diff checks
  pass without disturbing the active joint-integration database

J1 changes no frontend payload shape and does not open P4, P5, P6, v0.2,
deployment, or sibling-repository edits.

## A0 — travel-admin Product Contract Freeze

Status: **Accepted**.

The frozen contract keeps `USER`/`ADMIN` as the database roles and derives one
OWNER product identity from a configured immutable `app_user.id`. It freezes
the task-specific endpoint inventory, OWNER/ADMIN capability matrix, signed
quota adjustments, HMAC-backed short Invitation codes with OWNER-only encrypted recovery,
permanent redacted audit, archive/failed-draft/Artifact read boundaries,
Dashboard/report formulas, UUID idempotency, and the no-secondary-confirmation
policy. Frontend page composition and template choice remain P5B concerns and
do not block the BFF API implementation.

Accepted status:

`Documentation Accepted / Implementation Pending`

## P4 — Administrator API

The Administrator API is implemented only in `travel-web-api`. A0 is accepted.
The `travel-admin` repository remains a separately gated static React
workstream and never connects to PostgreSQL or `hermes-travel` directly.

### P4.0 — Persistence and security infrastructure

- Alembic migration from both empty and existing v0.1 schemas
- Invitation batch/code metadata compatible with existing redemptions
- configured immutable OWNER id and capability projection
- permanent Administrator UUID idempotency records
- permanent append-only redacted audit records
- immutable signed quota adjustment ledger

Acceptance requires migration proof, USER/ADMIN/OWNER capability-unit tests,
same-key same-request replay, conflict, concurrent-deduplication evidence, and
proof that secrets/personal bodies cannot enter audit.

### P4.1 — Identity, Users, roles, and signed quota

- `/api/admin/me`
- paginated/masked User list and detail
- separately audited/no-store full-email reveal
- disable/restore with immediate session revocation
- OWNER-only ADMIN grant/revoke and final-OWNER protection
- signed quota add/subtract, insufficient-balance rejection, ledger, and one
  linked reversal

Acceptance requires Visitor 401, USER 403, ADMIN/OWNER matrix, session
revocation, atomic balance and concurrent idempotency evidence against
PostgreSQL.

### P4.2 — Invitation batches and short codes

- batch list/create/detail and irreversible batch disable
- exact `YT-XXXX-XXXX` codes, HMAC redemption lookup, OWNER-only encrypted recovery, required 1-90 day expiry
- one-time raw disclosure and non-disclosing idempotent replay
- JSON-body full-code lookup and irreversible one-code disable
- separate ACTIVE/EXPIRED/DISABLED/EXHAUSTED states
- compatibility with existing Invitation/redemption rows

Acceptance requires format/alphabet tests, HMAC/no-log evidence, collision
retry, concurrent creation/redeem/disable, and one-time-disclosure proof.

### P4.3 — Dashboard, reports, and audit query

- frozen Dashboard counts and 24-hour terminal formula
- Trip-generation trends/rates/distributions/P50/P95/slow-task metrics
- privacy-bounded structured preference aggregation
- allowlisted paginated audit query without export

Acceptance requires exact zero-denominator semantics, 180-second exception
classification, multi-select denominator proof, `<3 -> OTHER`, and no raw
notes/email/prompt/Writer analysis.

### P4.4 — Trip/archive/failure-draft/Artifact projection

- permanent Trip archive filtering and safe operational diagnostics
- read-only unpublished failed Writer draft
- read-only READY/EXPIRED Artifact metadata and audited download
- opaque navigation among job/result/Artifact ids

Hermes P4.4-H1 is accepted and exposes the six required versioned
service-authenticated internal-admin routes. P4.4 consumes that contract with a
dedicated credential, validates safe projections and stable Artifact states,
and does not read Hermes tables, modify the sibling repository, or invent a
BFF source of truth.

### P4.5 — Contract and acceptance evidence

- complete OpenAPI endpoint/schema/error inventory
- targeted and full unit tests
- real PostgreSQL integration/security/idempotency tests
- empty and existing-baseline Alembic upgrade verification
- `uv run pytest`
- `uv run ruff check .`
- `uv run ruff format --check .`

P4 passes only when P4.0-P4.5 each pass serially. The resulting status is
`Implementation Complete / Acceptance Pending`; it is not deployment
acceptance.

## P5 — Frontend Integration Workstreams

This is a hard cross-repository gate. User frontend work is implemented and
reviewed in the sibling `travel-web` repository. Administrator frontend work is
implemented and reviewed in a separate `travel-admin` repository after A0.

### P5A — travel-web

Scope:

- production `VITE_API_BASE=/api`
- login UI and callback/result handling
- authenticated bootstrap via `/api/me`
- Header login/identity/history entry
- quota display and exhausted state
- trip submission through the BFF
- authenticated SSE with polling fallback
- history page
- safe failed-attempt view and credit-release messaging
- owned PDF export
- Account Closure
- removal of browser-generated trusted identity fields

Compatibility:

- preserve existing trip form and result rendering
- preserve job/result/artifact endpoint shapes where documented
- no Next.js migration

Acceptance:

- desktop and mobile flows
- refresh/session restoration
- expired-session recovery
- generation failure quota message
- history navigation
- PDF export ownership/error states
- no direct browser request to `hermes-travel`

### P5B — travel-admin

Scope:

- separate static React/Vite frontend
- same-origin `/api/admin/*` only
- page map and responsive boundary accepted by A0
- no ordinary-User quota/history/account surface
- no database or internal-service credential in the browser bundle

Acceptance:

- ordinary Users receive `403 ADMIN_REQUIRED`
- no direct browser request to PostgreSQL or `hermes-travel`
- Administrator mutations use explicit result-labelled controls and reason
  fields, with no recent re-authentication/MFA/second-confirmation protocol,
  and produce audit evidence

## P6 — Deployment and Live Acceptance

Preconditions:

- `hermes-travel` reliability gate accepted separately
- P0-P4 internal checkpoints passed
- P5A/P5B frontend integrations accepted
- backup and rollback verified
- database/internal port exposure audited

Deployment order:

1. create dedicated DB/role and run reviewed migrations
2. deploy BFF privately with no public route
3. verify BFF-to-Hermes internal connectivity
4. deploy compatible user and Administrator frontend assets
5. route main-site `/api` to BFF
6. verify `admin.kakarot8.com` has no direct database/internal-service path
7. remove/restrict direct public Hermes route
8. run live acceptance

Live acceptance:

- login and logout
- authenticated generation
- duplicate-submit idempotency
- quota exhaustion
- FAILED/TIMEOUT/REJECTED release
- concurrent last-unit reservation
- cross-user job/result/artifact denial
- SSE and polling fallback
- history
- service restart and reconciliation
- direct public Hermes bypass fails
- logs contain no session/OTP/personal secrets

Rollback:

- preserve DB rows and migration compatibility
- restore previous frontend and routing only through a documented decision
- never reopen an unauthenticated direct generation path merely to hide a BFF
  failure
- keep an operator-only internal smoke path protected by separate credentials

Exit status:

`Deployment Accepted`

## v0.1.1 — Unique Display Name Delivery

v0.1.1 is a new strict serial slice after the completed v0.1 implementation.
It does not reopen or renumber the accepted v0.1 P0-P4 checkpoints.

### V011-D0 — Documentation Freeze

- freeze Display Name terminology, format, normalization, reserved-name policy,
  seven-day rename cooldown, and 15-day former-name quarantine
- freeze database, API, Account Closure, Administrator search, and
  sibling-frontend boundaries
- accept migration and rollback/forward-repair behavior before code

Exit status: `Documentation Accepted / Implementation Pending`.

### V011-P0 — Schema and Domain Foundation

- add `app_user.display_name_normalized` and `display_name_changed_at`
- add the no-owner `display_name_quarantine` table
- preserve valid unique existing names, deterministically resolve any existing
  normalized collision, and backfill generated defaults before making Display
  Name fields non-null
- add the normalized unique constraint and required expiry index
- implement one canonical validator/normalizer and purpose-bound former-name
  digest

Acceptance requires empty-database and existing-v0.1 Alembic upgrade evidence on
disposable PostgreSQL, including duplicate-normalization and downgrade or
forward-repair behavior.

### V011-P1 — Registration and Existing-User Defaults

- generate a collision-safe `user_`-prefixed default in the User-creation
  transaction
- preserve exactly-once Invitation redemption, initial-credit grant, and Session
  creation
- ensure every existing User has one valid unique default after migration
- return a non-null Display Name from `/api/me`

### V011-P2 — Self-Service Rename

- implement authenticated `PATCH /api/me/profile`
- enforce format, reserved names, normalized uniqueness, seven-day cooldown, and
  15-day former-name quarantine transactionally
- treat an exact replay as a no-op and make concurrent claims deterministic
- add explicit response models and stable Display Name error codes

### V011-P3 — Closure and Administrator Projection

- quarantine only the no-owner former-name digest during Account Closure before
  deleting `app_user`
- retain the Display Name while a User is disabled and through restoration
- include Display Name in the existing trimmed, case-insensitive Administrator
  User search without adding an Administrator rename mutation
- keep Administrator actions and audit ownership keyed by immutable User ids

### V011-P4 — BFF Acceptance

- run unit, disposable-PostgreSQL integration, migration, concurrency, closure,
  authorization, OpenAPI, and full-regression checks
- prove no name becomes a login, role, ownership, linking, or Hermes input
- record `Implementation Complete / Acceptance Pending`

### V011-F1 — Separate `travel-web` Integration Gate

The main-site header/profile UI may display and edit the accepted Display Name
only in a separately reviewed sibling-repository diff. BFF acceptance does not
authorize that edit, deployment, commit, or push.

## Protected Scope

Until a phase explicitly opens it:

- do not edit `D:\tools\workSpace\hermes-travel`
- do not edit `D:\tools\workSpace\travel-web`
- do not create or modify production databases
- do not modify Nginx/firewall/container networking
- do not create a GitHub repository
- do not commit, push, or deploy

## D0 Decision Record

Resolved:

1. one single-use Invitation per registration
2. passwordless email OTP through Alibaba Cloud DirectMail API using
   `no-reply@notify.kakarot8.com`
3. three beta-lifetime generation credits, login required, failure releases
   credit, and seven-day user-facing Trip History
4. Administrator APIs remain in the BFF; `travel-admin` is a separate static
   frontend and never connects directly to PostgreSQL or `hermes-travel`
5. Python 3.12/FastAPI with SQLAlchemy, asyncpg, and Alembic is the BFF stack;
   frontend types may be generated from OpenAPI
6. all User and Administrator sessions use a fixed seven-day absolute expiry
   with no sliding or separate idle expiry
7. the first Administrator is a verified User promoted by a controlled private
   bootstrap transaction that revokes sessions, writes `SYSTEM_BOOTSTRAP`, and
   configures that immutable `app_user.id` as OWNER; authenticated OWNER-only
   role grant/revoke APIs then manage the `ADMIN` database role
8. Trip History is visible for seven days, then retained indefinitely as a
   Content Archive; Account Closure deletes identity/session data and severs
   ownership from every retained terminal Trip Attempt without deleting its
   de-identified content; a non-terminal Trip Attempt blocks closure
9. all ordinary-User login, quota, own history, safe failure, PDF, and Account
   Closure interfaces belong to `travel-web`; `travel-admin` is operations-only
10. v0.2 is limited to Linux.do L1 Community Admission and growth/quality
    validation; later commercial and community capabilities are deferred
11. v0.1.1 is limited to one globally unique mutable Display Name per User,
    with a seven-day manual rename cooldown and 15-day former-name quarantine

Remaining:

v0.1 and v0.1.1 product decisions are resolved. The v0.2 Account Closure/re-registration
anti-abuse choice remains `[ASK USER]` in `docs/release-roadmap.md` and does not
block v0.1 or v0.1.1.
