import { DataPacket_Kind } from 'livekit-client'
import {
  LIVE_TRANSCRIPTION_SCHEMA_VERSION,
  type LiveTranscriptionStatePacket,
  type LiveTranscriptionStateSnapshot,
  type LiveTranscriptionStatus,
} from './liveTranscriptionTypes'

const STATE_PACKET_KEYS = new Set([
  'schemaVersion',
  'roomSid',
  'state',
  'stateVersion',
  'eventId',
  'occurredAt',
  'sessionId',
  'reason',
])

const SNAPSHOT_KEYS = new Set([
  'state',
  'stateVersion',
  'sessionId',
  'updatedAt',
  'reason',
  'desired',
  'roomSid',
])

const PUBLIC_STATES: readonly LiveTranscriptionStatus[] = [
  'unknown',
  'inactive',
  'starting',
  'live',
  'reconnecting',
  'degraded',
  'unavailable',
  'stopping',
  'stopped',
]

type RecordValue = Record<string, unknown>

export type StateContractError =
  | 'invalid-json'
  | 'invalid-shape'
  | 'unknown-field'
  | 'invalid-version'
  | 'invalid-state'
  | 'invalid-room-sid'
  | 'invalid-state-version'
  | 'invalid-event-id'
  | 'invalid-date'
  | 'invalid-optional-field'

export type StatePacketParseResult =
  | { ok: true; packet: LiveTranscriptionStatePacket }
  | { ok: false; error: StateContractError }

export type StateSnapshotParseResult =
  | { ok: true; snapshot: LiveTranscriptionStateSnapshot }
  | { ok: false; error: StateContractError }

const isRecord = (value: unknown): value is RecordValue =>
  typeof value === 'object' &&
  value !== null &&
  !Array.isArray(value) &&
  !ArrayBuffer.isView(value)

const parseJson = (payload: string): unknown => {
  try {
    return JSON.parse(payload) as unknown
  } catch {
    return undefined
  }
}

const hasOnlyAllowedKeys = (
  record: RecordValue,
  allowedKeys: ReadonlySet<string>
) => Object.keys(record).every((key) => allowedKeys.has(key))

const parseStatus = (value: unknown): LiveTranscriptionStatus | undefined =>
  typeof value === 'string' &&
  PUBLIC_STATES.includes(value as LiveTranscriptionStatus)
    ? (value as LiveTranscriptionStatus)
    : undefined

const parseNonEmptyString = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() !== '' ? value : undefined

const parseStateVersion = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0
    ? value
    : undefined

const parseDate = (value: unknown): string | undefined =>
  typeof value === 'string' && !Number.isNaN(Date.parse(value))
    ? value
    : undefined

const parseOptionalString = (
  record: RecordValue,
  key: string
): string | null | undefined | typeof INVALID_OPTIONAL_FIELD => {
  if (!(key in record)) return undefined
  if (record[key] === null) return null
  const value = parseNonEmptyString(record[key])
  return value ?? INVALID_OPTIONAL_FIELD
}

const INVALID_OPTIONAL_FIELD = Symbol('invalid-optional-field')

const parseStatePacketRecord = (
  record: RecordValue
): StatePacketParseResult => {
  if (!hasOnlyAllowedKeys(record, STATE_PACKET_KEYS)) {
    return { ok: false, error: 'unknown-field' }
  }
  if (record.schemaVersion !== LIVE_TRANSCRIPTION_SCHEMA_VERSION) {
    return { ok: false, error: 'invalid-version' }
  }

  const state = parseStatus(record.state)
  const roomSid = parseNonEmptyString(record.roomSid)
  const stateVersion = parseStateVersion(record.stateVersion)
  const eventId = parseNonEmptyString(record.eventId)
  const occurredAt = parseDate(record.occurredAt)
  if (!state) return { ok: false, error: 'invalid-state' }
  if (!roomSid) return { ok: false, error: 'invalid-room-sid' }
  if (stateVersion === undefined) {
    return { ok: false, error: 'invalid-state-version' }
  }
  if (!eventId) return { ok: false, error: 'invalid-event-id' }
  if (!occurredAt) return { ok: false, error: 'invalid-date' }

  const sessionId = parseOptionalString(record, 'sessionId')
  const reason = parseOptionalString(record, 'reason')
  if (
    sessionId === INVALID_OPTIONAL_FIELD ||
    reason === INVALID_OPTIONAL_FIELD
  ) {
    return { ok: false, error: 'invalid-optional-field' }
  }

  return {
    ok: true,
    packet: {
      schemaVersion: LIVE_TRANSCRIPTION_SCHEMA_VERSION,
      roomSid,
      state,
      stateVersion,
      eventId,
      occurredAt,
      ...(sessionId !== undefined ? { sessionId } : {}),
      ...(reason !== undefined ? { reason } : {}),
    },
  }
}

export const parseLiveTranscriptionStatePacket = (
  payload: Uint8Array | string
): StatePacketParseResult => {
  const record = isRecord(payload)
    ? payload
    : typeof payload === 'string'
      ? parseJson(payload)
      : parseJson(new TextDecoder().decode(payload))
  if (!isRecord(record)) return { ok: false, error: 'invalid-json' }
  return parseStatePacketRecord(record)
}

export const parseLiveTranscriptionStateSnapshot = (
  value: unknown
): StateSnapshotParseResult => {
  if (!isRecord(value)) return { ok: false, error: 'invalid-shape' }
  if (!hasOnlyAllowedKeys(value, SNAPSHOT_KEYS)) {
    return { ok: false, error: 'unknown-field' }
  }

  const state = parseStatus(value.state)
  const stateVersion = parseStateVersion(value.stateVersion)
  const roomSid =
    value.roomSid === null ? null : parseNonEmptyString(value.roomSid)
  const sessionId =
    value.sessionId === null ? null : parseNonEmptyString(value.sessionId)
  const occurredAt =
    value.updatedAt === null ? null : parseDate(value.updatedAt)
  const reason =
    value.reason === null ? null : parseNonEmptyString(value.reason)
  const desired = value.desired

  if (!state) return { ok: false, error: 'invalid-state' }
  if (stateVersion === undefined) {
    return { ok: false, error: 'invalid-state-version' }
  }
  if (value.roomSid !== null && !roomSid) {
    return { ok: false, error: 'invalid-room-sid' }
  }
  if (value.sessionId !== null && !sessionId) {
    return { ok: false, error: 'invalid-optional-field' }
  }
  if (value.updatedAt !== null && !occurredAt) {
    return { ok: false, error: 'invalid-date' }
  }
  if (value.reason !== null && !reason) {
    return { ok: false, error: 'invalid-optional-field' }
  }
  if (desired !== undefined && desired !== 'ON' && desired !== 'OFF') {
    return { ok: false, error: 'invalid-optional-field' }
  }

  return {
    ok: true,
    snapshot: {
      roomSid: roomSid ?? null,
      state,
      stateVersion,
      sessionId: sessionId ?? null,
      occurredAt: occurredAt ?? null,
      reason: reason ?? null,
    },
  }
}

/**
 * A server-published LiveKit data packet has no remote participant. Requiring
 * RELIABLE plus that server-only shape prevents agent/user packets from
 * becoming the frontend's collective state authority.
 */
export const isReliableBackendStatePacket = (
  participant: unknown,
  kind: DataPacket_Kind | undefined
) => kind === DataPacket_Kind.RELIABLE && participant === undefined
