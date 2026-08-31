import '@livekit/components-styles'
import { ReactNode, useEffect, useState } from 'react'
import { useLocation, useParams } from 'wouter'
import { ErrorScreen } from '@/components/ErrorScreen'
import { UserAware } from '@/features/auth/components/UserAware'
import { useUser } from '@/features/auth/api/useUser'
import { Conference } from '../components/Conference'
import { Join } from '../components/Join'
import { Permissions } from '../components/Permissions'
import { SilentMicDialog } from '../components/SilentMicDialog'
import { useKeyboardShortcuts } from '@/features/shortcuts/useKeyboardShortcuts'
import {
  isMastraoRoomId,
  isRoomValid,
  normalizeRoomId,
} from '@/features/rooms/utils/isRoomValid'
import { useConfig } from '@/api/useConfig.ts'
import { LogLevel, setLogLevel } from 'livekit-client'
import { useWatchDeviceAvailability } from '@/features/rooms/hooks/useWatchDeviceAvailability'
import { useRoomPageTitle } from '@/features/rooms/livekit/hooks/useRoomPageTitle'
import { MeetingLifecycleProvider } from '../contexts/MeetingLifecycleProvider'

const BaseRoom = ({ children }: { children: ReactNode }) => {
  return (
    <UserAware>
      <Permissions />
      <SilentMicDialog />
      {children}
    </UserAware>
  )
}

const Room = () => {
  const { isLoggedIn } = useUser()
  const [hasSubmittedEntry, setHasSubmittedEntry] = useState(false)

  const { roomId } = useParams()
  const [location, setLocation] = useLocation()
  const isMastraoRoom = roomId ? isMastraoRoomId(roomId) : false
  const initialRoomData = isMastraoRoom ? undefined : history.state?.initialRoomData
  const mode =
    isLoggedIn && !isMastraoRoom && history.state?.create ? 'create' : 'join'
  const skipJoinScreen = isLoggedIn && !isMastraoRoom && mode === 'create'

  const { data } = useConfig()

  useRoomPageTitle(roomId)

  useEffect(() => {
    const shouldSilenceLogs = data?.silence_livekit_debug_logs || false
    setLogLevel(shouldSilenceLogs ? LogLevel.silent : LogLevel.debug)
  }, [data?.silence_livekit_debug_logs])

  useKeyboardShortcuts()
  useWatchDeviceAvailability()

  const clearRouterState = () => {
    if (window?.history?.state) {
      window.history.replaceState({}, '')
    }
  }

  useEffect(() => {
    window.addEventListener('beforeunload', clearRouterState)
    return () => {
      window.removeEventListener('beforeunload', clearRouterState)
    }
  }, [])

  useEffect(() => {
    if (roomId && !isRoomValid(roomId)) {
      setLocation(normalizeRoomId(roomId))
    }
  }, [roomId, setLocation, location])

  if (!roomId) {
    return <ErrorScreen />
  }

  return (
    <BaseRoom>
      <MeetingLifecycleProvider key={roomId} roomId={roomId}>
        {!hasSubmittedEntry && !skipJoinScreen ? (
          <Join enterRoom={() => setHasSubmittedEntry(true)} roomId={roomId} />
        ) : (
          <Conference
            initialRoomData={initialRoomData}
            roomId={roomId}
            mode={mode}
          />
        )}
      </MeetingLifecycleProvider>
    </BaseRoom>
  )
}

export default Room
