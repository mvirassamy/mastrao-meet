import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { focusManager } from '@tanstack/react-query'
import { type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createGuestInvitationShare } from '../../api/createGuestInvitationShare'
import { useRoomShareLink } from './useRoomShareLink'

vi.mock('../../api/createGuestInvitationShare', () => ({
  createGuestInvitationShare: vi.fn(),
}))

const createShare = vi.mocked(createGuestInvitationShare)
const canonicalRoom = 'room_0123456789abcdef0123456789abcdef'

const wrapperFor = (client: QueryClient) => {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return Wrapper
}

describe('useRoomShareLink', () => {
  afterEach(() => vi.useRealTimers())
  beforeEach(() => {
    createShare.mockReset()
    createShare.mockResolvedValue(
      `${window.location.origin}/guest#organization=organization_test&share=share_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef`
    )
  })

  it('keeps a single guest invitation across focus and remounts', async () => {
    const client = new QueryClient()
    const wrapper = wrapperFor(client)
    const first = renderHook(() => useRoomShareLink(canonicalRoom), {
      wrapper,
    })
    await waitFor(() =>
      expect(first.result.current.shareUrl).toContain('/guest#organization=')
    )

    act(() => {
      focusManager.setFocused(false)
      focusManager.setFocused(true)
    })
    vi.useFakeTimers()
    first.unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60 * 60 * 1000)
    })
    renderHook(() => useRoomShareLink(canonicalRoom), { wrapper })

    expect(createShare).toHaveBeenCalledOnce()
  })

  it('shares ordinary rooms without requesting an invitation', () => {
    const { result } = renderHook(() => useRoomShareLink('ordinary-room'), {
      wrapper: wrapperFor(new QueryClient()),
    })

    expect(result.current.shareUrl).toContain('/ordinary-room')
    expect(result.current.isShareLinkPending).toBe(false)
    expect(createShare).not.toHaveBeenCalled()
  })
})
