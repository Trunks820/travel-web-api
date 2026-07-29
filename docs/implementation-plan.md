# Implementation and Acceptance Plan

Status: **v0.1 Documentation Accepted / Implementation Pending**

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

## P4 — Administrator API

The Administrator API is implemented in `travel-web-api`. P4 requires the
separate Administrator A0 product freeze. The `travel-admin` repository is a
separate static React workstream and never connects to PostgreSQL or
`hermes-travel` directly.

Scope:

- Administrator role enforcement
- dashboard summary API
- User search, suspension/restoration, and audited quota grants
- Invitation creation, visibility, and disablement
- seven-day Trip Attempt and safe-failure inspection
- append-only Administrator audit log
- accepted A0 archive inspection projection, if any

Acceptance:

- ordinary Users receive `403 ADMIN_REQUIRED`
- every Administrator mutation has authorization, idempotency, and audit proof
- no generic SQL/table/query endpoint exists
- OpenAPI is sufficient for a separate `travel-admin` implementation

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
- Administrator mutations show confirmation/reason states and produce audit
  evidence

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
7. the first Administrator is a verified User promoted by a reviewed private
   server PostgreSQL transaction that revokes sessions and writes a
   `SYSTEM_BOOTSTRAP` audit event; v0.1 has no public role-management API
8. Trip History is visible for seven days, then retained indefinitely as a
   Content Archive; Account Closure deletes identity/session data and severs
   ownership from every retained terminal Trip Attempt without deleting its
   de-identified content; a non-terminal Trip Attempt blocks closure
9. all ordinary-User login, quota, own history, safe failure, PDF, and Account
   Closure interfaces belong to `travel-web`; `travel-admin` is operations-only
10. v0.2 is limited to Linux.do L1 Community Admission and growth/quality
    validation; later commercial and community capabilities are deferred

Remaining:

v0.1 decisions are resolved. The v0.2 Account Closure/re-registration
anti-abuse choice remains `[ASK USER]` in `docs/release-roadmap.md` and does not
block v0.1.
