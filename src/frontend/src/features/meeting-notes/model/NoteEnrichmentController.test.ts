import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PersonalNotesApi, PersonalNote } from '../api/personalNotesApi'
import type {
  NoteEnrichmentApi,
  NoteEnrichmentState,
} from '../api/noteEnrichmentTypes'
import { NoteEnrichmentRequestError } from '../api/noteEnrichmentApi'
import {
  enrichmentState,
  readyProposal,
  savedNote,
  deferred,
  meetingRef,
  proposalId,
} from '../test/enrichmentFixtures'
import { PersonalNotesController } from './PersonalNotesController'
import { NoteEnrichmentController } from './NoteEnrichmentController'

const cleanups: (() => void)[] = []
async function setup(server = enrichmentState()) {
  const readNote = vi
    .fn<PersonalNotesApi['read']>()
    .mockResolvedValue(savedNote())
  const writeNote = vi
    .fn<PersonalNotesApi['write']>()
    .mockImplementation(async (text, revision) => savedNote(text, revision + 1))
  const notes = new PersonalNotesController({
    read: readNote,
    write: writeNote,
  })
  notes.start()
  await notes.retry()
  const read = vi
    .fn<NoteEnrichmentApi['read']>()
    .mockImplementation(async () => server)
  const command = vi
    .fn<NoteEnrichmentApi['command']>()
    .mockImplementation(async (request) => {
      if (request.action === 'generate')
        server = enrichmentState({
          state_version: server.state_version + 1,
          proposal: readyProposal({
            command_id: request.command_id,
            base_revision: request.expected_revision,
          }),
        })
      if (request.action === 'discard')
        server = {
          ...server,
          state_version: server.state_version + 1,
          proposal: null,
        }
      return server
    })
  const enrichment = new NoteEnrichmentController(
    { read, command },
    notes,
    meetingRef
  )
  cleanups.push(() => {
    enrichment.stop()
    notes.stop()
  })
  enrichment.start()
  await vi.advanceTimersByTimeAsync(0)
  return {
    notes,
    enrichment,
    readNote,
    writeNote,
    read,
    command,
    setServer: (state: NoteEnrichmentState) => {
      server = state
    },
  }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  cleanups.forEach((stop) => stop())
  cleanups.length = 0
  vi.useRealTimers()
})

