import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { Router } from 'wouter'
import { memoryLocation } from 'wouter/memory-location'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import type { ApiUser } from '@/features/auth/api/ApiUser'
import copy from '@/locales/fr/home.json'
import { AuthenticatedHome } from './AuthenticatedHome'
import { ScheduleMeetingDialog } from '../components/ScheduleMeetingDialog'
import { useCreateCanonicalMeeting } from '../api/createCanonicalMeeting'

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
vi.mock('../hooks/useCalendarToday', () => ({
  useCalendarToday: () => new Date('2026-10-05T12:00:00Z'),
}))
vi.mock('../components/authenticated/MeetWorkspaceShell', () => ({
  MeetWorkspaceShell: ({ children }: { children: React.ReactNode }) => children,
}))
vi.mock('../components/authenticated/MeetWorkspaceToolbar', () => ({
  MeetWorkspaceToolbar: () => null,
}))
vi.mock('../components/authenticated/MeetingWeekStrip', () => ({
  MeetingWeekStrip: () => null,
}))
vi.mock('../components/CreateMeetingMenu', () => ({
  CreateMeetingMenu: () => null,
}))
vi.mock('react-i18next', () => ({
  useTranslation: (_ns: unknown, options?: { keyPrefix?: string }) => ({
    t: (key: string, values?: Record<string, unknown>) => {
      const path = options?.keyPrefix ? `${options.keyPrefix}.${key}` : key
      let value: unknown = copy
      for (const part of path.split('.'))
        value = (value as Record<string, unknown>)?.[part]
      if (typeof value !== 'string') return key
      return value.replace(/{{(\w+)}}/g, (_match, name: string) =>
        String(values?.[name] ?? '')
      )
    },
    i18n: { language: 'fr', resolvedLanguage: 'fr' },
  }),
}))
const fetchMock = vi.mocked(fetchApi)
const roomRef = 'room_0123456789abcdef0123456789abcdef'
const planned = {
  meeting_ref: 'meeting_0123456789abcdef0123456789abcdef',
  room_ref: roomRef,
  title: 'Réunion calendrier',
  created_at: Date.parse('2026-10-05T12:00:00Z') / 1000,
  scheduled_start_at: Date.parse('2026-10-06T08:00:00Z') / 1000,
  scheduled_end_at: Date.parse('2026-10-06T09:15:00Z') / 1000,
  timezone: 'Europe/Paris',
  state: 'ready',
  ended_at: null,
}
const historyPlanned = {
  id: planned.meeting_ref,
  title: planned.title,
  started_at: '2026-10-05T12:00:00Z',
  scheduled_start_at: planned.scheduled_start_at,
  ended_at: null,
}
const user = { timezone: 'Europe/Paris' } as ApiUser
const Harness = ({ withCreation }: { withCreation: boolean }) => {
  const create = useCreateCanonicalMeeting()
  const [creating, setCreating] = useState(withCreation)
  return (
    <>
      <AuthenticatedHome user={user} />
      {creating && (
        <ScheduleMeetingDialog
          isOpen
          onClose={() => setCreating(false)}
          onCreate={async (schedule) => {
            await create.mutateAsync({
              idempotencyKey: 'meet_readback_0123456789abcdef',
              schedule,
            })
            setCreating(false)
          }}
        />
      )}
    </>
  )
}
const renderHome = (day = '2026-10-06', withCreation = false) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const location = memoryLocation({ path: `/?jour=${day}` })
  render(
    <QueryClientProvider client={client}>
      <Router hook={location.hook} searchHook={location.searchHook}>
        <Harness withCreation={withCreation} />
      </Router>
    </QueryClientProvider>
  )
}
beforeEach(() => {
  fetchMock.mockReset()
  vi.stubEnv('TZ', 'Europe/Paris')
})
afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
})

