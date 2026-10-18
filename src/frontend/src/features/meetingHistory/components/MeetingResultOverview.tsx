import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import type { MeetingHistoryDetail } from '../api/types'

const noticeClass = css({
  margin: 0,
  color: 'muted-foreground',
  fontSize: '0.8125rem',
})

/**
 * Warnings that concern the whole result. The state of the summary and the
 * transcript shows on their tabs and inside each tab.
 */
export function MeetingResultOverview({
  meeting,
}: {
  meeting: MeetingHistoryDetail
}) {
  const { t } = useTranslation('meetingHistory')
  const endUnconfirmed = !meeting.endedAt
  const refreshing =
    meeting.summaryProjection.refreshPending ||
    meeting.transcriptProjection.refreshPending
  if (!endUnconfirmed && !refreshing) return null
  return (
    <div
      className={css({
        marginBottom: '1.25rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.25rem',
      })}
    >
      {endUnconfirmed && (
        <p role="status" className={noticeClass}>
          {t('result.endUnconfirmed')}
        </p>
      )}
      {refreshing && (
        <p role="status" className={noticeClass}>
          {t('result.refreshing')}
        </p>
      )}
    </div>
  )
}
