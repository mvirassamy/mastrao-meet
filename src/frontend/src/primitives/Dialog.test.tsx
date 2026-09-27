import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Dialog } from './Dialog'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(cleanup)

const overlayOf = (name: string) =>
  screen.getByRole('dialog', { name }).closest('.authenticated-meet-workspace')

describe('Dialog', () => {
  it('draws room dialogs with the standard, without a live backdrop blur', () => {
    render(
      <Dialog title="Fin de réunion" isOpen>
        contenu
      </Dialog>
    )
    const overlay = overlayOf('Fin de réunion')
    expect(overlay).toBeTruthy()
    // Blurring the live video behind the dialog would cost a pass per frame.
    expect(overlay?.className).not.toContain('backdrop-filter')
    expect(overlay?.className).not.toMatch(/bkdp|blur/)
  })

  it('keeps the Platform blurred backdrop in the authenticated workspace', () => {
    render(
      <Dialog title="Rejoindre" appearance="app" isOpen>
        contenu
      </Dialog>
    )
    expect(overlayOf('Rejoindre')?.className).toMatch(/bkdp|blur/)
  })
})
