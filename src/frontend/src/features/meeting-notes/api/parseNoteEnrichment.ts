import type {
  EnrichmentCoverage,
  EnrichmentEvidence,
  EnrichmentProposal,
  NoteEnrichmentState,
} from './noteEnrichmentTypes'

const reference = (value: unknown): value is string =>
  typeof value === 'string' && /^[A-Za-z0-9_-]{16,160}$/.test(value)
const digest = (value: unknown): value is string =>
  typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const revision = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const oneOf = (value: unknown, values: readonly string[]) =>
  typeof value === 'string' && values.includes(value)
const boundedText = (
  value: unknown,
  maximum: number,
  minimum = 0
): value is string =>
  typeof value === 'string' &&
  value.length >= minimum &&
  value.length <= maximum

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function keys(
  value: Record<string, unknown>,
  required: string[],
  optional: string[] = []
) {
  return (
    required.every((key) => Object.hasOwn(value, key)) &&
    Object.keys(value).every(
      (key) => required.includes(key) || optional.includes(key)
    )
  )
}

export function validEnrichmentText(value: unknown): value is string {
  return (
    boundedText(value, 8000) &&
    !value.includes('\u0000') &&
    !/[\uD800-\uDFFF]/u.test(value)
  )
}

function evidence(value: unknown): value is EnrichmentEvidence {
  if (!object(value) || !keys(value, ['citation', 'text', 'speaker', 'status']))
    return false
  const citation = value.citation
  if (
    !object(citation) ||
    !keys(
      citation,
      [
        'citation_id',
        'meeting_ref',
        'source_kind',
        'source_ref',
        'source_version',
        'source_digest',
        'segment_id',
        'start_ms',
        'end_ms',
        'time_basis',
      ],
      ['epoch_ref']
    )
  )
    return false
  return (
    reference(citation.citation_id) &&
    reference(citation.meeting_ref) &&
    oneOf(citation.source_kind, [
      'provider_run',
      'reviewed_revision',
      'native_epoch',
      'published_brief',
    ]) &&
    reference(citation.source_ref) &&
    revision(citation.source_version) &&
    digest(citation.source_digest) &&
    typeof citation.segment_id === 'string' &&
    /^[A-Za-z0-9_-]{1,160}$/.test(citation.segment_id) &&
    revision(citation.start_ms) &&
    revision(citation.end_ms) &&
    citation.end_ms >= citation.start_ms &&
    oneOf(citation.time_basis, ['meeting_relative_ms', 'source_relative_ms']) &&
    (citation.epoch_ref === undefined || reference(citation.epoch_ref)) &&
    boundedText(value.text, 8000, 1) &&
    (value.speaker === null || boundedText(value.speaker, 160)) &&
    oneOf(value.status, [
      'transcript',
      'published_summary',
      'published_proposal',
      'confirmed_decision',
      'confirmed_action',
      'confirmed_deadline',
    ])
  )
}

function coverage(value: unknown): value is EnrichmentCoverage {
  if (
    !object(value) ||
    !keys(value, [
      'state',
      'source_coverage',
      'selection',
      'total_segments',
      'selected_segments',
      'omitted_segments',
    ])
  )
    return false
  return (
    oneOf(value.state, ['full', 'partial', 'absent']) &&
    oneOf(value.source_coverage, [
      'reference_transcript',
      'partial_sources',
      'published_only',
      'absent',
    ]) &&
    oneOf(value.selection, [
      'complete',
      'relevant_passages',
      'distributed_passages',
      'none',
    ]) &&
    revision(value.total_segments) &&
    revision(value.selected_segments) &&
    revision(value.omitted_segments) &&
    value.total_segments === value.selected_segments + value.omitted_segments
  )
}

function proposal(value: unknown): value is EnrichmentProposal {
  if (
    !object(value) ||
    !keys(value, [
      'id',
      'command_id',
      'status',
      'base_revision',
      'base_digest',
      'text',
      'evidence',
      'coverage',
    ])
  )
    return false
  return (
    reference(value.id) &&
    reference(value.command_id) &&
    oneOf(value.status, [
      'generating',
      'ready',
      'empty',
      'failed',
      'unknown',
      'stale',
    ]) &&
    revision(value.base_revision) &&
    digest(value.base_digest) &&
    validEnrichmentText(value.text) &&
    Array.isArray(value.evidence) &&
    value.evidence.length <= 32 &&
    value.evidence.every(evidence) &&
    (value.coverage === null || coverage(value.coverage))
  )
}

function lastApplied(
  value: unknown
): value is NonNullable<NoteEnrichmentState['last_applied']> {
  return (
    object(value) &&
    keys(value, ['proposal_id', 'revision', 'digest', 'undo_available']) &&
    reference(value.proposal_id) &&
    revision(value.revision) &&
    digest(value.digest) &&
    typeof value.undo_available === 'boolean'
  )
}

function receipt(
  value: unknown
): value is NonNullable<NoteEnrichmentState['receipt']> {
  return (
    object(value) &&
    keys(value, [
      'command_id',
      'action',
      'revision',
      'digest',
      'idempotent_replay',
    ]) &&
    reference(value.command_id) &&
    oneOf(value.action, ['apply', 'undo']) &&
    revision(value.revision) &&
    digest(value.digest) &&
    typeof value.idempotent_replay === 'boolean'
  )
}

export function isNoteEnrichmentState(
  value: unknown
): value is NoteEnrichmentState {
  if (
    !object(value) ||
    !keys(value, [
      'version',
      'meeting_ref',
      'state_version',
      'eligible',
      'reason',
      'proposal',
      'last_applied',
      'receipt',
    ])
  )
    return false
  return (
    value.version === 1 &&
    reference(value.meeting_ref) &&
    revision(value.state_version) &&
    typeof value.eligible === 'boolean' &&
    (value.reason === null ||
      oneOf(value.reason, [
        'empty_note',
        'meeting_active',
        'sources_unavailable',
        'note_changed',
        'input_too_large',
        'operation_in_progress',
      ])) &&
    (value.proposal === null || proposal(value.proposal)) &&
    (value.last_applied === null || lastApplied(value.last_applied)) &&
    (value.receipt === null || receipt(value.receipt))
  )
}
