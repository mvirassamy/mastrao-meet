import { beforeEach, describe, expect, it, vi } from 'vitest'

describe('host invitation fragment', () => {
  beforeEach(() => {
    vi.resetModules()
    window.history.replaceState({}, '', '/host?locale=fr')
  })

  it('captures the bearer and removes the fragment immediately', async () => {
    window.location.hash = `host=${'a'.repeat(64)}`
    const replace = vi.spyOn(window.history, 'replaceState')
    const { consumeHostInvitationFragment } =
      await import('./hostInvitationFragment')

    const invitation = consumeHostInvitationFragment()

    expect(invitation).toBe('a'.repeat(64))
    expect(replace).toHaveBeenCalledWith(undefined, '', '/host?locale=fr')
    expect(window.location.hash).toBe('')
  })

  it('removes and refuses an oversized bearer', async () => {
    window.location.hash = `host=${'a'.repeat(16385)}`
    const { consumeHostInvitationFragment } =
      await import('./hostInvitationFragment')

    expect(consumeHostInvitationFragment()).toBeNull()
    expect(window.location.hash).toBe('')
  })
})
