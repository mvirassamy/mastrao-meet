import { useTranslation } from 'react-i18next'
import {
  useLocalParticipant,
  useTrackToggle,
  UseTrackToggleProps,
} from '@livekit/components-react'
import { Button, Popover } from '@/primitives'
import { RiArrowUpSFill } from '@remixicon/react'
import { LocalAudioTrack, Track } from 'livekit-client'

import { ToggleDevice } from './ToggleDevice'
import { css } from '@/styled-system/css'
import { useCanPublishTrack } from '../../../hooks/useCanPublishTrack'
import { useCannotUseDevice } from '../../../hooks/useCannotUseDevice'
import { SelectDevice } from './SelectDevice'
import { SettingsButton } from './SettingsButton'
import { SettingsDialogExtendedKey } from '@/features/settings/type'
import { TrackSource } from '@livekit/protocol'
import Source = Track.Source
import { isSafari } from '@/utils/livekit'
import {
  saveAudioInputDeviceId,
  saveAudioInputEnabled,
  saveAudioOutputDeviceId,
  userChoicesStore,
} from '@/stores/userChoices'

import { useSnapshot } from 'valtio'

type AudioDevicesControlProps = Omit<
  UseTrackToggleProps<Source.Microphone>,
  'source' | 'onChange'
> & {
  hideMenu?: boolean
}

export const AudioDevicesControl = ({
  hideMenu,
  ...props
}: AudioDevicesControlProps) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'selectDevice' })

  const { audioDeviceId, audioOutputDeviceId } = useSnapshot(userChoicesStore)

  const trackProps = useTrackToggle({
    source: Source.Microphone,
    ...props,
  })

  const { microphoneTrack } = useLocalParticipant()
  const localAudioTrack =
    microphoneTrack?.track instanceof LocalAudioTrack
      ? microphoneTrack.track
      : undefined

  const kind = 'audioinput'
  const cannotUseDevice = useCannotUseDevice(kind)
  const selectLabel = t(`settings.${SettingsDialogExtendedKey.AUDIO}`)

  const canPublishTrack = useCanPublishTrack(TrackSource.MICROPHONE)

  return (
    <div
      className={css({
        display: 'flex',
        gap: '6px',
      })}
    >
      <ToggleDevice<Source.Microphone>
        {...trackProps}
        isDisabled={!canPublishTrack}
        kind={kind}
        toggle={trackProps.toggle}
        onUserChange={saveAudioInputEnabled}
        overrideToggleButtonProps={{
          ...(hideMenu
            ? {
                groupPosition: undefined,
              }
            : {}),
        }}
      />
      {!hideMenu && (
        <Popover variant="dark" withArrow={false}>
          <Button
            tooltip={selectLabel}
            aria-label={selectLabel}
            shape="circle"
            square
            variant={cannotUseDevice ? 'error2' : 'primaryDark'}
          >
            <RiArrowUpSFill />
          </Button>
          {({ close }) => (
            <div
              className={css({
                maxWidth: '36rem',
                padding: '0.15rem',
                display: 'flex',
                gap: '0.5rem',
              })}
            >
              <div
                style={{
                  flex: '1 1 0',
                  minWidth: 0,
                }}
              >
                <SelectDevice
                  context="room"
                  kind={kind}
                  id={audioDeviceId}
                  track={localAudioTrack}
                  onSubmit={saveAudioInputDeviceId}
                />
              </div>
              {!isSafari() && (
                <div
                  style={{
                    flex: '1 1 0',
                    minWidth: 0,
                  }}
                >
                  <SelectDevice
                    context="room"
                    kind="audiooutput"
                    id={audioOutputDeviceId}
                    onSubmit={saveAudioOutputDeviceId}
                  />
                </div>
              )}
              <SettingsButton
                settingTab={SettingsDialogExtendedKey.AUDIO}
                onPress={close}
              />
            </div>
          )}
        </Popover>
      )}
    </div>
  )
}
