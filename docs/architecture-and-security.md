# Architecture and Security

Status: **v0.1.1 Architecture Contract Accepted / Source Integration Pushed / Main Merge Pending**

Repository state and recovery stop rules: [v0.1.1 Source Integration Gate](v0.1.1-source-integration-gate.md).

## 1. Target Topology

```text
Internet
  -> Nginx
       -> kakarot8.com/*
            -> travel-web static files
       -> admin.kakarot8.com/api/*
            -> travel-web-api:6670
       -> admin.kakarot8.com/*
            -> travel-admin static files
       -> kakarot8.com/api/*
            -> travel-web-api:6670
                 -> PostgreSQL / dedicated travel_web database
                 -> private HTTP -> hermes-travel:6666
```

The browser must not call `hermes-travel` directly after cutover.
`api.kakarot8.com`, if retained, must also terminate at the BFF or be restricted
to trusted internal clients. CORS is not an authentication boundary.

## 2. Runtime Responsibilities

### Nginx

- TLS termination
- same-origin `/api` routing for both the user and Administrator hosts
- static frontend delivery
- request-size and basic rate limits
- denial of direct internal-service access

### travel-web-api

- authentication integration
- invitation validation and email OTP verification
- v0.2 Linux.do OAuth callback, admission, and explicit identity linking
- session issuance and revocation
- user and administrator role authorization
- `OWNER` product-identity projection from one configured immutable
  `app_user.id` while the database enum remains `USER`/`ADMIN`
- CSRF/origin enforcement for cookie-authenticated mutations
- user ownership checks
- quota transactions
- trip/history persistence
- upstream request shaping
- upstream error normalization
- SSE authorization and relay
- administrator action auditing
- Administrator UUID idempotency, signed quota adjustments, and stable
  allowlisted operational reporting

### hermes-travel

- city gate and internal knowledge retrieval
- route/time/food/accommodation planning
- Writer, Review, Publish Gate
- generation job execution and result records

## 3. Trust Boundaries

```text
Untrusted:
  browser input
  URL job/result identifiers
  cookies before validation
  forwarded client headers

Trusted only after verification:
  BFF server-side session
  BFF database transaction state
  internal service credential

Internal but fallible:
  hermes-travel responses
  PostgreSQL availability
email delivery provider responses
```

Opaque identifiers are not authorization. Every read or mutation must check
the authenticated user's ownership in the BFF database.

## 4. Authentication and Session Contract

The v0.1 identity tuple is:

```text
provider = "email_otp"
provider_subject = normalized verified email
verified_email
```

The BFF maps that tuple to one `app_user`.

v0.2 extends the identity tuple without changing the server-side Session
contract:

```text
provider = "linux_do"
provider_subject = immutable Linux.do id
verified_email = null
```

One `app_user` may have more than one verified identity, but each
`(provider, provider_subject)` belongs to exactly one User. The BFF never
auto-merges Users by matching email, username, display name, or avatar.

v0.1.1 assigns every existing User one globally unique mutable Display Name.
The normalized uniqueness key is presentation-only and never replaces
`app_user.id`, `public_id`, or `(provider, provider_subject)`. Registration,
rename, disable/restore, and Account Closure preserve this separation.

For a new Linux.do User, the callback verifies `active=true`,
`silenced=false`, and `trust_level >= 1`. The trust-level minimum is an initial
registration gate only; an existing linked User may later log in at L0, while
inactive or silenced provider state still prevents creation of a new Session.
Linux.do registration does not consume an Invitation.

Email OTP requirements:

- one unredeemed single-use invitation is checked before an OTP send is
  accepted for registration
- OTP responses do not reveal whether an email is already registered
- store only an OTP hash, never the raw code
- enforce expiry, single use, bounded verification attempts, resend cooldown,
  per-email and per-IP limits, and a global send circuit breaker
- email delivery success does not itself create a User or consume an Invitation
- the Invitation is consumed only in the verified registration transaction
- Invitations are never shared or redeemable more than once

Email delivery uses Alibaba Cloud DirectMail API in East China 1 with the
verified `notify.kakarot8.com` sending domain and
`no-reply@notify.kakarot8.com` sender. DirectMail is an outbound delivery
dependency only; OTP generation, hashing, expiry, verification, and abuse
controls remain owned by the BFF.

Session requirements:

