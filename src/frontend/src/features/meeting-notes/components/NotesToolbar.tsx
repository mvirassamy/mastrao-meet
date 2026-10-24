import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { RedoIcon, UndoIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'

/** The note's actions above its text: enrichment on the left, history on the right. */
export function NotesToolbar({
  children,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
}: {
  children: ReactNode
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
        gap: '0.5rem',
        paddingX: '1.25rem',
        paddingTop: '0.75rem',
      })}
    >
      {children}
      <span className={css({ flex: 1 })} />
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
