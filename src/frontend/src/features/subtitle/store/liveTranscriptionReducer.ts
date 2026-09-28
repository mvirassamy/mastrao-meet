import {
  MAX_ACTIVE_TRANSCRIPTION_SEGMENTS,
  MAX_FINAL_TRANSCRIPTION_SEGMENTS,
  type LiveTranscriptionAction,
  type LiveTranscriptionSegment,
  type LiveTranscriptionSegmentInput,
  type LiveTranscriptionState,
} from './liveTranscriptionTypes'

export const createLiveTranscriptionState = (
  roomId: string
): LiveTranscriptionState => ({
  roomId,
  status: 'inactive',
  segments: [],
  gaps: [],
  truncated: false,
  nextSequence: 0,
})

export const getLiveTranscriptionSegmentKey = (
  segment: Pick<
    LiveTranscriptionSegmentInput,
    'participantIdentity' | 'trackSid' | 'legId' | 'itemId'
  >
) =>
  JSON.stringify([
    segment.participantIdentity,
    segment.trackSid,
    segment.legId,
    segment.itemId,
  ])

const sortSegments = (segments: LiveTranscriptionSegment[]) =>
  segments.slice().sort((left, right) => {
    if (left.sequence !== right.sequence) return left.sequence - right.sequence
    if (left.receivedAt !== right.receivedAt) {
      return left.receivedAt - right.receivedAt
    }
    return left.key.localeCompare(right.key)
  })

const trimFinalSegments = (state: LiveTranscriptionState) => {
  const finalCount = state.segments.filter(
    (segment) => segment.state === 'final'
  ).length
  if (finalCount <= MAX_FINAL_TRANSCRIPTION_SEGMENTS) return state

  const finalSegmentsToRemove = finalCount - MAX_FINAL_TRANSCRIPTION_SEGMENTS
  let removed = 0
  const segments = state.segments.filter((segment) => {
    if (segment.state !== 'final' || removed >= finalSegmentsToRemove)
      return true
    removed += 1
    return false
  })

  return { ...state, segments, truncated: true }
}

const trimActiveSegments = (state: LiveTranscriptionState) => {
  const activeCount = state.segments.filter(
    (segment) => segment.state === 'interim'
  ).length
  if (activeCount <= MAX_ACTIVE_TRANSCRIPTION_SEGMENTS) return state

  const activeSegmentsToRemove = activeCount - MAX_ACTIVE_TRANSCRIPTION_SEGMENTS
  let removed = 0
  const segments = state.segments.filter((segment) => {
    if (segment.state !== 'interim' || removed >= activeSegmentsToRemove)
      return true
    removed += 1
    return false
  })

  return { ...state, segments, truncated: true }
}

const materializeSegment = (
  state: LiveTranscriptionState,
  input: LiveTranscriptionSegmentInput,
  existing?: LiveTranscriptionSegment
): LiveTranscriptionSegment => {
  const sequence = existing?.sequence ?? input.sequence ?? state.nextSequence
  const revision = existing
    ? (input.revision ?? existing.revision + 1)
    : (input.revision ?? 0)
  return {
    ...input,
    key: getLiveTranscriptionSegmentKey(input),
    sequence,
    revision,
    receivedAt: input.receivedAt ?? Date.now(),
    metadataSource: input.metadataSource ?? 'envelope',
  }
}

const reduceSegment = (
  state: LiveTranscriptionState,
  input: LiveTranscriptionSegmentInput
): LiveTranscriptionState => {
  if (!input.participantIdentity || !input.itemId || !input.text.trim()) {
    return state
  }

  const key = getLiveTranscriptionSegmentKey(input)
  const existing = state.segments.find((segment) => segment.key === key)

  if (existing?.state === 'final') return state
  if (
    existing &&
    input.revision !== undefined &&
    input.revision <= existing.revision
  ) {
    return state
  }

  const nextSegment = materializeSegment(state, input, existing)
  const segments = existing
    ? state.segments.map((segment) =>
        segment.key === key ? nextSegment : segment
      )
    : [...state.segments, nextSegment]
  const nextState = trimActiveSegments(
    trimFinalSegments({
      ...state,
      segments: sortSegments(segments),
      nextSequence: Math.max(state.nextSequence, nextSegment.sequence + 1),
    })
  )
  return nextState
}

export const liveTranscriptionReducer = (
  state: LiveTranscriptionState,
  action: LiveTranscriptionAction
): LiveTranscriptionState => {
  switch (action.type) {
    case 'reset':
      return createLiveTranscriptionState(action.roomId)
    case 'status':
      return { ...state, status: action.status }
    case 'gap':
      if (state.gaps.some((gap) => gap.id === action.gap.id)) return state
      return { ...state, gaps: [...state.gaps, action.gap], status: 'degraded' }
    case 'ingest':
      if (action.event.type === 'status') {
        return { ...state, status: action.event.status }
      }
      if (action.event.type === 'gap') {
        return liveTranscriptionReducer(state, {
          type: 'gap',
          gap: action.event.gap,
        })
      }
      return action.event.segments.reduce(reduceSegment, state)
  }
}
