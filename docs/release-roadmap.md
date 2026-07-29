# Release Roadmap

Status: **v0.1 Accepted / v0.2 Product Freeze In Progress**

Only v0.1 and v0.2 are committed product work. Later ideas remain unplanned
candidates until live evidence justifies a new version.

## 1. Version Boundaries

```text
v0.1 controlled public beta
  -> invitation-gated email OTP
  -> hosted-product identity, quota, ownership, history, PDF, and operations
  -> small real-user acceptance

v0.2 Linux.do growth validation
  -> eligible Linux.do identities register without an Invitation
  -> promotion attribution, quality feedback, and unsupported-city demand
  -> evidence for the next product decision

v0.3+
  -> not committed
  -> selected from evidence rather than a fixed feature list
```

`D0/P0-P6` describe design and implementation gates. `v0.1/v0.2` describe
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

Its v0.1 product scope is frozen separately by the Administrator A0 interview.

### v0.1 exit evidence

- registration, login, logout, and session recovery work on desktop and mobile
- quota reservation/settlement concurrency invariants pass
- another User cannot access job, result, artifact, or history objects
- failure reasons are safe and failed attempts release credit exactly once
- PDF export is ownership-protected
- recent real generation attempts have no unexplained stuck jobs
- direct public Hermes bypass is closed before broad promotion

## 3. v0.2 — Linux.do Growth Validation

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

## 4. v0.2 Delivery Gates

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

## 5. Deferred Candidate Backlog

These are not assigned to a release:

- Google OIDC and fully public registration
- one-time trip/credit packs
- subscription billing
- private share links
- permanent user-owned saved trips
- multi-role operations (`OWNER`, `OPERATOR`, read-only roles)
- public trip discovery, likes, comments, and creator features

The next version after v0.2 is chosen from observed demand:

- high unsupported-city demand -> expand city data
- high satisfaction and credit exhaustion -> test one-time packs
- low satisfaction -> improve `hermes-travel` before monetization
- strong PDF/share behavior -> evaluate private share links
- strong non-Linux.do demand -> evaluate Google OIDC/public registration
- more operators -> introduce a narrow operational role model

## 6. Open v0.2 Decision

`[ASK USER]` After Account Closure deletes a Linux.do Login Identity, should the
same Linux.do identity be allowed to create a new User and receive three new
beta credits, or should the BFF retain an irreversible anti-abuse tombstone that
prevents a second initial grant?

This decision does not block v0.1 implementation. It must be resolved before
V2-P1.
