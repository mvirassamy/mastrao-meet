import { type DialogProps } from '@/primitives'
import { AppSettingsDialog } from './AppSettingsDialog'

export type SettingsDialogProps = Pick<
  DialogProps,
  'isOpen' | 'onOpenChange' | 'appearance'
>

/**
 * Settings dialog opened from the header, the workspace sidebar and the room
 * device menus. Every entry point shares the Mastrao application dialog.
 */
export const SettingsDialog = ({
  isOpen,
  onOpenChange,
  appearance,
}: SettingsDialogProps) => (
  <AppSettingsDialog
    isOpen={isOpen}
    onOpenChange={onOpenChange}
    // Blur only in the authenticated workspace: never over live video.
    backdrop={appearance === 'app' ? 'blur' : 'dim'}
  />
)
