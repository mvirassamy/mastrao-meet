# Independent browser verification — meeting personal notes

Verified 2026-10-16, approximately 10:13–10:23 UTC, against the running worktree `/root/.codex/worktrees/meeting-personal-notes/mastrao-meet`.

**PASS for the UI scenarios exercised below. No confirmed application failure remained in those scenarios. Full production end-to-end acceptance is NOT PROVEN by this run.** Real Core authorization and persistence require separate backend evidence. No application or fixture backend files were edited by this verifier; this report is the only repository file created.

## Environment and exact URLs

- UI preview: `http://127.0.0.1:5197`, reused from Portly.
- Live room: `http://127.0.0.1:5197/room_0123456789abcdef0123456789abcdef`.
- Feedback after leaving: `http://127.0.0.1:5197/feedback?outcome=left&room_id=room_0123456789abcdef0123456789abcdef`.
- History detail: `http://127.0.0.1:5197/reunions/historique/meeting_personal_notes_demo`.
- History list: `http://127.0.0.1:5197/reunions/historique`.
- UI contract fixture API: `http://127.0.0.1:8099`, implemented by the existing `/tmp/meeting-notes-verification/api.py`.
- Real local LiveKit connection: `ws://127.0.0.1:7880`; joined with the UI's Rejoindre button. Camera and microphone disabled through `lk-user-choices` before room reload/join.
- All browser interaction used `dev-browser --headless --browser notes-independent-verifier`. The second fixture author used the separate browser `notes-independent-verifier-author2`.
- Cookies: `mastrao_lang=fr`, primary `verification_author=verifier`, second browser `verification_author=browser2author`.

**The API is a mock contract fixture, not real Core.** Its notes map is keyed by a controllable author cookie, and room/history endpoints share that map for the demo. Author separation and cross-surface read-back below establish only UI/fixture behavior. They do not establish secure authorization, real meeting identity mapping, database persistence, or production room/history association.

## Acceptance results

Evidence filenames below are relative to `/root/.local/share/code-os/files/skills-verify/meeting-personal-notes-independent-20261016/`. The first section above gives the exact URL for each named surface.

