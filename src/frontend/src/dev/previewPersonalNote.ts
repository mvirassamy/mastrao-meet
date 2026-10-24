/**
 * A fictional, in-memory walk through « Enrichir mes notes » for the local
 * preview: the note saves, a proposal arrives after a short wait, and saving,
 * cancelling and undoing it change only this page's memory. Nothing is sent.
 */

const GENERATION_DELAY_MS = 2500

const note = {
  id: 'preview-note',
  content:
    'Points à vérifier avec Camille :\n- calendrier de signature du compromis\n- pièces manquantes côté vendeur\n\nÀ relancer avant vendredi.',
  revision: 3,
  created_at: '2026-10-16T09:00:00Z',
  updated_at: '2026-10-16T09:12:00Z',
}

interface ApprovedItem {
  index: number
  text: string
}
interface Command {
  action: 'generate' | 'apply' | 'discard' | 'undo'
  command_id: string
  proposal_id?: string
  items?: ApprovedItem[]
}
interface ProposalItem {
  after_line: number
  text: string
  citation_ids: string[]
}

let stateVersion = 0
let commandCount = 0
let proposal: Record<string, unknown> | null = null
let proposalItems: ProposalItem[] = []
let lastApplied: Record<string, unknown> | null = null
let receipt: Record<string, unknown> | null = null
let contentBeforeApply: string | null = null

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

/** The first line mentioning the word, or the last line of the note. */
function lineAbout(content: string, word: string) {
  const lines = content.split('\n')
  const index = lines.findIndex((line) => line.includes(word))
  return index >= 0 ? index + 1 : lines.length
}

/** Same rule as Core: each kept complement on its own line under its anchor. */
function insert(content: string, items: ApprovedItem[]) {
  return content
    .split('\n')
    .flatMap((line, at) => [
      line,
      ...items
        .filter((item) => proposalItems[item.index]?.after_line === at + 1)
        .map((item) => item.text),
    ])
    .join('\n')
}

function citation(meetingRef: string) {
  return {
    citation_id: 'citation_apercu_000000001',
    meeting_ref: meetingRef,
    source_kind: 'native_epoch',
    source_ref: 'source_apercu_0000000001',
    source_version: 1,
    source_digest: 'b'.repeat(64),
    segment_id: 'segment_1',
    start_ms: 754_000,
    end_ms: 768_000,
    time_basis: 'source_relative_ms',
  }
}

function readyProposal(meetingRef: string, commandId: string) {
  commandCount += 1
  proposalItems = [
    {
      after_line: lineAbout(note.content, 'calendrier'),
      text: '  → Camille l’envoie avant jeudi.',
      citation_ids: ['citation_apercu_000000001'],
    },
    {
      after_line: lineAbout(note.content, 'pièces'),
      text: '  → Diagnostic amiante et titre de propriété à demander au vendeur.',
      citation_ids: ['citation_apercu_000000001'],
    },
  ]
  return {
    id: `proposal_apercu_${String(commandCount).padStart(10, '0')}`,
    command_id: commandId,
    status: 'ready',
    base_revision: note.revision,
    base_digest: 'a'.repeat(64),
    items: proposalItems,
    evidence: [
      {
        citation: citation(meetingRef),
        text: 'Je vous envoie le calendrier de signature avant jeudi.',
        speaker: 'Camille',
        status: 'transcript',
      },
    ],
    coverage: {
      state: 'partial',
      source_coverage: 'reference_transcript',
      selection: 'relevant_passages',
      total_segments: 24,
      selected_segments: 6,
      omitted_segments: 18,
    },
  }
}

function writeNote(content: string) {
  note.content = content
  note.revision += 1
  note.updated_at = new Date().toISOString()
}

function enrichmentState(meetingRef: string) {
  return {
    version: 1,
    meeting_ref: meetingRef,
    state_version: stateVersion,
    eligible: note.content.trim().length > 0,
    reason: note.content.trim() ? null : 'empty_note',
    proposal,
    last_applied: lastApplied,
    receipt,
  }
}

function mutationReceipt(command: Command, action: 'apply' | 'undo') {
  return {
    command_id: command.command_id,
    action,
    revision: note.revision,
    digest: 'c'.repeat(64),
    idempotent_replay: false,
  }
}

async function runCommand(meetingRef: string, command: Command) {
  stateVersion += 1
  switch (command.action) {
    case 'generate':
      await new Promise((resolve) => setTimeout(resolve, GENERATION_DELAY_MS))
      proposal = readyProposal(meetingRef, command.command_id)
      break
    case 'apply':
      contentBeforeApply = note.content
      writeNote(insert(note.content, command.items ?? []))
      receipt = mutationReceipt(command, 'apply')
      lastApplied = {
        proposal_id: command.proposal_id,
        revision: note.revision,
        digest: 'c'.repeat(64),
        undo_available: true,
      }
      proposal = null
      break
    case 'discard':
      proposal = null
      break
    case 'undo':
      if (contentBeforeApply !== null) writeNote(contentBeforeApply)
      contentBeforeApply = null
      receipt = mutationReceipt(command, 'undo')
      lastApplied = lastApplied && { ...lastApplied, undo_available: false }
      break
  }
}

/** Serves the note and its enrichment; any other request is not ours. */
export async function previewPersonalNoteResponse(
  url: URL,
  method: string,
  body: unknown
): Promise<Response | null> {
  if (url.pathname.endsWith('/personal-note/enrichment/')) {
    const meetingRef = url.pathname.split('/').at(-4) ?? ''
    if (method === 'POST')
      await runCommand(meetingRef, JSON.parse(String(body)) as Command)
    return json(enrichmentState(meetingRef))
  }
  if (url.pathname.endsWith('/personal-note/')) {
    if (method === 'PUT')
      writeNote((JSON.parse(String(body)) as { content: string }).content)
    return json(note)
  }
  return null
}
