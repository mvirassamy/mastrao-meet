import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { useMeetingLifecycle } from './MeetingLifecycleContext'
import { MeetingLifecycleProvider } from './MeetingLifecycleProvider'

const Probe = () => {
  const lifecycle = useMeetingLifecycle()
  return (
    <button type="button" onClick={() => lifecycle.beginEnding()}>
      {lifecycle.phase}:{lifecycle.closeRequestId ?? 'none'}
    </button>
  )
}

describe('MeetingLifecycleProvider', () => {
  it('restores one uncertain close intent with the same request id', () => {
    const roomId = 'room_0123456789abcdef0123456789abcdef'
    const key = `mastrao-meeting-close-v1:${roomId}`
    window.sessionStorage.setItem(key, 'close_existing')

    render(
      <MeetingLifecycleProvider roomId={roomId}>
        <Probe />
      </MeetingLifecycleProvider>
    )

    expect(screen.getByRole('button').textContent).toBe(
      'uncertain:close_existing'
    )
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByRole('button').textContent).toBe(
      'requesting:close_existing'
    )
  })
})
