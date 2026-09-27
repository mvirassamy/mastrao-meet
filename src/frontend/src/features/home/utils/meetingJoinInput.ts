import { isRoomValid } from '@/features/rooms'
import { normalizeRoomId } from '@/features/rooms/utils/isRoomValid'

export const normalizeMeetingInput = (value: string) => {
  const trimmed = value.trim().replace(/\/$/, '')
  const withoutOrigin = trimmed.startsWith(`${window.location.origin}/`)
    ? trimmed.slice(window.location.origin.length + 1)
    : trimmed

  return normalizeRoomId(withoutOrigin)
}

export const parseMeetingInput = (value: string) => {
  const normalized = normalizeMeetingInput(value)
  return normalized.length > 0 && isRoomValid(normalized) ? normalized : null
}

export const isMeetingInputValid = (value: string) =>
  parseMeetingInput(value) !== null
