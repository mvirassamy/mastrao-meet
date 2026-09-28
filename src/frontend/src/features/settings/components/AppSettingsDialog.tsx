import { setInterfaceLanguage } from '@/i18n/setInterfaceLanguage'
import { type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { TabPanel } from 'react-aria-components'
import { RoomsIcon, LogoutIcon, SettingsIcon } from '@/icons'
import { useLanguageLabels } from '@/i18n/useLanguageLabels'
import { AppDialog, type AppDialogProps } from '@/primitives/AppDialog'
import { Button, Field } from '@/primitives'
import { css } from '@/styled-system/css'
import { useUser } from '@/features/auth/api/useUser'
import { logout } from '@/features/auth/utils/logout'
import { LoginButton } from '@/components/LoginButton'
import { MeetUserAvatar } from '@/features/home/components/authenticated/MeetUserAvatar'
import { RoomsTab } from './tabs/RoomsTab'
import { SettingsTabsLayout } from './SettingsTabsLayout'
import { settingsDialogBodyClass } from './settingsDialogStyles'

type AppSettingsDialogProps = Pick<
  AppDialogProps,
  'isOpen' | 'onOpenChange' | 'backdrop'
>

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
  const { t } = useTranslation('settings')
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
              size="sm"
              variant="outline"
              icon={<LogoutIcon aria-hidden="true" />}
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
              void setInterfaceLanguage(lang as string)
            }}
          />
        </div>
      </Section>
    </div>
  )
}

/**
 * Settings dialog (home, history, public pages and room device menus).
 * The in-room settings use SettingsDialogExtended with the same layout.
 */
export const AppSettingsDialog = (props: AppSettingsDialogProps) => {
  const { t } = useTranslation('settings')
  const { isLoggedIn } = useUser()

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
      bodyClassName={settingsDialogBodyClass}
    >
      <SettingsTabsLayout
        label={t('dialog.heading')}
        defaultSelectedKey={GENERAL}
        tabs={[
          {
            id: GENERAL,
            label: t(`tabs.${GENERAL}`),
            icon: <SettingsIcon aria-hidden="true" />,
          },
          {
            id: ROOMS,
            label: t(`tabs.${ROOMS}`),
            icon: <RoomsIcon aria-hidden="true" />,
          },
        ]}
      >
        <TabPanel id={GENERAL}>
          <GeneralSettings />
        </TabPanel>
        <RoomsTab id={ROOMS} />
      </SettingsTabsLayout>
    </AppDialog>
  )
}
