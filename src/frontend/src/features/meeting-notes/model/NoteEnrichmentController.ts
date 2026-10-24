import { NoteEnrichmentRequestError } from '../api/noteEnrichmentApi'
import {
  validEnrichmentText,
  withinEnrichmentBudget,
} from '../api/parseNoteEnrichment'
import type {
  ApprovedEnrichmentItem,
  NoteEnrichmentApi,
  NoteEnrichmentCommand,
  NoteEnrichmentState,
  EnrichmentProposal,
} from '../api/noteEnrichmentTypes'
import type { PersonalNotesController } from './PersonalNotesController'

type Phase =
  | 'loading'
  | 'idle'
  | 'generating'
  | 'discarding'
  | 'applying'
  | 'reconciling'
/** The user's decision on one proposed complement, at the same position. */
export interface EnrichmentReviewItem {
  text: string
  kept: boolean
}
export interface EnrichmentViewState {
  server: NoteEnrichmentState | null
  proposal: EnrichmentProposal | null
  phase: Phase
  review: EnrichmentReviewItem[]
  /** At least one kept text differs from the proposal. */
  modified: boolean
  stale: boolean
  dismissed: boolean
  error: 'load' | 'generate' | 'discard' | 'mutation' | null
  notice:
    | 'applied'
    | 'cancelled'
    | 'proposalRemoved'
    | 'proposalAppliedElsewhere'
    | null
  unavailable: boolean
  canRetryMutation: boolean
  cancellationPending: boolean
}

interface PendingMutation {
  command: Extract<NoteEnrichmentCommand, { action: 'apply' }>
  settled: boolean
  applied: boolean
  minimumRevision: number
}

interface CancellationTarget {
  proposalId: string
  commandId: string
  stateVersion: number
}

/** Every proposed complement starts kept, with the proposed text. */
const reviewOf = (
  proposal: EnrichmentProposal | null
): EnrichmentReviewItem[] =>
  (proposal?.items ?? []).map((item) => ({ text: item.text, kept: true }))

/** The kept complements, by proposal position, as the apply command sends them. */
export function approvedItems(
  review: readonly EnrichmentReviewItem[]
): ApprovedEnrichmentItem[] {
  return review.flatMap((entry, index) =>
    entry.kept ? [{ index, text: entry.text }] : []
  )
}

/** At least one complement, each valid, within the shared budget. */
export function validApproval(items: readonly ApprovedEnrichmentItem[]) {
  return (
    items.length > 0 &&
    items.every((item) => validEnrichmentText(item.text)) &&
    withinEnrichmentBudget(items)
  )
}

/** Owns the proposal only. The existing notes controller owns every note write. */
export class NoteEnrichmentController {
  private state: EnrichmentViewState = {
    server: null,
    proposal: null,
    phase: 'loading',
    review: [],
    modified: false,
    stale: false,
    dismissed: false,
    error: null,
    notice: null,
    unavailable: false,
    canRetryMutation: false,
    cancellationPending: false,
  }
  private listeners = new Set<() => void>()
  private active = false
  private scope = 0
  private generationToken = 0
  private generationCommandId: string | null = null
  private cancellationTarget: CancellationTarget | null = null
  private proposalEditVersion = 0
  private noteEditVersion = 0
  private readRevision: number | null = null
  private reading = false
  private reconciling = false
  private sendingMutation = false
  private pendingMutation: PendingMutation | null = null
  private requestController = new AbortController()
  private unsubscribe: (() => void) | undefined
  private poll: ReturnType<typeof setTimeout> | undefined

  constructor(
    private readonly api: NoteEnrichmentApi,
    private readonly notes: PersonalNotesController,
    private readonly meetingRef: string
  ) {}

  getSnapshot = () => this.state
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  start = () => {
    this.active = true
    this.requestController = new AbortController()
    this.unsubscribe = this.notes.subscribe(this.notesChanged)
    this.noteEditVersion = this.notes.getSnapshot().editVersion
    void this.refresh()
  }

