import { act, cleanup, render, screen } from '@testing-library/react'
import { Track } from 'livekit-client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RecordingStage } from './RecordingStage'

const liveKit = vi.hoisted(() => ({
  room: {
    localParticipant: { identity: 'egress-recorder' },
    remoteParticipants: new Map(),
    on: vi.fn(),
    off: vi.fn(),
  },
  tracks: [] as Array<Record<string, unknown>>,
}))

vi.mock('@livekit/components-react', () => ({
  useRoomContext: () => liveKit.room,
  useTracks: () => liveKit.tracks,
  VideoTrack: ({ className }: { className?: string }) => (
    <video className={className} data-testid="video-track" />
  ),
}))

vi.mock('@livekit/components-core', () => ({
  getTrackReferenceSource: (track: { source: Track.Source }) => track.source,
  isTrackReference: (track: { publication?: unknown }) =>
    Boolean(track.publication),
}))

vi.mock('./RecordingParticipantTile', () => ({
  RecordingParticipantTile: ({
    track,
  }: {
    track: { participant: { identity: string } }
  }) => <div>{track.participant.identity}</div>,
}))

beforeEach(() => {
  vi.useFakeTimers()
  liveKit.tracks = []
  liveKit.room.remoteParticipants = new Map()
  liveKit.room.on.mockClear()
  liveKit.room.off.mockClear()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('RecordingStage', () => {
  it('gives screen sharing the main stage and keeps participant tiles', () => {
    liveKit.tracks = [
      {
        source: Track.Source.Camera,
        participant: { identity: 'martin' },
      },
      {
        source: Track.Source.ScreenShare,
        participant: { identity: 'matthias' },
        publication: {
          source: Track.Source.ScreenShare,
          trackSid: 'screen-share',
        },
      },
    ]

    render(<RecordingStage />)

    expect(screen.getByLabelText('Partage d’écran')).toBeTruthy()
    expect(screen.getByText('martin')).toBeTruthy()
    expect(screen.getByTestId('video-track')).toBeTruthy()
  })

  it('signals Egress after an audio track is subscribed', async () => {
    const log = vi.spyOn(console, 'log').mockImplementation(() => undefined)
    liveKit.room.remoteParticipants = new Map([
      [
        'matthias',
        {
          trackPublications: new Map([
            ['microphone', { isSubscribed: true, kind: Track.Kind.Audio }],
          ]),
        },
      ],
    ])

    render(<RecordingStage />)
    await act(() => vi.advanceTimersByTimeAsync(600))

    expect(log).toHaveBeenCalledOnce()
    expect(log).toHaveBeenCalledWith('START_RECORDING')
  })

  it('waits for a decoded video frame before signaling Egress', async () => {
    const log = vi.spyOn(console, 'log').mockImplementation(() => undefined)
    liveKit.room.remoteParticipants = new Map([
      [
        'martin',
        {
          trackPublications: new Map([
            [
              'camera',
              {
                isSubscribed: true,
                kind: Track.Kind.Video,
                videoTrack: {
                  getRTCStatsReport: () =>
                    Promise.resolve(
                      new Map([
                        [
                          'inbound-video',
                          { type: 'inbound-rtp', framesDecoded: 1 },
                        ],
                      ])
                    ),
                },
              },
            ],
          ]),
        },
      ],
    ])

    render(<RecordingStage />)
    await act(() => vi.advanceTimersByTimeAsync(100))

    expect(log).toHaveBeenCalledOnce()
    expect(log).toHaveBeenCalledWith('START_RECORDING')
  })
})
