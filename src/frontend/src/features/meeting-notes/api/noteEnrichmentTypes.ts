/** Browser DTOs mirror meeting-runtime-contract/note-enrichment, without Node imports. */
/** Budget shared by every complement of one proposal. */
export const MAX_ENRICHMENT_LENGTH = 8_000
export const MAX_ENRICHMENT_ITEMS = 32

export interface EnrichmentEvidence {
  citation: {
    citation_id: string
    meeting_ref: string
    source_kind:
      | 'provider_run'
      | 'reviewed_revision'
      | 'native_epoch'
      | 'published_brief'
    source_ref: string
    source_version: number
    source_digest: string
    segment_id: string
    start_ms: number
    end_ms: number
    time_basis: 'meeting_relative_ms' | 'source_relative_ms'
    epoch_ref?: string
  }
  text: string
  speaker: string | null
  status:
    | 'transcript'
    | 'published_summary'
    | 'published_proposal'
    | 'confirmed_decision'
    | 'confirmed_action'
    | 'confirmed_deadline'
}

export interface EnrichmentCoverage {
  state: 'full' | 'partial' | 'absent'
  source_coverage:
    | 'reference_transcript'
    | 'partial_sources'
    | 'published_only'
    | 'absent'
  selection: 'complete' | 'relevant_passages' | 'distributed_passages' | 'none'
  total_segments: number
  selected_segments: number
  omitted_segments: number
}

/** A complement inserted right under one line of the note it was generated from. */
export interface EnrichmentItem {
  after_line: number
  text: string
  citation_ids: string[]
}

/** A kept complement, by its position in the proposal, with the text to add. */
export interface ApprovedEnrichmentItem {
  index: number
  text: string
}

export interface EnrichmentProposal {
  id: string
  command_id: string
  status: 'generating' | 'ready' | 'empty' | 'failed' | 'unknown' | 'stale'
  base_revision: number
  base_digest: string
  items: EnrichmentItem[]
  evidence: EnrichmentEvidence[]
  coverage: EnrichmentCoverage | null
}

export type EnrichmentReason =
  | 'empty_note'
  | 'meeting_active'
  | 'sources_unavailable'
  | 'note_changed'
  | 'input_too_large'
  | 'operation_in_progress'

export interface NoteEnrichmentState {
  version: 1
  meeting_ref: string
  state_version: number
  eligible: boolean
  reason: EnrichmentReason | null
  proposal: EnrichmentProposal | null
  last_applied: {
    proposal_id: string
    revision: number
    digest: string
    undo_available: boolean
  } | null
  receipt: {
    command_id: string
    action: 'apply' | 'undo'
    revision: number
    digest: string
    idempotent_replay: boolean
  } | null
}

interface CommandBase {
  version: 1
  meeting_ref: string
  command_id: string
  expected_state_version: number
}

export type NoteEnrichmentCommand = CommandBase &
  (
    | { action: 'generate'; expected_revision: number }
    | {
        action: 'apply'
        expected_revision: number
        proposal_id: string
        items: ApprovedEnrichmentItem[]
      }
    | { action: 'discard'; proposal_id: string }
    | { action: 'undo'; expected_revision: number; proposal_id: string }
  )

export interface NoteEnrichmentApi {
  read(signal: AbortSignal): Promise<NoteEnrichmentState>
  command(
    command: NoteEnrichmentCommand,
    signal: AbortSignal
  ): Promise<NoteEnrichmentState>
}
