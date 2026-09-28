import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LiveTranscriptPanel } from './LiveTranscriptPanel'
import frRooms from '@/locales/fr/rooms.json'
import {
  LiveTranscriptionContext,
  type LiveTranscriptionContextValue,
} from '../store/liveTranscriptionContext'
import type {
  LiveTranscriptionSegment,
  LiveTranscriptionState,
} from '../store/liveTranscriptionTypes'

const { frenchAvailabilityNote } = vi.hoisted(() => ({
  frenchAvailabilityNote: 'Transcription disponible depuis votre arrivée.',
}))

const room = {
  localParticipant: { identity: 'alice', name: 'Alice' },
  getParticipantByIdentity: (identity: string) =>
    identity === 'bob' ? { identity: 'bob', name: 'Bob' } : undefined,
}

vi.mock('@livekit/components-react', () => ({
  useRoomContext: () => room,
}))

vi.mock('@/utils/useIsMobile', () => ({ useIsMobile: () => true }))

vi.mock('@/features/rooms/livekit/hooks/useSidePanel', () => ({
  useSidePanel: () => ({ isLiveTranscriptOpen: true }),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number }) => {
      if (key === 'availabilityNote') return frenchAvailabilityNote
      return options?.count === undefined ? key : `${key} ${options.count}`
    },
  }),
}))

vi.mock('@/components/Avatar', () => ({
  Avatar: ({ name }: { name: string }) => <span>{name}</span>,
}))

vi.mock('@/primitives', () => ({
  Button: ({
    children,
    onPress,
    ...props
  }: {
    children: ReactNode
    onPress?: () => void
  }) => (
    <button onClick={onPress} {...props}>
      {children}
    </button>
  ),
  Text: ({
    children,
    margin: _margin,
    ...props
  }: {
    children: ReactNode
    margin?: boolean
  }) => <span {...props}>{children}</span>,
}))

vi.mock('@/styled-system/css', () => ({ css: () => '' }))

const createSegment = (
  overrides: Partial<LiveTranscriptionSegment> = {}
): LiveTranscriptionSegment => ({
  key: 'alice-track-leg-item-1',
  participantIdentity: 'alice',
  trackSid: 'track-1',
  legId: 'leg-1',
  itemId: 'item-1',
  state: 'final',
  text: 'Bonjour',
  sequence: 0,
  revision: 0,
  receivedAt: 1,
  metadataSource: 'envelope',
  ...overrides,
})

const createState = (
  overrides: Partial<LiveTranscriptionState> = {}
): LiveTranscriptionState => ({
  roomId: 'room-1',
  status: 'live',
  connectionStatus: 'connected',
  segments: [createSegment()],
  gaps: [],
  truncated: false,
  nextSequence: 1,
  ...overrides,
})

const renderPanel = (
  state: LiveTranscriptionState,
  syncSubtitleState = vi.fn()
) => {
  const value: LiveTranscriptionContextValue = {
    ...state,
    dispatch: vi.fn(),
    syncSubtitleState,
  }
  return render(
    <LiveTranscriptionContext.Provider value={value}>
      <LiveTranscriptPanel />
    </LiveTranscriptionContext.Provider>
  )
}

