# Product Scope

Status: **v0.1 Implementation Complete / Acceptance Pending**

## 1. Problem

The hosted YunTu product currently lets the browser call `hermes-travel`
directly. The browser has only a tab-scoped random `conversation_id`, so the
system has no trustworthy user identity, trip ownership, generation quota, or
cross-device history.

This project adds the hosted-product layer without coupling product accounts
to the travel-generation engine.

## 2. v0.1 Goals

`travel-web-api` v0.1 must provide:

1. invitation-gated email OTP registration and passwordless login
2. opaque server-side sessions using secure cookies
3. authenticated trip submission
4. user ownership for job, SSE, result, and artifact access
5. transactional generation quota reservation and settlement
6. automatic quota release for failed, timed-out, or rejected generation
7. authenticated seven-day trip history including safe failure reasons
8. administrator APIs for users and role control, signed quota adjustments,
   short-code Invitation batches, permanent Trip/Artifact inspection,
   operational reports, and audit logs
9. an internal-only HTTP boundary to `hermes-travel`
10. observability sufficient to explain login, quota, ownership, and upstream
   failures without logging secrets or personal data
11. seven-day user-visible Trip History followed by permanent internal Content
    Archive retention
12. fresh-OTP Account Closure that deletes identity and preserves only
    de-identified trip content
13. ownership-protected PDF export for a successful trip result

## 3. Non-goals

v0.1 does not include:

- subscription billing or payment
- multiple membership tiers
- organization or team accounts
- social profiles, followers, or comments
- collaborative itinerary editing
- public share pages
- deletion of individual Trip Attempts while an account remains active
- password-based login, password reset, or password storage
- a separate administrator backend service
- a generic API gateway
- model/provider routing
- city data, route planning, or travel-content generation
- Redis as the quota or session source of truth
- migration of `travel-web` from Vite to Next.js

## 4. Ownership Matrix

| Capability | `travel-web` | `travel-admin` | `travel-web-api` | `hermes-travel` |
|---|---:|---:|---:|---:|
| Ordinary User login/account UI | Owns | No | Supports | No |
| Administrator login UI | No | Owns | Supports and authorizes | No |
| Email OTP verification | No | No | Owns | No |
| Session cookie | Uses | Uses | Owns | No |
| User profile display | Owns | Displays managed view | Owns API | No |
| Trip form | Owns | No | Validates boundary | Validates generation request |
| Beta generation credit | Displays | Applies signed adjustments | Owns transaction and ledger | No |
| Job ownership | No | Observes through API | Owns | Executes opaque job |
| Trip history | Displays | Observes through API | Owns | Stores generation record only |
| Safe failure records | Displays own seven-day view | Displays operational view | Owns | Produces upstream outcome |
| PDF export | Owns trigger/download UX | Read-only operational view | Authorizes/proxies | Renders artifact |
| Invitations | Uses at registration | Manages short-code batches | Owns HMAC records and redemption | No |
| Admin audit | No | Displays | Owns | No |
| Operational reports | No | Displays | Owns BFF aggregates | Supplies versioned job projection only |
| Failed Writer draft | No | Read-only diagnostic view | Authorizes/audits proxy | Owns retained diagnostic source |
| City/retrieval/route | No | No | No | Owns |
| Writer/Review/Publish | No | No | No | Owns |
| Provider pool | No | No | No | Owns |
| Production user data | No | Must not persist | Owns | Must not receive |

## 5. User Flows

### 5.1 Register

```text
Visitor opens YunTu
  -> enters a valid invitation
  -> enters an email address
  -> completes email OTP verification
  -> BFF creates app_user and consumes the invitation
  -> BFF creates an opaque server-side session
  -> browser receives HttpOnly Secure cookie
  -> GET /api/me returns the user and quota summary
```

### 5.2 Returning login

```text
Existing User enters the registered email without another invitation
  -> completes email OTP verification
  -> BFF reuses app_user
  -> BFF creates a new opaque seven-day session
```

### 5.3 Generate a trip

