import { normalizeNativeCoverage } from './meetingNativeCoverage'
import type { MeetingNativePartial } from './types'

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const hasFields = (value: Record<string, unknown>, fields: string[]) =>
  Object.keys(value).length === fields.length &&
  fields.every((field) => Object.hasOwn(value, field))

// Bounds inherited from nativeTranscriptSegmentSchema and native epoch names.
// Platform prefixes the 160-character segment ID with the epoch UUID and '_'.
const MAX_SEGMENT_ID_LENGTH = 36 + 1 + 160
const MAX_SPEAKER_LENGTH = 160
const MAX_TEXT_LENGTH = 4000
const MAX_SEGMENTS = 1000

type NativeSegment = MeetingNativePartial['segments'][number]

const normalizeSegment = (
  value: unknown,
  availableEpochs: Set<string>
): NativeSegment | undefined => {
  if (
    !isRecord(value) ||
    !hasFields(value, ['id', 'start_ms', 'speaker', 'text', 'source_epoch']) ||
    typeof value.id !== 'string' ||
    value.id.length === 0 ||
    value.id.length > MAX_SEGMENT_ID_LENGTH ||
    typeof value.start_ms !== 'number' ||
    !Number.isSafeInteger(value.start_ms) ||
    value.start_ms < 0 ||
    typeof value.speaker !== 'string' ||
    value.speaker.length > MAX_SPEAKER_LENGTH ||
    typeof value.text !== 'string' ||
    value.text.length === 0 ||
    value.text.length > MAX_TEXT_LENGTH ||
    !isRecord(value.source_epoch) ||
    !hasFields(value.source_epoch, ['epoch_ref', 'time_basis']) ||
    typeof value.source_epoch.epoch_ref !== 'string' ||
    !availableEpochs.has(value.source_epoch.epoch_ref) ||
    value.source_epoch.time_basis !== 'source_relative_ms'
  )
    return undefined

  return {
    id: value.id,
    startMs: value.start_ms,
    speaker: value.speaker,
    text: value.text,
    sourceEpoch: {
      epochRef: value.source_epoch.epoch_ref,
      timeBasis: 'source_relative_ms',
    },
  }
}

/** Accept the whole bounded consultation or withdraw it; never repair its text. */
export const normalizeNativePartial = (
  value: unknown,
  rawCoverage: unknown,
  meetingRef: string
): MeetingNativePartial | undefined => {
  const coverage = normalizeNativeCoverage(rawCoverage, meetingRef)
  if (
    !coverage ||
    coverage.epochsUnavailableReason !== undefined ||
    !isRecord(rawCoverage) ||
    !isRecord(value) ||
    !hasFields(value, [
      'version',
      'meeting_ref',
      'snapshot_digest',
      'coverage',
      'segments',
      'truncated',
    ]) ||
    value.version !== 1 ||
    value.meeting_ref !== meetingRef ||
    value.snapshot_digest !== rawCoverage.snapshot_digest ||
    typeof value.snapshot_digest !== 'string' ||
    value.coverage !== 'partial_sources' ||
    typeof value.truncated !== 'boolean' ||
    !Array.isArray(value.segments) ||
    value.segments.length === 0 ||
    value.segments.length > MAX_SEGMENTS
  )
    return undefined

  const availableEpochs = new Set(
    coverage.epochs
      .filter((epoch) => epoch.state === 'available')
      .map((epoch) => epoch.epochRef)
  )
  const segments: NativeSegment[] = []
  const identities = new Set<string>()
  for (const raw of value.segments) {
    const segment = normalizeSegment(raw, availableEpochs)
    if (!segment) return undefined
    const identity = `${segment.sourceEpoch.epochRef}:${segment.id}`
    if (identities.has(identity)) return undefined
    identities.add(identity)
    segments.push(segment)
  }
  return {
    version: 1,
    meetingRef,
    snapshotDigest: value.snapshot_digest,
    coverage: 'partial_sources',
    segments,
    truncated: value.truncated,
  }
}
