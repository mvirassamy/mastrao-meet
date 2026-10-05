import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MissingGuestLink } from './MissingGuestLink'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'guestInvitation.missing.label': 'Lien d’invitation',
        'guestInvitation.missing.submit': 'Rejoindre',
        'guestInvitation.missing.error': 'Lien incomplet',
      })[key] ?? key,
  }),
}))

const share = 'share_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef'

const paste = (value: string) => {
  const input = screen.getByLabelText('Lien d’invitation')
  fireEvent.change(input, { target: { value } })
  fireEvent.submit(input.closest('form') as HTMLFormElement)
}

afterEach(cleanup)

describe('MissingGuestLink', () => {
  it('joins with a complete pasted guest link', () => {
    window.history.replaceState(null, '', '/guest')
    const onLink = vi.fn()
    render(<MissingGuestLink onLink={onLink} />)

    paste(
      `${window.location.origin}/guest#organization=org_test&share=${share}`
    )

    expect(onLink).toHaveBeenCalledWith({
      kind: 'durable',
      organization: 'org_test',
      share,
    })
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('keeps an incomplete link on the page with an accessible error', () => {
    window.history.replaceState(null, '', '/guest')
    const onLink = vi.fn()
    render(<MissingGuestLink onLink={onLink} />)

    paste(`${window.location.origin}/guest#organization=org_test`)

    expect(screen.getByRole('alert').textContent).toBe('Lien incomplet')
    expect(
      screen.getByLabelText('Lien d’invitation').getAttribute('aria-invalid')
    ).toBe('true')
    expect(onLink).not.toHaveBeenCalled()
  })

  it('cannot be submitted while the field is empty', () => {
    render(<MissingGuestLink onLink={vi.fn()} />)

    expect(
      (screen.getByRole('button', { name: 'Rejoindre' }) as HTMLButtonElement)
        .disabled
    ).toBe(true)
  })
})
