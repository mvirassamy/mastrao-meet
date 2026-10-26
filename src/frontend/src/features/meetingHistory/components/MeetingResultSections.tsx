import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { InformationIcon, RetryIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import type {
  MeetingContentStatus,
  MeetingSummary,
  MeetingTranscript,
} from '../api/types'
import {
  contentPhase,
  SECTION_ICONS,
  type MeetingContentKind,
} from './meetingContent'
import {
  summaryEditorialStatusKey,
  type SummaryView,
} from './meetingResultState'
import {
  MeetingSectionState,
  type SectionStateLook,
} from './MeetingHistoryStatePanel'
import { MeetingContentToolbar } from './MeetingContentToolbar'
import { MeetingLiveResults } from './MeetingLiveResults'
import { MeetingNativeCoverage } from './MeetingNativeCoverage'
import { MeetingNativeFullTranscript } from './MeetingNativeFullTranscript'
import {
  MeetingNativePartialTranscript,
  MeetingTranscriptContent,
} from './MeetingTranscriptContent'

const isReadableStatus = (
  status: MeetingContentStatus
): status is 'available' | 'partial' =>
  status === 'available' || status === 'partial'

/** How a tab without readable content shows its status. */
const unavailableLook = (status: MeetingContentStatus): SectionStateLook => {
  switch (contentPhase(status)) {
    case 'pending':
      return 'pending'
    case 'failed':
      return 'failed'
    default:
      return 'empty'
  }
}

export const MeetingContentSection = ({
  kind,
  statusKey,
  statusLabel,
  children,
}: {
  kind: MeetingContentKind
  /** Translation key of the status announced for the section. */
  statusKey: string
  /** A specific processing state can refine the announced status. */
  statusLabel?: string
  children: ReactNode
}) => {
  const { t } = useTranslation('meetingHistory')
  const headingId = `meeting-${kind}-heading`

  return (
    <section
      id={`meeting-${kind}`}
      aria-labelledby={headingId}
      data-section={kind}
      className={css({ minWidth: 0 })}
    >
      {/* The tab already shows the title and its 3D icon. */}
      <h2 id={headingId} className={css({ srOnly: true })}>
        {t(`${kind}.title`)}
      </h2>
      <span
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className={css({ srOnly: true })}
      >
        {statusLabel ?? t(statusKey)}
      </span>
      {children}
    </section>
  )
}

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
      icon={SECTION_ICONS[kind]}
      look={unavailableLook(status)}
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

export const SummaryBody = ({
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
          icon={SECTION_ICONS.summary}
          look="failed"
          title={t('summary.requestFailed.title')}
          description={t('summary.requestFailed.description')}
          action={
            <Button
              size="sm"
              variant="outline"
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
          icon={SECTION_ICONS.summary}
          look="pending"
          title={t('summary.waitingTranscript.title')}
          description={t('summary.waitingTranscript.description')}
        />
      )
  }
  if (!isReadableStatus(view))
    return <UnavailableContent kind="summary" status={view} />

  return (
    <div>
      {summary.status === 'partial' && (
        <div className={css({ marginBottom: '1rem' })}>
          <PartialContentWarning kind="summary" />
        </div>
      )}
      <MeetingContentToolbar
        note={
          <>
            <InformationIcon size={15} aria-hidden="true" />
            {t(summaryEditorialStatusKey(summary.productState))}
          </>
        }
        copyText={summaryPlainText(summary)}
      />
      <div className={css({ borderTop: '1px solid token(colors.border)' })}>
        {summary.paragraphs.length > 0 && (
          <SummaryRow
            label={<p className={rubricLabel}>{t('summary.brief')}</p>}
          >
            {summary.paragraphs.map((paragraph, index) => (
              <p key={index} className={bodyTextClass}>
                {paragraph}
              </p>
            ))}
          </SummaryRow>
        )}
        {summary.sections.map((section) => (
          <SummaryRow
            key={section.title}
            label={<h3 className={rubricLabel}>{section.title}</h3>}
          >
            {section.items.length === 0 && (
              <p className={bodyTextClass}>{t('summary.emptySection')}</p>
            )}
            <ul
              className={css({
                display: 'flex',
                flexDirection: 'column',
                gap: '0.375rem',
                margin: 0,
                padding: 0,
                listStyle: 'none',
              })}
            >
              {section.items.map((item, index) => (
                <li key={index} className={bodyTextClass}>
                  {item}
                </li>
              ))}
            </ul>
          </SummaryRow>
        ))}
      </div>
    </div>
  )
}

const rubricLabel = css({
  margin: 0,
  paddingTop: '0.125rem',
  color: 'muted-foreground',
  fontSize: '0.8125rem',
  lineHeight: '1.25rem',
  fontWeight: 600,
})

/**
 * One rubric of the summary sheet: its label on the left, content on the
 * right. Summary sections label their rubric with a heading; the brief is
 * not a section and keeps a plain label.
 */
const SummaryRow = ({
  label,
  children,
}: {
  label: ReactNode
  children: ReactNode
}) => (
  <div
    className={css({
      display: 'grid',
      gridTemplateColumns: {
        base: 'minmax(0, 1fr)',
        sm: '8rem minmax(0, 1fr)',
      },
      columnGap: '1.25rem',
      rowGap: '0.25rem',
      paddingY: '0.875rem',
      borderBottom: '1px solid token(colors.border)',
    })}
  >
    {label}
    <div
      className={css({
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        minWidth: 0,
      })}
    >
      {children}
    </div>
  </div>
)

/** The summary as plain text, ready to paste into an e-mail. */
const summaryPlainText = (summary: MeetingSummary) =>
  [
    summary.paragraphs.join('\n\n'),
    ...summary.sections.map((section) =>
      [section.title, ...section.items.map((item) => `- ${item}`)].join('\n')
    ),
  ]
    .filter(Boolean)
    .join('\n\n')

export const TranscriptBody = ({
  transcript,
  meetingRef,
}: {
  transcript: MeetingTranscript
  meetingRef?: string
}) => {
  const nativeCoverage = transcript.nativeCoverage && (
    <MeetingNativeCoverage coverage={transcript.nativeCoverage} />
  )
  const hasCanonicalContent =
    isReadableStatus(transcript.status) && transcript.segments.length > 0
  const nativePartial = transcript.status !== 'completed_empty' &&
    !hasCanonicalContent &&
    transcript.nativePartial && (
      <MeetingNativePartialTranscript
        partial={transcript.nativePartial}
        coverage={transcript.nativeCoverage}
      />
    )
  const liveSources = transcript.liveSources ?? []
  const liveContent = (liveSources.length > 0 ||
    transcript.liveObservedDomain) && (
    <MeetingLiveResults meetingRef={meetingRef} transcript={transcript} />
  )
  const consultationStatus = nativePartial ? 'transcribing' : transcript.status
  const unavailableContent = !isReadableStatus(consultationStatus) && (
    <UnavailableContent kind="transcript" status={consultationStatus} />
  )
  if (!isReadableStatus(transcript.status) || nativePartial) {
    return (
      <>
        {nativeCoverage}
        {nativePartial}
        {liveContent}
        {transcript.projection.processing_state === 'temporarily_suspended' &&
        transcript.status === 'transcribing' ? (
          <SuspendedTranscriptState />
        ) : (
          (!liveContent || nativePartial) && unavailableContent
        )}
      </>
    )
  }
  return (
    <>
      {nativeCoverage}
      <CanonicalTranscriptBody
        transcript={transcript}
        meetingRef={meetingRef}
      />
      {liveContent}
    </>
  )
}

const CanonicalTranscriptBody = ({
  transcript,
  meetingRef,
}: {
  transcript: MeetingTranscript
  meetingRef?: string
}) => (
  <>
    {transcript.status === 'partial' && (
      <div className={css({ marginBottom: '1rem' })}>
        <PartialContentWarning kind="transcript" />
      </div>
    )}
    <MeetingNativeFullTranscript
      meetingRef={meetingRef}
      transcript={transcript}
      snapshot={transcript.nativeFullSnapshot}
    >
      <MeetingTranscriptContent
        segments={transcript.segments}
        truncated={transcript.truncated}
        nativeCoverage={transcript.nativeCoverage}
      />
    </MeetingNativeFullTranscript>
  </>
)

const SuspendedTranscriptState = () => {
  const { t } = useTranslation('meetingHistory')
  return (
    <MeetingSectionState
      icon={SECTION_ICONS.transcript}
      look="empty"
      title={t('transcript.temporarilySuspended.title')}
      description={t('transcript.temporarilySuspended.description')}
    />
  )
}