describe('enrichment and the single autosave authority', () => {
  it('refreshes eligibility again if autosave finishes during a background read', async () => {
    const { notes, enrichment, read, setServer } = await setup()
    const background = deferred<NoteEnrichmentState>()
    read.mockReturnValueOnce(background.promise)
    const refreshing = enrichment.refresh()
    notes.setContent('a newly saved draft')
    await notes.flush()
    setServer(enrichmentState())
    background.resolve(
      enrichmentState({ eligible: false, reason: 'empty_note' })
    )
    await refreshing
    await vi.advanceTimersByTimeAsync(0)
    expect(enrichment.getSnapshot().server?.eligible).toBe(true)
  })
  it('does not let an older background read reset an in-flight mutation', async () => {
    const { enrichment, read, command } = await setup(
      enrichmentState({ state_version: 1, proposal: readyProposal() })
    )
    const background = deferred<NoteEnrichmentState>()
    const applied = deferred<NoteEnrichmentState>()
    read.mockReturnValueOnce(background.promise)
    command.mockReturnValueOnce(applied.promise)
    const refreshing = enrichment.refresh()
    const applying = enrichment.apply()
    await vi.advanceTimersByTimeAsync(0)
    background.resolve(
      enrichmentState({ state_version: 1, proposal: readyProposal() })
    )
    await refreshing
    expect(enrichment.getSnapshot().phase).toBe('applying')
    applied.reject(new NoteEnrichmentRequestError(409))
    await applying
  })

  it('preserves edited additions when another tab replaces the proposal', async () => {
    const { enrichment, setServer, command } = await setup(
      enrichmentState({ state_version: 1, proposal: readyProposal() })
    )
    enrichment.setAdditions('My edited additions must remain available to copy')
    setServer(
      enrichmentState({
        state_version: 2,
        proposal: readyProposal({ id: 'proposal_other0123456789' }),
      })
    )
    await enrichment.refresh()
    expect(enrichment.getSnapshot().additions).toBe(
      'My edited additions must remain available to copy'
    )
    expect(enrichment.getSnapshot().stale).toBe(true)
    await enrichment.apply()
    expect(command).not.toHaveBeenCalled()
  })

  it('waits for all in-flight autosaves and generates only from the confirmed revision', async () => {
    const { notes, enrichment, writeNote, command } = await setup()
    const first = deferred<PersonalNote>()
    const second = deferred<PersonalNote>()
    writeNote
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    notes.setContent('first draft')
    await vi.advanceTimersByTimeAsync(800)
    const generating = enrichment.generate()
    notes.setContent('latest draft')
    first.resolve(savedNote('first draft', 2))
    await vi.advanceTimersByTimeAsync(0)
    expect(command).not.toHaveBeenCalled()
    second.resolve(savedNote('latest draft', 3))
    await generating
    expect(command).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ action: 'generate', expected_revision: 3 }),
      expect.any(AbortSignal)
    )
    expect(notes.getSnapshot().content).toBe('latest draft')
  })

  it('never dispatches after a failed autosave', async () => {
    const { notes, enrichment, writeNote, command } = await setup()
    writeNote.mockRejectedValueOnce(new Error('offline'))
    notes.setContent('unconfirmed draft')
    await enrichment.generate()
    expect(command).not.toHaveBeenCalled()
    expect(notes.getSnapshot().content).toBe('unconfirmed draft')
  })

  it('deduplicates clicks and keeps edits made while generation is in flight', async () => {
    const { notes, enrichment, command } = await setup()
    const result = deferred<NoteEnrichmentState>()
    command.mockReturnValueOnce(result.promise)
    const generating = enrichment.generate()
    await vi.advanceTimersByTimeAsync(0)
    await enrichment.generate()
    notes.setContent('later private edit')
    result.resolve(
      enrichmentState({ state_version: 1, proposal: readyProposal() })
    )
    await generating
    expect(command).toHaveBeenCalledOnce()
    expect(enrichment.getSnapshot().stale).toBe(true)
    await enrichment.apply()
    expect(command).toHaveBeenCalledOnce()
    expect(notes.getSnapshot().content).toBe('later private edit')
  })

  it('does not clear local staleness when a generating proposal becomes ready through polling', async () => {
    const { notes, enrichment, setServer } = await setup(
      enrichmentState({
        state_version: 1,
        proposal: readyProposal({ status: 'generating' }),
      })
    )
    notes.setContent('edited and not saved')
    setServer(enrichmentState({ state_version: 2, proposal: readyProposal() }))
    await enrichment.refresh()
    expect(enrichment.getSnapshot().stale).toBe(true)
    expect(notes.getSnapshot().content).toBe('edited and not saved')
  })

  it('refuses cancellation before a server proposal ID exists, including during autosave', async () => {
    const { notes, enrichment, writeNote, command } = await setup()
    const saved = deferred<PersonalNote>()
    writeNote.mockReturnValueOnce(saved.promise)
    notes.setContent('pending draft')
    const generating = enrichment.generate()
    expect(enrichment.canDiscard()).toBe(false)
    await enrichment.discard()
    expect(enrichment.getSnapshot().phase).toBe('generating')
    expect(enrichment.getSnapshot().notice).toBeNull()
    saved.resolve(savedNote('pending draft', 2))
    await generating
    expect(command.mock.calls.map(([request]) => request.action)).toEqual([
      'generate',
    ])
    expect(notes.getSnapshot().content).toBe('pending draft')
    expect(enrichment.canDiscard()).toBe(true)
  })

  it('never uses an old proposal ID to cancel a new generation command', async () => {
    const { enrichment, command } = await setup(
      enrichmentState({ state_version: 1, proposal: readyProposal() })
    )
    const pending = deferred<NoteEnrichmentState>()
    command.mockReturnValueOnce(pending.promise)
    const generating = enrichment.generate()
    await vi.advanceTimersByTimeAsync(0)
    expect(command.mock.calls[0][0].command_id).not.toBe(
      readyProposal().command_id
    )
    expect(enrichment.canDiscard()).toBe(false)
    await enrichment.discard()
    expect(command).toHaveBeenCalledOnce()
    expect(enrichment.getSnapshot().phase).toBe('generating')
    pending.reject(new NoteEnrichmentRequestError())
    await generating
    await enrichment.refresh()
    expect(enrichment.canDiscard()).toBe(false)
    expect(enrichment.getSnapshot().cancellationPending).toBe(false)
  })

  it('locks apply through uncertain outcomes, retries only reads, and adopts the current note', async () => {
    const { notes, enrichment, command, readNote, setServer, writeNote } =
      await setup(
        enrichmentState({ state_version: 1, proposal: readyProposal() })
      )
    command.mockRejectedValueOnce(new NoteEnrichmentRequestError())
    await enrichment.apply()
    expect(notes.getSnapshot().isMutationLocked).toBe(true)
    notes.setContent('must not enter a locked field')
    expect(notes.getSnapshot().content).toBe(savedNote().content)
    expect(await notes.flush()).toBe(false)
    expect(await notes.retry()).toBe(false)
    const applied = command.mock.calls[0][0]
    const receipt = {
      command_id: applied.command_id,
      action: 'apply' as const,
      revision: 2,
      digest: 'c'.repeat(64),
      idempotent_replay: false,
    }
    setServer(
      enrichmentState({
        state_version: 2,
        receipt,
        last_applied: {
          proposal_id: proposalId,
          revision: 2,
          digest: receipt.digest,
          undo_available: true,
        },
      })
    )
    readNote.mockRejectedValueOnce(new Error('read unavailable'))
    await enrichment.refresh()
    expect(notes.getSnapshot().isMutationLocked).toBe(true)
    // A newer server edit wins over the historical apply receipt.
    readNote.mockResolvedValue(savedNote('current note from another tab', 3))
    await enrichment.refresh()
    expect(notes.getSnapshot()).toMatchObject({
      content: 'current note from another tab',
      confirmedRevision: 3,
      isMutationLocked: false,
      hasUnsavedChanges: false,
    })
    expect(command).toHaveBeenCalledOnce()
    expect(writeNote).not.toHaveBeenCalled()
    expect(enrichment.canUndo()).toBe(false)
  })

  it.each(['apply', 'undo'] as const)(
    'explicitly replays the identical %s after a negative read, without an automatic retry',
    async (action) => {
      const initial = enrichmentState({
        state_version: 3,
        proposal: action === 'apply' ? readyProposal() : null,
        last_applied:
          action === 'undo'
            ? {
                proposal_id: proposalId,
                revision: 1,
                digest: 'c'.repeat(64),
                undo_available: true,
              }
            : null,
      })
      const { notes, enrichment, command, readNote, setServer } =
        await setup(initial)
      enrichment.setAdditions('Approved additions exactly as typed\n')
      command.mockRejectedValueOnce(new NoteEnrichmentRequestError())
      await enrichment[action]()
      const firstCommand = command.mock.calls[0][0]
      expect(enrichment.getSnapshot()).toMatchObject({
        phase: 'reconciling',
        canRetryMutation: true,
      })
      expect(notes.getSnapshot().isMutationLocked).toBe(true)
      await enrichment.refresh()
      await vi.advanceTimersByTimeAsync(6000)
      expect(command).toHaveBeenCalledOnce()
      const result = deferred<NoteEnrichmentState>()
      command.mockReturnValueOnce(result.promise)
      const retrying = enrichment.retryMutation()
      await enrichment.retryMutation()
      await enrichment.refresh()
      expect(command).toHaveBeenCalledTimes(2)
      expect(command.mock.calls[1][0]).toBe(firstCommand)
      expect(notes.getSnapshot().isMutationLocked).toBe(true)
      const current = enrichmentState({
        state_version: 4,
        receipt: {
          command_id: firstCommand.command_id,
          action,
          revision: 2,
          digest: 'd'.repeat(64),
          idempotent_replay: false,
        },
      })
      setServer(current)
      readNote.mockResolvedValue(
        savedNote('current server text, never the old receipt', 3)
      )
      result.resolve(current)
      await retrying
      expect(notes.getSnapshot()).toMatchObject({
        isMutationLocked: false,
        content: 'current server text, never the old receipt',
        confirmedRevision: 3,
      })
      expect(enrichment.getSnapshot().canRetryMutation).toBe(false)
      expect(enrichment.getSnapshot().notice).toBe(
        action === 'apply' ? 'applied' : 'undone'
      )
    }
  )

  it('does not enter cancellation or block undo after a generation never reaches Core', async () => {
    const { notes, enrichment, command, read } = await setup(
      enrichmentState({
        last_applied: {
          proposal_id: proposalId,
          revision: 1,
          digest: 'c'.repeat(64),
          undo_available: true,
        },
      })
    )
    const generation = deferred<NoteEnrichmentState>()
    command.mockReturnValueOnce(generation.promise)
    const generating = enrichment.generate()
    await vi.advanceTimersByTimeAsync(0)
    await enrichment.discard()
    generation.reject(new NoteEnrichmentRequestError())
    await generating
    await enrichment.refresh()
    const reads = read.mock.calls.length
    await vi.advanceTimersByTimeAsync(6000)
    expect(read).toHaveBeenCalledTimes(reads)
    expect(command).toHaveBeenCalledOnce()
    expect(enrichment.getSnapshot()).toMatchObject({
      phase: 'idle',
      cancellationPending: false,
      notice: null,
    })
    expect(enrichment.canUndo()).toBe(true)
    expect(notes.getSnapshot().isMutationLocked).toBe(false)
  })

  it.each(['absent', 'replaced', 'applied'] as const)(
    'reconciles a lost discard response when the target is %s at an advanced version',
    async (outcome) => {
      const { notes, enrichment, command, setServer, readNote } = await setup(
        enrichmentState({ state_version: 2, proposal: readyProposal() })
      )
      const next = enrichmentState({ state_version: 3 })
      if (outcome === 'replaced')
        next.proposal = readyProposal({
          id: 'proposal_new0123456789',
          command_id: 'command_new0123456789',
          text: 'A new proposal',
        })
      if (outcome === 'applied') {
        next.last_applied = {
          proposal_id: proposalId,
          revision: 2,
          digest: 'c'.repeat(64),
          undo_available: true,
        }
        notes.setContent('Later local typing must survive')
        readNote.mockResolvedValue(savedNote('Applied in another tab', 2))
      }
      command.mockImplementationOnce(async () => {
        setServer(next)
        throw new NoteEnrichmentRequestError()
      })
      await enrichment.discard()
      expect(enrichment.getSnapshot()).toMatchObject({
        phase: 'idle',
        cancellationPending: false,
        error: null,
        notice:
          outcome === 'applied'
            ? 'proposalAppliedElsewhere'
            : 'proposalRemoved',
      })
      expect(command).toHaveBeenCalledOnce()
      if (outcome === 'replaced') {
        expect(enrichment.getSnapshot().dismissed).toBe(false)
        expect(enrichment.getSnapshot().additions).toBe('A new proposal')
      }
      if (outcome === 'applied') {
        expect(notes.getSnapshot().content).toBe(
          'Later local typing must survive'
        )
        expect(notes.getSnapshot().conflict?.content).toBe(
          'Applied in another tab'
        )
      }
    }
  )

  it.each(['unchanged', 'older', 'still-present', 'read-error'] as const)(
    'exits uncertain cancellation with an explicit retry after %s',
    async (outcome) => {
      const initial = enrichmentState({
        state_version: 2,
        proposal: readyProposal(),
      })
      const { enrichment, command, read, setServer } = await setup(initial)
      enrichment.setAdditions('Edited preview survives failed cancellation')
      command.mockImplementationOnce(async () => {
        if (outcome === 'read-error')
          read.mockRejectedValueOnce(new NoteEnrichmentRequestError())
        if (outcome === 'older')
          setServer(enrichmentState({ state_version: 1 }))
        if (outcome === 'still-present')
          setServer({ ...initial, state_version: 3 })
        throw new NoteEnrichmentRequestError()
      })
      await enrichment.discard()
      expect(enrichment.getSnapshot()).toMatchObject({
        phase: 'idle',
        cancellationPending: false,
        notice: null,
        error: 'discard',
        dismissed: false,
      })
      expect(enrichment.getSnapshot().additions).toBe(
        'Edited preview survives failed cancellation'
      )
      expect(enrichment.canDiscard()).toBe(true)
      const reads = read.mock.calls.length
      await vi.advanceTimersByTimeAsync(6000)
      expect(read).toHaveBeenCalledTimes(reads)
      expect(command).toHaveBeenCalledOnce()
      setServer({
        ...initial,
        state_version: enrichment.getSnapshot().server!.state_version,
      })
      await enrichment.discard()
      expect(command).toHaveBeenCalledTimes(2)
      expect(enrichment.getSnapshot().notice).toBe('cancelled')
    }
  )

  it('keeps a definitive apply failure editable after reading current authority', async () => {
    const { notes, enrichment, command, readNote } = await setup(
      enrichmentState({ state_version: 1, proposal: readyProposal() })
    )
    command.mockRejectedValueOnce(new NoteEnrichmentRequestError(409))
    readNote.mockResolvedValue(savedNote('other tab won', 2))
    await enrichment.apply()
    expect(notes.getSnapshot()).toMatchObject({
      content: 'other tab won',
      isMutationLocked: false,
    })
    expect(enrichment.getSnapshot().error).toBe('mutation')
    expect(enrichment.getSnapshot().notice).toBeNull()
  })

  it('restores undo availability after reload and refuses after any subsequent local edit', async () => {
    const state = enrichmentState({
      last_applied: {
        proposal_id: proposalId,
        revision: 1,
        digest: 'c'.repeat(64),
        undo_available: true,
      },
    })
    const { notes, enrichment, command } = await setup(state)
    expect(enrichment.canUndo()).toBe(true)
    notes.setContent('later typing')
    await notes.flush()
    await enrichment.undo()
    expect(command).not.toHaveBeenCalled()
    expect(notes.getSnapshot().content).toBe('later typing')
  })

  it('undoes only under the confirmed revision and reads the restored note', async () => {
    const { notes, enrichment, command, readNote, setServer } = await setup(
      enrichmentState({
        state_version: 3,
        last_applied: {
          proposal_id: proposalId,
          revision: 1,
          digest: 'c'.repeat(64),
          undo_available: true,
        },
      })
    )
    command.mockImplementationOnce(async (request) => {
      const state = enrichmentState({
        state_version: 4,
        receipt: {
          command_id: request.command_id,
          action: 'undo',
          revision: 2,
          digest: 'd'.repeat(64),
          idempotent_replay: false,
        },
      })
      setServer(state)
      readNote.mockResolvedValue(savedNote('exact original', 2))
      return state
    })
    await enrichment.undo()
    expect(command.mock.calls[0][0]).toMatchObject({
      action: 'undo',
      expected_state_version: 3,
      expected_revision: 1,
      proposal_id: proposalId,
    })
    expect(notes.getSnapshot().content).toBe('exact original')
    expect(enrichment.getSnapshot().notice).toBe('undone')
  })

  it('ignores pending responses after the meeting or identity scope stops', async () => {
    const { enrichment, command } = await setup()
    const result = deferred<NoteEnrichmentState>()
    command.mockReturnValueOnce(result.promise)
    const generating = enrichment.generate()
    await vi.advanceTimersByTimeAsync(0)
    const signal = command.mock.calls[0][1]
    enrichment.stop()
    result.resolve(
      enrichmentState({ state_version: 1, proposal: readyProposal() })
    )
    await generating
    expect(signal.aborted).toBe(true)
    expect(enrichment.getSnapshot().server?.proposal).toBeNull()
  })
})
