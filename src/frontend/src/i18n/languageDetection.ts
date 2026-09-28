import type { DetectorOptions } from 'i18next-browser-languagedetector'

/*
 * Interface language, shared by every Mastrao application:
 * 1. the explicit choice stored in the shared `mastrao_lang` cookie;
 * 2. otherwise the first supported browser language (nl-BE → nl);
 * 3. otherwise English.
 * Only an explicit choice writes the cookie (see setInterfaceLanguage):
 * detection never pins a guess. The Platform must apply the same rule and
 * cookie so the language stays the same across the Meet ⇄ Platform redirects.
 */
export const SUPPORTED_LANGUAGES = ['en', 'fr', 'nl', 'de']
export const FALLBACK_LANGUAGE = 'en'
export const LANGUAGE_COOKIE = 'mastrao_lang'

const ONE_YEAR_SECONDS = 60 * 60 * 24 * 365

type CookieLocation = Pick<Location, 'hostname' | 'protocol'>

/**
 * Parent domain shared by the Mastrao applications (meet.mastrao.com and
 * app.mastrao.com → .mastrao.com). Undefined on localhost or an IP address,
 * where the cookie stays host-only.
 */
export const sharedCookieDomain = (hostname: string): string | undefined => {
  const labels = hostname.split('.')
  const isIp = /^[\d.]+$/.test(hostname) || hostname.includes(':')
  if (isIp || labels.length < 3) return undefined
  return `.${labels.slice(-2).join('.')}`
}

export const isSupportedLanguage = (language: string) =>
  SUPPORTED_LANGUAGES.includes(language)

/** `document.cookie` assignment storing an explicit language choice. */
export const languageCookie = (language: string, location: CookieLocation) => {
  const domain = sharedCookieDomain(location.hostname)
  return [
    `${LANGUAGE_COOKIE}=${language}`,
    'path=/',
    `max-age=${ONE_YEAR_SECONDS}`,
    'samesite=lax',
    ...(domain ? [`domain=${domain}`] : []),
    ...(location.protocol === 'https:' ? ['secure'] : []),
  ].join('; ')
}

export const languageDetectionOptions = (): DetectorOptions => ({
  // The legacy `i18nextLng` localStorage key is ignored on purpose: the old
  // detector saved every guess there (including the former French fallback),
  // so it cannot be told apart from a real choice.
  order: ['cookie', 'navigator'],
  // "nl-BE" → "nl": keeps the browser's order, otherwise i18next would prefer
  // a later exact match ("en") over the first regional one.
  convertDetectedLanguage: (language) => language.split('-')[0].toLowerCase(),
  lookupCookie: LANGUAGE_COOKIE,
  // Never persist a detected language; only setInterfaceLanguage writes.
  caches: [],
})
