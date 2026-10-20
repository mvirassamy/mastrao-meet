import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  connectionsClosed,
  qualifyMeeting,
  requireMediaProgress,
} from './meeting.mjs'
import { readMediaSnapshot } from './media-probe.mjs'

test('refuses expired authentication before navigating and closes its context', async () => {
  let closed = false
  const browser = {
    async newContext() {
      return {
        setDefaultTimeout() {},
        setDefaultNavigationTimeout() {},
        async addCookies() {},
        request: {
          async get(_url, options) {
            assert.equal(options.maxRedirects, 0)
            return { status: () => 401 }
          },
        },
        async newPage() {
          assert.fail('Unauthenticated users must never navigate')
        },
        async close() {
          closed = true
        },
      }
    },
  }
  const result = await qualifyMeeting(browser, {
    participants: [{ expectedUserId: 'synthetic-a', storageState: {} }],
  })
  assert.deepEqual(result, {
    status: 'MEET_TWO_PARTY_BROWSER_NOT_CONFIRMED',
    stage: 'AUTHENTICATION',
  })
  assert.equal(closed, true)
})

test('refuses a valid session belonging to another user and hides private errors', async () => {
  for (const rejectWithRawError of [false, true]) {
    let closed = false
    const browser = {
      async newContext() {
        return {
          setDefaultTimeout() {},
          setDefaultNavigationTimeout() {},
          async addCookies() {},
          request: {
            async get() {
              if (rejectWithRawError)
                throw new Error(
                  'private-marker https://private.invalid/?credential=hidden'
                )
              return {
                status: () => 200,
                json: async () => ({ id: 'another-user' }),
              }
            },
          },
          async close() {
            closed = true
          },
        }
      },
    }
    const result = await qualifyMeeting(browser, {
      participants: [{ expectedUserId: 'synthetic-a' }],
    })
    assert.equal(result.stage, 'AUTHENTICATION')
    assert.equal(JSON.stringify(result).includes('private-marker'), false)
    assert.equal(closed, true)
  }
})

test('no peer statistics cannot pass the functional media gate', async () => {
  let samples = 0
  const page = {
    async evaluate() {
      samples++
      return { connections: [], streams: [] }
    },
  }
  await assert.rejects(
    requireMediaProgress([page, page], { attempts: 2, intervalMs: 1 }),
    /did not progress/
  )
  assert.equal(samples, 6)
})

test('departure requires every observed peer to be closed, including disconnected peers', () => {
  const closed = { connections: [{ state: 'closed' }, { state: 'closed' }] }
  assert.equal(connectionsClosed(closed), true)
  for (const state of [
    'new',
    'connecting',
    'connected',
    'disconnected',
    'failed',
  ]) {
    assert.equal(
      connectionsClosed({ connections: [{ state: 'closed' }, { state }] }),
      false,
      `An unclosed ${state} peer must prevent departure confirmation`
    )
  }
  assert.equal(connectionsClosed(undefined), false)
  assert.equal(connectionsClosed({}), false)
  assert.equal(connectionsClosed({ connections: [] }), false)
})

test('a hanging snapshot read rejects at its deadline without waiting five seconds', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let reads = 0
  const pending = readMediaSnapshot({
    evaluate() {
      reads++
      return new Promise(() => {})
    },
  })
  const rejected = assert.rejects(pending, /deadline/i)
  t.mock.timers.tick(5000)
  await rejected
  assert.equal(reads, 1)
})

test('a hanging context cleanup becomes an unconfirmed result at its deadline', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let notifyClosing
  const closing = new Promise((resolve) => {
    notifyClosing = resolve
  })
  let closeAttempts = 0
  const browser = {
    async newContext() {
      return {
        setDefaultTimeout() {},
        setDefaultNavigationTimeout() {},
        async addCookies() {},
        request: { get: async () => ({ status: () => 401 }) },
        close() {
          closeAttempts++
          notifyClosing()
          return new Promise(() => {})
        },
      }
    },
  }
  const pending = qualifyMeeting(browser, {
    participants: [{ expectedUserId: 'synthetic-a', storageState: {} }],
  })
  await closing
  t.mock.timers.tick(5000)
  assert.deepEqual(await pending, {
    status: 'MEET_TWO_PARTY_BROWSER_NOT_CONFIRMED',
    stage: 'CONTEXT_CLEANUP',
  })
  assert.equal(closeAttempts, 1)
})

