import { SidebarIcon } from '@/icons'
import { ReactNode, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog, Modal, ModalOverlay } from 'react-aria-components'
import type { ApiUser } from '@/features/auth/api/ApiUser'
import { Button } from '@/primitives'
import { AppAppearanceProvider } from '@/primitives/appAppearance'
import { css } from '@/styled-system/css'
import { MeetSidebar } from './MeetSidebar'

const COLLAPSED_STORAGE_KEY = 'mastrao-meet.sidebar-collapsed'

// Keeps the desktop rail state while moving between workspace pages.
const readStoredCollapsed = () => {
  try {
    return sessionStorage.getItem(COLLAPSED_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

const storeCollapsed = (value: boolean) => {
  try {
    sessionStorage.setItem(COLLAPSED_STORAGE_KEY, String(value))
  } catch {
    // Storage can be unavailable (private mode); the rail still toggles.
  }
}

type MeetWorkspaceShellProps = {
  user: ApiUser
  toolbar: ReactNode
  children: ReactNode
}

export const MeetWorkspaceShell = ({
  user,
  toolbar,
  children,
}: MeetWorkspaceShellProps) => {
  const { t } = useTranslation('home')
  const [collapsed, setCollapsed] = useState(readStoredCollapsed)
  const [mobileOpen, setMobileOpen] = useState(false)
  const toggleCollapsed = () => {
    const next = !collapsed
    setCollapsed(next)
    storeCollapsed(next)
  }

  return (
    <AppAppearanceProvider>
      <div
        className={`authenticated-meet-workspace ${css({
          display: 'flex',
          width: '100%',
          height: '100dvh',
          minHeight: 0,
          overflow: 'hidden',
          backgroundColor: 'var(--workspace-paper)',
          color: 'foreground',
        })}`}
      >
        <aside
          aria-label={t('dashboard.sidebar.label')}
          className={css({
            display: { base: 'none', md: 'flex' },
            width: collapsed ? '70px' : '260px',
            flexShrink: 0,
            overflow: 'hidden',
            borderRight: '1px solid token(colors.border)',
            // Same motion as the Mastrao application sidebar: 200 ms, linear.
            transition: 'width 200ms linear',
            _motionReduce: { transition: 'none' },
          })}
        >
          <MeetSidebar
            user={user}
            collapsed={collapsed}
            onToggle={toggleCollapsed}
          />
        </aside>

        {mobileOpen && (
          <ModalOverlay
            isOpen
            isDismissable
            onOpenChange={setMobileOpen}
            className={`authenticated-meet-workspace ${css({
              position: 'fixed',
              inset: 0,
              zIndex: 50,
              display: { base: 'block', md: 'none' },
              backgroundColor: 'overlay',
            })}`}
          >
            <Modal
              className={css({
                width: 'min(18rem, calc(100vw - 2rem))',
                height: '100dvh',
                outline: 'none',
              })}
            >
              <Dialog
                aria-label={t('dashboard.sidebar.label')}
                className={css({
                  width: '100%',
                  height: '100%',
                  outline: 'none',
                  boxShadow: 'sm',
                })}
              >
                <MeetSidebar
                  user={user}
                  mobile
                  onToggle={() => setMobileOpen(false)}
                  onNavigate={() => setMobileOpen(false)}
                />
              </Dialog>
            </Modal>
          </ModalOverlay>
        )}

        <div
          className={css({
            display: 'flex',
            minWidth: 0,
            minHeight: 0,
            flex: 1,
            flexDirection: 'column',
            overflowY: 'auto',
            overscrollBehavior: 'contain',
            backgroundColor: 'background',
          })}
        >
          <header
            className={css({
              display: 'flex',
              minHeight: '64px',
              flexShrink: 0,
              alignItems: 'center',
              borderBottom: '1px solid token(colors.border)',
            })}
          >
            <div
              className={css({
                display: 'flex',
                width: '100%',
                maxWidth: '80rem',
                marginX: 'auto',
                flexWrap: 'wrap',
                alignItems: 'center',
                gap: '0.5rem',
                padding: { base: '0.625rem 1rem', md: '0.75rem 1rem' },
              })}
            >
              <div
                className={css({
                  display: { base: 'flex', md: 'none' },
                  minWidth: 0,
                  alignItems: 'center',
                  gap: '0.625rem',
                  marginRight: 'auto',
                })}
              >
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={t('dashboard.sidebar.open')}
                  tooltip={t('dashboard.sidebar.open')}
                  onPress={() => setMobileOpen(true)}
                  className={css({
                    width: '44px',
                    height: '44px',
                    minWidth: '44px',
                    minHeight: '44px',
                  })}
                >
                  <SidebarIcon />
                </Button>
                <span
                  className={css({
                    overflow: 'hidden',
                    fontSize: '0.875rem',
                    fontWeight: 600,
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  })}
                >
                  Mastrao Visio
                </span>
              </div>
              {toolbar}
            </div>
          </header>
          <div
            className={css({
              display: 'flex',
              minWidth: 0,
              minHeight: 0,
              flex: 1,
              flexDirection: 'column',
            })}
          >
            {children}
          </div>
        </div>
      </div>
    </AppAppearanceProvider>
  )
}
