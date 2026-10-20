import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import test from 'node:test'
import vm from 'node:vm'
import {
  hasBidirectionalMediaProgress,
  installMediaProbe,
  readMediaSnapshot,
} from './media-probe.mjs'

function snapshot(pcId = 'pc-1', offset = 0) {
  return {
    connections: [{ id: pcId, state: 'connected' }],
    streams: [
      {
        id: `${pcId}:ia`,
        direction: 'inbound',
        kind: 'audio',
        bytes: 100 + offset,
        frames: null,
        energy: 1 + offset / 100,
      },
      {
        id: `${pcId}:iv`,
        direction: 'inbound',
        kind: 'video',
        bytes: 200 + offset,
        frames: 10 + offset,
        energy: null,
      },
      {
        id: `${pcId}:oa`,
        direction: 'outbound',
        kind: 'audio',
        bytes: 300 + offset,
        frames: null,
        energy: null,
      },
      {
        id: `${pcId}:ov`,
        direction: 'outbound',
        kind: 'video',
        bytes: 400 + offset,
        frames: 20 + offset,
        energy: null,
      },
    ],
  }
}

test('requires sustained audio and video in both directions without mutating snapshots', () => {
  const before = snapshot()
  const after = snapshot('pc-1', 10)
  const original = structuredClone({ before, after })
  assert.equal(hasBidirectionalMediaProgress(before, after), true)
  assert.deepEqual({ before, after }, original)
  assert.equal(hasBidirectionalMediaProgress(before, before), false)
})

test('each direction and kind is required, including when only audio progresses', () => {
  for (let index = 0; index < 4; index++) {
    const before = snapshot()
    const after = snapshot('pc-1', 10)
    after.streams.splice(index, 1)
    assert.equal(hasBidirectionalMediaProgress(before, after), false)
    const stalled = snapshot('pc-1', 10)
    stalled.streams[index].bytes = before.streams[index].bytes
    assert.equal(hasBidirectionalMediaProgress(before, stalled), false)
  }
  const audioOnly = snapshot('pc-1', 10)
  audioOnly.streams = audioOnly.streams.filter(
    (stream) => stream.kind === 'audio'
  )
  assert.equal(hasBidirectionalMediaProgress(snapshot(), audioOnly), false)
})

test('video byte traffic alone cannot hide frozen decoded or encoded frames', () => {
  for (const index of [1, 3]) {
    const after = snapshot('pc-1', 10)
    after.streams[index].frames = snapshot().streams[index].frames
    assert.equal(hasBidirectionalMediaProgress(snapshot(), after), false)
  }
})

test('bytes and frames on different streams cannot be combined into progress', () => {
  const before = snapshot()
  const after = snapshot('pc-1', 10)
  before.streams.push({ ...before.streams[1], id: 'pc-1:other-video' })
  after.streams.push({ ...after.streams[1], id: 'pc-1:other-video' })
  after.streams[1].frames = before.streams[1].frames
  after.streams.at(-1).bytes = before.streams.at(-1).bytes
  assert.equal(hasBidirectionalMediaProgress(before, after), false)
})

test('incoming audio needs increasing energy; outgoing energy is optional', () => {
  const before = snapshot()
  const silent = snapshot('pc-1', 10)
  silent.streams[0].energy = before.streams[0].energy
  assert.equal(hasBidirectionalMediaProgress(before, silent), false)
  delete silent.streams[0].energy
  assert.equal(hasBidirectionalMediaProgress(before, silent), false)
  const after = snapshot('pc-1', 10)
  delete before.streams[2].energy
  delete after.streams[2].energy
  assert.equal(hasBidirectionalMediaProgress(before, after), true)
})

test('rejects missing, malformed, nonfinite and negative required counters in either sample', () => {
  const fields = [
    [0, 'bytes'],
    [0, 'energy'],
    [1, 'frames'],
    [3, 'frames'],
  ]
  for (const [index, field] of fields) {
    for (const value of [
      undefined,
      null,
      NaN,
      Infinity,
      -Infinity,
      -1,
      '10',
      true,
      {},
    ]) {
      for (const side of ['before', 'after']) {
        const pair = { before: snapshot(), after: snapshot('pc-1', 10) }
        pair[side].streams[index][field] = value
        assert.equal(
          hasBidirectionalMediaProgress(pair.before, pair.after),
          false,
          `${side} ${field} ${String(value)}`
        )
      }
    }
  }
  for (const field of ['bytes', 'frames']) {
    const after = snapshot('pc-1', 10)
    after.streams[1][field] = 1.5
    assert.equal(hasBidirectionalMediaProgress(snapshot(), after), false)
  }
})

