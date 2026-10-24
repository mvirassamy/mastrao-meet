import { describe, expect, it } from 'vitest'
import { NoteHistory } from './NoteHistory'

describe('note history', () => {
  it('goes back and forth over recorded versions', () => {
    const history = new NoteHistory('a')
    history.record('ab')
    history.record('abc')
    expect(history.undo('abc')).toBe('ab')
    expect(history.undo('ab')).toBe('a')
    expect(history.undo('a')).toBeNull()
    expect(history.canRedo('a')).toBe(true)
    expect(history.redo('a')).toBe('ab')
    expect(history.redo('ab')).toBe('abc')
    expect(history.redo('abc')).toBeNull()
  })

  it('records unfinished typing before going back, so redo restores it', () => {
    const history = new NoteHistory('a')
    expect(history.canUndo('a typed')).toBe(true)
    expect(history.undo('a typed')).toBe('a')
    expect(history.redo('a')).toBe('a typed')
  })

  it('drops what could be redone once the note changes again', () => {
    const history = new NoteHistory('a')
    history.record('b')
    history.undo('b')
    expect(history.canRedo('c')).toBe(false)
    history.record('c')
    expect(history.redo('c')).toBeNull()
    expect(history.undo('c')).toBe('a')
  })

  it('keeps the last hundred steps', () => {
    const history = new NoteHistory('0')
    for (let step = 1; step <= 150; step += 1) history.record(String(step))
    let steps = 0
    let current = '150'
    for (;;) {
      const previous = history.undo(current)
      if (previous === null) break
      current = previous
      steps += 1
    }
    expect(steps).toBe(100)
    expect(current).toBe('50')
  })
})
