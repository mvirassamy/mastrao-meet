import { beforeEach, describe, expect, it, vi } from 'vitest'

describe('host invitation fragment', () => {
  beforeEach(() => {
    vi.resetModules()
    window.history.replaceState({}, '', '/host?locale=fr')
  })

  it.each(['host_invitation', 'host'])(
    'captures the %s bearer and removes the fragment immediately',
    async (parameter) => {
      window.location.hash = `${parameter}=${'a'.repeat(64)}`
      const replace = vi.spyOn(window.history, 'replaceState')
      const { consumeHostInvitationFragment } =
        await import('./hostInvitationFragment')

      const invitation = consumeHostInvitationFragment()

      expect(invitation).toBe('a'.repeat(64))
      expect(replace).toHaveBeenCalledWith(undefined, '', '/host?locale=fr')
      expect(window.location.hash).toBe('')
    }
  )

  it('removes and refuses an oversized bearer', async () => {
    window.location.hash = `host_invitation=${'a'.repeat(16385)}`
    const { consumeHostInvitationFragment } =
      await import('./hostInvitationFragment')

    expect(consumeHostInvitationFragment()).toBeNull()
    expect(window.location.hash).toBe('')
  })

  it.each([
    '',
    'host_invitation=',
    `host_invitation=${'a'.repeat(64)}&host_invitation=${'b'.repeat(64)}`,
    `host_invitation=${'a'.repeat(64)}&host=${'b'.repeat(64)}`,
    `host_invitation=${'a'.repeat(64)}&extra=value`,
    `unrecognized=${'a'.repeat(64)}`,
  ])('clears and refuses an invalid or ambiguous fragment', async (hash) => {
    window.location.hash = hash
    const { consumeHostInvitationFragment } =
      await import('./hostInvitationFragment')

    expect(consumeHostInvitationFragment()).toBeNull()
    expect(window.location.hash).toBe('')
  })

  it('keeps the captured bearer for React rerenders after removing the hash', async () => {
    window.location.hash = `host_invitation=${'a'.repeat(64)}`
    const { consumeHostInvitationFragment } =
      await import('./hostInvitationFragment')

    expect(consumeHostInvitationFragment()).toBe('a'.repeat(64))
    expect(window.location.hash).toBe('')
    expect(consumeHostInvitationFragment()).toBe('a'.repeat(64))
  })
})
