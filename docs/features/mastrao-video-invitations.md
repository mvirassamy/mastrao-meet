# Personal notices for scheduled Mastrao meetings

The planned meeting form accepts an optional list of up to 50 emails. It normalizes case, deduplicates, supports removal and rejects the full submission if any address is invalid. Empty invitees preserve the immediate and scheduled creation flow. The existing canonical creation request includes `invitee_emails` under the same idempotency key; Platform/Core owns validation and recipient identity.

## Delivery boundary

The creation response contains recipient metadata only: invitation reference, email and delivery state. It contains no personal choice capability. After the host handoff is redeemed into the existing authenticated session, Meet verifies current host claims, obtains the existing guest share URL and signs a delivery claim with the existing receipt key.

Core alone owns the delivery journal. Only the first authorized claim contains the personal capability and permits one SMTP attempt. Meet sends the scheduled meeting time, guest link and personal yes/no link without an account through `InvitationService`. The SMTP result is acknowledged to Core. A lost acknowledgement replays that same acknowledgement; it never replays SMTP. A crash or uncertain send becomes `unknown` and cannot trigger an automatic resend.

This guarantees at most one SMTP attempt. `sent` means the SMTP service accepted the message, not that the recipient received it. A missing/unknown send supplies no proof of consultation and cannot imply no opposition or explicit acceptance. The completion dialog identifies unconfirmed sends.

The mail uses Platform's `/meet/video-choice?token=...` personal page, whose read operation is read-only. Core owns yes/no updates and the canonical decision lock. Its default is absence of opposition after a confirmed notice, never a fabricated explicit yes. Any canonical email refusal blocks the whole video capture.

## Personal guest entry

The normal guest URL keeps routing data and `video_choice` capability in its fragment. The guest page captures and clears the fragment before entry, then submits the capability through the existing guest-share redemption path. The capability stays in that guest's server session; the creator response never receives it.

After the exact guest admission is confirmed and while choices are open, Meet asks Core to bind that capability to the current signed participant grant and session digest. Core verifies the capability, admission, current ACL and meeting identity. A bound personal response can be used for the participant's video policy. An invalid capability produces no implicit agreement. Unbound participants require their explicit present yes when included in the host-click snapshot.

After the snapshot/start lock, later arrivals use the existing host information and recording indicator policy with no extra blocking collection. The boundary and native audio independence are described in [manual video capture](mastrao-manual-video.md).

## Configuration and coordinated rollout

Meet uses the existing SMTP, Platform facade, guest-share route and recording receipt signer. Configure the exact private Core URLs:

- `MASTRAO_CORE_VIDEO_DELIVERY_ENDPOINT`: `/internal/v1/meetings/video-invitations/delivery`
- `MASTRAO_CORE_VIDEO_PARTICIPANT_ENDPOINT`: `/internal/v1/meetings/video-invitations/participant`

This PR stacks on the manual video PR and requires the matching Platform/Core video contracts. Neither Meet draft is ready for an isolated rollout against the old Core schema. No calendar connector, RSVP, recurrence, delivery outbox in Meet or new transcription engine is introduced.

Tests capture mail with Django's local memory email backend. Real recipient delivery and staging changes are excluded from qualification. The PR lists the exact component, PostgreSQL/Redis and integrated HTTP evidence separately.

## Local HTTP qualification

`test_mastrao_video_http_qualification.py` consumes the private `MASTRAO_VIDEO_QUALIFICATION_CONFIG` produced by the existing Core native runtime fixture. It proves real signed delivery claim/ACK, one captured email and replay without resend, personal read/yes/no, admitted guest binding, refusal before provider invocation, and the temporary decision fence with HTTP 409 without mutation.

The Core HTTP server and its PostgreSQL ledger are real. The meeting, grants and ACL are precreated; host activation/effect setup uses explicit fixture SQL. SMTP uses Django locmem. Admission, SFU observations, provider and local adapter persistence are doubled; their separate tests do not constitute a complete Platform bootstrap or real media proof. Native notice wording and engine limitations remain those documented in `mastrao-manual-video.md`.
