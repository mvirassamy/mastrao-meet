import type { ApiRoom } from './api/ApiRoom'

type PlatformReturn = NonNullable<ApiRoom['platform_return']>

const opaqueExternalId = /^[A-Za-z0-9._:-]{1,200}$/
const opaqueReference = /^[A-Za-z0-9_-]{16,160}$/
const roomSlug = /^[A-Za-z0-9_-]{1,160}$/
const cachePrefix = 'mastrao-platform-return-v1:'

export const validatePlatformReturn = (
  value: unknown
): PlatformReturn | null => {
  if (!value || typeof value !== 'object') return null
  const descriptor = value as { url?: unknown; expires_at?: unknown }
  if (
    typeof descriptor.url !== 'string' ||
    typeof descriptor.expires_at !== 'number' ||
    !Number.isInteger(descriptor.expires_at) ||
    descriptor.expires_at <= Date.now() / 1000
  ) {
    return null
  }
  try {
    const url = new URL(descriptor.url)
    const local = ['localhost', '127.0.0.1', '[::1]', '::1'].includes(
      url.hostname
    )
    if (
      (url.protocol !== 'https:' && !(local && url.protocol === 'http:')) ||
      url.username ||
      url.password ||
      url.pathname !== '/api/meeting-return' ||
      url.hash ||
      [...url.searchParams.keys()].length !== 2 ||
      url.searchParams.getAll('organization_ref').length !== 1 ||
      url.searchParams.getAll('meeting_ref').length !== 1 ||
      !opaqueExternalId.test(url.searchParams.get('organization_ref') ?? '') ||
      !opaqueReference.test(url.searchParams.get('meeting_ref') ?? '')
    ) {
      return null
    }
    return { url: url.href, expires_at: descriptor.expires_at }
  } catch {
    return null
  }
}

const keyFor = (slug: string) =>
  roomSlug.test(slug) ? `${cachePrefix}${slug}` : null

export const cachePlatformReturn = (slug: string, value: unknown) => {
  const key = keyFor(slug)
  const descriptor = validatePlatformReturn(value)
  if (!key || !descriptor) return
  try {
    window.sessionStorage.setItem(key, JSON.stringify(descriptor))
  } catch {
    // Session storage is an availability aid only; history state remains enough.
  }
}

export const readCachedPlatformReturn = (slug: string) => {
  const key = keyFor(slug)
  if (!key) return null
  try {
    const descriptor = validatePlatformReturn(
      JSON.parse(window.sessionStorage.getItem(key) ?? 'null')
    )
    if (!descriptor) window.sessionStorage.removeItem(key)
    return descriptor
  } catch {
    window.sessionStorage.removeItem(key)
    return null
  }
}

export const clearCachedPlatformReturn = (slug: string) => {
  const key = keyFor(slug)
  if (!key) return
  try {
    window.sessionStorage.removeItem(key)
  } catch {
    // Nothing durable or authoritative depends on this cleanup.
  }
}
