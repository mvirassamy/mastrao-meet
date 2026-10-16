import {
  PersonalNoteRequestError,
  type PersonalNote,
  type PersonalNotesApi,
} from '../api/personalNotesApi'
import { applyNoteEdit } from './noteContent'

export type NoteStatus =
  | 'loading'
  | 'ready'
  | 'pending'
  | 'saving'
  | 'error'
  | 'unauthorized'
  | 'conflict'

export interface PersonalNotesState {
  content: string
  length: number
  wasRejected: boolean
  status: NoteStatus
  hasLoaded: boolean
  hasUnsavedChanges: boolean
  conflict: PersonalNote | null
}

interface UnconfirmedWrite {
  content: string
  baseline: PersonalNote
  conflicted: boolean
}

const DEBOUNCE_MS = 800
const MAX_WAIT_MS = 5_000

/** Owns a single author's draft for one endpoint; never caches across owners. */
export class PersonalNotesController {
  private state: PersonalNotesState = {
    content: '',
    length: 0,
    wasRejected: false,
    status: 'loading',
    hasLoaded: false,
    hasUnsavedChanges: false,
    conflict: null,
  }
  private listeners = new Set<() => void>()
  private baseline: PersonalNote | null = null
  private unconfirmed: UnconfirmedWrite | null = null
  private operation: Promise<boolean> | null = null
  private generation = 0
  private active = false
  private composing = false
  private compositionStartContent: string | null = null
  private dirtySince: number | null = null
  private timer: ReturnType<typeof setTimeout> | undefined
  private requestController = new AbortController()

  constructor(private readonly api: PersonalNotesApi) {}

  getSnapshot = () => this.state

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  start = () => {
    this.active = true
    this.requestController = new AbortController()
    void this.retry()
  }

  stop = () => {
    this.active = false
    this.generation += 1
    this.clearTimer()
    this.requestController.abort()
    this.operation = null
  }

  setContent = (value: string) => {
    if (!this.active || !this.baseline) return
    // Keep in-progress IME text visible; validate the whole edit on commit.
    const edit = this.composing
      ? { content: value, length: Array.from(value).length, wasRejected: false }
      : applyNoteEdit(value, this.compositionStartContent ?? this.state.content)
    this.update(edit)
    if (this.isBlocked()) return
    if (!this.operation) {
      this.update({
        status: this.state.hasUnsavedChanges ? 'pending' : 'ready',
      })
    }
    this.schedule()
  }

  setComposing = (composing: boolean) => {
    if (this.composing === composing) return
    this.composing = composing
    if (composing) {
      this.compositionStartContent = this.state.content
      this.clearTimer()
      return
    }
    this.setContent(this.state.content)
    this.compositionStartContent = null
  }

  /** Resolves only after all edits made during an in-flight save are confirmed. */
  flush = (): Promise<boolean> => {
    this.clearTimer()
    if (this.operation) return this.operation
    if (!this.active || !this.baseline || this.composing || this.isBlocked()) {
      return Promise.resolve(false)
    }
    return this.run(() => this.saveUntilClean(this.generation))
  }

  /** Always re-reads before retrying a write with an uncertain outcome. */
  retry = (): Promise<boolean> => {
    if (this.operation) return this.operation
    if (!this.active) return Promise.resolve(false)
    if (this.state.conflict) return Promise.resolve(false)
    this.clearTimer()
    const generation = this.generation
    return this.run(async () => {
      this.update({ status: 'loading' })
      try {
        const note = await this.api.read(this.requestController.signal)
        if (!this.isCurrent(generation)) return false
        if (!this.reconcile(note)) return false
        return await this.saveUntilClean(generation)
      } catch (error) {
        if (this.isCurrent(generation)) this.fail(error)
        return false
      }
    })
  }

  loadSavedVersion = () => {
    if (!this.state.conflict || this.operation) return
    const note = this.state.conflict
    this.baseline = note
    this.unconfirmed = null
    this.dirtySince = null
    this.update({
      content: note.content,
      length: Array.from(note.content).length,
      wasRejected: false,
      conflict: null,
      status: 'ready',
    })
  }

  /** The editor asks for explicit confirmation before invoking this action. */
  keepDraft = (): Promise<boolean> => {
    if (!this.state.conflict || this.operation) return Promise.resolve(false)
    this.baseline = this.state.conflict
    this.unconfirmed = null
    this.update({ conflict: null, status: 'pending' })
    return this.flush()
  }

