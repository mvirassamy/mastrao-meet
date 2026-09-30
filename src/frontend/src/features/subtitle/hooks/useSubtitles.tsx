import { useSnapshot } from 'valtio'
import { layoutStore } from '@/stores/layout'
import { useRoomContext } from '@livekit/components-react'
import { useEffect } from 'react'
import { RoomEvent } from 'livekit-client'
import { useLiveTranscription } from '../store/liveTranscriptionContext'

export const useSubtitles = () => {
  const layoutSnap = useSnapshot(layoutStore)

  const room = useRoomContext()
  const { ensureSubtitleStarted, subtitleStartStatus } = useLiveTranscription()

  const toggleSubtitles = async () => {
    if (!layoutSnap.showSubtitles) {
      await ensureSubtitleStarted()
    }

    layoutStore.showSubtitles = !layoutSnap.showSubtitles
  }

  useEffect(() => {
    if (!room) return

    const closeSubtitles = () => {
      layoutStore.showSubtitles = false
    }
    room.on(RoomEvent.Disconnected, closeSubtitles)
    return () => {
      room.off(RoomEvent.Disconnected, closeSubtitles)
    }
  }, [room])

  return {
    areSubtitlesOpen: layoutSnap.showSubtitles,
    toggleSubtitles,
    areSubtitlesPending: subtitleStartStatus === 'pending',
  }
}
