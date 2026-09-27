import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SettingsDialog } from './SettingsDialog'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { changeLanguage: vi.fn() },
  }),
  Trans: () => null,
}))

vi.mock('@/features/auth/api/useUser', () => ({
  useUser: () => ({
    isLoggedIn: true,
    user: {
      id: 'u1',
      email: 'matthias@mastrao.com',
      full_name: 'Matthias Virassamy',
    },
  }),
}))

vi.mock('@/i18n/useLanguageLabels', () => ({
  useLanguageLabels: () => ({
    languagesList: [{ value: 'fr', label: 'Français' }],
    currentLanguage: { key: 'fr' },
  }),
}))

vi.mock('./tabs/RoomsTab', () => ({ RoomsTab: () => null }))

vi.mock('@/features/rooms/livekit/hooks/useMediaQuery', () => ({
  useMediaQuery: () => true,
}))

afterEach(cleanup)

describe('SettingsDialog appearance', () => {
  it('uses the Mastrao application dialog from the workspace', () => {
    render(<SettingsDialog appearance="app" isOpen />)
    const dialog = screen.getByRole('dialog', { name: 'dialog.heading' })
    expect(dialog.closest('.authenticated-meet-workspace')).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'tabs.general' })).toBeTruthy()
    expect(screen.getByText('Matthias Virassamy')).toBeTruthy()
  })

  it('uses the same dialog from every other entry point', () => {
    render(<SettingsDialog isOpen />)
    const dialog = screen.getByRole('dialog', { name: 'dialog.heading' })
    expect(dialog.closest('.authenticated-meet-workspace')).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'tabs.general' })).toBeTruthy()
  })
})
