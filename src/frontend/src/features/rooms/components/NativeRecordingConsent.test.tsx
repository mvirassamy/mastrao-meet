import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NativeRecordingConsent } from './NativeRecordingConsent'
import type { NativeNoticeProjection } from '../api/nativeNotice'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options: { defaultValue: string }) =>
      options?.defaultValue ?? key,
    i18n: { language: 'fr' },
  }),
}))
vi.mock('@/api/apiUrl', () => ({
  apiUrl: (path: string) => `/api/v1.0${path}`,
}))

const projection: NativeNoticeProjection = {
  version: 1,
  text: 'Texte exact fourni par Core, sans réécriture.',
  decision: null,
  capture_authorized: false,
  notice: {
    policy_ref: 'native_policy_fixture',
    notice_version: 'native_notice_fixture',
    notice_digest: 'a'.repeat(64),
    purpose: 'meeting_transcription_source_audio',
    scope: 'consented_microphone_track_epoch',
    retention_expires_at: 2_000_000_000,
  },
}
const setup = (onDecided = vi.fn(async () => undefined)) => {
  const query = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={query}>
      <NativeRecordingConsent
        roomId="room_fixture"
        projection={projection}
        onDecided={onDecided}
      />
    </QueryClientProvider>
  )
  return onDecided
}
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Native preentry decision', () => {
  it.each(['accepted', 'refused'] as const)(
    'sends only explicit %s plus exact notice with the CSRF/session transport',
    async (choice) => {
      const fetch = vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            ...projection,
            decision: {
              ...projection.notice,
              decision: choice,
              decision_ref: 'decision_fixture',
              decided_at: 1,
            },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      )
      vi.stubGlobal('fetch', fetch)
      document.cookie = 'csrftoken=synthetic-csrf-only'
      const done = setup()
      expect(screen.getByText(projection.text)).toBeTruthy()
      expect(fetch).not.toHaveBeenCalled()
      fireEvent.click(
        screen.getByRole('button', {
          name:
            choice === 'accepted'
              ? 'Accepter cette capture audio'
              : 'Refuser cette capture audio',
        })
      )
      await waitFor(() => expect(done).toHaveBeenCalledOnce())
      expect(fetch).toHaveBeenCalledOnce()
      const [url, options] = fetch.mock.calls[0]
      expect(url).toBe('/api/v1.0/rooms/room_fixture/native-notice-decision/')
      expect(JSON.parse(options.body)).toEqual({
        decision: choice,
        notice: projection.notice,
      })
      expect(options.credentials).toBe('include')
      expect(options.headers['X-CSRFToken']).toBe('synthetic-csrf-only')
    }
  )

  it('disables both choices while confirming and does not advance on failure', async () => {
    let complete: (value: Response) => void = () => undefined
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            complete = resolve
          })
      )
    )
    const done = setup()
    fireEvent.click(
      screen.getByRole('button', { name: 'Accepter cette capture audio' })
    )
    await waitFor(() =>
      expect(
        screen
          .getAllByRole('button')
          .every((b) => (b as HTMLButtonElement).disabled)
      ).toBe(true)
    )
    expect(done).not.toHaveBeenCalled()
    complete(
      new Response('{}', {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      })
    )
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(done).not.toHaveBeenCalled()
    expect(
      screen
        .getAllByRole('button')
        .every((b) => !(b as HTMLButtonElement).disabled)
    ).toBe(true)
  })

  it('does not claim success when post-decision room refresh fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('{}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      )
    )
    setup(vi.fn().mockRejectedValue(new Error('synthetic reload failure')))
    fireEvent.click(
      screen.getByRole('button', { name: 'Refuser cette capture audio' })
    )
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
  })
})
