# API Contract

Status: **v0.1 Documentation Accepted / Implementation Pending**

## 1. Conventions

- Public base path: `/api`
- Content type: `application/json`
- Authentication: opaque server-side session cookie
- Mutating requests: validated `Origin` and endpoint-appropriate replay or
  idempotency protection
- Timestamps: RFC 3339 UTC
- Public IDs: opaque UUIDs/ULIDs; database sequence IDs are not public IDs

Error shape:

```json
{
  "ok": false,
  "error": {
    "code": "STABLE_MACHINE_CODE",
    "message": "用户可读的安全说明",
    "retryable": false
  }
}
```

Do not return stack traces, upstream response bodies, provider tokens, or
internal database identifiers.

## 2. Authentication

### POST `/api/auth/email/send-code`

- request body:

```json
{
  "mode": "login",
  "email": "user@example.com",
  "invitation_code": null
}
```

- `mode` is exactly `login` or `register`
- `register` requires `invitation_code`; `login` must omit it
- registration mode requires a valid invitation before sending
- applies bot protection, resend cooldown, per-email/per-IP limits, and a
  global send circuit breaker
- returns the same public response regardless of account existence
- delegates outbound delivery to Alibaba Cloud DirectMail API; provider
  response bodies and identifiers are never exposed to the browser

Accepted response:

```json
{
  "ok": true,
  "challenge_id": "otp_opaque",
  "resend_after_seconds": 60
}
```

### POST `/api/auth/email/verify`

- request body contains only the opaque `challenge_id` and the submitted `code`;
  email, mode, purpose, and Invitation binding come from the server-side
  challenge
- verifies a single-use email OTP
- registration atomically creates the User, consumes the Invitation, grants
  three beta generation credits, and creates a session
- login creates a session for an existing active User
- account-existence correction is allowed only after the email OTP has been
  verified successfully:
  - verified `login` for an unregistered email returns
    `409 REGISTRATION_REQUIRED`
  - verified `register` for an existing identity returns
    `409 LOGIN_REQUIRED` without consuming the Invitation
- the send-code endpoint never returns `USER_NOT_FOUND` or
  `EMAIL_ALREADY_REGISTERED`
- raw OTP values never enter persistent storage or logs

### GET `/api/me`

Response:

```json
{
  "ok": true,
  "user": {
    "user_id": "usr_opaque",
    "display_name": "可选显示名",
    "masked_email": "u***@example.com"
  },
  "quota": {
    "policy": "beta_lifetime",
    "limit": 3,
    "reserved": 1,
    "consumed": 1,
    "remaining": 1,
    "resets_at": null
  },
  "active_trip": {
    "trip_id": "trip_opaque",
    "job_id": "hermes_job_opaque",
    "status": "RUNNING"
  }
}
```

`active_trip` is `null` when no Trip Attempt is `SUBMITTING`, `PENDING`, or
`RUNNING`. It lets a refreshed tab restore the one-active-trip state without
trusting browser storage.

Unauthenticated response: `401 AUTH_REQUIRED`.

### POST `/api/auth/logout`

- revokes the current server-side session
- clears the browser cookie
- is idempotent

Response:

```json
{"ok": true}
```

### POST `/api/me/closure/send-code`

- sends a fresh email OTP to the current verified identity
- applies the same abuse controls as login OTP delivery
- does not close the account by itself
- returns an opaque purpose-bound `challenge_id` and resend cooldown

### POST `/api/me/closure/confirm`

- request contains the opaque closure `challenge_id` and submitted `code`
- requires the fresh, single-use closure OTP
- returns `409 ACTIVE_TRIP_IN_PROGRESS` without changing the account while any
  owned Trip Attempt is `SUBMITTING`, `PENDING`, or `RUNNING`
- atomically revokes all sessions and closes the User account after every owned
  Trip Attempt is terminal
- removes identity mappings and severs ownership from all retained Trip Attempts
- preserves only the de-identified Content Archive projection
- returns no reusable authentication state

v0.1 has no endpoint for deleting one Trip Attempt while keeping the User.

## 3. Submit Trip

### POST `/api/trip/async`

This preserves the current frontend path while changing ownership from direct
Hermes access to the BFF.

Request:

```json
{
  "trip_request": {
    "to_city": "重庆",
    "days": 3,
    "people_count": 2,
    "preferences": ["美食", "citywalk"],
    "avoid": [],
    "notes": ""
  },
  "request_id": "web-<browser-generated-uuid>"
}
```

The BFF ignores browser-supplied `source`, `conversation_id`, and upstream
identity fields. It creates trusted opaque upstream values itself.

Successful response:

```json
{
  "ok": true,
  "trip_id": "trip_opaque",
  "job_id": "hermes_job_opaque",
  "status": "PENDING",
  "quota": {
    "state": "RESERVED",
    "remaining": 2
  }
}
```

