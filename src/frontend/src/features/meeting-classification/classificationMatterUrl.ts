import type { MeetingClassification } from './classificationApi'

/** Use the configured Platform origin and the authorized path verbatim. */
export function classificationMatterUrl(
  classification: MeetingClassification | undefined,
  platformOrigin: unknown
): string | null {
  if (!classification?.matter_path || typeof platformOrigin !== 'string')
    return null
  try {
    const origin = new URL(platformOrigin)
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(origin.hostname)
    if (
      (origin.protocol !== 'https:' &&
        !(local && origin.protocol === 'http:')) ||
      origin.username ||
      origin.password ||
      origin.pathname !== '/' ||
      origin.search ||
      origin.hash
    )
      return null
    return `${origin.origin}${classification.matter_path}`
  } catch {
    return null
  }
}
