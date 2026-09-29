import { describe, expect, it } from 'vitest'
import {
  MAX_ACTIVE_TRANSCRIPTION_SEGMENTS,
  MAX_FINAL_TRANSCRIPTION_SEGMENTS,
  type LiveTranscriptionSegmentInput,
  type LiveTranscriptionStatePacket,
} from './liveTranscriptionTypes'
import {
  createLiveTranscriptionState,
  getLiveTranscriptionSegmentKey,
  liveTranscriptionReducer,
} from './liveTranscriptionReducer'

const segment = (
  overrides: Partial<LiveTranscriptionSegmentInput> = {}
): LiveTranscriptionSegmentInput => ({
  participantIdentity: 'alice',
  trackSid: 'TR_alice',
  legId: 'leg-1',
  itemId: 'item-1',
  state: 'interim',
  text: 'bon',
  ...overrides,
})

const ingest = (
  state: ReturnType<typeof createLiveTranscriptionState>,
  segments: LiveTranscriptionSegmentInput[]
) =>
  liveTranscriptionReducer(state, {
    type: 'ingest',
    event: { type: 'segments', segments },
  })

const statePacket = (
  overrides: Partial<LiveTranscriptionStatePacket> = {}
): LiveTranscriptionStatePacket => ({
  schemaVersion: 1,
  roomSid: 'RM_room-1',
  state: 'live',
  stateVersion: 1,
  eventId: 'event-1',
  occurredAt: '2026-09-29T00:00:00.000Z',
  sessionId: 'session-1',
  reason: null,
  ...overrides,
})