describe('create → canonical selected day → host access', () => {
  it('shows persisted data after creation and after a fresh home reload', async () => {
    let created = false
    fetchMock.mockImplementation(async (url, options) => {
      if (url === 'meetings/') {
        expect(JSON.parse(options?.body as string)).toEqual({
          title: planned.title,
          scheduled_start_at: planned.scheduled_start_at,
          scheduled_end_at: planned.scheduled_end_at,
          timezone: planned.timezone,
        })
        created = true
        return planned
      }
      if (url === 'meetings/history/')
        return { results: created ? [historyPlanned] : [], next_cursor: null }
      if (url.startsWith('meetings/?'))
        return { results: created ? [planned] : [], next_cursor: null }
      throw new Error(`unexpected ${url}`)
    })
    renderHome('2026-10-06', true)
    fireEvent.change(screen.getByLabelText('Titre (facultatif)'), {
      target: { value: planned.title },
    })
    fireEvent.change(screen.getByLabelText('Date'), {
      target: { value: '2026-10-06' },
    })
    fireEvent.change(screen.getByLabelText('Heure de début'), {
      target: { value: '10:00' },
    })
    fireEvent.change(screen.getByLabelText('Heure de fin'), {
      target: { value: '11:15' },
    })
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => url.startsWith('meetings/?'))
      ).toBe(true)
    )
    fireEvent.click(screen.getByRole('button', { name: 'Créer la réunion' }))
    expect(
      await screen.findByRole('heading', { name: planned.title })
    ).toBeTruthy()
    expect(screen.getByText(/10:00.*11:15/)).toBeTruthy()
    expect(
      screen
        .getByRole('link', { name: 'Rejoindre la réunion' })
        .getAttribute('href')
    ).toBe(`/host/${roomRef}`)
    expect(
      fetchMock.mock.calls.filter(([url]) => url.startsWith('meetings/?'))
        .length
    ).toBeGreaterThanOrEqual(2)
    cleanup()
    renderHome()
    expect(
      await screen.findByRole('heading', { name: planned.title })
    ).toBeTruthy()
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(
      screen
        .getByRole('link', { name: 'Rejoindre la réunion' })
        .getAttribute('href')
    ).toBe(`/host/${roomRef}`)
  })
  it('never places a planned meeting on the creation day, preserving instant history', async () => {
    fetchMock.mockImplementation(async (url) => {
      if (url === 'meetings/history/')
        return {
          results: [
            historyPlanned,
            {
              id: 'instant',
              title: 'Immédiate',
              started_at: '2026-10-05T09:00:00Z',
              scheduled_start_at: null,
            },
          ],
          next_cursor: null,
        }
      return { results: [], next_cursor: null }
    })
    renderHome('2026-10-05')
    expect(await screen.findByRole('link', { name: /Immédiate/ })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: planned.title })).toBeNull()
    expect(
      screen.queryByRole('link', { name: /Réunion calendrier/ })
    ).toBeNull()
  })
  it('reads every canonical day page and deduplicates canonical references', async () => {
    fetchMock.mockImplementation(async (url) => {
      if (url === 'meetings/history/')
        return { results: [historyPlanned], next_cursor: null }
      const cursor = new URLSearchParams(url.split('?')[1]).get('cursor')
      if (cursor)
        return {
          results: [
            planned,
            {
              ...planned,
              meeting_ref: 'meeting_fedcba9876543210fedcba9876543210',
              title: 'Dernière page',
            },
          ],
          next_cursor: null,
        }
      return { results: [planned], next_cursor: 'page_2' }
    })
    renderHome()
    expect(
      await screen.findByRole('heading', { name: 'Dernière page' })
    ).toBeTruthy()
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(
      fetchMock.mock.calls.filter(([url]) => url.includes('cursor=page_2'))
    ).toHaveLength(1)
  })
  it('shows a read error rather than a false empty planned day', async () => {
    fetchMock.mockImplementation(async (url) => {
      if (url === 'meetings/history/') return { results: [], next_cursor: null }
      throw new Error('day read unavailable')
    })
    renderHome()
    expect(await screen.findByRole('alert', {}, { timeout: 3000 })).toBeTruthy()
    expect(
      screen.queryByRole('heading', { name: copy.dashboard.empty.dateTitle })
    ).toBeNull()
  })

  it('retries a failed next page before displaying the complete day', async () => {
    let failing = true
    fetchMock.mockImplementation(async (url) => {
      if (url === 'meetings/history/') return { results: [], next_cursor: null }
      if (url.includes('cursor=page_2')) {
        if (failing) throw new Error('page unavailable')
        return {
          results: [
            {
              ...planned,
              meeting_ref: 'meeting_fedcba9876543210fedcba9876543210',
              title: 'Page retrouvée',
            },
          ],
          next_cursor: null,
        }
      }
      return { results: [planned], next_cursor: 'page_2' }
    })
    renderHome()
    await screen.findByRole('alert', {}, { timeout: 3000 })
    failing = false
    fireEvent.click(screen.getByRole('button', { name: 'error.retry' }))
    expect(
      await screen.findByRole('heading', { name: 'Page retrouvée' })
    ).toBeTruthy()
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it.each(['cancelled', 'ending', 'ended'])(
    'does not reopen a persisted %s meeting after reload',
    async (state) => {
      fetchMock.mockImplementation(async (url) =>
        url === 'meetings/history/'
          ? { results: [historyPlanned], next_cursor: null }
          : { results: [{ ...planned, state }], next_cursor: null }
      )
      renderHome()
      expect(
        await screen.findByRole('heading', { name: planned.title })
      ).toBeTruthy()
      expect(
        screen.queryByRole('link', { name: 'Rejoindre la réunion' })
      ).toBeNull()
    }
  )
})
