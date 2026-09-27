import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Home from './Home'

const authState = vi.hoisted(() => ({
  isLoggedIn: undefined as boolean | undefined,
  user: undefined as
    | { id: string; email: string; full_name: string }
    | undefined,
}))

vi.mock('@/features/auth/api/useUser', () => ({ useUser: () => authState }))
vi.mock('@/features/auth/components/UserAware', () => ({
  UserAware: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="user-aware">{children}</div>
  ),
}))
vi.mock('./PublicHome', () => ({
  PublicHome: () => <div>public-home</div>,
}))
vi.mock('./AuthenticatedHome', () => ({
  AuthenticatedHome: ({ user }: { user: { email: string } }) => (
    <div>authenticated-home:{user.email}</div>
  ),
}))

describe('Home', () => {
  beforeEach(() => {
    authState.isLoggedIn = undefined
    authState.user = undefined
  })

  it('keeps the public home for signed-out users', () => {
    authState.isLoggedIn = false
    render(<Home />)
    expect(screen.getByText('public-home')).toBeTruthy()
  })

  it('shows the authenticated home only with a resolved user', () => {
    authState.isLoggedIn = true
    authState.user = {
      id: 'user-1',
      email: 'matthias@mastrao.com',
      full_name: 'Matthias',
    }
    render(<Home />)
    expect(
      screen.getByText('authenticated-home:matthias@mastrao.com')
    ).toBeTruthy()
  })
})
