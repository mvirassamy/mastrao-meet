import { FALLBACK_LANGUAGE } from '@/i18n/languageDetection'
import { useEffect, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'wouter'
import {
  ChevronLeftIcon,
  CalendarIcon,
  TranscriptIcon,
  ErrorIcon,
  SummaryIcon,
  FileSearchIcon,
  MinusCircleIcon,
  InformationIcon,
  RetryIcon,
  TimeIcon,
} from '@/icons'
import { ApiError } from '@/api/ApiError'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import type {
  MeetingContentStatus,
  MeetingHistoryDetail,
  MeetingSummary,
  MeetingTranscript,
  MeetingTranscriptSegment,
} from '../api/types'
import {
  shouldRequestSummary,
  useMeetingHistoryDetail,
  useRequestMeetingSummary,
} from '../api/useMeetingHistory'
import {
  isAuthRequiredError,
  useLoginRedirectOnAuthError,
} from '../api/authRedirect'
import { MEETING_HISTORY_PATH } from '../paths'
import {
  formatMeetingDay,
  formatMeetingDuration,
  formatMeetingTimeRange,
  formatTranscriptTimestamp,
} from '../utils/meetingHistoryFormat'
import { type MeetingContentKind } from './MeetingContentStatusBadge'
import {
  MeetingHistoryStatePanel,
  MeetingSectionState,
} from './MeetingHistoryStatePanel'
import { MeetingHistorySkeleton } from './MeetingHistorySkeleton'

const isNotFoundError = (error: unknown) =>
  error instanceof ApiError && [403, 404].includes(error.statusCode)

/** Local state of the summary request sent when the detail opens. */
type SummaryRequestState = 'idle' | 'pending' | 'failed'

export const MeetingHistoryDetailView = ({
  meetingId,
  timeZone,
}: {
  meetingId: string
  timeZone?: string
}) => {
  const { t } = useTranslation('meetingHistory')
  const query = useMeetingHistoryDetail(meetingId)
  useLoginRedirectOnAuthError(query.error)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const settled = !query.isPending
  const focusedRef = useRef(false)
  const { mutate: requestSummary, ...summaryRequest } =
    useRequestMeetingSummary(meetingId)
  const summaryRequestedRef = useRef(false)
  const detail = query.data

  // Moves keyboard and screen reader focus to the page heading once known.
  useEffect(() => {
    if (!settled || focusedRef.current) return
    focusedRef.current = true
    headingRef.current?.focus({ preventScroll: true })
  }, [settled])

  // At most one summary request per opening of the detail; the backend
  // command is idempotent, and a failure is never retried automatically.
  useEffect(() => {
    if (!detail || summaryRequestedRef.current) return
    if (!shouldRequestSummary(detail)) return
    summaryRequestedRef.current = true
    requestSummary()
  }, [detail, requestSummary])

  const summaryRequestState: SummaryRequestState = summaryRequest.isPending
    ? 'pending'
    : summaryRequest.isError &&
        !(
          summaryRequest.error instanceof ApiError &&
          [401, 404, 409].includes(summaryRequest.error.statusCode)
        )
      ? 'failed'
      : 'idle'

  let content: ReactNode
  if (query.isPending || isAuthRequiredError(query.error)) {
    content = <MeetingHistorySkeleton label={t('detail.loading')} rows={3} />
  } else if (!query.data) {
    const notFound = isNotFoundError(query.error)
    content = (
      <MeetingHistoryStatePanel
        role="alert"
        headingLevel={1}
        headingRef={headingRef}
        tone={notFound ? 'neutral' : 'danger'}
        icon={notFound ? <FileSearchIcon size={22} /> : undefined}
        illustration={
          notFound
            ? undefined
            : '/assets/illustrations/reunion-detail-erreur.webp'
        }
        title={t(notFound ? 'detail.notFound.title' : 'detail.error.title')}
        description={t(
          notFound ? 'detail.notFound.description' : 'detail.error.description'
        )}
        action={
          notFound ? undefined : (
            <Button
              variant="secondary"
              icon={<RetryIcon aria-hidden="true" />}
              onPress={() => void query.refetch()}
            >
              {t('error.retry')}
            </Button>
          )
        }
      />
    )
  } else {
    content = (
      <MeetingDetailContent
        meeting={query.data}
        timeZone={timeZone}
        headingRef={headingRef}
        summaryRequestState={summaryRequestState}
        onRetrySummary={() => requestSummary()}
      />
    )
  }

  return (
    <div
      className={css({
        width: '100%',
        maxWidth: '72rem',
        marginX: 'auto',
        padding: { base: '1rem 1rem 2rem', md: '1.5rem 2rem 3rem' },
      })}
    >
      <Link
        to={MEETING_HISTORY_PATH}
        className={css({
          display: 'inline-flex',
          alignItems: 'center',
          gap: '0.25rem',
          minHeight: { base: '44px', md: '32px' },
          marginLeft: '-0.5rem',
          marginBottom: '0.75rem',
          paddingX: '0.5rem',
          borderRadius: 'control',
          color: 'var(--workspace-ink-soft, var(--foreground))',
          fontSize: '0.8125rem',
          fontWeight: 500,
          textDecoration: 'none',
          _hover: { backgroundColor: 'muted', color: 'foreground' },
          _focusVisible: {
            outline: '2px solid',
            outlineColor: 'ring',
            outlineOffset: '2px',
          },
        })}
      >
        <ChevronLeftIcon size={18} aria-hidden="true" />
        {t('detail.back')}
      </Link>
      {content}
    </div>
  )
}

const MeetingDetailContent = ({
  meeting,
  timeZone,
  headingRef,
  summaryRequestState,
  onRetrySummary,
}: {
  meeting: MeetingHistoryDetail
  timeZone?: string
  headingRef: React.Ref<HTMLHeadingElement>
  summaryRequestState: SummaryRequestState
  onRetrySummary: () => void
}) => {
  const { t, i18n } = useTranslation('meetingHistory')
  const locale = i18n.resolvedLanguage || i18n.language || FALLBACK_LANGUAGE
  const duration = formatMeetingDuration(
    meeting.startedAt,
    meeting.endedAt,
    locale
  )
  const metaItemClass = css({
    display: 'inline-flex',
    alignItems: 'center',
    gap: '0.375rem',
  })

  return (
    <>
      <header className={css({ marginBottom: '1.5rem' })}>
        <h1
          ref={headingRef}
          tabIndex={-1}
          className={css({
            margin: 0,
            fontSize: { base: '1.375rem', md: '1.625rem' },
            lineHeight: 1.25,
            fontWeight: 600,
            letterSpacing: '-0.025em',
            overflowWrap: 'anywhere',
          })}
        >
          {meeting.title ?? t('untitled')}
        </h1>
        <p
          className={css({
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            columnGap: '1rem',
            rowGap: '0.25rem',
            marginTop: '0.5rem',
            marginBottom: 0,
            color: 'muted-foreground',
            fontSize: '0.8125rem',
            lineHeight: '1.25rem',
          })}
        >
          <span className={metaItemClass}>
            <CalendarIcon size={15} aria-hidden="true" />
            {formatMeetingDay(meeting.startedAt, locale, timeZone)}
          </span>
          <span className={metaItemClass}>
            <TimeIcon size={15} aria-hidden="true" />
            {[
              formatMeetingTimeRange(
                meeting.startedAt,
                meeting.endedAt,
                locale,
                timeZone
              ),
              duration,
            ]
              .filter(Boolean)
              .join(' · ')}
          </span>
          {meeting.participantCount !== null && (
            <span className={metaItemClass}>
              {t('participants', { count: meeting.participantCount })}
            </span>
          )}
        </p>
      </header>

      <div
        className={css({
          display: 'grid',
          gridTemplateColumns: {
            base: 'minmax(0, 1fr)',
            lg: 'minmax(0, 1fr) minmax(0, 1.2fr)',
          },
          alignItems: 'start',
          gap: { base: '1rem', md: '1.25rem' },
        })}
      >
        <MeetingContentSection
          kind="summary"
          status={meeting.summary.status}
          icon={<SummaryIcon size={18} aria-hidden="true" />}
        >
          <SummaryBody
            summary={meeting.summary}
            transcriptStatus={meeting.transcript.status}
            requestState={summaryRequestState}
            onRetry={onRetrySummary}
          />
        </MeetingContentSection>
        <MeetingContentSection
          kind="transcript"
          status={meeting.transcript.status}
          icon={<TranscriptIcon size={18} aria-hidden="true" />}
        >
          <TranscriptBody transcript={meeting.transcript} />
        </MeetingContentSection>
      </div>
    </>
  )
}

const MeetingContentSection = ({
  kind,
  status,
  icon,
  children,
}: {
  kind: MeetingContentKind
  status: MeetingContentStatus
  icon: ReactNode
  children: ReactNode
}) => {
  const { t } = useTranslation('meetingHistory')
  const headingId = `meeting-${kind}-heading`

  return (
    <section
      aria-labelledby={headingId}
      data-section={kind}
      data-status={status}
      className={css({
        minWidth: 0,
        border: '1px solid token(colors.border)',
        borderRadius: '12px',
        backgroundColor: 'card',
      })}
    >
      <div
        className={css({
          padding: '0.875rem 1.125rem',
          borderBottom: '1px solid token(colors.border)',
        })}
      >
        <h2
          id={headingId}
          className={css({
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem',
            margin: 0,
            fontSize: '1rem',
            lineHeight: '1.5rem',
            fontWeight: 600,
            '& svg': { color: 'primary' },
          })}
        >
          {icon}
          {t(`${kind}.title`)}
        </h2>
      </div>
      <div className={css({ padding: '1rem 1.125rem 1.25rem' })}>
        {children}
      </div>
    </section>
  )
}

const UnavailableContent = ({
  kind,
  status,
}: {
  kind: MeetingContentKind
  status: Exclude<MeetingContentStatus, 'available'>
}) => {
  const { t } = useTranslation('meetingHistory')
  const visual = {
    processing: { tone: 'info', icon: <TimeIcon size={18} /> },
    absent: {
      tone: 'neutral',
      icon: <MinusCircleIcon size={18} />,
    },
    failed: { tone: 'danger', icon: <ErrorIcon size={18} /> },
  } as const
  return (
    <MeetingSectionState
      tone={visual[status].tone}
      icon={visual[status].icon}
      title={t(`${kind}.${status}.title`)}
      description={t(`${kind}.${status}.description`)}
    />
  )
}

const bodyTextClass = css({
  margin: 0,
  color: 'foreground',
  fontSize: '0.9375rem',
  lineHeight: '1.5rem',
  overflowWrap: 'anywhere',
})

const SummaryBody = ({
  summary,
  transcriptStatus,
  requestState,
  onRetry,
}: {
  summary: MeetingSummary
  transcriptStatus: MeetingContentStatus
  requestState: SummaryRequestState
  onRetry: () => void
}) => {
  const { t } = useTranslation('meetingHistory')
  if (summary.status === 'absent') {
    if (requestState === 'pending')
      return <UnavailableContent kind="summary" status="processing" />
    if (requestState === 'failed')
      return (
        <MeetingSectionState
          tone="danger"
          icon={<ErrorIcon size={18} />}
          title={t('summary.requestFailed.title')}
          description={t('summary.requestFailed.description')}
          action={
            <Button
              size="sm"
              variant="secondary"
              icon={<RetryIcon aria-hidden="true" />}
              onPress={onRetry}
            >
              {t('error.retry')}
            </Button>
          }
        />
      )
    if (transcriptStatus === 'processing')
      return (
        <MeetingSectionState
          tone="info"
          icon={<TimeIcon size={18} />}
          title={t('summary.waitingTranscript.title')}
          description={t('summary.waitingTranscript.description')}
        />
      )
  }
  if (summary.status !== 'available')
    return <UnavailableContent kind="summary" status={summary.status} />

  return (
    <div
      className={css({
        display: 'flex',
        flexDirection: 'column',
        gap: '1rem',
      })}
    >
      <p
        className={css({
          display: 'flex',
          alignItems: 'flex-start',
          gap: '0.375rem',
          margin: 0,
          color: 'muted-foreground',
          fontSize: '0.8125rem',
          lineHeight: '1.25rem',
          '& svg': { flexShrink: 0, marginTop: '0.125rem' },
        })}
      >
        <InformationIcon size={15} aria-hidden="true" />
        {t('summary.automatic')}
      </p>
      {summary.paragraphs.map((paragraph, index) => (
        <p key={index} className={bodyTextClass}>
          {paragraph}
        </p>
      ))}
      {summary.sections.map((section) => (
        <div key={section.title}>
          <h3
            className={css({
              margin: 0,
              marginBottom: '0.375rem',
              fontSize: '0.875rem',
              lineHeight: '1.25rem',
              fontWeight: 600,
            })}
          >
            {section.title}
          </h3>
          <ul
            className={css({
              margin: 0,
              paddingLeft: '1.25rem',
              listStyleType: 'disc',
              '& li + li': { marginTop: '0.25rem' },
            })}
          >
            {section.items.map((item, index) => (
              <li key={index} className={bodyTextClass}>
                {item}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

type SpeakerTurn = {
  id: string
  speaker: string | null
  startMs: number
  texts: string[]
}

const groupTurns = (segments: MeetingTranscriptSegment[]) =>
  segments.reduce<SpeakerTurn[]>((turns, segment) => {
    const previous = turns[turns.length - 1]
    if (previous && previous.speaker === segment.speaker)
      previous.texts.push(segment.text)
    else
      turns.push({
        id: segment.id,
        speaker: segment.speaker,
        startMs: segment.startMs,
        texts: [segment.text],
      })
    return turns
  }, [])

const TranscriptBody = ({ transcript }: { transcript: MeetingTranscript }) => {
  const { t } = useTranslation('meetingHistory')
  if (transcript.status !== 'available')
    return <UnavailableContent kind="transcript" status={transcript.status} />

  return (
    <>
      <ol
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '1rem',
          margin: 0,
          padding: 0,
          listStyle: 'none',
        })}
      >
        {groupTurns(transcript.segments).map((turn) => (
          <li key={turn.id}>
            <p
              className={css({
                display: 'flex',
                alignItems: 'baseline',
                gap: '0.5rem',
                margin: 0,
                marginBottom: '0.125rem',
                fontSize: '0.8125rem',
                lineHeight: '1.25rem',
              })}
            >
              <span className={css({ fontWeight: 600 })}>
                {turn.speaker ?? t('transcript.unknownSpeaker')}
              </span>
              <span
                className={css({
                  color: 'muted-foreground',
                  fontVariantNumeric: 'tabular-nums',
                })}
              >
                {formatTranscriptTimestamp(turn.startMs)}
              </span>
            </p>
            {turn.texts.map((text, index) => (
              <p
                key={index}
                className={`${bodyTextClass} ${css({ '& + &': { marginTop: '0.375rem' } })}`}
              >
                {text}
              </p>
            ))}
          </li>
        ))}
      </ol>
      {transcript.truncated && (
        <p
          className={css({
            marginTop: '1rem',
            marginBottom: 0,
            color: 'muted-foreground',
            fontSize: '0.8125rem',
          })}
        >
          {t('transcript.truncated')}
        </p>
      )}
    </>
  )
}