  stop = () => {
    this.active = false
    this.scope += 1
    this.generationToken += 1
    this.reading = false
    this.requestController.abort()
    this.unsubscribe?.()
    clearTimeout(this.poll)
  }

  setItemText = (index: number, text: string) => {
    this.updateReview(index, { text })
  }

  setItemKept = (index: number, kept: boolean) => {
    this.updateReview(index, { kept })
  }

  /** Back to the proposal as generated: every complement kept, unedited. */
  resetReview = () => {
    if (!this.active || this.state.phase !== 'idle') return
    this.update({ review: reviewOf(this.state.proposal), modified: false })
  }

  private updateReview(index: number, change: Partial<EnrichmentReviewItem>) {
    if (!this.active || this.state.phase !== 'idle') return
    const review = this.state.review.map((entry, at) =>
      at === index ? { ...entry, ...change } : entry
    )
    const proposed = this.state.proposal?.items ?? []
    this.update({
      review,
      modified: review.some(
        (entry, at) => entry.kept && entry.text !== proposed[at]?.text
      ),
    })
  }

  refresh = async () => {
    if (!this.active || this.reading) return
    if (this.pendingMutation) return this.reconcileMutation()
    if (this.state.cancellationPending) return this.verifyCancellation()
    if (this.state.phase !== 'idle' && this.state.phase !== 'loading') return
    const scope = this.scope
    const token = this.generationToken
    this.reading = true
    this.readRevision = this.notes.getSnapshot().confirmedRevision
    try {
      const server = await this.api.read(this.requestController.signal)
      if (!this.isReadCurrent(scope, token)) return
      this.acceptServer(server)
      if (this.isReadCurrent(scope, token))
        this.update({ phase: 'idle', error: null, unavailable: false })
    } catch (error) {
      if (this.isReadCurrent(scope, token)) {
        const unavailable =
          error instanceof NoteEnrichmentRequestError &&
          [401, 403, 404].includes(error.statusCode ?? 0)
        this.update({
          phase: 'idle',
          error: unavailable ? null : 'load',
          unavailable,
        })
      }
    } finally {
      if (this.isCurrent(scope)) {
        this.reading = false
        const note = this.notes.getSnapshot()
        if (
          note.status === 'ready' &&
          !note.hasUnsavedChanges &&
          note.confirmedRevision !== this.readRevision
        )
          void this.refresh()
        else this.schedulePoll()
      }
    }
  }

  generate = async () => {
    if (!this.active || this.state.phase !== 'idle' || this.pendingMutation)
      return
    const scope = this.scope
    const token = ++this.generationToken
    this.generationCommandId = null
    this.update({
      phase: 'generating',
      error: null,
      notice: null,
      dismissed: true,
    })
    const saved = await this.notes.flush()
    if (!this.isGenerationCurrent(scope, token)) return
    const baseline = this.notes.getConfirmedSnapshot()
    if (!saved || !baseline || !baseline.content.trim()) {
      this.update({ phase: 'idle', error: 'generate', dismissed: false })
      return
    }
    const editVersion = this.notes.getSnapshot().editVersion
    try {
      const server = await this.api.read(this.requestController.signal)
      if (!this.isGenerationCurrent(scope, token)) return
      this.acceptServer(server)
      if (
        !server.eligible ||
        this.notes.getSnapshot().editVersion !== editVersion ||
        this.notes.getConfirmedSnapshot()?.revision !== baseline.revision
      ) {
        this.update({ phase: 'idle', error: 'generate', dismissed: false })
        return
      }
      const command: NoteEnrichmentCommand = {
        ...this.envelope(server),
        action: 'generate',
        expected_revision: baseline.revision,
      }
      this.generationCommandId = command.command_id
      this.proposalEditVersion = editVersion
      const result = await this.api.command(
        command,
        this.requestController.signal
      )
      if (!this.isCurrent(scope)) return
      if (token !== this.generationToken) {
        if (this.state.cancellationPending) await this.refresh()
        return
      }
      this.acceptServer(result, editVersion)
      this.generationCommandId = null
      this.update({ phase: 'idle', dismissed: false })
    } catch {
      if (this.isGenerationCurrent(scope, token))
        this.update({ phase: 'idle', error: 'generate', dismissed: false })
    } finally {
      if (this.isCurrent(scope)) this.schedulePoll()
    }
  }

