# Release Roadmap

Status: **v0.1.1 Production Artifact Deployed / Source Integration Complete on Main / Owner Live UAT Accepted**

Current recovery gate: [v0.1.1 Source Integration Gate](v0.1.1-source-integration-gate.md).

Only v0.1, v0.1.1, and v0.2 are committed product work. Later ideas remain unplanned
candidates until live evidence justifies a new version.

## 1. Version Boundaries

```text
v0.1 controlled public beta
  -> invitation-gated email OTP
  -> hosted-product identity, quota, ownership, history, PDF, and operations
  -> small real-user acceptance

v0.1.1 unique Display Name
  -> every User receives one globally unique mutable product name
  -> self-service rename without changing identity, ownership, or authorization
  -> no additional user-profile or authentication capability

v0.2 Linux.do growth validation
  -> eligible Linux.do identities register without an Invitation
  -> promotion attribution, quality feedback, and unsupported-city demand
  -> evidence for the next product decision

v0.3+
  -> not committed
  -> selected from evidence rather than a fixed feature list
```

`D0/P0-P6` describe design and implementation gates. `v0.1/v0.1.1/v0.2` describe
shipped product capability. They are not interchangeable labels.

## 2. v0.1 — Controlled Public Beta

### User capability in `travel-web`

- invitation-gated email OTP registration
- returning email OTP login without another Invitation
- fixed seven-day server-side session
- three beta-lifetime successful generation credits
- authenticated trip submission and progress
- safe failure reasons with automatic credit release
- seven-day own Trip History
- owned result and PDF export
- fresh-OTP Account Closure

### Operator capability in `travel-admin`

`travel-admin` is never an ordinary-user portal. It is limited to the owner,
Administrators, and future operators, and uses only `/api/admin/*`.

Its v0.1 A0 scope is frozen:

- the database role enum remains `USER`/`ADMIN`; one configured immutable
  `app_user.id` receives the server-side `OWNER` product identity
- OWNER-only role grant/revoke, ADMIN-bounded User disable/restore, and
  immediate session revocation
- immutable signed quota adjustments and linked reversals
- HMAC-backed `YT-XXXX-XXXX` Invitation batches with OWNER-only encrypted
  plaintext recovery for batches created after migration `0011`
- permanent redacted Administrator audit
- full archive Trip/failed-draft/READY-Artifact read projections
- fixed Dashboard, Trip-generation report, and structured preference insights
- UUID idempotency on every Administrator write

P4 is implemented as P4.0-P4.5 serial checkpoints. Hermes P4.4-H1 now provides
the required versioned, service-authenticated internal-admin HTTP contract;
the BFF consumes it without a cross-database join.

### v0.1 exit evidence

- registration, login, logout, and session recovery work on desktop and mobile
- quota reservation/settlement concurrency invariants pass
- another User cannot access job, result, artifact, or history objects
- failure reasons are safe and failed attempts release credit exactly once
- PDF export is ownership-protected
- recent real generation attempts have no unexplained stuck jobs
- direct public Hermes bypass is closed before broad promotion

## 3. v0.1.1 — Unique Display Name

v0.1.1 adds exactly one user-facing product capability: every existing User has a
globally unique, mutable **Display Name**.

- registration assigns `user_` plus ten cryptographically random lowercase
  ASCII letters or digits without deriving it from email, provider identity, or
  the immutable User id
- existing Users without a Display Name receive a generated default
- an authenticated User may rename only their own Display Name
- normalized uniqueness is enforced transactionally in PostgreSQL
- the first manual rename is immediate; later renames use the frozen cooldown
- reserved official/system names cannot be claimed
- a former Display Name is quarantined from every User for 15 days after rename
  or Account Closure, then becomes claimable again
- Account Closure retains only a non-reversible normalized-name digest and
  quarantine expiry, with no User mapping
- a disabled User keeps their Display Name until restoration or Account Closure
- Administrator User search includes Display Name, but Administrator writes do
  not rename Users
- Account Closure preserves no Display Name-to-User ownership mapping

Display Name never becomes a Login Identity, login credential, authorization
key, ownership key, Linux.do account-linking signal, or Hermes identity field.
v0.1.1 adds no avatar, biography, public profile, `@mention`, public User search,
username login, device/session management, or unrelated BFF optimization.

