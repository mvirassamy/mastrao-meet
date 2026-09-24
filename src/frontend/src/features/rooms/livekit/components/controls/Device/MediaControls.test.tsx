import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RoomContext } from '@livekit/components-react'
import {
  ParticipantEvent,
  Room,
  type AudioCaptureOptions,
  type VideoCaptureOptions,
  type TrackPublishOptions,
} from 'livekit-client'
import { AudioDevicesControl } from './AudioDevicesControl'
import { VideoDeviceControl } from './VideoDeviceControl'
import { userChoicesStore } from '@/stores/userChoices'
import { ToggleDevice } from './ToggleDevice'
import { reportError } from '@/features/analytics/telemetry'

vi.mock('@/features/analytics/telemetry', () => ({ reportError: vi.fn() }))

const shortcuts = vi.hoisted(() => new Map<string, () => unknown>())

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { keyPrefix?: string }) =>
      `${options?.keyPrefix ?? ''}.${key}`,
  }),
}))
vi.mock('@/primitives', () => ({
  ToggleButton: ({
    onPress,
    isDisabled,
    children,
    'aria-label': label,
  }: {
    onPress: () => void
    isDisabled?: boolean
    children: ReactNode
    'aria-label'?: string
  }) => (
    <button disabled={isDisabled} aria-label={label} onClick={onPress}>
      {children}
    </button>
  ),
  Button: () => null,
  Popover: () => null,
}))
vi.mock('@/stores/userChoices', async () => {
  const { proxy } = await import('valtio')
  const store = proxy({ audioEnabled: true, videoEnabled: true })
  return {
    userChoicesStore: store,
    saveAudioInputEnabled: (enabled: boolean) => {
      store.audioEnabled = enabled
    },
    saveVideoInputEnabled: (enabled: boolean) => {
      store.videoEnabled = enabled
    },
    saveAudioInputDeviceId: vi.fn(),
    saveAudioOutputDeviceId: vi.fn(),
    saveVideoInputDeviceId: vi.fn(),
  }
})
vi.mock('@/stores/silentMic', async () => ({
  silentMicStore: (await import('valtio')).proxy({ status: 'idle' }),
  openSilentMicDialog: vi.fn(),
}))
vi.mock('@/stores/permissions', () => ({ openPermissionsDialog: vi.fn() }))
vi.mock('@/features/shortcuts/useRegisterKeyboardShortcut', () => ({
  useRegisterKeyboardShortcut: ({
    id,
    handler,
  }: {
    id?: string
    handler: () => unknown
  }) => {
    if (id) shortcuts.set(id, handler)
  },
}))
vi.mock('@/hooks/useScreenReaderAnnounce', () => ({
  useScreenReaderAnnounce: () => vi.fn(),
}))
vi.mock('@/utils/livekit', () => ({ isSafari: () => false }))
vi.mock('../../../hooks/useDeviceIcons', () => ({
  useDeviceIcons: () => ({ toggleOn: () => null, toggleOff: () => null }),
}))
vi.mock('../../../hooks/useDeviceShortcut', () => ({
  useDeviceShortcut: (kind: string) => ({
    id: kind === 'audioinput' ? 'toggle-microphone' : 'toggle-camera',
  }),
}))
vi.mock('@/features/shortcuts/catalog', () => ({
  getShortcutDescriptorById: () => ({ code: 'KeyV' }),
}))
vi.mock('../../../hooks/useCanPublishTrack', () => ({
  useCanPublishTrack: () => true,
}))
vi.mock('../../../hooks/useCannotUseDevice', () => ({
  useCannotUseDevice: () => false,
}))
vi.mock('../../../hooks/useDeviceMissing', () => ({
  useDeviceMissing: () => false,
}))
vi.mock('../../../hooks/useJoinTracks', () => ({
  requestDevicePermission: vi.fn(),
}))
vi.mock('../../../hooks/useSidePanel', () => ({ useSidePanel: () => ({}) }))
vi.mock('./PermissionNeededButton', () => ({
  PermissionNeededButton: () => null,
}))
vi.mock('./SelectDevice', () => ({ SelectDevice: () => null }))
vi.mock('./SettingsButton', () => ({ SettingsButton: () => null }))
vi.mock('@/features/rooms/components/ActiveSpeaker', () => ({
  ActiveSpeaker: () => <span>Talking</span>,
}))
vi.mock('@/features/rooms/components/MediaDeviceErrorAlert', () => ({
  MediaDeviceErrorAlert: () => null,
}))
vi.mock('../../blur', () => ({
  BackgroundProcessorFactory: { fromProcessorConfig: () => undefined },
}))

