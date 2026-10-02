import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Text } from '@/primitives'
import { css } from '@/styled-system/css'
import { Avatar } from '@/components/Avatar'
import { DEFAULT_PARTICIPANT_COLOR } from '@/features/rooms/utils/getParticipantColor'
import { useLiveTranscription } from '../store/liveTranscriptionContext'
import { useSubtitles } from '../hooks/useSubtitles'
import type {
  LiveTranscriptionConnectionStatus,
  LiveTranscriptionSegment,
} from '../store/liveTranscriptionTypes'
import { groupConsecutiveSpeakerSegments } from './liveTranscriptTurns'

const connectionStatusClassName = (status: LiveTranscriptionConnectionStatus) =>
  css({
    width: '0.5rem',
    height: '0.5rem',
    borderRadius: '50%',
    flexShrink: 0,
    backgroundColor:
      status === 'connected'
        ? 'success'
        : status === 'reconnecting'
          ? 'warning'
          : 'danger',
  })

const Segment = ({
  segment,
  speakerLabel,
  speakerColor,
}: {
  segment: LiveTranscriptionSegment
  speakerLabel: string
  speakerColor: string
}) => {
  const isFinal = segment.state === 'final'

  return (
    <article
      className={css({
        display: 'flex',
        alignItems: 'flex-start',
        gap: '0.625rem',
        width: '100%',
      })}
      data-segment-state={segment.state}
      data-segment-key={segment.key}
    >
      <Avatar name={speakerLabel} bgColor={speakerColor} context="list" />
      <div
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '0.25rem',
          minWidth: 0,
          flex: 1,
        })}
      >
        <Text
          as="span"
          variant="bodyXsMedium"
          margin={false}
          className={css({
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          })}
        >
          {speakerLabel}
        </Text>
        <Text
          as="p"
          variant="sm"
          margin={false}
          wrap="pretty"
          className={css({
            width: 'fit-content',
            maxWidth: '100%',
            padding: '0.375rem 0.5rem',
            borderRadius: '6px',
            backgroundColor: 'muted',
            color: 'foreground',
            whiteSpace: 'pre-wrap',
            opacity: isFinal ? 1 : 0.72,
          })}
        >
          {segment.text}
        </Text>
      </div>
    </article>
  )
}

const StatusRow = ({
  connectionStatus,
  connectionLabel,
  statusLabel,
  connectionLabelTitle,
  statusLabelTitle,
}: {
  connectionStatus: LiveTranscriptionConnectionStatus
  connectionLabel: string
  statusLabel: string
  connectionLabelTitle: string
  statusLabelTitle: string
}) => (
  <div
    className={css({
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: '0.75rem',
      padding: '0.75rem 1.25rem',
      borderBottomWidth: '1px',
      borderBottomStyle: 'solid',
      borderBottomColor: 'box.border',
    })}
    role="status"
    aria-live="polite"
  >
    <div
      className={css({
        display: 'flex',
        alignItems: 'center',
        gap: '0.5rem',
        minWidth: 0,
      })}
    >
      <span
        className={connectionStatusClassName(connectionStatus)}
        aria-hidden="true"
      />
      <Text as="span" variant="sm" margin={false}>
        {connectionLabelTitle}: {connectionLabel}
      </Text>
    </div>
    <Text
      as="span"
      variant="note"
      margin={false}
      className={css({ textAlign: 'end' })}
    >
      {statusLabelTitle}: {statusLabel}
    </Text>
  </div>
)

export const LiveTranscriptSidePanel = () => {
  const { t } = useTranslation('rooms', { keyPrefix: 'liveTranscript' })
  const { t: tParticipants } = useTranslation('rooms', {
    keyPrefix: 'participants',
  })
  const [isRetryingLocally, setIsRetryingLocally] = useState(false)
  const [hasStartFailed, setHasStartFailed] = useState(false)
  const { ensureSubtitlesStarted } = useSubtitles()
  const {
    status,
    connectionStatus,
    resyncStatus,
    segments,
    syncSubtitleState,
    resolveSpeaker,
  } = useLiveTranscription()

  const isRetrying = resyncStatus === 'pending' || isRetryingLocally
  const hasSegments = segments.length > 0
  const speakerTurns = groupConsecutiveSpeakerSegments(segments)
  const hasFailed = hasStartFailed || resyncStatus === 'failed'

  useEffect(() => {
    let isCurrent = true
    ensureSubtitlesStarted().catch(() => {
      if (isCurrent) setHasStartFailed(true)
    })
    return () => {
      isCurrent = false
    }
  }, [ensureSubtitlesStarted])

  const handleRetry = async () => {
    if (isRetrying) return
    setIsRetryingLocally(true)
    try {
      if (hasStartFailed) {
        try {
          await ensureSubtitlesStarted()
          setHasStartFailed(false)
        } catch {
          return
        }
      }
      await syncSubtitleState()
    } finally {
      setIsRetryingLocally(false)
    }
  }

  return (
    <div
      className={css({
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        height: '100%',
      })}
      data-testid="live-transcript-panel"
    >
      <StatusRow
        connectionStatus={connectionStatus}
        connectionLabel={t(`connection.${connectionStatus}`)}
        statusLabel={t(`status.${status}`)}
        connectionLabelTitle={t('connectionLabel')}
        statusLabelTitle={t('statusLabel')}
      />

      {hasFailed && (
        <div
          className={css({
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '0.75rem',
            padding: '0.75rem 1.25rem',
            backgroundColor: 'danger.subtle',
            color: 'danger.subtle-text',
          })}
          role="alert"
        >
          <Text as="span" variant="warning" margin={false}>
            {t(hasStartFailed ? 'startError' : 'error')}
          </Text>
          <Button
            variant="outline"
            size="sm"
            onPress={() => void handleRetry()}
            isDisabled={isRetrying}
          >
            {t('retry')}
          </Button>
        </div>
      )}

      {isRetrying && (
        <Text
          as="p"
          variant="note"
          margin={false}
          padding={false}
          className={css({ padding: '0.75rem 1.25rem 0' })}
          role="status"
        >
          {t('refreshing')}
        </Text>
      )}

      {connectionStatus === 'disconnected' && (
        <Text
          as="p"
          variant="warning"
          margin={false}
          padding={false}
          className={css({ padding: '0.75rem 1.25rem 0' })}
        >
          {t('connection.disconnectedDescription')}
        </Text>
      )}

      <div
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '0.625rem',
          minHeight: 0,
          overflowY: 'auto',
          padding: '1rem 1.25rem 1.25rem',
        })}
        role="log"
        aria-live="off"
        aria-label={t('segmentsLabel')}
      >
        {!hasSegments ? (
          <Text
            as="p"
            variant="note"
            margin={false}
            className={css({
              padding: '1rem 0',
              textAlign: 'center',
            })}
          >
            {t('empty')}
          </Text>
        ) : (
          speakerTurns.map((segment) => {
            const speaker = resolveSpeaker(segment.participantIdentity)

            return (
              <Segment
                key={segment.key}
                segment={segment}
                speakerLabel={speaker?.label ?? tParticipants('unknown')}
                speakerColor={speaker?.color ?? DEFAULT_PARTICIPANT_COLOR}
              />
            )
          })
        )}
      </div>
    </div>
  )
}
