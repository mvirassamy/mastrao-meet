import { beforeEach, describe, expect, it } from 'vitest'
import {
  cachePlatformReturn,
  clearCachedPlatformReturn,
  clearPlatformReturnForRoomUrl,
  readCachedPlatformReturn,
  validatePlatformReturn,
} from './platformReturn'

const descriptor = (meeting = 'meeting_0123456789abcdef') => ({
  url: `https://platform.mastrao.test/api/meeting-return?organization_ref=organization_0123456789&meeting_ref=${meeting}`,
  expires_at: Math.floor(Date.now() / 1000) + 60,
})

const platformOrigin = 'https://platform.mastrao.test'

describe('Platform return descriptor', () => {
  beforeEach(() => window.sessionStorage.clear())

  it('accepts only the fixed resolver contract', () => {
    expect(validatePlatformReturn(descriptor(), platformOrigin)).toEqual(
      descriptor()
    )
    expect(
      validatePlatformReturn(
        {
          ...descriptor(),
          url: `${descriptor().url}&return_url=https://attacker.test`,
        },
        platformOrigin
      )
    ).toBeNull()
    expect(
      validatePlatformReturn(
        {
          ...descriptor(),
          url: 'https://platform.mastrao.test/other',
        },
        platformOrigin
      )
    ).toBeNull()
    expect(
      validatePlatformReturn({ ...descriptor(), expires_at: 1 }, platformOrigin)
    ).toBeNull()
    expect(
      validatePlatformReturn(
        {
          ...descriptor(),
          url: descriptor().url.replace(
            platformOrigin,
            'https://attacker.test'
          ),
        },
        platformOrigin
      )
    ).toBeNull()
  })

  it('keeps short-lived room caches isolated and clears only the chosen room', () => {
    cachePlatformReturn('room_first_01234567', descriptor(), platformOrigin)
    cachePlatformReturn(
      'room_second_01234567',
      descriptor('meeting_second_01234567'),
      platformOrigin
    )

    expect(
      readCachedPlatformReturn('room_first_01234567', platformOrigin)
    ).toEqual(descriptor())
    clearCachedPlatformReturn('room_first_01234567')
    expect(
      readCachedPlatformReturn('room_first_01234567', platformOrigin)
    ).toBeNull()
    expect(
      readCachedPlatformReturn('room_second_01234567', platformOrigin)?.url
    ).toContain('meeting_second_01234567')
  })

  it('clears the host return cache before entering a guest room', () => {
    cachePlatformReturn('room_first_01234567', descriptor(), platformOrigin)
    cachePlatformReturn(
      'room_second_01234567',
      descriptor('meeting_second_01234567'),
      platformOrigin
    )

    clearPlatformReturnForRoomUrl('/room_first_01234567?silentLogin=false')

    expect(
      readCachedPlatformReturn('room_first_01234567', platformOrigin)
    ).toBeNull()
    expect(
      readCachedPlatformReturn('room_second_01234567', platformOrigin)?.url
    ).toContain('meeting_second_01234567')
  })
})
