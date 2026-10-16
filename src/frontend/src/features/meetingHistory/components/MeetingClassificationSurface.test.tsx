import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import { ApiError } from '@/api/ApiError'
import type { MeetingClassification } from '@/features/meeting-classification/classificationApi'
import { MeetingHistoryDetailView } from './MeetingHistoryDetailView'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { name?: string }) =>
      options?.name ? `${key}: ${options.name}` : key,
    i18n: { language: 'fr' },
  }),
}))
vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
vi.mock('@/api/useConfig', () => ({
  useConfig: () => ({
    data: { mastrao_platform_origin: 'https://platform.example/' },
  }),
}))
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
          matter_path: null,
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
    expect(
      screen.queryByRole('link', { name: 'classification.openMatter' })
    ).toBeNull()
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
  it('restores the authorized folder and link for a reader without filing rights on a fresh page', async () => {
    const meetingId = 'meeting_0123456789abcdef'
    vi.mocked(fetchApi).mockImplementation(async (path) => {
      if (path.includes('/classification/'))
        return {
          version: 1,
          meeting_ref: meetingId,
          matter_ref: 'matter_0123456789abcdef',
          matter_display_label: 'Dossier restauré',
          matter_path: '/orgs/server-tenant/matters/matter_0123456789abcdef',
          classification_version: 1,
          can_classify: false,
        }
      return {
        id: meetingId,
        title: 'Réunion',
        started_at: '2026-10-16T08:00:00Z',
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
    expect(
      await screen.findByText('classification.current: Dossier restauré')
    ).toBeTruthy()
    expectFolderLinks(
      'https://platform.example/orgs/server-tenant/matters/matter_0123456789abcdef'
    )
    expect(
      screen.queryByRole('button', { name: 'classification.title' })
    ).toBeNull()
  })
  it('replaces the folder projection when canonical classification changes', async () => {
    const { client, updateClassification } = await renderFiledMeeting()
    expectFolderLinks(
      'https://platform.example/orgs/server-tenant/matters/matter_0123456789abcdef'
    )
    updateClassification({
      matter_ref: 'matter_other0123456789',
      matter_path: '/orgs/server-tenant/matters/matter_other0123456789',
      matter_display_label: 'Dossier suivant',
      classification_version: 2,
    })
    await act(() =>
      client.invalidateQueries({ queryKey: ['meeting-classification'] })
    )
    expect(
      await screen.findByText('classification.current: Dossier suivant')
    ).toBeTruthy()
    expectFolderLinks(
      'https://platform.example/orgs/server-tenant/matters/matter_other0123456789'
    )
    expect(
      screen.queryByText('classification.current: Dossier initial')
    ).toBeNull()
  })
  it('withdraws folder details and filing controls after a denied reread', async () => {
    const { client, denyAccess } = await renderFiledMeeting()
    denyAccess()
    await act(() =>
      client.invalidateQueries({ queryKey: ['meeting-classification'] })
    )
    await screen.findByText('classification.unavailable')
    expect(
      screen.queryByRole('link', { name: 'classification.openMatter' })
    ).toBeNull()
    expect(
      screen.queryByText('classification.current: Dossier initial')
    ).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'classification.title' })
    ).toBeNull()
  })
  it('removes the previous folder when the canonical meeting is no longer filed', async () => {
    const { client, updateClassification } = await renderFiledMeeting()
    updateClassification({
      matter_ref: null,
      matter_path: null,
      matter_display_label: null,
      classification_version: 2,
    })
    await act(() =>
      client.invalidateQueries({ queryKey: ['meeting-classification'] })
    )
    await screen.findByText('classification.unclassified')
    expect(
      screen.queryByRole('link', { name: 'classification.openMatter' })
    ).toBeNull()
    expect(
      screen.queryByText('classification.current: Dossier initial')
    ).toBeNull()
  })
  it('withholds the previous link while current rights are being reread', async () => {
    const { client, deferReread } = await renderFiledMeeting()
    const resolve = deferReread()
    let refresh: Promise<void>
    act(() => {
      refresh = client.invalidateQueries({
        queryKey: ['meeting-classification'],
      })
    })
    await screen.findByText('classification.loadingCurrent')
    expect(
      screen.queryByRole('link', { name: 'classification.openMatter' })
    ).toBeNull()
    await act(async () => {
      resolve()
      await refresh
    })
    await waitFor(() =>
      expectFolderLinks(
        'https://platform.example/orgs/server-tenant/matters/matter_0123456789abcdef'
      )
    )
  })
  it('withdraws the link when folder read rights are revoked but meeting access remains', async () => {
    const { client, updateClassification } = await renderFiledMeeting()
    updateClassification({
      matter_display_label: null,
      matter_path: null,
      can_classify: false,
    })
    await act(() =>
      client.invalidateQueries({ queryKey: ['meeting-classification'] })
    )
    await screen.findByText('classification.filed')
    expect(
      screen.queryByRole('link', { name: 'classification.openMatter' })
    ).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'classification.title' })
    ).toBeNull()
    expect(screen.getByText('private notes unchanged')).toBeTruthy()
  })
  it('keeps the canonical link after unmounting and loading a fresh page without local state', async () => {
    const { unmount } = await renderFiledMeeting()
    unmount()
    await renderFiledMeeting()
    expectFolderLinks(
      'https://platform.example/orgs/server-tenant/matters/matter_0123456789abcdef'
    )
  })
  it('uses the new canonical destination from a successful filing receipt in both surfaces', async () => {
    await renderFiledMeeting()
    fireEvent.click(
      screen.getAllByRole('button', { name: 'classification.title' })[1]
    )
    fireEvent.click(
      await screen.findByRole('button', { name: 'Dossier suivant' })
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'classification.confirm' })
    )
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await screen.findByText('classification.current: Dossier suivant')
    expectFolderLinks(
      'https://platform.example/orgs/server-tenant/matters/matter_other0123456789'
    )
  })
})

