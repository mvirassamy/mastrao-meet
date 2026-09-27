import { Button, Dialog, DialogActions, P } from '@/primitives'
import { useTranslation } from 'react-i18next'
import { useSnapshot } from 'valtio'
import { recordingStore } from '@/stores/recording'

export const ErrorAlertDialog = () => {
  const recordingSnap = useSnapshot(recordingStore)
  const { t } = useTranslation('rooms', {
    keyPrefix: 'errorRecordingAlertDialog',
  })

  return (
    <Dialog
      isOpen={!!recordingSnap.isErrorDialogOpen}
      role="alertdialog"
      title={t('title')}
      aria-label={t('title')}
    >
      <P>{t(`body.${recordingSnap.isErrorDialogOpen}`)}</P>
      <DialogActions>
        <Button
          variant="default"
          onPress={() => (recordingStore.isErrorDialogOpen = '')}
        >
          {t('button')}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
