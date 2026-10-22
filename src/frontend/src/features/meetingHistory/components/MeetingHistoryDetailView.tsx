import { FALLBACK_LANGUAGE } from '@/i18n/languageDetection'
import { useEffect, useRef, useState, type ReactNode } from 'react'
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
import { useConfig } from '@/api/useConfig'
import { Button } from '@/primitives'
import { logout } from '@/features/auth/utils/logout'
import { classificationMatterUrl } from '@/features/meeting-classification/classificationMatterUrl'
import { ClassificationDialog } from '@/features/meeting-classification/ClassificationDialog'
import { ClassificationStatus } from '@/features/meeting-classification/ClassificationStatus'
import { useMeetingClassification } from '@/features/meeting-classification/useMeetingClassification'
import { MeetingAssistant } from '@/features/meeting-assistant/MeetingAssistant'
import { assistantReadiness } from '@/features/meeting-assistant/assistantReadiness'
import { useMediaQuery } from '@/features/rooms/livekit/hooks/useMediaQuery'
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
import { MeetingAssistantDrawer } from './MeetingAssistantDrawer'
import { ASSISTANT_ICON } from './meetingContent'
import { MeetingRecordingSection } from './MeetingRecordingSection'
import { MeetingPersonalNotesSection } from './MeetingPersonalNotesSection'
import { detailHeader } from './meetingDetailLayout'
import { MeetingResultTabs } from './MeetingResultTabs'
import { MeetingResultOverview } from './MeetingResultOverview'
import { MeetingContentSection, SummaryBody } from './MeetingResultSections'
import { MeetingTranscriptSection } from './MeetingTranscriptSection'
import {
  contentSectionLook,
  summaryView,
  summaryStatusKey,
  summarySectionLook,
  type SummaryRequestState,
} from './meetingResultState'

const isNotFoundError = (error: unknown) =>
  error instanceof ApiError && [403, 404].includes(error.statusCode)

/** From this width the assistant sits beside the page (Panda `xl`). */
const DOCKED_ASSISTANT_QUERY = '(min-width: 80em)'

