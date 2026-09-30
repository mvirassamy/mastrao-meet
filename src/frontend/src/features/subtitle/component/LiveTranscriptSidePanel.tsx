import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge, Button, Text } from '@/primitives'
import { css } from '@/styled-system/css'
import { useLiveTranscription } from '../store/liveTranscriptionContext'
import { useSubtitles } from '../hooks/useSubtitles'
import type {
  LiveTranscriptionConnectionStatus,
  LiveTranscriptionSegment,
} from '../store/liveTranscriptionTypes'

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
  finalLabel,
  interimLabel,
}: {
  segment: LiveTranscriptionSegment
  finalLabel: string
  interimLabel: string
}) => {
  const isFinal = segment.state === 'final'

  return (
    <article
      className={css({
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        padding: '0.75rem',
        borderWidth: '1px',
        borderStyle: isFinal ? 'solid' : 'dashed',
        borderColor: isFinal ? 'box.border' : 'primary',
        borderRadius: '8px',
        backgroundColor: isFinal ? 'box.bg' : 'accent',
      })}
      data-segment-state={segment.state}
      data-segment-key={segment.key}
    >
      <div
        className={css({
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '0.75rem',
        })}
      >
        <Text
          as="span"
          variant="note"
          margin={false}
          className={css({
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          })}
        >
          {segment.participantIdentity}
        </Text>
        <Badge size="sm">{isFinal ? finalLabel : interimLabel}</Badge>
      </div>
      <Text as="p" margin={false} wrap="pretty">
        {segment.text}
      </Text>
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
  const [isRetryingLocally, setIsRetryingLocally] = useState(false)
  const { ensureSubtitlesStarted } = useSubtitles()
  const {
    status,
    connectionStatus,
    resyncStatus,
    segments,
    syncSubtitleState,
  } = useLiveTranscription()

  const isRetrying = resyncStatus === 'pending' || isRetryingLocally
  const hasSegments = segments.length > 0

  useEffect(() => {
    void ensureSubtitlesStarted().catch(() => undefined)
  }, [ensureSubtitlesStarted])

  const handleRetry = async () => {
    if (isRetrying) return
    setIsRetryingLocally(true)
    try {
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

      {resyncStatus === 'failed' && (
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
            {t('error')}
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
          segments.map((segment) => (
            <Segment
              key={segment.key}
              segment={segment}
              finalLabel={t('final')}
              interimLabel={t('interim')}
            />
          ))
        )}
      </div>
    </div>
  )
}