async function renderFiledMeeting() {
  const meetingId = 'meeting_0123456789abcdef'
  let projection: MeetingClassification = {
    version: 1,
    meeting_ref: meetingId,
    matter_ref: 'matter_0123456789abcdef',
    matter_display_label: 'Dossier initial',
    matter_path: '/orgs/server-tenant/matters/matter_0123456789abcdef',
    classification_version: 1,
    can_classify: true,
  }
  let denied = false
  let reread: Promise<void> | undefined
  vi.mocked(fetchApi).mockImplementation(async (path, options) => {
    if (path.startsWith('matters/'))
      return {
        version: 1,
        next_cursor: null,
        items: [
          {
            version: 1,
            matter_ref: 'matter_other0123456789',
            display_label: 'Dossier suivant',
          },
        ],
      }
    if (path.includes('/classification/')) {
      if (options?.method === 'POST') {
        projection = {
          ...projection,
          matter_ref: 'matter_other0123456789',
          matter_display_label: 'Dossier suivant',
          matter_path: '/orgs/server-tenant/matters/matter_other0123456789',
          classification_version: 2,
        }
        return { ...projection, idempotent_replay: false }
      }
      if (reread) await reread
      if (denied) throw new ApiError(403, {})
      return projection
    }
    return {
      id: meetingId,
      title: 'Réunion',
      started_at: '2026-10-16T08:00:00Z',
      summary: { status: 'failed' },
      transcript: { status: 'failed' },
    }
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const view = render(
    <QueryClientProvider client={client}>
      <MeetingHistoryDetailView meetingId={meetingId} identityKey="user-a" />
    </QueryClientProvider>
  )
  await screen.findByText('classification.current: Dossier initial')
  await waitFor(() =>
    expect(
      screen.getAllByRole('button', { name: 'classification.title' })
    ).toHaveLength(2)
  )
  return {
    ...view,
    client,
    deferReread: () => {
      let resolve!: () => void
      reread = new Promise<void>((done) => {
        resolve = done
      })
      return resolve
    },
    updateClassification: (fields: Partial<MeetingClassification>) => {
      projection = { ...projection, ...fields }
    },
    denyAccess: () => {
      denied = true
    },
  }
}

function expectFolderLinks(url: string) {
  const links = screen.getAllByRole('link', {
    name: 'classification.openMatter',
  })
  expect(links).toHaveLength(2)
  for (const link of links) {
    expect(link.getAttribute('href')).toBe(url)
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
  }
}
