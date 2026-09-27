import { useEffect } from 'react'
import { ApiError } from '@/api/ApiError'
import { redirectToLogin } from './loginRedirect'

export const isAuthRequiredError = (error: unknown) =>
  error instanceof ApiError && error.statusCode === 401

/** Sends an expired or missing session to the login flow once. */
export const useLoginRedirectOnAuthError = (error: unknown) => {
  useEffect(() => {
    if (isAuthRequiredError(error)) redirectToLogin()
  }, [error])
}
