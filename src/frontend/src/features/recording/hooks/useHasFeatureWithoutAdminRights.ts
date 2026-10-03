import type { RecordingMode } from '../types'
import { useIsRecordingModeEnabled } from './useIsRecordingModeEnabled'
import { useIsAdminOrOwner } from '@/features/rooms/livekit/hooks/useIsAdminOrOwner'

export const useHasFeatureWithoutAdminRights = (mode: RecordingMode) => {
  const isRecordingModeEnabled = useIsRecordingModeEnabled(mode)
  const isAdminOrOwner = useIsAdminOrOwner()

  return isRecordingModeEnabled && !isAdminOrOwner
}
