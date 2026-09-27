import { useTranslation } from 'react-i18next'
import { Button, Dialog, DialogActions, P } from '@/primitives'

export const MuteAlertDialog = ({
  isOpen,
  onClose,
  onSubmit,
  name,
}: {
  isOpen: boolean
  onClose: () => void
  onSubmit: () => void
  name: string
}) => {
  const { t } = useTranslation('rooms', {
    keyPrefix: 'participants.muteParticipantAlert',
  })
  return (
    <Dialog
      isOpen={isOpen}
      role="alertdialog"
      aria-label={t('heading', { name })}
    >
      <P>{t('description', { name })}</P>
      <DialogActions>
        <Button variant="outline" onPress={onClose}>
          {t('cancel')}
        </Button>
        <Button variant="default" onPress={onSubmit}>
          {t('confirm')}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
