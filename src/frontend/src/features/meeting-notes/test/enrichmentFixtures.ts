import type { PersonalNote } from '../api/personalNotesApi'
import type {
  EnrichmentProposal,
  NoteEnrichmentState,
} from '../api/noteEnrichmentTypes'

export const meetingRef = 'meeting_0123456789abcdef'
export const proposalId = 'proposal_0123456789abcdef'
export const endpoint = `/meetings/history/${meetingRef}/personal-note/`

export function savedNote(
  content = 'Original\navec espaces  ',
  revision = 1
): PersonalNote {
  return { id: 'note', content, revision, created_at: null, updated_at: null }
}

export function readyProposal(
  patch: Partial<EnrichmentProposal> = {}
): EnrichmentProposal {
  return {
    id: proposalId,
    command_id: 'command_0123456789abcdef',
    status: 'ready',
    base_revision: 1,
    base_digest: 'a'.repeat(64),
    text: 'Vérifier la date avec Alice.',
    evidence: [
      {
        citation: {
          citation_id: 'citation_0123456789abcdef',
          meeting_ref: meetingRef,
          source_kind: 'native_epoch',
          source_ref: 'source_0123456789abcdef',
          source_version: 1,
          source_digest: 'b'.repeat(64),
          segment_id: 'segment_1',
          start_ms: 0,
          end_ms: 1000,
          time_basis: 'source_relative_ms',
        },
        text: 'Alice : la date reste à vérifier.',
        speaker: 'Alice',
        status: 'transcript',
      },
    ],
    coverage: {
      state: 'partial',
      source_coverage: 'reference_transcript',
      selection: 'relevant_passages',
      total_segments: 3,
      selected_segments: 1,
      omitted_segments: 2,
    },
    ...patch,
  }
}

export function enrichmentState(
  patch: Partial<NoteEnrichmentState> = {}
): NoteEnrichmentState {
  return {
    version: 1,
    meeting_ref: meetingRef,
    state_version: 0,
    eligible: true,
    reason: null,
    proposal: null,
    last_applied: null,
    receipt: null,
    ...patch,
  }
}

export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((done, fail) => {
    resolve = done
    reject = fail
  })
  return { promise, resolve, reject }
}
