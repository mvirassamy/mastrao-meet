import { useTranslation } from 'react-i18next'
import type { ApiUser } from '@/features/auth/api/ApiUser'
import { css } from '@/styled-system/css'
import { MeetSidebarBrand } from './MeetSidebarBrand'
import { MeetSidebarContext } from './MeetSidebarContext'
import { MeetSidebarNav } from './MeetSidebarNav'
import { MeetSidebarUserMenu } from './MeetSidebarUserMenu'

type MeetSidebarProps = {
  user: ApiUser
  collapsed?: boolean
  mobile?: boolean
  onToggle: () => void
  onNavigate?: () => void
}

export const MeetSidebar = ({
  user,
  collapsed = false,
  mobile = false,
  onToggle,
  onNavigate,
}: MeetSidebarProps) => {
  const { t } = useTranslation('home')
  const toggleLabel = mobile
    ? t('dashboard.sidebar.close')
    : collapsed
      ? t('dashboard.sidebar.expand')
      : t('dashboard.sidebar.collapse')

  return (
    <div
      className={css({
        display: 'flex',
        width: '100%',
        height: '100%',
        minHeight: 0,
        flexDirection: 'column',
        backgroundColor: 'var(--workspace-paper)',
        color: 'foreground',
      })}
    >
      <div
        className={css({
          flexShrink: 0,
          paddingX: collapsed ? '12px' : '14px',
          paddingTop: mobile ? '12px' : '22px',
        })}
      >
        <MeetSidebarBrand
          collapsed={collapsed}
          mobile={mobile}
          onToggle={onToggle}
          toggleLabel={toggleLabel}
        />
        <div className={css({ marginTop: '24px' })}>
          <MeetSidebarContext collapsed={collapsed} mobile={mobile} />
        </div>
      </div>
      <div
        className={css({
          minHeight: 0,
          flex: 1,
          overflowY: 'auto',
          paddingX: collapsed ? '12px' : '14px',
          marginTop: '24px',
        })}
      >
        <MeetSidebarNav
          collapsed={collapsed}
          mobile={mobile}
          onNavigate={onNavigate}
        />
      </div>
      <footer
        className={css({
          flexShrink: 0,
          padding: collapsed ? '16px 8px' : '16px',
          borderTop: '1px solid token(colors.border)',
        })}
      >
        <MeetSidebarUserMenu
          user={user}
          collapsed={collapsed}
          onLogout={onNavigate}
        />
      </footer>
    </div>
  )
}
