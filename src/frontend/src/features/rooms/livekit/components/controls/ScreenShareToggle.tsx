import { ToggleButton } from '@/primitives'
import { ShareBoxIcon, StopCircleIcon } from '@/icons'
import { useTranslation } from 'react-i18next'
import {
  useTrackToggle,
  type UseTrackToggleProps,
} from '@livekit/components-react'
import { Track } from 'livekit-client'
import React from 'react'
import { type ButtonRecipeProps } from '@/primitives/buttonRecipe'
import { ToggleButtonProps } from '@/primitives/ToggleButton'
import { TrackSource } from '@livekit/protocol'
import { useCanPublishTrack } from '@/features/rooms/livekit/hooks/useCanPublishTrack'

type Props = Omit<
  UseTrackToggleProps<Track.Source.ScreenShare>,
  'source' | 'captureOptions'
> &
  Pick<NonNullable<ButtonRecipeProps>, 'variant'> &
  ToggleButtonProps

export const ScreenShareToggle = ({
  variant = 'outline',
  onPress,
  ...props
}: Props) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'controls.screenShare' })
  const { buttonProps, enabled } = useTrackToggle({
    ...props,
    source: Track.Source.ScreenShare,
    captureOptions: { audio: true, selfBrowserSurface: 'include' },
  })

  const tooltipLabel = enabled ? 'stop' : 'start'
  const Icon = enabled ? StopCircleIcon : ShareBoxIcon

  const canShareScreen = useCanPublishTrack(TrackSource.SCREEN_SHARE)

  return (
    <ToggleButton
      shape="circle"
      isSelected={enabled}
      isDisabled={!canShareScreen}
      variant={variant}
      aria-label={t(tooltipLabel)}
      tooltip={t(tooltipLabel)}
      onPress={(e) => {
        buttonProps.onClick?.(
          e as unknown as React.MouseEvent<HTMLButtonElement, MouseEvent>
        )
        onPress?.(e)
      }}
      data-attr={`controls-screenshare-${tooltipLabel}`}
      {...props}
    >
      <Icon size={24} aria-hidden="true" />
    </ToggleButton>
  )
}
