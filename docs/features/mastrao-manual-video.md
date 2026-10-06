# Mastrao manual video capture

This describes canonical Mastrao rooms. The upstream recording/transcription documentation describes the separate legacy flow.

## Start and decisions

Joining, reconnecting and publishing a track never activate video. The host uses **Enregistrer**. Browser input contains only an activation request identifier. Meet reads the current SFU room and participant incarnation, correlates each human participant with the issued media token and admitted host/guest session, and signs the snapshot in the existing activation assertion.

Core saves the participants present at that host click. Choices remain editable while the start effect is queued. Immediately before the first provider Start, Meet supplies a fresh signed observation under the exact effect claim. Core revalidates current grants/ACL, the decisions of the participants from the host-click snapshot, and every canonical email refusal. A pending or refused result invokes no provider Start and permits a later explicit host request.

A successful authorization temporarily fences decisions while provider outcome is unresolved. A signed provider receipt establishes actual capture start and permanently freezes the choices. A change refused with HTTP 409 never changes the selected UI state optimistically. The UI rereads authoritative status after actions. A host stop permanently ends video for that meeting.

The snapshot fixes the consulted population. People arriving after it use the existing host information and recording indicator policy. There is no extra blocking collection, admission fence or token revocation for these arrivals. This is not a claim of atomic membership across Core's database and the SFU/provider APIs.

## Audio and transcription

The existing native audio notice remains the explicit yes/no authority for the participant's microphone track, with the same notice version/digest, retention, session/grant binding and current ACL. Video refusal, an undecided video choice and video stop do not suppress that notice or block native audio/transcription. The earlier combined video/transcription consent screen is removed. No replacement audio popup or synthetic acceptance is introduced.

Core removes the redundant legacy transcription-decision prerequisite only from the native track path. Legacy MP4 transcription endpoints and processing remain available. Attribution and transcription/brief engines are preserved. Native brief sources may have `recordingId: null`.

The existing native notice explicitly names transcription. It does not explicitly name the subsequent summary. This change does not expand its wording, purpose or digest and does not assert broader consent or legal compliance.

## Schema and rollout

Core and Meet must use the coordinated video contracts: recorded session status has a required `video` projection; activation assertions carry `observed_at` and server-produced `participants`; start effects carry `claim_id`; the existing recording receipt key signs the start-authorization roster. Older Core/Meet combinations are not supported by a compatibility fallback.

Migration `0052_mutable_video_decisions` removes only the uniqueness of the combination recording/participant/session/decision. The globally unique `decision_request_id` and assertion JTI, participant/session/grant evidence, recording foreign key and digest checks remain. A distinct request can therefore express yes → no → yes; replaying one request retains one local receipt.

An application-code rollback may retain the expanded schema. A schema downgrade restores the old uniqueness and must first verify that no repeated decision exists for a recording/participant/session. After mutable responses have been recorded, do not automatically reverse the migration or delete receipt history to force a downgrade. No staging migration is applied by this qualification work.

## Qualification

The draft PR records the exact tested head and distinguishes real PostgreSQL/Redis tests, SMTP captured by the test backend, browser component tests, and tests with provider/Core doubles. Integrated local HTTP qualification is required before readiness. No real recipient email or staging operation is part of these tests.

## Stable post-ASR integration

Video migration `0052_mutable_video_decisions` depends on `0051_normal_post_meeting_transcription`, which remains unchanged. The upgrade proof starts at normal migration `0051` and applies `0052` without downgrading or replaying the normal-ASR migration. PR102 and PR114 are open, unmerged sibling proposals and are not dependencies of this graph.

The normal sealed profile remains authoritative in the recording projection and activation response. Its existing provider disclosure is shown as supplementary UI information before Join and in the native audio notice; it does not modify the canonical notice text, purpose, version or digest or create a new consent contract. The removed combined recording/transcription gate is not restored. Normal submit v4, egress v2, ASR engines and speaker attribution are preserved.

### Integration conflict resolutions

- CHANGELOG: retain the stable normal-ASR entries and the video entries.
- Lobby: retain independent native audio decisions and manual video start; show sealed normal provider information before Join without a new gate.
- RecordingConsent.tsx and RecordingConsent.test.tsx: keep deletion of the superseded combined gate; carry the stable provider disclosure and its provenance checks into the current UI tests.
- Four room locale files: retain video labels and reuse the exact stable managed-provider wording under the current audio notice namespace.

The backend automatic merge retains sealed-profile validation/projection and exact managed activation notice binding alongside the required video schema. Stable profile fixtures now include that required video projection. The normal-ASR adapter, attempt, contract, pipeline, worker, speaker-attribution implementation and migration `0051` are unchanged relative to the stable develop.
