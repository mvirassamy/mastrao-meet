import { STAGING_ORIGIN } from './fixture.mjs'
import { within } from './deadline.mjs'
import {
  openParticipants,
  requireExpectedParticipants,
} from './participants.mjs'
import {
  installMediaProbe,
  readMediaSnapshot,
  hasBidirectionalMediaProgress,
} from './media-probe.mjs'

const ACTION_TIMEOUT = 30_000

export function connectionsClosed(snapshot) {
  return (
    Array.isArray(snapshot?.connections) &&
    snapshot.connections.length > 0 &&
    snapshot.connections.every((connection) => connection.state === 'closed')
  )
}

async function openParticipant(browser, participant, contexts) {
  const context = await browser.newContext({
    storageState: participant.storageState,
    locale: 'en-US',
    permissions: ['camera', 'microphone'],
    viewport: { width: 1440, height: 1000 },
  })
  contexts.push(context)
  context.setDefaultTimeout(ACTION_TIMEOUT)
  context.setDefaultNavigationTimeout(ACTION_TIMEOUT)
  await context.addCookies([
    { name: 'mastrao_lang', value: 'en', url: STAGING_ORIGIN, secure: true },
  ])
  const response = await context.request.get(
    `${STAGING_ORIGIN}/api/v1.0/users/me/`,
    {
      maxRedirects: 0,
      timeout: ACTION_TIMEOUT,
    }
  )
  if (
    response.status() !== 200 ||
    (await response.json()).id !== participant.expectedUserId
  ) {
    throw new Error('Participant authentication does not match the fixture')
  }
  await installMediaProbe(context)
  return context.newPage()
}

async function joinMeeting(page, roomUrl, reload = false) {
  if (reload) await page.reload({ waitUntil: 'domcontentloaded' })
  else await page.goto(roomUrl, { waitUntil: 'domcontentloaded' })
  const join = page.getByRole('button', { name: 'Join', exact: true })
  await join.waitFor({ state: 'visible' })
  for (const device of ['microphone', 'camera']) {
    const enable = page.getByRole('button', {
      name: new RegExp(`^Enable ${device}(?:$| )`),
    })
    if (await enable.isVisible()) await enable.click()
    await page
      .getByRole('button', { name: new RegExp(`^Disable ${device}(?:$| )`) })
      .waitFor({ state: 'visible' })
  }
  await join.click()
  await page
    .locator('[data-attr="controls-leave"]')
    .waitFor({ state: 'visible' })
}

export async function requireMediaProgress(
  pages,
  { attempts = 12, intervalMs = 1000, expectedIds } = {}
) {
  async function readSamples() {
    if (expectedIds)
      await Promise.all(
        pages.map((page) => requireExpectedParticipants(page, expectedIds))
      )
    const snapshots = await Promise.all(pages.map(readMediaSnapshot))
    if (expectedIds) {
      await Promise.all(
        pages.map((page) => requireExpectedParticipants(page, expectedIds))
      )
      for (const snapshot of snapshots) {
        for (const kind of ['audio', 'video']) {
          const incoming = snapshot.streams.filter(
            (stream) => stream.direction === 'inbound' && stream.kind === kind
          )
          if (incoming.length > 1)
            throw new Error('Only one remote media source per kind is allowed')
        }
      }
    }
    return snapshots
  }
  let previous = await readSamples()
  for (let attempt = 0; attempt < attempts; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
    const current = await readSamples()
    if (
      current.every((snapshot, index) =>
        hasBidirectionalMediaProgress(previous[index], snapshot)
      )
    )
      return
    previous = current
  }
  throw new Error(
    'Bidirectional audio and video did not progress for both participants'
  )
}

async function leaveMeeting(page, departure) {
  await page.locator('[data-attr="controls-leave"]').click()
  const dialog = page.getByRole('alertdialog', {
    name: 'Leave or end the meeting?',
    exact: true,
  })
  // The owner gets the leave-only dialog; an admitted guest leaves directly.
  const leaveOnly = dialog.getByRole('button', {
    name: 'Leave only',
    exact: true,
  })
  departure.step = 'TRANSITION'
  let waitingForTransition = true
  try {
    await Promise.race([
      leaveOnly.waitFor({ state: 'visible' }).then(() => {
        if (!waitingForTransition) return
        departure.step = 'CONFIRMATION'
        return leaveOnly.click()
      }),
      page.waitForURL(
        (url) =>
          url.pathname === '/feedback' &&
          url.searchParams.get('outcome') === 'left'
      ),
    ])
  } finally {
    waitingForTransition = false
  }
  departure.step = 'FEEDBACK'
  await page.waitForURL(
    (url) =>
      url.pathname === '/feedback' && url.searchParams.get('outcome') === 'left'
  )
  departure.feedbackReached = true
  departure.step = 'CONNECTIONS'
  const controller = new AbortController()
  try {
    await within(
      waitForClosedConnections(page, departure, controller.signal),
      5000
    )
  } finally {
    controller.abort()
  }
}

async function waitForClosedConnections(page, departure, signal) {
  while (!signal.aborted) {
    const snapshot = await readMediaSnapshot(page)
    if (signal.aborted) return
    departure.connectionStates = Object.fromEntries(
      [
        'new',
        'connecting',
        'connected',
        'disconnected',
        'failed',
        'closed',
      ].map((state) => [
        state,
        snapshot.connections.filter((connection) => connection.state === state)
          .length,
      ])
    )
    if (connectionsClosed(snapshot)) return
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
}

export async function qualifyMeeting(browser, fixture) {
  const contexts = []
  let stage = 'AUTHENTICATION'
  let departure
  let result
  try {
    const pages = []
    for (const participant of fixture.participants) {
      pages.push(await openParticipant(browser, participant, contexts))
    }
    stage = 'JOIN'
    for (const page of pages) await joinMeeting(page, fixture.roomUrl)
    stage = 'AUDIO_VIDEO'
    const expectedIds = fixture.participants.map(
      (participant) => participant.expectedRtcIdentity
    )
    for (const page of pages) await openParticipants(page)
    await requireMediaProgress(pages, { expectedIds })
    stage = 'RELOAD_REJOIN'
    await joinMeeting(pages[1], fixture.roomUrl, true)
    await openParticipants(pages[1])
    await requireMediaProgress(pages, { expectedIds })
    stage = 'LEAVE'
    for (let index = pages.length - 1; index >= 0; index--) {
      departure = {
        participant: index + 1,
        step: 'CONTROL',
        feedbackReached: false,
        connectionStates: null,
      }
      await leaveMeeting(pages[index], departure)
    }
    result = {
      status: 'MEET_TWO_PARTY_BROWSER_PASS',
      stage: 'COMPLETE',
      checks: {
        twoAuthenticatedUsers: true,
        join: true,
        bidirectionalAudioVideo: true,
        expectedParticipantsOnly: true,
        reloadRejoin: true,
        leave: true,
      },
    }
  } catch {
    result = { status: 'MEET_TWO_PARTY_BROWSER_NOT_CONFIRMED', stage }
    if (departure) result.departure = structuredClone(departure)
  } finally {
    const cleanup = await Promise.allSettled(
      contexts.map((context) => within(context.close(), 5000))
    )
    if (cleanup.some((outcome) => outcome.status === 'rejected')) {
      result = {
        status: 'MEET_TWO_PARTY_BROWSER_NOT_CONFIRMED',
        stage: 'CONTEXT_CLEANUP',
      }
    }
  }
  return result
}
