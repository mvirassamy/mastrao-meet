import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiAccessLevel, type ApiRoom } from '@/features/rooms/api/ApiRoom'
import { JoinMeetingDialog } from './JoinMeetingDialog'
import { LaterMeetingDialog } from './LaterMeetingDialog'

vi.mock('@/primitives', () => ({
  Bold: ({ children }: { children: React.ReactNode }) => (
    <strong>{children}</strong>
  ),
  Button: ({ children }: { children: React.ReactNode }) => (
    <button type="button">{children}</button>
  ),
  Dialog: ({
    appearance,
    children,
    title,
  }: {
    appearance?: string
    children: React.ReactNode
    title?: string
  }) => (
    <section role="dialog" data-appearance={appearance} aria-label={title}>
      {children}
    </section>
  ),
  Field: ({ label }: { label: string }) => <input aria-label={label} />,
  Form: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  H: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  P: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  Text: ({ children }: { children: React.ReactNode }) => (
    <span>{children}</span>
  ),
  Ul: ({ children }: { children: React.ReactNode }) => <ul>{children}</ul>,
}))
vi.mock('@/styled-system/jsx', () => ({
  HStack: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}))
vi.mock('@/navigation/getRouteUrl', () => ({
  getRouteUrl: () => 'https://meet.example/abc-defg-hij',
}))
vi.mock('@/navigation/navigateTo', () => ({ navigateTo: vi.fn() }))
vi.mock('@/features/rooms/livekit/hooks/useTelephony', () => ({
  useTelephony: () => undefined,
}))
vi.mock('@/features/rooms/livekit/hooks/useCopyRoomToClipboard', () => ({
  useCopyRoomToClipboard: () => ({
    isCopied: false,
    copyRoomToClipboard: vi.fn(),
    isRoomUrlCopied: false,
    copyRoomUrlToClipboard: vi.fn(),
    shareUrl: 'https://meet.example/abc-defg-hij',
    shareUrlDisplay: 'meet.example/abc-defg-hij',
    isShareLinkPending: false,
    shareLinkError: null,
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

const room: ApiRoom = {
  id: 'room-id',
  name: 'Room',
  slug: 'abc-defg-hij',
  is_administrable: true,
  access_level: ApiAccessLevel.RESTRICTED,
}

describe('application dialogs', () => {
  afterEach(cleanup)

  it('uses the application appearance for joining a meeting', () => {
    render(<JoinMeetingDialog />)

    expect(screen.getByRole('dialog').dataset.appearance).toBe('app')
  })

  it('uses the application appearance for a meeting created for later', () => {
    render(<LaterMeetingDialog room={room} />)

    expect(screen.getByRole('dialog').dataset.appearance).toBe('app')
  })
})
