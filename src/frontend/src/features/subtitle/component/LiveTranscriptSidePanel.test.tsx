import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LiveTranscriptSidePanel } from './LiveTranscriptSidePanel'

const syncSubtitleState = vi.fn()
const ensureSubtitlesStarted = vi.fn()
const useLiveTranscriptionMock = vi.hoisted(() => vi.fn())
const resolveSpeaker = vi.fn()

vi.mock('../store/liveTranscriptionContext', () => ({
  useLiveTranscription: useLiveTranscriptionMock,
}))

vi.mock('../hooks/useSubtitles', () => ({
  useSubtitles: () => ({ ensureSubtitlesStarted }),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'status.live': 'Live',
        statusLabel: 'Transcription',
        'notice.starting': 'Starting',
        'notice.waiting': 'Waiting for speech',
        'notice.stopped': 'Stopped',
        'notice.unavailable': 'Unavailable',
        'notice.interrupted': 'Connection interrupted',
        'notice.interruptedDescription': 'Will resume',
        segmentsLabel: 'Live transcript segments',
        interim: 'In progress',
        final: 'Final',
        error: 'Refresh failed',
        startError: 'Start failed',
        retry: 'Retry',
        refreshing: 'Refreshing',
        unknown: 'Unknown participant',
      })[key] ?? key,
  }),
}))

vi.mock('@/primitives', () => ({
  Button: ({
    children,
    isDisabled,
    onPress,
  }: {
    children: ReactNode
    isDisabled?: boolean
    onPress?: () => void
  }) => (
    <button disabled={isDisabled} onClick={onPress} type="button">
      {children}
    </button>
  ),
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
    margin?: boolean
    padding?: boolean
    variant?: string
    wrap?: string
    children: ReactNode
    [key: string]: unknown
  }) => {
    const Component = as
    return <Component {...props}>{children}</Component>
  },
}))

vi.mock('@/components/Avatar', () => ({
  Avatar: ({ name }: { name: string }) => (
    <span data-testid="speaker-avatar" data-name={name} />
  ),
}))