function successfulMeetingDouble({
  rejectCleanup = false,
  initiallyEmptyMedia = false,
  rejectDeparture = false,
  rejectFeedback = false,
  retainClosedConnection = false,
  delayedConfirmation = false,
} = {}) {
  const contexts = []
  const fixture = {
    roomUrl: 'https://meet.mastrao-staging.com/room_' + 'a'.repeat(32),
    participants: [0, 1].map((index) => ({
      expectedUserId: `synthetic-${index}`,
      expectedRtcIdentity: `rtc-${index}`,
      storageState: { cookies: [], origins: [] },
    })),
  }
  const browser = {
    async newContext() {
      const expectedUserId =
        fixture.participants[contexts.length].expectedUserId
      let sample = 0
      let left = false
      function element(name) {
        return {
          async waitFor() {
            if (delayedConfirmation && name === 'Leave only') {
              await new Promise((resolve) => setTimeout(resolve, 100))
            }
          },
          async isVisible() {
            return false
          },
          async getAttribute(attribute) {
            assert.equal(attribute, 'aria-controls')
            return 'synthetic-participants'
          },
          async click() {
            if (name === 'Leave only') context.confirmationClicks++
            if (rejectDeparture && name === 'leave')
              throw new Error(
                'private-marker https://private.invalid/?token=hidden'
              )
            if (name === 'leave' || name === 'Leave only') left = true
          },
          getByRole: (_role, options) => element(options.name),
        }
      }
      const page = {
        async goto() {},
        async reload() {},
        async waitForURL() {
          if (rejectFeedback)
            throw new Error(
              'private-marker https://private.invalid/?token=hidden'
            )
        },
        getByRole: (_role, options) => element(options.name),
        locator: () => element('leave'),
        async evaluate(_callback, id) {
          if (context.closed) throw new Error('Synthetic context is closed')
          if (id !== undefined) {
            assert.equal(id, 'synthetic-participants')
            return ['rtc-0', 'rtc-1']
          }
          if (left) {
            return {
              connections: [
                {
                  id: 'pc-1',
                  state: retainClosedConnection ? 'disconnected' : 'closed',
                },
              ],
              streams: [],
            }
          }
          sample++
          if (initiallyEmptyMedia && sample === 1) {
            return {
              connections: [{ id: 'pc-1', state: 'connected' }],
              streams: [],
            }
          }
          return {
            connections: [{ id: 'pc-1', state: 'connected' }],
            streams: ['inbound', 'outbound'].flatMap((direction) =>
              ['audio', 'video'].map((kind) => ({
                id: `pc-1:${direction}-${kind}`,
                direction,
                kind,
                bytes: 100 + sample,
                frames: kind === 'video' ? sample : null,
                energy: kind === 'audio' ? sample : null,
              }))
            ),
          }
        },
      }
      const context = {
        confirmationClicks: 0,
        closeAttempts: 0,
        closed: false,
        setDefaultTimeout() {},
        setDefaultNavigationTimeout() {},
        async addCookies() {},
        async addInitScript() {},
        request: {
          async get() {
            return {
              status: () => 200,
              json: async () => ({ id: expectedUserId }),
            }
          },
        },
        async newPage() {
          return page
        },
        async close() {
          this.closeAttempts++
          if (rejectCleanup && contexts[0] === this) {
            throw new Error(
              'private-marker https://private.invalid/?token=hidden'
            )
          }
          this.closed = true
        },
      }
      contexts.push(context)
      return context
    },
  }
  return { browser, fixture, contexts }
}