Idempotency:

- `(user_id, request_id)` uniquely identifies one submission.
- Repeating the same request returns the existing trip/job and does not reserve
  another unit.
- Reusing the same `request_id` with different normalized input returns
  `409 REQUEST_ID_CONFLICT`.
- one User may have at most one `SUBMITTING`, `PENDING`, or `RUNNING` Trip
  Attempt; a different request while one is active returns
  `409 ACTIVE_TRIP_EXISTS` with the owned `active_trip` projection

Errors:

- `401 AUTH_REQUIRED`
- `409 ACTIVE_TRIP_EXISTS`
- `409 REQUEST_ID_CONFLICT`
- `422 VALIDATION_ERROR`
- `422 CITY_NOT_SUPPORTED`
- `429 QUOTA_EXHAUSTED`
- `503 GENERATION_SERVICE_UNAVAILABLE`

## 4. Job Status

### GET `/api/trip/jobs/{job_id}`

The BFF first resolves `job_id` to a `user_trip` owned by the current user,
then queries/reconciles the upstream job.

Response remains compatible with the existing frontend adapter:

```json
{
  "ok": true,
  "job_id": "hermes_job_opaque",
  "status": "RUNNING",
  "current_stage": "FINAL_WRITER",
  "result_record_id": null,
  "plan_count": null,
  "error_code": null,
  "error_message": null
}
```

Another user's or unknown job returns `404 TRIP_NOT_FOUND`.

### GET `/api/trip/jobs/{job_id}/stream`

- verifies ownership before opening the stream
- relays only the public upstream event projection
- settles quota on terminal events idempotently
- supports frontend fallback to polling

Business events remain `progress`, `complete`, and `failed`. A fourth
transport-only event, `interrupted`, tells the browser that the stream ended
without proving a business terminal state:

```text
event: interrupted
data: {"ok":false,"job_id":"hermes_job_opaque","stream_state":"INTERRUPTED","job_status_known":false,"fallback":"POLLING","error":{"code":"GENERATION_STREAM_INTERRUPTED","message":"状态流暂时中断，请改用轮询确认任务状态。","retryable":true}}
```

`interrupted` has these invariants:

- a Hermes read timeout/network error, or clean EOF before any `complete` or
  `failed` event, emits exactly one `interrupted`
- it contains no Trip Attempt `status`
- it never settles the local Trip Attempt or quota
- the local Trip Attempt remains `SUBMITTING`, `PENDING`, or `RUNNING`
- the reserved Beta Generation Credit remains `RESERVED`
- the browser closes the EventSource and falls back to authenticated
  `GET /api/trip/jobs/{job_id}` polling
- only a later authenticated status response or true upstream terminal SSE
  event may produce `SUCCESS`, `FAILED`, `TIMEOUT`, or `REJECTED`
- once a true `complete` or `failed` event has been processed, normal upstream
  EOF does not append `interrupted`

`GENERATION_STREAM_INTERRUPTED` is an SSE transport signal, not an HTTP
business-terminal error.

## 5. Result

### GET `/api/trip/results/{result_record_id}?job_id={job_id}`

The compatibility contract keeps the existing URL. The BFF verifies:

```text
current user owns user_trip
user_trip.hermes_job_id == job_id
user_trip.result_record_id == result_record_id
user_trip.status == SUCCESS
```

It then proxies the versioned public result projection from `hermes-travel`.
Another user's or mismatched result returns `404 TRIP_NOT_FOUND`.

The frontend must not treat possession of `job_id` as authorization.

## 6. Places and Artifacts

Initial compatibility paths:

```text
GET  /api/trip/places
GET  /api/trip/places/{place_id}
POST /api/trip/results/{result_id}/artifacts/{artifact_type}
GET  /api/trip/results/{result_id}/artifacts/{artifact_type}
GET  artifact download path returned by the BFF
```

Artifact creation/status/download requires result ownership. Place list/detail
may use authenticated product access without per-trip ownership.

Raw storage paths, upstream filenames, upstream `Content-Disposition`, and
internal upstream URLs must never be exposed.

The v0.1 artifact allowlist is exactly:

| `artifact_type` | download `Content-Type` | BFF filename |
|---|---|---|
| `pdf` | `application/pdf` | `trip-{result_id}.pdf` |
| `share_image` | `image/png` | `trip-{result_id}.png` |

All other artifact types return `422 ARTIFACT_TYPE_UNSUPPORTED` before an
upstream request. Downloads larger than 25 MiB or whose media type does not
exactly match the requested artifact type fail safely as
`502 GENERATION_SERVICE_ERROR`; the BFF never returns the raw upstream body.