test('rejects counter resets and invalid optional energy even with another progressing stream', () => {
  for (const [index, field] of [
    [0, 'bytes'],
    [0, 'energy'],
    [1, 'frames'],
    [3, 'frames'],
  ]) {
    const after = snapshot('pc-1', 10)
    after.streams[index][field] = 0
    after.streams.push({
      ...snapshot('pc-1', 10).streams[index],
      id: 'pc-1:alternative',
    })
    const before = snapshot()
    before.streams.push({ ...before.streams[index], id: 'pc-1:alternative' })
    assert.equal(hasBidirectionalMediaProgress(before, after), false)
  }
  const before = snapshot()
  before.streams[2].energy = 2
  const after = snapshot('pc-1', 10)
  after.streams[2].energy = 1
  assert.equal(hasBidirectionalMediaProgress(before, after), false)
  after.streams[2].energy = NaN
  assert.equal(hasBidirectionalMediaProgress(snapshot(), after), false)
})

test('never counts streams from a PC not connected at both endpoints', () => {
  for (const state of [
    'new',
    'connecting',
    'disconnected',
    'failed',
    'closed',
  ]) {
    for (const side of ['before', 'after']) {
      const pair = { before: snapshot(), after: snapshot('pc-1', 10) }
      pair[side].connections[0].state = state
      assert.equal(
        hasBidirectionalMediaProgress(pair.before, pair.after),
        false
      )
    }
  }
})

test('a replacement PC or stats stream cannot reuse a previous baseline', () => {
  assert.equal(
    hasBidirectionalMediaProgress(snapshot(), snapshot('pc-2', 1000)),
    false
  )
  const after = snapshot('pc-1', 1000)
  for (const stream of after.streams) stream.id += '-replacement'
  assert.equal(hasBidirectionalMediaProgress(snapshot(), after), false)

  const reconnected = snapshot('pc-2', 0)
  reconnected.connections.unshift({ id: 'pc-1', state: 'closed' })
  const progressing = snapshot('pc-2', 10)
  progressing.connections.unshift({ id: 'pc-1', state: 'closed' })
  assert.equal(hasBidirectionalMediaProgress(snapshot(), reconnected), false)
  assert.equal(hasBidirectionalMediaProgress(reconnected, progressing), true)
})

test('publisher and subscriber PCs may provide different media paths', () => {
  const before = snapshot()
  const after = snapshot('pc-1', 10)
  for (const sample of [before, after]) {
    sample.connections.push({ id: 'pc-2', state: 'connected' })
    for (const stream of sample.streams) {
      if (stream.direction === 'outbound')
        stream.id = stream.id.replace('pc-1:', 'pc-2:')
    }
  }
  assert.equal(hasBidirectionalMediaProgress(before, after), true)
})

test('rejects malformed snapshots, ambiguous IDs and stream identity changes', () => {
  for (const invalid of [
    null,
    undefined,
    false,
    {},
    { connections: [], streams: null },
  ]) {
    assert.equal(hasBidirectionalMediaProgress(invalid, snapshot()), false)
    assert.equal(hasBidirectionalMediaProgress(snapshot(), invalid), false)
  }
  const mutations = [
    (value) => value.connections.push({ ...value.connections[0] }),
    (value) => value.streams.push({ ...value.streams[0] }),
    (value) => {
      value.connections[0].state = 'unknown'
    },
    (value) => {
      value.streams[0].id = 'pc-3:unknown'
    },
    (value) => {
      value.streams[0].id = 'pc-1:'
    },
    (value) => {
      value.streams[0].direction = 'unknown'
    },
    (value) => {
      value.streams[0].kind = 'unknown'
    },
    (value) => {
      value.streams[0].direction = 'outbound'
    },
  ]
  for (const mutate of mutations) {
    const after = snapshot('pc-1', 10)
    mutate(after)
    assert.equal(hasBidirectionalMediaProgress(snapshot(), after), false)
  }
})

async function browserHarness({ initialState = 'connected' } = {}) {
  class NativePeerConnection {
    static generateCertificate() {
      return 'native-static'
    }
    constructor(configuration) {
      this.configuration = configuration
      this.connectionState = initialState
      this.reports = new Map()
    }
    async getStats() {
      return this.reports
    }
    addEventListener() {}
    getSenders() {
      return []
    }
    getReceivers() {
      return []
    }
  }
  const browser = vm.createContext({
    RTCPeerConnection: NativePeerConnection,
    crypto: webcrypto,
  })
  vm.runInContext('window = globalThis', browser)
  const context = {
    async addInitScript(callback) {
      vm.runInContext(`(${callback.toString()})()`, browser)
    },
  }
  await installMediaProbe(context)
  const page = {
    async evaluate(callback) {
      const value = await vm.runInContext(`(${callback.toString()})()`, browser)
      return structuredClone(value)
    },
  }
  return { browser, context, page, NativePeerConnection }
}

