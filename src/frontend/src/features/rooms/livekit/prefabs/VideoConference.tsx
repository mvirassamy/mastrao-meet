import { isWeb } from '@livekit/components-core'
import { Track } from 'livekit-client'
import React, { useRef, useState } from 'react'
import {
  ConnectionStateToast,
  RoomAudioRenderer,
} from '@livekit/components-react'

import { ControlBar } from './ControlBar/ControlBar'
import { SidePanel } from '../components/SidePanel'
import { RecordingProvider } from '@/features/recording'
import { ScreenShareErrorModal } from '../components/ScreenShareErrorModal'
import { ConnectionObserver } from '../components/ConnectionObserver'
import { reportError } from '@/features/analytics/telemetry'
import { MediaStateObserver } from '../components/MediaStateObserver'
import { RoomMetadataSynchronizer } from '../components/RoomMetadataSynchronizer'
import { useNoiseReduction } from '../hooks/useNoiseReduction'
import { VideoResolutionSubscription } from '../components/VideoResolutionSubscription'
import { SettingsDialogProvider } from '@/features/settings/components/SettingsDialogProvider'
import { IsIdleDisconnectModal } from '../components/IsIdleDisconnectModal'
import { ReactionPortals } from '@/features/reactions/components/ReactionPortals'
import { RoomContentArea } from '@/features/layout/components/RoomContentArea'
import { usePictureInPicture } from '@/features/pip/hooks/usePictureInPicture'
import { PipRoomPlaceholder } from '@/features/pip/components/PipRoomPlaceholder'
import { StageLayout } from '@/features/layout/components/StageLayout'
import { PinAnnouncer } from '@/features/layout/components/PinAnnouncer'
import { ChatProvider } from '@/features/chat/components/ChatProvider'
import { SyncDevicePreferences } from '@/features/rooms/livekit/components/SyncDevicePreferences'
import { RoomSilentMicDetector } from '@/features/rooms/components/SilentMicDetector'
import { useMeetingLifecycle } from '@/features/rooms/contexts/MeetingLifecycleContext'
import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import { Button } from '@/primitives'
import type { ApiRoom } from '@/features/rooms/api/ApiRoom'
import {
  decideRecording,
  stopRecording,
} from '@/features/rooms/api/recordingConsent'

/**
 * @public
 */
export interface VideoConferenceProps extends React.HTMLAttributes<HTMLDivElement> {
  /** @alpha */
  SettingsComponent?: React.ComponentType
  roomId: string
  canEnd?: boolean
  recording?: ApiRoom['recording']
  onRecordingChanged?: () => Promise<unknown>
}

/**
 * The `VideoConference` ready-made component is your drop-in solution for a classic video conferencing application.
 * It provides functionality such as focusing on one participant, grid view with pagination to handle large numbers
 * of participants, basic non-persistent chat, screen sharing, and more.
 *
 * @remarks
 * The component is implemented with other LiveKit components like `FocusContextProvider`,
 * `GridLayout`, `ControlBar`, `FocusLayoutContainer` and `FocusLayout`.
 * You can use this components as a starting point for your own custom video conferencing application.
 *
 * @example
 * ```tsx
 * <LiveKitRoom>
 *   <VideoConference />
 * <LiveKitRoom>
 * ```
 * @public
 */
export function VideoConference({
  roomId,
  canEnd,
  recording,
  onRecordingChanged,
  ...props
}: VideoConferenceProps) {
  useNoiseReduction()
  const { isEnding } = useMeetingLifecycle()
  const { t } = useTranslation('rooms', {
    keyPrefix: 'controls.endMeeting',
  })
  const { t: tRecording } = useTranslation('rooms', {
    keyPrefix: 'recordingConsent',
  })

  const { isOpen: isPictureInPictureOpen } = usePictureInPicture()

  const [isShareErrorVisible, setIsShareErrorVisible] = useState(false)
  const [isWithdrawing, setIsWithdrawing] = useState(false)
  const [withdrawFailed, setWithdrawFailed] = useState(false)
  const withdrawalIds = useRef(crypto.randomUUID().replaceAll('-', ''))

  const withdraw = async () => {
    setIsWithdrawing(true)
    setWithdrawFailed(false)
    try {
      if (canEnd) {
        await stopRecording(roomId, 'host', `stop_${withdrawalIds.current}`)
      } else {
        await decideRecording(
          roomId,
          'withdrawn',
          `withdrawal_${withdrawalIds.current}`
        )
      }
      await onRecordingChanged?.()
    } catch {
      setWithdrawFailed(true)
    } finally {
      setIsWithdrawing(false)
    }
  }

  return (
    <>
      <RoomMetadataSynchronizer />
      <ConnectionObserver />
      <SyncDevicePreferences />
      <RoomSilentMicDetector />
      {isEnding && (
        <div
          role="status"
          aria-live="polite"
          className={css({
            position: 'absolute',
            top: '1rem',
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 1001,
            padding: '0.75rem 1rem',
            borderRadius: 'md',
            backgroundColor: 'primaryDark.100',
            color: 'white',
          })}
        >
          {t('status')}
        </div>
      )}
      {recording?.mode === 'recorded' &&
        ['starting', 'active', 'stopping'].includes(
          recording.recording_state ?? ''
        ) && (
          <div
            role="status"
            aria-live="polite"
            className={css({
              position: 'absolute',
              top: '1rem',
              right: '1rem',
              zIndex: 1001,
              display: 'flex',
              alignItems: 'center',
              gap: '0.75rem',
              padding: '0.5rem 0.75rem',
              borderRadius: 'md',
              backgroundColor: 'primaryDark.100',
              color: 'white',
            })}
          >
            {withdrawFailed
              ? tRecording('withdrawError')
              : tRecording(
                  recording.recording_state === 'stopping'
                    ? 'stopping'
                    : recording.recording_state === 'starting'
                      ? 'starting'
                      : 'active'
                )}
            {recording.decision === 'accepted' &&
              recording.recording_state !== 'stopping' && (
                <Button
                  size="sm"
                  variant="danger"
                  isDisabled={isWithdrawing}
                  onPress={withdraw}
                >
                  {tRecording(canEnd ? 'stop' : 'withdraw')}
                </Button>
              )}
          </div>
        )}
      <MediaStateObserver />
      <ChatProvider />
      <VideoResolutionSubscription />
      <div
        className="lk-video-conference"
        {...props}
        style={{
          overflowX: 'hidden',
        }}
      >
        {isWeb() && (
          <>
            <ScreenShareErrorModal
              isOpen={isShareErrorVisible}
              onClose={() => setIsShareErrorVisible(false)}
            />
            <IsIdleDisconnectModal />
            <PinAnnouncer />
            <RoomContentArea>
              {isPictureInPictureOpen ? (
                <PipRoomPlaceholder />
              ) : (
                <StageLayout />
              )}
            </RoomContentArea>
            <ControlBar
              roomId={roomId}
              canEnd={canEnd}
              onDeviceError={(e) => {
                reportError('device_switch_failure', e.error, {
                  at: 'ControlBar.onDeviceError',
                  source: e.source,
                })
                if (
                  e.source == Track.Source.ScreenShare &&
                  e.error.toString() ==
                    'NotAllowedError: Permission denied by system'
                ) {
                  setIsShareErrorVisible(true)
                }
              }}
            />
            <SidePanel />
          </>
        )}
        <RoomAudioRenderer />
        <ConnectionStateToast />
        <RecordingProvider />
        <SettingsDialogProvider />
        <ReactionPortals />
      </div>
    </>
  )
}
