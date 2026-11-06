import { cleanup, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LiveTranscriptSidePanel } from './LiveTranscriptSidePanel'

const useLiveTranscriptionMock = vi.hoisted(() => vi.fn())

vi.mock('../store/liveTranscriptionContext', () => ({
  useLiveTranscription: useLiveTranscriptionMock,
}))

vi.mock('react-i18next', () => ({
  useTranslation: (_namespace: string, options?: { keyPrefix?: string }) => ({
    t: (key: string) => {
      if (options?.keyPrefix === 'participants' && key === 'unknown') {
        return 'Participant inconnu'
      }
      const labels: Record<string, string> = {
        statusLabel: 'Transcription',
        'status.live': 'En direct',
        'status.unknown': 'Initialisation',
        'status.degraded': 'Dégradée',
        'notice.starting': 'Démarrage',
        'notice.waiting': 'En attente de parole',
        'notice.stopped': 'Arrêtée',
        'notice.unavailable': 'Indisponible',
        'notice.interrupted': 'Reconnexion…',
        segmentsLabel: 'Transcription en direct',
      }
      return labels[key] ?? key
    },
  }),
}))

vi.mock('@/primitives', () => ({
  Text: ({
    as = 'p',
    children,
    margin: _margin,
    padding: _padding,
    variant: _variant,
    wrap: _wrap,
    ...props
  }: {
    as?: keyof JSX.IntrinsicElements
    children: ReactNode
    margin?: boolean
    padding?: boolean
    variant?: string
    wrap?: string
    [key: string]: unknown
  }) => {
    const Component = as
    return <Component {...props}>{children}</Component>
  },
}))

vi.mock('@/primitives/Spinner', () => ({
  Spinner: () => <span data-testid="spinner" />,
}))

vi.mock('@/components/Avatar', () => ({
  Avatar: ({ name }: { name: string }) => <span data-name={name} />,
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('LiveTranscriptSidePanel', () => {
  it('renders the track-bound transcript without a start or retry control', () => {
    useLiveTranscriptionMock.mockReturnValue({
      status: 'live',
      connectionStatus: 'connected',
      segments: [
        {
          key: 'segment-1',
          participantIdentity: 'alice',
          trackSid: 'TR_alice',
          legId: 'leg-1',
          itemId: 'item-1',
          state: 'final',
          text: 'Bonjour la salle',
          sequence: 1,
          revision: 1,
          receivedAt: 1,
          metadataSource: 'envelope',
        },
      ],
      resolveSpeaker: () => undefined,
    })

    render(<LiveTranscriptSidePanel />)

    expect(screen.getByText('Bonjour la salle')).toBeTruthy()
    expect(screen.getByText('alice')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('keeps the transcript panel visible while LiveKit reconnects', () => {
    useLiveTranscriptionMock.mockReturnValue({
      status: 'unknown',
      connectionStatus: 'reconnecting',
      segments: [],
      resolveSpeaker: () => undefined,
    })

    render(<LiveTranscriptSidePanel />)

    expect(screen.getByText('Reconnexion…')).toBeTruthy()
  })
})
