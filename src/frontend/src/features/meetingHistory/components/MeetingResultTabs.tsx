import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Tab, TabList, TabPanel, Tabs } from '@/primitives/Tabs'
import { css } from '@/styled-system/css'
import { NOTES_ICON, SECTION_ICONS, STATUS_COLORS } from './meetingContent'
import type { SectionLook } from './meetingResultState'

const TAB_IDS = ['summary', 'transcript', 'notes'] as const
type ResultTab = (typeof TAB_IDS)[number]

const TAB_ICONS: Record<ResultTab, string> = {
  summary: SECTION_ICONS.summary,
  transcript: SECTION_ICONS.transcript,
  notes: NOTES_ICON,
}

const pillTab = css({
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.5rem',
  padding: '0.375rem 0.875rem 0.375rem 0.5rem!',
  borderRadius: 'full',
  color: 'foreground',
  fontSize: '0.875rem',
  fontWeight: 500,
  transition: 'background 150ms, color 150ms',
  '&[data-hovered]': { backgroundColor: 'muted' },
  '&[data-selected]': { backgroundColor: 'accent', color: 'primary' },
  '&[data-focus-visible]': {
    outline: '2px solid token(colors.ring)',
    outlineOffset: '1px',
  },
})

/** Colour of the dot, and of its soft halo, for a tab without content. */
const dotColors = (look: Exclude<SectionLook, 'content'>) => {
  switch (look) {
    case 'pending':
      return { color: '#f59e0b', halo: '#fef3c7' }
    case 'failed':
      return { color: STATUS_COLORS.failed, halo: '#fde2e2' }
    case 'empty':
      return { color: STATUS_COLORS.absent, halo: 'transparent' }
  }
}

/** Small dot on a tab whose content is in progress, failed or absent. */
const StateDot = ({ look }: { look: SectionLook }) => {
  const { t } = useTranslation('meetingHistory')
  if (look === 'content') return null
  const { color, halo } = dotColors(look)
  return (
    <>
      <span
        aria-hidden="true"
        style={{ backgroundColor: color, boxShadow: `0 0 0 3px ${halo}` }}
        className={css({ width: '7px', height: '7px', borderRadius: 'full' })}
      />
      <span className={css({ srOnly: true })}>
        {t(`result.tabState.${look}`)}
      </span>
    </>
  )
}

/**
 * Summary, transcript and private notes as pill tabs. Every panel stays
 * mounted so a draft note or a scrolled transcript survives a tab change.
 */
export function MeetingResultTabs({
  summary,
  transcript,
  notes,
  states,
}: {
  summary: ReactNode
  transcript: ReactNode
  notes: ReactNode
  /** Tabs without readable content show a small coloured dot. */
  states: Partial<Record<ResultTab, SectionLook>>
}) {
  const { t } = useTranslation('meetingHistory')
  const { t: notesT } = useTranslation('personalNotes', { useSuspense: false })
  const labels: Record<ResultTab, string> = {
    summary: t('summary.title'),
    transcript: t('transcript.title'),
    notes: notesT('title', { defaultValue: 'Mes notes' }),
  }
  const panels: Record<ResultTab, ReactNode> = { summary, transcript, notes }
  return (
    <Tabs defaultSelectedKey="summary">
      <TabList
        aria-label={t('result.sections')}
        border={false}
        className={css({
          flexWrap: 'wrap',
          gap: '0.25rem',
          paddingBottom: '0.75rem',
          borderBottom: '1px solid token(colors.border)',
        })}
      >
        {TAB_IDS.map((id) => (
          <Tab key={id} id={id} className={pillTab}>
            <img
              src={TAB_ICONS[id]}
              alt=""
              aria-hidden="true"
              width={24}
              height={24}
            />
            {labels[id]}
            {states[id] && <StateDot look={states[id]} />}
          </Tab>
        ))}
      </TabList>
      {TAB_IDS.map((id) => (
        <TabPanel
          key={id}
          id={id}
          shouldForceMount
          className={css({
            padding: '0!',
            marginTop: '1rem',
            '&[inert]': { display: 'none' },
          })}
        >
          {panels[id]}
        </TabPanel>
      ))}
    </Tabs>
  )
}