function reports(offset = 0) {
  return new Map([
    [
      'ia',
      {
        id: 'ia',
        type: 'inbound-rtp',
        kind: 'audio',
        bytesReceived: 100 + offset,
        totalAudioEnergy: 1 + offset / 100,
      },
    ],
    [
      'iv',
      {
        id: 'iv',
        type: 'inbound-rtp',
        kind: 'video',
        bytesReceived: 200 + offset,
        framesDecoded: 10 + offset,
      },
    ],
    [
      'oa',
      {
        id: 'oa',
        type: 'outbound-rtp',
        kind: 'audio',
        bytesSent: 300 + offset,
        mediaSourceId: 'source',
      },
    ],
    [
      'ov',
      {
        id: 'ov',
        type: 'outbound-rtp',
        kind: 'video',
        bytesSent: 400 + offset,
        framesEncoded: 20 + offset,
      },
    ],
    [
      'source',
      {
        id: 'source',
        type: 'media-source',
        kind: 'audio',
        totalAudioEnergy: 2 + offset / 100,
      },
    ],
  ])
}

test('native constructor, prototype, subclass and static behavior survive installation', async () => {
  const { browser, NativePeerConnection } = await browserHarness()
  const Wrapped = browser.RTCPeerConnection
  const configuration = { iceServers: [{ urls: 'PRIVATE_SENTINEL' }] }
  const connection = new Wrapped(configuration)
  assert.equal(connection.configuration, configuration)
  assert.ok(connection instanceof Wrapped)
  assert.ok(connection instanceof NativePeerConnection)
  assert.equal(Wrapped.prototype, NativePeerConnection.prototype)
  assert.equal(Wrapped.generateCertificate(), 'native-static')
  class Child extends Wrapped {}
  assert.ok(new Child() instanceof Child)
  assert.throws(() => Wrapped(), TypeError)
})

test('departure diagnostics distinguish existing and later peers without private values', async () => {
  const { browser, page } = await browserHarness({ initialState: 'new' })
  const active = new browser.RTCPeerConnection()
  active.connectionState = 'connected'
  active.reports = reports()
  await readMediaSnapshot(page)
  vm.runInContext('window.__meetMarkDeparture()', browser)
  active.connectionState = 'closed'
  const later = new browser.RTCPeerConnection()
  later.connectionState = 'new'
  later.localDescription = null
  later.remoteDescription = null
  const result = await readMediaSnapshot(page)
  assert.deepEqual(result.departureConnections, [
    {
      state: 'new',
      stateBeforeLeave: null,
      createdAfterLeaveMark: true,
      everConnected: false,
      localDescriptionPresent: false,
      remoteDescriptionPresent: false,
      liveSenderTracks: 0,
      liveReceiverTracks: 0,
    },
  ])
})

test('departure flags retain prior connection evidence and never export descriptions or track labels', async () => {
  const { browser, page } = await browserHarness()
  const peer = new browser.RTCPeerConnection()
  peer.reports = reports()
  await readMediaSnapshot(page)
  vm.runInContext('window.__meetMarkDeparture()', browser)
  peer.connectionState = 'new'
  peer.localDescription = { sdp: 'PRIVATE_SENTINEL' }
  peer.remoteDescription = { sdp: 'PRIVATE_SENTINEL' }
  peer.getSenders = () => [
    { track: { readyState: 'live', label: 'PRIVATE_SENTINEL' } },
  ]
  peer.getReceivers = () => [
    { track: { readyState: 'ended', label: 'PRIVATE_SENTINEL' } },
  ]
  const result = await readMediaSnapshot(page)
  assert.deepEqual(result.departureConnections, [
    {
      state: 'new',
      stateBeforeLeave: 'connected',
      createdAfterLeaveMark: false,
      everConnected: true,
      localDescriptionPresent: true,
      remoteDescriptionPresent: true,
      liveSenderTracks: 1,
      liveReceiverTracks: 0,
    },
  ])
  assert.equal(JSON.stringify(result).includes('PRIVATE_SENTINEL'), false)
})

