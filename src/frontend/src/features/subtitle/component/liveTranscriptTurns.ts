import type { LiveTranscriptionSegment } from '../store/liveTranscriptionTypes'

const PARAGRAPH_PAUSE_MS = 5_000
const LEADING_PUNCTUATION = /^[,.!?;:…)]/
const TRAILING_JOINER = /['’(-]$/

const segmentSeparator = (
  pause: number,
  previousText: string,
  nextText: string
): string => {
  if (pause >= PARAGRAPH_PAUSE_MS) {
    return '\n\n'
  }
  if (
    LEADING_PUNCTUATION.test(nextText) ||
    TRAILING_JOINER.test(previousText)
  ) {
    return ''
  }
  return ' '
}

export const groupConsecutiveSpeakerSegments = (
  segments: LiveTranscriptionSegment[]
): LiveTranscriptionSegment[] =>
  segments.reduce<LiveTranscriptionSegment[]>((turns, segment) => {
    const previous = turns.at(-1)
    const isSameMicrophone =
      previous?.participantIdentity === segment.participantIdentity &&
      previous.trackSid === segment.trackSid

    if (!previous || !isSameMicrophone) {
      turns.push({ ...segment })
      return turns
    }

    const pause = segment.receivedAt - previous.receivedAt
    const nextText = segment.text.trim()
    const previousText = previous.text.trim()
    previous.text = `${previousText}${segmentSeparator(pause, previousText, nextText)}${nextText}`
    previous.state = segment.state
    previous.sequence = segment.sequence
    previous.revision = segment.revision
    previous.receivedAt = segment.receivedAt
    previous.endTime = segment.endTime
    previous.key = `${previous.key}:${segment.key}`
    return turns
  }, [])
