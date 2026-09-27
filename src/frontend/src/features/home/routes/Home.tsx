import { UserAware } from '@/features/auth/components/UserAware'
import { useUser } from '@/features/auth/api/useUser'
import { PublicHome } from './PublicHome'
import { AuthenticatedHome } from './AuthenticatedHome'

const Home = () => {
  const { isLoggedIn, user } = useUser()

  return (
    <UserAware>
      {isLoggedIn && user ? <AuthenticatedHome user={user} /> : <PublicHome />}
    </UserAware>
  )
}

export default Home
