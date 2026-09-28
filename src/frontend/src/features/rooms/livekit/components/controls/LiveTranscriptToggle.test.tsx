import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LiveTranscriptToggle } from './LiveTranscriptToggle'

const toggleLiveTranscript = vi.fn()

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
  afterEach(() => {
    cleanup()
    toggleLiveTranscript.mockClear()
  })

  it('opens the panel without dispatching a transcription start action', () => {
    render(<LiveTranscriptToggle />)

    fireEvent.click(screen.getByRole('button'))

    expect(toggleLiveTranscript).toHaveBeenCalledOnce()
  })
})
