import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import { MeetingHistoryDetailView } from './MeetingHistoryDetailView'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'fr' },
  }),
}))
vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
vi.mock('@/api/useConfig', () => ({ useConfig: () => ({ data: {} }) }))
// Narrow screen: the assistant opens over the page.
vi.mock('@/features/rooms/livekit/hooks/useMediaQuery', () => ({
  useMediaQuery: () => false,
}))
vi.mock('@/features/meeting-assistant/MeetingAssistant', () => ({
  MeetingAssistant: ({ onClose }: { onClose: () => void }) => (
    <button type="button" onClick={onClose}>
      close assistant
    </button>
  ),
}))
vi.mock('./MeetingPersonalNotesSection', () => ({
  MeetingPersonalNotesSection: () => null,
}))
afterEach(cleanup)

describe('assistant on a narrow screen', () => {
  it('opens over the page from the header button and closes back to it', async () => {
    const meetingId = 'meeting_0123456789abcdef'
    vi.mocked(fetchApi).mockImplementation(async (path) => {
      if (path.includes('/classification/'))
        return {
          version: 1,
          meeting_ref: meetingId,
          matter_ref: null,
          matter_path: null,
          classification_version: 0,
          can_classify: false,
        }
      return {
        id: meetingId,
        title: 'Réunion',
        started_at: '2026-10-16T08:00:00Z',
        ended_at: '2026-10-16T09:00:00Z',
        summary: { status: 'failed' },
        transcript: { status: 'failed' },
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
    const open = await screen.findByRole('button', { name: 'result.assistant' })
    expect(screen.queryByRole('dialog')).toBeNull()

    fireEvent.click(open)
    expect(
      await screen.findByRole('dialog', { name: 'result.assistant' })
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'close assistant' }))
    expect(
      await screen.findByRole('button', { name: 'result.assistant' })
    ).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
