import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MeetingPersonalNotesSection } from '@/features/meetingHistory/components/MeetingPersonalNotesSection'
import fr from '@/locales/fr/personalNotes.json'
import en from '@/locales/en/personalNotes.json'
import type {
  ApprovedEnrichmentItem,
  NoteEnrichmentCommand,
  NoteEnrichmentState,
} from '../api/noteEnrichmentTypes'
import {
  deferred,
  enrichmentState,
  readyProposal,
  savedNote,
  endpoint,
  meetingRef,
  proposalId,
} from '../test/enrichmentFixtures'

const auth = vi.hoisted(() => ({
  user: { id: 'account_0123456789abcdef' } as { id: string } | undefined,
}))
vi.mock('@/features/auth/api/useUser', () => ({
  useUser: () => ({ user: auth.user }),
}))
const i18n = createInstance()

function response(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Core's rule: each kept complement on its own line, under its anchor line. */
function insertApproved(
  content: string,
  state: NoteEnrichmentState,
  items: ApprovedEnrichmentItem[]
) {
  const proposed = state.proposal?.items ?? []
  return content
    .split('\n')
    .flatMap((line, at) => [
      line,
      ...items
        .filter((item) => proposed[item.index]?.after_line === at + 1)
        .map((item) => item.text),
    ])
    .join('\n')
}

function mockBackend() {
  let note = savedNote()
  let state = enrichmentState()
  let generationWait: Promise<void> | undefined
  let mutationWait: Promise<void> | undefined
  let denied = false
  let rejectMutation = false
  let rejectGeneration = false
  let discardFailure: 'before' | 'after' | undefined
  const fetch = vi
    .fn<typeof globalThis.fetch>()
    .mockImplementation(async (url, options) => {
      if (String(url).endsWith('/enrichment/')) {
        if (denied) return response({ message: 'unavailable' }, 404)
        if (!options?.body) return response(state)
        const command = JSON.parse(
          String(options.body)
        ) as NoteEnrichmentCommand
        if (rejectMutation && command.action === 'apply') {
          rejectMutation = false
          throw new TypeError('Request never reached Core')
        }
        switch (command.action) {
          case 'generate':
            if (rejectGeneration) {
              rejectGeneration = false
              throw new TypeError('Generation never reached Core')
            }
            await generationWait
            state = enrichmentState({
              state_version: state.state_version + 1,
              proposal: readyProposal({
                // Like Core, a new generation replaces any earlier proposal.
                id: state.proposal
                  ? `proposal_${command.command_id}`
                  : proposalId,
                command_id: command.command_id,
                base_revision: command.expected_revision,
              }),
            })
            break
          case 'apply':
            await mutationWait
            note = savedNote(
              insertApproved(note.content, state, command.items),
              note.revision + 1
            )
            state = enrichmentState({
              state_version: state.state_version + 1,
              last_applied: {
                proposal_id: command.proposal_id,
                revision: note.revision,
                digest: 'c'.repeat(64),
                undo_available: true,
              },
              receipt: {
                command_id: command.command_id,
                action: 'apply',
                revision: note.revision,
                digest: 'c'.repeat(64),
                idempotent_replay: false,
              },
            })
            break
          case 'discard':
            if (discardFailure === 'before') {
              discardFailure = undefined
              throw new TypeError('Discard never reached Core')
            }
            state = enrichmentState({ state_version: state.state_version + 1 })
            if (discardFailure === 'after') {
              discardFailure = undefined
              throw new TypeError('Discard response lost')
            }
            break
        }
        return response(state)
      }
      if (options?.method === 'PUT') {
        const body = JSON.parse(String(options.body)) as {
          content: string
          expected_revision: number
        }
        note = savedNote(body.content, note.revision + 1)
      }
      return response(note)
    })
  vi.stubGlobal('fetch', fetch)
  return {
    fetch,
    getNote: () => note,
    commands: () =>
      fetch.mock.calls
        .filter(([, options]) => options?.method === 'POST')
        .map(
          ([, options]) =>
            JSON.parse(String(options?.body)) as NoteEnrichmentCommand
        ),
    setState: (value: typeof state) => {
      state = value
    },
    delayGeneration: (wait: Promise<void>) => {
      generationWait = wait
    },
    delayMutation: (wait: Promise<void>) => {
      mutationWait = wait
    },
    deny: () => {
      denied = true
    },
    failNextMutation: () => {
      rejectMutation = true
    },
    failNextGeneration: () => {
      rejectGeneration = true
    },
    failNextDiscard: (when: 'before' | 'after') => {
      discardFailure = when
    },
  }
}

function mount() {
  return render(
    <I18nextProvider i18n={i18n}>
      <MeetingPersonalNotesSection
        endpoint={endpoint}
        meetingRef={meetingRef}
        inTab
      />
    </I18nextProvider>
  )
}

async function advance(milliseconds = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds)
  })
}
async function generate() {
  fireEvent.click(screen.getByRole('button', { name: fr.enrichment.generate }))
  await advance()
}
/** The note editor; a proposal under review stands in for it. */
const editor = () =>
  screen.getByRole('textbox', { name: fr.title }) as HTMLTextAreaElement
