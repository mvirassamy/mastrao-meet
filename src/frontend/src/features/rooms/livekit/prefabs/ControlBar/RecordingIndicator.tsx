import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { StopCircleIcon } from '@/icons'
import { css } from '@/styled-system/css'
import { Button } from '@/primitives'
import { stopRecording } from '@/features/rooms/api/recordingConsent'
import type { ApiRoom } from '@/features/rooms/api/ApiRoom'
import { useMeetingLifecycle } from '@/features/rooms/contexts/MeetingLifecycleContext'

type Recording = ApiRoom['recording']

interface RecordingIndicatorProps {
  roomId: string
  canEnd?: boolean
  recording?: Recording
  onRecordingChanged?: () => Promise<unknown>
}

export function RecordingIndicator({
  roomId,
  canEnd,
  recording,
  onRecordingChanged,
}: RecordingIndicatorProps) {
  const { t } = useTranslation('rooms', { keyPrefix: 'recordingConsent' })
  const { isEnding } = useMeetingLifecycle()
  const [isWithdrawing, setIsWithdrawing] = useState(false)
  const [withdrawFailed, setWithdrawFailed] = useState(false)
  const withdrawalIds = useRef(crypto.randomUUID().replaceAll('-', ''))

  const withdraw = async () => {
    if (!canEnd || isEnding || isWithdrawing) return
    setIsWithdrawing(true)
    setWithdrawFailed(false)
    try {
      await stopRecording(roomId, 'host', `stop_${withdrawalIds.current}`)
    } catch {
      setWithdrawFailed(true)
      return
    } finally {
      setIsWithdrawing(false)
    }
    await onRecordingChanged?.().catch(() => undefined)
  }

  if (
    recording?.mode !== 'recorded' ||
    !['starting', 'active', 'stopping'].includes(
      recording.recording_state ?? ''
    )
  ) {
    return null
  }

  const canStop =
    canEnd &&
    recording.decision === 'accepted' &&
    recording.recording_state !== 'stopping'

  let status = t('active')
  if (recording.recording_state === 'stopping') {
    status = t('stopping')
  } else if (withdrawFailed) {
    status = t('withdrawError')
  } else if (recording.recording_state === 'starting') {
    status = t('starting')
  }

  return (
    <div
      role="status"
      aria-live="polite"
      data-attr="recording-indicator"
      className={css({
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.5rem',
        maxWidth: '100%',
        minHeight: '36px',
        paddingX: '0.625rem',
        paddingY: '0.25rem',
        borderRadius: 'surface',
        backgroundColor: 'recording',
        color: 'recording-foreground',
        fontSize: '0.75rem',
        fontWeight: 600,
        lineHeight: '1rem',
      })}
    >
      <span
        aria-hidden="true"
        className={css({
          width: '8px',
          height: '8px',
          flexShrink: 0,
          borderRadius: 'full',
          backgroundColor: 'recording-foreground',
        })}
      />
      <span>{status}</span>
      {canStop && (
        <Button
          shape="circle"
          size="sm"
          variant="outline"
          aria-label={t('stop')}
          tooltip={t('stop')}
          isDisabled={isEnding || isWithdrawing}
          onPress={withdraw}
        >
          <StopCircleIcon size={18} />
        </Button>
      )}
    </div>
  )
}
