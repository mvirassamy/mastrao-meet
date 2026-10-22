import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import { ApiError } from '@/api/ApiError'
import { SettingsDialog } from './SettingsDialog'

const auth = vi.hoisted(() => ({ isLoggedIn: true as boolean | undefined }))

vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))

vi.mock('@/api/useConfig', () => ({
  useConfig: () => ({ data: { use_proconnect_button: false } }),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { changeLanguage: vi.fn() },
  }),
  Trans: () => null,
}))

vi.mock('@/features/auth/api/useUser', () => ({
  useUser: () => ({
    isLoggedIn: auth.isLoggedIn,
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

beforeEach(() => {
  auth.isLoggedIn = true
  vi.mocked(fetchApi).mockReset().mockRejectedValue(new ApiError(404, {}))
})

const renderSettings = (isOpen = true) => {
  const client = new QueryClient()
  const rendered = render(<SettingsDialog isOpen={isOpen} />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  })
  return { ...rendered, client }
}

describe('SettingsDialog appearance', () => {
  it('uses the Mastrao application dialog from the workspace', () => {
    const client = new QueryClient()
    render(
      <QueryClientProvider client={client}>
        <SettingsDialog appearance="app" isOpen />
      </QueryClientProvider>
    )
    const dialog = screen.getByRole('dialog', { name: 'dialog.heading' })
    expect(dialog.closest('.authenticated-meet-workspace')).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'tabs.general' })).toBeTruthy()
    expect(screen.getByText('Matthias Virassamy')).toBeTruthy()
  })

  it('uses the same dialog from every other entry point', () => {
    renderSettings()
    const dialog = screen.getByRole('dialog', { name: 'dialog.heading' })
    expect(dialog.closest('.authenticated-meet-workspace')).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'tabs.general' })).toBeTruthy()
  })
})

describe('Usage and budget settings link', () => {
  it('links to Platform for the explicit organization returned by the bridge', async () => {
    const url = 'https://platform.test/orgs/example-org/settings/usage-budget'
    vi.mocked(fetchApi).mockResolvedValue({
      organization_slug: 'example-org',
      usage_budget_url: url,
    })
    renderSettings()
    const link = await screen.findByRole('link', {
      name: 'usageBudget.link (usageBudget.opensInNewTab)',
    })
    expect(link.getAttribute('href')).toBe(url)
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
    expect(screen.queryByText('example-org')).toBeNull()
    expect(fetchApi).toHaveBeenCalledExactlyOnceWith('usage-budget/')
  })

  it.each([false, undefined])(
    'does not request a descriptor when authentication is %s',
    (isLoggedIn) => {
      auth.isLoggedIn = isLoggedIn
      renderSettings()
      expect(fetchApi).not.toHaveBeenCalled()
      expect(
        screen.queryByRole('link', { name: /usageBudget.link/ })
      ).toBeNull()
    }
  )

  it('does not request the descriptor while settings are closed', () => {
    renderSettings(false)
    expect(fetchApi).not.toHaveBeenCalled()
  })

  it('hides a 404 without retrying or presenting it as an outage', async () => {
    const { client } = renderSettings()
    await waitFor(() =>
      expect(client.getQueryData(['usage-budget', 'u1'])).toBeNull()
    )
    expect(fetchApi).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('link', { name: /usageBudget.link/ })).toBeNull()
    expect(screen.queryByText('usageBudget.unavailable')).toBeNull()
  })

  it.each([401, 503])(
    'shows unavailability for HTTP %s without retrying',
    async (status) => {
      vi.mocked(fetchApi).mockRejectedValue(new ApiError(status, {}))
      renderSettings()
      expect(await screen.findByText('usageBudget.unavailable')).toBeTruthy()
      expect(
        screen.queryByRole('link', { name: /usageBudget.link/ })
      ).toBeNull()
      expect(fetchApi).toHaveBeenCalledTimes(1)
    }
  )
})