const complement = (number = 1, label = fr.enrichment.item) =>
  screen.getByRole('textbox', {
    name: label.replace('{{number}}', String(number)),
  }) as HTMLTextAreaElement
const decision = (label: string, number = 1) =>
  screen.getByRole('button', {
    name: label.replace('{{number}}', String(number)),
  })

beforeEach(async () => {
  vi.useFakeTimers()
  auth.user = { id: 'account_0123456789abcdef' }
  await i18n.init({
    lng: 'fr',
    fallbackLng: 'en',
    resources: { fr: { personalNotes: fr }, en: { personalNotes: en } },
    interpolation: { escapeValue: false },
  })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('history enrichment through real APIs, controller and primitives', () => {
  it('shows each complement under its line in place of the editor and inserts it on the server only', async () => {
    const backend = mockBackend()
    const pending = deferred<void>()
    backend.delayMutation(pending.promise)
    mount()
    await advance()
    expect(backend.commands()).toEqual([])
    expect(editor().value).toBe(savedNote().content)
    await generate()
    expect(screen.queryByRole('textbox', { name: fr.title })).toBeNull()
    const review = screen.getByRole('region', { name: fr.enrichment.review })
    const lines = [...review.querySelectorAll('p, textarea')].map((element) =>
      element instanceof HTMLTextAreaElement
        ? element.value
        : element.textContent
    )
    expect(lines.slice(0, 3)).toEqual([
      'Original',
      readyProposal().items[0].text,
      'avec espaces  ',
    ])
    expect(document.activeElement).toBe(complement())
    expect(screen.queryByText(/Passages analysés/)).toBeNull()
    fireEvent.click(screen.getByText(fr.enrichment.evidence))
    expect(screen.getByText(readyProposal().evidence[0].text)).toBeTruthy()
    expect(screen.queryByRole('link')).toBeNull()
    fireEvent.change(complement(), {
      target: { value: '- Complément approuvé et corrigé.' },
    })
    expect(screen.getByText(fr.enrichment.modified)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: fr.enrichment.apply }))
    await advance()
    expect(complement().readOnly).toBe(true)
    expect(screen.queryByText(fr.enrichment.applied)).toBeNull()
    const request = backend
      .commands()
      .find((command) => command.action === 'apply')
    expect(request).toMatchObject({
      items: [{ index: 0, text: '- Complément approuvé et corrigé.' }],
      expected_revision: 1,
      proposal_id: proposalId,
    })
    expect(request).not.toHaveProperty('content')
    pending.resolve()
    await advance()
    expect(editor().readOnly).toBe(false)
    expect(editor().value).toBe(
      'Original\n- Complément approuvé et corrigé.\navec espaces  '
    )
    expect(screen.getByText(fr.enrichment.applied)).toBeTruthy()
    // Going back is an ordinary note edit, through the note history.
    fireEvent.click(screen.getByRole('button', { name: fr.toolbar.undo }))
    await advance(800)
    expect(editor().value).toBe(savedNote().content)
    expect(screen.queryByText(fr.enrichment.applied)).toBeNull()
    expect(backend.getNote().content).toBe(savedNote().content)
    expect(backend.commands().map((command) => command.action)).toEqual([
      'generate',
      'apply',
    ])
    fireEvent.click(screen.getByRole('button', { name: fr.toolbar.redo }))
    await advance(800)
    expect(backend.getNote().content).toBe(
      'Original\n- Complément approuvé et corrigé.\navec espaces  '
    )
  })

  it('keeps or refuses each complement and restores the proposal', async () => {
    const backend = mockBackend()
    const item = readyProposal().items[0]
    backend.setState(
      enrichmentState({
        state_version: 1,
        proposal: readyProposal({
          items: [item, { ...item, after_line: 2, text: '- Relancer Bob.' }],
        }),
      })
    )
    mount()
    await advance()
    expect(screen.getByText('2 compléments proposés')).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: fr.enrichment.revert })
    ).toBeNull()
    fireEvent.change(complement(1), { target: { value: '- Texte corrigé.' } })
    expect(screen.getByText(fr.enrichment.modified)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: fr.enrichment.revert }))
    expect(complement(1).value).toBe(item.text)
    expect(screen.queryByText(fr.enrichment.modified)).toBeNull()
    const refuse = decision(fr.enrichment.refuse, 1)
    fireEvent.click(refuse)
    expect(refuse.getAttribute('aria-pressed')).toBe('true')
    expect(complement(1).readOnly).toBe(true)
    expect(screen.getByText('Gardés : 1 · refusés : 1')).toBeTruthy()
    fireEvent.click(decision(fr.enrichment.refuse, 2))
    const save = screen.getByRole('button', {
      name: fr.enrichment.apply,
    }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    fireEvent.click(decision(fr.enrichment.keep, 2))
    fireEvent.click(screen.getByRole('button', { name: fr.enrichment.apply }))
    await advance()
    expect(
      backend.commands().find((command) => command.action === 'apply')
    ).toMatchObject({ items: [{ index: 1, text: '- Relancer Bob.' }] })
    expect(backend.getNote().content).toBe(
      'Original\navec espaces  \n- Relancer Bob.'
    )
  })

  it('keeps note-taking editable during generation and rejects the resulting stale proposal', async () => {
    const backend = mockBackend()
    const pending = deferred<void>()
    backend.delayGeneration(pending.promise)
    mount()
    await advance()
    await generate()
    const original = editor()
    expect(original.readOnly).toBe(false)
    original.focus()
    fireEvent.change(original, {
      target: { value: 'Saisie pendant la génération' },
    })
    pending.resolve()
    await advance()
    expect(original.value).toBe('Saisie pendant la génération')
    expect(document.activeElement).toBe(original)
    expect(screen.getByText(fr.enrichment.stale)).toBeTruthy()
    expect(
      screen.queryByRole('region', { name: fr.enrichment.review })
    ).toBeNull()
    expect(
      backend.commands().filter((request) => request.action === 'apply')
    ).toEqual([])
    // The stale toast offers the way out: a new proposal for the saved note.
    await advance(800)
    fireEvent.click(screen.getByRole('button', { name: fr.enrichment.retry }))
    await advance()
    expect(
      screen.getByRole('region', { name: fr.enrichment.review })
    ).toBeTruthy()
    expect(backend.commands().map((request) => request.action)).toEqual([
      'generate',
      'generate',
    ])
  })

  it('retries a generation that could not be confirmed from its toast', async () => {
    const backend = mockBackend()
    mount()
    await advance()
    backend.failNextGeneration()
    await generate()
    expect(screen.getByRole('alert').textContent).toBe(
      fr.enrichment.errors.generate
    )
    fireEvent.click(screen.getByRole('button', { name: fr.enrichment.retry }))
    await advance()
    expect(
      screen.getByRole('region', { name: fr.enrichment.review })
    ).toBeTruthy()
    expect(backend.commands().map((request) => request.action)).toEqual([
      'generate',
      'generate',
    ])
  })

  it('shows a failed generation as a toast that generates again', async () => {
    const backend = mockBackend()
    backend.setState(
      enrichmentState({
        state_version: 1,
        proposal: readyProposal({ status: 'failed', items: [] }),
      })
    )
    mount()
    await advance()
    expect(screen.getByRole('alert').textContent).toBe(fr.enrichment.failed)
    fireEvent.click(screen.getByRole('button', { name: fr.enrichment.retry }))
    await advance()
    expect(backend.commands().map((request) => request.action)).toEqual([
      'generate',
    ])
  })

  it('only offers cancellation after a server proposal exists and restores focus after confirmation', async () => {
    const backend = mockBackend()
    const pending = deferred<void>()
    backend.delayGeneration(pending.promise)
    mount()
    await advance()
    await generate()
    expect(
      screen.queryByRole('button', { name: fr.enrichment.cancel })
    ).toBeNull()
    expect(screen.queryByText(fr.enrichment.cancelled)).toBeNull()
    expect(
      (screen.getByRole('textbox', { name: fr.title }) as HTMLTextAreaElement)
        .readOnly
    ).toBe(false)
    pending.resolve()
    await advance()
    fireEvent.click(screen.getByRole('button', { name: fr.enrichment.cancel }))
    await advance()
    expect(screen.getByText(fr.enrichment.cancelled)).toBeTruthy()
    expect(document.activeElement).toBe(
      screen.getByRole('button', { name: fr.enrichment.generate })
    )
    expect(
      screen.queryByRole('region', { name: fr.enrichment.review })
    ).toBeNull()
    expect(editor().value).toBe(savedNote().content)
    expect(backend.commands().map((request) => request.action)).toEqual([
      'generate',
      'discard',
    ])
  })

  it.each(['before', 'after'] as const)(
    'resolves a discard response lost %s Core without an endless lock',
    async (when) => {
      const backend = mockBackend()
      mount()
      await advance()
      await generate()
      backend.failNextDiscard(when)
      fireEvent.click(
        screen.getByRole('button', { name: fr.enrichment.cancel })
      )
      await advance()
      expect(screen.queryByText(fr.enrichment.discarding)).toBeNull()
      expect(screen.queryByText(fr.enrichment.cancelled)).toBeNull()
      if (when === 'after') {
        expect(editor().readOnly).toBe(false)
        expect(screen.getByText(fr.enrichment.proposalRemoved)).toBeTruthy()
        expect(
          screen.queryByRole('region', { name: fr.enrichment.review })
        ).toBeNull()
      } else {
        expect(complement().readOnly).toBe(false)
        expect(screen.getByRole('alert').textContent).toBe(
          fr.enrichment.errors.discard
        )
        fireEvent.click(
          screen.getByRole('button', { name: fr.enrichment.retry })
        )
        await advance()
        expect(screen.getByText(fr.enrichment.cancelled)).toBeTruthy()
      }
    }
  )

  it('offers an accessible explicit replay of an uncertain apply and unlocks after confirmation', async () => {
    const backend = mockBackend()
    mount()
    await advance()
    await generate()
    backend.failNextMutation()
    fireEvent.click(screen.getByRole('button', { name: fr.enrichment.apply }))
    await advance()
    // An uncertain apply keeps the proposal under review, read-only.
    expect(complement().readOnly).toBe(true)
    expect(screen.getByRole('alert').textContent).toBe(
      fr.enrichment.retryMutationTitle
    )
    expect(
      backend.commands().filter((command) => command.action === 'apply')
    ).toHaveLength(1)
    const pending = deferred<void>()
    backend.delayMutation(pending.promise)
    fireEvent.click(screen.getByRole('button', { name: fr.enrichment.retry }))
    await advance()
    expect(
      screen.queryByRole('button', { name: fr.enrichment.retry })
    ).toBeNull()
    expect(complement().readOnly).toBe(true)
    pending.resolve()
    await advance()
    const commands = backend
      .commands()
      .filter((command) => command.action === 'apply')
    expect(commands).toHaveLength(2)
    expect(commands[1]).toEqual(commands[0])
    expect(editor().readOnly).toBe(false)
    expect(editor().value).toBe(backend.getNote().content)
    expect(screen.getByText(fr.enrichment.applied)).toBeTruthy()
  })

  it('shows the server eligibility reason without loading transcript sources', async () => {
    const backend = mockBackend()
    backend.setState(
      enrichmentState({ eligible: false, reason: 'sources_unavailable' })
    )
    mount()
    await advance()
    expect(
      screen.getByText(fr.enrichment.reasons.sources_unavailable)
    ).toBeTruthy()
    expect(
      (
        screen.getByRole('button', {
          name: fr.enrichment.generate,
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    expect(
      backend.fetch.mock.calls.every(([url]) =>
        String(url).includes('/personal-note/')
      )
    ).toBe(true)
    expect(backend.commands()).toEqual([])
  })

  it('accepts server-eligible settled partial sources and displays their coverage', async () => {
    const backend = mockBackend()
    const proposal = readyProposal()
    proposal.coverage!.source_coverage = 'partial_sources'
    backend.setState(enrichmentState({ state_version: 1, proposal }))
    mount()
    await advance()
    expect(
      screen.getByText(/Certaines sources de la réunion sont manquantes/)
    ).toBeTruthy()
    expect(
      (
        screen.getByRole('button', {
          name: fr.enrichment.apply,
        }) as HTMLButtonElement
      ).disabled
    ).toBe(false)
    expect(backend.commands()).toEqual([])
  })

  it('has no AI controls or enrichment requests for temporary guest notes', async () => {
    const backend = mockBackend()
    auth.user = undefined
    mount()
    await advance()
    expect(screen.getByText(fr.guestNotice)).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: fr.enrichment.generate })
    ).toBeNull()
    expect(backend.fetch).toHaveBeenCalledOnce()
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(
      false
    )
  })

  it('also hides the feature when server authority refuses a signed-in guest document', async () => {
    const backend = mockBackend()
    backend.deny()
    mount()
    await advance()
    expect(
      screen.queryByRole('button', { name: fr.enrichment.generate })
    ).toBeNull()
    expect(backend.commands()).toEqual([])
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(
      savedNote().content
    )
  })

  it('undoes and redoes typing with the arrows and ⌘Z, saving each step', async () => {
    const backend = mockBackend()
    mount()
    await advance()
    const undo = screen.getByRole('button', {
      name: fr.toolbar.undo,
    }) as HTMLButtonElement
    const redo = screen.getByRole('button', {
      name: fr.toolbar.redo,
    }) as HTMLButtonElement
    expect(undo.disabled).toBe(true)
    expect(redo.disabled).toBe(true)
    fireEvent.change(editor(), { target: { value: 'Première saisie' } })
    await advance(800)
    fireEvent.change(editor(), { target: { value: 'Deuxième saisie' } })
    expect(undo.disabled).toBe(false)
    fireEvent.keyDown(editor(), { key: 'z', metaKey: true })
    await advance(800)
    expect(editor().value).toBe('Première saisie')
    expect(backend.getNote().content).toBe('Première saisie')
    fireEvent.click(undo)
    await advance(800)
    expect(editor().value).toBe(savedNote().content)
    expect(undo.disabled).toBe(true)
    fireEvent.keyDown(editor(), { key: 'Z', metaKey: true, shiftKey: true })
    expect(editor().value).toBe('Première saisie')
    fireEvent.change(editor(), { target: { value: 'Nouvelle branche' } })
    expect(redo.disabled).toBe(true)
    await advance(800)
    expect(backend.getNote().content).toBe('Nouvelle branche')
    expect(backend.commands()).toEqual([])
  })

  it('uses English labels, announces status and rejects empty or oversized additions accessibly', async () => {
    mockBackend()
    await i18n.changeLanguage('en')
    mount()
    await advance()
    fireEvent.click(
      screen.getByRole('button', { name: en.enrichment.generate })
    )
    await advance()
    const preview = complement(1, en.enrichment.item)
    expect(preview.getAttribute('aria-invalid')).toBe('false')
    fireEvent.change(preview, { target: { value: ' ' } })
    expect(preview.getAttribute('aria-invalid')).toBe('true')
    expect(screen.getByRole('alert').textContent).toBe(
      en.enrichment.invalidAdditions
    )
    expect(
      (
        screen.getByRole('button', {
          name: en.enrichment.apply,
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    fireEvent.change(preview, { target: { value: 'x'.repeat(8001) } })
    expect(screen.getByRole('alert').textContent).toBe(
      en.enrichment.invalidAdditions
    )
    expect(
      screen
        .getAllByRole('status')
        .every((element) => element.getAttribute('aria-live') === 'polite')
    ).toBe(true)
  })
})