```text
Authenticated user submits form
  -> BFF validates session and request
  -> BFF transactionally reserves one quota unit
  -> BFF creates user_trip
  -> BFF submits an opaque request to hermes-travel
  -> BFF records hermes_job_id
  -> user observes only BFF-owned job/status/result endpoints
```

### 5.4 Settle quota

```text
hermes job SUCCESS
  -> reservation becomes CONSUMED
  -> result is visible in history

hermes job FAILED / TIMEOUT / REJECTED
  -> reservation becomes RELEASED exactly once
  -> failed attempt remains visible according to history policy
```

### 5.5 View history

```text
Authenticated user opens My Trips
  -> BFF returns only that user's trips
  -> selecting a trip verifies ownership again
  -> BFF returns or proxies the authorized result
```

### 5.6 Export PDF

```text
Authenticated owner opens a successful result
  -> travel-web requests the PDF artifact through the BFF
  -> BFF verifies Trip Attempt and result ownership
  -> hermes-travel renders or returns the cached PDF
  -> BFF exposes an authorized download
```

## 6. Accepted v0.1 Product Defaults

These defaults are accepted as the concrete v0.1 implementation target:

- Visitors may browse the public landing page but cannot generate a trip.
- Registration requires one unredeemed single-use invitation and verified
  email OTP.
- Email OTP is also the only v0.1 login method; v0.1 has no password or OAuth.
- Every newly registered user receives three beta generation credits.
- The three credits are lifetime for the beta policy and do not reset daily.
- A quota unit is reserved when an upstream job is accepted.
- One User may have at most one non-terminal Trip Attempt; refreshes restore the
  owned active trip, while a different concurrent submission is rejected.
- Successful jobs consume one unit.
- Failed, timed-out, and business-rejected jobs release the unit.
- User-facing history includes successful and failed attempts for seven days.
- After seven days, Trip Attempts leave Trip History and enter the permanent
  internal Content Archive; this is archival, not soft deletion.
- Failed attempts expose only a stable safe reason and remain retryable when
  the credit was released.
- Quota and administrator audit ledgers are not deleted with seven-day history.
- v0.1 exposes Account Closure but no per-trip deletion action.
- Account Closure deletes the User identity and sessions, severs ownership from
  every retained Trip Attempt, removes residual personal information, and
  preserves the de-identified trip content and quality telemetry indefinitely.
- Account Closure is rejected while that User has a non-terminal Trip Attempt;
  the User may retry after the attempt reaches a terminal state.
- Administrators use a separate `travel-admin` frontend that calls only
  authorized `/api/admin/*` BFF endpoints; it never connects to PostgreSQL or
  `hermes-travel` directly.
- The database role enum remains exactly `USER`/`ADMIN`. `OWNER` is a
  server-side product identity bound to one immutable configured `app_user.id`.
- `OWNER` may grant or revoke `ADMIN`, including on the configured Owner
  account; the last configured Owner identity remains protected. An `ADMIN`
  cannot grant/revoke roles and cannot disable or adjust quota for self,
  another `ADMIN`, or the configured `OWNER`.
- User disablement revokes all current sessions immediately. Restoration does
  not restore old sessions, role, quota, history, or already-running Trip
  Attempts.
- Every Administrator write requires UUID idempotency, a reason, an atomic
  transaction, and a redacted append-only audit record. v0.1 does not add
  recent re-authentication, MFA, or a second confirmation protocol.
- New Invitation batches contain 1-200 single-use `YT-XXXX-XXXX` codes,
  defaulting to 50 codes and 30 days, with a 1-90 day expiry. New codes are
  never permanent and raw values are disclosed only once.
- Administrator Trip inspection defaults to seven days but may filter the full
  permanent de-identified archive. Failed Writer drafts and READY Artifacts are
  diagnostic/read-only; neither may be published, regenerated, deleted, or
  turned into a second BFF truth source.
- Ordinary Users never use `travel-admin`; login, quota, own Trip History, safe
  failure records, PDF export, and Account Closure belong to `travel-web`.
- No personal login data is sent to `hermes-travel`.

## 7. D0 Product Decisions

All product choices required for D0 are resolved. No `[ASK USER]` item remains.

Accepted email delivery choice:

- Alibaba Cloud DirectMail API in the East China 1 region
- sending domain `notify.kakarot8.com`
- sender `no-reply@notify.kakarot8.com`
- no SMTP dependency

Accepted implementation stack:

- Python 3.12 and FastAPI
- SQLAlchemy 2 with asyncpg and Alembic
- PostgreSQL
- OpenAPI as the frontend contract and generated TypeScript-type boundary

Accepted session policy:

- every User and Administrator session has one fixed seven-day absolute expiry
- activity does not slide or extend the expiry
- there is no separate idle-expiry policy in v0.1
- logout, account disablement, identity changes, and Administrator demotion
  revoke affected sessions immediately

Accepted Administrator bootstrap and role policy:

- the first Administrator registers and verifies email as a normal User
- the server operator promotes that existing User with a controlled bootstrap
  transaction on the private server and configures that immutable
  `app_user.id` as the product `OWNER`
- the same transaction revokes existing sessions and records a
  `SYSTEM_BOOTSTRAP` Administrator audit event
- v0.1 exposes task-specific OWNER-only APIs to grant or revoke the `ADMIN`
  database role after normal authentication and current-permission checks
- the configured OWNER identity is derived only from `app_user.id`, never email
- OWNER may operate on every existing account, including self; ADMIN may manage
  only ordinary USER accounts according to the endpoint matrix
- the final configured OWNER identity cannot be removed or left without the
  privileges required to recover administration

Accepted retention policy:

- Trip History is user-visible for seven days
- expired Trip Attempts remain permanently in the internal Content Archive
- there is no per-trip deletion endpoint in v0.1
- Account Closure deletes identity and session data and irreversibly severs all
  retained trip content from that User
- de-identified trip content, outcome, failure category, and quality telemetry
  remain indefinitely as product data

## 8. travel-admin A0 Product Contract

The Administrator A0 contract is frozen for v0.1:

- `travel-admin` is an internal operations frontend and calls only
  `/api/admin/*`; Visitor requests receive `401` and ordinary USER requests
  receive `403`.
- The API contains only task-specific User, role, quota, Invitation, Trip,
  Artifact, report, and audit operations. It accepts no SQL, schema/table/field
  names, query fragments, or generic BI definitions.
- All lists are server-paginated with stable sorting and explicit filter
  allowlists. Responses include `request_id`; aggregate responses also include
  server-computed `as_of`.
- User lists mask email. Full-email reveal, failed-draft inspection, and
  Artifact download are separately authenticated, audited, and returned with
  `Cache-Control: no-store`.
- Quota changes use an immutable signed adjustment ledger. Negative adjustments
  are rejected atomically if the post-adjustment available balance would be
  below zero; corrections use a linked reverse entry.
- Raw Invitation codes use a keyed HMAC at rest, are never logged, and are
  disclosed only in the first successful batch-creation response. An
  idempotent retry returns the stored batch result without revealing raw codes
  again.
- Administrator reporting is limited to the frozen Dashboard, Trip generation,
  and structured User preference aggregates. Raw notes, email, prompts, Writer
  text, arbitrary SQL, word clouds, and user-level preference drill-down are
  excluded.
- Hermes remains the source for global job-step, failed-draft, structured
  result, and Artifact metadata. If the required versioned internal-admin HTTP
  contract is absent, P4.4 stops with a minimal contract proposal; the BFF
  never connects to the Hermes database.

## 9. v0.2 Product Boundary

v0.2 is a Linux.do promotion-validation release, not a general public launch.
It adds:

- Linux.do OAuth2 authorization-code login through the BFF
- Invitation-free registration for active, unsilenced Linux.do identities at
  L1 or higher
- immutable Linux.do ID uniqueness and one initial beta-credit grant
- L1 enforcement at first registration only
- explicit Linux.do linking for an existing email User without automatic merge
- `travel-web` OAuth/callback/linking UX
- source funnel, unsupported-city demand, PDF-use, credit-exhaustion, and simple
  result-feedback evidence

v0.2 explicitly excludes Google OIDC, fully public email registration, payment,
packs, subscriptions, WeChat, public sharing/community, and complex operator
roles. See `docs/release-roadmap.md`.
