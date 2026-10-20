import { within } from './deadline.mjs'

/** Install before navigation. Keep returned snapshots internal to the runner. */
export async function installMediaProbe(context) {
  await context.addInitScript(() => {
    if (typeof window.__meetMediaSnapshot === 'function') return

    const connections = new Set()
    const identifiers = new WeakMap()
    let connectionCount = 0
    let documentId = ''
    const NativePeerConnection = window.RTCPeerConnection

    if (typeof NativePeerConnection === 'function') {
      // Native stats IDs and the PC counter may repeat after navigation.
      documentId = window.crypto.randomUUID()
      window.RTCPeerConnection = new Proxy(NativePeerConnection, {
        construct(target, argumentsList, newTarget) {
          const connection = Reflect.construct(target, argumentsList, newTarget)
          connections.add(connection)
          identifiers.set(connection, `pc-${++connectionCount}`)
          return connection
        },
      })
    }

    // Missing optional counters stay missing. Malformed values become an
    // invalid numeric sentinel, never arbitrary text from a stats report.
    function counter(value) {
      if (value === undefined) return null
      if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
        return -1
      }
      return value
    }

    window.__meetMediaSnapshot = async () => {
      const snapshot = { connections: [], streams: [] }
      for (const connection of [...connections]) {
        const id = identifiers.get(connection)
        const entry = { id, state: connection.connectionState }
        snapshot.connections.push(entry)
        if (entry.state !== 'connected') continue

        let reports
        try {
          reports = await connection.getStats()
        } catch {
          entry.state = connection.connectionState
          if (entry.state !== 'connected') continue
          throw new Error('WebRTC media statistics unavailable')
        }
        entry.state = connection.connectionState
        if (entry.state !== 'connected') continue

        for (const report of reports.values()) {
          if (report.type !== 'inbound-rtp' && report.type !== 'outbound-rtp') {
            continue
          }
          if (report.kind !== 'audio' && report.kind !== 'video') continue

          const inbound = report.type === 'inbound-rtp'
          let frames = null
          let energy = null
          if (report.kind === 'video') {
            frames = counter(
              inbound ? report.framesDecoded : report.framesEncoded
            )
          } else if (inbound) {
            energy = counter(report.totalAudioEnergy)
          } else {
            const source = reports.get(report.mediaSourceId)
            if (source?.type === 'media-source' && source.kind === 'audio') {
              energy = counter(source.totalAudioEnergy)
            }
          }

          let statsId = ''
          if (typeof report.id === 'string') statsId = report.id
          snapshot.streams.push({
            id: statsId ? `${id}:${documentId}:${statsId}` : `${id}:`,
            direction: inbound ? 'inbound' : 'outbound',
            kind: report.kind,
            bytes: counter(inbound ? report.bytesReceived : report.bytesSent),
            frames,
            energy,
          })
        }
      }
      return snapshot
    }
  })
}

export async function readMediaSnapshot(page) {
  return within(
    page.evaluate(async () => {
      if (typeof window.__meetMediaSnapshot !== 'function') {
        throw new Error('WebRTC media probe is not installed')
      }
      return window.__meetMediaSnapshot()
    }),
    5000
  )
}

function isCounter(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function isOptionalCounter(value) {
  return value == null || isCounter(value)
}

function connectionId(streamId) {
  return streamId.slice(0, streamId.indexOf(':'))
}

function indexSnapshot(snapshot) {
  if (
    !snapshot ||
    !Array.isArray(snapshot.connections) ||
    !Array.isArray(snapshot.streams)
  ) {
    return null
  }
  const connections = new Map()
  const states = new Set([
    'new',
    'connecting',
    'connected',
    'disconnected',
    'failed',
    'closed',
  ])
  for (const connection of snapshot.connections) {
    if (
      !connection ||
      typeof connection.id !== 'string' ||
      !/^pc-[1-9]\d*$/.test(connection.id)
    ) {
      return null
    }
    if (!states.has(connection.state) || connections.has(connection.id))
      return null
    connections.set(connection.id, connection.state)
  }

  const streams = new Map()
  for (const stream of snapshot.streams) {
    if (
      !stream ||
      typeof stream.id !== 'string' ||
      !/^pc-[1-9]\d*:.+$/.test(stream.id)
    ) {
      return null
    }
    if (streams.has(stream.id) || !connections.has(connectionId(stream.id)))
      return null
    if (stream.direction !== 'inbound' && stream.direction !== 'outbound')
      return null
    if (stream.kind !== 'audio' && stream.kind !== 'video') return null
    if (!Number.isSafeInteger(stream.bytes) || stream.bytes < 0) return null
    if (!isOptionalCounter(stream.frames) || !isOptionalCounter(stream.energy))
      return null
    if (
      stream.kind === 'video' &&
      (!Number.isSafeInteger(stream.frames) || stream.frames < 0)
    ) {
      return null
    }
    if (
      stream.kind === 'audio' &&
      stream.direction === 'inbound' &&
      !isCounter(stream.energy)
    ) {
      return null
    }
    streams.set(stream.id, stream)
  }
  return { connections, streams }
}

/** No aggregation across streams or PCs: all four media paths need a baseline. */
export function hasBidirectionalMediaProgress(before, after) {
  const previous = indexSnapshot(before)
  const current = indexSnapshot(after)
  if (!previous || !current) return false

  const progressing = new Set()
  for (const stream of current.streams.values()) {
    const baseline = previous.streams.get(stream.id)
    if (!baseline) continue
    if (
      baseline.kind !== stream.kind ||
      baseline.direction !== stream.direction
    )
      return false
    for (const field of ['bytes', 'frames', 'energy']) {
      if (
        baseline[field] != null &&
        stream[field] != null &&
        stream[field] < baseline[field]
      ) {
        return false
      }
    }

    const pcId = connectionId(stream.id)
    if (
      previous.connections.get(pcId) !== 'connected' ||
      current.connections.get(pcId) !== 'connected'
    ) {
      continue
    }
    if (stream.bytes <= baseline.bytes) continue
    if (stream.kind === 'video' && stream.frames <= baseline.frames) continue
    if (
      stream.kind === 'audio' &&
      stream.direction === 'inbound' &&
      stream.energy <= baseline.energy
    ) {
      continue
    }
    progressing.add(`${stream.direction}:${stream.kind}`)
  }
  return [
    'inbound:audio',
    'inbound:video',
    'outbound:audio',
    'outbound:video',
  ].every((path) => progressing.has(path))
}