- generate at least 256 bits of cryptographically secure randomness
- send the raw token only in an `HttpOnly`, `Secure` cookie
- store only a one-way hash of the token
- use host-only `SameSite=Lax` cookies for each same-origin deployment
- rotate the session on login and privilege-sensitive changes
- support explicit logout and server-side revocation
- use one fixed seven-day absolute expiry for both User and Administrator
  sessions; activity never extends it
- v0.1 has no separate idle-expiry timer
- never expose the session token to frontend JavaScript

JWT in browser storage is explicitly out of scope.

## 5. Mutation Protection

Cookie-authenticated POST/PUT/PATCH/DELETE requests must:

- require JSON content type where applicable
- validate `Origin` against the explicit canonical user/admin origin allowlist
- reject missing or unexpected origins in production
- use a CSRF token if a future flow cannot rely on strict same-origin requests
- enforce endpoint-specific idempotency

Every `/api/admin/*` mutation additionally requires a client UUID
`idempotency_key`, scoped by `(actor_user_id, idempotency_key)`. Authentication
and current capability checks happen before replay lookup. Same key plus the
same canonical request returns the original result; same key plus a different
request returns `409 IDEMPOTENCY_CONFLICT`. Concurrent duplicates may commit
one business change only. Successful deduplication facts are retained
permanently; validation or authorization failures before execution do not
consume the key.

## 6. Upstream Identity and Privacy

The BFF sends only opaque, generation-relevant identifiers upstream:

```text
source = "web"
request_id = stable opaque id derived for this user submission
conversation_id = "web-user:<opaque key>"
```

It must not send:

- email
- phone number
- display name
- identity-provider subject
- session token
- membership or billing data

`hermes-travel` responses are treated as untrusted service data and validated
before being returned or persisted.

OAuth authorization codes, access tokens, provider client secrets, and raw
provider responses are also BFF-only data. `travel-web` owns login buttons and
callback UX, but the provider redirects to the BFF callback and frontend
JavaScript never receives the provider authorization code, OAuth state, client
secret, access token, or raw profile.

## 7. Account Closure and Content Archive

Account Closure is distinct from logout and disablement:

- logout revokes one session
- disablement blocks access while retaining the User relationship
- Account Closure deletes identities and sessions and prevents future login
- Trip Attempts remain as de-identified Content Archive records
- owner references and other reversible identity mappings are removed
- the current Display Name enters a no-owner 15-day keyed-digest quarantine;
  plaintext and Display Name-to-User mappings are not retained
- free-text fields are removed or redacted when they may retain personal data
- generated trip content, structured non-identifying input, terminal outcome,
  stable failure category, and quality telemetry remain indefinitely

The browser has no direct per-trip deletion endpoint in v0.1. Account Closure
requires fresh email OTP verification and is an auditable transactional
workflow. It returns `409 ACTIVE_TRIP_IN_PROGRESS` while any owned Trip Attempt
is `SUBMITTING`, `PENDING`, or `RUNNING`; after every attempt is terminal, the
workflow removes ownership from all retained Trip Attempts, including items
that are still within the seven-day user-visible window.

## 8. Endpoint Authorization Matrix

| Endpoint group | Visitor | Authenticated owner | Other authenticated user | Administrator |
|---|---:|---:|---:|---:|
| Invitation check / email OTP | Allowed with rate limits | Allowed | Allowed | Allowed |
| Linux.do OAuth start/callback (v0.2) | Allowed with admission controls | Allowed for explicit linking | N/A | Same User rule |
| `GET /api/me` | 401 | Allowed | N/A |
| `PATCH /api/me/profile` (v0.1.1) | 401 | Allowed for own Display Name | Cannot target another User | Same User rule |
| Submit trip | 401 | Allowed if credit permits | N/A | Same user rule |
| Job status/SSE | 401 | Allowed | 404 | Admin projection only |
| Trip result | 401 | Allowed | 404 | Admin projection only |
| Create/download artifact | 401 | Allowed | 404 | Admin projection only |
| Place list/detail | Authenticated only | Allowed | Allowed | Allowed |
| Own history | 401 | Allowed | Cannot request another user | Uses admin API |
| `/api/admin/*` | 401 | 403 | 403 | Allowed by ADMIN/OWNER capability |

Use 404 rather than 403 for another user's object to avoid confirming its
existence.

Administrator endpoints must be task-specific. There is no generic SQL,
arbitrary table, or unrestricted proxy endpoint. Every mutation records the
Administrator, target, action, stable reason, timestamp, and correlation id.

