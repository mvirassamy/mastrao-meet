import { useEffect, useState } from 'react'
import { RoomEvent, type Participant, type Room } from 'livekit-client'
import { getParticipantForTranscription } from './liveTranscriptionParticipants'
import type { LiveTranscriptionSegment } from './liveTranscriptionTypes'
import {
  readKnownSpeakers,
  rememberParticipant,
  writeKnownSpeakers,
} from './liveTranscriptSpeakerCache'

export const useLiveTranscriptSpeakers = (
  room: Room,
  roomId: string,
  segments: LiveTranscriptionSegment[]
) => {
  const [cache, setCache] = useState(() => ({
    roomId,
    speakers: readKnownSpeakers(roomId),
  }))

  useEffect(() => {
    let speakers = readKnownSpeakers(roomId)
    const rememberSpeakers = (participants: Participant[]) => {
      const next = new Map(speakers)
      let changed = false
      participants.forEach((participant) => {
        changed = rememberParticipant(next, participant) || changed
      })
      const identities = new Set(
        segments.map(({ participantIdentity }) => participantIdentity)
      )
      identities.forEach((participantIdentity) => {
        const participant = getParticipantForTranscription(
          room,
          participantIdentity
        )
        if (participant)
          changed =
            rememberParticipant(next, participant, participantIdentity) ||
            changed
      })
      if (changed) {
        writeKnownSpeakers(roomId, next)
        speakers = next
      }
      setCache((previous) =>
        previous.roomId === roomId && previous.speakers === speakers
          ? previous
          : { roomId, speakers }
      )
    }
    const rememberSpeaker = (participant: Participant) =>
      rememberSpeakers([participant])
    const rememberName = (_name: string, participant: Participant) =>
      rememberSpeaker(participant)
    const rememberAttributes = (
      _attributes: Record<string, string>,
      participant: Participant
    ) => rememberSpeaker(participant)
    room.on(RoomEvent.ParticipantConnected, rememberSpeaker)
    room.on(RoomEvent.ParticipantNameChanged, rememberName)
    room.on(RoomEvent.ParticipantAttributesChanged, rememberAttributes)
    rememberSpeakers([
      room.localParticipant,
      ...room.remoteParticipants.values(),
    ])
    return () => {
      room.off(RoomEvent.ParticipantConnected, rememberSpeaker)
      room.off(RoomEvent.ParticipantNameChanged, rememberName)
      room.off(RoomEvent.ParticipantAttributesChanged, rememberAttributes)
    }
  }, [room, roomId, segments])

  return cache.roomId === roomId ? cache.speakers : undefined
}
