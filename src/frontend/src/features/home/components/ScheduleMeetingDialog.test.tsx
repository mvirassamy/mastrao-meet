import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ScheduleMeetingDialog } from './ScheduleMeetingDialog'
import translations from '@/locales/en/home.json'

vi.mock('react-i18next', () => ({
  useTranslation: (_ns: string, options?: { keyPrefix?: string }) => ({
    t: (key: string, values?: { timeZone: string }) => {
      if (!options?.keyPrefix) return key
      const copy = translations.scheduleMeetingDialog
      if (key.startsWith('errors.'))
        return copy.errors[key.slice(7) as keyof typeof copy.errors]
      return copy[key as keyof typeof copy]
        .toString()
        .replace('{{timeZone}}', values?.timeZone ?? '')
    },
  }),
}))

beforeEach(() => {
  // Only the clock is fixed: "today" is Monday 5 October 2026.
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-05T09:00:00'))
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

const dateButton = () => screen.getByRole('button', { name: /^Date/ })
const startInput = () => screen.getByRole('combobox', { name: 'Start' })
const endInput = () => screen.getByRole('combobox', { name: 'End' })

const fill = () => {
  fireEvent.change(screen.getByLabelText('Title'), {
    target: { value: 'Équipe' },
  })
  fireEvent.click(dateButton())
  fireEvent.click(screen.getByRole('option', { name: /tomorrow/ }))
  fireEvent.change(startInput(), { target: { value: '10:00' } })
  fireEvent.change(endInput(), { target: { value: '11:00' } })
}
const submit = () =>
  fireEvent.submit(
    screen
      .getByRole('button', { name: 'Create meeting' })
      .closest('div')!
      .parentElement!.querySelector('form')!
  )

describe('schedule meeting dialog with real application primitives', () => {
  it('opens without creation and shows the browser time zone', () => {
    const create = vi.fn()
    render(<ScheduleMeetingDialog isOpen onClose={vi.fn()} onCreate={create} />)
    expect(
      screen.getByRole('dialog').getAttribute('aria-labelledby')
    ).toBeTruthy()
    const city = Intl.DateTimeFormat()
      .resolvedOptions()
      .timeZone.split('/')
      .pop()!
      .replaceAll('_', ' ')
    expect(screen.getByText(`Time zone: ${city}`)).toBeTruthy()
    expect(create).not.toHaveBeenCalled()
  })

  it('focuses the first invalid field and associates its error', () => {
    const create = vi.fn()
    render(<ScheduleMeetingDialog isOpen onClose={vi.fn()} onCreate={create} />)
    submit()
    const date = dateButton()
    expect(document.activeElement).toBe(date)
    expect(date.hasAttribute('data-invalid')).toBe(true)
    expect(
      document.getElementById(date.getAttribute('aria-describedby')!)
        ?.textContent
    ).toBe('Choose a valid date.')
    expect(create).not.toHaveBeenCalled()
  })

  it('creates the meeting from a date shortcut and the chosen times', async () => {
    const create = vi.fn().mockResolvedValue(undefined)
    render(<ScheduleMeetingDialog isOpen onClose={vi.fn()} onCreate={create} />)
    fill()
    expect(dateButton().textContent).toContain('October 6, 2026')
    submit()
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
    expect(create.mock.calls[0][0]).toMatchObject({
      title: 'Équipe',
      startsAt: new Date('2026-10-06T10:00:00').getTime() / 1000,
      endsAt: new Date('2026-10-06T11:00:00').getTime() / 1000,
    })
  })

  it('does not offer a day before today', () => {
    render(
      <ScheduleMeetingDialog isOpen onClose={vi.fn()} onCreate={vi.fn()} />
    )
    fireEvent.click(dateButton())
    const yesterday = screen.getByRole('button', { name: /October 4, 2026/ })
    expect(yesterday.getAttribute('aria-disabled')).toBe('true')
  })

  it('reads a typed time and lists the end times after the start', () => {
    // jsdom has no CSS.escape, which the open list uses to scroll.
    vi.stubGlobal('CSS', { escape: (value: string) => value })
    render(
      <ScheduleMeetingDialog isOpen onClose={vi.fn()} onCreate={vi.fn()} />
    )
    fireEvent.change(startInput(), { target: { value: '9h' } })
    fireEvent.blur(startInput())
    expect((startInput() as HTMLInputElement).value).toBe('09:00')

    fireEvent.click(
      screen.getByRole('button', { name: /timeSelect.showSlots End/ })
    )
    const options = screen.getAllByRole('option')
    expect(options[0].textContent).toContain('09:15')
    expect(options[0].textContent).toContain('timeSelect.duration.minutes')
    vi.unstubAllGlobals()
  })

  it('keeps values after failure and allows retry', async () => {
    const create = vi
      .fn()
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce(undefined)
    render(<ScheduleMeetingDialog isOpen onClose={vi.fn()} onCreate={create} />)
    fill()
    submit()
    await screen.findByRole('alert')
    expect((screen.getByLabelText('Title') as HTMLInputElement).value).toBe(
      'Équipe'
    )
    expect(dateButton().textContent).toContain('October 6, 2026')
    expect((startInput() as HTMLInputElement).value).toBe('10:00')
    submit()
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2))
    expect(create.mock.calls[1]).toEqual(create.mock.calls[0])
  })

  it('locks concurrent submits and cancellation while creation is pending', async () => {
    let resolve!: () => void
    const create = vi.fn(
      () =>
        new Promise<void>((done) => {
          resolve = done
        })
    )
    const close = vi.fn()
    render(<ScheduleMeetingDialog isOpen onClose={close} onCreate={create} />)
    fill()
    submit()
    submit()
    expect(create).toHaveBeenCalledTimes(1)
    expect(
      (screen.getByRole('button', { name: 'cancel' }) as HTMLButtonElement)
        .disabled
    ).toBe(true)
    expect((dateButton() as HTMLButtonElement).disabled).toBe(true)
    expect((startInput() as HTMLInputElement).disabled).toBe(true)
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(close).not.toHaveBeenCalled()
    await act(async () => resolve())
  })

  it('cancels without creation', () => {
    const create = vi.fn(),
      close = vi.fn()
    render(<ScheduleMeetingDialog isOpen onClose={close} onCreate={create} />)
    fireEvent.click(screen.getByRole('button', { name: 'cancel' }))
    expect(close).toHaveBeenCalledTimes(1)
    expect(create).not.toHaveBeenCalled()
  })
})
