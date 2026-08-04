# Database and Quota

Status: **v0.1.1 Database Contract Accepted / Production Migration `0009` / Source Integration Complete on Main**

Repository state and recovery stop rules: [v0.1.1 Source Integration Gate](v0.1.1-source-integration-gate.md).

## 1. Database Boundary

Use the existing PostgreSQL server with logical isolation:

```text
PostgreSQL server
  travel_agent database
    owned by hermes-travel role

  travel_web database
    owned by travel-web-api role
```

`travel-web-api` receives no privileges on the `travel_agent` database.
References to Hermes jobs/results are opaque values, not foreign keys.

All schema changes use Alembic. Production migrations are separate,
reviewable deployment steps.

## 2. Core Tables

### 2.1 `app_user`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | internal stable user id |
| `public_id` | TEXT UNIQUE | opaque API identifier |
| `status` | TEXT | `ACTIVE`, `DISABLED` |
| `role` | TEXT | `USER`, `ADMIN` |
| `display_name` | TEXT NOT NULL | v0.1.1 globally unique mutable product name |
| `display_name_normalized` | TEXT UNIQUE NOT NULL | NFKC-normalized, case-folded uniqueness key |
| `display_name_changed_at` | TIMESTAMPTZ NULL | null until the first manual rename |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

v0.1.1 backfills every existing null, invalid, or reserved `display_name` with
`user_` plus ten cryptographically random lowercase ASCII letters or digits
before setting the columns `NOT NULL`. Registration generates the same kind of
default in the existing User-creation transaction. A valid existing name is
preserved when its normalized key is unique; for a pre-existing normalized
collision, the earliest `(created_at, id)` User keeps the name and every later
User receives a generated default.
The application may pre-check availability for a friendly error, but the
PostgreSQL unique constraint on `display_name_normalized` is the final authority
for concurrent registration and rename attempts.

`display_name` is presentation data. User identity, authorization, ownership,
Administrator audit targets, and future Login Identity linking continue to use
the immutable internal/public User ids.

### 2.1.1 `display_name_quarantine`

| Column | Type | Notes |
|---|---|---|
| `name_digest` | BYTEA PK | keyed digest of the normalized former name |
| `expires_at` | TIMESTAMPTZ | exactly 15 days after rename or Account Closure |
| `created_at` | TIMESTAMPTZ | quarantine creation time |

The quarantine stores no plaintext Display Name, User id, identity reference,
reason, or ownership mapping. Availability checks derive the same purpose-bound
keyed digest and treat an unexpired row as unavailable. Expired rows do not
block a claim and may be deleted by bounded maintenance cleanup.

Renaming is one transaction that locks the User, re-checks the seven-day manual
rename cooldown, rejects reserved or quarantined names, inserts the former-name
digest with a 15-day expiry, and updates the Display Name. A request that sets
the already stored presentation value is a successful no-op and does not start
a cooldown or quarantine entry. A presentation-only change with the same
normalized key counts as a rename but does not quarantine that still-owned key.

A disabled User retains all three Display Name fields. Disable/restore therefore
cannot transfer a name to another User.

### 2.2 `user_identity`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK | `app_user.id` |
| `provider` | TEXT | v0.1: `email_otp`; v0.2: `linux_do` |
| `provider_subject` | TEXT | immutable provider subject |
| `verified_email` | TEXT NULL | normalized email for email identity |
| `created_at` | TIMESTAMPTZ | |
| `last_login_at` | TIMESTAMPTZ NULL | |

Unique constraint: `(provider, provider_subject)`.

One User may own multiple identities. One identity may never belong to multiple
Users. v0.2 uses immutable Linux.do `id`, not username, as
`provider_subject`; linking is explicit and never inferred from profile fields.

v0.2 adds a non-identifying `app_user.registration_source` projection and a
minimal admission audit containing the accepted provider and registration-time
trust level. OAuth access tokens and raw provider profiles are not persisted.
The exact Account Closure anti-abuse tombstone policy remains `[ASK USER]` in
`docs/release-roadmap.md` and does not block v0.1.

### 2.3 `email_otp_challenge`

Stores a hashed, expiring, single-use email verification challenge with
bounded attempt and send counters. Raw codes are never persisted.

### 2.4 `invitation`

Stores one single-use Invitation code with a keyed HMAC digest, nullable batch
reference for legacy compatibility, per-batch sequence number, source,
required expiry for new short codes, disabled state, redeemed timestamp, and
timestamps. It has no multi-redemption counter. New codes use exact
`YT-XXXX-XXXX` syntax and never store plaintext or reversible ciphertext.
Existing redeemed legacy rows remain valid and keep their registration audit
history.

### 2.5 `invitation_redemption`

Links exactly one verified User registration to one Invitation redemption.
The User creation, redemption, initial beta-credit grant, and first session
creation share one database transaction.

