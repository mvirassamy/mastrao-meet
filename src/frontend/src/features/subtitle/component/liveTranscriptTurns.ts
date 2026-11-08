import type { LiveTranscriptionSegment } from '../store/liveTranscriptionTypes'

const PARAGRAPH_PAUSE_MS = 5_000
const OVERLAPPING_SPEECH_GRACE_MS = 1_500
const LEADING_PUNCTUATION = /^[,.!?;:…)]/
const TRAILING_JOINER = /['’(-]$/

const segmentSeparator = (
  previousText: string,
  nextText: string,
  pause: number
): string => {
  if (
    LEADING_PUNCTUATION.test(nextText) ||
    TRAILING_JOINER.test(previousText)
  ) {
    return ''
  }
  if (pause >= PARAGRAPH_PAUSE_MS) {
    return '\n\n'
  }
  return ' '
}

const transcriptSpeakerKey = (segment: LiveTranscriptionSegment): string =>
  segment.participantIdentity

const appendSegmentText = (
  turn: LiveTranscriptionSegment,
  segment: LiveTranscriptionSegment,
  pause: number
) => {
  const nextText = segment.text.trim()
  const previousText = turn.text.trim()
  turn.text = `${previousText}${segmentSeparator(previousText, nextText, pause)}${nextText}`
  turn.state = segment.state
  turn.sequence = segment.sequence
  turn.revision = segment.revision
  turn.receivedAt = segment.receivedAt
  turn.endTime = segment.endTime
}

/**
 * Build readable speaker turns without changing the ordered transcript state.
 *
 * A speaker keeps one visible turn across pauses and provider speech-window
 * restarts. Long pauses become paragraphs. Another established speaker starts
 * the participant's next turn, while rapidly interleaved microphone fragments
 * remain grouped as overlapping speech.
 */
export const groupLiveTranscriptSpeakerTurns = (
  segments: LiveTranscriptionSegment[]
): LiveTranscriptionSegment[] => {
  const turns: LiveTranscriptionSegment[] = []
  const activeTurnBySpeaker = new Map<string, LiveTranscriptionSegment>()

  for (const segment of segments) {
    const speakerKey = transcriptSpeakerKey(segment)
    const activeTurn = activeTurnBySpeaker.get(speakerKey)
    const pause = activeTurn ? segment.receivedAt - activeTurn.receivedAt : 0
    const interruptedByAnotherSpeaker = Array.from(
      activeTurnBySpeaker.entries()
    ).some(
      ([otherSpeakerKey, otherTurn]) =>
        otherSpeakerKey !== speakerKey &&
        otherTurn.receivedAt > (activeTurn?.receivedAt ?? segment.receivedAt) &&
        pause >= OVERLAPPING_SPEECH_GRACE_MS
    )
    const canContinueTurn =
      activeTurn !== undefined && pause >= 0 && !interruptedByAnotherSpeaker

    if (!canContinueTurn) {
      const nextTurn = { ...segment }
      turns.push(nextTurn)
      activeTurnBySpeaker.set(speakerKey, nextTurn)
      continue
    }

    appendSegmentText(activeTurn, segment, pause)
  }

  return turns
}
