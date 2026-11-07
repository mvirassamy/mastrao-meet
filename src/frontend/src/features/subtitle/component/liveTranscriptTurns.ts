import type { LiveTranscriptionSegment } from '../store/liveTranscriptionTypes'

const PARAGRAPH_PAUSE_MS = 5_000
const LEADING_PUNCTUATION = /^[,.!?;:…)]/
const TRAILING_JOINER = /['’(-]$/

const segmentSeparator = (previousText: string, nextText: string): string => {
  if (
    LEADING_PUNCTUATION.test(nextText) ||
    TRAILING_JOINER.test(previousText)
  ) {
    return ''
  }
  return ' '
}

const transcriptSourceKey = (segment: LiveTranscriptionSegment): string =>
  JSON.stringify([segment.participantIdentity, segment.trackSid, segment.legId])

const appendSegmentText = (
  turn: LiveTranscriptionSegment,
  segment: LiveTranscriptionSegment
) => {
  const nextText = segment.text.trim()
  const previousText = turn.text.trim()
  turn.text = `${previousText}${segmentSeparator(previousText, nextText)}${nextText}`
  turn.state = segment.state
  turn.sequence = segment.sequence
  turn.revision = segment.revision
  turn.receivedAt = segment.receivedAt
  turn.endTime = segment.endTime
}

/**
 * Build readable speaker turns without changing the ordered transcript state.
 *
 * A speaker can keep the same visible turn while another speaker interjects.
 * Reconnects, track changes and pauses of five seconds or more always start a
 * new turn.
 */
export const groupLiveTranscriptSpeakerTurns = (
  segments: LiveTranscriptionSegment[]
): LiveTranscriptionSegment[] => {
  const turns: LiveTranscriptionSegment[] = []
  const activeTurnBySource = new Map<string, LiveTranscriptionSegment>()

  for (const segment of segments) {
    const sourceKey = transcriptSourceKey(segment)
    const activeTurn = activeTurnBySource.get(sourceKey)
    const pause = activeTurn
      ? segment.receivedAt - activeTurn.receivedAt
      : PARAGRAPH_PAUSE_MS
    const canContinueTurn =
      activeTurn !== undefined && pause >= 0 && pause < PARAGRAPH_PAUSE_MS

    if (!canContinueTurn) {
      const nextTurn = { ...segment }
      turns.push(nextTurn)
      activeTurnBySource.set(sourceKey, nextTurn)
      continue
    }

    appendSegmentText(activeTurn, segment)
  }

  return turns
}
