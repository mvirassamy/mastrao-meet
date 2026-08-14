import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRoomContext } from '@livekit/components-react'
import { RoomEvent } from 'livekit-client'
import { useRoomData } from '@/features/rooms/livekit/hooks/useRoomData'
import { useIsAdminOrOwner } from '@/features/rooms/livekit/hooks/useIsAdminOrOwner'
import { useEnterRoom } from '../api/enterRoom'
import {
  useListWaitingParticipants,
  type WaitingParticipant,
} from '../../participants/api/listWaitingParticipants'
import { decodeNotificationDataReceived } from '@/features/notifications/utils'
import { NotificationType } from '@/features/notifications/NotificationType'
import { reportError } from '@/features/analytics/telemetry'
import { useMeetingLifecycle } from '@/features/rooms/contexts/MeetingLifecycleContext'

export const POLL_INTERVAL_MS = 1000

export const useWaitingParticipants = () => {
  const [listEnabled, setListEnabled] = useState(true)
  const { isEnding } = useMeetingLifecycle()

  const roomData = useRoomData()
  const roomId = roomData?.id || '' // FIXME - bad practice

  const room = useRoomContext()
  const isAdminOrOwner = useIsAdminOrOwner()

  const handleDataReceived = useCallback((payload: Uint8Array) => {
    const notification = decodeNotificationDataReceived(payload)
    if (notification?.type === NotificationType.ParticipantWaiting) {
      setListEnabled(true)
    }
  }, [])

  useEffect(() => {
    if (isAdminOrOwner) {
      room.on(RoomEvent.DataReceived, handleDataReceived)
    }
    return () => {
      room.off(RoomEvent.DataReceived, handleDataReceived)
    }
  }, [isAdminOrOwner, room, handleDataReceived])

  const { data: waitingData, refetch: refetchWaiting } =
    useListWaitingParticipants(roomId, {
      retry: false,
      enabled: listEnabled && isAdminOrOwner && !isEnding,
      refetchInterval: POLL_INTERVAL_MS,
      refetchIntervalInBackground: true,
    })

  const waitingParticipants = useMemo(
    () => (isEnding ? [] : waitingData?.participants || []),
    [isEnding, waitingData]
  )

  useEffect(() => {
    if (!waitingParticipants.length) setListEnabled(false)
  }, [waitingParticipants])

  const { mutateAsync: enterRoom } = useEnterRoom()

  const handleParticipantEntry = async (
    participant: WaitingParticipant,
    allowEntry: boolean
  ) => {
    if (isEnding) return
    await enterRoom({
      roomId: roomId,
      allowEntry,
      participantId: participant.id,
    })
    await refetchWaiting()
  }

  const handleParticipantsEntry = async (
    allowEntry: boolean
  ): Promise<void> => {
    if (isEnding) return
    try {
      setListEnabled(false)

      await Promise.all(
        waitingParticipants.map((participant) =>
          enterRoom({
            roomId: roomId,
            allowEntry,
            participantId: participant.id,
          })
        )
      )

      await refetchWaiting()
    } catch (e) {
      reportError('generic_failure', e)
      setListEnabled(true)
    }
  }

  return {
    waitingParticipants,
    handleParticipantEntry,
    handleParticipantsEntry,
  }
}
