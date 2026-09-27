import { type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Tab, TabList, TabPanel, Tabs } from 'react-aria-components'
import {
  RiDoorOpenFill,
  RiLogoutBoxRFill,
  RiSettings3Fill,
} from '@remixicon/react'
import { useLanguageLabels } from '@/i18n/useLanguageLabels'
import { AppDialog, type AppDialogProps } from '@/primitives/AppDialog'
import { Button, Field } from '@/primitives'
import { css } from '@/styled-system/css'
import { useUser } from '@/features/auth/api/useUser'
import { logout } from '@/features/auth/utils/logout'
import { LoginButton } from '@/components/LoginButton'
import { MeetUserAvatar } from '@/features/home/components/authenticated/MeetUserAvatar'
import { useMediaQuery } from '@/features/rooms/livekit/hooks/useMediaQuery'
import { RoomsTab } from './tabs/RoomsTab'

type AppSettingsDialogProps = Pick<AppDialogProps, 'isOpen' | 'onOpenChange'>

const GENERAL = 'general'
const ROOMS = 'rooms'

const Section = ({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) => (
  <section
    className={css({
      display: 'flex',
      flexDirection: 'column',
      gap: '0.625rem',
    })}
  >
    <h3
      className={css({
        margin: 0,
        fontSize: '0.875rem',
        lineHeight: '1.25rem',
        fontWeight: 600,
      })}
    >
      {title}
    </h3>
    {children}
  </section>
)

const mutedText = css({
  margin: 0,
  color: 'muted-foreground',
  fontSize: '0.875rem',
  lineHeight: '1.25rem',
})

const GeneralSettings = () => {
  const { t, i18n } = useTranslation('settings')
  const { user, isLoggedIn } = useUser()
  const { languagesList, currentLanguage } = useLanguageLabels()
  const name = user?.full_name?.trim() || user?.email || ''

  return (
    <div
      className={css({
        display: 'flex',
        flexDirection: 'column',
        gap: '1.5rem',
      })}
    >
      <Section title={t('account.heading')}>
        {isLoggedIn && user ? (
          <div
            className={css({
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'center',
              gap: '0.75rem',
              padding: '0.75rem',
              border: '1px solid token(colors.border)',
              borderRadius: '10px',
            })}
          >
            <MeetUserAvatar name={name} />
            <div
              className={css({
                display: 'grid',
                minWidth: 0,
                flex: 1,
                lineHeight: '1.25rem',
              })}
            >
              <span
                className={css({
                  overflow: 'hidden',
                  fontWeight: 500,
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                })}
              >
                {name}
              </span>
              {user.full_name?.trim() && (
                <span
                  className={css({
                    overflow: 'hidden',
                    color: 'muted-foreground',
                    fontSize: '0.8125rem',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  })}
                >
                  {user.email}
                </span>
              )}
            </div>
            <Button
              size="appSm"
              variant="outline"
              icon={<RiLogoutBoxRFill aria-hidden="true" />}
              onPress={() => void logout()}
              className={css({ minHeight: { base: '40px', md: '32px' } })}
            >
              {t('logout', { ns: 'global' })}
            </Button>
          </div>
        ) : (
          <>
            <p className={mutedText}>{t('account.youAreNotLoggedIn')}</p>
            <div className={css({ display: 'flex' })}>
              <LoginButton size="sm" />
            </div>
          </>
        )}
      </Section>
      <Section title={t('language.heading')}>
        <div className={css({ maxWidth: '20rem' })}>
          <Field
            type="select"
            label={t('language.label')}
            items={languagesList}
            defaultSelectedKey={currentLanguage.key}
            onSelectionChange={(lang) => {
              i18n.changeLanguage(lang as string)
            }}
          />
        </div>
      </Section>
    </div>
  )
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
 * Settings opened from the authenticated workspace, drawn with AppDialog.
 * Room settings keep their own dialogs and styles.
 */
export const AppSettingsDialog = (props: AppSettingsDialogProps) => {
  const { t } = useTranslation('settings')
  const { isLoggedIn } = useUser()
  const isWide = useMediaQuery('(min-width: 640px)')

  if (!isLoggedIn) {
    return (
      <AppDialog title={t('dialog.heading')} size="sm" {...props}>
        <GeneralSettings />
      </AppDialog>
    )
  }

  return (
    <AppDialog
      title={t('dialog.heading')}
      size="lg"
      {...props}
      bodyClassName={css({
        display: 'flex',
        overflowY: 'hidden',
        paddingBottom: 0,
      })}
    >
      <Tabs
        orientation={isWide ? 'vertical' : 'horizontal'}
        defaultSelectedKey={GENERAL}
        className={css({
          display: 'flex',
          flexDirection: { base: 'column', sm: 'row' },
          gap: { base: '0.75rem', sm: '1.25rem' },
          width: '100%',
          minHeight: 0,
          height: { base: 'auto', sm: 'min(32rem, calc(100dvh - 7rem))' },
        })}
      >
        <TabList
          aria-label={t('dialog.heading')}
          className={css({
            display: 'flex',
            flexDirection: { base: 'row', sm: 'column' },
            flexShrink: 0,
            gap: '5px',
            width: { base: 'auto', sm: '10.5rem' },
            paddingBottom: { base: '0.75rem', sm: '1rem' },
            paddingRight: { base: 0, sm: '1.25rem' },
            borderBottom: {
              base: '1px solid token(colors.border)',
              sm: 'none',
            },
            borderRight: {
              base: 'none',
              sm: '1px solid token(colors.border)',
            },
            overflowX: { base: 'auto', sm: 'visible' },
          })}
        >
          <Tab id={GENERAL} className={tabClass}>
            <RiSettings3Fill aria-hidden="true" />
            {t(`tabs.${GENERAL}`)}
          </Tab>
          <Tab id={ROOMS} className={tabClass}>
            <RiDoorOpenFill aria-hidden="true" />
            {t(`tabs.${ROOMS}`)}
          </Tab>
        </TabList>
        <div
          className={css({
            minWidth: 0,
            minHeight: 0,
            flex: 1,
            overflowY: 'auto',
            paddingBottom: '1rem',
            // Room defaults reuse their existing panel; align its typography.
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
          <TabPanel id={GENERAL}>
            <GeneralSettings />
          </TabPanel>
          <RoomsTab id={ROOMS} />
        </div>
      </Tabs>
    </AppDialog>
  )
}
