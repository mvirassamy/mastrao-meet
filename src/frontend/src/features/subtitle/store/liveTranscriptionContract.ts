import type {
  Participant,
  TrackPublication,
  TranscriptionSegment,
} from 'livekit-client'
import {
  LIVE_TRANSCRIPTION_SCHEMA_VERSION,
  type LiveTranscriptionGap,
  type LiveTranscriptionSegmentInput,
  type LiveTranscriptionStatus,
  type LiveTranscriptionTextStreamReader,
  type LiveTranscriptionTransportEvent,
} from './liveTranscriptionTypes'

const LEGACY_LEG_ID = 'legacy'
const UNKNOWN_PARTICIPANT_IDENTITY = 'unknown-participant'
const UNKNOWN_TRACK_SID = 'unknown-track'

const ATTRIBUTE_NAMES = {
  participantIdentity: 'mastrao.participant_identity',
  trackSid: 'lk.transcribed_track_id',
  legId: 'mastrao.leg_id',
  itemId: 'lk.segment_id',
  final: 'lk.transcription_final',
  sequence: 'mastrao.sequence',
  revision: 'mastrao.revision',
} as const

const asRecord = (value: unknown): Record<string, unknown> | null => {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null
  }
  return value as Record<string, unknown>
}

const asNonEmptyString = (value: unknown): string | undefined => {
  if (typeof value !== 'string' || value.trim() === '') return undefined
  return value
}

const asNonNegativeInteger = (value: unknown): number | undefined => {
  const numberValue = typeof value === 'number' ? value : Number(value)
  if (!Number.isInteger(numberValue) || numberValue < 0) return undefined
  return numberValue
}

const asReceivedAt = (value: unknown): number => {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Date.parse(value)
    if (!Number.isNaN(parsed)) return parsed
  }
  return Date.now()
}

const asState = (
  value: unknown,
  finalValue: unknown
): LiveTranscriptionSegmentInput['state'] => {
  if (value === 'final' || value === 'interim') return value
  return finalValue === true || finalValue === 'true' ? 'final' : 'interim'
}

const parseJson = (value: string): unknown => {
  try {
    return JSON.parse(value) as unknown
  } catch {
    return value
  }
}

const readField = (
  record: Record<string, unknown>,
  attributes: Record<string, string>,
  attributeName: string,
  fieldName: string
) => record[fieldName] ?? record[attributeName] ?? attributes[attributeName]

const toSegment = (
  value: unknown,
  attributes: Record<string, string>,
  senderIdentity: string,
  streamId: string,
  metadataSource: LiveTranscriptionSegmentInput['metadataSource']
): LiveTranscriptionSegmentInput | null => {
  const record = asRecord(value) ?? {}
  const text = asNonEmptyString(record.text)
  if (text === undefined) return null

  const participantIdentity =
    asNonEmptyString(
      readField(
        record,
        attributes,
        ATTRIBUTE_NAMES.participantIdentity,
        'participantIdentity'
      )
    ) ?? senderIdentity
  const itemId =
    asNonEmptyString(
      readField(record, attributes, ATTRIBUTE_NAMES.itemId, 'itemId')
    ) ?? streamId
  if (!participantIdentity || !itemId) return null

  return {
    participantIdentity: participantIdentity || UNKNOWN_PARTICIPANT_IDENTITY,
    trackSid:
      asNonEmptyString(
        readField(record, attributes, ATTRIBUTE_NAMES.trackSid, 'trackSid')
      ) ?? UNKNOWN_TRACK_SID,
    legId:
      asNonEmptyString(
        readField(record, attributes, ATTRIBUTE_NAMES.legId, 'legId')
      ) ?? LEGACY_LEG_ID,
    itemId,
    state: asState(
      record.state,
      record.final ?? attributes[ATTRIBUTE_NAMES.final]
    ),
    text,
    sequence: asNonNegativeInteger(
      readField(record, attributes, ATTRIBUTE_NAMES.sequence, 'sequence')
    ),
    revision: asNonNegativeInteger(
      readField(record, attributes, ATTRIBUTE_NAMES.revision, 'revision')
    ),
    language: asNonEmptyString(record.language),
    startTime: asNonNegativeInteger(record.startTime),
    endTime: asNonNegativeInteger(record.endTime),
    receivedAt: asReceivedAt(record.receivedAt),
    metadataSource,
  }
}

