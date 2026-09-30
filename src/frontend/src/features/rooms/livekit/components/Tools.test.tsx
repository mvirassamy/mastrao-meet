import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Tools } from './Tools'

const mocks = vi.hoisted(() => ({
  ensureSubtitleStarted: vi.fn(),
  openLiveTranscript: vi.fn(),
  syncSubtitleState: vi.fn(),
}))

vi.mock('../hooks/useSidePanel', () => ({
  SubPanelId: {
    TRANSCRIPT: 'transcript',
    LIVE_TRANSCRIPT: 'liveTranscript',
    SCREEN_RECORDING: 'screenRecording',
  },
  useSidePanel: () => ({
    openTranscript: vi.fn(),
    openLiveTranscript: mocks.openLiveTranscript,
    openScreenRecording: vi.fn(),
    activeSubPanelId: null,
    isToolsOpen: true,
    isSidePanelOpen: true,
  }),
}))
vi.mock('@/features/subtitle/store/liveTranscriptionContext', () => ({
  useLiveTranscription: () => ({
    ensureSubtitleStarted: mocks.ensureSubtitleStarted,
    syncSubtitleState: mocks.syncSubtitleState,
  }),
}))
vi.mock('@/features/subtitle/hooks/useAreSubtitlesAvailable', () => ({
  useAreSubtitlesAvailable: () => true,
}))
vi.mock('@/features/recording', () => ({
  RecordingMode: { Transcript: 'transcript', ScreenRecording: 'screen' },
  ScreenRecordingSidePanel: () => null,
  TranscriptSidePanel: () => null,
  useIsRecordingModeEnabled: () => false,
}))
vi.mock('@/api/useConfig', () => ({ useConfig: () => ({ data: {} }) }))
vi.mock('@/hooks/useRestoreFocus', () => ({ useRestoreFocus: vi.fn() }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/primitives', () => ({
  A: ({ children }: { children: ReactNode }) => <a>{children}</a>,
  Button: ({
    children,
    onPress,
  }: {
    children: ReactNode
    onPress: () => void
  }) => (
    <button type="button" onClick={onPress}>
      {children}
    </button>
  ),
  Div: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Icon: () => <span />,
  Text: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))

afterEach(cleanup)

describe('Tools live transcript action', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.ensureSubtitleStarted.mockResolvedValue(undefined)
    mocks.syncSubtitleState.mockResolvedValue(undefined)
  })

  it('opens and starts from the tools panel shared by desktop and mobile', async () => {
    render(<Tools />)

    fireEvent.click(
      screen.getByRole('button', { name: /tools.liveTranscript.title/ })
    )

    expect(mocks.openLiveTranscript).toHaveBeenCalledOnce()
    expect(mocks.ensureSubtitleStarted).toHaveBeenCalledOnce()
    await waitFor(() => expect(mocks.syncSubtitleState).toHaveBeenCalledOnce())
  })
})
