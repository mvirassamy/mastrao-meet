import { useRef, useState } from 'react'
import { useConnectionState } from '@livekit/components-react'
import { ConnectionState } from 'livekit-client'
import { useTranslation } from 'react-i18next'
import { RecordIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import type { VideoRecordingPolicy } from '@/features/rooms/api/ApiRoom'
import {
  activateRecording,
  decideRecording,
} from '@/features/rooms/api/recordingConsent'
import { useMeetingLifecycle } from '@/features/rooms/contexts/MeetingLifecycleContext'

type Action = 'start' | 'accepted' | 'refused'

export const RecordingPreparation = ({
  roomId,
  canStart,
  isHost,
  video,
  onRecordingChanged,
}: {
  roomId: string
  canStart?: boolean
  isHost?: boolean
  video: VideoRecordingPolicy
  onRecordingChanged?: () => Promise<unknown>
}) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'recordingConsent' })
  const { isEnding } = useMeetingLifecycle()
  const connectionState = useConnectionState()
  const [pending, setPending] = useState<Action | null>(null)
  const [failed, setFailed] = useState(false)
  const inFlight = useRef(false)
  const startRequestId = useRef(
    `activation_${crypto.randomUUID().replaceAll('-', '')}`
  )
  const locked = video.decision_lock !== 'open' || isEnding
  const startUnavailable =
    locked ||
    !canStart ||
    !video.start_available ||
    connectionState !== ConnectionState.Connected
  const showParticipantChoice =
    !isHost &&
    (video.start_requested || video.decision === 'refused') &&
    video.consultation_source === 'present' &&
    video.decision_lock === 'open'
  const showRefused = video.start_status === 'refused'
  const showHostPending =
    canStart &&
    pending !== 'start' &&
    video.start_requested &&
    video.start_status === 'pending' &&
    video.decision_lock === 'open'
  const showStarting =
    pending === 'start' || video.decision_lock === 'start_in_progress'

  const act = async (action: Action) => {
    if (locked || inFlight.current) return
    if (action === 'start' && startUnavailable) return
    inFlight.current = true
    setPending(action)
    setFailed(false)
    try {
      if (action === 'start') {
        await activateRecording(roomId, startRequestId.current)
        startRequestId.current = `activation_${crypto.randomUUID().replaceAll('-', '')}`
      } else {
        await decideRecording(
          roomId,
          action,
          `decision_${crypto.randomUUID().replaceAll('-', '')}`
        )
      }
      await onRecordingChanged?.()
    } catch {
      setFailed(true)
      await onRecordingChanged?.().catch(() => undefined)
    } finally {
      inFlight.current = false
      setPending(null)
    }
  }

  if (
    !showParticipantChoice &&
    !canStart &&
    !showRefused &&
    !showHostPending &&
    !showStarting &&
    !failed
  ) {
    return null
  }

  return (
    <div
      className={css({
        display: 'flex',
        flexWrap: 'wrap',
        gap: '0.5rem',
        alignItems: 'center',
      })}
    >
      {showParticipantChoice && (
        <div
          className={css({
            display: 'flex',
            flexWrap: 'wrap',
            gap: '0.375rem',
            alignItems: 'center',
            fontSize: '0.8125rem',
          })}
        >
          <span>{t('videoQuestion')}</span>
          <Button
            variant="outline"
            size="xs"
            aria-pressed={video.decision === 'accepted'}
            isDisabled={locked || pending !== null}
            loading={pending === 'accepted'}
            onPress={() => act('accepted')}
          >
            {t('videoYes')}
          </Button>
          <Button
            variant="outline"
            size="xs"
            aria-pressed={video.decision === 'refused'}
            isDisabled={locked || pending !== null}
            loading={pending === 'refused'}
            onPress={() => act('refused')}
          >
            {t('videoNo')}
          </Button>
        </div>
      )}
      {canStart && (
        <Button
          variant="outline"
          size="sm"
          icon={<RecordIcon aria-hidden="true" />}
          isDisabled={startUnavailable || pending !== null}
          loading={pending === 'start'}
          onPress={() => act('start')}
        >
          {t('startVideo')}
        </Button>
      )}
      {showRefused && <span role="status">{t('videoRefused')}</span>}
      {showHostPending && <span role="status">{t('videoPending')}</span>}
      {showStarting && <span role="status">{t('starting')}</span>}
      {failed && <span role="alert">{t('videoActionError')}</span>}
    </div>
  )
}
