import { afterEach, describe, expect, it } from 'vitest'
import i18next from 'i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import {
  FALLBACK_LANGUAGE,
  LANGUAGE_COOKIE,
  SUPPORTED_LANGUAGES,
  languageDetectionOptions,
  sharedCookieDomain,
} from './languageDetection'

const detect = async (browserLanguages: string[]) => {
  Object.defineProperty(window.navigator, 'languages', {
    value: browserLanguages,
    configurable: true,
  })
  const instance = i18next.createInstance()
  await instance.use(LanguageDetector).init({
    supportedLngs: SUPPORTED_LANGUAGES,
    fallbackLng: FALLBACK_LANGUAGE,
    resources: {},
    detection: languageDetectionOptions({
      hostname: 'localhost',
      protocol: 'http:',
    }),
  })
  return instance.language
}

const clearCookie = () => {
  document.cookie = `${LANGUAGE_COOKIE}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`
}

afterEach(() => {
  clearCookie()
  window.localStorage?.clear()
})

describe('interface language detection', () => {
  it('uses the first supported browser language, ignoring the region', async () => {
    expect(await detect(['nl-BE', 'fr-BE', 'en'])).toBe('nl')
    clearCookie()
    window.localStorage?.clear()
    expect(await detect(['es-ES', 'de-CH'])).toBe('de')
    clearCookie()
    window.localStorage?.clear()
    expect(await detect(['de-CH', 'en'])).toBe('de')
  })

  it('falls back to English when no browser language is supported', async () => {
    expect(await detect(['es-ES', 'it'])).toBe('en')
  })

  it('lets the shared cookie override the browser', async () => {
    document.cookie = `${LANGUAGE_COOKIE}=de; path=/`
    expect(await detect(['fr-FR'])).toBe('de')
  })

  it('stores the resolved language in the shared cookie', async () => {
    await detect(['fr-FR'])
    expect(document.cookie).toContain(`${LANGUAGE_COOKIE}=fr`)
  })
})

describe('sharedCookieDomain', () => {
  it('returns the parent domain of Mastrao sub-domains', () => {
    expect(sharedCookieDomain('meet.mastrao.com')).toBe('.mastrao.com')
    expect(sharedCookieDomain('app.mastrao-staging.com')).toBe(
      '.mastrao-staging.com'
    )
  })

  it('keeps the cookie host-only on local hosts', () => {
    expect(sharedCookieDomain('localhost')).toBeUndefined()
    expect(sharedCookieDomain('127.0.0.1')).toBeUndefined()
    expect(sharedCookieDomain('mastrao.com')).toBeUndefined()
  })
})