test('serialized init script and page reader expose only projected RTP counters', async () => {
  const { browser, context, page } = await browserHarness()
  const pc = new browser.RTCPeerConnection({ credential: 'PRIVATE_SENTINEL' })
  pc.reports = reports()
  pc.reports.set('candidate', {
    id: 'PRIVATE_SENTINEL',
    type: 'local-candidate',
    address: 'PRIVATE_SENTINEL',
  })
  pc.reports.get('ia').sdp = 'PRIVATE_SENTINEL'
  const before = await readMediaSnapshot(page)
  assert.deepEqual(before.connections, [{ id: 'pc-1', state: 'connected' }])
  assert.equal(before.streams.length, 4)
  assert.match(before.streams[0].id, /^pc-1:.+:ia$/)
  assert.deepEqual(
    { ...before.streams[0], id: 'pc-1:ia' },
    snapshot().streams[0]
  )
  assert.equal(before.streams[2].energy, 2)
  for (const stream of before.streams) {
    assert.deepEqual(Object.keys(stream).sort(), [
      'bytes',
      'direction',
      'energy',
      'frames',
      'id',
      'kind',
    ])
  }
  assert.equal(JSON.stringify(before).includes('PRIVATE_SENTINEL'), false)
  pc.reports = reports(10)
  const after = await readMediaSnapshot(page)
  assert.equal(hasBidirectionalMediaProgress(before, after), true)
  await installMediaProbe(context)
  assert.deepEqual(await readMediaSnapshot(page), after)
})

test('distinct native PCs with reused stats IDs never share a baseline', async () => {
  const { browser, page } = await browserHarness()
  const first = new browser.RTCPeerConnection()
  first.reports = reports()
  const before = await readMediaSnapshot(page)
  first.connectionState = 'closed'
  const second = new browser.RTCPeerConnection()
  second.reports = reports(1000)
  const baseline = await readMediaSnapshot(page)
  assert.equal(hasBidirectionalMediaProgress(before, baseline), false)
  second.reports = reports(1010)
  assert.equal(
    hasBidirectionalMediaProgress(baseline, await readMediaSnapshot(page)),
    true
  )
})

test('a new document cannot reuse the baseline of a previous document', async () => {
  const first = await browserHarness()
  const second = await browserHarness()
  const beforePc = new first.browser.RTCPeerConnection()
  beforePc.reports = reports()
  const afterPc = new second.browser.RTCPeerConnection()
  afterPc.reports = reports(1000)
  const before = await readMediaSnapshot(first.page)
  const after = await readMediaSnapshot(second.page)
  assert.equal(hasBidirectionalMediaProgress(before, after), false)
})

test('missing native stats IDs cannot become a synthetic stable baseline', async () => {
  const { browser, page } = await browserHarness()
  const pc = new browser.RTCPeerConnection()
  pc.reports = reports()
  delete pc.reports.get('iv').id
  const before = await readMediaSnapshot(page)
  pc.reports = reports(10)
  delete pc.reports.get('iv').id
  const after = await readMediaSnapshot(page)
  assert.equal(hasBidirectionalMediaProgress(before, after), false)
})

test('absent WebRTC or an uninstalled probe cannot yield media proof', async () => {
  const browser = vm.createContext({})
  vm.runInContext('window = globalThis', browser)
  const page = {
    evaluate: (callback) =>
      vm.runInContext(`(${callback.toString()})()`, browser),
  }
  await assert.rejects(readMediaSnapshot(page), /probe is not installed/)
  await installMediaProbe({
    addInitScript: (callback) =>
      vm.runInContext(`(${callback.toString()})()`, browser),
  })
  const sample = await readMediaSnapshot(page)
  assert.equal(hasBidirectionalMediaProgress(sample, sample), false)
})

test('getStats failures stay sanitized; disconnection during the read discards its streams', async () => {
  const { browser, page } = await browserHarness()
  const pc = new browser.RTCPeerConnection()
  pc.getStats = async () => {
    throw new Error('PRIVATE_SENTINEL')
  }
  await assert.rejects(readMediaSnapshot(page), {
    message: 'WebRTC media statistics unavailable',
  })
  pc.getStats = async () => {
    pc.connectionState = 'disconnected'
    return reports(10)
  }
  const sample = await readMediaSnapshot(page)
  assert.deepEqual(sample.connections, [{ id: 'pc-1', state: 'disconnected' }])
  assert.deepEqual(sample.streams, [])
})

test('projection preserves missing counters and rejects malformed ones without exporting their text', async () => {
  const { browser, page } = await browserHarness()
  const pc = new browser.RTCPeerConnection()
  pc.reports = reports()
  const before = await readMediaSnapshot(page)
  pc.reports = reports(10)
  delete pc.reports.get('iv').framesDecoded
  let after = await readMediaSnapshot(page)
  assert.equal(after.streams[1].frames, null)
  assert.equal(hasBidirectionalMediaProgress(before, after), false)
  pc.reports = reports(10)
  pc.reports.get('source').totalAudioEnergy = 'PRIVATE_SENTINEL'
  after = await readMediaSnapshot(page)
  assert.equal(after.streams[2].energy, -1)
  assert.equal(JSON.stringify(after).includes('PRIVATE_SENTINEL'), false)
  assert.equal(hasBidirectionalMediaProgress(before, after), false)
})
