import { useTranslation } from 'react-i18next'
import { useSnapshot } from 'valtio'
import { css } from '@/styled-system/css'
import { Button, Dialog, DialogActions, P } from '@/primitives'
import {
  closeSilentMicDialog,
  discardSilentMicDetection,
  silentMicStore,
} from '@/stores/silentMic'

/**
 * Opened from the "!" badge on the microphone toggle when the silent-mic
 * check tripped (see stores/silentMic.ts). Explains the likely causes
 * and lets the user opt out of the detection for good.
 */
export const SilentMicDialog = () => {
  const { t } = useTranslation('rooms', { keyPrefix: 'silentMic.dialog' })
  const { isDialogOpen } = useSnapshot(silentMicStore)

  return (
    <Dialog
      isOpen={isDialogOpen}
      role="dialog"
      type="flex"
      title={t('title')}
      aria-label={t('title')}
      onClose={closeSilentMicDialog}
    >
      <div>
        <P>{t('intro')}</P>
        <ul className={css({ listStyle: 'disc', paddingLeft: '24px' })}>
          <li>{t('causes.system')}</li>
          <li>{t('causes.hardware')}</li>
          <li>{t('causes.wrongDevice')}</li>
        </ul>
        <P>{t('hint')}</P>
        <DialogActions>
          <Button variant="outline" onPress={discardSilentMicDetection}>
            {t('discard')}
          </Button>
          <Button variant="default" onPress={closeSilentMicDialog}>
            {t('close')}
          </Button>
        </DialogActions>
      </div>
    </Dialog>
  )
}
