const UUID =
  /^(?:[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[1-8][a-fA-F0-9]{3}-[89abAB][a-fA-F0-9]{3}-[a-fA-F0-9]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$/u
const DIGEST = /^[a-f0-9]{64}$/u
const COUNT_FIELDS = [
  'observations',
  'epochs',
  'captures',
  'ended_epochs',
  'refused_epochs',
  'unadmitted_epochs',
  'terminal_captures',
  'failed_captures',
  'aborted_captures',
  'limit_reached_captures',
  'verified_sources',
  'verified_results',
] as const
export const nativeDomainBlockerReasons = [
  'core_observation_omitted',
  'core_capture_omitted',
  'closure_unconfirmed',
  'epoch_not_ended',
  'admission_unresolved',
  'start_unresolved',
  'capture_not_drained',
  'epoch_conflict',
] as const
export const nativeDomainInvalidationReasons = [
  'observation_limit_exceeded',
  'epoch_limit_exceeded',
  'payload_limit_exceeded',
  'inventory_contradiction',
  'late_core_facts',
] as const

type DomainBlockerReason = (typeof nativeDomainBlockerReasons)[number]
type DomainInvalidationReason = (typeof nativeDomainInvalidationReasons)[number]
type DomainCounts = Record<(typeof COUNT_FIELDS)[number], number>
export type MeetingNativeCaptureDomain = {
  version: 1
  scope: 'observed_native_capture_domain'
  domainRef: string
  producerRevision: number
  snapshotDigest: string
  closureState: 'unknown' | 'open' | 'closing' | 'closed'
  reason: DomainInvalidationReason | null
  counts: DomainCounts | null
  blockers: {
    reason: DomainBlockerReason
    epochRef: string | null
    observationRef: string | null
  }[]
}
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const hasFields = (value: Record<string, unknown>, fields: readonly string[]) =>
  Object.keys(value).length === fields.length &&
  fields.every((field) => Object.hasOwn(value, field))
const isUuid = (value: unknown): value is string =>
  typeof value === 'string' && UUID.test(value)
const nullableUuid = (value: unknown): value is string | null =>
  value === null || isUuid(value)
const isChoice = <T extends string>(
  value: unknown,
  choices: readonly T[]
): value is T => typeof value === 'string' && choices.includes(value as T)

const normalizeCounts = (value: unknown): DomainCounts | undefined => {
  if (!isRecord(value) || !hasFields(value, COUNT_FIELDS)) return undefined
  const counts = {} as DomainCounts
  for (const field of COUNT_FIELDS) {
    const number = value[field]
    const limit = field === 'observations' ? 128 : 32
    if (
      typeof number !== 'number' ||
      !Number.isInteger(number) ||
      number < 0 ||
      number > limit
    )
      return undefined
    counts[field] = number
  }
  return counts
}

/** This boundary validates known capture inventory, never meeting completeness. */
export function normalizeNativeCaptureDomain(
  value: unknown
): MeetingNativeCaptureDomain | undefined {
  if (
    !isRecord(value) ||
    !hasFields(value, [
      'version',
      'scope',
      'domain_ref',
      'producer_revision',
      'snapshot_digest',
      'closure_state',
      'reason',
      'counts',
      'blockers',
    ]) ||
    value.version !== 1 ||
    value.scope !== 'observed_native_capture_domain' ||
    !isUuid(value.domain_ref) ||
    typeof value.producer_revision !== 'number' ||
    !Number.isSafeInteger(value.producer_revision) ||
    value.producer_revision <= 0 ||
    typeof value.snapshot_digest !== 'string' ||
    !DIGEST.test(value.snapshot_digest) ||
    !isChoice(value.closure_state, [
      'unknown',
      'open',
      'closing',
      'closed',
    ] as const) ||
    (value.reason !== null &&
      !isChoice(value.reason, nativeDomainInvalidationReasons)) ||
    !Array.isArray(value.blockers) ||
    value.blockers.length > 256
  )
    return undefined
  const counts = value.counts === null ? null : normalizeCounts(value.counts)
  if (counts === undefined) return undefined
  if (value.closure_state === 'unknown') {
    if (value.reason === null || counts !== null) return undefined
  } else if (value.reason !== null || counts === null) return undefined
  if (value.closure_state === 'closed' && value.blockers.length !== 0)
    return undefined
  const blockers: MeetingNativeCaptureDomain['blockers'] = []
  for (const raw of value.blockers) {
    if (
      !isRecord(raw) ||
      !hasFields(raw, ['reason', 'epoch_ref', 'observation_ref']) ||
      !isChoice(raw.reason, nativeDomainBlockerReasons) ||
      !nullableUuid(raw.epoch_ref) ||
      !nullableUuid(raw.observation_ref)
    )
      return undefined
    blockers.push({
      reason: raw.reason,
      epochRef: raw.epoch_ref,
      observationRef: raw.observation_ref,
    })
  }
  return {
    version: 1,
    scope: value.scope,
    domainRef: value.domain_ref,
    producerRevision: value.producer_revision,
    snapshotDigest: value.snapshot_digest,
    closureState: value.closure_state,
    reason: value.reason,
    counts,
    blockers,
  }
}
