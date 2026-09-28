import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LiveTranscriptToggle } from './LiveTranscriptToggle'

const toggleLiveTranscript = vi.fn()
const startTranscription = vi.fn()
const stopTranscription = vi.fn()
const mockUseConfig = vi.hoisted(() => vi.fn())
const mockUseLiveTranscription = vi.hoisted(() => vi.fn())

vi.mock('@/api/useConfig', () => ({
  isLiveTranscriptPanelEnabled: (config: {
    subtitle?: { live_transcript_panel_enabled?: boolean }
  }) => config?.subtitle?.live_transcript_panel_enabled === true,
  useConfig: mockUseConfig,
}))

vi.mock('@/features/subtitle/store', () => ({
  useLiveTranscription: mockUseLiveTranscription,
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
    tooltip,
    ...props
  }: {
    children: ReactNode
    onPress?: () => void
    isSelected?: boolean
    tooltip?: string
  }) => (
    <button onClick={onPress} data-tooltip={tooltip} {...props}>
      {children}
    </button>
  ),
}))

describe('LiveTranscriptToggle', () => {
  beforeEach(() => {
    mockUseConfig.mockReturnValue({
      data: { subtitle: { live_transcript_panel_enabled: true } },
    })
    mockUseLiveTranscription.mockReturnValue({
      status: 'unknown',
      startTranscription,
      stopTranscription,
    })
  })

  afterEach(() => {
    cleanup()
    toggleLiveTranscript.mockClear()
    startTranscription.mockClear()
    stopTranscription.mockClear()
    mockUseConfig.mockReset()
    mockUseLiveTranscription.mockReset()
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

    expect(
      screen.getByRole('button').getAttribute('data-transcription-status')
    ).toBe('unknown')
  })

  it('reflects collective live status without controlling transcription', () => {
    mockUseLiveTranscription.mockReturnValue({ status: 'live' })

    render(<LiveTranscriptToggle />)

    const button = screen.getByRole('button')
    expect(button.getAttribute('data-transcription-status')).toBe('live')
    expect(
      button.querySelector('[data-testid="live-transcript-active-indicator"]')
    ).not.toBeNull()
    expect(button.getAttribute('aria-label')).toContain(
      'transcriptionInProgress'
    )
    expect(button.getAttribute('data-tooltip')).toContain(
      'transcriptionInProgress'
    )
  })

  it.each(['inactive', 'stopped', 'unknown'] as const)(
    'does not show an activity indicator for %s',
    (status) => {
      mockUseLiveTranscription.mockReturnValue({ status })

      render(<LiveTranscriptToggle />)

      const button = screen.getByRole('button')
      expect(
        button.querySelector('[data-testid="live-transcript-active-indicator"]')
      ).toBeNull()
      expect(button.getAttribute('aria-label')).not.toContain(
        'transcriptionInProgress'
      )
    }
  )

  it('opens the panel without dispatching a transcription start action', () => {
    render(<LiveTranscriptToggle />)

    fireEvent.click(screen.getByRole('button'))

    expect(toggleLiveTranscript).toHaveBeenCalledOnce()
    expect(startTranscription).not.toHaveBeenCalled()
    expect(stopTranscription).not.toHaveBeenCalled()
  })
})
