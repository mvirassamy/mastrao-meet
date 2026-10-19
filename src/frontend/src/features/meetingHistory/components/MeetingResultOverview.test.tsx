import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { normalizeHistoryDetail } from '../api/meetingHistoryApi'
import { MeetingResultOverview } from './MeetingResultOverview'
import { SummaryBody } from './MeetingResultSections'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(cleanup)

const detail = () => {
  const meeting = normalizeHistoryDetail({
    id: 'm1',
    title: 'Réunion',
    started_at: '2026-10-16T08:00:00Z',
    ended_at: null,
    summary_status: 'failed',
    transcript_status: 'processing',
  })
  if (!meeting) throw new Error('Invalid test meeting')
  return meeting
}

describe('meeting result overview', () => {
  it('warns that the end of the call is not confirmed', () => {
    render(<MeetingResultOverview meeting={detail()} />)
    expect(screen.getByRole('status').textContent).toBe('result.endUnconfirmed')
  })
  it('stays silent once the call ended and the result is current', () => {
    const meeting = detail()
    meeting.endedAt = new Date('2026-10-16T09:00:00Z')
    const { container } = render(<MeetingResultOverview meeting={meeting} />)
    expect(container.textContent).toBe('')
  })
  it('marks a retained older projection as awaiting verification', () => {
    const meeting = detail()
    meeting.endedAt = new Date('2026-10-16T09:00:00Z')
    meeting.summaryProjection.refreshPending = true
    render(<MeetingResultOverview meeting={meeting} />)
    expect(screen.getByRole('status').textContent).toBe('result.refreshing')
  })
  it('keeps an explicitly empty result section visible without inventing a decision', () => {
    const meeting = normalizeHistoryDetail({
      id: 'm1',
      started_at: '2026-10-16T08:00:00Z',
      summary: {
        status: 'available',
        text: 'Résumé de la discussion.',
        sections: [{ title: 'Décisions', items: [] }],
      },
    })!
    render(
      <SummaryBody
        summary={meeting.summary}
        view="available"
        onRetry={vi.fn()}
      />
    )
    expect(screen.getByRole('heading', { name: 'Décisions' })).toBeTruthy()
    expect(screen.getByText('summary.emptySection')).toBeTruthy()
  })
})