## 4. v0.2 — Linux.do Growth Validation

### Linux.do admission

- use Linux.do OAuth2 authorization-code flow through the BFF
- use immutable Linux.do `id` as `provider_subject`
- a new Linux.do identity registers without an Invitation
- initial registration requires `trust_level >= 1`
- the minimum trust level is not re-applied to an existing User on later login
- `inactive` or `silenced` provider state prevents a new session
- one Linux.do identity links to at most one User
- one newly created User receives the same initial three beta credits
- returning login never grants the initial credits again
- never identify or merge a User by mutable username
- never auto-merge accounts by matching display name or email

### Explicit account linking

An authenticated email User may explicitly link an eligible, currently
unbound Linux.do identity. Linking is never inferred from matching profile
fields. A Linux.do identity already linked to another User returns a safe
conflict.

### `travel-web` additions

- “使用 Linux.do 登录” entry
- OAuth redirect/callback/loading/error flow
- explicit Linux.do identity-linking flow for an existing User
- registration/login return-to restoration
- simple “有帮助 / 没帮助” result feedback
- approved not-helpful reason taxonomy

### Growth and quality evidence

The BFF records privacy-minimized product events sufficient to explain:

- registrations by admission source
- registration-to-first-submit conversion
- submit-to-success conversion
- PDF export count
- users who exhaust all three beta credits
- safe failure categories
- unsupported-city demand
- repeat use within the observable beta window
- helpful/not-helpful result feedback

This is product observability, not third-party behavioral surveillance. Events
must not contain raw session tokens, OAuth tokens, full free-text trip requests,
or provider secrets.

### v0.2 non-goals

- Google OIDC
- fully public email registration
- password login
- WeChat login
- payments, one-time packs, or subscriptions
- public share pages or a public trip community
- followers, comments, or creator monetization
- complex Administrator RBAC
- city-data expansion inside the BFF

City coverage remains a `hermes-travel` data workstream. v0.2 only records
unsupported-city demand so that future city work is evidence-driven.

## 5. v0.2 Delivery Gates

### V2-D0 — Contract and UX Freeze

- frontend login/account/history/quota/failure/PDF/feedback flows accepted
- OAuth start/callback/link contracts accepted
- stable OAuth errors accepted
- Account Closure/re-registration policy resolved

### V2-P1 — BFF Identity Extension

- Linux.do OAuth integration and secret handling
- immutable identity uniqueness
- L1 admission transaction
- explicit link transaction
- initial-credit exactly-once tests
- replay, state, callback, provider-failure, and conflict tests

### V2-P2 — `travel-web` Integration

- Linux.do entry and callback experience
- account linking
- return-to restoration
- responsive and accessible error/loading states

### V2-P3 — Validation Instrumentation

- registration-source funnel
- unsupported-city demand
- PDF usage
- three-credit exhaustion
- result feedback
- Administrator read-only aggregate projection

### V2-P4 — Promotion Gate

- small live OAuth acceptance passes
- no duplicate User or duplicate initial grant
- no token/identity leakage
- generation reliability is acceptable for unfamiliar users
- supported-city boundary is visible before submission
- rollback can disable Linux.do admission without breaking email login

## 6. Deferred Candidate Backlog

These are not assigned to a release:

- Google OIDC and fully public registration
- one-time trip/credit packs
- subscription billing
- private share links
- permanent user-owned saved trips
- additional database/operational roles (`OPERATOR`, read-only roles); v0.1
  keeps only `USER`/`ADMIN` plus the server-side OWNER identity
- public trip discovery, likes, comments, and creator features

The next version after v0.2 is chosen from observed demand:

- high unsupported-city demand -> expand city data
- high satisfaction and credit exhaustion -> test one-time packs
- low satisfaction -> improve `hermes-travel` before monetization
- strong PDF/share behavior -> evaluate private share links
- strong non-Linux.do demand -> evaluate Google OIDC/public registration
- more operators -> introduce a narrow operational role model

## 7. Open v0.2 Decision

`[ASK USER]` After Account Closure deletes a Linux.do Login Identity, should the
same Linux.do identity be allowed to create a new User and receive three new
beta credits, or should the BFF retain an irreversible anti-abuse tombstone that
prevents a second initial grant?

This decision does not block v0.1 implementation. It must be resolved before
V2-P1.
