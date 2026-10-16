import { useTranslation } from 'react-i18next'
import {
  PersonalNotesEditor,
  PersonalNotesProvider,
  usePersonalNotes,
} from '@/features/meeting-notes'
import { useUser } from '@/features/auth/api/useUser'
import { sectionCard, sectionHeader, sectionTitle } from './meetingDetailLayout'

const NOTES_ICON = '/assets/illustrations/section-notes.webp'

function NotesCard() {
  const { t } = useTranslation('personalNotes', { useSuspense: false })
  return (
    <section
      aria-labelledby="personal-notes-heading"
      data-section="personal-notes"
      className={sectionCard}
    >
      <div className={sectionHeader({ look: 'content' })}>
        <img
          src={NOTES_ICON}
          alt=""
          aria-hidden="true"
          width={96}
          height={96}
          decoding="async"
        />
        <h2 id="personal-notes-heading" className={sectionTitle}>
          {t('title', { defaultValue: 'Mes notes' })}
        </h2>
      </div>
      {/* The whole note shows at once, growing with its text. */}
      <PersonalNotesEditor fit="content" />
    </section>
  )
}

/** Deliberately outside the shared meeting summary/transcript payload. */
export function MeetingPersonalNotesSection({
  endpoint,
}: {
  endpoint: string
}) {
  const { user } = useUser()
  const notes = usePersonalNotes()
  if (notes?.endpoint === endpoint) return <NotesCard />
  return (
    <PersonalNotesProvider
      endpoint={endpoint}
      identityKey={user?.id ?? 'guest'}
    >
      <NotesCard />
    </PersonalNotesProvider>
  )
}