The first Administrator is a normally registered, email-verified User promoted
by a controlled bootstrap transaction executed by the server operator on the
private server. The transaction updates the existing User rather than
inserting a parallel identity, revokes existing sessions, writes a
`SYSTEM_BOOTSTRAP` audit event, and installs that immutable `app_user.id` as
the configured OWNER identity. Email never determines OWNER.

OWNER and ADMIN are capability projections, not a new RBAC system. OWNER may
grant/revoke the `ADMIN` database role and may operate on any existing account,
including self. ADMIN cannot grant/revoke roles and cannot disable or adjust
quota for self, another ADMIN, or OWNER. The final configured OWNER identity is
protected. Disablement revokes existing sessions immediately; restoration
never restores old sessions. No v0.1 action requires recent re-authentication,
MFA, or a second confirmation protocol, but explicit reason, idempotency,
authorization, atomicity, and audit remain mandatory.

`travel-admin` is not an ordinary-User surface and is never linked from the
User account area as a profile/history destination. Ordinary User login, quota,
own history, safe failure records, PDF export, and Account Closure are rendered
by `travel-web`. Knowing the Administrator hostname is not authorization;
non-Administrator sessions receive `403 ADMIN_REQUIRED`.

## 9. Service-to-Service Protection

Defense in depth:

1. `hermes-travel` is reachable only on the private container network.
2. Public Nginx routes terminate at `travel-web-api`.
3. BFF-to-Hermes calls carry a rotatable internal credential.
4. Hermes validates the credential on BFF-owned public generation endpoints.
5. The Administrator projection for global jobs, steps, failed Writer drafts,
   structured results, and Artifacts uses the accepted Hermes P4.4-H1
   `/internal/v1/admin/*` service-authenticated HTTP allowlist with a credential
   separate from ordinary generation calls.
6. The BFF validates the v1 envelope and request-id binding, and never reads
   Hermes PostgreSQL/MySQL directly.
7. Internal crawl/city administration retains a separate administrator
   credential and is never proxied by the BFF.

The internal credential design is an explicitly scoped integration change and
must not become end-user authentication inside `hermes-travel`.

## 10. Data and Network Isolation

The BFF may share the existing PostgreSQL server but must use:

- a dedicated `travel_web` database
- a dedicated database role
- no privileges on `hermes-travel` tables
- no cross-database joins or foreign keys

Before P0 deployment, verify firewall and container bindings for PostgreSQL,
Redis, MySQL, and internal APIs. User/session data must not be introduced while
database ports are reachable from the public internet.

## 11. Logging and Observability

Allowed structured fields:

- request correlation id
- opaque user id
- public trip id
- opaque Hermes job id
- endpoint, status, latency
- quota transition and stable reason code
- upstream status/error category
- Administrator action code, redacted target type/id, result/error code,
  request id, and irreversible source-IP digest

Never log:

- raw cookies or session tokens
- authorization headers
- OTP codes
- email or phone by default
- identity-provider tokens
- full free-text trip notes at info level
- raw Invitation codes, full email reveals, failed Writer drafts, prompts,
  artifact bytes/storage paths, SQL, or unredacted stack traces

`admin_audit_log` is permanent and append-only. It records the actor's
then-current OWNER/ADMIN product identity, action, redacted target and
before/after projection, result/error code, reason, idempotency key, request
id, server timestamp, irreversible source-IP digest, and bounded client
metadata. Full-email reveal, failed-draft inspection, full Invitation lookup,
and Artifact download are audited reads and return `Cache-Control: no-store`.

Required metrics:

- login success/failure by provider and stable reason
- active/revoked/expired sessions
- quota reserve/consume/release counts
- duplicate/idempotent submission count
- ownership denials
- upstream success/failure/timeout latency
- orphaned reservation reconciliation count

## 12. Failure and Degradation

- Identity provider unavailable: existing valid sessions continue; new login
  returns a safe retryable error.
- PostgreSQL unavailable: fail closed for login, ownership, and quota.
- Hermes unavailable before job acceptance: release reservation.
- Hermes accepted job but BFF response failed: idempotent retry recovers the
  same `user_trip`; never create a second charge.
- SSE unavailable, read-timeout/network interrupted, or cleanly ended before a
  terminal event: emit exactly one non-terminal `interrupted` transport event
  and let the frontend fall back to authenticated polling. Do not emit a
  business `failed` event, change the local Trip Attempt to a terminal status,
  or settle the reserved credit without a true Hermes terminal result. Once a
  true `complete` or `failed` event has been processed, do not append
  `interrupted`.
- Reconciliation uncertainty: keep reservation and mark for retry; do not
  blindly release while an upstream job may still succeed.
