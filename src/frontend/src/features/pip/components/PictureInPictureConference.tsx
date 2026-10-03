import { PictureInPicturePortal } from '@/features/pip/components/PictureInPicturePortal'
import { PipView } from '@/features/pip/components/PipView'

export const PictureInPictureConference = ({
  roomId,
  canEnd,
  onMeetingEnded,
}: {
  roomId: string
  canEnd?: boolean
  onMeetingEnded?: () => void
}) => (
  <PictureInPicturePortal>
    <PipView roomId={roomId} canEnd={canEnd} onMeetingEnded={onMeetingEnded} />
  </PictureInPicturePortal>
)
