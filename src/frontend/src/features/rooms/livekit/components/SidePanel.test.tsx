import { cleanup, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mockUseConfig = vi.hoisted(() => vi.fn())
const mockUseSidePanel = vi.hoisted(() => vi.fn())

vi.mock('@/api/useConfig', () => ({
  isLiveTranscriptPanelEnabled: (config: {
    subtitle?: { live_transcript_panel_enabled?: boolean }
  }) => config?.subtitle?.live_transcript_panel_enabled === true,
  useConfig: mockUseConfig,
}))

vi.mock('../hooks/useSidePanel', () => ({
  PanelId: { LIVE_TRANSCRIPT: 'liveTranscript' },
  useSidePanel: mockUseSidePanel,
}))

vi.mock('@/stores/layout', () => ({
  closeSidePanel: vi.fn(),
  layoutStore: { activeSubPanelId: null },
}))

vi.mock('@/styled-system/css', () => ({ css: () => '' }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('react-aria-components', () => ({
  Heading: ({ children }: { children: ReactNode }) => <h1>{children}</h1>,
}))
vi.mock('@/primitives', () => ({
  Button: ({ children }: { children: ReactNode }) => (
    <button>{children}</button>
  ),
}))
vi.mock('@/primitives/appAppearance', () => ({
  AppAppearanceProvider: ({ children }: { children: ReactNode }) => (
    <>{children}</>
  ),
}))
vi.mock('@/icons', () => ({
  ArrowLeftIcon: () => null,
  CloseIcon: () => null,
}))
vi.mock('@/features/participants/components/ParticipantsList', () => ({
  ParticipantsList: () => null,
}))
vi.mock('@/features/chat/components/Chat', () => ({ Chat: () => null }))
vi.mock('./effects/Effects', () => ({ Effects: () => null }))
vi.mock('./Admin', () => ({ Admin: () => null }))
vi.mock('./Tools', () => ({ Tools: () => null }))
vi.mock('./Info', () => ({ Info: () => null }))
vi.mock('@/features/reactions/hooks/useReactionsToolbar', () => ({
  useReactionsToolbar: () => ({ isOpen: false }),
}))
vi.mock('@/hooks/useRestoreFocus', () => ({ useRestoreFocus: vi.fn() }))
vi.mock('@/features/subtitle/component/LiveTranscriptPanel', () => ({
  LiveTranscriptPanel: () => <div data-testid="live-transcript-panel" />,
}))
vi.mock('@/utils/useIsMobile', () => ({ useIsMobile: () => false }))

import { SidePanel } from './SidePanel'

const liveTranscriptPanelState = {
  activePanelId: 'liveTranscript',
  activeSubPanelId: null,
  isParticipantsOpen: false,
  isEffectsOpen: false,
  isChatOpen: false,
  isSidePanelOpen: true,
  isToolsOpen: false,
  isAdminOpen: false,
  isInfoOpen: false,
  isSubPanelOpen: false,
  isLiveTranscriptOpen: true,
}

describe('SidePanel live transcript visibility', () => {
  beforeEach(() => {
    mockUseSidePanel.mockReturnValue(liveTranscriptPanelState)
  })

  afterEach(() => {
    cleanup()
    mockUseConfig.mockReset()
    mockUseSidePanel.mockReset()
  })

  it('is not mounted when the live panel flag is disabled', () => {
    mockUseConfig.mockReturnValue({
      data: { subtitle: { live_transcript_panel_enabled: false } },
    })

    render(<SidePanel />)

    expect(screen.queryByRole('complementary')).toBeNull()
  })

  it('is mounted when the live panel flag is enabled', () => {
    mockUseConfig.mockReturnValue({
      data: { subtitle: { live_transcript_panel_enabled: true } },
    })

    render(<SidePanel />)

    expect(screen.getByRole('complementary')).not.toBeNull()
    expect(screen.getByTestId('live-transcript-panel')).not.toBeNull()
  })
})
