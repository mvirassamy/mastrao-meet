import { describe, expect, it } from 'vitest'
import {
  LIVE_TRANSCRIPTION_TOPIC,
  parseLiveTranscriptionGapStream,
  parseLiveTranscriptionStream,
  toLegacyTranscriptionEvent,
} from './index'

describe('live transcription contract', () => {
  it('parses a worker gap marker from its dedicated topic', () => {
    expect(
      parseLiveTranscriptionGapStream(
        JSON.stringify({
          schemaVersion: 1,
          roomId: 'room-1',
          participantIdentity: 'alice',
          trackSid: 'TR_A',
          fromLegId: 'LEG_1',
          toLegId: 'LEG_2',
          gapId: 'GAP_1',
          durationMs: 820,
          reason: 'provider-reconnect',
        })
      )
    ).toMatchObject([
      { type: 'gap', gap: { id: 'GAP_1', reason: 'provider-reconnect' } },
    ])
    expect(parseLiveTranscriptionGapStream('plain text')).toEqual([])
    expect(
      parseLiveTranscriptionGapStream(
        JSON.stringify({ schemaVersion: 2, gapId: 'GAP_2', reason: 'x' })
      )
    ).toEqual([])
  })

  it('parses a versioned multi-segment envelope', () => {
    const events = parseLiveTranscriptionStream(
      JSON.stringify({
        schemaVersion: 1,
        segments: [
          {
            participantIdentity: 'alice',
            trackSid: 'TR_alice',
            legId: 'leg-1',
            itemId: 'item-1',
            sequence: 4,
            revision: 2,
            state: 'interim',
            text: 'bonjour',
          },
          {
            participantIdentity: 'bob',
            trackSid: 'TR_bob',
            legId: 'leg-1',
            itemId: 'item-1',
            sequence: 5,
            revision: 0,
            state: 'final',
            text: 'salut',
          },
        ],
      })
    )

    expect(LIVE_TRANSCRIPTION_TOPIC).toBe('lk.transcription')
    expect(events).toEqual([
      {
        type: 'segments',
        segments: [
          expect.objectContaining({
            participantIdentity: 'alice',
            sequence: 4,
            revision: 2,
            state: 'interim',
            text: 'bonjour',
            metadataSource: 'envelope',
          }),
          expect.objectContaining({
            participantIdentity: 'bob',
            sequence: 5,
            revision: 0,
            state: 'final',
            text: 'salut',
          }),
        ],
      },
    ])
  })

  it('falls back to standard LiveKit attributes for plain text streams', () => {
    const [event] = parseLiveTranscriptionStream(
      'texte provisoire',
      {
        'lk.segment_id': 'item-1',
        'lk.transcribed_track_id': 'TR_alice',
        'lk.transcription_final': 'false',
      },
      'agent',
      'stream-1'
    )

    expect(event).toMatchObject({
      type: 'segments',
      segments: [
        {
          participantIdentity: 'agent',
          trackSid: 'TR_alice',
          legId: 'legacy',
          itemId: 'item-1',
          state: 'interim',
          text: 'texte provisoire',
          metadataSource: 'livekit-attributes',
        },
      ],
    })
  })

  it.each(['2024', 'true', 'null'])(
    'keeps valid JSON primitive %s as a text segment',
    (payload) => {
      const [event] = parseLiveTranscriptionStream(
        payload,
        {
          'lk.segment_id': `item-${payload}`,
          'lk.transcribed_track_id': 'TR_alice',
        },
        'agent',
        `stream-${payload}`
      )

      expect(event).toMatchObject({
        type: 'segments',
        segments: [{ text: payload }],
      })
    }
  )

  it('preserves the exact raw text for a valid JSON number', () => {
    const [event] = parseLiveTranscriptionStream(
      '2.50',
      {
        'lk.segment_id': 'item-2-50',
        'lk.transcribed_track_id': 'TR_alice',
      },
      'agent',
      'stream-2-50'
    )

    expect(event).toMatchObject({
      type: 'segments',
      segments: [{ text: '2.50' }],
    })
  })

  it('adapts all legacy TranscriptionReceived segments', () => {
    const event = toLegacyTranscriptionEvent(
      [
        {
          id: 'first',
          text: 'un',
          language: 'fr',
          startTime: 1,
          endTime: 2,
          final: false,
          firstReceivedTime: 1,
          lastReceivedTime: 2,
        },
        {
          id: 'second',
          text: 'deux',
          language: 'fr',
          startTime: 3,
          endTime: 4,
          final: true,
          firstReceivedTime: 3,
          lastReceivedTime: 4,
        },
      ],
      { identity: 'alice' } as never,
      { trackSid: 'TR_alice' } as never
    )

    expect(event.type).toBe('segments')
    if (event.type === 'segments') {
      expect(
        event.segments.map(({ itemId, state }) => [itemId, state])
      ).toEqual([
        ['first', 'interim'],
        ['second', 'final'],
      ])
    }
  })

  it('rejects an unsupported envelope version', () => {
    expect(
      parseLiveTranscriptionStream(
        JSON.stringify({ schemaVersion: 2, text: 'ignored' })
      )
    ).toEqual([])
  })
})