Unique constraints on both `invitation_id` and `user_id` enforce one
Invitation per registration and prevent reuse.

### 2.6 `user_session`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK | |
| `token_hash` | BYTEA UNIQUE | never store raw cookie token |
| `created_at` | TIMESTAMPTZ | |
| `last_seen_at` | TIMESTAMPTZ | bounded observability updates; not sliding expiry |
| `expires_at` | TIMESTAMPTZ | fixed `created_at + 7 days` |
| `revoked_at` | TIMESTAMPTZ NULL | logout/administrative revoke |
| `revoke_reason` | TEXT NULL | stable reason code |

Indexes:

- `(user_id, expires_at)`
- `(expires_at)` for cleanup

### 2.7 `user_trip`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | internal id |
| `public_id` | TEXT UNIQUE | user-facing trip id |
| `user_id` | UUID NULL FK | owner while account exists; `ON DELETE SET NULL` |
| `client_request_id` | TEXT | browser idempotency key |
| `request_hash` | TEXT | normalized request conflict check |
| `request_json` | JSONB | product request snapshot |
| `city` | TEXT | history projection |
| `days` | SMALLINT | history projection |
| `status` | TEXT | state below |
| `hermes_job_id` | TEXT UNIQUE NULL | opaque upstream id |
| `result_record_id` | BIGINT NULL | opaque upstream reference |
| `quota_entry_id` | UUID UNIQUE | one quota lifecycle per trip |
| `error_code` | TEXT NULL | safe stable code |
| `error_message` | TEXT NULL | safe user message |
| `telemetry_json` | JSONB | non-identifying stage, latency, schema, and quality projection |
| `created_at` | TIMESTAMPTZ | |
| `started_at` | TIMESTAMPTZ NULL | |
| `finished_at` | TIMESTAMPTZ NULL | |
| `updated_at` | TIMESTAMPTZ | |
| `visible_until` | TIMESTAMPTZ | `created_at + 7 days` |
| `archived_at` | TIMESTAMPTZ NULL | internal archive transition |
| `identity_erased_at` | TIMESTAMPTZ NULL | owner association removed |

Constraints:

- unique `(user_id, client_request_id)`
- partial unique `user_id` while `status IN ('SUBMITTING', 'PENDING',
  'RUNNING')`, enforcing at most one active Trip Attempt per User

Status:

```text
SUBMITTING
PENDING
RUNNING
SUCCESS
FAILED
TIMEOUT
REJECTED
```

### 2.8 `trip_quota_entry`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK | |
| `trip_id` | UUID UNIQUE FK | one lifecycle per trip |
| `period_type` | TEXT | `BETA_LIFETIME` initially |
| `period_key` | TEXT | stable beta policy identifier |
| `units` | SMALLINT | v0.1 always `1` |
| `status` | TEXT | `RESERVED`, `CONSUMED`, `RELEASED` |
| `reserve_reason` | TEXT | stable reason |
| `settle_reason` | TEXT NULL | `SUCCESS`, `FAILED`, etc. |
| `created_at` | TIMESTAMPTZ | reserve time |
| `settled_at` | TIMESTAMPTZ NULL | |
| `updated_at` | TIMESTAMPTZ | |

Indexes:

- `(user_id, period_type, period_key, status)`
- `(status, created_at)` for reconciliation

The row is an auditable quota lifecycle. Status may transition only through
the rules below; arbitrary deletion is forbidden.

### 2.9 `quota_grant`

Stores initial and Administrator-issued generation credits. Every
Administrator grant records the actor, target User, units, stable reason, and
idempotency key.

Existing rows remain the positive baseline source. P4 does not rewrite or
recalculate them and does not change existing User balances.

### 2.10 `invitation_batch`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | internal id |
| `public_id` | TEXT UNIQUE | opaque `batch_id` |
| `name` | TEXT | bounded operator label |
| `source_label` | TEXT | registration attribution |
| `code_count` | SMALLINT | 1-200 |
| `valid_days` | SMALLINT | 1-90 |
| `expires_at` | TIMESTAMPTZ | required for new codes |
| `disabled_at` | TIMESTAMPTZ NULL | irreversible whole-batch disable |
| `created_by_user_id` | UUID NULL FK | de-identified on Account Closure |
| `plaintext_recoverable` | BOOLEAN | false for pre-0011 history; true only when every code has authenticated ciphertext |
| `created_at` | TIMESTAMPTZ | |

Raw codes never enter `invitation_batch`. `invitation.encrypted_secret` stores
an optional versioned AES-GCM envelope for OWNER-only recovery while
`invitation.secret_hash` remains the authoritative redemption lookup. A unique
`(batch_id, sequence_number)` supplies stable `#001` projections. Existing
rows keep `encrypted_secret=NULL`; HMAC digest uniqueness rejects collisions
and generation retries with a new cryptographically secure value.

