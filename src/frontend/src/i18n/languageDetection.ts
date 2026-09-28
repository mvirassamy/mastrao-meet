import type { DetectorOptions } from 'i18next-browser-languagedetector'

/*
 * Interface language, shared by every Mastrao application:
 * 1. the explicit choice stored in the shared `mastrao_lang` cookie;
 * 2. otherwise the first supported language of the browser (nl-BE → nl);
 * 3. otherwise English.
 * The Platform must apply the same rule and cookie so the language stays the
 * same across the Meet ⇄ Platform login redirects.
 */
export const SUPPORTED_LANGUAGES = ['en', 'fr', 'nl', 'de']
export const FALLBACK_LANGUAGE = 'en'
export const LANGUAGE_COOKIE = 'mastrao_lang'

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

export const languageDetectionOptions = (
  location: Pick<Location, 'hostname' | 'protocol'>
): DetectorOptions => ({
  // localStorage keeps the choice of users who picked a language before the
  // shared cookie existed.
  order: ['cookie', 'localStorage', 'navigator'],
  // "nl-BE" → "nl": keeps the browser's order, otherwise i18next would prefer
  // a later exact match ("en") over the first regional one.
  convertDetectedLanguage: (language) => language.split('-')[0].toLowerCase(),
  lookupCookie: LANGUAGE_COOKIE,
  caches: ['cookie', 'localStorage'],
  cookieDomain: sharedCookieDomain(location.hostname),
  cookieMinutes: 60 * 24 * 365,
  cookieOptions: {
    path: '/',
    sameSite: 'lax',
    secure: location.protocol === 'https:',
  },
})