`travel-web` owns the export interaction, the BFF owns result ownership,
authorization, safe projection, and download validation, and `hermes-travel`
remains the rendering/cache authority. The BFF does not create a second
artifact cache or generation queue.

For an owned result whose requested artifact has not been created, artifact
status returns:

```text
HTTP 404
{"ok":false,"error":{"code":"EXPORT_ARTIFACT_NOT_FOUND","message":"导出文件尚未创建。","retryable":false}}
```

The browser may then call the existing POST creation route. Only the upstream
`EXPORT_ARTIFACT_NOT_FOUND` code is admitted on artifact GET; unknown upstream
4xx responses and the same code on POST remain `502 GENERATION_SERVICE_ERROR`.

Artifact POST narrowly exposes:

- `429 EXPORT_RATE_LIMITED` with a safe public message
- `422 RESULT_CONTRACT_UNSUPPORTED`

Other upstream 4xx responses remain `502 GENERATION_SERVICE_ERROR`.

Each v0.1 User is already bounded by three successful Trip Attempts and may
access artifacts only for owned successful results. For the same
`result_record_id` plus `artifact_type` and unchanged source hash/version,
Hermes reuses its ready artifact instead of generating again. The BFF adds no
per-User image quota or database table.

The whole-service share-image accident fuse is a Hermes runtime setting, not a
BFF quota. The first 50-account beta target is **100 share images per day**
across the service; its runtime configuration is handled separately after BFF
acceptance.

## 7. User History

### GET `/api/me/trips`

Query parameters:

```text
cursor? opaque
limit? 1..50, default 20
status? SUCCESS | FAILED | TIMEOUT | REJECTED
```

Response:

```json
{
  "ok": true,
  "items": [
    {
      "trip_id": "trip_opaque",
      "job_id": "hermes_job_opaque",
      "status": "SUCCESS",
      "city": "重庆",
      "days": 3,
      "result_record_id": 1234,
      "created_at": "2026-07-28T10:00:00Z",
      "finished_at": "2026-07-28T10:01:20Z",
      "expires_from_history_at": "2026-08-04T10:00:00Z",
      "retry_input": {
        "trip_request": {
          "to_city": "重庆",
          "days": 3,
          "people_count": 2,
          "preferences": ["美食", "citywalk"],
          "avoid": [],
          "notes": ""
        }
      },
      "error": null
    }
  ],
  "next_cursor": null
}
```

The endpoint always derives `user_id` from the session; it never accepts a
user id path/query parameter.

User-facing items are limited to the latest seven days. Failed items include a
stable safe `error.code`, `error.message`, and `error.retryable`; they never
include stack traces or raw upstream/provider errors.

`retry_input` is the authenticated User's bounded structured input projection
for an explicit retry action. It never includes `request_id`, `source`,
`conversation_id`, prompts, provider payloads, or internal identity fields.
Free-text `notes` follows the same seven-day User visibility and Account Closure
erasure policy as the Trip Attempt.

Rows older than seven days are absent from this endpoint but remain available
to authorized internal Content Archive inspection.

## 8. Administrator API

All endpoints under `/api/admin/*` require an active Administrator session.
Non-administrator sessions receive `403 ADMIN_REQUIRED`.

Initial resource groups:

```text
GET  /api/admin/dashboard
GET  /api/admin/users
POST /api/admin/users/{user_id}/quota-grants
POST /api/admin/users/{user_id}/suspend
POST /api/admin/users/{user_id}/restore
GET  /api/admin/trips
GET  /api/admin/invitations
POST /api/admin/invitation-batches
POST /api/admin/invitations/{invitation_id}/disable
GET  /api/admin/audit-logs
```

Administrator mutations require a stable reason and an idempotency key. There
is no generic SQL endpoint and no endpoint that accepts a raw table name,
column name, or query fragment.

`POST /api/admin/invitation-batches` creates a bounded number of independently
redeemable, single-use Invitations with one source label and optional expiry.
It never creates a shared redeemable campaign code.

## 9. Stable Error Codes

