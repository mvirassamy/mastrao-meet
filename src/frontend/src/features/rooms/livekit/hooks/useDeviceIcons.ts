import {
  RemixiconComponentType,
  RiMicFill,
  RiMicOffFill,
  RiVideoOffFill,
  RiVideoOnFill,
  RiVolumeDownFill,
  RiVolumeMuteFill,
} from '@remixicon/react'

export interface DeviceIcons {
  toggleOn: RemixiconComponentType
  toggleOff: RemixiconComponentType
  select: RemixiconComponentType
}

const ICONS: Record<MediaDeviceKind | 'default', DeviceIcons> = {
  audioinput: {
    toggleOn: RiMicFill,
    toggleOff: RiMicOffFill,
    select: RiMicFill,
  },
  videoinput: {
    toggleOn: RiVideoOnFill,
    toggleOff: RiVideoOffFill,
    select: RiVideoOnFill,
  },
  audiooutput: {
    toggleOn: RiVolumeDownFill,
    toggleOff: RiVolumeMuteFill,
    select: RiVolumeDownFill,
  },
  default: { toggleOn: RiMicFill, toggleOff: RiMicOffFill, select: RiMicFill },
}

export const useDeviceIcons = (kind: MediaDeviceKind): DeviceIcons =>
  ICONS[kind] ?? ICONS.default
