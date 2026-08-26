import { cleanup, render, screen } from '@testing-library/react'
import { forwardRef, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import FeedbackRoute from './Feedback'

const setLocation = vi.fn()

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
  Button: ({
    children,
    onPress,
  }: {
    children: string
    onPress: () => void
  }) => (
    <button type="button" onClick={onPress}>
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

describe('Feedback Mastrao return', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
    window.history.replaceState({}, '', '/')
  })

  afterEach(cleanup)

  it('offers the fixed Platform resolver after a host meeting ends', () => {
    window.history.replaceState(
      {
        reason: 4,
        room_id: 'room_0123456789abcdef',
        platform_return: {
          url: 'https://platform.mastrao.test/api/meeting-return?organization_ref=organization_0123456789&meeting_ref=meeting_0123456789abcdef',
          expires_at: Math.floor(Date.now() / 1000) + 60,
        },
      },
      ''
    )

    render(<FeedbackRoute />)

    expect(
      screen.getByRole('button', { name: 'feedback.returnToMatter' })
    ).toBeTruthy()
    expect(document.activeElement).toBe(screen.getByRole('heading'))
  })

  it('keeps the ordinary Meet fallback when no verified return exists', () => {
    window.history.replaceState({}, '', '/')
    render(<FeedbackRoute />)

    expect(
      screen.queryByRole('button', { name: 'feedback.returnToMatter' })
    ).toBeNull()
    expect(screen.getByRole('button', { name: 'feedback.home' })).toBeTruthy()
  })

  it('restores the ended screen and verified return after a reload', () => {
    const roomId = 'room_0123456789abcdef0123456789abcdef'
    const descriptor = {
      url: 'https://platform.mastrao.test/api/meeting-return?organization_ref=organization_0123456789&meeting_ref=meeting_0123456789abcdef',
      expires_at: Math.floor(Date.now() / 1000) + 60,
    }
    window.sessionStorage.setItem(
      `mastrao-platform-return-v1:${roomId}`,
      JSON.stringify(descriptor)
    )
    window.history.replaceState(
      {},
      '',
      `/feedback?outcome=ended&room_id=${roomId}`
    )

    render(<FeedbackRoute />)

    expect(
      screen.getByRole('heading', { name: 'feedback.heading.meetingEnded' })
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'feedback.returnToMatter' })
    ).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'feedback.back' })).toBeNull()
  })
})