  private update(patch: Partial<PersonalNotesState>) {
    this.state = { ...this.state, ...patch }
    this.state.hasUnsavedChanges =
      this.unconfirmed !== null ||
      (this.baseline !== null && this.state.content !== this.baseline.content)
    this.listeners.forEach((listener) => listener())
  }

  private isCurrent(generation: number) {
    return this.active && this.generation === generation
  }

  private isBlocked() {
    switch (this.state.status) {
      case 'error':
      case 'unauthorized':
      case 'conflict':
        return true
      default:
        return false
    }
  }

  private run(action: () => Promise<boolean>) {
    const generation = this.generation
    const operation = Promise.resolve().then(() => {
      if (!this.isCurrent(generation)) return false
      return action()
    })
    this.operation = operation
    void operation.finally(() => {
      if (this.operation !== operation) return
      this.operation = null
      if (this.active && this.baseline) this.schedule()
    })
    return operation
  }

  private clearTimer() {
    clearTimeout(this.timer)
    this.timer = undefined
  }

  private schedule() {
    this.clearTimer()
    if (!this.active || !this.baseline) return
    if (!this.state.hasUnsavedChanges) {
      this.dirtySince = null
      return
    }
    this.dirtySince ??= Date.now()
    if (this.composing || this.operation || this.isBlocked()) return
    const remaining = MAX_WAIT_MS - (Date.now() - this.dirtySince)
    this.timer = setTimeout(
      () => void this.flush(),
      Math.max(0, Math.min(DEBOUNCE_MS, remaining))
    )
  }

  private fail(error: unknown) {
    let status: NoteStatus = 'error'
    if (
      error instanceof PersonalNoteRequestError &&
      (error.statusCode === 401 || error.statusCode === 403)
    ) {
      status = 'unauthorized'
    }
    this.update({ status })
  }

  private showConflict(note: PersonalNote) {
    this.update({ conflict: note, status: 'conflict' })
    return false
  }

  private reconcile(note: PersonalNote): boolean {
    if (!this.baseline) {
      this.baseline = note
      this.update({
        content: note.content,
        length: Array.from(note.content).length,
        hasLoaded: true,
        status: 'ready',
      })
      return true
    }
    const attempt = this.unconfirmed
    if (attempt?.conflicted) return this.showConflict(note)
    if (
      attempt &&
      note.content === attempt.content &&
      note.revision > attempt.baseline.revision
    ) {
      this.baseline = note
      this.unconfirmed = null
      this.update({ status: 'pending' })
      return true
    }
    const baseline = attempt?.baseline ?? this.baseline
    if (
      note.revision !== baseline.revision ||
      note.content !== baseline.content
    ) {
      return this.showConflict(note)
    }
    this.baseline = note
    this.unconfirmed = null
    this.update({ status: 'pending' })
    return true
  }

  private async saveUntilClean(generation: number): Promise<boolean> {
    while (this.isCurrent(generation) && this.baseline) {
      if (this.composing) {
        this.update({ status: 'pending' })
        return false
      }
      if (!this.state.hasUnsavedChanges) {
        this.dirtySince = null
        this.update({ status: 'ready' })
        return true
      }
      const attempt: UnconfirmedWrite = {
        content: this.state.content,
        baseline: this.baseline,
        conflicted: false,
      }
      this.unconfirmed = attempt
      this.dirtySince = null
      this.update({ status: 'saving' })
      try {
        const note = await this.api.write(
          attempt.content,
          attempt.baseline.revision,
          this.requestController.signal
        )
        if (!this.isCurrent(generation)) return false
        if (
          note.content !== attempt.content ||
          note.revision <= attempt.baseline.revision
        ) {
          throw new PersonalNoteRequestError()
        }
        this.baseline = note
        this.unconfirmed = null
        this.update({})
      } catch (error) {
        if (!this.isCurrent(generation)) return false
        if (
          error instanceof PersonalNoteRequestError &&
          error.statusCode === 409
        ) {
          attempt.conflicted = true
          try {
            const current = await this.api.read(this.requestController.signal)
            if (!this.isCurrent(generation)) return false
            return this.showConflict(current)
          } catch (readError) {
            if (this.isCurrent(generation)) this.fail(readError)
            return false
          }
        }
        this.fail(error)
        return false
      }
    }
    return false
  }
}
