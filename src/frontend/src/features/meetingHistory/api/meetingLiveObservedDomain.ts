const UUID =
  /^(?:[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$/u
const DIGEST = /^[a-f0-9]{64}$/u
const COUNT_FIELDS = [
  'observations',
  'microphone_epochs',
  'unclassified_epochs',
  'correlated_epochs',
  'admitted_sources',
  'journal_ended_sources',
  'send_stop_acknowledged_sources',
] as const
const STRUCTURAL_REASONS = [
  'observation_limit_exceeded',
  'epoch_limit_exceeded',
  'payload_limit_exceeded',
  'identity_conflict',
  'correlation_conflict',
  'revision_exhausted',
  'core_source_unrepresented',
  'source_limit_exceeded',
  'leg_limit_exceeded',
  'inventory_contradiction',
  'blocker_limit_exceeded',
] as const
export const liveDomainBlockerReasons = [
  ...STRUCTURAL_REASONS,
  'correlation_unresolved',
  'publication_unobserved',
  'epoch_terminal_unobserved',
  'live_admission_absent',
  'journal_end_unconfirmed',
  'send_stop_unacknowledged',
] as const
export type LiveDomainBlockerReason = (typeof liveDomainBlockerReasons)[number]
type DomainCounts = Record<(typeof COUNT_FIELDS)[number], number>
export type MeetingLiveObservedDomain = {
  version: 1
  scope: 'observed_live_domain'
  domainRef: string
  producerRevision: number
  snapshotDigest: string
  projectionDigest: string
  closureState: 'closed'
  state: 'observed' | 'unknown'
  retentionExpiresAt: number
  blockers: { reason: LiveDomainBlockerReason; epochRef: string | null }[]
  counts: DomainCounts | null
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const hasFields = (value: Record<string, unknown>, fields: readonly string[]) =>
  Object.keys(value).length === fields.length &&
  fields.every((field) => Object.hasOwn(value, field))
const isInteger = (
  value: unknown,
  minimum: number,
  maximum: number
): value is number =>
  typeof value === 'number' &&
  Number.isSafeInteger(value) &&
  !Object.is(value, -0) &&
  value >= minimum &&
  value <= maximum
const isUuid = (value: unknown): value is string =>
  typeof value === 'string' && UUID.test(value)
const isDigest = (value: unknown): value is string =>
  typeof value === 'string' && DIGEST.test(value)

function normalizeCounts(value: unknown): DomainCounts | undefined {
  if (!isRecord(value) || !hasFields(value, COUNT_FIELDS)) return undefined
  const counts = {} as DomainCounts
  for (const field of COUNT_FIELDS) {
    const limit = field === 'observations' ? 128 : 32
    if (!isInteger(value[field], 0, limit)) return undefined
    counts[field] = value[field]
  }
  const epochs = counts.microphone_epochs + counts.unclassified_epochs
  if (
    epochs > 32 ||
    counts.correlated_epochs > epochs ||
    (counts.admitted_sources > 0 && epochs === 0) ||
    counts.journal_ended_sources > counts.admitted_sources ||
    counts.send_stop_acknowledged_sources > counts.admitted_sources
  )
    return undefined
  return counts
}

/** Validate observed metadata independently of canonical text readiness. */
export function normalizeLiveObservedDomain(
  value: unknown
): MeetingLiveObservedDomain | undefined {
  if (
    !isRecord(value) ||
    !hasFields(value, [
      'version',
      'scope',
      'domain_ref',
      'producer_revision',
      'snapshot_digest',
      'projection_digest',
      'closure_state',
      'state',
      'retention_expires_at',
      'blockers',
      'counts',
    ]) ||
    value.version !== 1 ||
    value.scope !== 'observed_live_domain' ||
    !isUuid(value.domain_ref) ||
    !(
      isInteger(value.producer_revision, 1, 1024) ||
      value.producer_revision === Number.MAX_SAFE_INTEGER
    ) ||
    !isDigest(value.snapshot_digest) ||
    !isDigest(value.projection_digest) ||
    value.closure_state !== 'closed' ||
    (value.state !== 'observed' && value.state !== 'unknown') ||
    !isInteger(value.retention_expires_at, 1, Number.MAX_SAFE_INTEGER) ||
    !Array.isArray(value.blockers) ||
    value.blockers.length > 128
  )
    return undefined

  const blockers: MeetingLiveObservedDomain['blockers'] = []
  for (const entry of value.blockers) {
    if (
      !isRecord(entry) ||
      !hasFields(entry, ['reason', 'epoch_ref']) ||
      typeof entry.reason !== 'string' ||
      !liveDomainBlockerReasons.includes(
        entry.reason as LiveDomainBlockerReason
      ) ||
      !(entry.epoch_ref === null || isUuid(entry.epoch_ref))
    )
      return undefined
    blockers.push({
      reason: entry.reason as LiveDomainBlockerReason,
      epochRef: entry.epoch_ref,
    })
  }
  const counts = value.counts === null ? null : normalizeCounts(value.counts)
  if (counts === undefined) return undefined
  if ((value.state === 'unknown') !== (counts === null)) return undefined
  if (value.state === 'unknown' && blockers.length === 0) return undefined
  if (
    value.state !== 'unknown' &&
    blockers.some(({ reason }) =>
      (STRUCTURAL_REASONS as readonly string[]).includes(reason)
    )
  )
    return undefined
  const hasExhaustion = blockers.some(
    ({ reason }) => reason === 'revision_exhausted'
  )
  if ((value.producer_revision === Number.MAX_SAFE_INTEGER) !== hasExhaustion)
    return undefined
  if (
    blockers.some(({ reason }) => reason === 'blocker_limit_exceeded') &&
    (blockers.length !== 1 || blockers[0].epochRef !== null)
  )
    return undefined
  return {
    version: 1,
    scope: 'observed_live_domain',
    domainRef: value.domain_ref,
    producerRevision: value.producer_revision,
    snapshotDigest: value.snapshot_digest,
    projectionDigest: value.projection_digest,
    closureState: 'closed',
    state: value.state,
    retentionExpiresAt: value.retention_expires_at,
    blockers,
    counts,
  }
}
