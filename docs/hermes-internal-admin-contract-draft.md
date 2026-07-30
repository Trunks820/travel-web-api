# Hermes Internal Admin HTTP Contract

Status: **Hermes P4.4-H1 Internal Admin Contract Accepted**

The historical filename is retained for traceability. Hermes P4.4-H1 now
provides the accepted versioned global internal-admin contract required by
travel-admin. The BFF consumes only this HTTP boundary and never replaces it
with direct Hermes database access.

Minimum service-authenticated routes:

```text
GET /internal/v1/admin/trip-jobs
GET /internal/v1/admin/trip-jobs/{job_id}
GET /internal/v1/admin/trip-jobs/{job_id}/failed-draft
GET /internal/v1/admin/artifacts
GET /internal/v1/admin/artifacts/{artifact_id}
GET /internal/v1/admin/artifacts/{artifact_id}/download
```

Requirements:

- Require `X-Internal-Credential` with the dedicated rotatable
  `HERMES_BFF_INTERNAL_ADMIN_CREDENTIAL`; never accept the ordinary generation
  credential, browser sessions, or public traffic.
- Paginate and sort stably. Allowlist time, city, status, result type,
  error code, detailed reason, and READY/EXPIRED Artifact filters.
- Job detail returns opaque job/result ids, status, current stage, safe error
  and detailed reason, stage/total durations, retry count, result type, and
  diagnostic-draft availability.
- Failed draft exists only after Writer produced content and a later terminal
  failure. Return a redacted unpublished diagnostic projection without system
  prompt, secret, raw provider request/response, or unredacted stack.
- Artifact types are exactly `pdf` and `share_image`. List/detail return opaque
  ids, result linkage, status, media type, byte size, created/ready/expiry
  timestamps, and download availability without storage paths.
- Download returns bytes only for READY, non-expired files with strict media
  type and size headers. `ARTIFACT_EXPIRED`, `ARTIFACT_FILE_MISSING`, and
  `ARTIFACT_NOT_READY` remain distinct stable business states.
- All responses include contract version and upstream request id. Errors use
  stable codes and never include SQL, stack traces, provider payloads, or paths.
- These routes are read-only. They provide no publish, force-success,
  generation, retry, delete, restore, or arbitrary query operation.

Hermes-side isolated PostgreSQL acceptance is recorded in the owning Hermes
slice. This BFF document does not authorize another `hermes-travel` edit or
deployment.
