import { createContext, useContext } from 'react'

/**
 * Marks the authenticated Mastrao workspace (home, history and their
 * dialogs). Shared controls read it to switch to the Mastrao application
 * look, so meeting rooms keep their original controls untouched.
 */
export const AppAppearanceContext = createContext(false)

export const useAppAppearance = () => useContext(AppAppearanceContext)
