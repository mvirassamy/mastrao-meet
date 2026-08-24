import { beforeEach, describe, expect, it } from 'vitest'
import {
  cachePlatformReturn,
  clearCachedPlatformReturn,
  readCachedPlatformReturn,
  validatePlatformReturn,
} from './platformReturn'

const descriptor = (meeting = 'meeting_0123456789abcdef') => ({
  url: `https://platform.mastrao.test/api/meeting-return?organization_ref=organization_0123456789&meeting_ref=${meeting}`,
  expires_at: Math.floor(Date.now() / 1000) + 60,
})

describe('Platform return descriptor', () => {
  beforeEach(() => window.sessionStorage.clear())

  it('accepts only the fixed resolver contract', () => {
    expect(validatePlatformReturn(descriptor())).toEqual(descriptor())
    expect(
      validatePlatformReturn({
        ...descriptor(),
        url: `${descriptor().url}&return_url=https://attacker.test`,
      })
    ).toBeNull()
    expect(
      validatePlatformReturn({
        ...descriptor(),
        url: 'https://platform.mastrao.test/other',
      })
    ).toBeNull()
    expect(
      validatePlatformReturn({ ...descriptor(), expires_at: 1 })
    ).toBeNull()
  })

  it('keeps short-lived room caches isolated and clears only the chosen room', () => {
    cachePlatformReturn('room_first_01234567', descriptor())
    cachePlatformReturn(
      'room_second_01234567',
      descriptor('meeting_second_01234567')
    )

    expect(readCachedPlatformReturn('room_first_01234567')).toEqual(
      descriptor()
    )
    clearCachedPlatformReturn('room_first_01234567')
    expect(readCachedPlatformReturn('room_first_01234567')).toBeNull()
    expect(readCachedPlatformReturn('room_second_01234567')?.url).toContain(
      'meeting_second_01234567'
    )
  })
})
