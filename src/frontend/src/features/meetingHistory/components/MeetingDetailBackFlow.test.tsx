import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Route, Switch } from 'wouter'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import { DayMeetingEvent } from '@/features/home/components/authenticated/DayMeetingEvent'
import type { MeetingContentProjection, MeetingHistoryItem } from '../api/types'
import { MEETING_HISTORY_PATH } from '../paths'
import { MeetingHistoryDetailView } from './MeetingHistoryDetailView'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'fr', resolvedLanguage: 'fr' },
  }),
}))

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))

const projection: MeetingContentProjection = {
  version: 1,
  state: 'available',
  revision: 1,
  digest: null,
  source: null,
}

const item: MeetingHistoryItem = {
  id: 'point-client',
  title: 'Point client',
  startedAt: new Date('2026-10-05T11:00:00Z'),
  endedAt: new Date('2026-10-05T11:30:00Z'),
  participantCount: null,
  participantNames: [],
  summaryStatus: 'available',
  transcriptStatus: 'available',
  summaryProjection: projection,
  transcriptProjection: projection,
}

/** The real browser routing, as in the app: home day, then meeting detail. */
const renderApp = () => {
  vi.mocked(fetchApi).mockResolvedValue({
    id: item.id,
    title: item.title,
    started_at: '2026-10-05T11:00:00Z',
    ended_at: '2026-10-05T11:30:00Z',
    summary_status: 'not_started',
    transcript_status: 'not_started',
    summary: { status: 'not_started' },
    transcript: { status: 'not_started' },
  })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Switch>
        <Route path="/">
          <ul>
            <DayMeetingEvent item={item} color="#2d5be3" locale="fr" />
          </ul>
        </Route>
        <Route<{ meetingId: string }>
          path={`${MEETING_HISTORY_PATH}/:meetingId`}
        >
          {({ meetingId }) => (
            <MeetingHistoryDetailView meetingId={meetingId} />
          )}
        </Route>
      </Switch>
    </QueryClientProvider>
  )
}

afterEach(() => {
  cleanup()
  window.history.replaceState(null, '', '/')
})

describe('meeting detail back link', () => {
  it('returns to the home day the meeting was opened from', async () => {
    window.history.replaceState(null, '', '/?jour=2026-10-05')
    renderApp()

    fireEvent.click(screen.getByRole('link', { name: /Point client/ }))
    const back = await screen.findByRole('link', { name: 'detail.back' })
    expect(back.getAttribute('href')).toBe('/?jour=2026-10-05')

    fireEvent.click(back)
    expect(window.location.pathname + window.location.search).toBe(
      '/?jour=2026-10-05'
    )
    expect(
      await screen.findByRole('link', { name: /Point client/ })
    ).toBeTruthy()
  })

  it('returns to the history when the detail is opened directly', async () => {
    window.history.replaceState(
      null,
      '',
      `${MEETING_HISTORY_PATH}/point-client`
    )
    renderApp()

    const back = await screen.findByRole('link', { name: 'detail.back' })
    expect(back.getAttribute('href')).toBe(MEETING_HISTORY_PATH)
  })
})
