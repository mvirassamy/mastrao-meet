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
import GuestInvitation from './GuestInvitation'

const state = vi.hoisted(() => ({ redeemGuestShare: vi.fn() }))
const link = {
  kind: 'durable',
  organization: 'org_test',
  share: 'share_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef',
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('../api/redeemGuestInvitation', () => ({
  redeemGuestShare: state.redeemGuestShare,
  redeemGuestInvitation: vi.fn(),
}))
vi.mock('../utils/guestInvitationFragment', () => ({
  consumeGuestInvitationFragment: () => link,
  guestRedemptionId: () => 'redemption_test',
  forgetGuestRedemption: vi.fn(),
}))
vi.mock('../platformReturn', () => ({ clearPlatformReturnForRoomUrl: vi.fn() }))
vi.mock('@/layout/Screen', () => ({
  Screen: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/primitives', () => ({
  H: ({ children }: { children: ReactNode }) => <h1>{children}</h1>,
  Text: ({ children, role }: { children: ReactNode; role?: string }) => (
    <p role={role}>{children}</p>
  ),
  Button: ({
    children,
    onPress,
  }: {
    children: ReactNode
    onPress: () => void
  }) => (
    <button type="button" onClick={onPress}>
      {children}
    </button>
  ),
  LinkButton: ({ children, href }: { children: ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))

describe('guest invitation', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(cleanup)

  it('replaces the join page with a dedicated screen when the link is no longer valid', async () => {
    state.redeemGuestShare.mockRejectedValue(new ApiError(404, {}))
    render(<GuestInvitation />)

    fireEvent.click(
      screen.getByRole('button', { name: 'guestInvitation.submit' })
    )

    await waitFor(() =>
      expect(
        screen.getByRole('heading', {
          name: 'guestInvitation.unavailable.title',
        })
      ).toBeTruthy()
    )
    expect(screen.getByRole('alert').textContent).toBe(
      'guestInvitation.unavailable.body'
    )
    expect(
      screen.getByRole('link', { name: 'backToHome' }).getAttribute('href')
    ).toBe('/')
    expect(screen.queryByText('guestInvitation.title')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
    expect(
      screen.getByRole('main').querySelector('img')?.getAttribute('src')
    ).toBe('/assets/illustrations/lien-expire.webp')
  })

  it('keeps the join page with a retry when the service is temporarily unavailable', async () => {
    state.redeemGuestShare.mockRejectedValue(new ApiError(503, {}))
    render(<GuestInvitation />)

    fireEvent.click(
      screen.getByRole('button', { name: 'guestInvitation.submit' })
    )

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'guestInvitation.retry' })
      ).toBeTruthy()
    )
    expect(screen.getByText('guestInvitation.title')).toBeTruthy()
    expect(screen.getByRole('alert').textContent).toBe(
      'guestInvitation.temporaryError'
    )
    expect(screen.queryByText('guestInvitation.unavailable.title')).toBeNull()
  })
})