### 2.11 `admin_idempotency`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `actor_user_id` | UUID NULL FK | current actor; null after identity erasure |
| `actor_scope_id` | TEXT | irreversible per-actor scope digest |
| `idempotency_key` | UUID | client key |
| `request_hash` | BYTEA | canonical method/path/body hash |
| `state` | TEXT | `IN_PROGRESS`, `SUCCEEDED` |
| `http_status` | SMALLINT NULL | first successful result |
| `response_json` | JSONB NULL | redacted replay-safe response |
| `created_at` | TIMESTAMPTZ | |
| `completed_at` | TIMESTAMPTZ NULL | |

Unique `(actor_scope_id, idempotency_key)` provides the permanent dedupe fact
without retaining a reversible identity after Account Closure. Authorization
is always evaluated before reading a replay result. Failed validation or
authorization does not create a row.

### 2.12 `quota_adjustment`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | internal id |
| `public_id` | TEXT UNIQUE | `adjustment_id` |
| `target_user_id` | UUID NULL FK | target while identity exists |
| `actor_user_id` | UUID NULL FK | actor while identity exists |
| `delta` | INTEGER | non-zero signed units |
| `balance_before` | INTEGER | available balance before |
| `balance_after` | INTEGER | available balance after, never negative |
| `reason` | TEXT | allowlisted stable reason |
| `note` | TEXT NULL | bounded and redacted |
| `idempotency_id` | UUID UNIQUE FK | one successful write |
| `reverses_adjustment_id` | UUID UNIQUE NULL FK | linked original |
| `created_at` | TIMESTAMPTZ | |

Rows are append-only. Reversal inserts the exact opposite delta and links the
original once; neither row is modified or deleted. Balance calculation, row
insert, idempotency success, and audit success share one transaction and lock
the target User. ACTIVE and DISABLED Users are eligible. Missing, closed, or
de-identified targets are rejected.

### 2.13 `admin_audit_log`

Permanent append-only record of Administrator reads and writes. It stores audit
id; nullable actor reference and then-current OWNER/ADMIN product identity;
action; redacted target type/id; result/error code; redacted before/after;
reason; idempotency key; request id; server timestamp; irreversible source-IP
digest; and bounded client metadata.

The initial Administrator promotion uses a reserved system actor and
`SYSTEM_BOOTSTRAP` action in the same transaction that changes
`app_user.role` and revokes the User's existing sessions.

Audit rows cannot be updated or deleted. They never contain raw Invitation
codes, full emails, prompts, Writer/failed-draft text, authentication tokens,
Artifact bytes or storage paths, SQL, stack traces, or raw provider responses.

### 2.14 Administrator Trip/Artifact source boundary

The BFF retains local `user_trip` ownership/archive data and opaque
`hermes_job_id`/`result_record_id` only. Global job steps, failed Writer drafts,
structured result diagnostics, and Artifact metadata/binary remain Hermes
sources accessed through a versioned, service-authenticated internal-admin HTTP
contract. No BFF migration may add a cross-database foreign key, direct Hermes
table mapping, duplicate rendering/cache table, or generic proxy.

## 3. Quota Source of Truth

For one user and period:

```text
used_units = SUM(units WHERE trip_quota_entry.status IN ('RESERVED', 'CONSUMED'))
adjusted_limit = SUM(quota_grant.units) + SUM(quota_adjustment.delta)
remaining = adjusted_limit - used_units
```

`RELEASED` entries do not consume remaining quota but remain for audit.

Registration creates an initial grant of three units for the beta policy.
Additional Administrator changes use the audited immutable signed adjustment
ledger. Negative adjustments lock the User and are rejected atomically if
`remaining + delta < 0`; they are never clamped. Legacy positive grants remain
part of `adjusted_limit`.

## 4. Reservation Transaction

One PostgreSQL transaction must:

1. acquire a per-user/per-period lock
2. look up `(user_id, client_request_id)`
3. return the existing trip if the request is an idempotent retry
4. reject a conflicting request hash
5. compute `RESERVED + CONSUMED` units
6. reject if the limit would be exceeded
7. create `user_trip(status=SUBMITTING)`
8. create `trip_quota_entry(status=RESERVED)`
9. commit

The upstream Hermes call happens after this transaction. Network I/O must not
hold the database lock.

If Hermes rejects the submission before returning a job id, a second
transaction marks the trip terminal and releases the entry.

## 5. Settlement State Machine

```text
RESERVED
  -> CONSUMED  when upstream job reaches SUCCESS
  -> RELEASED  when upstream job reaches FAILED
  -> RELEASED  when upstream job reaches TIMEOUT
  -> RELEASED  when upstream job reaches REJECTED

CONSUMED
  -> terminal in v0.1

RELEASED
  -> terminal in v0.1
```

