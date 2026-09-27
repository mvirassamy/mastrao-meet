import {
  type AppIconComponent,
  MicrophoneIcon,
  MicrophoneOffIcon,
  VideoOffIcon,
  VideoOnIcon,
  VolumeDownIcon,
  VolumeMuteIcon,
} from '@/icons'
export interface DeviceIcons {
  toggleOn: AppIconComponent
  toggleOff: AppIconComponent
  select: AppIconComponent
}

const ICONS: Record<MediaDeviceKind | 'default', DeviceIcons> = {
  audioinput: {
    toggleOn: MicrophoneIcon,
    toggleOff: MicrophoneOffIcon,
    select: MicrophoneIcon,
  },
  videoinput: {
    toggleOn: VideoOnIcon,
    toggleOff: VideoOffIcon,
    select: VideoOnIcon,
  },
  audiooutput: {
    toggleOn: VolumeDownIcon,
    toggleOff: VolumeMuteIcon,
    select: VolumeDownIcon,
  },
  default: {
    toggleOn: MicrophoneIcon,
    toggleOff: MicrophoneOffIcon,
    select: MicrophoneIcon,
  },
}

export const useDeviceIcons = (kind: MediaDeviceKind): DeviceIcons =>
  ICONS[kind] ?? ICONS.default
