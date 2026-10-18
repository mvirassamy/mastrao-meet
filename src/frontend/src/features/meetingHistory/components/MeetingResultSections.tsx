import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { InformationIcon, RetryIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import type {
  MeetingContentStatus,
  MeetingSummary,
  MeetingTranscript,
  MeetingTranscriptSegment,
} from '../api/types'
import { formatTranscriptTimestamp } from '../utils/meetingHistoryFormat'
import { SECTION_ICONS, type MeetingContentKind } from './meetingContent'
import {
  contentSectionLook,
  summaryEditorialStatusKey,
  type SectionLook,
  type SummaryView,
} from './meetingResultState'
import { MeetingSectionState } from './MeetingHistoryStatePanel'
import {
  sectionBody,
  sectionCard,
  sectionHeader,
  sectionTitle,
} from './meetingDetailLayout'

const isReadableStatus = (
  status: MeetingContentStatus
): status is 'available' | 'partial' =>
  status === 'available' || status === 'partial'

/** Dot of a section state: none while pending, the header bar shows it. */
const sectionStateTone = (look: SectionLook) => {
  if (look === 'failed') return 'danger'
  if (look === 'empty') return 'neutral'
  return undefined
}

export const MeetingContentSection = ({
  kind,
  statusKey,
  look,
  children,
}: {
  kind: MeetingContentKind
  /** Translation key of the status announced for the section. */
  statusKey: string
  look: SectionLook
  children: ReactNode
}) => {
  const { t } = useTranslation('meetingHistory')
  const headingId = `meeting-${kind}-heading`

  return (
    <section
      id={`meeting-${kind}`}
      aria-labelledby={headingId}
      data-section={kind}
      className={sectionCard}
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
        <h2 id={headingId} className={sectionTitle}>
          {t(`${kind}.title`)}
        </h2>
        <span
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className={css({ srOnly: true })}
        >
          {t(statusKey)}
        </span>
        {look === 'pending' && <SectionProgressBar />}
      </div>
      <div className={sectionBody}>{children}</div>
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
        {t(summaryEditorialStatusKey(summary.productState))}
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
          {section.items.length === 0 && (
            <p className={bodyTextClass}>{t('summary.emptySection')}</p>
          )}
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

export const TranscriptBody = ({
  transcript,
}: {
  transcript: MeetingTranscript
}) => {
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
