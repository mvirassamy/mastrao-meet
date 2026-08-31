import { useMutation } from '@tanstack/react-query'
import { keys } from '@/api/queryKeys'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { queryClient } from '@/api/queryClient'
import { updateUserPreferences } from './updateUserPreferences'
import { convertToBackendLanguage } from '@/utils/languages'
import { useUser } from './useUser'

/**
 * Hook that synchronizes user browser preferences (language, timezone) with backend user settings.
 * Automatically updates backend when browser settings change for logged-in users.
 */
export const useSyncUserPreferencesWithBackend = () => {
  const { i18n } = useTranslation()
  const { user, isLoggedIn } = useUser()

  const { mutateAsync } = useMutation({
    mutationFn: updateUserPreferences,
    onSuccess: (updatedUser) => {
      queryClient.setQueryData([keys.user], updatedUser)
    },
  })

  useEffect(() => {
    if (!user || !isLoggedIn) return
    if (window.location.pathname.startsWith('/room_')) return

    const syncBrowserPreferencesToBackend = async () => {
      const currentTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone
      const currentLanguage = convertToBackendLanguage(i18n.language)
      if (
        currentLanguage !== user.language ||
        currentTimezone !== user.timezone
      ) {
        await mutateAsync({
          user: {
            id: user.id,
            timezone: currentTimezone,
            language: currentLanguage,
          },
        })
      }
    }

    void syncBrowserPreferencesToBackend().catch(() => {
      // Preference sync is best-effort. Mastrao host handoff sessions are
      // intentionally constrained and may read /users/me/ while being denied
      // generic user updates. That must not block joining a meeting.
    })
  }, [i18n.language, isLoggedIn, user, mutateAsync])
}