  canDiscard = () => {
    const proposal = this.state.server?.proposal
    if (
      !this.active ||
      this.pendingMutation ||
      !proposal ||
      this.state.cancellationPending
    )
      return false
    if (this.state.phase !== 'idle' && this.state.phase !== 'generating')
      return false
    if (this.state.phase === 'generating' && !this.generationCommandId)
      return false
    if (
      this.generationCommandId &&
      proposal.command_id !== this.generationCommandId
    )
      return false
    return proposal.id === this.state.proposal?.id
  }

  discard = async () => {
    const server = this.state.server
    const proposal = server?.proposal
    if (!this.canDiscard() || !server || !proposal) return
    this.cancellationTarget = {
      proposalId: proposal.id,
      commandId: proposal.command_id,
      stateVersion: server.state_version,
    }
    this.generationToken += 1
    clearTimeout(this.poll)
    this.update({
      phase: 'discarding',
      dismissed: true,
      notice: null,
      error: null,
      cancellationPending: true,
    })
    await this.verifyCancellation()
  }

  private async verifyCancellation() {
    if (!this.active || this.reading || !this.state.cancellationPending) return
    const target = this.cancellationTarget
    if (!target) return
    const scope = this.scope
    this.reading = true
    try {
      const current = await this.api.read(this.requestController.signal)
      if (!this.isCurrent(scope)) return
      if (this.targetRemoved(current, target))
        await this.finishCancellation(current, target, false)
      else if (
        current.state_version >= target.stateVersion &&
        this.matchesTarget(current, target)
      )
        await this.discardProposal(current, target)
      else this.failCancellation(current)
    } catch {
      if (this.isCurrent(scope)) this.failCancellation()
    } finally {
      if (this.isCurrent(scope)) {
        this.reading = false
        this.schedulePoll()
      }
    }
  }

  retryMutation = async () => {
    const pending = this.pendingMutation
    if (
      !this.active ||
      !pending ||
      !this.state.canRetryMutation ||
      this.state.phase !== 'reconciling' ||
      this.reconciling
    )
      return
    this.update({ phase: 'applying', canRetryMutation: false })
    await this.sendMutation(pending)
  }

  /** Adds the kept complements; going back afterwards is the note history's job. */
  apply = async () => {
    if (
      !this.active ||
      this.state.phase !== 'idle' ||
      !this.state.server ||
      this.pendingMutation ||
      this.state.stale ||
      this.state.dismissed ||
      this.state.server.proposal?.status !== 'ready' ||
      !validApproval(approvedItems(this.state.review))
    )
      return
    const scope = this.scope
    this.generationToken += 1
    this.update({ phase: 'applying', error: null, notice: null })
    const saved = await this.notes.flush()
    if (!this.isCurrent(scope)) return
    const baseline = this.notes.getConfirmedSnapshot()
    const server = this.state.server
    const proposal = server.proposal
    if (
      !saved ||
      !baseline ||
      !proposal ||
      baseline.revision !== proposal.base_revision ||
      this.state.stale ||
      !this.notes.beginExternalMutation(baseline.revision)
    ) {
      this.update({ phase: 'idle', error: 'mutation' })
      return
    }
    const pending: PendingMutation = {
      command: {
        ...this.envelope(server),
        action: 'apply',
        proposal_id: proposal.id,
        expected_revision: baseline.revision,
        items: approvedItems(this.state.review),
      },
      settled: false,
      applied: false,
      minimumRevision: baseline.revision,
    }
    this.pendingMutation = pending
    await this.sendMutation(pending)
  }

