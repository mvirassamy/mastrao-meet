# Durable meeting entry (PR2a)

Contract: Platform PR #201 at commit
`4c3668237e3a4cddb42ec2f5c45083f15cf1ac95`,
`docs/architecture/meet-durable-link-contract.md`.

## Routes and configuration

The existing host share endpoint now calls Platform's `share-link` operation
with an idempotency key saved before the request. It validates the exact response
and formats `/guest#organization=<organization>&share=<192-bit-locator>`.
The fragment is removed before entry; it never reaches HTTP access logs or
Referer headers. Both frontend Nginx configurations return private/no-store and
no-referrer headers for `/guest`. Existing invitation fragments remain supported.

Configure `MASTRAO_CORE_GUEST_SHARE_REDEMPTION_ENDPOINT` to the private Core
origin plus `/internal/v1/meetings/guest-share-links/redeem`. Use the existing
receipt signer and Core grant verifier. This is an endpoint, not a feature flag.

`/host/<canonical-room-slug>` provides authenticated recovery into the same
meeting. The bookmark is an untrusted locator. Meet forwards the current OIDC
bearer to Platform, which checks selected organization and current ACL. It
consumes the fresh handoff server side without replacing the OIDC user.

Guest attempts retain a browser redemption ID and a server nonce before Core
mutation. Transport retries use fresh assertions and the same semantic identity.
Host attempts retain both the Platform command key and the Core redemption ID.
An expired/refused host attempt allows the user to retry with a new key. Exact
grant retries preserve original issuance and expiry dates. Guests still wait
for confirmed host approval before receiving media authority.

## Local evidence and remaining qualification

The focused backend tests use real Ed25519 signatures, Redis sessions and
PostgreSQL grant projections. They simulate a lost Core response and resume
from a separate request/session reader using only browser cookies. They check
immutable grant dates, exact organization/provider/credential bindings,
expired-grant refusal, OIDC preservation and opaque upstream denial.
Remote Platform/Core transport is stubbed in these tests; these are not a
qualification of deployed tenant ACL or of Core's durable storage.

The share bridge accepts the original dates even when older than four hours.
Frontend tests check exact same-origin URLs, fragment removal, preserved
redemption identity on reopening, and legacy invitation support. Browser
inspection checks that opening the guest page neither posts an authority
exchange nor sends the locator in an HTTP URL.

Before lifecycle enforcement (PR3), qualify against local PR1 Core/Platform:

1. Copy/reopen a stored link after the original invitation exceeds four hours;
   verify original link dates and bounded new lobby authority.
2. Recover host authority in that exact meeting with current OIDC membership;
   refuse another tenant, removed ACL, revoked invitation and closed meeting.
3. Interrupt responses after committed exchanges and retry after a process
   restart; inspect Core receipts and Meet grants for unchanged dates.
4. Qualify PR2b's real authenticated LiveKit human join producer, including a
   host alone, an approved guest, exclusions, conflicts and durable redelivery.
5. Check access logs, telemetry and HTTP referrers without exposing credentials.

This change does not introduce meeting expiry or change the creation-time
`started_at` field. PR3 remains gated on the combined qualification above.
