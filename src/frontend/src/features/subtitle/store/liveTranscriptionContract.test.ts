import { describe, expect, it } from 'vitest'
import {
  LIVE_TRANSCRIPTION_TOPIC,
  parseLiveTranscriptionGapStream,
  parseLiveTranscriptionStream,
  readLiveTranscriptionStream,
  roomAudioLiveWorkerIdentity,
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
          receivedAt: 1,
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
    expect(
      parseLiveTranscriptionGapStream(
        JSON.stringify({
          schemaVersion: 1,
          id: 'GAP_ALIAS',
          reason: 'non-canonical alias',
          receivedAt: 1,
        })
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
            receivedAt: 1,
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
            receivedAt: 2,
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

  it('rejects incomplete and unversioned transport payloads', () => {
    expect(parseLiveTranscriptionStream('texte provisoire')).toEqual([])
    expect(
      parseLiveTranscriptionStream(
        JSON.stringify({
          schemaVersion: 1,
          participantIdentity: 'alice',
          text: 'missing track binding',
        })
      )
    ).toEqual([])
  })

  it('rejects an unsupported envelope version', () => {
    expect(
      parseLiveTranscriptionStream(
        JSON.stringify({ schemaVersion: 2, text: 'ignored' })
      )
    ).toEqual([])
  })

  it('rejects coercible fields and an invalid member in a batch', () => {
    const base = {
      participantIdentity: 'alice',
      trackSid: 'TR_alice',
      legId: 'leg-1',
      itemId: 'item-1',
      sequence: 1,
      revision: 1,
      state: 'final',
      text: 'bonjour',
      receivedAt: 1,
    }
    expect(
      parseLiveTranscriptionStream(
        JSON.stringify({ schemaVersion: '1', ...base })
      )
    ).toEqual([])
    expect(
      parseLiveTranscriptionStream(
        JSON.stringify({ schemaVersion: 1, ...base, sequence: '1' })
      )
    ).toEqual([])
    expect(
      parseLiveTranscriptionStream(
        JSON.stringify({
          schemaVersion: 1,
          segments: [base, { ...base, itemId: 'item-2', receivedAt: null }],
        })
      )
    ).toEqual([])
    expect(
      parseLiveTranscriptionStream(
        JSON.stringify({ schemaVersion: 1, segments: 'not-an-array', ...base })
      )
    ).toEqual([])
  })

  it('accepts transcript text only from the exact room live worker', async () => {
    const roomId = 'room-1'
    const workerIdentity = await roomAudioLiveWorkerIdentity(roomId)
    const reader = {
      info: { id: 'stream-1' },
      readAll: async () =>
        JSON.stringify({
          schemaVersion: 1,
          participantIdentity: 'alice',
          trackSid: 'TR_alice',
          legId: 'leg-1',
          itemId: 'item-1',
          sequence: 1,
          revision: 1,
          state: 'final',
          text: 'bonjour',
          receivedAt: 1,
        }),
    }

    await expect(
      readLiveTranscriptionStream(
        reader,
        'ordinary-participant',
        workerIdentity
      )
    ).resolves.toEqual([])
    await expect(
      readLiveTranscriptionStream(reader, workerIdentity, workerIdentity)
    ).resolves.toHaveLength(1)
  })
})
