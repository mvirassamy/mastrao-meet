import { act, cleanup, render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createLocalVideoTrack,
  type LocalVideoTrack,
  Room,
} from 'livekit-client'
import { RoomContext } from '@livekit/components-react'
import { VideoTab } from '@/features/settings/components/tabs/VideoTab'
import { reportError } from '@/features/analytics/telemetry'

vi.mock('@/features/analytics/telemetry', () => ({ reportError: vi.fn() }))

vi.mock('livekit-client', async (original) => ({
  ...(await original<typeof import('livekit-client')>()),
  createLocalVideoTrack: vi.fn(),
}))
vi.mock('@livekit/components-react', async (original) => ({
  ...(await original<typeof import('@livekit/components-react')>()),
  useMediaDeviceSelect: () => ({ devices: [], setActiveMediaDevice: vi.fn() }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/primitives', () => ({ Field: () => null }))
vi.mock('@/primitives/Tabs', () => ({
  TabPanel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))
vi.mock('@/features/settings/components/tabs/layout/RowWrapper', () => ({
  RowWrapper: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))
vi.mock('@/features/rooms/livekit/components/blur', () => ({
  BackgroundProcessorFactory: { fromProcessorConfig: () => undefined },
}))
vi.mock('@/stores/userChoices', async () => ({
  userChoicesStore: (await import('valtio')).proxy({ videoDeviceId: 'camera' }),
  saveVideoInputDeviceId: vi.fn(),
  saveVideoPublishResolution: vi.fn(),
  saveVideoSubscribeQuality: vi.fn(),
}))

function setup() {
  let resolve!: (track: LocalVideoTrack) => void
  let reject!: (error: Error) => void
  vi.mocked(createLocalVideoTrack).mockReturnValue(
    new Promise((done, fail) => {
      resolve = done
      reject = fail
    })
  )
  const track = { attach: vi.fn(), detach: vi.fn(), stop: vi.fn() }
  const room = new Room()
  vi.spyOn(room.localParticipant, 'isCameraEnabled', 'get').mockReturnValue(
    true
  )
  const view = render(
    <RoomContext.Provider value={room}>
      <VideoTab id="video" />
    </RoomContext.Provider>
  )
  return {
    ...view,
    track,
    resolve: () => resolve(track as unknown as LocalVideoTrack),
    reject,
  }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

describe('settings preview acquisition lifecycle', () => {
  it('stops an acquired preview if attachment fails', async () => {
    const preview = setup()
    const error = new Error('preview attachment failed')
    preview.track.attach.mockImplementationOnce(() => {
      throw error
    })
    await act(async () => {
      preview.resolve()
    })
    expect(preview.track.stop).toHaveBeenCalledOnce()
    expect(reportError).toHaveBeenCalledWith('device_switch_failure', error, {
      at: 'VideoTab.preview',
    })
  })

  it('handles a preview acquisition rejection while settings remain mounted', async () => {
    const preview = setup()
    const error = new Error('camera unavailable')
    await act(async () => {
      preview.reject(error)
    })
    expect(reportError).toHaveBeenCalledWith('device_switch_failure', error, {
      at: 'VideoTab.preview',
    })
    expect(preview.track.attach).not.toHaveBeenCalled()
  })

  it('ignores acquisition rejection after settings unmount', async () => {
    const preview = setup()
    preview.unmount()
    await act(async () => {
      preview.reject(new Error('camera unavailable'))
    })
    expect(reportError).not.toHaveBeenCalled()
  })

  it('stops a preview acquired after settings have unmounted without attaching it', async () => {
    const preview = setup()
    expect(createLocalVideoTrack).toHaveBeenCalledOnce()
    preview.unmount()
    await act(async () => {
      preview.resolve()
    })
    expect(preview.track.stop).toHaveBeenCalledOnce()
    expect(preview.track.attach).not.toHaveBeenCalled()
  })

  it('attaches an on-time preview and stops it when settings unmount', async () => {
    const preview = setup()
    await act(async () => {
      preview.resolve()
    })
    expect(preview.track.attach).toHaveBeenCalledOnce()
    expect(preview.track.stop).not.toHaveBeenCalled()
    preview.unmount()
    expect(preview.track.detach).toHaveBeenCalledOnce()
    expect(preview.track.stop).toHaveBeenCalledOnce()
  })
})