// Real controls, useLongPress, useTrackToggle, Room and participant subscriptions.
// Only the hardware boundary is replaced: no connection or media capture occurs.
function setup(kind: 'audio' | 'video', enabled = true) {
  const room = new Room()
  const participant = room.localParticipant
  let mediaEnabled = enabled
  const property = kind === 'audio' ? 'isMicrophoneEnabled' : 'isCameraEnabled'
  const method = kind === 'audio' ? 'setMicrophoneEnabled' : 'setCameraEnabled'
  vi.spyOn(participant, property, 'get').mockImplementation(() => mediaEnabled)
  const setEnabled = vi.fn<
    (
      value: boolean,
      options?: AudioCaptureOptions | VideoCaptureOptions,
      publishOptions?: TrackPublishOptions
    ) => Promise<undefined>
  >(async (value) => {
    mediaEnabled = value
    participant.emit(ParticipantEvent.ParticipantPermissionsChanged)
    return undefined
  })
  vi.spyOn(participant, method).mockImplementation(setEnabled)
  const view = render(
    <RoomContext.Provider value={room}>
      {kind === 'audio' ? (
        <AudioDevicesControl hideMenu onDeviceError={vi.fn()} />
      ) : (
        <VideoDeviceControl hideMenu onDeviceError={vi.fn()} />
      )}
    </RoomContext.Provider>
  )
  return { ...view, participant, setEnabled, isEnabled: () => mediaEnabled }
}

