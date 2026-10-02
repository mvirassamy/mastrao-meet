import type { Participant } from 'livekit-client'
import { getParticipantName } from '@/features/rooms/utils/getParticipantName'
import { getParticipantColor } from '@/features/rooms/utils/getParticipantColor'

export type SpeakerPresentation = {
  label: string
  color: string
  identity?: string
}

const MAX_CACHED_ROOMS = 20
const MAX_CACHED_SPEAKERS = 500
const roomCaches = new Map<string, Map<string, SpeakerPresentation>>()

const retainRoom = (
  roomId: string,
  speakers: Map<string, SpeakerPresentation>
) => {
  while (speakers.size > MAX_CACHED_SPEAKERS) {
    speakers.delete(speakers.keys().next().value!)
  }
  roomCaches.delete(roomId)
  roomCaches.set(roomId, speakers)
  while (roomCaches.size > MAX_CACHED_ROOMS) {
    roomCaches.delete(roomCaches.keys().next().value!)
  }
}

const SPEAKER_CACHE_PREFIX = 'mastrao-live-transcript-speakers-v1'

export const readKnownSpeakers = (roomId: string) => {
  const cached = roomCaches.get(roomId)
  if (cached) return cached
  const speakers = new Map<string, SpeakerPresentation>()
  if (!roomId || typeof window === 'undefined') return speakers

  try {
    const stored = JSON.parse(
      window.sessionStorage.getItem(`${SPEAKER_CACHE_PREFIX}:${roomId}`) ?? '[]'
    ) as unknown
    if (!Array.isArray(stored)) throw new Error('Invalid speaker cache')

    stored.forEach((entry) => {
      if (
        Array.isArray(entry) &&
        typeof entry[0] === 'string' &&
        typeof entry[1]?.label === 'string' &&
        typeof entry[1]?.color === 'string'
      ) {
        speakers.set(entry[0], {
          label: entry[1].label,
          color: entry[1].color,
          identity:
            typeof entry[1].identity === 'string'
              ? entry[1].identity
              : entry[0],
        })
      }
    })
  } catch {
    // A malformed or unavailable browser cache must not break the transcript.
  }
  retainRoom(roomId, speakers)
  return speakers
}

export const writeKnownSpeakers = (
  roomId: string,
  speakers: Map<string, SpeakerPresentation>
) => {
  if (!roomId) return
  retainRoom(roomId, speakers)
  if (typeof window === 'undefined') return

  try {
    window.sessionStorage.setItem(
      `${SPEAKER_CACHE_PREFIX}:${roomId}`,
      JSON.stringify(Array.from(speakers.entries()))
    )
  } catch {
    // The room cache stays available in memory when storage is unavailable.
  }
}

export const rememberParticipant = (
  speakers: Map<string, SpeakerPresentation>,
  participant: Participant,
  alias = participant.identity
) => {
  if (!participant.identity) return false
  const presentation = {
    identity: participant.identity,
    label: getParticipantName(participant),
    color: getParticipantColor(participant),
  }
  let changed = false
  const identities = new Set([participant.identity, alias])
  speakers.forEach((speaker, key) => {
    if (speaker.identity === participant.identity) identities.add(key)
  })
  identities.forEach((identity) => {
    const previous = speakers.get(identity)
    if (
      previous?.label === presentation.label &&
      previous?.color === presentation.color &&
      previous?.identity === presentation.identity
    )
      return
    speakers.delete(identity)
    speakers.set(identity, presentation)
    changed = true
  })
  return changed
}
