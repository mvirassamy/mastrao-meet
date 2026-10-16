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
  it('keeps navigation available during processing and does not claim the call ended', () => {
    render(<MeetingResultOverview meeting={detail()} />)
    expect(
      screen.getByRole('link', { name: 'summary.title' }).getAttribute('href')
    ).toBe('#meeting-summary')
    expect(
      screen
        .getByRole('link', { name: 'result.privateNotes' })
        .getAttribute('href')
    ).toBe('#meeting-notes')
    expect(screen.getByRole('status').textContent).toContain(
      'result.endUnconfirmed'
    )
    expect(screen.getByRole('status').textContent).toContain(
      'status.summary.failed'
    )
    expect(screen.getByRole('status').textContent).toContain(
      'status.transcript.transcribing'
    )
  })
  it('marks a retained older projection as awaiting verification', () => {
    const meeting = detail()
    meeting.endedAt = new Date('2026-10-16T09:00:00Z')
    meeting.summaryProjection.refreshPending = true
    render(<MeetingResultOverview meeting={meeting} />)
    expect(screen.getByText('result.refreshing')).toBeTruthy()
    expect(screen.getAllByRole('status')[0].textContent).toContain(
      'result.ended'
    )
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
