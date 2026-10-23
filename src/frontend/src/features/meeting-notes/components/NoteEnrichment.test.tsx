import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MeetingPersonalNotesSection } from '@/features/meetingHistory/components/MeetingPersonalNotesSection'
import fr from '@/locales/fr/personalNotes.json'
import en from '@/locales/en/personalNotes.json'
import type { NoteEnrichmentCommand } from '../api/noteEnrichmentTypes'
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

function mockBackend() {
  let note = savedNote()
  let state = enrichmentState()
  let generationWait: Promise<void> | undefined
  let mutationWait: Promise<void> | undefined
  let denied = false
  let rejectMutation = false
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
        if (
          rejectMutation &&
          (command.action === 'apply' || command.action === 'undo')
        ) {
          rejectMutation = false
          throw new TypeError('Request never reached Core')
        }
        switch (command.action) {
          case 'generate':
            await generationWait
            state = enrichmentState({
              state_version: state.state_version + 1,
              proposal: readyProposal({
                command_id: command.command_id,
                base_revision: command.expected_revision,
              }),
            })
            break
          case 'apply':
            await mutationWait
            note = savedNote(
              `${note.content}\n\nCompléments de la réunion\n${command.additions}`,
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
          case 'undo':
            await mutationWait
            note = savedNote(savedNote().content, note.revision + 1)
            state = enrichmentState({
              state_version: state.state_version + 1,
              last_applied: state.last_applied && {
                ...state.last_applied,
                undo_available: false,
              },
              receipt: {
                command_id: command.command_id,
                action: 'undo',
                revision: note.revision,
                digest: 'd'.repeat(64),
                idempotent_replay: false,
              },
            })
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
  it('reviews editable additions beside the untouched original and applies on the server only', async () => {
    const backend = mockBackend()
    const pending = deferred<void>()
    backend.delayMutation(pending.promise)
    mount()
    await advance()
    expect(backend.commands()).toEqual([])
    const original = screen.getByRole('textbox', {
      name: fr.title,
    }) as HTMLTextAreaElement
    await generate()
    const preview = screen.getByRole('textbox', {
      name: fr.enrichment.preview,
    }) as HTMLTextAreaElement
    expect(original.value).toBe(savedNote().content)
    expect(preview.value).toBe(readyProposal().text)
    expect(screen.getByText(/^Passages analysés : 1 \/ 3\./)).toBeTruthy()
    fireEvent.click(screen.getByText(fr.enrichment.evidence))
    expect(screen.getByText(readyProposal().evidence[0].text)).toBeTruthy()
    expect(screen.queryByRole('link')).toBeNull()
    fireEvent.change(preview, {
      target: { value: 'Complément approuvé et corrigé.' },
    })
    expect(screen.getByText(fr.enrichment.modified)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: fr.enrichment.apply }))
    await advance()
    expect(original.readOnly).toBe(true)
    expect(original.value).toBe(savedNote().content)
    expect(screen.queryByText(fr.enrichment.applied)).toBeNull()
    const request = backend
      .commands()
      .find((command) => command.action === 'apply')
    expect(request).toMatchObject({
      additions: 'Complément approuvé et corrigé.',
      expected_revision: 1,
      proposal_id: proposalId,
    })
    expect(request).not.toHaveProperty('content')
    pending.resolve()
    await advance()
    expect(original.readOnly).toBe(false)
    expect(original.value).toBe(
      `${savedNote().content}\n\nCompléments de la réunion\nComplément approuvé et corrigé.`
    )
    expect(screen.getByText(fr.enrichment.applied)).toBeTruthy()
    expect(
      (
        screen.getByRole('button', {
          name: fr.enrichment.undo,
        }) as HTMLButtonElement
      ).disabled
    ).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: fr.enrichment.undo }))
    await advance()
    expect(original.value).toBe(savedNote().content)
    expect(screen.getByText(fr.enrichment.undone)).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: fr.enrichment.undo })
    ).toBeNull()
  })

  it('keeps note-taking editable during generation and rejects the resulting stale proposal', async () => {
    const backend = mockBackend()
    const pending = deferred<void>()
    backend.delayGeneration(pending.promise)
    mount()
    await advance()
    await generate()
    const original = screen.getByRole('textbox', {
      name: fr.title,
    }) as HTMLTextAreaElement
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
      (
        screen.getByRole('button', {
          name: fr.enrichment.apply,
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    expect(
      backend.commands().filter((request) => request.action === 'apply')
    ).toEqual([])
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
      screen.queryByRole('textbox', { name: fr.enrichment.preview })
    ).toBeNull()
    expect(
      (screen.getByRole('textbox', { name: fr.title }) as HTMLTextAreaElement)
        .value
    ).toBe(savedNote().content)
    expect(backend.commands().map((request) => request.action)).toEqual([
      'generate',
      'discard',
    ])
  })

  it('keeps an old proposal from enabling cancellation during regeneration', async () => {
    const backend = mockBackend()
    backend.setState(
      enrichmentState({ state_version: 1, proposal: readyProposal() })
    )
    mount()
    await advance()
    const pending = deferred<void>()
    backend.delayGeneration(pending.promise)
    await generate()
    expect(
      screen.queryByRole('button', { name: fr.enrichment.cancel })
    ).toBeNull()
    expect(
      (screen.getByRole('textbox', { name: fr.title }) as HTMLTextAreaElement)
        .readOnly
    ).toBe(false)
    pending.resolve()
    await advance()
    expect(
      screen.getByRole('button', { name: fr.enrichment.cancel })
    ).toBeTruthy()
    expect(backend.commands().map((command) => command.action)).toEqual([
      'generate',
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
      expect(
        (screen.getByRole('textbox', { name: fr.title }) as HTMLTextAreaElement)
          .readOnly
      ).toBe(false)
      expect(screen.queryByText(fr.enrichment.discarding)).toBeNull()
      expect(screen.queryByText(fr.enrichment.cancelled)).toBeNull()
      if (when === 'after') {
        expect(screen.getByText(fr.enrichment.proposalRemoved)).toBeTruthy()
        expect(
          screen.queryByRole('textbox', { name: fr.enrichment.preview })
        ).toBeNull()
      } else {
        expect(screen.getByRole('alert').textContent).toBe(
          fr.enrichment.errors.discard
        )
        fireEvent.click(
          screen.getByRole('button', { name: fr.enrichment.retryCancel })
        )
        await advance()
        expect(screen.getByText(fr.enrichment.cancelled)).toBeTruthy()
      }
    }
  )

  it.each(['apply', 'undo'] as const)(
    'offers an accessible explicit replay of an uncertain %s and unlocks after confirmation',
    async (action) => {
      const backend = mockBackend()
      if (action === 'undo')
        backend.setState(
          enrichmentState({
            last_applied: {
              proposal_id: proposalId,
              revision: 1,
              digest: 'c'.repeat(64),
              undo_available: true,
            },
          })
        )
      mount()
      await advance()
      if (action === 'apply') await generate()
      backend.failNextMutation()
      fireEvent.click(
        screen.getByRole('button', { name: fr.enrichment[action] })
      )
      await advance()
      const original = screen.getByRole('textbox', {
        name: fr.title,
      }) as HTMLTextAreaElement
      expect(original.readOnly).toBe(true)
      const retry = screen.getByRole('button', {
        name: fr.enrichment.retryMutation,
      })
      expect(
        document.getElementById(retry.getAttribute('aria-describedby')!)
          ?.textContent
      ).toBe(fr.enrichment.retryMutationHelp)
      fireEvent.click(screen.getByRole('button', { name: fr.enrichment.check }))
      await advance()
      expect(
        backend.commands().filter((command) => command.action === action)
      ).toHaveLength(1)
      const pending = deferred<void>()
      backend.delayMutation(pending.promise)
      fireEvent.click(
        screen.getByRole('button', { name: fr.enrichment.retryMutation })
      )
      await advance()
      expect(
        screen.queryByRole('button', { name: fr.enrichment.retryMutation })
      ).toBeNull()
      expect(original.readOnly).toBe(true)
      pending.resolve()
      await advance()
      const commands = backend
        .commands()
        .filter((command) => command.action === action)
      expect(commands).toHaveLength(2)
      expect(commands[1]).toEqual(commands[0])
      expect(original.readOnly).toBe(false)
      expect(original.value).toBe(backend.getNote().content)
      expect(
        screen.getByText(
          action === 'apply' ? fr.enrichment.applied : fr.enrichment.undone
        )
      ).toBeTruthy()
    }
  )

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

  it('keeps undo unavailable immediately after typing, including before autosave', async () => {
    const backend = mockBackend()
    backend.setState(
      enrichmentState({
        last_applied: {
          proposal_id: proposalId,
          revision: 1,
          digest: 'c'.repeat(64),
          undo_available: true,
        },
      })
    )
    mount()
    await advance()
    const undo = screen.getByRole('button', {
      name: fr.enrichment.undo,
    }) as HTMLButtonElement
    expect(undo.disabled).toBe(false)
    fireEvent.change(screen.getByRole('textbox', { name: fr.title }), {
      target: { value: 'Nouvelle saisie' },
    })
    expect(undo.disabled).toBe(true)
    expect(screen.getByText(fr.enrichment.undoUnavailable)).toBeTruthy()
    fireEvent.click(undo)
    await advance(800)
    expect(backend.getNote().content).toBe('Nouvelle saisie')
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
    const preview = screen.getByRole('textbox', { name: en.enrichment.preview })
    expect(preview.getAttribute('aria-describedby')).toBeTruthy()
    fireEvent.change(preview, { target: { value: ' ' } })
    expect(preview.getAttribute('aria-invalid')).toBe('true')
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
