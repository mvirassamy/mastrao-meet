import { afterEach, describe, expect, it, vi } from 'vitest'
import i18next from 'i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import {
  FALLBACK_LANGUAGE,
  LANGUAGE_COOKIE,
  SUPPORTED_LANGUAGES,
  languageCookie,
  languageDetectionOptions,
  sharedCookieDomain,
} from './languageDetection'
import { setInterfaceLanguage } from './setInterfaceLanguage'

const setBrowserLanguages = (languages: string[]) => {
  vi.spyOn(window.navigator, 'languages', 'get').mockReturnValue(languages)
  vi.spyOn(window.navigator, 'language', 'get').mockReturnValue(
    languages[0] ?? ''
  )
}

const detect = async (browserLanguages: string[]) => {
  setBrowserLanguages(browserLanguages)
  const instance = i18next.createInstance()
  await instance.use(LanguageDetector).init({
    supportedLngs: SUPPORTED_LANGUAGES,
    fallbackLng: FALLBACK_LANGUAGE,
    resources: {},
    detection: languageDetectionOptions(),
  })
  return instance.language
}

const readLanguageCookie = () =>
  document.cookie
    .split('; ')
    .find((cookie) => cookie.startsWith(`${LANGUAGE_COOKIE}=`))

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.cookie = `${LANGUAGE_COOKIE}=; max-age=0; path=/`
})

describe('interface language detection', () => {
  it('uses the first supported browser language, ignoring the region', async () => {
    expect(await detect(['nl-BE', 'fr-BE', 'en'])).toBe('nl')
  })

  it('skips unsupported languages', async () => {
    expect(await detect(['es-ES', 'de-CH'])).toBe('de')
  })

  it('keeps the first language even when a later one matches exactly', async () => {
    expect(await detect(['de-CH', 'en'])).toBe('de')
  })

  it('falls back to English when no browser language is supported', async () => {
    expect(await detect(['es-ES', 'it'])).toBe('en')
  })

  it('lets the shared cookie override the browser', async () => {
    document.cookie = `${LANGUAGE_COOKIE}=de; path=/`
    expect(await detect(['fr-FR'])).toBe('de')
  })

  it('never stores a detected language', async () => {
    await detect(['fr-FR'])
    expect(readLanguageCookie()).toBeUndefined()
  })

  it('ignores the legacy localStorage guess', async () => {
    const getItem = vi.fn(() => 'fr')
    vi.stubGlobal('localStorage', { getItem, setItem: vi.fn() })
    expect(await detect(['nl-BE'])).toBe('nl')
    expect(getItem).not.toHaveBeenCalled()
  })
})

describe('setInterfaceLanguage', () => {
  it('stores an explicit choice in the shared cookie', async () => {
    const changeLanguage = vi
      .spyOn(i18next, 'changeLanguage')
      .mockResolvedValue((() => '') as never)
    await setInterfaceLanguage('nl')
    expect(readLanguageCookie()).toBe(`${LANGUAGE_COOKIE}=nl`)
    expect(changeLanguage).toHaveBeenCalledWith('nl')
  })

  it('ignores unsupported languages', async () => {
    const changeLanguage = vi.spyOn(i18next, 'changeLanguage')
    await setInterfaceLanguage('es')
    expect(readLanguageCookie()).toBeUndefined()
    expect(changeLanguage).not.toHaveBeenCalled()
  })
})

describe('languageCookie', () => {
  it('shares the choice with the parent domain over HTTPS', () => {
    expect(
      languageCookie('de', { hostname: 'meet.mastrao.com', protocol: 'https:' })
    ).toBe(
      `${LANGUAGE_COOKIE}=de; path=/; max-age=31536000; samesite=lax; domain=.mastrao.com; secure`
    )
  })

  it('stays host-only on localhost', () => {
    expect(
      languageCookie('fr', { hostname: 'localhost', protocol: 'http:' })
    ).toBe(`${LANGUAGE_COOKIE}=fr; path=/; max-age=31536000; samesite=lax`)
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
