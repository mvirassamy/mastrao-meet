import { useTranslation } from 'react-i18next'
import {
  PersonalNotesEditor,
  PersonalNotesProvider,
  usePersonalNotes,
  NoteEnrichment,
} from '@/features/meeting-notes'
import { useUser } from '@/features/auth/api/useUser'
import { css } from '@/styled-system/css'
import { NOTES_ICON } from './meetingContent'
import { sectionCard, sectionHeader, sectionTitle } from './meetingDetailLayout'

/** In a tab, the tab already shows the title and its 3D icon. */
function NotesCard({
  inTab,
  meetingRef,
}: {
  inTab: boolean
  meetingRef?: string
}) {
  const { t } = useTranslation('personalNotes', { useSuspense: false })
  const title = t('title', { defaultValue: 'Mes notes' })
  return (
    <section
      aria-labelledby="personal-notes-heading"
      data-section="personal-notes"
      className={inTab ? undefined : sectionCard}
    >
      {inTab ? (
        <h2 id="personal-notes-heading" className={css({ srOnly: true })}>
          {title}
        </h2>
      ) : (
        <div className={sectionHeader}>
          <img
            src={NOTES_ICON}
            alt=""
            aria-hidden="true"
            width={96}
            height={96}
            decoding="async"
          />
          <h2 id="personal-notes-heading" className={sectionTitle}>
            {title}
          </h2>
        </div>
      )}
      {/* The whole note shows at once, growing with its text. */}
      {meetingRef ? (
        <NoteEnrichment meetingRef={meetingRef} />
      ) : (
        <PersonalNotesEditor fit="content" />
      )}
    </section>
  )
}

/** Deliberately outside the shared meeting summary/transcript payload. */
export function MeetingPersonalNotesSection({
  endpoint,
  inTab = false,
  meetingRef,
}: {
  endpoint: string
  inTab?: boolean
  meetingRef?: string
}) {
  const { user } = useUser()
  const notes = usePersonalNotes()
  if (notes?.endpoint === endpoint)
    return <NotesCard inTab={inTab} meetingRef={meetingRef} />
  return (
    <PersonalNotesProvider
      endpoint={endpoint}
      identityKey={user?.id ?? 'guest'}
    >
      <NotesCard inTab={inTab} meetingRef={meetingRef} />
    </PersonalNotesProvider>
  )
}