describe('LiveTranscriptPanel', () => {
  afterEach(cleanup)

  beforeEach(() => {
    HTMLElement.prototype.scrollTo = vi.fn()
  })

  it('shows participant interventions and announces finals only', () => {
    renderPanel(
      createState({
        segments: [
          createSegment({ state: 'interim', text: 'Bon', revision: 1 }),
          createSegment({
            key: 'bob-track-leg-item-2',
            participantIdentity: 'bob',
            itemId: 'item-2',
            sequence: 1,
            text: 'Bonjour à tous',
          }),
        ],
        nextSequence: 2,
      })
    )

    expect(
      document.querySelector('[data-participant-identity="alice"]')
    ).not.toBeNull()
    expect(
      document.querySelector('[data-participant-identity="bob"]')
    ).not.toBeNull()
    expect(
      document.querySelector('[data-segment-state="interim"]')
    ).not.toBeNull()
    expect(
      document
        .querySelector('[data-segment-state="interim"]')
        ?.getAttribute('aria-live')
    ).toBeNull()
    expect(
      document.querySelector('[data-segment-state="final"]')
    ).not.toBeNull()
    // Finals are announced through one persistent live region, interims never.
    const announcer = document.querySelector(
      '[data-testid="live-transcript-announcer"]'
    )
    expect(announcer?.getAttribute('aria-live')).toBe('polite')
    expect(announcer?.textContent).toContain('Bonjour à tous')
    expect(announcer?.textContent).not.toContain('Bon ')
    expect(document.querySelectorAll('[aria-live]')).toHaveLength(1)
    expect(document.querySelector('[role="region"]')?.textContent).toContain(
      'Bonjour à tous'
    )
  })

  it('keeps the availability note when there are no segments or when populated', () => {
    const syncSubtitleState = vi.fn()
    const { rerender } = renderPanel(
      createState({ segments: [] }),
      syncSubtitleState
    )

    expect(syncSubtitleState).toHaveBeenCalledOnce()
    expect(frRooms.liveTranscript.availabilityNote).toBe(frenchAvailabilityNote)

    expect(
      screen.getByTestId('live-transcript-availability-note').textContent
    ).toBe('Transcription disponible depuis votre arrivée.')

    rerender(
      <LiveTranscriptionContext.Provider
        value={{
          ...createState(),
          dispatch: vi.fn(),
          syncSubtitleState: vi.fn(),
        }}
      >
        <LiveTranscriptPanel />
      </LiveTranscriptionContext.Provider>
    )

    expect(
      screen.getByTestId('live-transcript-availability-note').textContent
    ).toBe('Transcription disponible depuis votre arrivée.')
  })

  it('replaces an interim intervention with its final text in place', () => {
    const { rerender } = renderPanel(
      createState({
        segments: [createSegment({ state: 'interim', text: 'Bon' })],
      })
    )

    rerender(
      <LiveTranscriptionContext.Provider
        value={{
          ...createState({ segments: [createSegment({ text: 'Bonjour' })] }),
          dispatch: vi.fn(),
          syncSubtitleState: vi.fn(),
        }}
      >
        <LiveTranscriptPanel />
      </LiveTranscriptionContext.Provider>
    )

    expect(document.querySelectorAll('[data-segment-state]')).toHaveLength(1)
    expect(document.querySelector('[data-segment-state]')?.textContent).toBe(
      'Bonjour'
    )
  })

  it.each([
    'unknown',
    'inactive',
    'starting',
    'live',
    'reconnecting',
    'degraded',
    'unavailable',
    'stopping',
    'stopped',
  ] as const)('exposes the %s status without a start action', (status) => {
    renderPanel(createState({ status }))

    expect(
      document.querySelector(`[data-transcription-status="${status}"]`)
    ).not.toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('counts finals received while the reader is away from the live edge', () => {
    const { rerender } = renderPanel(createState())
    const transcript = screen.getByLabelText('segmentsLabel')

    Object.defineProperties(transcript, {
      clientHeight: { configurable: true, value: 20 },
      scrollHeight: { configurable: true, value: 100 },
      scrollTop: { configurable: true, writable: true, value: 0 },
    })
    fireEvent.scroll(transcript)

    rerender(
      <LiveTranscriptionContext.Provider
        value={{
          ...createState({
            segments: [
              createSegment(),
              createSegment({
                key: 'alice-track-leg-item-2',
                itemId: 'item-2',
                sequence: 1,
                text: 'Bienvenue',
              }),
            ],
            nextSequence: 2,
          }),
          dispatch: vi.fn(),
          syncSubtitleState: vi.fn(),
        }}
      >
        <LiveTranscriptPanel />
      </LiveTranscriptionContext.Provider>
    )

    expect(screen.getByRole('button').textContent).toContain('returnToLive 1')
  })
})
