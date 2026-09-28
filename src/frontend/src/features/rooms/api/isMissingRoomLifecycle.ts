import { ApiError } from '@/api/ApiError'

// A lifecycle 404 also masks a missing browser grant, so it cannot prove that
// the meeting ended. A valid grant receives the authoritative `ended` state.
export const isMissingRoomLifecycle = (error: unknown) =>
  error instanceof ApiError && error.statusCode === 410
