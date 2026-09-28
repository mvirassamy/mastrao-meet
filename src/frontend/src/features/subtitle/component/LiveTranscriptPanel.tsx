import { useRoomContext } from '@livekit/components-react'
import type { Participant } from 'livekit-client'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Avatar } from '@/components/Avatar'
import { useIsMobile } from '@/utils/useIsMobile'
import { getParticipantName } from '@/features/rooms/utils/getParticipantName'
import {
  DEFAULT_PARTICIPANT_COLOR,
  getParticipantColor,
} from '@/features/rooms/utils/getParticipantColor'
import { Button, Text } from '@/primitives'
import { css } from '@/styled-system/css'

import {
  getParticipantForTranscription,
  useLiveTranscription,
  type LiveTranscriptionSegment,
  type LiveTranscriptionStatus,
} from '../store'

const LIVE_TRANSCRIPTION_STATUS_KEYS: Record<LiveTranscriptionStatus, string> =
  {
    inactive: 'inactive',
    starting: 'starting',
    live: 'live',
    reconnecting: 'reconnecting',
    degraded: 'degraded',
    unavailable: 'unavailable',
    stopped: 'stopped',
  }

type TranscriptGroup = {
  id: string
  participantIdentity: string
  participant?: Participant
  segments: LiveTranscriptionSegment[]
}

const groupSegmentsByParticipant = (
  segments: LiveTranscriptionSegment[],
  room: ReturnType<typeof useRoomContext>
): TranscriptGroup[] => {
  const groups: TranscriptGroup[] = []

  for (const segment of segments) {
    const previousGroup = groups.at(-1)
    if (previousGroup?.participantIdentity === segment.participantIdentity) {
      previousGroup.segments.push(segment)
      continue
    }

    groups.push({
      id: `${segment.participantIdentity}-${segment.sequence}`,
      participantIdentity: segment.participantIdentity,
      participant: getParticipantForTranscription(
        room,
        segment.participantIdentity
      ),
      segments: [segment],
    })
  }

  return groups
}

const getFinalSegmentKeys = (segments: LiveTranscriptionSegment[]) =>
  segments
    .filter((segment) => segment.state === 'final')
    .map((segment) => segment.key)

const LiveTranscriptSegment = ({
  segment,
  interimLabel,
}: {
  segment: LiveTranscriptionSegment
  interimLabel: string
}) => (
  <p
    aria-live={segment.state === 'final' ? 'polite' : undefined}
    data-segment-state={segment.state}
    className={css({
      margin: 0,
      padding: '0.5rem 0.75rem',
      borderRadius: '0.5rem',
      backgroundColor:
        segment.state === 'final' ? 'box.subtleBg' : 'accent.subtleBg',
      color: 'box.text',
      lineHeight: '1.45',
    })}
  >
    {segment.state === 'interim' && (
      <span
        className={css({
          marginRight: '0.375rem',
          color: 'muted-foreground',
          fontSize: '0.75rem',
          fontStyle: 'italic',
        })}
      >
        {interimLabel}
      </span>
    )}
    {segment.text}
  </p>
)

const LiveTranscriptGroup = ({
  group,
  interimLabel,
}: {
  group: TranscriptGroup
  interimLabel: string
}) => {
  const participantName = group.participant
    ? getParticipantName(group.participant)
    : group.participantIdentity
  const participantColor = group.participant
    ? getParticipantColor(group.participant)
    : DEFAULT_PARTICIPANT_COLOR

  return (
    <article
      data-participant-identity={group.participantIdentity}
      className={css({
        display: 'flex',
        alignItems: 'flex-start',
        gap: '0.625rem',
      })}
    >
      <Avatar
        name={participantName}
        bgColor={participantColor}
        context="list"
        aria-hidden="true"
      />
      <div
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '0.375rem',
          minWidth: 0,
          flex: 1,
        })}
      >
        <Text as="span" variant="sm" margin={false}>
          {participantName}
        </Text>
        {group.segments.map((segment) => (
          <LiveTranscriptSegment
            key={segment.key}
            segment={segment}
            interimLabel={interimLabel}
          />
        ))}
      </div>
    </article>
  )
}