Settlement update condition:

```sql
WHERE status = 'RESERVED'
```

This makes repeated polling, duplicate SSE terminal events, and reconciliation
safe. A transition affecting zero rows must be treated as already-settled or
an invariant violation after re-reading the row.

## 6. Submission Failure Windows

### Before upstream acceptance

- mark `user_trip=FAILED`
- release quota
- return retryable service error

### Upstream accepted but response to browser failed

- idempotent browser retry reads the existing `user_trip`
- if `hermes_job_id` is present, return it
- if upstream outcome is uncertain, reconciliation investigates before
  releasing or resubmitting

### BFF crash between upstream acceptance and saving `hermes_job_id`

The upstream `request_id` must be stable for the user submission. Reconciliation
or retry resubmits the same idempotency key and recovers the same upstream job,
then saves it. It must not reserve a second quota unit.

### SSE or status transport interruption

An SSE disconnect, read timeout, invalid stream frame, clean EOF without a
terminal event, or transient status-query failure is not a Hermes business
terminal result. The BFF keeps the current `user_trip` activity state and
`trip_quota_entry=RESERVED`. It tells the browser exactly once to fall back to
polling, but it must not synthesize `FAILED`, `TIMEOUT`, or `REJECTED`. A later
true Hermes terminal result performs the normal
exactly-once settlement.

## 7. Reconciliation

A bounded background reconciler may process:

- `SUBMITTING` trips older than the submit SLA
- `PENDING/RUNNING` trips with stale local status
- `RESERVED` entries whose trip is terminal or inconsistent

Rules:

- use row locking with skip-locked semantics
- never release solely because a local timer elapsed
- check the upstream idempotent job/status when acceptance is possible
- cap retry count and record stable error categories
- expose unresolved invariants to operations

This is a narrow state reconciler, not a general distributed scheduler.

## 8. Retention, Archival, and Account Closure

User-facing Trip History contains only Trip Attempts whose `visible_until` is
in the future. At expiry, the row is marked `archived_at`; it is not soft
deleted or physically deleted.

The Content Archive retains indefinitely:

- structured non-identifying trip input
- generated content reference/projection
- terminal status and stable failure category
- latency, stage, and quality telemetry

Account Closure is a transactional workflow that:

1. verifies a fresh closure OTP
2. locks the User and rejects closure if any owned `user_trip` is
   `SUBMITTING`, `PENDING`, or `RUNNING`
3. revokes and deletes all sessions
4. deletes `user_identity` and other direct identifiers
5. sets every retained `user_trip.user_id = NULL` and `identity_erased_at`,
   including terminal rows that are still within the seven-day visibility
   window
6. removes or redacts personal information from free-text request fields
7. deletes or irreversibly de-identifies invitation, quota, and audit
   references that would reconnect the archive to the User
8. inserts only the current normalized Display Name's keyed digest into
   `display_name_quarantine` with a 15-day expiry and no User mapping
9. physically deletes the `app_user` row after dependent identity cleanup

Account Closure does not call `hermes-travel` to delete generated content.
`hermes-travel` content is retained under its own audit policy and must not
retain BFF email or identity data.

Expired/revoked session and OTP-challenge cleanup windows remain technical
configuration, but neither may be used to reconstruct a closed User identity.

## 9. Database Acceptance

P2 cannot pass without PostgreSQL integration evidence for:

1. two concurrent last-unit reservations: exactly one succeeds
2. duplicate same request: one trip, one quota entry
3. duplicate conflicting request: 409, no additional quota entry
4. repeated SUCCESS settlement: one consumption
5. repeated failure settlement: one release
6. success/failure race: one legal terminal result, no double transition
7. another user cannot read or settle the trip
8. closure with a non-terminal trip returns a conflict and changes no state
9. closure of a User with terminal trips deletes identity while retaining
   de-identified trip content with no reversible owner mapping
10. migration upgrade and downgrade behavior is documented and tested
11. concurrent signed quota adjustments and duplicate UUID idempotency commit
    one legal balance transition
12. a deduction below zero is rejected without a ledger, idempotency-success,
    or partial balance change
13. one reversal creates one exact opposite row; concurrent repeats cannot
    reverse twice
14. short-code batch creation stores HMAC digests only, discloses raw codes
    once, and concurrent duplicate creation produces one batch
15. Account Closure nulls or irreversibly de-identifies Administrator
    idempotency, adjustment, Invitation, and audit actor/target references so
    retained rows cannot reconstruct the closed identity
16. concurrent Display Name claims produce exactly one owner and a stable
    unavailable result for every loser
17. case/full-width variants collide on `display_name_normalized`
18. rename and Account Closure each quarantine the former-name digest for
    exactly 15 days without retaining plaintext or a User mapping
19. disabled Users retain their Display Name through restoration
