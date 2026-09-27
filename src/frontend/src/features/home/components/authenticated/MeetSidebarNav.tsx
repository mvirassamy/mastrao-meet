import { CalendarEventIcon, HistoryIcon, type AppIconComponent } from '@/icons'
import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'wouter'
import {
  MEETING_HISTORY_PATH,
  isMeetingHistoryPath,
} from '@/features/meetingHistory/paths'
import { VisualOnlyTooltip } from '@/primitives/VisualOnlyTooltip'
import { css } from '@/styled-system/css'

type MeetSidebarNavProps = {
  collapsed?: boolean
  mobile?: boolean
  onNavigate?: () => void
}

type NavItem = {
  to: string
  label: string
  Icon: AppIconComponent
  isActive: boolean
}

export const MeetSidebarNav = ({
  collapsed = false,
  mobile = false,
  onNavigate,
}: MeetSidebarNavProps) => {
  const { t } = useTranslation('home')
  const [location] = useLocation()
  const items: NavItem[] = [
    {
      to: '/',
      label: t('dashboard.sidebar.meetings'),
      Icon: CalendarEventIcon,
      isActive: location === '/',
    },
    {
      to: MEETING_HISTORY_PATH,
      label: t('dashboard.sidebar.history'),
      Icon: HistoryIcon,
      isActive: isMeetingHistoryPath(location),
    },
  ]

  const renderLink = ({ to, label, Icon, isActive }: NavItem) => (
    <Link
      to={to}
      aria-current={isActive ? 'page' : undefined}
      aria-label={collapsed ? label : undefined}
      onClick={onNavigate}
      className={css({
        display: 'flex',
        width: collapsed ? '40px' : '100%',
        height: mobile ? '44px' : collapsed ? '40px' : '41px',
        minHeight: mobile ? '44px' : collapsed ? '40px' : '41px',
        marginX: collapsed ? 'auto' : 0,
        alignItems: 'center',
        justifyContent: collapsed ? 'center' : 'flex-start',
        gap: collapsed ? 0 : '9px',
        paddingX: collapsed ? 0 : '12px',
        borderRadius: collapsed ? '12px' : '9px',
        backgroundColor: isActive ? 'accent' : 'transparent',
        color: isActive
          ? 'primary'
          : 'var(--workspace-ink-soft, var(--foreground))',
        fontSize: '0.875rem',
        fontWeight: 400,
        textDecoration: 'none',
        _hover: {
          backgroundColor: isActive ? 'accent' : 'muted',
          color: isActive ? 'primary' : 'foreground',
        },
        _focusVisible: {
          outline: '2px solid',
          outlineColor: 'ring',
          outlineOffset: '2px',
        },
      })}
    >
      <Icon size={19} aria-hidden="true" />
      <span className={collapsed ? css({ srOnly: true }) : undefined}>
        {label}
      </span>
    </Link>
  )

  return (
    <nav
      aria-label={t('dashboard.sidebar.label')}
      className={css({ width: '100%' })}
    >
      <ul
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '5px',
          margin: 0,
          padding: 0,
          listStyle: 'none',
        })}
      >
        {items.map((item) => (
          <li key={item.to}>
            {collapsed ? (
              <VisualOnlyTooltip
                tooltip={item.label}
                ariaLabel={item.label}
                tooltipPosition="bottom"
                className={css({ width: '100%' })}
              >
                {renderLink(item)}
              </VisualOnlyTooltip>
            ) : (
              renderLink(item)
            )}
          </li>
        ))}
      </ul>
    </nav>
  )
}
