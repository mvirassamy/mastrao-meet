import { Redirect, useParams } from 'wouter'
import { useUser } from '@/features/auth/api/useUser'
import { UserAware } from '@/features/auth/components/UserAware'
import { MeetingHistoryMeetingPage } from '../components/MeetingHistoryPages'

const safeDecode = (value: string) => {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

const MeetingHistoryMeeting = () => {
  const { isLoggedIn, user } = useUser()
  const { meetingId = '' } = useParams<{ meetingId: string }>()

  return (
    <UserAware>
      {isLoggedIn && user ? (
        <MeetingHistoryMeetingPage
          user={user}
          meetingId={safeDecode(meetingId)}
        />
      ) : (
        <Redirect to="/" replace />
      )}
    </UserAware>
  )
}

export default MeetingHistoryMeeting
