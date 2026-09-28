import { describe, expect, it } from 'vitest'
import {
  MAX_ACTIVE_TRANSCRIPTION_SEGMENTS,
  MAX_FINAL_TRANSCRIPTION_SEGMENTS,
  type LiveTranscriptionSegmentInput,
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

describe('liveTranscriptionReducer', () => {
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

  it('exposes degraded gaps and purges on room reset', () => {
    let state = ingest(createLiveTranscriptionState('room-1'), [segment()])
    state = liveTranscriptionReducer(state, {
      type: 'gap',
      gap: { id: 'gap-1', reason: 'reconnect', receivedAt: 10 },
    })

    expect(state.status).toBe('degraded')
    expect(state.gaps).toHaveLength(1)

    const reset = liveTranscriptionReducer(state, {
      type: 'reset',
      roomId: 'room-2',
    })
    expect(reset).toMatchObject({ roomId: 'room-2', segments: [], gaps: [] })
  })
})
