import { LANGUAGE_COOKIE, isSupportedLanguage } from '@/i18n/languageDetection'
import type { ApiConfig } from '@/api/useConfig'
import type { ApiUser } from '@/features/auth/api/ApiUser'
import { ApiAccessLevel, type ApiRoom } from '@/features/rooms/api/ApiRoom'
import {
  isPreviewMeetingHistoryRequest,
  previewMeetingHistoryResponse,
} from './previewMeetingHistoryFixtures'

export const previewRoomId = 'room_00000000000000000000000000000000'
export const previewScenario =
  new URLSearchParams(location.search).get('screen') ?? 'join'
/** Fictional signed-in user for the authenticated workspace screens only. */
export const previewUser: ApiUser = {
  id: 'preview-user',
  email: 'matthias@mastrao.com',
  full_name: 'Matthias Virassamy',
  last_name: 'Virassamy',
  language: 'fr-fr',
  timezone: 'Europe/Paris',
}
export const previewRoom: ApiRoom = {
  id: previewRoomId,
  slug: previewRoomId,
  name: 'Réunion de démonstration',
  access_level: ApiAccessLevel.RESTRICTED,
  is_administrable: false,
  recording: { mode: 'disabled' },
}
export const previewConfig: ApiConfig = {
  feedback: { url: '' },
  background_image: {
    upload_is_enabled: false,
    max_size: 0,
    max_count_by_user: 0,
    allowed_extensions: [],
    allowed_mimetypes: [],
  },
  subtitle: { enabled: false },
  diagnostics: { connection_test_enabled: false },
  telephony: { enabled: false },
  recording: { is_enabled: false, available_modes: [] },
  livekit: {
    url: '',
    force_wss_protocol: false,
    enable_firefox_proxy_workaround: false,
    default_sources: [],
  },
  max_participants_for_sound: 10,
  auto_mute_on_join_threshold: 10,
  authenticated_users_can_edit_display_name: true,
  is_silent_login_enabled: false,
}

const jsonResponse = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

/** No backend, credentials, media capture or connection can be used by this entry. */
export const preparePreview = () => {
  const requestedLanguage = new URLSearchParams(location.search).get('lang')
  // The preview is French unless ?lang= asks otherwise.
  document.cookie = `${LANGUAGE_COOKIE}=${
    requestedLanguage && isSupportedLanguage(requestedLanguage)
      ? requestedLanguage
      : 'fr'
  }; path=/`
  if (previewScenario === 'devices-off') {
    const originalQuery = navigator.permissions.query.bind(
      navigator.permissions
    )
    navigator.permissions.query = async (descriptor) => {
      if (['camera', 'microphone'].includes(descriptor.name)) {
        return Object.assign(new EventTarget(), {
          name: descriptor.name,
          state: 'granted' as const,
          onchange: null,
        })
      }
      return originalQuery(descriptor)
    }
    navigator.mediaDevices.enumerateDevices = async () =>
      (['audioinput', 'audiooutput', 'videoinput'] as const).map((kind) => ({
        deviceId: `preview-${kind}`,
        groupId: 'preview',
        kind,
        label: {
          audioinput: 'Micro de test',
          audiooutput: 'Haut-parleur de test',
          videoinput: 'Caméra de test',
        }[kind],
        toJSON: () => ({ kind }),
      }))
  }
  const originalFetch = window.fetch.bind(window)
  window.fetch = async (input, options) => {
    const url = new URL(
      input instanceof Request ? input.url : String(input),
      location.href
    )
    if (url.pathname.includes('/api/')) {
      if (url.pathname.endsWith('/config/')) return jsonResponse(previewConfig)
      if (url.pathname.endsWith('/users/me/')) return jsonResponse({}, 401)
      if (url.pathname.endsWith('/request-entry/'))
        return jsonResponse({ status: 'waiting' })
      // Consent and all other writes fail visibly. Never simulate their success.
      if (
        (options?.method ??
          (input instanceof Request ? input.method : 'GET')) !== 'GET'
      )
        return jsonResponse(
          { detail: 'Aperçu local : aucune action envoyée.' },
          503
        )
      if (
        url.pathname.endsWith('/meetings/') &&
        url.searchParams.has('day_start')
      )
        return jsonResponse({ results: [], next_cursor: null })
      if (isPreviewMeetingHistoryRequest(url))
        return previewMeetingHistoryResponse(url, previewScenario)
      if (url.pathname.includes('/rooms/')) {
        if (previewScenario === 'loading')
          return new Promise<Response>(() => undefined)
        if (previewScenario === 'error') return jsonResponse({}, 503)
        return jsonResponse(previewRoom)
      }
      return jsonResponse({}, 503)
    }
    if (url.origin !== location.origin)
      throw new Error('Aperçu local : accès externe désactivé.')
    return originalFetch(input, options)
  }
  navigator.mediaDevices.getUserMedia = async () => {
    throw new DOMException(
      'Aperçu local : capture désactivée.',
      'NotAllowedError'
    )
  }
  navigator.mediaDevices.getDisplayMedia = async () => {
    throw new DOMException(
      'Aperçu local : partage désactivé.',
      'NotAllowedError'
    )
  }
}