test('departure failure identifies the participant and UI step without private errors', async () => {
  const { browser, fixture, contexts } = successfulMeetingDouble({
    rejectDeparture: true,
  })
  const result = await qualifyMeeting(browser, fixture)
  assert.deepEqual(result, {
    status: 'MEET_TWO_PARTY_BROWSER_NOT_CONFIRMED',
    stage: 'LEAVE',
    departure: {
      participant: 2,
      step: 'CONTROL',
      feedbackReached: false,
      connectionStates: null,
    },
  })
  assert.equal(JSON.stringify(result).includes('private-marker'), false)
  assert.equal(
    contexts.every((context) => context.closed),
    true
  )
})

test('an unconfirmed feedback transition cannot be reported as closed connections', async () => {
  const { browser, fixture } = successfulMeetingDouble({ rejectFeedback: true })
  const result = await qualifyMeeting(browser, fixture)
  assert.equal(result.status, 'MEET_TWO_PARTY_BROWSER_NOT_CONFIRMED')
  assert.equal(result.departure.participant, 2)
  assert.equal(result.departure.feedbackReached, false)
  assert.equal(result.departure.connectionStates, null)
  assert.equal(JSON.stringify(result).includes('private-marker'), false)
})

test('a feedback page with an unclosed peer reports bounded state counts and cannot pass', async () => {
  const { browser, fixture, contexts } = successfulMeetingDouble({
    retainClosedConnection: true,
    delayedConfirmation: true,
  })
  const result = await qualifyMeeting(browser, fixture)
  assert.equal(result.status, 'MEET_TWO_PARTY_BROWSER_NOT_CONFIRMED')
  assert.equal(result.departure.participant, 2)
  assert.equal(result.departure.step, 'CONNECTIONS')
  assert.equal(result.departure.feedbackReached, true)
  assert.deepEqual(result.departure.connectionStates, {
    new: 0,
    connecting: 0,
    connected: 0,
    disconnected: 1,
    failed: 0,
    closed: 0,
  })
  assert.equal(
    contexts.every((context) => context.confirmationClicks === 0),
    true
  )
  assert.equal(
    contexts.every((context) => context.closed),
    true
  )
})

test('empty initial media waits for two subsequent valid progressing samples', async () => {
  const { browser, fixture } = successfulMeetingDouble({
    initiallyEmptyMedia: true,
  })
  const pages = []
  const mediaReads = [0, 0]
  for (let index = 0; index < 2; index++) {
    const context = await browser.newContext()
    const page = await context.newPage()
    const evaluate = page.evaluate.bind(page)
    page.evaluate = (callback, id) => {
      if (id === undefined) mediaReads[index]++
      return evaluate(callback, id)
    }
    pages.push(page)
  }
  await requireMediaProgress(pages, {
    attempts: 2,
    intervalMs: 1,
    expectedIds: fixture.participants.map(
      (participant) => participant.expectedRtcIdentity
    ),
  })
  assert.deepEqual(mediaReads, [3, 3])
})

test('successful media and departure cannot hide rejected context cleanup', async () => {
  for (const rejectCleanup of [false, true]) {
    const { browser, fixture, contexts } = successfulMeetingDouble({
      rejectCleanup,
    })
    const result = await qualifyMeeting(browser, fixture)
    assert.equal(contexts.length, 2)
    assert.deepEqual(
      contexts.map((context) => context.closeAttempts),
      [1, 1]
    )
    assert.equal(contexts[1].closed, true)
    assert.equal(JSON.stringify(result).includes('private-marker'), false)
    if (rejectCleanup) {
      assert.deepEqual(result, {
        status: 'MEET_TWO_PARTY_BROWSER_NOT_CONFIRMED',
        stage: 'CONTEXT_CLEANUP',
      })
    } else {
      assert.equal(result.status, 'MEET_TWO_PARTY_BROWSER_PASS')
      assert.equal(result.stage, 'COMPLETE')
      assert.equal(contexts[0].closed, true)
    }
  }
})
