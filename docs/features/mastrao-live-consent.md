# Live transcription consent bridge

This slice connects the browser's existing server-side host/guest session to
Core's live microphone consent ledger. It does not expose a product control or
change the current subtitle dispatch. It must not be used to claim that a
choice starts or stops the existing live provider path.

## Browser routes

Both routes accept JSON POSTs and require a CSRF cookie/header, including for
anonymous guests whose authority is held in their Django session. Responses,
including errors, use `Cache-Control: private, no-store`.

- `/api/v1.0/rooms/{room}/live-consent-offer/`: empty object. Returns Core's exact
  notice, immutable policy snapshot, current decision and consent reference.
  Core only offers this purpose for an explicit compatible live ASR mode.
- `/api/v1.0/rooms/{room}/live-consent-decision/`: `decision_ref`, `decision`
  (`accepted`, `refused`, `withdrawn`), `consent_ref`, `policy_ref`,
  `notice_version`, `notice_digest`, `retention_expires_at`. Send the displayed
  snapshot unchanged. Reuse the decision reference when retrying that exact
  choice. A conflicting reference or snapshot remains a Core conflict (409).

Browser requests cannot supply organization, meeting, room, participant,
session, grant or provider authority. Meet resolves these from the verified
compact grant and local room/session binding, signs a fresh 30-second assertion
and calls the existing bounded private Core HTTP transport. Unknown input
fields and unexpected Core response fields are rejected. Private upstream
diagnostics are never returned.

Only withdrawal may resolve an unexpired session grant after room closure.
Offers and new acceptance/refusal still require an open room. No choice or
bearer is copied into a new Meet table or cache. Core owns persistence,
permissions, retention, idempotence and the canonical decision.

## Configuration and deployment boundary

The following settings default to an empty value and fail closed:

- `MASTRAO_CORE_LIVE_CONSENT_OFFER_ENDPOINT`, exact private path
  `/internal/v1/meetings/transcripts/live/consent/offer`.
- `MASTRAO_CORE_LIVE_CONSENT_DECISION_ENDPOINT`, exact private path
  `/internal/v1/meetings/transcripts/live/consent/decision`.

The adapter uses the existing recording receipt signing trust and HTTP timeout.
No secret, signing identity, provider, image, Helm value or deployed environment
is changed. Core shared-schema migrations 035–037 and the corresponding Core
binary must exist before configuring these endpoints in an authorized test
environment. Disabling the endpoints does not erase existing Core decisions.

## Required next slice

`capture_authorized` always remains false. Before exposing the notice and
choice in the product, bind a live attempt grant to verified RTC observations,
the immutable mode and current Core consent. Enforce it before provider stream
creation, stop further audio and drain/close the stream on withdrawal, then
prove that no queued frame or reconnect escapes the stop barrier. Journal
admission is not provider permission. Notice approval and full timeline/roster
qualification remain required before mode activation and transcript reuse.

## Local evidence

The adapter tests verify exact binding, notice, snapshots, signed envelopes and
bounded transport failures. Browser integration tests use actual Django
sessions, PostgreSQL, Ed25519 signatures and a local synthetic Core HTTP peer;
they cover hosts, pending guests, exact retries, conflicts, CSRF, closure and
malformed responses. The peer simulates Core's response, not its real ledger or
RLS. Core's own PostgreSQL tests remain the evidence for those boundaries.
No provider, SFU, object storage, real meeting or deployed environment is used.