beforeEach(() => {
  userChoicesStore.audioEnabled = true
  userChoicesStore.videoEnabled = true
  shortcuts.clear()
  vi.clearAllMocks()
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('media control regressions', () => {
  it('does not run temporary push-to-talk through the persistent prejoin toggle', async () => {
    vi.useFakeTimers()
    userChoicesStore.audioEnabled = false
    const toggle = vi.fn(async () => {
      userChoicesStore.audioEnabled = true
    })
    render(
      <ToggleDevice
        kind="audioinput"
        context="join"
        enabled={false}
        toggle={toggle}
      />
    )
    fireEvent.keyDown(window, { code: 'KeyV' })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350)
    })
    fireEvent.keyUp(window, { code: 'KeyV' })
    expect(toggle).not.toHaveBeenCalled()
    expect(userChoicesStore.audioEnabled).toBe(false)
  })

  it('handles a rejected press without saving a choice', async () => {
    const error = new Error('capture rejected')
    const onUserChange = vi.fn()
    render(
      <ToggleDevice
        kind="audioinput"
        enabled={false}
        toggle={vi.fn().mockRejectedValue(error)}
        onUserChange={onUserChange}
      />
    )
    await act(async () => {
      fireEvent.click(screen.getByRole('button'))
    })
    expect(reportError).toHaveBeenCalledWith('device_switch_failure', error, {
      at: 'ToggleDevice.onPress',
      kind: 'audioinput',
    })
    expect(onUserChange).not.toHaveBeenCalled()
  })

  it('mutes on V release after the activation rerender, without an auto-repeat', async () => {
    vi.useFakeTimers()
    userChoicesStore.audioEnabled = false
    const media = setup('audio', false)
    fireEvent.keyDown(window, { code: 'KeyV' })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350)
    })
    expect(media.isEnabled()).toBe(true)
    expect(userChoicesStore.audioEnabled).toBe(false)
    expect(screen.getByText('Talking')).toBeTruthy()
    await act(async () => {
      fireEvent.keyUp(window, { code: 'KeyV' })
    })
    expect(media.isEnabled()).toBe(false)
    expect(userChoicesStore.audioEnabled).toBe(false)
    expect(screen.queryByText('Talking')).toBeNull()
  })

  it.each(['blur', 'unmount'] as const)(
    'ends temporary push-to-talk on %s without saving the temporary state',
    async (reason) => {
      vi.useFakeTimers()
      userChoicesStore.audioEnabled = false
      const media = setup('audio', false)
      fireEvent.keyDown(window, { code: 'KeyV' })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(350)
      })
      expect(media.isEnabled()).toBe(true)
      expect(userChoicesStore.audioEnabled).toBe(false)
      await act(async () => {
        if (reason === 'blur') fireEvent.blur(window)
        else media.unmount()
      })
      expect(media.isEnabled()).toBe(false)
      expect(userChoicesStore.audioEnabled).toBe(false)
    }
  )

  it.each(['keyup', 'blur', 'unmount'] as const)(
    'mutes after pending microphone activation resolves following %s',
    async (reason) => {
      vi.useFakeTimers()
      userChoicesStore.audioEnabled = false
      const media = setup('audio', false)
      const setEnabled = media.setEnabled.getMockImplementation()!
      let completeActivation!: () => void
      media.setEnabled.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            completeActivation = () => {
              void setEnabled(true).then(resolve)
            }
          })
      )
      fireEvent.keyDown(window, { code: 'KeyV' })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(350)
      })
      expect(media.setEnabled).toHaveBeenCalledWith(true, undefined, undefined)
      await act(async () => {
        if (reason === 'keyup') fireEvent.keyUp(window, { code: 'KeyV' })
        else if (reason === 'blur') fireEvent.blur(window)
        else media.unmount()
      })
      await act(async () => {
        completeActivation()
      })
      expect(media.isEnabled()).toBe(false)
      expect(userChoicesStore.audioEnabled).toBe(false)
    }
  )

  it('does not activate for a short press or a timer cancelled by unmount', async () => {
    vi.useFakeTimers()
    const media = setup('audio', false)
    fireEvent.keyDown(window, { code: 'KeyV' })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })
    fireEvent.keyUp(window, { code: 'KeyV' })
    fireEvent.keyDown(window, { code: 'KeyV' })
    media.unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400)
    })
    expect(media.setEnabled).not.toHaveBeenCalled()
  })

  it.each(['audio', 'video'] as const)(
    'persists successful %s button toggles in both directions',
    async (kind) => {
      const media = setup(kind)
      const preference = kind === 'audio' ? 'audioEnabled' : 'videoEnabled'
      await act(async () => {
        fireEvent.click(screen.getByRole('button'))
      })
      expect(media.isEnabled()).toBe(false)
      expect(userChoicesStore[preference]).toBe(false)
      await act(async () => {
        fireEvent.click(screen.getByRole('button'))
      })
      expect(media.isEnabled()).toBe(true)
      expect(userChoicesStore[preference]).toBe(true)
    }
  )

  it.each(['audio', 'video'] as const)(
    'persists successful %s keyboard toggles',
    async (kind) => {
      const media = setup(kind)
      await act(async () => {
        await shortcuts.get(
          kind === 'audio' ? 'toggle-microphone' : 'toggle-camera'
        )?.()
      })
      expect(media.isEnabled()).toBe(false)
      expect(
        userChoicesStore[kind === 'audio' ? 'audioEnabled' : 'videoEnabled']
      ).toBe(false)
    }
  )

  it.each(['audio', 'video'] as const)(
    'does not persist a failed %s toggle',
    async (kind) => {
      const media = setup(kind)
      media.setEnabled.mockRejectedValueOnce(new Error('device unavailable'))
      await act(async () => {
        fireEvent.click(screen.getByRole('button'))
      })
      expect(
        userChoicesStore[kind === 'audio' ? 'audioEnabled' : 'videoEnabled']
      ).toBe(true)
    }
  )

  it.each(['audio', 'video'] as const)(
    'does not persist a non-user %s state change',
    async (kind) => {
      const media = setup(kind)
      await act(async () => {
        await media.setEnabled(false)
      })
      expect(media.isEnabled()).toBe(false)
      expect(
        userChoicesStore[kind === 'audio' ? 'audioEnabled' : 'videoEnabled']
      ).toBe(true)
    }
  )
})
