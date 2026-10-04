import { FALLBACK_LANGUAGE } from '@/i18n/languageDetection'
import { useEffect, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'wouter'
import {
  ChevronLeftIcon,
  CalendarIcon,
  FileSearchIcon,
  InformationIcon,
  RetryIcon,
  TimeIcon,
} from '@/icons'
import { ApiError } from '@/api/ApiError'
import { Button } from '@/primitives'
import { css, cva } from '@/styled-system/css'
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
import {
  contentPhase,
  SECTION_ICONS,
  type MeetingContentKind,
} from './meetingContent'
import {
  MeetingHistoryStatePanel,
  MeetingSectionState,
} from './MeetingHistoryStatePanel'
import { MeetingHistorySkeleton } from './MeetingHistorySkeleton'

const isNotFoundError = (error: unknown) =>
  error instanceof ApiError && [403, 404].includes(error.statusCode)

/** Local state of the summary request sent when the detail opens. */
type SummaryRequestState = 'idle' | 'pending' | 'failed'

const isReadableStatus = (
  status: MeetingContentStatus
): status is 'available' | 'partial' =>
  status === 'available' || status === 'partial'

/** Statuses for which the service is still working on the content. */
const isPendingStatus = (status: MeetingContentStatus) =>
  contentPhase(status) === 'pending'

/** What a section shows, which sets the look of its header. */
type SectionLook = 'content' | 'pending' | 'empty' | 'failed'

const contentSectionLook = (status: MeetingContentStatus): SectionLook => {
  switch (contentPhase(status)) {
    case 'ready':
    case 'partial':
      return 'content'
    case 'pending':
      return 'pending'
    case 'failed':
      return 'failed'
    case 'absent':
      return 'empty'
  }
}

/** What the summary section shows: a step of the summary request, or the
 * summary status itself. */
type SummaryView =
  | 'request-pending'
  | 'request-failed'
  | 'waiting-transcript'
  | MeetingContentStatus

const summaryView = (
  summary: MeetingSummary,
  transcriptStatus: MeetingContentStatus,
  requestState: SummaryRequestState
): SummaryView => {
  if (summary.status !== 'not_started') return summary.status
  if (requestState === 'pending') return 'request-pending'
  if (requestState === 'failed') return 'request-failed'
  if (isPendingStatus(transcriptStatus)) return 'waiting-transcript'
  return 'not_started'
}

const summarySectionLook = (view: SummaryView): SectionLook => {
  switch (view) {
    case 'request-pending':
    case 'waiting-transcript':
      return 'pending'
    case 'request-failed':
      return 'failed'
    default:
      return contentSectionLook(view)
  }
}

/** Dot of a section state: none while pending, the header bar shows it. */
const sectionStateTone = (look: SectionLook) => {
  if (look === 'failed') return 'danger'
  if (look === 'empty') return 'neutral'
  return undefined
}

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
  const summaryState = summaryView(
    meeting.summary,
    meeting.transcript.status,
    summaryRequestState
  )
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
          look={summarySectionLook(summaryState)}
        >
          <SummaryBody
            summary={meeting.summary}
            view={summaryState}
            onRetry={onRetrySummary}
          />
        </MeetingContentSection>
        <MeetingContentSection
          kind="transcript"
          status={meeting.transcript.status}
          look={contentSectionLook(meeting.transcript.status)}
        >
          <TranscriptBody transcript={meeting.transcript} />
        </MeetingContentSection>
      </div>
    </>
  )
}

/**
 * Tinted header: the large section icon sits on the right as an
 * illustration, partly cropped, so the title stays clean. Grey while the
 * content is not ready or when there is nothing to show, pink when the
 * processing failed.
 */
const greyHeader = {
  background: 'linear-gradient(120deg, token(colors.card) 30%, #eef0f4 100%)',
  '& img': { filter: 'grayscale(1)', opacity: 0.45 },
}

const sectionHeader = cva({
  base: {
    position: 'relative',
    overflow: 'hidden',
    display: 'flex',
    alignItems: 'center',
    minHeight: '76px',
    padding: '0.875rem 1.125rem',
    paddingRight: '6.5rem',
    borderRadius: '11px 11px 0 0',
    background: 'linear-gradient(120deg, token(colors.card) 30%, #e6edff 100%)',
    borderBottom: '1px solid token(colors.border)',
    '& img': {
      position: 'absolute',
      right: '-6px',
      top: '-8px',
      userSelect: 'none',
      pointerEvents: 'none',
    },
  },
  variants: {
    look: {
      content: {},
      pending: greyHeader,
      empty: greyHeader,
      failed: {
        background:
          'linear-gradient(120deg, token(colors.card) 30%, #fcebec 100%)',
      },
    },
  },
})

