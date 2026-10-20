# Meet staging browser qualification

The first native Argo adoption verified the twelve workload specs and readiness.
This separate Playwright test exercises the installed web application. It does
not enable promotion, request a Sync, select an image or run a migration.

## What the test checks

Two isolated Chromium contexts reuse two existing, distinct authenticated test
users. Each identity is verified through `/api/v1.0/users/me/` before joining an
existing canonical staging room. Both participants publish synthetic camera and
microphone input. The probe reads standard `RTCPeerConnection.getStats()` counters
without capturing SDP, network addresses, tokens or media payloads.

Both browsers must show exactly the two expected RTC participant identities in
the participant list. After joining or rejoining, each browser has up to fifteen
seconds to discover both identities before sampling starts. A missing user,
different identity or additional participant that persists cannot pass. The
same exact roster remains required around every media sample.

Each browser must have one incoming audio and video source. Both must show
progress in inbound and outbound audio and video. Video
requires decoded/encoded frames as well as bytes; received audio requires energy
as well as bytes. A static preview or a successful signaling connection cannot
pass. The second participant reloads, rejoins and repeats the media checks; both
then leave through the UI, using **Leave only** for an owner. No meeting is ended
or deleted by the test. Contexts are closed even on failure. Snapshot reads and cleanup have five-second
deadlines; the functional recipe has a three-minute deadline. Failed cleanup
cannot produce a passing result.

Reload/rejoin tests recovery after a browser reload. It does not prove recovery
from a network outage, room creation, perceptual audio quality, multi-network TURN reachability,
recording, ASR, subtitles or summaries. No mocks intercept the live application
when running `test:staging`. The local probe test uses synthetic WebRTC peers and
does not establish that staging works.

## Existing fixture required for a live run

The infrastructure owner must identify a dedicated, otherwise empty staging
meeting and two authorized test members. Obtain fresh Playwright `storageState`
files using their normal login flow. Do not use production, real customer rooms
or administrative API keys. The users must already be admitted to this room and
able to rejoin after reload. No new account, room, secret or access is created by
this preparation. The fixture owner retains responsibility for room cleanup and
session renewal.

Keep the following JSON and both state files private (`chmod 600`), outside Git
or under the ignored `src/frontend/staging/.private/` directory. Paths in the
fixture are relative to its directory. The RTC identities must be those assigned
to these test users by the existing room join flow, not guessed from display names. Replace the synthetic identifiers locally;
do not paste the files or their contents in a chat.

```json
{
  "schemaVersion": 1,
  "approvedForSyntheticMedia": true,
  "roomUrl": "https://meet.mastrao-staging.com/room_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "participants": [
    {
      "expectedUserId": "test-member-a",
      "expectedRtcIdentity": "rtc-member-a",
      "storageStatePath": "participant-a.json"
    },
    {
      "expectedUserId": "test-member-b",
      "expectedRtcIdentity": "rtc-member-b",
      "storageStatePath": "participant-b.json"
    }
  ]
}
```

From `src/frontend`, with the repository's supported Node runtime:

```sh
npm ci
npx playwright install chromium
MEET_STAGING_FIXTURE=/absolute/private/path/fixture.json npm run test:staging
```

The process prints one sanitized JSON result and exits nonzero on failure. Raw
Playwright exceptions, URLs, DOM text, identities, cookies, screenshots, traces,
video and storage states are not written into the report. Do not enable
`DEBUG=pw:*`, tracing or network logs on a live fixture. Successful output is
`MEET_TWO_PARTY_BROWSER_PASS`, not a release qualification.

An unconfirmed `LEAVE` result includes `departure`: the participant's position
(1 or 2), a fixed step (`CONTROL`, `TRANSITION`, `CONFIRMATION`, `FEEDBACK` or
`CONNECTIONS`), whether the voluntary-leave feedback route was reached, and the
last observed number of WebRTC connections in each standard state. A null count
means no departure snapshot was obtained. These fields do not contain identities,
URLs or raw errors. They locate the failing check without relaxing the exit gate;
successful media checks alone still cannot produce a passing qualification.
An empty connection list also fails: a new document without the previously
observed peers cannot establish that those peers were closed.

An unconfirmed `AUDIO_VIDEO` or `RELOAD_REJOIN` result also includes `media`.
It identifies participant-list opening, rejoining or sampling. A participant step
of `WAIT_FOR_PARTICIPANTS` means the initial exact roster did not converge before
its deadline; no media snapshot has been read. Each sampling
participant has a fixed step (roster before/after sample, snapshot, stream count,
or progress), the number of samples read, connection-state counts and counts of
the four audio/video directions. Roster reads expose only whether a list was
found, its row count and whether the expected set matched. `failed` marks a
rejected boundary; another participant may still have a read in flight.
`progressing` is the existing complete media
predicate for that participant; null means no pair was compared. These counts
do not expose identifiers, byte/frame/energy counters, URLs or raw errors and
do not replace the functional checks. A read failure can leave another
participant's work unfinished; the report is a snapshot of the failure boundary.

## Local checks and release correlation

```sh
npm run test:staging:contracts
npm run test:staging:probe
```

CI runs these credential-free checks; it does not run against staging. Before
qualifying a release, separately correlate the successful browser test time with
the exact deployed Argo revision and runtime image IDs, and ensure no selection
changed during the test. The browser result intentionally keeps
`releaseCorrelationProven` and `automaticMeetDeliveryProven` false. No custom
archive or new verifier service is introduced.

For a new backend candidate, compare its migration graph with the installed
graph before deployment. The graph observed in staging has core migrations
0001–0057 applied. Recheck that evidence for the actual selected candidate;
absence from an old installed graph is not proof of application. Do not rerun
these migrations merely to qualify the browser flow. A candidate adding schema
changes needs its reviewed migration and compatibility procedure before rollout.

References: [Playwright authenticated state](https://playwright.dev/docs/auth)
and [WebRTC statistics](https://developer.mozilla.org/en-US/docs/Web/API/RTCPeerConnection/getStats).
