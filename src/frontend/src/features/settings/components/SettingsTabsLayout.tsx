import { type ReactNode } from 'react'
import { Tab, TabList, Tabs, type Key } from 'react-aria-components'
import { css } from '@/styled-system/css'
import { useMediaQuery } from '@/features/rooms/livekit/hooks/useMediaQuery'

export type SettingsTabItem = {
  id: string
  label: string
  icon: ReactNode
}

const tabClass = css({
  display: 'flex',
  alignItems: 'center',
  gap: '9px',
  minHeight: { base: '40px', md: '36px' },
  paddingX: '10px',
  borderRadius: '9px',
  color: 'var(--workspace-ink-soft, var(--foreground))',
  fontSize: '0.875rem',
  whiteSpace: 'nowrap',
  cursor: 'pointer',
  outline: 'none',
  '& svg': { width: '17px', height: '17px', flexShrink: 0 },
  '&[data-hovered]': { backgroundColor: 'muted', color: 'foreground' },
  '&[data-selected]': { backgroundColor: 'accent', color: 'primary' },
  '&[data-focus-visible]': {
    outline: '2px solid token(colors.ring)',
    outlineOffset: '2px',
  },
})

/**
 * Tabbed settings layout shared by every settings dialog: navigation styled
 * like the Mastrao sidebar, vertical on desktop and horizontal on mobile.
 */
export const SettingsTabsLayout = ({
  label,
  tabs,
  defaultSelectedKey,
  children,
}: {
  label: string
  tabs: SettingsTabItem[]
  defaultSelectedKey?: Key
  children: ReactNode
}) => {
  const isWide = useMediaQuery('(min-width: 640px)')

  return (
    <Tabs
      orientation={isWide ? 'vertical' : 'horizontal'}
      defaultSelectedKey={defaultSelectedKey}
      className={css({
        display: 'flex',
        flexDirection: { base: 'column', sm: 'row' },
        gap: { base: '0.75rem', sm: '1.25rem' },
        width: '100%',
        minHeight: 0,
        height: { base: 'auto', sm: 'min(34rem, calc(100dvh - 7rem))' },
      })}
    >
      <TabList
        aria-label={label}
        className={css({
          display: 'flex',
          flexDirection: { base: 'row', sm: 'column' },
          flexShrink: 0,
          gap: '5px',
          width: { base: 'auto', sm: '11rem' },
          paddingBottom: { base: '0.75rem', sm: '1rem' },
          paddingRight: { base: 0, sm: '1.25rem' },
          borderBottom: { base: '1px solid token(colors.border)', sm: 'none' },
          borderRight: { base: 'none', sm: '1px solid token(colors.border)' },
          overflowX: { base: 'auto', sm: 'visible' },
        })}
      >
        {tabs.map((tab) => (
          <Tab key={tab.id} id={tab.id} className={tabClass}>
            {tab.icon}
            {tab.label}
          </Tab>
        ))}
      </TabList>
      <div
        className={css({
          minWidth: 0,
          minHeight: 0,
          flex: 1,
          overflowY: 'auto',
          paddingBottom: '1rem',
          // Existing tab panels keep their content; align their layout.
          '& [role=tabpanel]': { padding: 0, outline: 'none' },
          '& :is(h2, h3)': {
            marginTop: 0,
            fontSize: '0.875rem',
            lineHeight: '1.25rem',
            fontWeight: 600,
          },
          '& p': { fontSize: '0.875rem', lineHeight: '1.25rem' },
        })}
      >
        {children}
      </div>
    </Tabs>
  )
}