export const MeetingHistoryDetailView = ({
  meetingId,
  timeZone,
  identityKey,
  accountEmail,
}: {
  meetingId: string
  timeZone?: string
  identityKey?: string
  accountEmail?: string
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
  // Wide screens show the assistant beside the page from the start; narrower
  // ones open it over the page on demand.
  const docked = useMediaQuery(DOCKED_ASSISTANT_QUERY)
  const [dockedOpen, setDockedOpen] = useState(true)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const setAssistantOpen = docked ? setDockedOpen : setDrawerOpen
  // The header button stays in place while the drawer is open, so the
  // drawer returns focus to it. Closing the docked panel removes the
  // panel, so focus moves back to the button explicitly.
  const assistantButtonRef = useRef<HTMLButtonElement>(null)
  const focusAssistantButton = useRef(false)
  useEffect(() => {
    if (dockedOpen || !focusAssistantButton.current) return
    focusAssistantButton.current = false
    assistantButtonRef.current?.focus()
  }, [dockedOpen])
  const closeAssistant = () => {
    if (docked) focusAssistantButton.current = true
    setAssistantOpen(false)
  }

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
        description={
          notFound && accountEmail
            ? t('detail.notFound.descriptionWithAccount', {
                email: accountEmail,
              })
            : t(
                notFound
                  ? 'detail.notFound.description'
                  : 'detail.error.description'
              )
        }
        action={
          notFound && accountEmail ? (
            <Button variant="secondary" onPress={() => void logout()}>
              {t('detail.notFound.switch')}
            </Button>
          ) : notFound ? undefined : (
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
        assistantInPage={docked && dockedOpen}
        assistantButtonRef={assistantButtonRef}
        onOpenAssistant={() => setAssistantOpen(true)}
      />
    )
  }

  const page = (
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
  const assistant = identityKey && detail && (
    <MeetingAssistant
      key={`${identityKey}:${detail.id}`}
      meetingId={detail.id}
      identityKey={identityKey}
      readiness={assistantReadiness(detail.transcriptStatus)}
      onClose={closeAssistant}
    />
  )
  // The page keeps one place in the tree whatever the screen width, so an
  // open dialog or a scrolled transcript survives a resize. Wide screens put
  // the assistant beside it, narrower ones over it.
  return (
    <div className={css({ display: 'flex', flex: 1, minHeight: 0 })}>
      <div className={css({ flex: 1, minWidth: 0, overflowY: 'auto' })}>
        {page}
      </div>
      {assistant && docked && dockedOpen && (
        <aside
          className={css({
            width: '24rem',
            flexShrink: 0,
            borderLeft: '1px solid token(colors.border)',
            backgroundColor: 'card',
          })}
        >
          {assistant}
        </aside>
      )}
      {assistant && !docked && (
        <MeetingAssistantDrawer
          isOpen={drawerOpen}
          onOpenChange={setDrawerOpen}
          label={t('result.assistant')}
        >
          {assistant}
        </MeetingAssistantDrawer>
      )}
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
  assistantInPage,
  assistantButtonRef,
  onOpenAssistant,
}: {
  meeting: MeetingHistoryDetail
  timeZone?: string
  headingRef: React.Ref<HTMLHeadingElement>
  summaryRequestState: SummaryRequestState
  onRetrySummary: () => void
  identityKey?: string
  /** The assistant is docked beside the page, so no button is needed. */
  assistantInPage: boolean
  assistantButtonRef: React.Ref<HTMLButtonElement>
  onOpenAssistant: () => void
}) => {
  const { t, i18n } = useTranslation('meetingHistory')
  const classification = useMeetingClassification(meeting.id, identityKey)
  const { data: config } = useConfig()
  const checkingClassification =
    classification.current.isPending || classification.current.isFetching
  let currentClassification = classification.current.data
  if (checkingClassification || classification.current.isError)
    currentClassification = undefined
  const matterUrl = classificationMatterUrl(
    currentClassification,
    config?.mastrao_platform_origin
  )
  const locale = i18n.resolvedLanguage || i18n.language || FALLBACK_LANGUAGE
  const showAssistantButton = Boolean(identityKey) && !assistantInPage
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

  const classificationLine = identityKey && (
    <>
      <ClassificationStatus
        classification={currentClassification}
        matter={classification.currentMatter}
        matterUrl={matterUrl}
        loading={checkingClassification}
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
  )
  return (
    // The header follows the width the page actually gets, which a docked
    // assistant or a tablet screen narrows, rather than the screen width.
    <div
      className={css({
        minWidth: 0,
        containerType: 'inline-size',
        containerName: 'meeting-detail',
      })}
    >
      <header
        className={`${detailHeader} ${css({
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: '1rem',
          // Below this width the title keeps the full line and the actions
          // move under it.
          '@container meeting-detail (min-width: 46rem)': {
            flexDirection: 'row',
          },
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
          {classificationLine}
        </div>
        <div
          className={css({
            display: 'flex',
            // Both actions stay on one line; the video date sits under them.
            flexWrap: 'wrap',
            alignItems: 'flex-start',
            gap: '0.5rem',
            flexShrink: 0,
            '@container meeting-detail (min-width: 46rem)': {
              flexWrap: 'nowrap',
            },
          })}
        >
          {/* A meeting without a recording shows nothing about video. */}
          {meeting.recordingProjected === true &&
            meeting.recording.status !== 'absent' && (
              <MeetingRecordingSection
                recording={meeting.recording}
                timeZone={timeZone}
                align={showAssistantButton ? 'start' : 'end'}
              />
            )}
          {showAssistantButton && (
            <Button
              ref={assistantButtonRef}
              size="sm"
              onPress={onOpenAssistant}
            >
              <img
                src={ASSISTANT_ICON}
                alt=""
                aria-hidden="true"
                width={20}
                height={20}
              />
              {t('result.assistant')}
            </Button>
          )}
        </div>
      </header>
      <MeetingResultOverview meeting={meeting} />
      <MeetingResultTabs
        states={{
          summary: summarySectionLook(summaryState),
          transcript: contentSectionLook(meeting.transcript.status),
        }}
        summary={
          <MeetingContentSection
            kind="summary"
            statusKey={summaryStatusKey(summaryState)}
          >
            <SummaryBody
              summary={meeting.summary}
              view={summaryState}
              onRetry={onRetrySummary}
            />
          </MeetingContentSection>
        }
        transcript={
          <MeetingTranscriptSection transcript={meeting.transcript} />
        }
        notes={
          <MeetingPersonalNotesSection
            inTab
            endpoint={`/meetings/history/${encodeURIComponent(meeting.id)}/personal-note/`}
          />
        }
      />
    </div>
  )
}
