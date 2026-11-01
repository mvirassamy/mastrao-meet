import { useSnapshot } from 'valtio'
import { layoutStore } from '@/stores/layout'
import { useRoomContext } from '@livekit/components-react'
import { useCallback, useEffect } from 'react'
import { RoomEvent } from 'livekit-client'

export const useSubtitles = () => {
  const layoutSnap = useSnapshot(layoutStore)
  const room = useRoomContext()

  const toggleSubtitles = useCallback(() => {
    layoutStore.showSubtitles = !layoutSnap.showSubtitles
  }, [layoutSnap.showSubtitles])

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
    areSubtitlesPending: false,
  }
}
