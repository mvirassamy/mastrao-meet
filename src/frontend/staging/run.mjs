import { chromium } from 'playwright'
import { loadFixture } from './fixture.mjs'
import { qualifyMeeting } from './meeting.mjs'
import { within } from './deadline.mjs'

let browser
let result = {
  status: 'MEET_TWO_PARTY_BROWSER_NOT_CONFIRMED',
  stage: 'FIXTURE',
}
try {
  const fixture = await loadFixture(process.env.MEET_STAGING_FIXTURE)
  result.stage = 'BROWSER'
  browser = await chromium.launch({
    args: [
      '--use-fake-device-for-media-stream',
      '--use-fake-ui-for-media-stream',
      '--autoplay-policy=no-user-gesture-required',
    ],
  })
  result.stage = 'QUALIFICATION'
  result = await within(qualifyMeeting(browser, fixture), 180_000)
} catch {
  // Playwright exceptions can contain private URLs, DOM text and session details.
} finally {
  if (browser) {
    try {
      await within(browser.close(), 5000)
    } catch {
      result = {
        status: 'MEET_TWO_PARTY_BROWSER_NOT_CONFIRMED',
        stage: 'BROWSER_CLEANUP',
      }
    }
  }
}
process.stdout.write(
  `${JSON.stringify({
    schemaVersion: 1,
    ...result,
    releaseCorrelationProven: false,
    roomCreationProven: false,
    networkOutageRecoveryProven: false,
    recordingAndTranscriptionProven: false,
    automaticMeetDeliveryProven: false,
  })}\n`,
  // A nonresponsive browser driver must not keep a failed qualification alive.
  () => process.exit(result.status === 'MEET_TWO_PARTY_BROWSER_PASS' ? 0 : 1)
)
