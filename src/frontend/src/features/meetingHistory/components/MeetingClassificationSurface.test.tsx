import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import { MeetingHistoryDetailView } from './MeetingHistoryDetailView'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'fr' } }),
}))
vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
vi.mock('./MeetingPersonalNotesSection', () => ({
  MeetingPersonalNotesSection: () => <p>private notes unchanged</p>,
}))
afterEach(cleanup)

describe('meeting classification entry points', () => {
  it('shares a single confirmation flow from the detail and summary while transcription runs', async () => {
    const meetingId = 'meeting_0123456789abcdef'
    const api = vi.mocked(fetchApi)
    api.mockReset()
    api.mockImplementation(async (path) => {
      if (path.includes('/classification/'))
        return {
          version: 1,
          meeting_ref: meetingId,
          matter_ref: null,
          classification_version: 0,
          can_classify: true,
        }
      if (path.startsWith('matters/'))
        return { version: 1, items: [], next_cursor: null }
      return {
        id: meetingId,
        title: 'En cours',
        started_at: '2026-10-16T08:00:00Z',
        ended_at: null,
        summary: { status: 'processing' },
        transcript: { status: 'processing' },
      }
    })
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <MeetingHistoryDetailView meetingId={meetingId} identityKey="user-a" />
      </QueryClientProvider>
    )
    const entries = await screen.findAllByRole('button', {
      name: 'classification.title',
    })
    expect(entries).toHaveLength(2)
    for (const entry of entries) {
      fireEvent.click(entry)
      expect(
        await screen.findByRole('dialog', { name: 'classification.title' })
      ).toBeTruthy()
      expect(screen.getByText('classification.sharing')).toBeTruthy()
      fireEvent.click(
        screen.getByRole('button', { name: 'classification.cancel' })
      )
    }
    expect(
      api.mock.calls.some(([, options]) => options?.method === 'POST')
    ).toBe(false)
    expect(screen.getByText('private notes unchanged')).toBeTruthy()
  })
})
