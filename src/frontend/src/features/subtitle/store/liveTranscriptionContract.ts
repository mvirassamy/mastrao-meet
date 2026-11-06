import {
  LIVE_TRANSCRIPTION_SCHEMA_VERSION,
  type LiveTranscriptionGap,
  type LiveTranscriptionSegmentInput,
  type LiveTranscriptionTextStreamReader,
  type LiveTranscriptionTransportEvent,
} from './liveTranscriptionTypes'

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
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    return undefined
  }
  return value
}

const hasOwn = (record: Record<string, unknown>, key: string) =>
  Object.prototype.hasOwnProperty.call(record, key)

const invalidOptionalInteger = (
  record: Record<string, unknown>,
  key: string,
  value: number | undefined
) => hasOwn(record, key) && value === undefined

const parseJson = (value: string): unknown => {
  try {
    return JSON.parse(value) as unknown
  } catch {
    return value
  }
}

const toSegment = (value: unknown): LiveTranscriptionSegmentInput | null => {
  const record = asRecord(value)
  if (!record) return null
  const text = asNonEmptyString(record.text)
  const participantIdentity = asNonEmptyString(record.participantIdentity)
  const trackSid = asNonEmptyString(record.trackSid)
  const legId = asNonEmptyString(record.legId)
  const itemId = asNonEmptyString(record.itemId)
  const sequence = asNonNegativeInteger(record.sequence)
  const revision = asNonNegativeInteger(record.revision)
  const startTime = asNonNegativeInteger(record.startTime)
  const endTime = asNonNegativeInteger(record.endTime)
  const receivedAt = asNonNegativeInteger(record.receivedAt)
  const language = asNonEmptyString(record.language)
  const state = record.state
  if (
    !text ||
    !participantIdentity ||
    !trackSid ||
    !legId ||
    !itemId ||
    sequence === undefined ||
    revision === undefined ||
    receivedAt === undefined ||
    invalidOptionalInteger(record, 'startTime', startTime) ||
    invalidOptionalInteger(record, 'endTime', endTime) ||
    (hasOwn(record, 'language') && language === undefined) ||
    (state !== 'final' && state !== 'interim')
  ) {
    return null
  }

  return {
    participantIdentity,
    trackSid,
    legId,
    itemId,
    state,
    text,
    sequence,
    revision,
    language,
    startTime,
    endTime,
    receivedAt,
    metadataSource: 'envelope',
  }
}

const parseGap = (
  value: Record<string, unknown>
): LiveTranscriptionGap | null => {
  const id = asNonEmptyString(value.gapId)
  const reason = asNonEmptyString(value.reason)
  const fromSequence = asNonNegativeInteger(value.fromSequence)
  const toSequence = asNonNegativeInteger(value.toSequence)
  const receivedAt = asNonNegativeInteger(value.receivedAt)
  if (
    !id ||
    !reason ||
    receivedAt === undefined ||
    invalidOptionalInteger(value, 'fromSequence', fromSequence) ||
    invalidOptionalInteger(value, 'toSequence', toSequence)
  ) {
    return null
  }
  return {
    id,
    reason,
    fromSequence,
    toSequence,
    receivedAt,
  }
}

const parsePayload = (payload: unknown): LiveTranscriptionTransportEvent[] => {
  const record = asRecord(payload)
  if (!record) return []

  const schemaVersion = asNonNegativeInteger(record.schemaVersion)
  if (schemaVersion !== LIVE_TRANSCRIPTION_SCHEMA_VERSION) {
    return []
  }

  if (hasOwn(record, 'segments')) {
    if (!Array.isArray(record.segments)) return []
    const segments = record.segments.map(toSegment)
    if (segments.length === 0 || segments.some((segment) => segment === null)) {
      return []
    }
    return [
      {
        type: 'segments',
        segments: segments as LiveTranscriptionSegmentInput[],
      },
    ]
  }

  const segment = toSegment(record)
  return segment ? [{ type: 'segments', segments: [segment] }] : []
}

/**
 * The product accepts only the complete versioned Mastrao envelope. Generic
 * LiveKit attributes are transport metadata, never identity authority.
 */
export const parseLiveTranscriptionStream = (
  payload: string
): LiveTranscriptionTransportEvent[] => {
  const parsed = parseJson(payload)
  const events = parsePayload(parsed)
  return events.length > 0 ? events : []
}

export const readLiveTranscriptionStream = async (
  reader: LiveTranscriptionTextStreamReader,
  senderIdentity: string,
  expectedSenderIdentity: string
): Promise<LiveTranscriptionTransportEvent[]> => {
  if (senderIdentity !== expectedSenderIdentity) return []
  return parseLiveTranscriptionStream(await reader.readAll())
}

export const roomAudioLiveWorkerIdentity = async (
  roomId: string
): Promise<string> => {
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(roomId)
  )
  const hex = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, '0')
  ).join('')
  return `room-audio-worker-live-${hex.slice(0, 32)}`
}

export const readLiveTranscriptionGapStream = async (
  reader: LiveTranscriptionTextStreamReader,
  senderIdentity: string,
  expectedSenderIdentity: string
): Promise<LiveTranscriptionTransportEvent[]> => {
  if (senderIdentity !== expectedSenderIdentity) return []
  return parseLiveTranscriptionGapStream(await reader.readAll())
}

/**
 * Gap markers travel on their own versioned topic so they never mix with
 * transcript text. Anything else than a versioned gap object is ignored.
 */
export const parseLiveTranscriptionGapStream = (
  payload: string
): LiveTranscriptionTransportEvent[] => {
  const record = asRecord(parseJson(payload))
  if (
    !record ||
    asNonNegativeInteger(record.schemaVersion) !==
      LIVE_TRANSCRIPTION_SCHEMA_VERSION
  ) {
    return []
  }
  const gap = parseGap(record)
  return gap ? [{ type: 'gap', gap }] : []
}