| HTTP | Code | Meaning |
|---:|---|---|
| 400 | `BAD_REQUEST` | malformed request |
| 401 | `AUTH_REQUIRED` | no valid session |
| 401 | `SESSION_EXPIRED` | known but expired session |
| 403 | `ADMIN_REQUIRED` | active session lacks administrator role |
| 404 | `TRIP_NOT_FOUND` | unknown or not owned |
| 409 | `REGISTRATION_REQUIRED` | verified email is not registered for login mode |
| 409 | `LOGIN_REQUIRED` | verified email already belongs to a User |
| 409 | `ACTIVE_TRIP_IN_PROGRESS` | account closure must wait for a non-terminal trip |
| 409 | `ACTIVE_TRIP_EXISTS` | another non-terminal Trip Attempt already belongs to the User |
| 409 | `REQUEST_ID_CONFLICT` | idempotency key reused with different input |
| 422 | `VALIDATION_ERROR` | valid JSON but invalid fields |
| 422 | `CITY_NOT_SUPPORTED` | requested city is outside the current supported set |
| 429 | `QUOTA_EXHAUSTED` | no generation units available |
| 502 | `GENERATION_SERVICE_ERROR` | invalid/unexpected upstream response |
| 503 | `GENERATION_SERVICE_UNAVAILABLE` | upstream unavailable before acceptance |
| 504 | `GENERATION_STATUS_TIMEOUT` | BFF could not obtain upstream status |

Upstream business rejection codes may be projected only through an explicit
allowlist with safe user messages.

## 10. v0.2 Linux.do OAuth Extension

Status: **Contract Draft / Frontend D0 Repair Pending**

Planned same-origin routes:

```text
GET /api/auth/oauth/linux-do/start
GET /api/auth/oauth/linux-do/callback
GET /api/me/identities
GET /api/me/identities/linux-do/link/start
```

Rules:

- the BFF creates and verifies high-entropy OAuth `state`
- the callback exchanges the authorization code server-side
- provider client secret and access token never enter frontend JavaScript
- the Linux.do provider redirects to the BFF callback, never directly to
  `travel-web`
- frontend JavaScript never receives or exchanges the provider authorization
  `code` or OAuth `state`
- a new active, unsilenced Linux.do identity must have `trust_level >= 1`
- eligible Linux.do registration does not require an Invitation
- immutable provider `id` is the identity subject
- a new User receives the initial three beta credits in the registration
  transaction
- returning login never creates another initial grant
- L1 is not rechecked as a minimum for a previously linked User
- an inactive or silenced provider identity cannot create a new Session
- link-start requires an authenticated User and records link intent server-side
- an already-bound identity returns a safe conflict
- no username/email/display-name based automatic merge
- callback completion sets or rotates the normal seven-day opaque Session
  cookie and returns `303` to the frontend `/auth/callback` landing page
- success uses
  `/auth/callback?outcome=success&mode=login|link&return_to=<relative-path>`
- failure uses `/auth/callback?outcome=error&error=<stable-code>`
- the frontend landing URL never contains a
  provider token, authorization code, raw profile, or OAuth state
- the frontend callback page bootstraps through `GET /api/me` after success,
  then restores the allowlisted `return_to`
- login completion defaults to `/`; explicit-link completion defaults to
  `/profile`

Minimal identity projection:

```json
{
  "ok": true,
  "items": [
    {
      "provider": "email_otp",
      "status": "VERIFIED",
      "display": "u***@example.com"
    },
    {
      "provider": "linux_do",
      "status": "LINKED",
      "display": "Linux.do"
    }
  ]
}
```

The identity projection does not expose the immutable provider subject. Avatar
storage/display is not part of the accepted v0.2 contract; adding it requires a
separate privacy, caching, and fallback decision.

Planned stable errors:

| HTTP | Code | Meaning |
|---:|---|---|
| 400 | `OAUTH_STATE_INVALID` | state missing, expired, mismatched, or replayed |
| 403 | `OAUTH_ACCOUNT_INELIGIBLE` | new registration does not meet community admission |
| 409 | `IDENTITY_ALREADY_LINKED` | provider identity belongs to another User |
| 502 | `OAUTH_PROVIDER_ERROR` | provider returned an invalid response |
| 503 | `OAUTH_PROVIDER_UNAVAILABLE` | provider could not be reached |

Provider callback failures are converted to the same stable codes before the
frontend landing page is reached. `OAUTH_STATE_INVALID` is a client/auth-flow
error, not a `502` provider error.

### POST `/api/trip/results/{result_id}/feedback`

- requires an authenticated owner of a successful Trip Attempt/result
- another User's or unknown result returns `404 TRIP_NOT_FOUND`
- one User/Trip Attempt has one current feedback record; a later submission
  replaces the previous selection idempotently
- `helpful=true` requires an empty `reasons` array
- `helpful=false` requires one or more allowlisted reason codes:
  - `ROUTE_INEFFICIENT`
  - `PACE_MISMATCH`
  - `TRANSIT_INACCURATE`
  - `PREFERENCE_MISSED`
  - `OTHER`
- `OTHER` is a structured category in v0.2 and does not accept free text

Request:

```json
{
  "helpful": false,
  "reasons": ["PACE_MISMATCH", "PREFERENCE_MISSED"]
}
```

Exact callback/loading/error presentation and the reason-code labels are owned
by the separate `travel-web` frontend D0. The transport and security contract
above is owned by the BFF.
