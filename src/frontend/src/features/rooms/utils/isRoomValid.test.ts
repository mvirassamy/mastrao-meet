import { describe, expect, it } from 'vitest'
import { shouldWaitForCanonicalRoom } from './isRoomValid'

describe('shouldWaitForCanonicalRoom', () => {
  it('waits for the canonical room lookup before showing the lobby', () => {
    expect(
      shouldWaitForCanonicalRoom('room_0123456789abcdef0123456789abcdef', true)
    ).toBe(true)
  })

  it('keeps legacy Meet room slugs joinable when the query is disabled', () => {
    expect(shouldWaitForCanonicalRoom('abc-defg-hij', true)).toBe(false)
  })
})
