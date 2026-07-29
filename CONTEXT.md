# YunTu Hosted Product

This context defines the hosted product concepts owned around a YunTu user,
their access to trip generation, and the operator controls required for a
limited public beta.

## Language

**User**:
A person represented by one hosted-product account and one or more verified
**Login Identities**.
_Avoid_: Customer, member, account

**Login Identity**:
A verified external or first-party identity linked to exactly one **User**,
such as an email OTP identity or an immutable Linux.do identity.
_Avoid_: Username, session, browser token

**Registration Gate**:
The admission rule that allows a new **Login Identity** to create a **User**.
v0.1 uses an **Invitation**; v0.2 also accepts eligible
**Linux.do Community Admission**.
_Avoid_: Login method, quota, paywall

**Linux.do Community Admission**:
A v0.2 **Registration Gate** for an active, unsilenced Linux.do identity whose
trust level is at least L1. It is checked only when creating a new **User** and
does not require an **Invitation**.
_Avoid_: Public registration, shared invitation

**Administrator**:
A **User** authorized to operate the hosted product through audited management
actions. An Administrator does not bypass normal product APIs or edit the
database from the browser.
_Avoid_: Superuser, database admin

**Invitation**:
A single-use registration entitlement consumed by exactly one verified
**User** registration.
_Avoid_: Shared code, promo code, activation password

**Invitation Batch**:
An operator-generated group of single-use **Invitations** that share a source
label and optional expiry, such as the first V2EX promotion batch. A batch is
not itself redeemable.
_Avoid_: Campaign code, shared invitation

**Beta Generation Credit**:
One entitlement to a successful trip generation during the public beta. It is
reserved while generation is in progress, consumed only on success, and
released on failure, timeout, or business rejection.
_Avoid_: Token, daily quota, API call

**Trip Attempt**:
One idempotent request by a **User** to generate a trip, including successful
and failed outcomes.
_Avoid_: Conversation, message

**Trip History**:
The user-facing seven-day projection of a **User**'s own **Trip Attempts**,
including safe failure reasons. It is not the quota or administrative audit
ledger.
_Avoid_: Audit log, permanent archive

**Content Archive**:
The permanent internal, de-identified collection of expired or
ownership-erased **Trip Attempts** retained for quality audit and product
improvement. It contains no surviving association to a closed **User**.
_Avoid_: Trip History, soft-deleted trip

**Account Closure**:
Permanent removal of a **User**'s identity and active access. It severs and
de-identifies all retained **Trip Attempts** but does not delete their content.
Closure waits until the User has no non-terminal Trip Attempt; it never deletes
a Trip Attempt merely to make account deletion possible.
_Avoid_: Logout, suspension, trip deletion

## Example Dialogue

> **Operator:** This Invitation came from the V2EX campaign and has reached its
> activation limit.
>
> **Developer:** I will stop new registrations for that Invitation without
> changing any existing User or Beta Generation Credit.
>
> **Operator:** This User's Trip Attempt failed, so show the safe reason in Trip
> History and release the reserved Beta Generation Credit.
>
> **Developer:** The Administrator action will go through the management API
> and remain in the audit log.
>
> **Operator:** This User requested Account Closure.
>
> **Developer:** I will wait for any active Trip Attempt to finish, delete the
> identity and sessions, sever every retained Trip Attempt from the User, and
> preserve the de-identified Content Archive.
>
> **Operator:** This person is joining through Linux.do in v0.2.
>
> **Developer:** I will apply Linux.do Community Admission once, link the
> immutable Login Identity to one User, and grant the initial Beta Generation
> Credits without consuming an Invitation.
