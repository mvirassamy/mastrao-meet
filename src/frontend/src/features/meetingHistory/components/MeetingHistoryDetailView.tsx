import { FALLBACK_LANGUAGE } from '@/i18n/languageDetection'
import { useEffect, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'wouter'
import { useHistoryState } from 'wouter/use-browser-location'
import {
  ChevronLeftIcon,
  CalendarIcon,
  FileSearchIcon,
  RetryIcon,
  TimeIcon,
} from '@/icons'
import { ApiError } from '@/api/ApiError'
import { Button, LinkButton } from '@/primitives'
import { ClassificationDialog } from '@/features/meeting-classification/ClassificationDialog'
import { ClassificationStatus } from '@/features/meeting-classification/ClassificationStatus'
import { useMeetingClassification } from '@/features/meeting-classification/useMeetingClassification'
import { MeetingAssistant } from '@/features/meeting-assistant/MeetingAssistant'
import { css } from '@/styled-system/css'
import type { MeetingHistoryDetail } from '../api/types'
import {
  shouldRequestSummary,
  useMeetingHistoryDetail,
  useRequestMeetingSummary,
} from '../api/useMeetingHistory'
import {
  isAuthRequiredError,
  useLoginRedirectOnAuthError,
} from '../api/authRedirect'
import { meetingBackPath } from '../utils/meetingOrigin'
import {
  formatMeetingDay,
  formatMeetingTime,
} from '../utils/meetingHistoryFormat'
import { MeetingHistoryStatePanel } from './MeetingHistoryStatePanel'
import { MeetingHistoryDetailSkeleton } from './MeetingHistoryDetailSkeleton'
import { MeetingRecordingSection } from './MeetingRecordingSection'
import { MeetingPersonalNotesSection } from './MeetingPersonalNotesSection'
import { detailHeader, detailSections } from './meetingDetailLayout'
import { MeetingResultOverview } from './MeetingResultOverview'
import {
  MeetingContentSection,
  SummaryBody,
  TranscriptBody,
} from './MeetingResultSections'
import {
  contentSectionLook,
  summaryView,
  summaryStatusKey,
  summarySectionLook,
  type SummaryRequestState,
} from './meetingResultState'

const isNotFoundError = (error: unknown) =>
  error instanceof ApiError && [403, 404].includes(error.statusCode)

export const MeetingHistoryDetailView = ({
  meetingId,
  timeZone,
  identityKey,
}: {
  meetingId: string
  timeZone?: string
  identityKey?: string
}) => {
  const { t } = useTranslation('meetingHistory')
  const query = useMeetingHistoryDetail(meetingId)
  const backPath = meetingBackPath(useHistoryState())
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

  let summaryRequestState: SummaryRequestState = 'idle'
  if (summaryRequest.isPending) summaryRequestState = 'pending'
  else if (
    summaryRequest.isError &&
    !(
      summaryRequest.error instanceof ApiError &&
      [401, 404, 409].includes(summaryRequest.error.statusCode)
    )
  )
    summaryRequestState = 'failed'

  let content: ReactNode
  if (query.isPending || isAuthRequiredError(query.error)) {
    content = <MeetingHistoryDetailSkeleton label={t('detail.loading')} />
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
        key={`${identityKey ?? ''}:${query.data.id}`}
        meeting={query.data}
        timeZone={timeZone}
        identityKey={identityKey}
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
        to={backPath}
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
  identityKey,
}: {
  meeting: MeetingHistoryDetail
  timeZone?: string
  headingRef: React.Ref<HTMLHeadingElement>
  summaryRequestState: SummaryRequestState
  onRetrySummary: () => void
  identityKey?: string
}) => {
  const { t, i18n } = useTranslation('meetingHistory')
  const classification = useMeetingClassification(meeting.id, identityKey)
  const locale = i18n.resolvedLanguage || i18n.language || FALLBACK_LANGUAGE
  const summaryState = summaryView(
    meeting.summary,
    meeting.transcript.status,
    summaryRequestState
  )
  const metaItemClass = css({
    display: 'inline-flex',
    alignItems: 'center',
    gap: '0.375rem',
  })

  return (
    <>
      <header
        className={`${detailHeader} ${css({
          display: 'flex',
          flexDirection: { base: 'column', sm: 'row' },
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: '1rem',
        })}`}
      >
        <div className={css({ minWidth: 0 })}>
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
              {formatMeetingTime(
                meeting.startedAt,
                meeting.endedAt,
                locale,
                timeZone
              )}
            </span>
            {meeting.participantCount !== null && (
              <span className={metaItemClass}>
                {t('participants', { count: meeting.participantCount })}
              </span>
            )}
          </p>
        </div>
        {meeting.recordingProjected === true && (
          <MeetingRecordingSection
            recording={meeting.recording}
            timeZone={timeZone}
          />
        )}
      </header>
      <MeetingResultOverview
        meeting={meeting}
        hasAssistant={Boolean(identityKey)}
      />
      {identityKey && (
        <>
          <ClassificationStatus
            classification={classification.current.data}
            matter={classification.currentMatter}
            loading={classification.current.isPending}
            failed={classification.current.isError}
            onRetry={() => void classification.current.refetch()}
            onOpen={classification.open}
          />
          {classification.current.data?.can_classify &&
            !classification.current.isError && (
              <ClassificationDialog
                key={classification.isOpen ? 'open' : 'closed'}
                {...classification}
                onClose={classification.close}
              />
            )}
        </>
      )}
      <div id="meeting-notes" className={css({ marginBottom: '1.25rem' })}>
        <MeetingPersonalNotesSection
          endpoint={`/meetings/history/${encodeURIComponent(meeting.id)}/personal-note/`}
        />
      </div>
      <div className={detailSections}>
        <MeetingContentSection
          kind="summary"
          statusKey={summaryStatusKey(summaryState)}
          look={summarySectionLook(summaryState)}
        >
          {identityKey &&
            classification.current.data?.can_classify &&
            !classification.current.isError && (
              <Button size="sm" variant="outline" onPress={classification.open}>
                {t('classification.title')}
              </Button>
            )}
          <SummaryBody
            summary={meeting.summary}
            view={summaryState}
            onRetry={onRetrySummary}
          />
          {identityKey && (
            <LinkButton variant="outline" size="sm" href="#meeting-assistant">
              {t('result.assistant')}
            </LinkButton>
          )}
        </MeetingContentSection>
        <MeetingContentSection
          kind="transcript"
          statusKey={`status.transcript.${meeting.transcript.status}`}
          look={contentSectionLook(meeting.transcript.status)}
        >
          <TranscriptBody transcript={meeting.transcript} />
        </MeetingContentSection>
      </div>
      {identityKey && (
        <MeetingAssistant meetingId={meeting.id} identityKey={identityKey} />
      )}
    </>
  )
}
