import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import {
  createGuestInvitationShare,
  GuestInvitationShareContractError,
} from './createGuestInvitationShare'

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))

const fetchApiMock = vi.mocked(fetchApi)

describe('createGuestInvitationShare', () => {
  beforeEach(() => {
    fetchApiMock.mockReset()
  })

  it('returns a same-origin guest link from the backend contract', async () => {
    fetchApiMock.mockResolvedValue({
      invite_url: `${window.location.origin}/guest#organization=organization_test&share=share_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef`,
    })

    await expect(
      createGuestInvitationShare('room_0123456789abcdef0123456789abcdef')
    ).resolves.toBe(
      `${window.location.origin}/guest#organization=organization_test&share=share_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef`
    )
    expect(fetchApiMock).toHaveBeenCalledWith(
      'rooms/room_0123456789abcdef0123456789abcdef/guest-invitation/',
      { method: 'POST' }
    )
  })

  it.each([
    'https://attacker.test/guest#organization=organization_test&share=share_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef',
    `${window.location.origin}/room_0123456789abcdef0123456789abcdef`,
    `${window.location.origin}/guest#invite=not-a-jws`,
    `${window.location.origin}/guest?share=share_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef`,
    `${window.location.origin}/guest#organization=organization_test&share=share_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef&invite=aaa.bbb.ccc`,
  ])('rejects an unsafe invitation URL: %s', async (inviteUrl) => {
    fetchApiMock.mockResolvedValue({ invite_url: inviteUrl })

    await expect(
      createGuestInvitationShare('room_0123456789abcdef0123456789abcdef')
    ).rejects.toBeInstanceOf(GuestInvitationShareContractError)
  })
})
