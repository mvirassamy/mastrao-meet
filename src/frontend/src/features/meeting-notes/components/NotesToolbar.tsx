import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { RedoIcon, UndoIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'

/**
 * A thin full-width bar under the tabs: enrichment on the left, then the save
 * status and the history arrows on the right.
 */
export function NotesToolbar({
  children,
  status,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
}: {
  children: ReactNode
  status: ReactNode
  canUndo: boolean
  canRedo: boolean
  onUndo: () => void
  onRedo: () => void
}) {
  const { t } = useTranslation('personalNotes')
  return (
    <div
      role="toolbar"
      aria-label={t('toolbar.label')}
      className={css({
        display: 'flex',
        alignItems: 'center',
        gap: '0.375rem',
        paddingX: '1rem',
        paddingY: '0.375rem',
        borderBottom: '1px solid token(colors.border)',
        fontSize: '0.75rem',
      })}
    >
      {children}
      <span className={css({ flex: 1 })} />
      {status}
      <span
        aria-hidden="true"
        className={css({
          width: '1px',
          height: '1.25rem',
          marginX: '0.25rem',
          backgroundColor: 'border',
        })}
      />
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={t('toolbar.undo')}
        tooltip={t('toolbar.undoShortcut')}
        isDisabled={!canUndo}
        onPress={onUndo}
      >
        <UndoIcon aria-hidden="true" />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={t('toolbar.redo')}
        tooltip={t('toolbar.redoShortcut')}
        isDisabled={!canRedo}
        onPress={onRedo}
      >
        <RedoIcon aria-hidden="true" />
      </Button>
    </div>
  )
}
