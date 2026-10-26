import { normalizeNativeCaptureDomain } from './meetingNativeCaptureDomain'
import type { MeetingNativeCoverage } from './types'

const MAX_EPOCHS = 32
const MAX_TRACKS = 128
const REFERENCE = /^[A-Za-z0-9_-]{16,160}$/u
const DIGEST = /^[a-f0-9]{64}$/u
// Match z.uuid() in the shared contract, including its nil and max UUIDs.
const UUID =
  /^(?:[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[1-8][a-fA-F0-9]{3}-[89abAB][a-fA-F0-9]{3}-[a-fA-F0-9]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$/u

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const isUuid = (value: unknown): value is string =>
  typeof value === 'string' && UUID.test(value)

const hasFields = (
  value: Record<string, unknown>,
  required: string[],
  optional: string[] = []
) =>
  required.every((field) => Object.hasOwn(value, field)) &&
  Object.keys(value).every(
    (field) => required.includes(field) || optional.includes(field)
  )

type NativeEpoch = MeetingNativeCoverage['epochs'][number]
type NativeTrack = MeetingNativeCoverage['tracks'][number]

const normalizeEpoch = (value: unknown): NativeEpoch | null => {
  if (
    !isRecord(value) ||
    !hasFields(value, ['epoch_ref', 'state']) ||
    !isUuid(value.epoch_ref)
  )
    return null
  switch (value.state) {
    case 'pending':
    case 'failed':
    case 'available':
      return { epochRef: value.epoch_ref, state: value.state }
    default:
      return null
  }
}

const normalizeTrack = (
  value: unknown,
  knownEpochs: Set<string>
): NativeTrack | null => {
  if (
    !isRecord(value) ||
    !hasFields(value, ['observation_ref', 'capture_state'], ['epoch_refs']) ||
    !isUuid(value.observation_ref) ||
    (value.capture_state !== 'admitted' && value.capture_state !== 'unadmitted')
  )
    return null

  const track: NativeTrack = {
    observationRef: value.observation_ref,
    captureState: value.capture_state,
  }
  if (value.epoch_refs === undefined) return track
  if (
    !Array.isArray(value.epoch_refs) ||
    value.epoch_refs.length > MAX_EPOCHS ||
    (value.capture_state === 'unadmitted' && value.epoch_refs.length > 0)
  )
    return null
  const epochRefs: string[] = []
  const identities = new Set<string>()
  for (const reference of value.epoch_refs) {
    if (
      !isUuid(reference) ||
      !knownEpochs.has(reference) ||
      identities.has(reference)
    )
      return null
    identities.add(reference)
    epochRefs.push(reference)
  }
  return { ...track, epochRefs }
}

/** Consultation facts do not establish complete coverage or canonical readiness. */
export const normalizeNativeCoverage = (
  value: unknown,
  meetingRef: string
): MeetingNativeCoverage | undefined => {
  if (
    !isRecord(value) ||
    !hasFields(
      value,
      ['version', 'meeting_ref', 'snapshot_digest', 'epochs'],
      ['track_observations', 'capture_domain', 'epochs_unavailable_reason']
    ) ||
    value.version !== 1 ||
    typeof value.meeting_ref !== 'string' ||
    !REFERENCE.test(value.meeting_ref) ||
    value.meeting_ref !== meetingRef ||
    typeof value.snapshot_digest !== 'string' ||
    !DIGEST.test(value.snapshot_digest) ||
    !Array.isArray(value.epochs) ||
    value.epochs.length > MAX_EPOCHS
  )
    return undefined

  const epochsUnavailableReason = value.epochs_unavailable_reason
  if (
    (Object.hasOwn(value, 'epochs_unavailable_reason') &&
      epochsUnavailableReason === undefined) ||
    (epochsUnavailableReason !== undefined &&
      epochsUnavailableReason !== 'epoch_limit_exceeded' &&
      epochsUnavailableReason !== 'result_bytes_limit_exceeded')
  )
    return undefined
  if (epochsUnavailableReason && value.epochs.length > 0) return undefined

  const captureDomain = normalizeNativeCaptureDomain(value.capture_domain)
  if (
    value.capture_domain !== undefined &&
    value.capture_domain !== null &&
    !captureDomain
  )
    return undefined
  const metadata: Pick<
    MeetingNativeCoverage,
    'captureDomain' | 'epochsUnavailableReason'
  > = {}
  if (captureDomain) metadata.captureDomain = captureDomain
  if (epochsUnavailableReason)
    metadata.epochsUnavailableReason = epochsUnavailableReason

  const epochs: NativeEpoch[] = []
  const knownEpochs = new Set<string>()
  for (const raw of value.epochs) {
    const epoch = normalizeEpoch(raw)
    if (!epoch || knownEpochs.has(epoch.epochRef)) return undefined
    knownEpochs.add(epoch.epochRef)
    epochs.push(epoch)
  }

  const observations = value.track_observations
  if (observations === undefined) {
    return {
      ...metadata,
      meetingCoverage: 'unverified',
      observationsAvailable: false,
      truncated: false,
      tracks: [],
      epochs,
    }
  }
  if (
    !isRecord(observations) ||
    !hasFields(observations, ['meeting_coverage', 'truncated', 'tracks']) ||
    observations.meeting_coverage !== 'unverified' ||
    typeof observations.truncated !== 'boolean' ||
    !Array.isArray(observations.tracks) ||
    observations.tracks.length > MAX_TRACKS
  )
    return undefined

  if (epochsUnavailableReason && observations.tracks.length > 0)
    return undefined

  const tracks: NativeTrack[] = []
  const identities = new Set<string>()
  for (const raw of observations.tracks) {
    const track = normalizeTrack(raw, knownEpochs)
    if (!track || identities.has(track.observationRef)) return undefined
    identities.add(track.observationRef)
    tracks.push(track)
  }
  return {
    ...metadata,
    meetingCoverage: 'unverified',
    observationsAvailable: true,
    truncated: observations.truncated,
    tracks,
    epochs,
  }
}
