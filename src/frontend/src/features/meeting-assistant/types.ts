/** Wire types published by meeting-runtime-contract/meeting-assistant.ts. */
export type MeetingAssistantOperation =
  | 'question'
  | 'summary'
  | 'actions'
  | 'email_draft'
export type MeetingAssistantCitation = {
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
export type MeetingAssistantCoverage = {
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
export type MeetingAssistantResult = {
  state: 'answered' | 'abstained' | 'refused'
  text: string
  rationale_category: 'supported' | 'insufficient' | 'contradictory' | 'policy'
  citations: MeetingAssistantCitation[]
  actions: {
    statement: string
    owner: string | null
    due_date: string | null
    citation_ids: string[]
  }[]
  email_draft: { subject: string; body: string } | null
  publication_state: 'private_proposal'
}
export type MeetingAssistantTurn = {
  command_id: string
  operation: MeetingAssistantOperation
  question: string | null
  state:
    | 'processing'
    | 'completed'
    | 'refused'
    | 'failed'
    | 'unknown'
    | 'sources_unavailable'
  result: MeetingAssistantResult | null
  coverage: MeetingAssistantCoverage | null
  created_at: number
}
export type MeetingAssistantThread = {
  version: 1
  meeting_ref: string
  thread_ref: string | null
  thread_version: number
  visibility: 'private'
  deleted: boolean
  turns: MeetingAssistantTurn[]
  has_more: boolean
  next_cursor: string | null
}
export type MeetingAssistantSendRequest = {
  version: 1
  meeting_ref: string
  command_id: string
  operation: MeetingAssistantOperation
  question: string
  expected_thread_version: number
}
export type MeetingAssistantSendResponse = {
  version: 1
  thread: MeetingAssistantThread
  turn: MeetingAssistantTurn
  idempotent_replay: boolean
}
export type MeetingAssistantSegment = {
  citation: MeetingAssistantCitation
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

export type MeetingAssistantDeleteRequest = {
  version: 1
  meeting_ref: string
  command_id: string
  expected_thread_version: number
}
export type MeetingAssistantDeleteResponse = {
  version: 1
  deleted: true
  thread_version: number
}
export type MeetingAssistantOpenCitationResponse = {
  version: 1
  segment: MeetingAssistantSegment
}
