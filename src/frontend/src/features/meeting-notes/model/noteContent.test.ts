import { describe, expect, it } from 'vitest'
import { applyNoteEdit, PERSONAL_NOTE_LIMIT } from './noteContent'

describe('personal note length limits', () => {
  it('accepts exactly 100,000 Unicode code points without altering text', () => {
    const content = '😀'.repeat(PERSONAL_NOTE_LIMIT)
    expect(applyNoteEdit(content, '')).toEqual({
      content,
      length: PERSONAL_NOTE_LIMIT,
      wasRejected: false,
    })
  })

  it('rejects an oversized paste entirely and preserves a shorter draft', () => {
    const previous = 'My unsaved draft 🐈'
    expect(
      applyNoteEdit('😀'.repeat(PERSONAL_NOTE_LIMIT + 1), previous)
    ).toEqual({
      content: previous,
      length: Array.from(previous).length,
      wasRejected: true,
    })
  })

  it('accepts clearing a note after a rejected edit', () => {
    expect(applyNoteEdit('', 'Previous text')).toEqual({
      content: '',
      length: 0,
      wasRejected: false,
    })
  })
})
