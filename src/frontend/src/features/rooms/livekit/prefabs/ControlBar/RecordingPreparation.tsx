import { useRef, useState } from 'react'
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
  video,
  onRecordingChanged,
}: {
  roomId: string
  canStart?: boolean
  video: VideoRecordingPolicy
  onRecordingChanged?: () => Promise<unknown>
}) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'recordingConsent' })
  const { isEnding } = useMeetingLifecycle()
  const [pending, setPending] = useState<Action | null>(null)
  const [failed, setFailed] = useState(false)
  const inFlight = useRef(false)
  const startRequestId = useRef(
    `activation_${crypto.randomUUID().replaceAll('-', '')}`
  )
  const locked = video.decision_lock !== 'open' || isEnding

  const act = async (action: Action) => {
    if (locked || inFlight.current) return
    if (action === 'start' && (!canStart || !video.start_available)) return
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

  return (
    <div
      className={css({
        display: 'flex',
        flexWrap: 'wrap',
        gap: '0.5rem',
        alignItems: 'center',
      })}
    >
      {video.decision_lock === 'open' && (
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
          {video.decision_basis === 'no_opposition' && (
            <span>{t('noOpposition')}</span>
          )}
        </div>
      )}
      {canStart && (
        <Button
          variant="outline"
          size="sm"
          icon={<RecordIcon aria-hidden="true" />}
          isDisabled={locked || pending !== null || !video.start_available}
          loading={pending === 'start'}
          onPress={() => act('start')}
        >
          {t('startVideo')}
        </Button>
      )}
      {video.start_status === 'refused' && (
        <span role="status">{t('videoRefused')}</span>
      )}
      {video.start_status === 'pending' && video.decision_lock === 'open' && (
        <span role="status">{t('videoPending')}</span>
      )}
      {video.decision_lock === 'start_in_progress' && (
        <span role="status">{t('starting')}</span>
      )}
      {failed && <span role="alert">{t('videoActionError')}</span>}
    </div>
  )
}
