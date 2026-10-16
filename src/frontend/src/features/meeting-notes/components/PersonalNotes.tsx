import { usePersonalNotes } from '../hooks/usePersonalNotes'
import { PersonalNotesEditor } from './PersonalNotesEditor'
import { PersonalNotesProvider } from './PersonalNotesProvider'

/** Uses the persistent meeting provider when present; otherwise stands alone. */
export function PersonalNotes({ endpoint }: { endpoint: string }) {
  const notes = usePersonalNotes()
  if (notes?.endpoint === endpoint) return <PersonalNotesEditor />
  return (
    <PersonalNotesProvider endpoint={endpoint}>
      <PersonalNotesEditor />
    </PersonalNotesProvider>
  )
}
