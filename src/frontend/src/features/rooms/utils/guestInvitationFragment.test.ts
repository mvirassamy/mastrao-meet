import { beforeEach, describe, expect, it, vi } from 'vitest'

const fragment =
  '#organization=organization_test&share=share_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef'

const capture = async (hash: string) => {
  vi.resetModules()
  window.history.replaceState(null, '', `/guest${hash}`)
  return import('./guestInvitationFragment')
}

describe('durable guest entry', () => {
  beforeEach(() => sessionStorage.clear())

  it('removes the locator before entry and reuses the same attempt on reopening', async () => {
    const first = await capture(fragment)
    const link = first.consumeGuestInvitationFragment()!
    expect(window.location.hash).toBe('')
    expect(link.kind).toBe('durable')
    const id = first.guestRedemptionId(link)
    const reopened = await capture(fragment)
    expect(
      reopened.guestRedemptionId(reopened.consumeGuestInvitationFragment()!)
    ).toBe(id)
    reopened.forgetGuestRedemption(link)
    expect(reopened.guestRedemptionId(link)).not.toBe(id)
  })

  it.each([
    `${fragment}&share=share_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef`,
    `${fragment}&invite=aaa.bbb.ccc`,
    '#organization=organization_test&share=too_short',
    '#share=share_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef',
  ])('rejects an ambiguous or malformed locator: %s', async (hash) => {
    const module = await capture(hash)
    expect(module.consumeGuestInvitationFragment()).toBeNull()
    expect(window.location.hash).toBe('')
  })

  it('retains issued legacy invitation entry', async () => {
    const module = await capture('#invite=aaa.bbb.ccc')
    expect(module.consumeGuestInvitationFragment()).toEqual({
      kind: 'legacy',
      invitation: 'aaa.bbb.ccc',
    })
    expect(window.location.hash).toBe('')
  })
})
