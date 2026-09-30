import { useSnapshot } from 'valtio'
import { layoutStore } from '@/stores/layout'
import { useStartSubtitle } from '../api/startSubtitle'
import { useRoomData } from '@/features/rooms/livekit/hooks/useRoomData'
import { useRoomContext } from '@livekit/components-react'
import { useCallback, useEffect } from 'react'
import { RoomEvent } from 'livekit-client'

export const useSubtitles = () => {
  const layoutSnap = useSnapshot(layoutStore)

  const room = useRoomContext()
  const apiRoomData = useRoomData()
  const { mutateAsync: startSubtitleRoom, isPending } = useStartSubtitle()

  const ensureSubtitlesStarted = useCallback(async () => {
    const livekit = apiRoomData?.livekit
    if (!livekit) return

    await startSubtitleRoom({
      id: livekit.room,
      token: livekit.token,
    })
  }, [apiRoomData?.livekit, startSubtitleRoom])

  const toggleSubtitles = useCallback(async () => {
    if (!layoutSnap.showSubtitles) await ensureSubtitlesStarted()

    layoutStore.showSubtitles = !layoutSnap.showSubtitles
  }, [ensureSubtitlesStarted, layoutSnap.showSubtitles])

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
    ensureSubtitlesStarted,
    toggleSubtitles,
    areSubtitlesPending: isPending,
  }
}
