import { authUrl } from '@/features/auth/utils/authUrl'

/** Existing login flow; the user comes back to the current page afterwards. */
export const redirectToLogin = () => {
  window.location.href = authUrl()
}