const segment = {
  key: 'segment-1',
  participantIdentity: 'alice',
  trackSid: 'track-1',
  legId: 'leg-1',
  itemId: 'item-1',
  state: 'interim' as const,
  text: 'Hello from the meeting',
  sequence: 0,
  revision: 1,
  receivedAt: 1,
  metadataSource: 'envelope' as const,
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

beforeEach(() => {
  ensureSubtitlesStarted.mockResolvedValue(undefined)
  resolveSpeaker.mockReturnValue(undefined)
})

describe('LiveTranscriptSidePanel', () => {
  it('requests the subtitle ON intent when it opens', async () => {
    useLiveTranscriptionMock.mockReturnValue({
      status: 'live',
      connectionStatus: 'connected',
      resyncStatus: 'idle',
      segments: [],
      syncSubtitleState,
      resolveSpeaker,
    })

    render(<LiveTranscriptSidePanel />)

    await waitFor(() => expect(ensureSubtitlesStarted).toHaveBeenCalledOnce())
  })

  it('announces the transcript state and shows compact speaker bubbles', () => {
    useLiveTranscriptionMock.mockReturnValue({
      status: 'live',
      connectionStatus: 'connected',
      resyncStatus: 'idle',
      segments: [segment],
      syncSubtitleState,
      resolveSpeaker,
    })

    render(<LiveTranscriptSidePanel />)

    // No technical status line on screen: the state is only announced.
    expect(screen.getByRole('status').textContent).toBe('Transcription: Live')
    expect(screen.queryByText('Connection interrupted')).toBeNull()
    expect(
      screen.getByRole('log', { name: 'Live transcript segments' }).textContent
    ).toContain('Hello from the meeting')
    expect(screen.getByText('Unknown participant')).toBeTruthy()
    expect(screen.getByTestId('speaker-avatar').dataset.name).toBe(
      'Unknown participant'
    )
    expect(screen.queryByText('In progress')).toBeNull()
    expect(screen.queryByText('Final')).toBeNull()
    expect(screen.getByTestId('live-transcript-panel')).toBeTruthy()
  })

  it('shows the participant display name instead of its technical identity', () => {
    resolveSpeaker.mockReturnValue({
      identity: 'alice',
      label: 'Alice Martin',
    })
    useLiveTranscriptionMock.mockReturnValue({
      status: 'live',
      connectionStatus: 'connected',
      resyncStatus: 'idle',
      segments: [segment],
      syncSubtitleState,
      resolveSpeaker,
    })

    render(<LiveTranscriptSidePanel />)

    expect(screen.getByText('Alice Martin')).toBeTruthy()
    expect(screen.queryByText('alice')).toBeNull()
  })

  it('groups consecutive segments from the same microphone into one speaker turn', () => {
    resolveSpeaker.mockImplementation((identity: string) => ({
      identity,
      label: identity === 'alice' ? 'Alice Martin' : 'Bob Dupont',
    }))
    useLiveTranscriptionMock.mockReturnValue({
      status: 'live',
      connectionStatus: 'connected',
      resyncStatus: 'idle',
      segments: [
        { ...segment, state: 'final', text: 'Bonjour', receivedAt: 1_000 },
        {
          ...segment,
          key: 'segment-2',
          itemId: 'item-2',
          state: 'final',
          text: 'comment allez-vous ?',
          receivedAt: 2_000,
        },
        {
          ...segment,
          key: 'segment-3',
          participantIdentity: 'bob',
          trackSid: 'track-2',
          itemId: 'item-3',
          state: 'final',
          text: 'Très bien',
          receivedAt: 3_000,
        },
        {
          ...segment,
          key: 'segment-4',
          itemId: 'item-4',
          state: 'final',
          text: 'Merci',
          receivedAt: 4_000,
        },
      ],
      syncSubtitleState,
      resolveSpeaker,
    })

    render(<LiveTranscriptSidePanel />)

    expect(screen.getAllByTestId('speaker-avatar')).toHaveLength(3)
    expect(screen.getByText('Bonjour comment allez-vous ?')).toBeTruthy()
    expect(screen.getByText('Très bien')).toBeTruthy()
    expect(screen.getByText('Merci')).toBeTruthy()
  })

  it('keeps a long pause in the same bubble as a new paragraph', () => {
    useLiveTranscriptionMock.mockReturnValue({
      status: 'live',
      connectionStatus: 'connected',
      resyncStatus: 'idle',
      segments: [
        {
          ...segment,
          state: 'final',
          text: 'Premier sujet',
          receivedAt: 1_000,
        },
        {
          ...segment,
          key: 'segment-2',
          itemId: 'item-2',
          state: 'final',
          text: 'Deuxième sujet',
          receivedAt: 7_000,
        },
      ],
      syncSubtitleState,
      resolveSpeaker,
    })

    render(<LiveTranscriptSidePanel />)

    expect(screen.getAllByTestId('speaker-avatar')).toHaveLength(1)
    expect(screen.getByRole('log').querySelector('p')?.textContent).toBe(
      'Premier sujet\n\nDeuxième sujet'
    )
  })

  it('does not expose a technical identity after the participant leaves', () => {
    useLiveTranscriptionMock.mockReturnValue({
      status: 'live',
      connectionStatus: 'connected',
      resyncStatus: 'idle',
      segments: [{ ...segment, participantIdentity: 'user_9d46b8f2' }],
      syncSubtitleState,
      resolveSpeaker,
    })

    render(<LiveTranscriptSidePanel />)

    expect(screen.getByText('Unknown participant')).toBeTruthy()
    expect(screen.queryByText('user_9d46b8f2')).toBeNull()
  })

  it.each([
    ['inactive', 'Starting'],
    ['starting', 'Starting'],
    ['live', 'Waiting for speech'],
    ['stopped', 'Stopped'],
    ['unavailable', 'Unavailable'],
  ] as const)(
    'explains an empty %s transcript in plain words',
    (status, notice) => {
      useLiveTranscriptionMock.mockReturnValue({
        status,
        connectionStatus: 'connected',
        resyncStatus: 'idle',
        segments: [],
        syncSubtitleState,
        resolveSpeaker,
      })

      render(<LiveTranscriptSidePanel />)

      expect(screen.getByRole('log').textContent).toBe(notice)
    }
  )

  it('says the connection is interrupted only while it is', () => {
    useLiveTranscriptionMock.mockReturnValue({
      status: 'reconnecting',
      connectionStatus: 'reconnecting',
      resyncStatus: 'idle',
      segments: [segment],
      syncSubtitleState,
      resolveSpeaker,
    })

    render(<LiveTranscriptSidePanel />)

    expect(screen.getByText('Connection interrupted')).toBeTruthy()
    expect(screen.getByText('Will resume')).toBeTruthy()
  })

  it('offers retry when the state refresh failed', () => {
    useLiveTranscriptionMock.mockReturnValue({
      status: 'live',
      connectionStatus: 'connected',
      resyncStatus: 'failed',
      segments: [],
      syncSubtitleState,
      resolveSpeaker,
    })

    render(<LiveTranscriptSidePanel />)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    expect(screen.getByRole('alert').textContent).toContain('Refresh failed')
    expect(syncSubtitleState).toHaveBeenCalledOnce()
  })

  it('disables retry while a refresh is in flight', async () => {
    let resolveRefresh: (() => void) | undefined
    syncSubtitleState.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveRefresh = resolve
        })
    )
    useLiveTranscriptionMock.mockReturnValue({
      status: 'live',
      connectionStatus: 'connected',
      resyncStatus: 'failed',
      segments: [],
      syncSubtitleState,
      resolveSpeaker,
    })

    render(<LiveTranscriptSidePanel />)
    const retryButton = screen.getByRole('button', {
      name: 'Retry',
    }) as HTMLButtonElement
    fireEvent.click(retryButton)

    await waitFor(() => expect(retryButton.disabled).toBe(true))
    resolveRefresh?.()
    await waitFor(() => expect(retryButton.disabled).toBe(false))
  })

  it('surfaces a failed start and retries the ON intent before resync', async () => {
    ensureSubtitlesStarted
      .mockRejectedValueOnce(new Error('subtitle unavailable'))
      .mockResolvedValueOnce(undefined)
    syncSubtitleState.mockResolvedValue(undefined)
    useLiveTranscriptionMock.mockReturnValue({
      status: 'inactive',
      connectionStatus: 'connected',
      resyncStatus: 'idle',
      segments: [],
      syncSubtitleState,
      resolveSpeaker,
    })

    render(<LiveTranscriptSidePanel />)

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('Start failed')
    )
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    expect(ensureSubtitlesStarted).toHaveBeenCalledTimes(2)
    expect(syncSubtitleState).toHaveBeenCalledOnce()
  })

  it('keeps the start failure visible when the retry fails again', async () => {
    ensureSubtitlesStarted.mockRejectedValue(new Error('subtitle unavailable'))
    useLiveTranscriptionMock.mockReturnValue({
      status: 'inactive',
      connectionStatus: 'connected',
      resyncStatus: 'idle',
      segments: [],
      syncSubtitleState,
      resolveSpeaker,
    })

    render(<LiveTranscriptSidePanel />)

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('Start failed')
    )
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(ensureSubtitlesStarted).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('alert').textContent).toContain('Start failed')
    expect(syncSubtitleState).not.toHaveBeenCalled()
  })
})
