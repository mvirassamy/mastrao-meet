import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiAccessLevel, type ApiRoom } from '@/features/rooms/api/ApiRoom'
import { createGuestInvitationShare } from '@/features/rooms/api/createGuestInvitationShare'
import homeTranslations from '@/locales/fr/home.json'
import { LaterMeetingDialog } from './LaterMeetingDialog'

vi.mock('@/primitives', () => ({
  Bold: ({ children }: { children: ReactNode }) => <strong>{children}</strong>,
  Button: ({
    children,
    isDisabled,
    onPress,
    'aria-label': ariaLabel,
  }: {
    children: ReactNode
    isDisabled?: boolean
    onPress?: () => void
    'aria-label'?: string
  }) => (
    <button disabled={isDisabled} onClick={onPress} aria-label={ariaLabel}>
      {children}
    </button>
  ),
  Dialog: ({ children, title }: { children: ReactNode; title: string }) => (
    <section role="dialog" aria-label={title}>
      {children}
    </section>
  ),
  P: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  Text: ({ children, role }: { children: ReactNode; role?: string }) => (
    <span role={role}>{children}</span>
  ),
}))

const telephony = vi.hoisted(() => ({
  enabled: false,
  country: 'FR',
  internationalPhoneNumber: '+33123456789',
}))
vi.mock('@/features/rooms/livekit/hooks/useTelephony', () => ({
  useTelephony: () => telephony,
}))
vi.mock('@/features/rooms/api/createGuestInvitationShare', () => ({
  createGuestInvitationShare: vi.fn(),
}))
vi.mock('react-i18next', () => ({
  useTranslation: (namespace: string) => ({
    t: (key: string, values?: { roomUrl?: string; emails?: string }) => {
      if (namespace === 'home') {
        const translations = homeTranslations.laterMeetingDialog
        const text = translations[key as keyof typeof translations]
        return (typeof text === 'string' ? text : key).replace(
          '{{emails}}',
          values?.emails ?? ''
        )
      }
      return values?.roomUrl ?? key
    },
  }),
}))

const room: ApiRoom = {
  id: 'room-id',
  name: 'Room',
  slug: 'room_0123456789abcdef0123456789abcdef',
  is_administrable: false,
  access_level: ApiAccessLevel.RESTRICTED,
  pin_code: '123456',
}
const inviteUrl = `${window.location.origin}/guest#invite=aaa.bbb.ccc`
const createShare = vi.mocked(createGuestInvitationShare)
const writeText = vi.fn()
let client: QueryClient

const renderDialog = () =>
  render(
    <QueryClientProvider client={client}>
      <LaterMeetingDialog room={room} />
    </QueryClientProvider>
  )

describe('LaterMeetingDialog invitation sharing', () => {
  beforeEach(() => {
    client = new QueryClient()
    createShare.mockReset()
    createShare.mockResolvedValue(inviteUrl)
    writeText.mockReset()
    writeText.mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
  })

  afterEach(() => {
    cleanup()
    client.clear()
  })

  it('shows SMTP acceptance without claiming recipient delivery', () => {
    render(
      <QueryClientProvider client={client}>
        <LaterMeetingDialog
          room={room}
          invitations={[
            {
              invitation_ref: 'invitation_0123456789',
              email: 'alice@example.com',
              delivery_state: 'sent',
            },
          ]}
        />
      </QueryClientProvider>
    )
    expect(
      screen
        .getByText(homeTranslations.laterMeetingDialog.invitationsSubmitted)
        .getAttribute('role')
    ).toBe('status')
  })

  it('identifies uncertain sends without claiming notification or agreement', () => {
    render(
      <QueryClientProvider client={client}>
        <LaterMeetingDialog
          room={room}
          invitations={[
            {
              invitation_ref: 'invitation_0123456789',
              email: 'alice@example.com',
              delivery_state: 'unknown',
            },
          ]}
        />
      </QueryClientProvider>
    )
    expect(
      screen
        .getByText(/Envoi non confirmé pour : alice@example.com/)
        .getAttribute('role')
    ).toBe('status')
    expect(
      screen.queryByText(
        homeTranslations.laterMeetingDialog.invitationsSubmitted
      )
    ).toBeNull()
  })

  it.each([false, true])(
    'displays and copies the invitation with telephony enabled: %s',
    async (enabled) => {
      telephony.enabled = enabled
      renderDialog()

      await screen.findByText(`${window.location.host}/guest`)
      expect(screen.queryByText(new RegExp(room.slug))).toBeNull()
      expect(screen.queryByText(/aaa\.bbb\.ccc/)).toBeNull()

      fireEvent.click(
        screen.getByRole('button', {
          name: homeTranslations.laterMeetingDialog.copy,
        })
      )
      await waitFor(() =>
        expect(writeText).toHaveBeenCalledWith(
          expect.stringContaining(inviteUrl)
        )
      )
      if (enabled) {
        fireEvent.click(
          screen.getByRole('button', {
            name: homeTranslations.laterMeetingDialog.copyUrl,
          })
        )
        await waitFor(() => expect(writeText).toHaveBeenCalledWith(inviteUrl))
      }
      expect(createShare).toHaveBeenCalledOnce()
    }
  )

  it.each([false, true])(
    'prevents copying while the invitation is pending with telephony enabled: %s',
    (enabled) => {
      telephony.enabled = enabled
      createShare.mockReturnValue(new Promise(() => {}))
      renderDialog()

      expect(screen.getByRole('status').textContent).toBe(
        homeTranslations.laterMeetingDialog.preparing
      )
      for (const button of screen.getAllByRole('button')) {
        expect((button as HTMLButtonElement).disabled).toBe(true)
        fireEvent.click(button)
      }
      expect(writeText).not.toHaveBeenCalled()
    }
  )

  it.each([false, true])(
    'reports an unavailable invitation and prevents copying with telephony enabled: %s',
    async (enabled) => {
      telephony.enabled = enabled
      createShare.mockRejectedValue(new Error('Invitation unavailable'))
      renderDialog()

      expect((await screen.findByRole('alert')).textContent).toBe(
        homeTranslations.laterMeetingDialog.unavailable
      )
      for (const button of screen.getAllByRole('button')) {
        expect((button as HTMLButtonElement).disabled).toBe(true)
        fireEvent.click(button)
      }
      expect(writeText).not.toHaveBeenCalled()
    }
  )
})
