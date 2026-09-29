import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LiveTranscriptSidePanel } from './LiveTranscriptSidePanel'

const syncSubtitleState = vi.fn()
const useLiveTranscriptionMock = vi.hoisted(() => vi.fn())

vi.mock('../store/liveTranscriptionContext', () => ({
  useLiveTranscription: useLiveTranscriptionMock,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'connection.connected': 'Connected',
        'connection.disconnectedDescription': 'Meeting unavailable',
        'status.live': 'Live',
        segmentsLabel: 'Live transcript segments',
        empty: 'No transcript yet',
        interim: 'In progress',
        final: 'Final',
        error: 'Refresh failed',
        retry: 'Retry',
        refreshing: 'Refreshing',
      })[key] ?? key,
  }),
}))

vi.mock('@/primitives', () => ({
  Badge: ({ children }: { children: ReactNode }) => <span>{children}</span>,
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
    children: ReactNode
    [key: string]: unknown
  }) => {
    const Component = as
    return <Component {...props}>{children}</Component>
  },
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

describe('LiveTranscriptSidePanel', () => {
  it('shows connection, transcript state, and progressive segments', () => {
    useLiveTranscriptionMock.mockReturnValue({
      status: 'live',
      connectionStatus: 'connected',
      resyncStatus: 'idle',
      segments: [segment],
      syncSubtitleState,
    })

    render(<LiveTranscriptSidePanel />)

    expect(screen.getByRole('status').textContent).toContain('Connected')
    expect(screen.getByRole('status').textContent).toContain('Live')
    expect(
      screen.getByRole('log', { name: 'Live transcript segments' }).textContent
    ).toContain('Hello from the meeting')
    expect(screen.getByText('In progress')).toBeTruthy()
    expect(screen.getByTestId('live-transcript-panel')).toBeTruthy()
  })

  it('offers retry when the state refresh failed', () => {
    useLiveTranscriptionMock.mockReturnValue({
      status: 'live',
      connectionStatus: 'connected',
      resyncStatus: 'failed',
      segments: [],
      syncSubtitleState,
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
})
