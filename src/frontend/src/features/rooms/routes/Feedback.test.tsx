import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { forwardRef, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/ApiError'
import FeedbackRoute from './Feedback'

const state = vi.hoisted(() => ({
  isLoggedIn: true as boolean | undefined,
  fetchApi: vi.fn(),
  assign: vi.fn(),
  authUrl: vi.fn(() => '/authenticate/?returnTo=meet'),
}))
const setLocation = vi.fn()
const browserLocation = window.location
const roomId = 'room_0123456789abcdef0123456789abcdef'
const descriptor = () => ({
  url: 'https://platform.mastrao.test/api/meeting-return?organization_ref=organization_0123456789&meeting_ref=meeting_0123456789abcdef',
  expires_at: Math.floor(Date.now() / 1000) + 60,
})
vi.mock('@/features/auth/api/useUser', () => ({
  useUser: () => ({ isLoggedIn: state.isLoggedIn }),
}))
vi.mock('@/api/fetchApi', () => ({ fetchApi: state.fetchApi }))
vi.mock('@/features/auth/utils/authUrl', () => ({ authUrl: state.authUrl }))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('wouter', () => ({
  useLocation: () => ['/', setLocation],
}))

vi.mock('@/api/useConfig', () => ({
  useConfig: () => ({
    data: { mastrao_platform_origin: 'https://platform.mastrao.test' },
  }),
}))

vi.mock('@/layout/Screen', () => ({
  Screen: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}))

vi.mock('@/primitives', () => ({
  Text: ({ children, role }: { children: ReactNode; role?: string }) => (
    <p role={role}>{children}</p>
  ),
  Button: ({
    children,
    onPress,
    isDisabled,
  }: {
    children: string
    onPress: () => void
    isDisabled?: boolean
  }) => (
    <button type="button" onClick={onPress} disabled={isDisabled}>
      {children}
    </button>
  ),
}))

vi.mock('@/styled-system/jsx', () => ({
  Center: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  HStack: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  VStack: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  styled: () =>
    forwardRef<HTMLHeadingElement, { children: ReactNode; tabIndex?: number }>(
      ({ children, tabIndex }, ref) => (
        <h1 ref={ref} tabIndex={tabIndex}>
          {children}
        </h1>
      )
    ),
}))

vi.mock('@/features/rooms/components/Rating.tsx', () => ({
  Rating: () => <div>rating</div>,
}))

describe('Feedback stays in Meet', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.isLoggedIn = true
    state.fetchApi.mockReset()
    window.sessionStorage.clear()
    window.history.replaceState({}, '', '/')
    vi.stubGlobal('location', {
      get search() {
        return browserLocation.search
      },
      get origin() {
        return browserLocation.origin
      },
      assign: state.assign,
    })
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })
  const showHostExit = (outcome = 'left', value = descriptor()) => {
    window.sessionStorage.setItem(
      `mastrao-platform-return-v1:${roomId}`,
      JSON.stringify(value)
    )
    window.history.replaceState(
      { room_id: roomId, platform_return: value },
      '',
      `/feedback?outcome=${outcome}&room_id=${roomId}`
    )
    render(<FeedbackRoute />)
  }
  it('removes dossier return after an ended meeting and keeps home internal', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    showHostExit('ended')
    expect(
      screen.queryByRole('button', { name: 'feedback.returnToMatter' })
    ).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'feedback.rejoinFromMatter' })
    ).toBeNull()
    expect(screen.queryByRole('button', { name: 'feedback.back' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'feedback.home' }))
    expect(setLocation).toHaveBeenCalledWith('/')
    expect(open).not.toHaveBeenCalled()
    expect(state.fetchApi).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(screen.getByRole('heading'))
  })
  it('recovers departed host rights and rejoins the exact room in the same tab', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    state.fetchApi.mockResolvedValueOnce({ room_url: `/${roomId}` })
    showHostExit()
    fireEvent.click(screen.getByRole('button', { name: 'feedback.back' }))
    await vi.waitFor(() =>
      expect(state.assign).toHaveBeenCalledWith(`/${roomId}`)
    )
    expect(state.fetchApi).toHaveBeenCalledWith(
      `rooms/${roomId}/host-handoff/`,
      { method: 'POST', headers: { 'X-Idempotency-Key': expect.any(String) } }
    )
    expect(open).not.toHaveBeenCalled()
    expect(
      screen.queryByRole('button', { name: 'feedback.rejoinFromMatter' })
    ).toBeNull()
  })
  it.each(['history', 'cache'])(
    'recovers an expired host grant after reload using %s context',
    async (source) => {
      const expired = { ...descriptor(), expires_at: 1 }
      state.fetchApi.mockResolvedValueOnce({ room_url: `/${roomId}` })
      if (source === 'history') {
        showHostExit('left', expired)
      } else {
        window.sessionStorage.setItem(
          `mastrao-platform-return-v1:${roomId}`,
          JSON.stringify(expired)
        )
        window.history.replaceState(
          {},
          '',
          `/feedback?outcome=left&room_id=${roomId}`
        )
        render(<FeedbackRoute />)
      }
      fireEvent.click(screen.getByRole('button', { name: 'feedback.back' }))
      await vi.waitFor(() =>
        expect(state.fetchApi).toHaveBeenCalledWith(
          `rooms/${roomId}/host-handoff/`,
          {
            method: 'POST',
            headers: { 'X-Idempotency-Key': expect.any(String) },
          }
        )
      )
      expect(setLocation).not.toHaveBeenCalled()
      expect(state.assign).toHaveBeenCalledWith(`/${roomId}`)
    }
  )
  it('keeps the host retry identity after a temporary failure', async () => {
    state.fetchApi
      .mockRejectedValueOnce(new ApiError(503, {}))
      .mockResolvedValueOnce({ room_url: `/${roomId}` })
    showHostExit()
    fireEvent.click(screen.getByRole('button', { name: 'feedback.back' }))
    await screen.findByRole('alert')
    expect(state.assign).not.toHaveBeenCalled()
    const key = state.fetchApi.mock.calls[0][1].headers['X-Idempotency-Key']
    fireEvent.click(screen.getByRole('button', { name: 'feedback.back' }))
    await vi.waitFor(() =>
      expect(state.assign).toHaveBeenCalledWith(`/${roomId}`)
    )
    expect(state.fetchApi.mock.calls[1][1].headers['X-Idempotency-Key']).toBe(
      key
    )
  })
  it('preserves login when the host session expires', async () => {
    state.fetchApi.mockRejectedValueOnce(new ApiError(401, {}))
    showHostExit()
    fireEvent.click(screen.getByRole('button', { name: 'feedback.back' }))
    await vi.waitFor(() =>
      expect(state.assign).toHaveBeenCalledWith('/authenticate/?returnTo=meet')
    )
    expect(state.authUrl).toHaveBeenCalledWith({
      returnTo: new URL(`/host/${roomId}`, browserLocation.origin).href,
    })
  })
  it('keeps a host authorization refusal on Meet without joining', async () => {
    state.fetchApi.mockRejectedValueOnce(new ApiError(403, {}))
    showHostExit()
    fireEvent.click(screen.getByRole('button', { name: 'feedback.back' }))
    await screen.findByRole('alert')
    expect(state.assign).not.toHaveBeenCalled()
    expect(setLocation).not.toHaveBeenCalled()
  })

  it('refuses a recovery response pointing at another room or origin', async () => {
    state.fetchApi.mockResolvedValueOnce({ room_url: 'https://attacker.test/' })
    showHostExit()
    fireEvent.click(screen.getByRole('button', { name: 'feedback.back' }))
    await screen.findByRole('alert')
    expect(state.assign).not.toHaveBeenCalled()
  })

  it('keeps ordinary feedback usable when storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage unavailable')
    })
    window.history.replaceState(
      {},
      '',
      `/feedback?outcome=left&room_id=${roomId}`
    )
    render(<FeedbackRoute />)
    fireEvent.click(screen.getByRole('button', { name: 'feedback.back' }))
    expect(setLocation).toHaveBeenCalledWith(`/${roomId}`)
  })

  it('rejoins an ordinary participant internally after reload', () => {
    const back = vi.spyOn(window.history, 'back')
    window.history.replaceState(
      {},
      '',
      `/feedback?outcome=left&room_id=${roomId}`
    )
    render(<FeedbackRoute />)
    fireEvent.click(screen.getByRole('button', { name: 'feedback.back' }))
    expect(setLocation).toHaveBeenCalledWith(`/${roomId}`)
    expect(state.fetchApi).not.toHaveBeenCalled()
    expect(back).not.toHaveBeenCalled()
  })
  it.each(['ended', 'removed'])('does not offer rejoin for %s', (outcome) => {
    showHostExit(outcome)
    expect(screen.queryByRole('button', { name: 'feedback.back' })).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'feedback.returnToMatter' })
    ).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'feedback.rejoinFromMatter' })
    ).toBeNull()
    expect(screen.getByRole('button', { name: 'feedback.home' })).toBeTruthy()
    expect(state.fetchApi).not.toHaveBeenCalled()
  })
  it('does not offer history back without a validated room id', () => {
    window.history.replaceState(
      {},
      '',
      '/feedback?outcome=left&room_id=https://attacker.test'
    )
    render(<FeedbackRoute />)
    expect(screen.queryByRole('button', { name: 'feedback.back' })).toBeNull()
    expect(screen.getByRole('button', { name: 'feedback.home' })).toBeTruthy()
  })
})
