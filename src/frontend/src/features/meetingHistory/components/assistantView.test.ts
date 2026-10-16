import { createInstance } from 'i18next'
import { describe, expect, it } from 'vitest'
import fr from '@/locales/fr/meetingAssistant.json'
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
    source_coverage: 'partial_sources',
    selection: 'distributed_passages',
    total_segments: 10,
    selected_segments: 3,
    omitted_segments: 7,
  },
  result: {
    state: 'answered',
    text: 'Une action proposée.',
    rationale_category: 'supported',
    citations: [],
    actions: [
      {
        statement: 'Préparer le dossier',
        owner: null,
        due_date: null,
        citation_ids: ['citation_0123456789abcdef'],
      },
    ],
    email_draft: null,
    publication_state: 'private_proposal',
  },
}

describe('assistant result projection', () => {
  it('keeps partial and non exhaustive coverage visible without inventing owners or dates', () => {
    const view = assistantTurnView(turn, i18n.t)
    expect(view.result.text).toBe(
      'Préparer le dossier\nResponsable: À préciser\nÉchéance: À préciser'
    )
    expect(view.result.editable).toBe(true)
    expect(view.result.coverage).toContain(
      'Sélection répartie dans la réunion ; couverture non exhaustive.'
    )
    expect(view.result.coverage).toContain('7 passages non inclus.')
  })
  it('does not reinterpret a failed generation as factual abstention or show an obsolete payload', () => {
    const view = assistantTurnView({ ...turn, state: 'failed' }, i18n.t)
    expect(view.result).toMatchObject({
      outcome: 'failure',
      text: '',
      coverage: [],
      citations: [],
      editable: false,
    })
  })
  it('distinguishes source insufficiency from a scope refusal and never makes them editable', () => {
    const result = {
      ...turn.result!,
      state: 'abstained' as const,
      rationale_category: 'insufficient' as const,
    }
    expect(assistantTurnView({ ...turn, result }, i18n.t).result).toMatchObject(
      { outcome: 'abstention', editable: false }
    )
    expect(
      assistantTurnView(
        { ...turn, state: 'refused', result: { ...result, state: 'refused' } },
        i18n.t
      ).result
    ).toMatchObject({ outcome: 'refusal', editable: false })
  })
  it('uses the supplied mail subject and body as a private editable proposal', () => {
    const result = {
      ...turn.result!,
      email_draft: {
        subject: 'Suite à notre réunion',
        body: 'Bonjour, voici les points à relire.',
      },
    }
    expect(
      assistantTurnView({ ...turn, operation: 'email_draft', result }, i18n.t)
        .result.text
    ).toBe(
      'Objet: Suite à notre réunion\n\nBonjour, voici les points à relire.'
    )
  })
})
