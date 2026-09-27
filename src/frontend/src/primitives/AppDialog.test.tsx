import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppDialog } from './AppDialog'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(cleanup)

describe('AppDialog', () => {
  it('renders a titled dialog in the workspace scope and closes', () => {
    const onOpenChange = vi.fn()
    render(
      <AppDialog
        title="Paramètres"
        description="Réglages du compte"
        isOpen
        onOpenChange={onOpenChange}
        footer={<button type="button">Enregistrer</button>}
      >
        contenu
      </AppDialog>
    )

    const dialog = screen.getByRole('dialog', { name: 'Paramètres' })
    expect(dialog.closest('.authenticated-meet-workspace')).toBeTruthy()
    expect(screen.getByText('Réglages du compte')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'closeDialog' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('has no close button for alert dialogs', () => {
    render(
      <AppDialog title="Attention" role="alertdialog" isOpen>
        contenu
      </AppDialog>
    )
    expect(screen.getByRole('alertdialog', { name: 'Attention' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'closeDialog' })).toBeNull()
  })
})
