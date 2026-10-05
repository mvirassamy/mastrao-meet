import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/ApiError'
import HostRecovery from './HostRecovery'

const state = vi.hoisted(() => ({
  isLoggedIn: undefined as boolean | undefined,
  fetchApi: vi.fn(),
  assign: vi.fn(),
}))
const roomRef = 'room_0123456789abcdef'

vi.mock('wouter', () => ({ useParams: () => ({ roomRef }) }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/features/auth/api/useUser', () => ({
  useUser: () => ({ isLoggedIn: state.isLoggedIn }),
}))
vi.mock('@/features/auth/utils/authUrl', () => ({
  authUrl: () => `/authenticate/?next=/host/${roomRef}`,
}))
vi.mock('@/api/fetchApi', () => ({ fetchApi: state.fetchApi }))
vi.mock('@/layout/Screen', () => ({
  Screen: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/styled-system/jsx', () => ({
  VStack: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))
vi.mock('@/primitives', () => ({
  H: ({ children }: { children: ReactNode }) => <h1>{children}</h1>,
  Text: ({ children, role }: { children: ReactNode; role?: string }) => (
    <p role={role}>{children}</p>
  ),
  Button: ({
    children,
    onPress,
    isDisabled,
  }: {
    children: ReactNode
    onPress: () => void
    isDisabled: boolean
  }) => (
    <button type="button" onClick={onPress} disabled={isDisabled}>
      {children}
    </button>
  ),
}))

describe('host recovery authentication and retry', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.isLoggedIn = true
    sessionStorage.clear()
    vi.stubGlobal('location', {
      origin: 'https://meet.mastrao.test',
      assign: state.assign,
    })
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('redirects an anonymous visitor before any mutation or retry key is saved', () => {
    state.isLoggedIn = false
    render(<HostRecovery />)
    fireEvent.click(screen.getByRole('button', { name: 'submit' }))
    expect(state.assign).toHaveBeenCalledWith(
      `/authenticate/?next=/host/${roomRef}`
    )
    expect(state.fetchApi).not.toHaveBeenCalled()
    expect(sessionStorage.length).toBe(0)
  })

  it('waits for authentication state before enabling recovery', () => {
    state.isLoggedIn = undefined
    render(<HostRecovery />)
    const button = screen.getByRole('button', { name: 'submit' })
    expect((button as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(button)
    expect(state.fetchApi).not.toHaveBeenCalled()
    expect(state.assign).not.toHaveBeenCalled()
  })

  it('keeps the same retry identity after a lost response', async () => {
    state.fetchApi
      .mockRejectedValueOnce(new ApiError(503, {}))
      .mockResolvedValueOnce({ room_url: `/${roomRef}` })
    render(<HostRecovery />)
    fireEvent.click(screen.getByRole('button', { name: 'submit' }))
    await screen.findByRole('alert')
    const key = state.fetchApi.mock.calls[0][1].headers['X-Idempotency-Key']
    fireEvent.click(screen.getByRole('button', { name: 'submit' }))
    await waitFor(() =>
      expect(state.assign).toHaveBeenCalledWith(`/${roomRef}`)
    )
    expect(state.fetchApi.mock.calls[1][1].headers['X-Idempotency-Key']).toBe(
      key
    )
  })

  it('redirects a session that expires after authentication was loaded', async () => {
    state.fetchApi.mockRejectedValueOnce(new ApiError(401, {}))
    render(<HostRecovery />)
    fireEvent.click(screen.getByRole('button', { name: 'submit' }))
    await waitFor(() =>
      expect(state.assign).toHaveBeenCalledWith(
        `/authenticate/?next=/host/${roomRef}`
      )
    )
  })

  it('preserves a CSRF refusal without treating it as an anonymous login', async () => {
    state.fetchApi.mockRejectedValueOnce(new ApiError(403, {}))
    render(<HostRecovery />)
    fireEvent.click(screen.getByRole('button', { name: 'submit' }))
    await screen.findByRole('alert')
    expect(state.assign).not.toHaveBeenCalled()
  })
})
