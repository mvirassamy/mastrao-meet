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

  it('keeps a long pause in the same turn as a new paragraph', () => {
    const turns = groupLiveTranscriptSpeakerTurns([
      segment('alice', 'Première phrase.', 1, { receivedAt: 1_000 }),
      segment('alice', 'Nouvelle phrase.', 2, { receivedAt: 6_000 }),
    ])

    expect(turns.map(({ text }) => text)).toEqual([
      'Première phrase.\n\nNouvelle phrase.',
    ])
  })

  it('starts a new turn when another speaker establishes the floor', () => {
    const turns = groupLiveTranscriptSpeakerTurns([
      segment('alice', 'Première intervention.', 1, { receivedAt: 1_000 }),
      segment('bob', 'Je prends la parole.', 2, { receivedAt: 1_500 }),
      segment('bob', 'Je poursuis.', 3, { receivedAt: 3_500 }),
      segment('alice', 'Je reprends.', 4, { receivedAt: 4_000 }),
    ])

    expect(
      turns.map(({ participantIdentity, text }) => ({
        participantIdentity,
        text,
      }))
    ).toEqual([
      { participantIdentity: 'alice', text: 'Première intervention.' },
      {
        participantIdentity: 'bob',
        text: 'Je prends la parole. Je poursuis.',
      },
      { participantIdentity: 'alice', text: 'Je reprends.' },
    ])
  })

  it('keeps a speaker turn across provider speech-window generations', () => {
    const turns = groupLiveTranscriptSpeakerTurns([
      segment('alice', 'Track initial', 1),
      segment('alice', 'même micro après une respiration', 2, {
        legId: 'leg-restarted',
      }),
    ])

    expect(turns.map(({ text }) => text)).toEqual([
      'Track initial même micro après une respiration',
    ])
  })

  it('keeps the same turn when a participant track changes without an interruption', () => {
    const turns = groupLiveTranscriptSpeakerTurns([
      segment('alice', 'Track initial', 1),
      segment('alice', 'nouveau micro', 2, { trackSid: 'TR_reconnected' }),
    ])

    expect(turns.map(({ text }) => text)).toEqual([
      'Track initial nouveau micro',
    ])
  })

  it('keeps one turn when a participant resumes through a known identity alias', () => {
    const canonicalIdentities = new Map([
      ['alice-before-reconnect', 'alice'],
      ['alice-after-reconnect', 'alice'],
    ])
    const turns = groupLiveTranscriptSpeakerTurns(
      [
        segment('alice-before-reconnect', 'Bonjour à tous.', 1),
        segment('alice-after-reconnect', 'Je poursuis.', 2),
      ],
      (identity) => canonicalIdentities.get(identity)
    )

    expect(turns.map(({ text }) => text)).toEqual([
      'Bonjour à tous. Je poursuis.',
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
