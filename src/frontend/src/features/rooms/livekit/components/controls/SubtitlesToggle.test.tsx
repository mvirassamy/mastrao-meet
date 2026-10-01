import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SubtitlesToggle } from './SubtitlesToggle'

const toggleSubtitles = vi.fn()
const useSubtitlesMock = vi.hoisted(() => vi.fn())
const useAreSubtitlesAvailableMock = vi.hoisted(() => vi.fn())

vi.mock('@/features/subtitle/hooks/useSubtitles', () => ({
  useSubtitles: useSubtitlesMock,
}))
vi.mock('@/features/subtitle/hooks/useAreSubtitlesAvailable', () => ({
  useAreSubtitlesAvailable: useAreSubtitlesAvailableMock,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      key === 'closed'
        ? 'Show captions over the video'
        : 'Hide captions over the video',
  }),
}))
vi.mock('@/icons', () => ({ CaptionsIcon: () => <span>CC</span> }))
vi.mock('@/primitives', () => ({
  ToggleButton: ({
    children,
    onPress,
    'aria-label': label,
  }: {
    children: ReactNode
    onPress?: () => void
    'aria-label'?: string
  }) => (
    <button type="button" aria-label={label} onClick={onPress}>
      {children}
    </button>
  ),
}))

describe('SubtitlesToggle', () => {
  beforeEach(() => {
    useAreSubtitlesAvailableMock.mockReturnValue(true)
    useSubtitlesMock.mockReturnValue({
      areSubtitlesOpen: false,
      areSubtitlesPending: false,
      toggleSubtitles,
    })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('controls only captions displayed over the video', () => {
    render(<SubtitlesToggle />)

    fireEvent.click(
      screen.getByRole('button', { name: 'Show captions over the video' })
    )

    expect(toggleSubtitles).toHaveBeenCalledOnce()
  })

  it('is hidden when captions are unavailable', () => {
    useAreSubtitlesAvailableMock.mockReturnValue(false)

    const view = render(<SubtitlesToggle />)

    expect(view.container.childElementCount).toBe(0)
  })
})
