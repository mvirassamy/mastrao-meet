import { cleanup, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import RecordingTemplateApp from './RecordingTemplateApp'

vi.mock('@livekit/components-react', () => ({
  LiveKitRoom: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  RoomAudioRenderer: () => <div data-testid="room-audio" />,
}))

vi.mock('./RecordingStage', () => ({
  RecordingStage: () => <div data-testid="recording-stage" />,
}))

afterEach(cleanup)

describe('RecordingTemplateApp', () => {
  it('renders room audio alongside the visual recording stage', () => {
    window.history.replaceState(
      null,
      '',
      '/recording-template?url=wss%3A%2F%2Frtc.test&token=secret'
    )

    render(<RecordingTemplateApp />)

    expect(screen.getByTestId('recording-stage')).toBeTruthy()
    expect(screen.getByTestId('room-audio')).toBeTruthy()
  })
})
