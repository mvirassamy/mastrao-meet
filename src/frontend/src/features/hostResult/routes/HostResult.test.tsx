import type { ReactNode } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import fr from '@/locales/fr/global.json'
import frMeetingHistory from '@/locales/fr/meetingHistory.json'

vi.mock('@/layout/Screen', () => ({
  Screen: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}))
// The public header only links to login and settings; it reads no result.
vi.mock('../components/HostResultHeader', () => ({
  HostResultHeader: () => null,
}))
vi.mock('@/components/LoginButton', () => ({
  LoginButton: () => <a href="/authenticate/">Se connecter</a>,
}))

const i18n = createInstance()
await i18n.init({
  lng: 'fr',
  defaultNS: 'global',
  resources: { fr: { global: fr, meetingHistory: frMeetingHistory } },
  interpolation: { escapeValue: false },
  initAsync: false,
})

const ACCESS = 'signed.' + 'a'.repeat(64)
const RESULT = {
  version: 1,
  meeting_ref: 'meeting_0123456789abcdef',
  title: 'Rendez-vous client',
  access_expires_at: Math.floor(Date.parse('2026-12-05T12:00:00Z') / 1000),
  started_at: Math.floor(Date.parse('2026-11-10T13:00:00Z') / 1000),
  ended_at: Math.floor(Date.parse('2026-11-10T13:42:00Z') / 1000),
  summary: {
    status: 'available',
    editorial_state: 'machine_generated',
    summary: {
      text: 'Le client confirme le calendrier.\n\nLe notaire attend une estimation.',
    },
    items: [{ kind: 'action', statement: 'Envoyer le projet de contrat.' }],
  },
  transcript: {
    language: 'fr',
    segments: [
      {
        segment_id: 'segment_1',
        start_ms: 65_000,
        end_ms: 70_000,
        speaker: { kind: 'participant', label: 'Maître Ambre' },
        text: 'Nous signerons vendredi.',
      },
      {
        segment_id: 'segment_2',
        start_ms: 71_000,
        end_ms: 72_000,
        speaker: { kind: 'anonymous', index: 2 },
        text: 'Entendu.',
      },
    ],
  },
}

const renderPage = async () => {
  const { default: HostResultPage } = await import('./HostResult')
  render(
    <I18nextProvider i18n={i18n}>
      <HostResultPage />
    </I18nextProvider>
  )
}

describe('host result page', () => {
  beforeEach(() => {
    vi.resetModules()
    window.history.replaceState({}, '', '/compte-rendu')
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('reads the report once with the fragment credential, then clears it', async () => {
    window.location.hash = `acces=${ACCESS}`
    const fetchMock = vi.fn(async () => Response.json(RESULT))
    vi.stubGlobal('fetch', fetchMock)

    await renderPage()

    expect(
      await screen.findByRole('heading', { name: 'Rendez-vous client' })
    ).toBeTruthy()
    expect(window.location.hash).toBe('')
    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, request] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ]
    expect(url).toMatch(/\/handoff\/host\/result\/$/)
    expect(JSON.parse(String(request.body))).toEqual({ access: ACCESS })
    expect(request.credentials).toBe('omit')
    expect(request.referrerPolicy).toBe('no-referrer')
  })

  it('shows the report like the meeting history detail', async () => {
    window.location.hash = `acces=${ACCESS}`
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Response.json(RESULT))
    )

    await renderPage()

    await screen.findByRole('heading', { name: 'Rendez-vous client' })
    expect(screen.getByText('Mardi 10 novembre 2026')).toBeTruthy()
    expect(screen.getByText(/42 min/)).toBeTruthy()
    expect(
      screen.getByText('Lien personnel, valable jusqu’au 5 décembre 2026.')
    ).toBeTruthy()
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Synthèse',
      'Transcription',
    ])
    expect(screen.getByText('Le client confirme le calendrier.')).toBeTruthy()
    expect(screen.getByText('Le notaire attend une estimation.')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Actions' })).toBeTruthy()
    expect(screen.getByText('Envoyer le projet de contrat.')).toBeTruthy()
    expect(screen.getByText('Nous signerons vendredi.')).toBeTruthy()
    expect(screen.getByText('Maître Ambre')).toBeTruthy()
    expect(screen.getByText('Intervenant 2')).toBeTruthy()
    expect(screen.getByText('01:05')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: 'Télécharger' })).toHaveLength(
      2
    )
  })

  it('shows one unavailable message for a refused link', async () => {
    window.location.hash = `acces=${ACCESS}`
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json({ message: 'Not found' }, { status: 404 })
      )
    )

    await renderPage()

    expect(
      await screen.findByRole('heading', {
        name: 'Ce lien n’est plus disponible',
      })
    ).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Se connecter' })).toBeTruthy()
  })

  it('does not call the server without a well-formed fragment', async () => {
    window.location.hash = `acces=${ACCESS}&extra=1`
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await renderPage()

    expect(
      await screen.findByRole('heading', {
        name: 'Ce lien n’est plus disponible',
      })
    ).toBeTruthy()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(window.location.hash).toBe('')
  })

  it('offers a retry when the service is temporarily unavailable', async () => {
    window.location.hash = `acces=${ACCESS}`
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json({ message: 'Unavailable' }, { status: 503 })
      )
    )

    await renderPage()

    expect(
      await screen.findByRole('button', { name: 'Réessayer' })
    ).toBeTruthy()
  })
})