  private async sendMutation(pending: PendingMutation) {
    const scope = this.scope
    const { command } = pending
    this.sendingMutation = true
    try {
      const result = await this.api.command(
        command,
        this.requestController.signal
      )
      if (!this.isCurrent(scope)) return
      pending.settled = true
      pending.applied = result.receipt?.command_id === command.command_id
      if (pending.applied && result.receipt)
        pending.minimumRevision = result.receipt.revision
    } catch (error) {
      if (!this.isCurrent(scope)) return
      pending.settled =
        error instanceof NoteEnrichmentRequestError &&
        error.statusCode !== undefined &&
        error.statusCode >= 400 &&
        error.statusCode < 500
    } finally {
      this.sendingMutation = false
    }
    if (this.isCurrent(scope)) await this.reconcileMutation()
  }

  /** Uncertain writes stay locked. Retrying this method only reads, never resends. */
  private async reconcileMutation() {
    const pending = this.pendingMutation
    if (!pending || this.reconciling || this.sendingMutation || !this.active)
      return
    const scope = this.scope
    this.reconciling = true
    this.update({ phase: 'reconciling', canRetryMutation: false })
    try {
      const current = await this.api.read(this.requestController.signal)
      if (!this.isCurrent(scope)) return
      const receipt = current.receipt
      if (receipt?.command_id === pending.command.command_id) {
        pending.settled = true
        pending.applied = true
        pending.minimumRevision = Math.max(
          pending.minimumRevision,
          receipt.revision
        )
      }
      // A newer state also fences a delayed command carrying the old CAS version.
      if (current.state_version > pending.command.expected_state_version)
        pending.settled = true
      if (!pending.settled) {
        // Absence is not proof of failure: only an explicit identical replay is safe.
        this.update({ canRetryMutation: true })
        return
      }
      if (
        !(await this.notes.reconcileExternalMutation(pending.minimumRevision))
      )
        return
      if (!this.isCurrent(scope)) return
      this.pendingMutation = null
      if (pending.applied) this.update({ modified: false })
      this.acceptServer(current)
      this.update({
        phase: 'idle',
        notice: pending.applied ? 'applied' : null,
        error: pending.applied ? null : 'mutation',
        canRetryMutation: false,
      })
    } catch {
      // The original remains readable; a separate retry action rechecks authority.
    } finally {
      if (this.isCurrent(scope)) this.reconciling = false
    }
  }

  private async discardProposal(
    server: NoteEnrichmentState,
    target: CancellationTarget
  ) {
    const scope = this.scope
    let result: NoteEnrichmentState
    let acknowledged = true
    try {
      result = await this.api.command(
        {
          ...this.envelope(server),
          action: 'discard',
          proposal_id: target.proposalId,
        },
        this.requestController.signal
      )
    } catch {
      if (!this.isCurrent(scope)) return
      acknowledged = false
      result = await this.api.read(this.requestController.signal)
    }
    if (this.isCurrent(scope)) {
      if (this.targetRemoved(result, target))
        await this.finishCancellation(result, target, acknowledged)
      else this.failCancellation(result)
    }
  }

  private matchesTarget(
    server: NoteEnrichmentState,
    target: CancellationTarget
  ) {
    return (
      server.proposal?.id === target.proposalId &&
      server.proposal.command_id === target.commandId
    )
  }

  private targetRemoved(
    server: NoteEnrichmentState,
    target: CancellationTarget
  ) {
    return (
      server.state_version > target.stateVersion &&
      !this.matchesTarget(server, target)
    )
  }

