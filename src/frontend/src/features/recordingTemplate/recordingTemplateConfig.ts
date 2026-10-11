export interface RecordingTemplateConfig {
  serverUrl: string
  token: string
}

export function readRecordingTemplateConfig(
  search = window.location.search
): RecordingTemplateConfig {
  const parameters = new URLSearchParams(search)
  const serverUrl = parameters.get('url')?.trim()
  const token = parameters.get('token')?.trim()

  if (!serverUrl || !token) {
    throw new Error('Missing LiveKit recording template connection parameters.')
  }

  return { serverUrl, token }
}

export function participantInitials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'

  const first = words[0]?.[0] ?? ''
  const last = words.length > 1 ? (words.at(-1)?.[0] ?? '') : ''
  return `${first}${last}`.toLocaleUpperCase('fr')
}

export function participantHue(identity: string): number {
  let hash = 0
  for (const character of identity) {
    hash = (hash * 31 + character.codePointAt(0)!) >>> 0
  }
  return hash % 360
}
