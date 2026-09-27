import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MeetSidebarUserMenu } from './MeetSidebarUserMenu'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({ loggedInUserTooltip: 'Utilisateur connecté', logout: 'Déconnexion' })[
        key
      ] ?? key,
  }),
}))

vi.mock('react-aria-components', () => ({
  Menu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  MenuItem: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}))

vi.mock('@/primitives', () => ({
  Button: ({ children }: { children: React.ReactNode }) => (
    <button type="button">{children}</button>
  ),
  Menu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

vi.mock('@/features/auth/utils/logout', () => ({ logout: vi.fn() }))

describe('MeetSidebarUserMenu', () => {
  it('falls back to the email when the OIDC profile has no full name', () => {
    render(
      <MeetSidebarUserMenu
        user={{
          id: 'user-1',
          email: 'matthias@mastrao.com',
          // The backend model permits null even though the legacy API type does not.
          full_name: null as unknown as string,
          last_name: '',
          language: 'fr-fr',
          timezone: 'Europe/Paris',
        }}
      />
    )

    expect(screen.getAllByText('matthias@mastrao.com').length).toBeGreaterThan(
      0
    )
  })
})
