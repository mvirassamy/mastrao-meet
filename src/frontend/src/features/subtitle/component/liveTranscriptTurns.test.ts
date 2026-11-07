import { describe, expect, it } from 'vitest'
import type { LiveTranscriptionSegment } from '../store/liveTranscriptionTypes'
import { groupLiveTranscriptSpeakerTurns } from './liveTranscriptTurns'

const segment = (
  participantIdentity: string,
  text: string,
  sequence: number,
  overrides: Partial<LiveTranscriptionSegment> = {}
): LiveTranscriptionSegment => ({
  key: `${participantIdentity}-${sequence}`,
  participantIdentity,
  trackSid: `TR_${participantIdentity}`,
  legId: `leg-${participantIdentity}`,
  itemId: `item-${sequence}`,
  state: 'final',
  text,
  sequence,
  revision: 1,
  receivedAt: sequence * 100,
  metadataSource: 'envelope',
  ...overrides,
})

describe('groupLiveTranscriptSpeakerTurns', () => {
  it('keeps one readable turn per speaker when their fragments interleave', () => {
    const turns = groupLiveTranscriptSpeakerTurns([
      segment('alice', 'Quand les deux', 1),
      segment('bob', 'deux', 2),
      segment('alice', 'parlent', 3),
      segment('bob', 'parlent, ça', 4),
      segment('alice', 'ensemble.', 5),
      segment('bob', 'fonctionne.', 6),
    ])

    expect(turns).toHaveLength(2)
    expect(
      turns.map(({ participantIdentity, text }) => ({
        participantIdentity,
        text,
      }))
    ).toEqual([
      {
        participantIdentity: 'alice',
        text: 'Quand les deux parlent ensemble.',
      },
      {
        participantIdentity: 'bob',
        text: 'deux parlent, ça fonctionne.',
      },
    ])
  })

  it('starts a new turn after a five-second pause', () => {
    const turns = groupLiveTranscriptSpeakerTurns([
      segment('alice', 'Première phrase.', 1, { receivedAt: 1_000 }),
      segment('alice', 'Nouvelle phrase.', 2, { receivedAt: 6_000 }),
    ])

    expect(turns.map(({ text }) => text)).toEqual([
      'Première phrase.',
      'Nouvelle phrase.',
    ])
  })

  it('does not merge across track or provider-stream generations', () => {
    const turns = groupLiveTranscriptSpeakerTurns([
      segment('alice', 'Track initial', 1),
      segment('alice', 'nouveau track', 2, { trackSid: 'TR_reconnected' }),
      segment('alice', 'nouveau flux', 3, { legId: 'leg-restarted' }),
    ])

    expect(turns.map(({ text }) => text)).toEqual([
      'Track initial',
      'nouveau track',
      'nouveau flux',
    ])
  })

  it('preserves punctuation and the stable key of the first fragment', () => {
    const turns = groupLiveTranscriptSpeakerTurns([
      segment('alice', 'Aujourd’', 1),
      segment('alice', 'hui', 2),
      segment('alice', ',', 3),
      segment('alice', 'tout va bien.', 4),
    ])

    expect(turns).toHaveLength(1)
    expect(turns[0]).toMatchObject({
      key: 'alice-1',
      text: 'Aujourd’hui, tout va bien.',
      sequence: 4,
    })
  })

  it('does not mutate the ordered transcript segments', () => {
    const segments = Array.from({ length: 5_000 }, (_, index) =>
      segment(index % 2 === 0 ? 'alice' : 'bob', `mot-${index}`, index + 1)
    )
    const snapshot = structuredClone(segments)

    const turns = groupLiveTranscriptSpeakerTurns(segments)

    expect(turns).toHaveLength(2)
    expect(segments).toEqual(snapshot)
  })
})
