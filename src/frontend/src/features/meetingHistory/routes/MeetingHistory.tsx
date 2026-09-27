import { Redirect } from 'wouter'
import { useUser } from '@/features/auth/api/useUser'
import { UserAware } from '@/features/auth/components/UserAware'
import { MeetingHistoryPage } from '../components/MeetingHistoryPages'

const MeetingHistory = () => {
  const { isLoggedIn, user } = useUser()

  return (
    <UserAware>
      {isLoggedIn && user ? (
        <MeetingHistoryPage user={user} />
      ) : (
        <Redirect to="/" replace />
      )}
    </UserAware>
  )
}

export default MeetingHistory
