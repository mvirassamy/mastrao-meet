import { ApiError } from '@/api/ApiError'

export const isMissingRoomLifecycle = (error: unknown) =>
  error instanceof ApiError && [404, 410].includes(error.statusCode)