| Criterion | Result | Observed behavior and evidence |
| --- | --- | --- |
| Live notes alongside existing Messages and Transcription | PASS | The joined live room exposed all three tabs. Notes had the privacy sentence, labelled textbox, character limit and save status. `independent-03-retained-saved.png`. The transcription tab showed an existing fixture-related startup error; successful transcription service operation was not tested. |
| Draft survives tab changes and panel close/reopen | PASS | Live text with newline, accented character and emoji survived Messages → Transcription → Mes notes, then Masquer les échanges → Afficher le chat → Mes notes. Repeated at mobile width while offline, after an actual failed save: identical unsaved text remained after both operations. `independent-03-retained-saved.png`, `independent-18-mobile-live-retained.png`. |
| Autosave survives reload | PASS | After Enregistré, reloaded the live room, rejoined, and reopened Mes notes. Exact value remained `Independent verifier — live draft, accents é, line 1\nLine 2 📝`. `independent-04-reloaded.png`. |
| Save before leaving; Feedback note access | PASS | Filled a new value and immediately clicked Quitter. A saving dialog appeared, then Feedback displayed the exact saved value `Independent verifier — save before leave 2026-10-16\nFeedback must retain this.` with Enregistré. `independent-06-feedback.png`. |
| History note access after leaving | PASS, fixture scope | History detail displayed the same saved note alongside the existing summary and transcript. `independent-07-history.png`. Cross-surface mapping is deliberately shared by the fixture. |
| Two-page stale revision yields explicit conflict | PASS | Opened history-a and history-b at the same baseline. Saved A, then edited stale B. B retained its draft and exposed Charger la version enregistrée and Enregistrer mon brouillon à la place. A subsequent conflict captured the actual PUT response: HTTP **409**, body `{"message": "revision conflict"}`. `independent-08-conflict.png`. |
| Explicitly load server version | PASS | Clicked Charger la version enregistrée. Editor became exactly `Independent SERVER A revision one`. `independent-09-server-loaded.png`. |
| Keep draft requires confirmation and persists | PASS, confirmation contract | Repeated the conflict after the root HMR update. An instrumented `window.confirm` recorded `Remplacer la version enregistrée par votre brouillon ?`. Returning false retained the conflict and draft; returning true produced PUT **200**, revision **6**, content `Independent preserved draft AFTER root HMR`. Reload retained that content. `independent-11-overwrite-confirmed.png`. Native browser dialog rendering was not captured; the browser confirmation boundary was instrumented to exercise cancellation and acceptance. |
| Failed-save draft and guarded history link, retry | PASS | Set the browser context offline, edited history detail, waited for Notes non enregistrées. Votre brouillon est conservé ici., then clicked Retour. The recovery dialog retained the exact draft. Retry while offline kept it. Reconnected and retried: saved successfully and completed navigation to history list. Reopening detail read back exactly `Independent OFFLINE draft — must survive guarded history navigation`. `independent-12-offline-history-guard.png`, `independent-13-retry-exited.png`. |
| **P1: browser Back through updated root guard** | **PASS** | Tested after loading the new GuardedRouter/App/NotesExitGuard code. Established a same-document list → detail history entry using the browser's native pushState, edited offline, then used Playwright page.goBack (actual browser history traversal). Address bar moved to history list, while the outgoing detail heading still existed and the recovery dialog retained `Independent SPA BROWSER BACK offline draft remains mounted`. Reconnected and retried; history list rendered only after saving. `independent-14-browser-back-guard.png`. |
| **P1: toolbar navigateTo through updated root guard** | **PASS** | On detail, filled the actual Code ou lien de réunion toolbar field with the live room URL. Edited offline, waited for failure, clicked Participer. Address bar changed to the room URL, while the outgoing detail and recovery editor remained mounted with `Independent TOOLBAR navigateTo offline draft retained until discard`. Explicit Quitter sans enregistrer completed navigation to the room's Rejoindre screen. This proves the explicit discard exit path; it does not assert a server-side no-write guarantee. `independent-15-toolbar-guard.png`, `independent-16-toolbar-discard.png`. |
| **P1: direct pushState exit and stable recovery modal** | **PASS** | In history detail with a failed offline save, called native history.pushState to history list. The outgoing draft `Independent final ROOT PUSHSTATE recovery proof — draft preserved` remained in a fully settled recovery modal. Reconnected and retried; navigation completed to the list. `independent-23-stable-root-recovery.png`. This is additional browser-history coverage, not an application function stub. |
| Mobile history keyboard editing | PASS, desktop browser emulation | At 390×844, focused the notes textbox and used keyboard End, Enter, text entry and Tab. Save reached Enregistré. `document.documentElement.scrollWidth === innerWidth === 390`. `independent-17-mobile-history.png`. |
| Mobile live keyboard layout | PASS, reduced viewport approximation | At 390×844 the live unsaved draft survived tab/panel changes with scrollWidth 390. At 390×420, focused the editor, used Control+End, Enter and keyboard text entry. Editor bounds were x=33, y=127, width=306, height=128, bottom=255; focused editor remained in viewport, scrollWidth 390. `independent-18-mobile-live-retained.png`, `independent-19-mobile-online.png`, `independent-20-mobile-short-keyboard.png`. |
| **P2: reject over-limit insertion without removing suffix** | **PASS** | Loaded the new rejection code. Filled and saved `A.repeat(99992) + 'TAIL-END'` (100,000 characters), placed caret in the middle and inserted Z through browser keyboard input. Entire prior value remained byte-for-byte equal, length 100,000, suffix TAIL-END, with the updated rejection alert. `independent-21-limit-rejected.png`. |
| P2: over-limit composition commit | PASS for synthetic composition events | Dispatched compositionstart to the real editor, inserted 漢 through browser keyboard input in the middle, then dispatched compositionend. Entire baseline and TAIL-END suffix remained unchanged at 100,000 characters with the rejection alert. This exercises React/editor composition handling; actual operating-system IME behavior is not proven. |
| Separate author UI | PASS, fixture scope only | Separate browser with browser2author showed Browser2Author Démo and an empty notes textbox while verifier had saved content. `independent-22-other-author.png`. This is not an authorization/security test because the fixture selects its map from that cookie. |

