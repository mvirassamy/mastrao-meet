import { useTranslation } from 'react-i18next'
import {
  PersonalNotesEditor,
  PersonalNotesProvider,
  usePersonalNotes,
} from '@/features/meeting-notes'
import { useUser } from '@/features/auth/api/useUser'
import { css } from '@/styled-system/css'
import { sectionCard, sectionTitle } from './meetingDetailLayout'

/** Deliberately outside the shared meeting summary/transcript payload. */
export function MeetingPersonalNotesSection({
  endpoint,
}: {
  endpoint: string
}) {
  const { t } = useTranslation('personalNotes', { useSuspense: false })
  const { user } = useUser()
  const notes = usePersonalNotes()
  let editor = <PersonalNotesEditor />
  if (notes?.endpoint !== endpoint) {
    editor = (
      <PersonalNotesProvider
        endpoint={endpoint}
        identityKey={user?.id ?? 'guest'}
      >
        {editor}
      </PersonalNotesProvider>
    )
  }
  return (
    <section
      aria-labelledby="personal-notes-heading"
      data-section="personal-notes"
      className={sectionCard}
    >
      <h2
        id="personal-notes-heading"
        className={`${sectionTitle} ${css({ padding: '1rem', paddingBottom: 0 })}`}
      >
        {t('title', { defaultValue: 'Mes notes' })}
      </h2>
      {editor}
    </section>
  )
}
