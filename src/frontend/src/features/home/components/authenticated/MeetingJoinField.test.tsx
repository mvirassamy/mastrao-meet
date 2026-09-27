import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MeetingJoinField } from './MeetingJoinField'

const navigateTo = vi.hoisted(() => vi.fn())

vi.mock('@/navigation/navigateTo', () => ({ navigateTo }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'dashboard.join.label': 'Code ou lien de réunion',
        'dashboard.join.placeholder': 'Saisir un code ou un lien',
        'dashboard.join.submit': 'Participer',
        'dashboard.join.error': 'Code invalide',
      })[key] ?? key,
  }),
}))

describe('MeetingJoinField', () => {
  beforeEach(() => navigateTo.mockReset())

  it('keeps invalid input on the page with an accessible error', () => {
    render(<MeetingJoinField />)
    const input = screen.getByLabelText('Code ou lien de réunion')
    fireEvent.change(input, { target: { value: 'invalide' } })
    fireEvent.blur(input)

    expect(screen.getByRole('alert').textContent).toBe('Code invalide')
    expect(
      (screen.getByRole('button', { name: 'Participer' }) as HTMLButtonElement)
        .disabled
    ).toBe(true)
    expect(navigateTo).not.toHaveBeenCalled()
  })

  it('submits the canonical room id from a compact code', () => {
    render(<MeetingJoinField />)
    const input = screen.getByLabelText('Code ou lien de réunion')
    fireEvent.change(input, { target: { value: 'ABCDEFGHIJ' } })
    fireEvent.submit(input.closest('form') as HTMLFormElement)

    expect(navigateTo).toHaveBeenCalledWith('room', 'abc-defg-hij')
  })
})
