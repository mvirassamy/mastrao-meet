import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

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

const DoubleProbe = () => {
  const lifecycle = useMeetingLifecycle()
  return (
    <button
      type="button"
      onClick={() => {
        const first = lifecycle.beginEnding()
        const second = lifecycle.beginEnding()
        window.history.replaceState({ first, second }, '')
      }}
    >
      close
    </button>
  )
}

describe('MeetingLifecycleProvider', () => {
  afterEach(cleanup)

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

  it('reuses one request id for two close attempts in the same tick', () => {
    const roomId = 'room_0123456789abcdef0123456789abcdef'
    window.sessionStorage.clear()

    render(
      <MeetingLifecycleProvider roomId={roomId}>
        <DoubleProbe />
      </MeetingLifecycleProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: 'close' }))

    expect(window.history.state.first).toBe(window.history.state.second)
    expect(window.history.state.first).toBe(
      window.sessionStorage.getItem(`mastrao-meeting-close-v1:${roomId}`)
    )
  })
})
