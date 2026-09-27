import type { ApiUser } from '@/features/auth/api/ApiUser'
import { MeetWorkspaceShell } from '@/features/home/components/authenticated/MeetWorkspaceShell'
import { MeetWorkspaceToolbar } from '@/features/home/components/authenticated/MeetWorkspaceToolbar'
import { Screen } from '@/layout/Screen'
import { MeetingHistoryDetailView } from './MeetingHistoryDetailView'
import { MeetingHistoryList } from './MeetingHistoryList'

export const MeetingHistoryPage = ({ user }: { user: ApiUser }) => (
  <Screen header={false} footer={false}>
    <MeetWorkspaceShell user={user} toolbar={<MeetWorkspaceToolbar />}>
      <MeetingHistoryList timeZone={user.timezone} />
    </MeetWorkspaceShell>
  </Screen>
)

export const MeetingHistoryMeetingPage = ({
  user,
  meetingId,
}: {
  user: ApiUser
  meetingId: string
}) => (
  <Screen header={false} footer={false}>
    <MeetWorkspaceShell user={user} toolbar={<MeetWorkspaceToolbar />}>
      <MeetingHistoryDetailView
        key={meetingId}
        meetingId={meetingId}
        timeZone={user.timezone}
      />
    </MeetWorkspaceShell>
  </Screen>
)
