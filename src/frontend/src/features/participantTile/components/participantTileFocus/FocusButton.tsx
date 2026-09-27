import {
  isEqualTrackRef,
  TrackReferenceOrPlaceholder,
} from '@livekit/components-core'
import { useTranslation } from 'react-i18next'
import { useSnapshot } from 'valtio'
import { clearPinnedTrack, layoutStore, setPinnedTrack } from '@/stores/layout'
import { Button } from '@/primitives'
import { PinIcon, UnpinIcon } from '@/icons'
export const FocusButton = ({
  trackRef,
}: {
  trackRef: TrackReferenceOrPlaceholder
}) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'participantTileFocus' })

  const { pinnedTrackRef } = useSnapshot(layoutStore)
  const inFocus = isEqualTrackRef(trackRef, pinnedTrackRef)

  return (
    <Button
      size="icon-sm"
      variant="ghost"
      aria-label={inFocus ? t('pin.disable') : t('pin.enable')}
      tooltip={inFocus ? t('pin.disable') : t('pin.enable')}
      onPress={() => (inFocus ? clearPinnedTrack() : setPinnedTrack(trackRef))}
    >
      {inFocus ? <UnpinIcon /> : <PinIcon />}
    </Button>
  )
}