## Recovery details and interpretation

The recovery dialog visibly names the risk, retains an editable draft, and offers Réessayer l’enregistrement and Quitter sans enregistrer. The updated root router intentionally allows the browser URL to move before committing a route. Verification therefore checked the outgoing detail heading and draft editor as well as the address bar; a changed URL alone was not treated as successful navigation or draft loss.

The decisive Back, toolbar and pushState checks were performed after observing the new `src/frontend/src/navigation/GuardedRouter.tsx`, its App wrapper, and the `useNavigationGuard(requestExit)` registration. Tests reloaded or entered new documents after the update. Earlier HMR-transition/tool-selector timeouts were not classified as product failures. An initial Back attempt crossed a full-document navigation while offline and produced Chromium ERR_INTERNET_DISCONNECTED; it was replaced by the explicit same-document history traversal above and is not evidence about the SPA root guard.

The modal screenshots captured immediately on opening can contain transition opacity. `independent-23-stable-root-recovery.png` was captured after settling the modal and is the preferred recovery visual.

## Evidence integrity

Screenshots were saved by dev-browser's saveScreenshot to its temporary directory, then copied to the filesRoot read from `/root/.config/code-os/config.json`; no hardcoded hosting domain or separate static server was used.

`evidence-manifest.json` in the evidence directory records **19** readable PNG files with nonzero byte sizes and dimensions. Each screenshot also passed the configured dashboard HTTPS gate: HTTP **200**, `image/png`, `Cache-Control: private, no-store`, nonzero decoded dimensions. The same media bypass key returned HTTP **401** on `/api/health`. The initial Python default User-Agent received Cloudflare 1010/403; the ordinary browser User-Agent passed the gates. No bypass URLs or key values are retained in this report or the manifest.

Preferred recovery proof:

![Stable root navigation recovery dialog](/root/.local/share/code-os/files/skills-verify/meeting-personal-notes-independent-20261016/independent-23-stable-root-recovery.png)

Preferred mobile proof:

![Focused live notes editor in reduced mobile viewport](/root/.local/share/code-os/files/skills-verify/meeting-personal-notes-independent-20261016/independent-20-mobile-short-keyboard.png)

## Explicitly not proven by this run

- Real Core author-only access, participant authorization, account switching/logout isolation, database persistence, actual room-to-history note association, or production conflict handling. All note HTTP traffic here used the fixture API.
- A server-ended/closed meeting lifecycle or forced disconnect. Voluntary Quitter → Feedback and ended-looking history fixture access were tested; the fixture lifecycle always returns open.
- Real mobile Safari/Android, an actual virtual keyboard/visualViewport resize, physical IME, or assistive technology. Viewport and keyboard tests used headless desktop Chromium; composition events were synthetic.
- Native browser confirmation dialog appearance, browser/tab close or reload cancellation with unsaved offline drafts, and cross-document hard navigation protection. The successful reload check was performed after saving.
- Real transcription and message delivery; the existing tabs were exercised for navigation and draft retention.
- Independent backend test reruns or full frontend build. The parent reported seven real PostgreSQL tests, a real large-note HTTP round trip, and ongoing/full frontend checks; those results belong to the parent's separate evidence and are not incorporated as this verifier's runtime proof.

Final result: **PASS — tested UI contract and P1/P2 browser scenarios. Full feature end-to-end acceptance remains NOT PROVEN by this fixture-backed browser run.**
