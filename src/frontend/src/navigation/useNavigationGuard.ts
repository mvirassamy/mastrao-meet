import { createContext, useContext, useLayoutEffect } from 'react'

export type RequestNavigation = (accept: () => void) => void
export const NavigationGuardContext = createContext<
  ((request: RequestNavigation) => () => void) | null
>(null)

/** A notes scope registers while mounted; ordinary routes need no guard. */
export function useNavigationGuard(requestNavigation: RequestNavigation) {
  const register = useContext(NavigationGuardContext)
  useLayoutEffect(
    () => register?.(requestNavigation),
    [register, requestNavigation]
  )
}