describe('liveTranscriptionReducer', () => {
  it.each(['inactive', 'stopped', 'live'] as const)(
    'keeps collective status %s separate from media reconnection',
    (status) => {
      let state = liveTranscriptionReducer(
        createLiveTranscriptionState('room-1'),
        { type: 'status', status }
      )
      state = liveTranscriptionReducer(state, {
        type: 'connection',
        status: 'reconnecting',
      })
      state = liveTranscriptionReducer(state, {
        type: 'connection',
        status: 'connected',
      })

      expect(state.status).toBe(status)
      expect(state.connectionStatus).toBe('connected')
    }
  )

  it('applies interim revisions and replaces them with an immutable final', () => {
    let state = createLiveTranscriptionState('room-1')
    state = ingest(state, [segment({ revision: 0, text: 'bon' })])
    state = ingest(state, [segment({ revision: 1, text: 'bonjour' })])
    state = ingest(state, [
      segment({ revision: 2, state: 'final', text: 'bonjour' }),
    ])

    expect(state.segments).toHaveLength(1)
    expect(state.segments[0]).toMatchObject({
      text: 'bonjour',
      state: 'final',
      revision: 2,
    })

    const finalState = ingest(state, [
      segment({ revision: 99, state: 'final', text: 'bonjour corrigé' }),
    ])
    expect(finalState).toBe(state)
  })

  it('rejects a late interim after finalization', () => {
    let state = createLiveTranscriptionState('room-1')
    state = ingest(state, [
      segment({ state: 'final', revision: 1, text: 'final' }),
    ])

    const nextState = ingest(state, [
      segment({ revision: 2, text: 'late interim' }),
    ])

    expect(nextState).toBe(state)
    expect(nextState.segments[0].text).toBe('final')
  })

  it('orders finals by sequence rather than arrival order', () => {
    let state = createLiveTranscriptionState('room-1')
    state = ingest(state, [
      segment({ itemId: 'second', sequence: 2, state: 'final', text: 'deux' }),
      segment({ itemId: 'first', sequence: 1, state: 'final', text: 'un' }),
    ])

    expect(state.segments.map(({ text }) => text)).toEqual(['un', 'deux'])
  })

  it('keeps equal item ids separate across tracks and processes all segments', () => {
    let state = createLiveTranscriptionState('room-1')
    state = ingest(state, [
      segment({ trackSid: 'TR_alice', itemId: 'same', text: 'Alice' }),
      segment({ trackSid: 'TR_bob', itemId: 'same', text: 'Bob' }),
      segment({ itemId: 'third', text: 'Troisième' }),
    ])

    expect(state.segments.map(({ text }) => text)).toEqual([
      'Alice',
      'Bob',
      'Troisième',
    ])
    expect(
      state.segments.map((current) => getLiveTranscriptionSegmentKey(current))
    ).toHaveLength(3)
  })

  it('keeps the store bounded while retaining active interims', () => {
    const finals = Array.from(
      { length: MAX_FINAL_TRANSCRIPTION_SEGMENTS + 1 },
      (_, sequence) =>
        segment({
          itemId: `final-${sequence}`,
          sequence,
          state: 'final',
          text: `final-${sequence}`,
        })
    )
    let state = ingest(createLiveTranscriptionState('room-1'), finals)
    state = ingest(state, [
      segment({
        itemId: 'active',
        sequence: MAX_FINAL_TRANSCRIPTION_SEGMENTS + 1,
      }),
    ])

    expect(state.truncated).toBe(true)
    expect(
      state.segments.filter(({ state }) => state === 'final')
    ).toHaveLength(MAX_FINAL_TRANSCRIPTION_SEGMENTS)
    expect(state.segments.some(({ itemId }) => itemId === 'active')).toBe(true)
    expect(state.segments.some(({ itemId }) => itemId === 'final-0')).toBe(
      false
    )

    const activeSegments = Array.from(
      { length: MAX_ACTIVE_TRANSCRIPTION_SEGMENTS + 1 },
      (_, sequence) =>
        segment({
          itemId: `active-${sequence}`,
          sequence,
          text: `active-${sequence}`,
        })
    )
    const activeState = ingest(
      createLiveTranscriptionState('room-1'),
      activeSegments
    )
    expect(activeState.truncated).toBe(true)
    expect(
      activeState.segments.filter(({ state }) => state === 'interim')
    ).toHaveLength(MAX_ACTIVE_TRANSCRIPTION_SEGMENTS)
  })

  it('exposes gaps without changing public state and purges on room reset', () => {
    let state = ingest(createLiveTranscriptionState('room-1'), [segment()])
    state = liveTranscriptionReducer(state, {
      type: 'gap',
      gap: { id: 'gap-1', reason: 'reconnect', receivedAt: 10 },
    })

    expect(state.status).toBe('unknown')
    expect(state.gaps).toHaveLength(1)

    const reset = liveTranscriptionReducer(state, {
      type: 'reset',
      roomId: 'room-2',
    })
    expect(reset).toMatchObject({ roomId: 'room-2', segments: [], gaps: [] })
  })

  it('orders state packets, ignores duplicates, and requests REST on a gap', () => {
    let state = createLiveTranscriptionState('room-1')
    state = liveTranscriptionReducer(state, {
      type: 'state-packet',
      packet: statePacket(),
    })
    const accepted = state

    const duplicate = liveTranscriptionReducer(state, {
      type: 'state-packet',
      packet: statePacket(),
    })
    expect(duplicate).toBe(accepted)

    const stale = liveTranscriptionReducer(state, {
      type: 'state-packet',
      packet: statePacket({ stateVersion: 0, eventId: 'event-0' }),
    })
    expect(stale).toBe(accepted)

    const gap = liveTranscriptionReducer(state, {
      type: 'state-packet',
      packet: statePacket({ stateVersion: 3, eventId: 'event-3' }),
    })
    expect(gap).toMatchObject({
      status: 'live',
      stateVersion: 1,
      resyncStatus: 'pending',
      resyncRequestId: 1,
    })
  })

  it('clears room state when an accepted packet belongs to a new room SID', () => {
    let state = createLiveTranscriptionState('room-1')
    state = liveTranscriptionReducer(state, {
      type: 'ingest',
      event: { type: 'segments', segments: [segment({ text: 'ancien' })] },
    })
    state = liveTranscriptionReducer(state, {
      type: 'state-packet',
      packet: statePacket({ roomSid: 'RM_room-1', stateVersion: 1 }),
    })

    const changedRoom = liveTranscriptionReducer(state, {
      type: 'state-packet',
      packet: statePacket({
        roomSid: 'RM_room-2',
        stateVersion: 1,
        eventId: 'event-room-2',
      }),
    })

    expect(changedRoom).toMatchObject({
      roomId: 'room-1',
      roomSid: 'RM_room-2',
      stateVersion: 1,
      segments: [],
      gaps: [],
      status: 'live',
    })
  })

  it('keeps the reliable state when a snapshot is stale', () => {
    let state = liveTranscriptionReducer(
      createLiveTranscriptionState('room-1'),
      { type: 'state-packet', packet: statePacket() }
    )
    state = liveTranscriptionReducer(state, {
      type: 'snapshot',
      snapshot: {
        roomSid: 'RM_room-1',
        state: 'inactive',
        stateVersion: 0,
        sessionId: null,
        occurredAt: null,
        reason: null,
      },
    })

    expect(state).toMatchObject({
      status: 'live',
      roomSid: 'RM_room-1',
      stateVersion: 1,
      resyncStatus: 'idle',
    })
  })
})
