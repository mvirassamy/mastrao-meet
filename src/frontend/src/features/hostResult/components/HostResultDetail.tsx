import type { Ref } from 'react'
import { useTranslation } from 'react-i18next'
import { FALLBACK_LANGUAGE } from '@/i18n/languageDetection'
import { CalendarIcon, LockIcon, TimeIcon } from '@/icons'
import {
  MeetingContentSection,
  SummarySheet,
  TranscriptSheet,
} from '@/features/meetingHistory/components/MeetingResultSections'
import { MeetingResultTabs } from '@/features/meetingHistory/components/MeetingResultTabs'
import {
  detailHeader,
  detailMeta,
  detailMetaItem,
  detailTitle,
} from '@/features/meetingHistory/components/meetingDetailLayout'
import {
  formatMeetingDay,
  formatMeetingTime,
} from '@/features/meetingHistory/utils/meetingHistoryFormat'
import { css } from '@/styled-system/css'
import type { HostResult } from '../api/readHostResult'
import { hostSummary, hostTranscript } from '../hostResultContent'

const fromEpochSeconds = (seconds: number) => new Date(seconds * 1000)

/** The host's meeting report, laid out like the meeting history detail. */
export const HostResultDetail = ({
  result,
  headingRef,
}: {
  result: HostResult
  headingRef: Ref<HTMLHeadingElement>
}) => {
  const { t, i18n } = useTranslation('global', { keyPrefix: 'hostResult' })
  const locale = i18n.resolvedLanguage || i18n.language || FALLBACK_LANGUAGE
  const startedAt = fromEpochSeconds(result.started_at)
  const endedAt = fromEpochSeconds(result.ended_at)
  const anonymousSpeaker = (index: number) => t('anonymousSpeaker', { index })

  return (
    <>
      <header className={detailHeader}>
        <h1 ref={headingRef} tabIndex={-1} className={detailTitle}>
          {result.title}
        </h1>
        <p className={detailMeta}>
          <span className={detailMetaItem}>
            <CalendarIcon size={15} aria-hidden="true" />
            {formatMeetingDay(startedAt, locale)}
          </span>
          <span className={detailMetaItem}>
            <TimeIcon size={15} aria-hidden="true" />
            {formatMeetingTime(startedAt, endedAt, locale)}
          </span>
        </p>
        <p
          className={css({
            display: 'flex',
            alignItems: 'flex-start',
            gap: '0.625rem',
            marginTop: '1rem',
            marginBottom: 0,
            padding: '0.625rem 0.875rem',
            border: '1px solid token(colors.border)',
            borderRadius: '12px',
            backgroundColor: 'muted',
            fontSize: '0.8125rem',
            lineHeight: '1.25rem',
            '& svg': { flexShrink: 0, marginTop: '0.125rem', color: 'primary' },
          })}
        >
          <LockIcon size={15} aria-hidden="true" />
          <span>
            <strong className={css({ fontWeight: 600 })}>
              {t('validUntil', {
                date: new Intl.DateTimeFormat(locale, {
                  dateStyle: 'long',
                }).format(fromEpochSeconds(result.access_expires_at)),
              })}
            </strong>{' '}
            {t('doNotForward')}
          </span>
        </p>
      </header>
      <MeetingResultTabs
        states={{}}
        summary={
          <MeetingContentSection
            kind="summary"
            statusKey="status.summary.available"
          >
            <SummarySheet
              summary={hostSummary(result.summary, (kind) =>
                t(`sections.${kind}`)
              )}
              downloadName={t('files.summary')}
            />
          </MeetingContentSection>
        }
        transcript={
          <MeetingContentSection
            kind="transcript"
            statusKey="status.transcript.available"
          >
            <TranscriptSheet
              transcript={hostTranscript(result.transcript, anonymousSpeaker)}
              downloadName={t('files.transcript')}
            />
          </MeetingContentSection>
        }
      />
      <p
        className={css({
          marginTop: '1.75rem',
          marginBottom: 0,
          color: 'muted-foreground',
          fontSize: '0.8125rem',
          lineHeight: '1.25rem',
        })}
      >
        {t('hostOnly')}
      </p>
    </>
  )
}
