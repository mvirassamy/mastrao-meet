import type { MeetingLiveEvent, MeetingLiveSource } from './types'
import type { LiveFullSnapshot } from './meetingLiveFull'
import {
  normalizeLiveObservedDomain,
  type MeetingLiveObservedDomain,
} from './meetingLiveObservedDomain'

const MAX_SOURCES = 32
const LIMITS = {
  preview: { events: 1000, bytes: 512 * 1024 },
  retained: { events: 8192, bytes: 2 * 1024 * 1024 },
}
const MAX_SEQUENCE = 100_000
const REFERENCE = /^[A-Za-z0-9_-]{16,160}$/u
const ITEM_REFERENCE = /^[A-Za-z0-9_-]{1,160}$/u
const LANGUAGE = /^[a-z]{2}(?:-[A-Z]{2})?$/u

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const isReference = (value: unknown): value is string =>
  typeof value === 'string' && REFERENCE.test(value)

const isInteger = (
  value: unknown,
  minimum: number,
  maximum = Number.MAX_SAFE_INTEGER
): value is number =>
  typeof value === 'number' &&
  Number.isSafeInteger(value) &&
  value >= minimum &&
  value <= maximum

const hasFields = (value: Record<string, unknown>, fields: string[]) =>
  Object.keys(value).length === fields.length &&
  fields.every((field) => Object.hasOwn(value, field))

type LiveFinal = Extract<MeetingLiveEvent, { kind: 'final' }>

const normalizeTiming = (value: unknown): LiveFinal['timing'] | null => {
  if (!isRecord(value)) return null
  if (value.state === 'unavailable' && hasFields(value, ['state'])) {
    return { state: 'unavailable' }
  }
  if (
    value.state !== 'source_buffer' ||
    !hasFields(value, ['state', 'sample_rate', 'start_sample', 'end_sample']) ||
    value.sample_rate !== 24000 ||
    !isInteger(value.start_sample, 0) ||
    !isInteger(value.end_sample, 1) ||
    value.end_sample <= value.start_sample ||
    value.end_sample - value.start_sample > 540 * 24000
  )
    return null
  return {
    state: 'source_buffer',
    sample_rate: 24000,
    start_sample: value.start_sample,
    end_sample: value.end_sample,
  }
}

const normalizeEvent = (
  value: unknown,
  sourceRef: string,
  confirmedSequence: number
): MeetingLiveEvent | null => {
  if (
    !isRecord(value) ||
    value.version !== 2 ||
    value.source_ref !== sourceRef ||
    !isReference(value.leg_ref) ||
    !isInteger(value.sequence, 1, confirmedSequence)
  )
    return null
  const common = {
    version: 2 as const,
    source_ref: sourceRef,
    leg_ref: value.leg_ref,
    sequence: value.sequence,
  }
  const fields = ['version', 'source_ref', 'leg_ref', 'sequence', 'kind']
  switch (value.kind) {
    case 'final': {
      if (
        !hasFields(value, [
          ...fields,
          'item_ref',
          'revision',
          'text',
          'language',
          'timing',
        ]) ||
        typeof value.item_ref !== 'string' ||
        !ITEM_REFERENCE.test(value.item_ref) ||
        !isInteger(value.revision, 1) ||
        typeof value.text !== 'string' ||
        value.text.trim() === '' ||
        value.text.length > 4000 ||
        !(
          value.language === null ||
          (typeof value.language === 'string' && LANGUAGE.test(value.language))
        )
      )
        return null
      const timing = normalizeTiming(value.timing)
      if (!timing) return null
      return {
        ...common,
        kind: 'final',
        item_ref: value.item_ref,
        revision: value.revision,
        text: value.text,
        language: value.language,
        timing,
      }
    }
    case 'gap':
      if (!hasFields(value, [...fields, 'reason'])) return null
      switch (value.reason) {
        case 'connection_lost':
        case 'provider_failed':
        case 'delivery_failed':
        case 'timing_unavailable':
        case 'capture_incomplete':
          return { ...common, kind: 'gap', reason: value.reason }
        default:
          return null
      }
    case 'end':
      if (
        !hasFields(value, [...fields, 'reason', 'final_event_count']) ||
        !isInteger(value.final_event_count, 0, value.sequence - 1)
      )
        return null
      switch (value.reason) {
        case 'track_unpublished':
        case 'meeting_ended':
        case 'provider_failed':
        case 'capture_incomplete':
          return {
            ...common,
            kind: 'end',
            reason: value.reason,
            final_event_count: value.final_event_count,
          }
        default:
          return null
      }
    default:
      return null
  }
}

