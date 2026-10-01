import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SubPanelId } from '../hooks/useSidePanel'
import { Tools } from './Tools'

const openLiveTranscript = vi.fn()
const useSidePanelMock = vi.hoisted(() => vi.fn())
const useAreSubtitlesAvailableMock = vi.hoisted(() => vi.fn())

vi.mock('../hooks/useSidePanel', async (importOriginal) => {
  const original =
    await importOriginal<typeof import('../hooks/useSidePanel')>()
  return {
    ...original,
    useSidePanel: useSidePanelMock,
  }
})

vi.mock('@/api/useConfig', () => ({ useConfig: () => ({ data: {} }) }))
vi.mock('@/hooks/useRestoreFocus', () => ({ useRestoreFocus: vi.fn() }))
vi.mock('@/features/subtitle/hooks/useAreSubtitlesAvailable', () => ({
  useAreSubtitlesAvailable: useAreSubtitlesAvailableMock,
}))
vi.mock('@/features/subtitle/component/LiveTranscriptSidePanel', () => ({
  LiveTranscriptSidePanel: () => <div>Live transcript panel</div>,
}))
vi.mock('@/features/recording', () => ({
  RecordingMode: {
    Transcript: 'transcript',
    ScreenRecording: 'screenRecording',
  },
  useIsRecordingModeEnabled: () => false,
  TranscriptSidePanel: () => <div>Post-meeting transcript panel</div>,
  ScreenRecordingSidePanel: () => <div>Recording panel</div>,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        body: 'Meeting tools',
        'tools.liveTranscript.title': 'Live transcript',
        'tools.liveTranscript.body': 'Follow the meeting as text',
      })[key] ?? key,
  }),
}))
vi.mock('@/primitives', () => ({
  A: ({ children }: { children: ReactNode }) => <a href="/">{children}</a>,
  Button: ({
    children,
    onPress,
  }: {
    children: ReactNode
    onPress?: () => void
  }) => (
    <button type="button" onClick={onPress}>
      {children}
    </button>
  ),
  Div: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Icon: ({ name }: { name: string }) => <span aria-hidden="true">{name}</span>,
  Text: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))

const sidePanelState = (activeSubPanelId: SubPanelId | null = null) => ({
  openTranscript: vi.fn(),
  openLiveTranscript,
  openScreenRecording: vi.fn(),
  activeSubPanelId,
  isToolsOpen: true,
  isSidePanelOpen: true,
})

describe('Tools', () => {
  beforeEach(() => {
    useSidePanelMock.mockReturnValue(sidePanelState())
    useAreSubtitlesAvailableMock.mockReturnValue(true)
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('opens the live transcript from the meeting tools list', () => {
    render(<Tools />)

    fireEvent.click(screen.getByRole('button', { name: /live transcript/i }))

    expect(openLiveTranscript).toHaveBeenCalledOnce()
  })

  it('renders the live transcript inside the meeting tools side panel', () => {
    useSidePanelMock.mockReturnValue(sidePanelState(SubPanelId.LIVE_TRANSCRIPT))

    render(<Tools />)

    expect(screen.getByText('Live transcript panel')).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: /live transcript/i })
    ).toBeNull()
  })

  it('does not expose the tool when live subtitles are unavailable', () => {
    useAreSubtitlesAvailableMock.mockReturnValue(false)

    render(<Tools />)

    expect(
      screen.queryByRole('button', { name: /live transcript/i })
    ).toBeNull()
  })
})
