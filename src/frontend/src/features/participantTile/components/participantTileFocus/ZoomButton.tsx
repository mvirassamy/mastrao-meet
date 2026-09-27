import { TrackReferenceOrPlaceholder } from '@livekit/components-core'
import { useTranslation } from 'react-i18next'
import { useFullScreen } from '@/features/rooms/livekit/hooks/useFullScreen'
import { Button } from '@/primitives'
import { FullscreenIcon } from '@/icons'
export const ZoomButton = ({
  trackRef,
}: {
  trackRef: TrackReferenceOrPlaceholder
}) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'participantTileFocus' })
  const { toggleFullScreen, isFullscreenAvailable } = useFullScreen({
    trackRef,
  })

  if (!isFullscreenAvailable) {
    return
  }

  return (
    <Button
      size="icon-sm"
      variant="ghost"
      aria-label={t('fullScreen')}
      tooltip={t('fullScreen')}
      onPress={() => toggleFullScreen()}
    >
      <FullscreenIcon />
    </Button>
  )
}
