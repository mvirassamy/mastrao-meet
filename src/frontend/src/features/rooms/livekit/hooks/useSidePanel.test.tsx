import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { closeSidePanel, layoutStore } from '@/stores/layout'
import { PanelId, useSidePanel } from './useSidePanel'

const Harness = () => {
  const { isLiveTranscriptOpen, toggleLiveTranscript } = useSidePanel()

  return (
    <button onClick={toggleLiveTranscript} aria-pressed={isLiveTranscriptOpen}>
      {isLiveTranscriptOpen ? 'open' : 'closed'}
    </button>
  )
}

describe('useSidePanel live transcript', () => {
  afterEach(cleanup)

  beforeEach(() => {
    closeSidePanel()
  })

  it('opens a distinct live panel without starting transcription', () => {
    const view = render(<Harness />)

    fireEvent.click(screen.getByRole('button'))
    view.rerender(<Harness />)

    expect(layoutStore.activePanelId).toBe(PanelId.LIVE_TRANSCRIPT)
    expect(layoutStore.activeSubPanelId).toBeNull()
    expect(screen.getByRole('button').getAttribute('aria-pressed')).toBe('true')
  })

  it('closes the live panel without changing the transcription state', () => {
    layoutStore.activePanelId = PanelId.LIVE_TRANSCRIPT
    render(<Harness />)

    fireEvent.click(screen.getByRole('button'))

    expect(layoutStore.activePanelId).toBeNull()
  })
})
