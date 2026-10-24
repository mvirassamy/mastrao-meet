/** How many steps back the note keeps. */
const MAX_STEPS = 100

/**
 * Undo and redo over whole versions of the note: a typing pause or added
 * complements make one step each. Going back is an ordinary note edit.
 */
export class NoteHistory {
  private past: string[] = []
  private future: string[] = []

  constructor(private committed: string) {}

  /** Records the note as a new step; a new step drops what could be redone. */
  record(content: string) {
    if (content === this.committed) return
    this.past = [...this.past, this.committed].slice(-MAX_STEPS)
    this.committed = content
    this.future = []
  }

  /** The previous version, after recording what was typed since the last step. */
  undo(current: string): string | null {
    this.record(current)
    const previous = this.past.at(-1)
    if (previous === undefined) return null
    this.past = this.past.slice(0, -1)
    this.future = [...this.future, this.committed]
    this.committed = previous
    return previous
  }

  redo(current: string): string | null {
    const next = this.future.at(-1)
    if (next === undefined || current !== this.committed) return null
    this.future = this.future.slice(0, -1)
    this.past = [...this.past, this.committed]
    this.committed = next
    return next
  }

  canUndo(current: string) {
    return this.past.length > 0 || current !== this.committed
  }

  canRedo(current: string) {
    return this.future.length > 0 && current === this.committed
  }
}
