import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { Router } from 'wouter'
import { useBrowserLocation } from 'wouter/use-browser-location'
import {
  NavigationGuardContext,
  type RequestNavigation,
} from './useNavigationGuard'

type Guard = { requestNavigation: RequestNavigation }

/**
 * Keep the outgoing route mounted until its draft is safe. Browser Back and
 * standalone navigateTo calls change browser history before a nested router
 * can intercept them, so committing the route belongs above the route switch.
 */
export function GuardedRouter({ children }: { children: ReactNode }) {
  const [browserPath, navigate] = useBrowserLocation()
  const latestPath = useRef(browserPath)
  latestPath.current = browserPath
  const [committedPath, setCommittedPath] = useState(browserPath)
  const [guard, setGuard] = useState<Guard | null>(null)

  const register = useCallback((requestNavigation: RequestNavigation) => {
    const registration = { requestNavigation }
    setGuard(registration)
    return () =>
      setGuard((current) => (current === registration ? null : current))
  }, [])

  useLayoutEffect(() => {
    if (browserPath === committedPath) return
    const accept = () => setCommittedPath(latestPath.current)
    if (guard) guard.requestNavigation(accept)
    else accept()
  }, [browserPath, committedPath, guard])

  const useCommittedLocation = useCallback(
    () => [committedPath, navigate] as [string, typeof navigate],
    [committedPath, navigate]
  )

  return (
    <NavigationGuardContext.Provider value={register}>
      <Router hook={useCommittedLocation}>{children}</Router>
    </NavigationGuardContext.Provider>
  )
}
