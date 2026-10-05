import { useTranslation } from 'react-i18next'
import { DialogTrigger } from 'react-aria-components'
import { SettingsDialog } from '@/features/settings'
import { SettingsIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'

/**
 * "Paramètres" row just above the profile, shaped like the navigation
 * links; icon only when the sidebar is collapsed.
 */
export const MeetSidebarSettingsItem = ({
  collapsed = false,
  mobile = false,
}: {
  collapsed?: boolean
  mobile?: boolean
}) => {
  const { t } = useTranslation('settings')
  const label = t('settingsButtonLabel')

  return (
    <DialogTrigger>
      <Button
        variant="ghost"
        aria-label={collapsed ? label : undefined}
        tooltip={collapsed ? label : undefined}
        // Layout only: the navigation link shape; colours come from "ghost".
        className={css({
          width: collapsed ? '40px' : '100%',
          height: mobile ? '44px' : collapsed ? '40px' : '41px',
          minHeight: mobile ? '44px' : collapsed ? '40px' : '41px',
          marginX: collapsed ? 'auto' : 0,
          justifyContent: collapsed ? 'center' : 'flex-start',
          gap: collapsed ? 0 : '9px',
          paddingX: collapsed ? 0 : '12px',
          borderRadius: collapsed ? '12px' : '9px',
          color: 'var(--workspace-ink-soft, var(--foreground))',
          fontSize: '0.875rem',
          fontWeight: 400,
        })}
      >
        <SettingsIcon size={19} aria-hidden="true" />
        {!collapsed && label}
      </Button>
      <SettingsDialog appearance="app" />
    </DialogTrigger>
  )
}