export const LiveTranscriptPanel = () => {
  const room = useRoomContext()
  const isMobile = useIsMobile()
  const { t } = useTranslation('rooms', { keyPrefix: 'liveTranscript' })
  const { status, segments, gaps, truncated } = useLiveTranscription()
  const transcriptRef = useRef<HTMLDivElement>(null)
  const seenFinalKeysRef = useRef(new Set<string>())
  const [isFollowing, setIsFollowing] = useState(true)
  const [newSegmentCount, setNewSegmentCount] = useState(0)

  const groups = useMemo(
    () => groupSegmentsByParticipant(segments, room),
    [room, segments]
  )
  const finalSegmentKeys = useMemo(
    () => getFinalSegmentKeys(segments),
    [segments]
  )

  const markFinalsAsSeen = useCallback(() => {
    for (const key of finalSegmentKeys) seenFinalKeysRef.current.add(key)
  }, [finalSegmentKeys])

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    const transcript = transcriptRef.current
    if (!transcript) return
    transcript.scrollTo({ top: transcript.scrollHeight, behavior })
  }, [])

  useEffect(() => {
    if (isFollowing) {
      markFinalsAsSeen()
      const animationFrame = requestAnimationFrame(() => scrollToBottom())
      setNewSegmentCount(0)
      return () => cancelAnimationFrame(animationFrame)
    }

    const unseenFinals = finalSegmentKeys.filter(
      (key) => !seenFinalKeysRef.current.has(key)
    )
    if (unseenFinals.length === 0) return

    for (const key of unseenFinals) seenFinalKeysRef.current.add(key)
    setNewSegmentCount((count) => count + unseenFinals.length)
  }, [finalSegmentKeys, isFollowing, markFinalsAsSeen, scrollToBottom])

  const handleTranscriptScroll = () => {
    const transcript = transcriptRef.current
    if (!transcript) return
    const isAtBottom =
      transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight <
      24
    setIsFollowing(isAtBottom)
    if (isAtBottom) {
      markFinalsAsSeen()
      setNewSegmentCount(0)
    }
  }

  const returnToLive = () => {
    markFinalsAsSeen()
    setNewSegmentCount(0)
    setIsFollowing(true)
    scrollToBottom('smooth')
  }

  const statusKey = LIVE_TRANSCRIPTION_STATUS_KEYS[status]
  const statusDescription =
    status === 'degraded' && gaps.length > 0
      ? t('status.degradedWithGap')
      : t(`status.${statusKey}`)

  return (
    <div
      data-mobile-fullscreen={isMobile ? 'true' : undefined}
      className={css({
        display: 'flex',
        flexDirection: 'column',
        flexGrow: 1,
        minHeight: 0,
        padding: '0 1rem 1rem',
        gap: '0.75rem',
      })}
    >
      <div
        role="status"
        data-transcription-status={status}
        className={css({
          padding: '0.625rem 0.75rem',
          borderRadius: '0.5rem',
          backgroundColor: status === 'live' ? 'success.subtleBg' : 'accent',
          color: 'box.text',
          fontSize: '0.8125rem',
        })}
      >
        {statusDescription}
      </div>

      {truncated && (
        <Text variant="note" margin={false}>
          {t('truncated')}
        </Text>
      )}

      <div
        ref={transcriptRef}
        onScroll={handleTranscriptScroll}
        aria-label={t('segmentsLabel')}
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '1rem',
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          padding: '0.25rem 0.25rem 1rem 0',
          overscrollBehavior: 'contain',
        })}
      >
        {groups.length === 0 ? (
          <Text variant="note" margin={false}>
            {t('empty')}
          </Text>
        ) : (
          groups.map((group) => (
            <LiveTranscriptGroup
              key={group.id}
              group={group}
              interimLabel={t('interim')}
            />
          ))
        )}
      </div>

      {!isFollowing && (
        <Button
          variant="secondary"
          size="sm"
          onPress={returnToLive}
          data-new-segment-count={newSegmentCount}
        >
          {t('returnToLive', { count: newSegmentCount })}
        </Button>
      )}
    </div>
  )
}