  private async finishCancellation(
    server: NoteEnrichmentState,
    target: CancellationTarget,
    acknowledged: boolean
  ) {
    const scope = this.scope
    this.acceptServer(server)
    let notice: EnrichmentViewState['notice'] = acknowledged
      ? 'cancelled'
      : 'proposalRemoved'
    if (server.last_applied?.proposal_id === target.proposalId) {
      // Use normal conflict recovery rather than restoring or replacing a local draft.
      await this.notes.retry()
      if (!this.isCurrent(scope)) return
      notice = 'proposalAppliedElsewhere'
    }
    this.cancellationTarget = null
    this.generationCommandId = null
    clearTimeout(this.poll)
    this.update({
      phase: 'idle',
      dismissed: !server.proposal,
      cancellationPending: false,
      notice,
      error: null,
    })
  }

  private failCancellation(server?: NoteEnrichmentState) {
    if (server) this.acceptServer(server)
    this.cancellationTarget = null
    clearTimeout(this.poll)
    this.update({
      phase: 'idle',
      dismissed: false,
      cancellationPending: false,
      notice: null,
      error: 'discard',
    })
  }

  private acceptServer(server: NoteEnrichmentState, editVersion?: number) {
    if (
      this.state.server &&
      server.state_version < this.state.server.state_version
    )
      return
    const displayed = this.state.proposal
    const proposalChanged = displayed?.id !== server.proposal?.id
    if (proposalChanged && this.state.modified && !this.state.dismissed) {
      // Keep the user's preview and its evidence available to copy after a remote replacement.
      this.update({ stale: true })
    } else if (proposalChanged) {
      this.proposalEditVersion =
        editVersion ?? this.notes.getSnapshot().editVersion
      this.update({
        review: reviewOf(server.proposal),
        modified: false,
        stale: false,
        proposal: server.proposal,
      })
    } else if (
      displayed?.status === 'generating' &&
      server.proposal?.status === 'ready'
    ) {
      this.update({
        review: reviewOf(server.proposal),
        modified: false,
        proposal: server.proposal,
      })
    } else if (!proposalChanged) {
      this.update({ proposal: server.proposal })
    }
    this.update({ server })
    this.markStale()
  }

  private notesChanged = () => {
    this.markStale()
    const note = this.notes.getSnapshot()
    // « Compléments ajoutés » no longer describes a note edited since.
    if (note.editVersion !== this.noteEditVersion) {
      this.noteEditVersion = note.editVersion
      if (this.state.notice) this.update({ notice: null })
    }
    if (
      note.status === 'ready' &&
      !note.hasUnsavedChanges &&
      note.confirmedRevision !== this.readRevision
    )
      void this.refresh()
  }

  private markStale() {
    const proposal = this.state.proposal
    if (!proposal || this.state.stale) return
    const note = this.notes.getSnapshot()
    if (
      note.hasLoaded &&
      (proposal.status === 'stale' ||
        proposal.base_revision !== note.confirmedRevision ||
        note.editVersion !== this.proposalEditVersion)
    ) {
      this.update({ stale: true })
    }
  }

  private schedulePoll() {
    clearTimeout(this.poll)
    if (
      this.active &&
      (this.state.cancellationPending ||
        (!this.state.dismissed &&
          this.state.server?.proposal?.status === 'generating'))
    ) {
      this.poll = setTimeout(() => void this.refresh(), 1500)
    }
  }

  private envelope(server: NoteEnrichmentState) {
    return {
      version: 1 as const,
      meeting_ref: this.meetingRef,
      command_id: crypto.randomUUID(),
      expected_state_version: server.state_version,
    }
  }

  private isCurrent(scope: number) {
    return this.active && this.scope === scope
  }
  private isGenerationCurrent(scope: number, token: number) {
    return this.isCurrent(scope) && token === this.generationToken
  }
  private isReadCurrent(scope: number, token: number) {
    return (
      this.isGenerationCurrent(scope, token) &&
      !this.pendingMutation &&
      (this.state.phase === 'idle' || this.state.phase === 'loading')
    )
  }
  private update(patch: Partial<EnrichmentViewState>) {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach((listener) => listener())
  }
}
