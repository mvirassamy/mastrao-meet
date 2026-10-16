import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import type { MeetingHistoryDetail } from '../api/types'

/** Navigation and processing remain available even when the summary fails. */
export function MeetingResultOverview({
  meeting,
  hasAssistant = false,
}: {
  meeting: MeetingHistoryDetail
  hasAssistant?: boolean
}) {
  const { t } = useTranslation('meetingHistory')
  const refreshing =
    meeting.summaryProjection.refreshPending ||
    meeting.transcriptProjection.refreshPending
  return (
    <div
      className={css({
        marginBottom: '1.25rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.75rem',
      })}
    >
      <nav
        aria-label={t('result.navigation')}
        className={css({
          display: 'flex',
          flexWrap: 'wrap',
          gap: '1rem',
          fontSize: '0.875rem',
        })}
      >
        <a href="#meeting-summary">{t('summary.title')}</a>
        <a href="#meeting-transcript">{t('transcript.title')}</a>
        <a href="#meeting-notes">{t('result.privateNotes')}</a>
        {hasAssistant && (
          <a href="#meeting-assistant">{t('result.assistant')}</a>
        )}
      </nav>
      <p
        role="status"
        className={css({
          margin: 0,
          color: 'muted-foreground',
          fontSize: '0.8125rem',
        })}
      >
        {t(meeting.endedAt ? 'result.ended' : 'result.endUnconfirmed')}
        {' · '}
        {t(`status.summary.${meeting.summary.status}`)}
        {' · '}
        {t(`status.transcript.${meeting.transcript.status}`)}
      </p>
      {refreshing && <p role="status">{t('result.refreshing')}</p>}
    </div>
  )
}
