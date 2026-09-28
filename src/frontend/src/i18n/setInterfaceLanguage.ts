import i18n from 'i18next'
import { isSupportedLanguage, languageCookie } from './languageDetection'

/** Applies a language the user picked and shares it with the other apps. */
export const setInterfaceLanguage = async (language: string) => {
  if (!isSupportedLanguage(language)) return
  document.cookie = languageCookie(language, window.location)
  await i18n.changeLanguage(language)
}
