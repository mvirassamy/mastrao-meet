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
  // While recording, the badge only says "Recording"; the full sentence is
  // read by screen readers.
  const isRecording = recording.recording_state === 'active' && !withdrawFailed

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
        paddingLeft: '0.875rem',
        paddingRight: canStop ? '0.25rem' : '0.875rem',
        paddingY: '0.25rem',
        borderRadius: '999px',
        // Full recording red: impossible to miss, white text above 4.5:1.
        backgroundColor: '#c4323d',
        color: 'white',
        fontSize: '0.8125rem',
        fontWeight: 500,
        lineHeight: '1.25rem',
        whiteSpace: 'nowrap',
      })}
    >
      <span
        aria-hidden="true"
        className={css({
          position: 'relative',
          width: '8px',
          height: '8px',
          flexShrink: 0,
          borderRadius: 'full',
          backgroundColor: 'white',
          '&[data-live=true]::after': {
            content: '""',
            position: 'absolute',
            inset: 0,
            borderRadius: 'full',
            backgroundColor: 'white',
            animation: 'unread_pulse 1.6s ease-out infinite',
            _motionReduce: { animation: 'none' },
          },
        })}
        data-live={isRecording}
      />
      {isRecording ? (
        <>
          <span aria-hidden="true">{t('badge')}</span>
          <span className={css({ srOnly: true })}>{status}</span>
        </>
      ) : (
        <span>{status}</span>
      )}
      {canStop && (
        <Button
          size="sm"
          variant="invert"
          aria-label={t('stop')}
          tooltip={t('stop')}
          isDisabled={isEnding || isWithdrawing}
          onPress={withdraw}
          // Layout only: a small pill inside the badge.
          className={css({
            height: '28px',
            minHeight: '28px',
            gap: '0.375rem',
            paddingX: '0.75rem',
            borderRadius: '999px',
            fontSize: '0.8125rem',
          })}
        >
          <StopCircleIcon size={16} aria-hidden="true" />
          {t('stopShort')}
        </Button>
      )}
    </div>
  )
}
