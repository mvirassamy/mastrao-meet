import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LiveTranscriptToggle } from './LiveTranscriptToggle'

const toggleLiveTranscript = vi.fn()
const mockUseConfig = vi.hoisted(() => vi.fn())

vi.mock('@/api/useConfig', () => ({
  isLiveTranscriptPanelEnabled: (config: {
    subtitle?: { live_transcript_panel_enabled?: boolean }
  }) => config?.subtitle?.live_transcript_panel_enabled === true,
  useConfig: mockUseConfig,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('@/features/rooms/livekit/hooks/useSidePanel', () => ({
  useSidePanel: () => ({
    isLiveTranscriptOpen: false,
    toggleLiveTranscript,
  }),
}))

vi.mock('@/icons', () => ({ TranscriptIcon: () => <span /> }))

vi.mock('@/primitives', () => ({
  ToggleButton: ({
    children,
    onPress,
    isSelected: _isSelected,
    tooltip: _tooltip,
    ...props
  }: {
    children: ReactNode
    onPress?: () => void
    isSelected?: boolean
    tooltip?: string
  }) => (
    <button onClick={onPress} {...props}>
      {children}
    </button>
  ),
}))

describe('LiveTranscriptToggle', () => {
  beforeEach(() => {
    mockUseConfig.mockReturnValue({
      data: { subtitle: { live_transcript_panel_enabled: true } },
    })
  })

  afterEach(() => {
    cleanup()
    toggleLiveTranscript.mockClear()
    mockUseConfig.mockReset()
  })

  it('stays hidden when the live panel flag is disabled', () => {
    mockUseConfig.mockReturnValue({
      data: { subtitle: { live_transcript_panel_enabled: false } },
    })

    render(<LiveTranscriptToggle />)

    expect(screen.queryByRole('button')).toBeNull()
  })

  it('renders when the live panel flag is enabled', () => {
    render(<LiveTranscriptToggle />)

    expect(screen.getByRole('button')).not.toBeNull()
  })

  it('opens the panel without dispatching a transcription start action', () => {
    render(<LiveTranscriptToggle />)

    fireEvent.click(screen.getByRole('button'))

    expect(toggleLiveTranscript).toHaveBeenCalledOnce()
  })
})
