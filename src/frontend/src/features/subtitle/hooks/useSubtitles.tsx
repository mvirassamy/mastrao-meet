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
  const livekitRoom = apiRoomData?.livekit?.room
  const livekitToken = apiRoomData?.livekit?.token

  const ensureSubtitlesStarted = useCallback(async () => {
    if (!livekitRoom || !livekitToken) return

    await startSubtitleRoom({
      id: livekitRoom,
      token: livekitToken,
    })
  }, [livekitRoom, livekitToken, startSubtitleRoom])

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
