import i18n from 'i18next'
import resourcesToBackend from 'i18next-resources-to-backend'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import {
  FALLBACK_LANGUAGE,
  SUPPORTED_LANGUAGES,
  languageDetectionOptions,
} from './languageDetection'
const i18nDefaultNamespace = 'global'
const fallbackLng = FALLBACK_LANGUAGE

i18n.setDefaultNamespace(i18nDefaultNamespace)
i18n
  .use(
    resourcesToBackend((language: string, namespace: string) => {
      return import(`../locales/${language}/${namespace}.json`)
    })
  )
  .use(initReactI18next)
  .use(LanguageDetector)
  .init({
    supportedLngs: SUPPORTED_LANGUAGES,
    fallbackLng,
    ns: i18nDefaultNamespace,
    detection: languageDetectionOptions(),
    interpolation: {
      escapeValue: false,
    },
  })
  .then(() => {
    document.documentElement.setAttribute('lang', i18n.language || fallbackLng)
  })

i18n.on('languageChanged', (lang) => {
  document.documentElement.setAttribute('lang', lang)
})
