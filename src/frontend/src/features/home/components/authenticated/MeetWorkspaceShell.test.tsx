import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MeetWorkspaceShell } from './MeetWorkspaceShell'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'dashboard.sidebar.label': 'Navigation Visio',
        'dashboard.sidebar.open': 'Ouvrir la navigation',
      })[key] ?? key,
  }),
}))

vi.mock('@/primitives', () => ({
  Button: ({
    children,
    onPress,
    'aria-label': ariaLabel,
  }: {
    children: React.ReactNode
    onPress?: () => void
    'aria-label'?: string
  }) => (
    <button type="button" aria-label={ariaLabel} onClick={onPress}>
      {children}
    </button>
  ),
}))

vi.mock('./MeetSidebar', () => ({
  MeetSidebar: ({
    collapsed,
    mobile,
    onToggle,
    onNavigate,
  }: {
    collapsed?: boolean
    mobile?: boolean
    onToggle: () => void
    onNavigate?: () => void
  }) => (
    <div
      data-testid={mobile ? 'mobile-sidebar' : 'desktop-sidebar'}
      data-collapsed={collapsed ? 'true' : 'false'}
    >
      <button type="button" onClick={onToggle}>
        {mobile ? 'close-mobile' : 'toggle-desktop'}
      </button>
      {mobile && (
        <button type="button" onClick={onNavigate}>
          navigate-mobile
        </button>
      )}
    </div>
  ),
}))

const user = {
  id: 'user-1',
  email: 'matthias@mastrao.com',
  full_name: 'Matthias',
  last_name: 'Doe',
  language: 'fr-fr' as const,
  timezone: 'Europe/Paris',
}

afterEach(cleanup)

describe('MeetWorkspaceShell', () => {
  it('keeps the authenticated palette scope and toggles the desktop rail', () => {
    const { container } = render(
      <MeetWorkspaceShell user={user} toolbar={<span>toolbar</span>}>
        content
      </MeetWorkspaceShell>
    )

    expect(container.firstElementChild?.classList).toContain(
      'authenticated-meet-workspace'
    )
    expect(screen.getByTestId('desktop-sidebar').dataset.collapsed).toBe(
      'false'
    )

    fireEvent.click(screen.getByRole('button', { name: 'toggle-desktop' }))

    expect(screen.getByTestId('desktop-sidebar').dataset.collapsed).toBe('true')
  })

  it('closes the mobile drawer after navigation', () => {
    render(
      <MeetWorkspaceShell user={user} toolbar={<span>toolbar</span>}>
        content
      </MeetWorkspaceShell>
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'Ouvrir la navigation' })
    )
    const drawer = screen.getByRole('dialog', { name: 'Navigation Visio' })
    expect(drawer).toBeTruthy()
    expect(drawer.closest('.authenticated-meet-workspace')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'navigate-mobile' }))

    expect(
      screen.queryByRole('dialog', { name: 'Navigation Visio' })
    ).toBeNull()
  })
})
