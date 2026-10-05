import { ChevronUpDownIcon, LogoutIcon } from '@/icons'
import { useTranslation } from 'react-i18next'
import { Menu as AriaMenu, MenuItem } from 'react-aria-components'
import type { ApiUser } from '@/features/auth/api/ApiUser'
import { logout } from '@/features/auth/utils/logout'
import { Button, Menu } from '@/primitives'
import { css } from '@/styled-system/css'
import { MeetUserAvatar } from './MeetUserAvatar'

type MeetSidebarUserMenuProps = {
  user: ApiUser
  collapsed?: boolean
  onLogout?: () => void
}

const truncate = {
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
} as const

export const MeetSidebarUserMenu = ({
  user,
  collapsed = false,
  onLogout,
}: MeetSidebarUserMenuProps) => {
  const { t } = useTranslation()
  const fullName = user.full_name?.trim() ?? ''
  const label = fullName || user.email
  const accessibleLabel = `${t('loggedInUserTooltip')} ${label}`

  return (
    <Menu placement={collapsed ? 'right' : 'top'} density="app">
      <Button
        size={collapsed ? 'icon' : 'default'}
        variant="invert"
        aria-label={accessibleLabel}
        tooltip={collapsed ? accessibleLabel : undefined}
        // Layout only: a white bordered profile button; colours come from
        // "invert".
        className={css({
          width: collapsed ? '40px' : '100%',
          height: collapsed ? '40px' : '48px',
          minWidth: collapsed ? '40px' : 0,
          minHeight: collapsed ? '40px' : '48px',
          justifyContent: collapsed ? 'center' : 'flex-start',
          gap: collapsed ? 0 : '0.5rem',
          padding: collapsed ? '0!' : '0.5rem!',
          borderRadius: '8px',
          borderColor: 'var(--border)',
          boxShadow: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
          fontWeight: 400,
          overflow: 'hidden',
        })}
      >
        <MeetUserAvatar name={label} compact={collapsed} />
        <span
          className={
            collapsed
              ? css({ srOnly: true })
              : css({
                  display: 'grid',
                  minWidth: 0,
                  flex: 1,
                  textAlign: 'left',
                  lineHeight: 1.25,
                })
          }
        >
          <span
            className={css({
              ...truncate,
              fontSize: '0.875rem',
              fontWeight: 600,
            })}
          >
            {label}
          </span>
          {fullName && (
            <span
              className={css({
                ...truncate,
                color: 'var(--muted-foreground)',
                fontSize: '0.75rem',
                fontWeight: 400,
              })}
            >
              {user.email}
            </span>
          )}
        </span>
        {!collapsed && (
          <ChevronUpDownIcon
            size={16}
            aria-hidden="true"
            className={css({
              marginLeft: 'auto',
              color: 'var(--muted-foreground)',
            })}
          />
        )}
      </Button>
      <div className={css({ width: '14rem', maxWidth: 'calc(100vw - 2rem)' })}>
        <div
          className={css({
            display: 'flex',
            minWidth: 0,
            flexDirection: 'column',
            gap: '0.25rem',
            padding: '0.5rem',
          })}
        >
          <span
            className={css({
              ...truncate,
              color: 'var(--foreground)',
              fontSize: '0.875rem',
              fontWeight: 500,
              lineHeight: 1.35,
            })}
          >
            {label}
          </span>
          {fullName && (
            <span
              className={css({
                ...truncate,
                color: 'var(--muted-foreground)',
                fontSize: '0.875rem',
                lineHeight: 1.35,
              })}
            >
              {user.email}
            </span>
          )}
        </div>
        <div
          role="separator"
          className={css({
            height: '1px',
            marginX: '-0.25rem',
            marginY: '0.25rem',
            backgroundColor: 'var(--border)',
          })}
        />
        <AriaMenu
          aria-label={accessibleLabel}
          onAction={(key) => {
            if (key !== 'logout') return
            onLogout?.()
            void logout()
          }}
          className={css({ outline: 'none' })}
        >
          <MenuItem
            id="logout"
            textValue={t('logout')}
            className={css({
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              paddingX: '0.375rem',
              paddingY: '0.375rem',
              borderRadius: '6px',
              color: 'var(--foreground)',
              fontSize: '0.875rem',
              lineHeight: '1.25rem',
              cursor: 'default',
              outline: 'none',
              '&[data-focused], &[data-hovered]': {
                backgroundColor: 'var(--muted)',
              },
              '&[data-focus-visible]': {
                boxShadow: '0 0 0 2px #2d5be3',
              },
              '& svg': {
                width: '16px',
                height: '16px',
                flexShrink: 0,
                color: 'var(--muted-foreground)',
              },
            })}
          >
            <LogoutIcon />
            <span>{t('logout')}</span>
          </MenuItem>
        </AriaMenu>
      </div>
    </Menu>
  )
}