const normalizeSource = (value: unknown): MeetingLiveSource | null => {
  if (
    !isRecord(value) ||
    !hasFields(value, [
      'source_ref',
      'participant_kind',
      'participant_ref',
      'state',
      'incomplete',
      'confirmed_sequence',
      'events',
    ]) ||
    !isReference(value.source_ref) ||
    !isReference(value.participant_ref) ||
    (value.participant_kind !== 'host' && value.participant_kind !== 'guest') ||
    (value.state !== 'collecting' && value.state !== 'ended') ||
    typeof value.incomplete !== 'boolean' ||
    !isInteger(value.confirmed_sequence, 0, MAX_SEQUENCE) ||
    !Array.isArray(value.events)
  )
    return null

  const events: MeetingLiveEvent[] = []
  const sequences = new Set<number>()
  const items = new Set<string>()
  for (const raw of value.events) {
    const event = normalizeEvent(
      raw,
      value.source_ref,
      value.confirmed_sequence
    )
    if (!event) return null
    if (sequences.has(event.sequence)) return null
    sequences.add(event.sequence)
    if (event.kind === 'final') {
      const item = `${event.leg_ref}:${event.item_ref}`
      // The reader already selected the latest correction for each leg/item.
      if (items.has(item)) return null
      items.add(item)
    }
    events.push(event)
  }
  return {
    sourceRef: value.source_ref,
    participantKind: value.participant_kind,
    participantRef: value.participant_ref,
    state: value.state,
    incomplete: value.incomplete,
    confirmedSequence: value.confirmed_sequence,
    events,
  }
}

/** Reject an ambiguous snapshot rather than silently hiding its loss markers. */
export const normalizeLiveTranscriptValue = (
  value: unknown,
  meetingRef: string,
  surface: keyof typeof LIMITS
): {
  liveSources?: MeetingLiveSource[]
  liveTruncated?: boolean
  liveObservedDomain?: MeetingLiveObservedDomain
} => {
  if (
    !isRecord(value) ||
    value.version !== 1 ||
    !isReference(value.meeting_ref) ||
    value.meeting_ref !== meetingRef ||
    !isReference(value.matter_ref) ||
    value.coverage !== 'partial_sources' ||
    value.meeting_coverage !== 'unverified' ||
    typeof value.truncated !== 'boolean' ||
    !Array.isArray(value.sources) ||
    value.sources.length > MAX_SOURCES
  )
    return {}

  let liveObservedDomain: MeetingLiveObservedDomain | undefined
  if (value.observed_domain !== undefined && value.observed_domain !== null) {
    liveObservedDomain = normalizeLiveObservedDomain(value.observed_domain)
    if (!liveObservedDomain) return {}
  }

  let eventCount = 0
  for (const source of value.sources) {
    if (!isRecord(source) || !Array.isArray(source.events)) return {}
    eventCount += source.events.length
    if (eventCount > LIMITS[surface].events) return {}
  }
  if (new Blob([JSON.stringify(value)]).size > LIMITS[surface].bytes) return {}

  const liveSources: MeetingLiveSource[] = []
  const identities = new Set<string>()
  for (const raw of value.sources) {
    const source = normalizeSource(raw)
    if (!source || identities.has(source.sourceRef)) return {}
    identities.add(source.sourceRef)
    liveSources.push(source)
  }
  // Preserve the reader's stable first-sequence order, including corrections.
  return { liveSources, liveTruncated: value.truncated, liveObservedDomain }
}

/** Expansion belongs only to this fresh wire body, never a canonical revision. */
export const normalizeLiveTranscript = (
  response: Record<string, unknown>,
  meetingRef: string
): {
  liveSources?: MeetingLiveSource[]
  liveTruncated?: boolean
  liveFullSnapshot?: LiveFullSnapshot
  liveObservedDomain?: MeetingLiveObservedDomain
} => {
  const value = response.live_transcript
  const preview = normalizeLiveTranscriptValue(value, meetingRef, 'preview')
  if (
    !isRecord(value) ||
    !preview.liveTruncated ||
    !preview.liveSources?.length ||
    typeof value.matter_ref !== 'string' ||
    typeof value.source_heads_digest !== 'string' ||
    !/^[a-f0-9]{64}$/u.test(value.source_heads_digest)
  )
    return preview
  return {
    ...preview,
    liveFullSnapshot: {
      meetingRef,
      matterRef: value.matter_ref,
      sourceHeadsDigest: value.source_heads_digest,
      previewIdentity: JSON.stringify([meetingRef, value]),
    },
  }
}
