import { type ReactNode } from 'react'
import { AppAppearanceContext } from './useAppAppearance'

/** Enables the Mastrao application look for the controls it wraps. */
export const AppAppearanceProvider = ({
  children,
}: {
  children: ReactNode
}) => (
  <AppAppearanceContext.Provider value>
    {children}
  </AppAppearanceContext.Provider>
)
