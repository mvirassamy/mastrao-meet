import { cleanup, render, screen } from '@testing-library/react'
import type { TrackReferenceOrPlaceholder } from '@livekit/components-core'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RecordingParticipantTile } from './RecordingParticipantTile'

vi.mock('@livekit/components-react', () => ({
  useIsSpeaking: () => true,
  VideoTrack: () => <video data-testid="camera" />,
}))

afterEach(cleanup)

describe('RecordingParticipantTile', () => {
  it('keeps a named participant visible when their camera is off', () => {
    const track = {
      participant: {
        identity: 'participant-martin',
        name: 'Martin Dupont',
      },
      source: 'camera',
    } as unknown as TrackReferenceOrPlaceholder

    const { container } = render(<RecordingParticipantTile track={track} />)

    expect(screen.getByText('MD')).toBeTruthy()
    expect(screen.getByText('Martin Dupont')).toBeTruthy()
    expect(screen.queryByTestId('camera')).toBeNull()
    expect(container.querySelector('[data-speaking="true"]')).toBeTruthy()
  })
})