const parseStatus = (value: Record<string, unknown>) => {
  const status = value.status
  if (
    status === 'inactive' ||
    status === 'starting' ||
    status === 'live' ||
    status === 'reconnecting' ||
    status === 'degraded' ||
    status === 'unavailable' ||
    status === 'stopped'
  ) {
    return status satisfies LiveTranscriptionStatus
  }
  return null
}

const parseGap = (
  value: Record<string, unknown>
): LiveTranscriptionGap | null => {
  const id = asNonEmptyString(value.id)
  const reason = asNonEmptyString(value.reason)
  if (!id || !reason) return null
  return {
    id,
    reason,
    fromSequence: asNonNegativeInteger(value.fromSequence),
    toSequence: asNonNegativeInteger(value.toSequence),
    receivedAt: asReceivedAt(value.receivedAt),
  }
}

const parsePayload = (
  payload: unknown,
  attributes: Record<string, string>,
  senderIdentity: string,
  streamId: string
): LiveTranscriptionTransportEvent[] => {
  if (Array.isArray(payload)) {
    return [
      {
        type: 'segments',
        segments: payload
          .map((value) =>
            toSegment(value, attributes, senderIdentity, streamId, 'envelope')
          )
          .filter(
            (segment): segment is LiveTranscriptionSegmentInput =>
              segment !== null
          ),
      },
    ]
  }

  const record = asRecord(payload)
  if (!record) {
    const segment = toSegment(
      { text: payload },
      attributes,
      senderIdentity,
      streamId,
      'livekit-attributes'
    )
    return segment ? [{ type: 'segments', segments: [segment] }] : []
  }

  const schemaVersion = asNonNegativeInteger(record.schemaVersion)
  if (
    schemaVersion !== undefined &&
    schemaVersion !== LIVE_TRANSCRIPTION_SCHEMA_VERSION
  ) {
    return []
  }

  if (record.type === 'status') {
    const status = parseStatus(record)
    return status ? [{ type: 'status', status }] : []
  }

  if (record.type === 'gap') {
    const gap = parseGap(record)
    return gap ? [{ type: 'gap', gap }] : []
  }

  if (Array.isArray(record.segments)) {
    return [
      {
        type: 'segments',
        segments: record.segments
          .map((value) =>
            toSegment(value, attributes, senderIdentity, streamId, 'envelope')
          )
          .filter(
            (segment): segment is LiveTranscriptionSegmentInput =>
              segment !== null
          ),
      },
    ]
  }

  const segment = toSegment(
    record,
    attributes,
    senderIdentity,
    streamId,
    'envelope'
  )
  return segment ? [{ type: 'segments', segments: [segment] }] : []
}

/**
 * livekit-client 2.20.0 exposes lk.transcription through the generic text
 * stream API, not a typed transcription-stream helper. The Mastrao envelope
 * carries fields absent from the SDK segment model. Standard lk.* attributes
 * are used as a fallback; the legacy RoomEvent path remains a compatibility
 * bridge until publishers migrate fully to the envelope.
 */
export const parseLiveTranscriptionStream = (
  payload: string,
  attributes: Record<string, string> = {},
  senderIdentity = 'unknown-sender',
  streamId = 'unknown-stream'
): LiveTranscriptionTransportEvent[] => {
  const parsed = parseJson(payload)
  const events = parsePayload(parsed, attributes, senderIdentity, streamId)
  return events.length > 0 ? events : []
}

export const readLiveTranscriptionStream = async (
  reader: LiveTranscriptionTextStreamReader,
  senderIdentity: string
): Promise<LiveTranscriptionTransportEvent[]> =>
  parseLiveTranscriptionStream(
    await reader.readAll(),
    reader.info.attributes,
    senderIdentity,
    reader.info.id
  )

export const toLegacyTranscriptionEvent = (
  segments: TranscriptionSegment[],
  participant?: Participant,
  publication?: TrackPublication
): LiveTranscriptionTransportEvent => ({
  type: 'segments',
  segments: participant
    ? segments.map((segment) => ({
        participantIdentity:
          participant.identity || UNKNOWN_PARTICIPANT_IDENTITY,
        trackSid: publication?.trackSid || UNKNOWN_TRACK_SID,
        legId: LEGACY_LEG_ID,
        itemId: segment.id,
        state: segment.final ? 'final' : 'interim',
        text: segment.text,
        language: segment.language,
        startTime: segment.startTime,
        endTime: segment.endTime,
        receivedAt: segment.lastReceivedTime,
        metadataSource: 'legacy-event',
      }))
    : [],
})
