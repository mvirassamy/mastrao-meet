import { createInstance } from 'i18next'
import { describe, expect, it } from 'vitest'
import fr from '@/locales/fr/meetingAssistant.json'
import { assistantReadiness } from '@/features/meeting-assistant/assistantReadiness'
import { assistantTurnView } from '@/features/meeting-assistant/assistantView'
import type { MeetingAssistantTurn } from '@/features/meeting-assistant/types'

const i18n = createInstance()
await i18n.init({
  lng: 'fr',
  resources: { fr: { translation: fr } },
  initAsync: false,
})
const turn: MeetingAssistantTurn = {
  command_id: 'command_0123456789abcdef',
  operation: 'actions',
  question: null,
  state: 'completed',
  created_at: 1,
  coverage: {
    state: 'partial',
    source_coverage: 'reference_transcript',
    selection: 'distributed_passages',
    total_segments: 10,
    selected_segments: 3,
    omitted_segments: 7,
  },
  result: {
    state: 'answered',
    text: 'Une action proposée.',
    rationale_category: 'supported',
    citations: [
      {
        citation_id: 'citation_0123456789abcdef',
        meeting_ref: 'meeting_0123456789abcdef',
        source_kind: 'native_epoch',
        source_ref: 'source_0123456789abcdef',
        source_version: 1,
        source_digest: 'a'.repeat(64),
        segment_id: 'segment-1',
        start_ms: 42_000,
        end_ms: 45_000,
        time_basis: 'meeting_relative_ms',
      },
    ],
    actions: [
      {
        statement: 'Préparer le dossier',
        owner: null,
        due_date: null,
        citation_ids: ['citation_0123456789abcdef'],
      },
      {
        statement: 'Relancer l’agence',
        owner: 'Camille',
        due_date: 'vendredi',
        citation_ids: ['citation_0123456789abcdef'],
      },
    ],
    email_draft: null,
    publication_state: 'private_proposal',
  },
}

describe('assistant result projection', () => {
  it('lists actions without inventing owners or dates, with time sources', () => {
    expect(assistantTurnView(turn, i18n.t).result).toEqual({
      id: turn.command_id,
      outcome: 'answer',
      answer: {
        kind: 'actions',
        actions: [
          { statement: 'Préparer le dossier', details: '' },
          {
            statement: 'Relancer l’agence',
            details: 'Camille · Échéance : vendredi',
          },
        ],
      },
      citations: [{ id: 'citation_0123456789abcdef', label: '00:42' }],
      sourceLimit: null,
    })
  })
  it('notes a gap in the sources only when there is one', () => {
    const coverage = {
      ...turn.coverage!,
      source_coverage: 'partial_sources' as const,
    }
    expect(
      assistantTurnView({ ...turn, coverage }, i18n.t).result
    ).toMatchObject({
      sourceLimit: 'Une partie de la réunion n’a pas été transcrite.',
    })
  })
  it('does not reinterpret a failed generation as factual abstention or show an obsolete payload', () => {
    expect(
      assistantTurnView({ ...turn, state: 'failed' }, i18n.t).result
    ).toEqual({ id: turn.command_id, outcome: 'failure' })
  })
  it('distinguishes source insufficiency from a scope refusal', () => {
    const result = {
      ...turn.result!,
      state: 'abstained' as const,
      rationale_category: 'insufficient' as const,
    }
    expect(assistantTurnView({ ...turn, result }, i18n.t).result.outcome).toBe(
      'abstention'
    )
    expect(
      assistantTurnView(
        { ...turn, state: 'refused', result: { ...result, state: 'refused' } },
        i18n.t
      ).result.outcome
    ).toBe('refusal')
  })
  it('keeps the mail subject apart from its body', () => {
    const result = {
      ...turn.result!,
      email_draft: {
        subject: 'Suite à notre réunion',
        body: 'Bonjour, voici les points à relire.',
      },
    }
    expect(
      assistantTurnView({ ...turn, operation: 'email_draft', result }, i18n.t)
        .result
    ).toMatchObject({
      answer: {
        kind: 'email',
        subject: 'Suite à notre réunion',
        body: 'Bonjour, voici les points à relire.',
      },
    })
  })
})

describe('assistant readiness', () => {
  it('follows the transcript of the meeting', () => {
    expect(assistantReadiness('transcribing')).toBe('preparing')
    expect(assistantReadiness('available')).toBe('ready')
    expect(assistantReadiness('partial')).toBe('ready')
    expect(assistantReadiness('failed')).toBe('unavailable')
    expect(assistantReadiness('completed_empty')).toBe('unavailable')
  })
})
