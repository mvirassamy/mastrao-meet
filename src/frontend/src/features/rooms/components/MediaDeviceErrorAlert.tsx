import type { MediaDeviceFailure } from 'livekit-client'
import { useTranslation } from 'react-i18next'
import { Button, Dialog, DialogActions, P } from '@/primitives'

export type MediaDeviceErrorAlertProps = {
  error?: MediaDeviceFailure | null
  kind?: MediaDeviceKind | null
  onClose: () => void
}

export const MediaDeviceErrorAlert = ({
  error,
  kind,
  onClose,
}: MediaDeviceErrorAlertProps) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'mediaErrorDialog' })

  if (!error || !kind) return

  return (
    <Dialog
      role="alertdialog"
      isOpen={!!error}
      onClose={onClose}
      title={t(`${error}.title.${kind}`)}
    >
      <P>{t(`${error}.body.${kind}`)}</P>
      <DialogActions>
        <Button variant="default" onPress={onClose}>
          {t('close')}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
