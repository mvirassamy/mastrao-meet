import type { LiveTranscriptionSegment } from '../store/liveTranscriptionTypes'

const PARAGRAPH_PAUSE_MS = 5_000

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
    const separator = pause >= PARAGRAPH_PAUSE_MS ? '\n\n' : ' '
    previous.text = `${previous.text.trim()}${separator}${segment.text.trim()}`
    previous.state = segment.state
    previous.sequence = segment.sequence
    previous.revision = segment.revision
    previous.receivedAt = segment.receivedAt
    previous.endTime = segment.endTime
    previous.key = `${previous.key}:${segment.key}`
    return turns
  }, [])
