# Verified human RTC admissions (PR2b)

Contract: Platform #201, commit
`4c3668237e3a4cddb42ec2f5c45083f15cf1ac95`,
`docs/architecture/meet-durable-link-contract.md`.

This change produces the verified first-entry facts. It adds no expiry policy,
frontend, link route, recording or transcription behavior. Core owns the accepted
append-only facts and their minimum `observed_at` projection.

## Trust and persistence

Meet verifies the exact Core `media_host` or `media_guest` compact authority at
issuance and stores it in the existing `MastraoMediaTokenBinding` transaction
before returning the RTC token. The token lifetime remains bounded by both the
local grant and the Core media authority. Authority never goes into RTC metadata,
a URL or the webhook inbox. The marker is an opaque lookup hint; it grants nothing.

The existing LiveKit `WebhookReceiver` authenticates the signature and exact body
hash before the inbox commits an event. Only `participant_joined` with LiveKit
`STANDARD` kind, a correlated identity/room/token connection, valid issuance time
and the exact persisted authority can produce a signal. The verified body kind
is checked explicitly because the SDK can erase unknown enum values into its
STANDARD default. A host alone counts.
Guest confirmation must precede the provider join. Mint, room opening, lobby,
track, leave, egress, ingress, SIP and agent events do not produce a signal.

The producer reuses the existing Meet room receipt signer and posts the strict
contract envelope to Core. Provider IDs and body/authority digests remain stable;
only delivery `jti`, `issued_at` and `expires_at` are refreshed. Historical authority
is verified at the provider event time, so expiry at delivery does not discard a
valid earlier entry. Local room closure is not re-opened by delivery; Core decides
whether the fact preceded its canonical closure.

Core acknowledgement, HTTP refusal status and attempt count are retained in the
inbox. Core refusal leaves the native capture pipeline intact. A completed
acknowledgement is not delivered again. A response-loss retry presents the same
facts to Core's idempotent boundary. Correlation conflicts, including reused
tokens on different connections, remain quarantined by the existing correlation.
A normal leave/rejoin obtains a new bound token.

## Required operator configuration

Apply Meet migration `0051_verified_rtc_admission` and Core migration
`056-meeting-share-links-and-rtc-admissions.sql` before enabling this producer in
the coordinated delivery. Configure the existing room effect trust and receipt
signer, plus this explicit endpoint in the backend runtime:

```sh
MASTRAO_CORE_RTC_ADMISSION_ENDPOINT=http://cabinet-core:8080/internal/v1/meetings/rtc-admissions
```

Use the actual private Core port for the environment. The endpoint uses the
existing allowlisted, bounded HTTP transport: no redirects or proxy inheritance.
It is intentionally absent from `BASE_CONTRACT_SETTINGS`; missing configuration
retains eligible facts with HTTP status `503`, rather than disabling the existing
Mastrao integration. Include it explicitly in the delivery configuration.

After a Core outage or a Meet restart, run:

```sh
python manage.py retry_mastrao_rtc_admissions --limit 100
```

The limit counts attempted qualified facts, not unqualified inbox rows. This
command also retries retained Core refusals; it does not mint new authority or
change any fact or deadline. Delivery holds the existing room lock for a bounded
five-second HTTP call. There is no new worker framework or periodic scheduler.

The stored authority is sensitive: retain the existing private database access
controls and exclude `participant_authority` and `observation_assertion` from
telemetry. No raw webhook body or RTC token is retained. Pre-migration rows have
unknown participant kind/no persisted authority and are not retroactively
qualified. They cannot be reconstructed safely from metadata.

## Local evidence and remaining gate

Ordinary tests use real Core/Meet Ed25519 signatures, the actual SDK
`WebhookReceiver`, HTTP envelopes and PostgreSQL. Their provider events and Core
HTTP response peer are synthetic. Coverage includes host/guest, wrong bindings,
service exclusion, unsigned/tampered input, duplicate/conflicting/delayed facts,
Core authz refusals, response loss, fresh database reads and a new-process retry.
Existing native capture/admission tests remain regression gates.

`MEET_RTC_QUALIFICATION=local` runs a separate test with an actual local
`livekit-server` and the optional Python `livekit` RTC SDK (neither is added as an
application dependency). The server sends its own webhook bytes/authorization;
host and guest connect with tokens returned by production Meet issuance, an agent
is excluded, and a fresh-token leave/rejoin preserves first start. This still uses
a synthetic Core HTTP response peer and signed fixture Core authorities.

`MEET_CORE_CONTRACT_ROOT=/path/to/platform-checkout` separately runs the actual
Core `MeetingStartSecurity` verifier against the produced host/guest assertions.
It first checks that verifier/schema/error sources are unchanged from the pinned
Platform commit. It reuses that checkout's installed `tsx`; no dependency copy or
build is needed. This proves JOSE/schema compatibility, not Core ledger/ACL state.

**PR3 remains blocked on qualification joining the real Core ledger/current ACLs
and committed authorities to the real SFU flow**, including tenant/ACL revocation
and closure, lost response/restart, and the durable-link/host-recovery cases from
PR2a. No staging mutation or deployment is part of this PR.