const MeetingContentSection = ({
  kind,
  status,
  look,
  children,
}: {
  kind: MeetingContentKind
  status: MeetingContentStatus
  look: SectionLook
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
      <div className={sectionHeader({ look })}>
        <img
          src={SECTION_ICONS[kind]}
          alt=""
          aria-hidden="true"
          width={96}
          height={96}
          decoding="async"
        />
        <h2
          id={headingId}
          className={css({
            margin: 0,
            fontSize: '1rem',
            lineHeight: '1.5rem',
            fontWeight: 600,
          })}
        >
          {t(`${kind}.title`)}
        </h2>
        <span
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className={css({ srOnly: true })}
        >
          {t(`status.${kind}.${status}`)}
        </span>
        {look === 'pending' && <SectionProgressBar />}
      </div>
      <div className={css({ padding: '1rem 1.125rem 1.25rem' })}>
        {children}
      </div>
    </section>
  )
}

/** Thin indeterminate bar under the header while the content is prepared. */
const SectionProgressBar = () => (
  <span
    aria-hidden="true"
    className={css({
      position: 'absolute',
      left: 0,
      right: 0,
      bottom: 0,
      height: '2px',
      overflow: 'hidden',
      backgroundColor: '#dfe6fa',
    })}
  >
    <span
      className={css({
        display: 'block',
        width: '30%',
        height: '100%',
        borderRadius: '2px',
        backgroundColor: 'primary',
        animation: 'progress_slide 1.4s ease-in-out infinite',
        _motionReduce: { display: 'none' },
      })}
    />
  </span>
)

const UnavailableContent = ({
  kind,
  status,
}: {
  kind: MeetingContentKind
  status: Exclude<MeetingContentStatus, 'available' | 'partial'>
}) => {
  const { t } = useTranslation('meetingHistory')
  return (
    <MeetingSectionState
      tone={sectionStateTone(contentSectionLook(status))}
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

const PartialContentWarning = ({ kind }: { kind: MeetingContentKind }) => {
  const { t } = useTranslation('meetingHistory')

  return (
    <p
      className={css({
        display: 'flex',
        alignItems: 'flex-start',
        gap: '0.375rem',
        margin: 0,
        padding: '0.625rem 0.75rem',
        borderRadius: '8px',
        backgroundColor: 'info',
        color: 'info-foreground',
        fontSize: '0.8125rem',
        lineHeight: '1.25rem',
        '& svg': { flexShrink: 0, marginTop: '0.125rem' },
      })}
    >
      <InformationIcon size={15} aria-hidden="true" />
      {t(`${kind}.partial`)}
    </p>
  )
}

const SummaryBody = ({
  summary,
  view,
  onRetry,
}: {
  summary: MeetingSummary
  view: SummaryView
  onRetry: () => void
}) => {
  const { t } = useTranslation('meetingHistory')
  switch (view) {
    case 'request-pending':
      return <UnavailableContent kind="summary" status="transcribing" />
    case 'request-failed':
      return (
        <MeetingSectionState
          tone="danger"
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
    case 'waiting-transcript':
      return (
        <MeetingSectionState
          title={t('summary.waitingTranscript.title')}
          description={t('summary.waitingTranscript.description')}
        />
      )
  }
  if (!isReadableStatus(view))
    return <UnavailableContent kind="summary" status={view} />

  return (
    <div
      className={css({
        display: 'flex',
        flexDirection: 'column',
        gap: '1rem',
      })}
    >
      {summary.status === 'partial' && <PartialContentWarning kind="summary" />}
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
  if (!isReadableStatus(transcript.status))
    return <UnavailableContent kind="transcript" status={transcript.status} />

  return (
    <>
      {transcript.status === 'partial' && (
        <PartialContentWarning kind="transcript" />
      )}
      <ol
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '1rem',
          margin: 0,
          padding: 0,
          listStyle: 'none',
          marginTop: transcript.status === 'partial' ? '1rem' : 0,
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
