import { useTranslation } from 'react-i18next'
import { useTrackToggle, UseTrackToggleProps } from '@livekit/components-react'
import { Button, Popover } from '@/primitives'
import { ChevronUpIcon, EffectsIcon } from '@/icons'
import { Track, type VideoCaptureOptions } from 'livekit-client'

import { ToggleDevice } from './ToggleDevice'
import { css } from '@/styled-system/css'
import { AppAppearanceProvider } from '@/primitives/appAppearance'
import { devicePanel, deviceSelect } from './devicePanelStyles'
import { useCanPublishTrack } from '../../../hooks/useCanPublishTrack'
import { useCannotUseDevice } from '../../../hooks/useCannotUseDevice'
import { useSidePanel } from '../../../hooks/useSidePanel'
import { BackgroundProcessorFactory } from '../../blur'
import Source = Track.Source
import { SelectDevice } from './SelectDevice'
import { SettingsButton } from './SettingsButton'
import { SettingsDialogExtendedKey } from '@/features/settings/type'
import { TrackSource } from '@livekit/protocol'
import { useSnapshot } from 'valtio'

import {
  saveVideoInputDeviceId,
  saveVideoInputEnabled,
  userChoicesStore,
} from '@/stores/userChoices'

const EffectsButton = ({ onPress }: { onPress: () => void }) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'selectDevice' })
  const { isEffectsOpen, toggleEffects } = useSidePanel()
  return (
    <Button
      size="icon-lg"
      tooltip={t('effects')}
      aria-label={t('effects')}
      variant="ghost"
      onPress={() => {
        if (!isEffectsOpen) toggleEffects()
        onPress()
      }}
    >
      <EffectsIcon aria-hidden="true" />
    </Button>
  )
}

type VideoDeviceControlProps = Omit<
  UseTrackToggleProps<Source.Camera>,
  'source' | 'onChange'
> & {
  hideMenu?: boolean
}

export const VideoDeviceControl = ({
  hideMenu,
  ...props
}: VideoDeviceControlProps) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'selectDevice' })

  const { videoDeviceId, processorConfig } = useSnapshot(userChoicesStore)

  const trackProps = useTrackToggle({
    source: Source.Camera,
    ...props,
  })

  const kind = 'videoinput'
  const cannotUseDevice = useCannotUseDevice(kind)

  const toggleWithProcessor = async (forceState?: boolean) => {
    /**
     * We need to make sure that we apply the in-memory processor when re-enabling the camera.
     * Before, we had the following bug:
     * 1 - Configure a processor on join screen
     * 2 - Turn off camera on join screen
     * 3 - Join the room
     * 4 - Turn on the camera
     * 5 - No processor is applied to the camera
     * Expected: The processor is applied.
     *
     * See https://github.com/numerique-gouv/meet/pull/309#issuecomment-2622404121
     */
    const processor =
      BackgroundProcessorFactory.fromProcessorConfig(processorConfig)

    const toggle = trackProps.toggle as (
      forceState: boolean,
      captureOptions: VideoCaptureOptions
    ) => Promise<boolean | undefined>

    return toggle(forceState ?? !trackProps.enabled, {
      processor: processor,
    } as VideoCaptureOptions)
  }

  const selectLabel = t(`settings.${SettingsDialogExtendedKey.VIDEO}`)
  const canPublishTrack = useCanPublishTrack(TrackSource.CAMERA)

  return (
    <div
      className={css({
        display: 'flex',
        gap: '10px',
      })}
    >
      <ToggleDevice
        {...trackProps}
        isDisabled={!canPublishTrack}
        kind={kind}
        toggle={toggleWithProcessor}
        onUserChange={saveVideoInputEnabled}
        overrideToggleButtonProps={{
          ...(hideMenu
            ? {
                groupPosition: undefined,
              }
            : {}),
        }}
      />
      {!hideMenu && (
        <Popover variant="dark" density="app" withArrow={false}>
          <Button
            tooltip={selectLabel}
            aria-label={selectLabel}
            shape="circle"
            variant={cannotUseDevice ? 'warning' : 'outline'}
          >
            <ChevronUpIcon />
          </Button>
          {({ close }) => (
            <AppAppearanceProvider>
              <div className={devicePanel}>
                <div className={deviceSelect}>
                  <SelectDevice
                    context="room"
                    kind={kind}
                    id={videoDeviceId}
                    onSubmit={saveVideoInputDeviceId}
                  />
                </div>
                <EffectsButton onPress={close} />
                <SettingsButton
                  settingTab={SettingsDialogExtendedKey.VIDEO}
                  onPress={close}
                />
              </div>
            </AppAppearanceProvider>
          )}
        </Popover>
      )}
    </div>
  )
}
