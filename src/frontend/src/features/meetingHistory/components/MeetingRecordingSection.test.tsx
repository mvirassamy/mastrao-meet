import { act, cleanup, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import { normalizeMeetingRecording } from '../api/meetingRecording'
import { normalizeHistoryPage } from '../api/meetingHistoryApi'
import { MeetingHistoryDetailView } from './MeetingHistoryDetailView'
import { MeetingRecordingSection } from './MeetingRecordingSection'
import { MeetingContentStatusIcons } from './MeetingContentStatusIcon'
import { MeetingRowLink } from './MeetingRowLink'

const config = vi.hoisted(() => ({
  origin: 'https://platform.mastrao.test' as string | undefined,
}))
vi.mock('@/api/useConfig', () => ({
  useConfig: () => ({ data: { mastrao_platform_origin: config.origin } }),
}))
vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { date?: string }) =>
      options?.date ? `${key}: ${options.date}` : key,
    i18n: { language: 'en', resolvedLanguage: 'en' },
  }),
}))

const available = {
  status: 'available',
  retention_expires_at: 4_097_210_400,
  access: {
    action_path: '/api/orgs/cabinet-test/meetings/recording/access',
    matter_ref: 'matter_0123456789',
    meeting_ref: 'meeting_01234567',
    recording_ref: 'recording_012345',
    artifact_ref: 'artifact_01234567',
  },
}

beforeEach(() => {
  config.origin = 'https://platform.mastrao.test'
})
afterEach(cleanup)

describe('recording history UI', () => {
  it('hides video UI for an older response without a video projection', () => {
    const item = normalizeHistoryPage({
      results: [{ id: 'm1', started_at: '2026-10-11T10:00:00Z' }],
    }).items[0]
    const linkRef = createRef<HTMLAnchorElement>()
    render(
      <>
        <MeetingRowLink item={item} linkRef={linkRef} />
        <MeetingContentStatusIcons item={item} linkRef={linkRef} />
      </>
    )
    expect(document.querySelector('[data-recording-status]')).toBeNull()
    expect(screen.getByRole('link').textContent).not.toContain(
      'status.recording'
    )
  })

  it('uses a native cross-origin POST with exact references, a new tab and noopener', () => {
    const view = render(
      <MeetingRecordingSection
        recording={normalizeMeetingRecording(available)}
        timeZone="UTC"
      />
    )
    const button = screen.getByRole('button', { name: 'recording.open' })
    expect(button.getAttribute('type')).toBe('submit')
    const form = button.closest('form')!
    expect(form.method).toBe('post')
    expect(form.action).toBe(config.origin + available.access.action_path)
    expect(form.target).toBe('_blank')
    expect(form.getAttribute('rel')).toBe('noopener')
    const formData = Object.fromEntries(new FormData(form))
    expect(formData).toEqual({
      matter_ref: 'matter_0123456789',
      meeting_ref: 'meeting_01234567',
      recording_ref: 'recording_012345',
      artifact_ref: 'artifact_01234567',
      command_id: expect.stringMatching(/^recording_access_[a-f0-9]{32}$/),
    })
    view.rerender(
      <MeetingRecordingSection
        recording={normalizeMeetingRecording(available)}
        timeZone="UTC"
      />
    )
    expect(
      new FormData(
        screen.getByRole('button', { name: 'recording.open' }).closest('form')!
      ).get('command_id')
    ).toBe(formData.command_id)
    expect(
      screen.getByText(/recording.retainedUntil: November 1, 2099/)
    ).toBeTruthy()
    expect(document.querySelector('video')).toBeNull()
  })

  it('removes the action when retention expires on an open page', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-11T10:00:00Z'))
    try {
      render(
        <MeetingRecordingSection
          recording={normalizeMeetingRecording({
            ...available,
            retention_expires_at: Date.parse('2026-10-11T10:00:01Z') / 1_000,
          })}
        />
      )
      const button = screen.getByRole('button', { name: 'recording.open' })
      button.focus()
      expect(document.activeElement).toBe(button)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_001)
      })
      expect(screen.queryByRole('button')).toBeNull()
      const message = screen.getByText('recording.accessUnavailable')
      expect(message.getAttribute('role')).toBe('status')
      expect(document.activeElement).toBe(message)
    } finally {
      vi.useRealTimers()
    }
  })

  it('moves focus when refreshed data removes the expired action', () => {
    const view = render(
      <MeetingRecordingSection
        recording={normalizeMeetingRecording(available)}
      />
    )
    const button = screen.getByRole('button', { name: 'recording.open' })
    button.focus()
    expect(document.activeElement).toBe(button)

    view.rerender(
      <MeetingRecordingSection
        recording={normalizeMeetingRecording({ status: 'expired' })}
      />
    )

    expect(screen.queryByRole('button')).toBeNull()
    expect(document.activeElement).toBe(
      screen.getByText('status.recording.expired')
    )
  })

  it.each([
    'absent',
    'processing',
    'available',
    'expired',
    'failed',
    'unknown',
  ])('announces %s and offers no action for a partial projection', (status) => {
    render(
      <MeetingRecordingSection
        recording={normalizeMeetingRecording({ status })}
      />
    )
    expect(
      screen.getByText(`status.recording.${status}`).getAttribute('role')
    ).toBe('status')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('offers no action without the Platform origin', () => {
    config.origin = undefined
    render(
      <MeetingRecordingSection
        recording={normalizeMeetingRecording(available)}
      />
    )
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('recording.accessUnavailable')).toBeTruthy()
  })

  it('shows video above readable summary and transcript even after a video failure', async () => {
    vi.mocked(fetchApi).mockResolvedValue({
      id: 'm1',
      started_at: '2026-10-11T10:00:00Z',
      recording: { status: 'failed' },
      summary: { status: 'available', text: 'Readable summary' },
      transcript: {
        status: 'available',
        segments: [{ id: 's1', start_ms: 0, text: 'Readable transcript' }],
      },
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={client}>
        <MeetingHistoryDetailView meetingId="m1" />
      </QueryClientProvider>
    )
    expect(await screen.findByText('Readable summary')).toBeTruthy()
    expect(screen.getByText('Readable transcript')).toBeTruthy()
    expect(
      [...document.querySelectorAll('[data-section]')].map((section) =>
        section.getAttribute('data-section')
      )
    ).toEqual(['recording', 'summary', 'transcript'])
    expect(
      within(
        screen.getByRole('region', { name: 'recording.title' })
      ).queryByRole('button')
    ).toBeNull()
    client.clear()
  })

  it.each([
    'processing',
    'available',
    'expired',
    'failed',
    'absent',
    'unknown',
  ])(
    'makes the row video status %s accessible through the meeting link',
    (status) => {
      const item = normalizeHistoryPage({
        results: [
          {
            id: 'm1',
            title: 'Meeting',
            started_at: '2026-10-11T10:00:00Z',
            recording_status: status,
          },
        ],
      }).items[0]
      const linkRef = createRef<HTMLAnchorElement>()
      render(
        <>
          <MeetingRowLink item={item} linkRef={linkRef} />
          <MeetingContentStatusIcons item={item} linkRef={linkRef} />
        </>
      )
      expect(
        screen.getByRole('link', {
          name: new RegExp(`status.recording.${status}`),
        })
      ).toBeTruthy()
      expect(
        document
          .querySelector('[data-recording-status]')
          ?.getAttribute('data-recording-status')
      ).toBe(status)
    }
  )
})
