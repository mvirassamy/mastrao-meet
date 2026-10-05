import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
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
afterEach(cleanup)

const fill = () => {
  fireEvent.change(screen.getByLabelText('Title (optional)'), {
    target: { value: 'Équipe' },
  })
  fireEvent.change(screen.getByLabelText('Date'), {
    target: { value: '2026-10-07' },
  })
  fireEvent.change(screen.getByLabelText('Start time'), {
    target: { value: '10:00' },
  })
  fireEvent.change(screen.getByLabelText('End time'), {
    target: { value: '11:00' },
  })
}
const submit = () =>
  fireEvent.submit(
    screen.getByRole('button', { name: 'Create meeting' }).closest('form')!
  )

describe('schedule meeting dialog with real application primitives', () => {
  it('opens without creation and shows the browser time zone', () => {
    const create = vi.fn()
    render(<ScheduleMeetingDialog isOpen onClose={vi.fn()} onCreate={create} />)
    expect(
      screen.getByRole('dialog').getAttribute('aria-labelledby')
    ).toBeTruthy()
    expect(
      screen.getByText(
        `Time zone: ${Intl.DateTimeFormat().resolvedOptions().timeZone}`
      )
    ).toBeTruthy()
    expect(create).not.toHaveBeenCalled()
  })
  it('focuses the first invalid input and associates its error', () => {
    const create = vi.fn()
    render(<ScheduleMeetingDialog isOpen onClose={vi.fn()} onCreate={create} />)
    submit()
    const date = screen.getByLabelText('Date')
    expect(document.activeElement).toBe(date)
    expect(date.getAttribute('aria-invalid')).toBe('true')
    expect(
      document.getElementById(date.getAttribute('aria-describedby')!)
        ?.textContent
    ).toBe('Choose a valid date.')
    expect(create).not.toHaveBeenCalled()
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
    expect(
      (screen.getByLabelText('Title (optional)') as HTMLInputElement).value
    ).toBe('Équipe')
    expect((screen.getByLabelText('Date') as HTMLInputElement).value).toBe(
      '2026-10-07'
    )
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
    expect((screen.getByLabelText('Date') as HTMLInputElement).disabled).toBe(
      true
    )
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
