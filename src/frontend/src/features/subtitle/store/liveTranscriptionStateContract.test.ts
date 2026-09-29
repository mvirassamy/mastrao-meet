import { DataPacket_Kind } from 'livekit-client'
import { describe, expect, it } from 'vitest'
import {
  isReliableBackendStatePacket,
  parseLiveTranscriptionStatePacket,
  parseLiveTranscriptionStateSnapshot,
} from './liveTranscriptionStateContract'

const packet = (overrides: Record<string, unknown> = {}) => ({
  schemaVersion: 1,
  roomSid: 'RM_room-1',
  state: 'live',
  stateVersion: 2,
  eventId: 'event-2',
  occurredAt: '2026-09-29T00:00:00.000Z',
  sessionId: 'session-1',
  reason: null,
  ...overrides,
})

describe('live transcription state contract', () => {
  it('validates the public v1 packet and rejects unknown fields', () => {
    expect(
      parseLiveTranscriptionStatePacket(JSON.stringify(packet()))
    ).toMatchObject({ ok: true, packet: packet() })

    expect(
      parseLiveTranscriptionStatePacket(
        JSON.stringify(packet({ privateWorkerState: 'ready' }))
      )
    ).toEqual({ ok: false, error: 'unknown-field' })
  })

  it.each([
    ['wrong schema version', { schemaVersion: 2 }, 'invalid-version'],
    ['unknown state', { state: 'worker_ready' }, 'invalid-state'],
    ['missing room SID', { roomSid: '' }, 'invalid-room-sid'],
    [
      'non-monotone version type',
      { stateVersion: '2' },
      'invalid-state-version',
    ],
    ['invalid event id', { eventId: '' }, 'invalid-event-id'],
    ['invalid occurrence date', { occurredAt: 'later' }, 'invalid-date'],
  ] as const)('rejects %s', (_label, overrides, error) => {
    expect(
      parseLiveTranscriptionStatePacket(JSON.stringify(packet(overrides)))
    ).toEqual({ ok: false, error })
  })

  it('accepts only reliable packets attributed to the backend', () => {
    expect(
      isReliableBackendStatePacket(undefined, DataPacket_Kind.RELIABLE)
    ).toBe(true)
    expect(isReliableBackendStatePacket(undefined, DataPacket_Kind.LOSSY)).toBe(
      false
    )
    expect(
      isReliableBackendStatePacket(
        { identity: 'agent' },
        DataPacket_Kind.RELIABLE
      )
    ).toBe(false)
  })

  it('validates the REST snapshot used for resynchronization', () => {
    expect(
      parseLiveTranscriptionStateSnapshot({
        state: 'live',
        stateVersion: 2,
        sessionId: 'session-1',
        updatedAt: '2026-09-29T00:00:00.000Z',
        reason: null,
        desired: 'ON',
        roomSid: 'RM_room-1',
      })
    ).toMatchObject({
      ok: true,
      snapshot: {
        roomSid: 'RM_room-1',
        state: 'live',
        stateVersion: 2,
      },
    })
  })
})
