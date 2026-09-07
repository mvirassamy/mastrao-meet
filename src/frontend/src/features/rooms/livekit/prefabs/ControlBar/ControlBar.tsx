import type { Track } from 'livekit-client'
import * as React from 'react'

import { MobileControlBar } from './MobileControlBar'
import { DesktopControlBar } from './DesktopControlBar'
import { useIsMobile } from '@/utils/useIsMobile'
import { ReactionsToolbar } from '@/features/reactions/components/toolbar/ReactionsToolbar'
import { css } from '@/styled-system/css'
import { useSize } from '../../hooks/useResizeObserver'

export interface ControlBarProps extends React.HTMLAttributes<HTMLDivElement> {
  onDeviceError?: (error: { source: Track.Source; error: Error }) => void
  roomId: string
  canEnd?: boolean
  onMeetingEnded?: () => void
}

/**
 * The `ControlBar` prefab gives the user the basic user interface to control their
 * media devices (camera, microphone and screen share), open the `Chat` and leave the room.
 */
export function ControlBar({
  onDeviceError,
  roomId,
  canEnd,
  onMeetingEnded,
}: ControlBarProps) {
  const isMobile = useIsMobile()
  const controlBarRef = React.useRef<HTMLDivElement>(null)
  const { height } = useSize(controlBarRef)

  React.useLayoutEffect(() => {
    const conference = controlBarRef.current?.closest<HTMLElement>(
      '.lk-video-conference'
    )
    if (!conference || !height) return
    const property = '--sizes-room-control-bar'
    const previousHeight = conference.style.getPropertyValue(property)
    conference.style.setProperty(property, `${height}px`)
    return () => {
      if (previousHeight) conference.style.setProperty(property, previousHeight)
      else conference.style.removeProperty(property)
    }
  }, [height])

  return (
    <div
      className={css({
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        position: 'absolute',
        bottom: 0,
        left: 0,
        right: 0,
      })}
    >
      <ReactionsToolbar />
      <div
        id="control-bar"
        ref={controlBarRef}
        className={css({
          zIndex: 100,
        })}
      >
        {isMobile ? (
          <MobileControlBar
            onDeviceError={onDeviceError}
            roomId={roomId}
            canEnd={canEnd}
            onMeetingEnded={onMeetingEnded}
          />
        ) : (
          <DesktopControlBar
            onDeviceError={onDeviceError}
            roomId={roomId}
            canEnd={canEnd}
            onMeetingEnded={onMeetingEnded}
          />
        )}
      </div>
    </div>
  )
}
export type ControlBarAuxProps = Pick<
  ControlBarProps,
  'onDeviceError' | 'roomId' | 'canEnd' | 'onMeetingEnded'
>
