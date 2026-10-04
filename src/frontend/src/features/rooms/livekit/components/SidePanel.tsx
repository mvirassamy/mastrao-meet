import { closeSidePanel, layoutStore } from '@/stores/layout'
import { css } from '@/styled-system/css'
import { Heading } from 'react-aria-components'
import { Button } from '@/primitives'
import { Tab, TabList, TabPanel, Tabs } from '@/primitives/Tabs'
import { AppAppearanceProvider } from '@/primitives/appAppearance'
import { ArrowLeftIcon, CloseIcon } from '@/icons'
import { useTranslation } from 'react-i18next'
import { ParticipantsList } from '@/features/participants/components/ParticipantsList'
import { PanelId, SubPanelId, useSidePanel } from '../hooks/useSidePanel'
import React, { ReactNode, useCallback, useEffect, useRef } from 'react'
import { Chat } from '@/features/chat/components/Chat'
import { LiveTranscriptSidePanel } from '@/features/subtitle/component/LiveTranscriptSidePanel'
import { Effects } from './effects/Effects'
import { Admin } from './Admin'
import { Tools } from './Tools'
import { Info } from './Info'
import { useReactionsToolbar } from '@/features/reactions/hooks/useReactionsToolbar'
import { useRestoreFocus } from '@/hooks/useRestoreFocus'

type StyledSidePanelProps = {
  title: string
  ariaLabel: string
  children: ReactNode
  onClose: () => void
  isClosed: boolean
  closeButtonTooltip: string
  isSubmenu: boolean
  onBack: () => void
  backButtonLabel: string
  isReactionToolbarOpen?: boolean
}

const StyledSidePanel = React.forwardRef<HTMLElement, StyledSidePanelProps>(
  (
    {
      title,
      ariaLabel,
      children,
      onClose,
      isClosed,
      isReactionToolbarOpen,
      closeButtonTooltip,
      isSubmenu = false,
      onBack,
      backButtonLabel,
    },
    ref
  ) => (
    <aside
      ref={ref}
      tabIndex={-1}
      // Mastrao application look (Inter, palette); pure CSS, no media impact.
      className={`authenticated-meet-workspace ${css({
        borderWidth: '1px',
        borderStyle: 'solid',
        borderColor: 'box.border',
        backgroundColor: 'box.bg',
        color: 'box.text',
        borderRadius: '12px',
        boxShadow: '0 1px 2px rgb(0 0 0 / 0.05)',
        fontSize: '0.875rem',
        lineHeight: '1.25rem',
        flex: 1,
        position: 'absolute',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        margin: 'var(--sizes-room-side-panel-margin)',
        marginLeft: 0,
        marginBottom: 0,
        padding: 0,
        gap: 0,
        right: 0,
        top: 0,
        width: 'min(var(--sizes-room-side-panel), calc(100vw - 1.5rem))',
        transition: '.5s cubic-bezier(.4,0,.2,1) 5ms',
        '&:focus': {
          outline: 'none',
        },
      })}`}
      style={{
        transform: isClosed
          ? 'translateX(calc(var(--sizes-room-side-panel) + var(--sizes-room-side-panel-margin)))'
          : 'none',
        bottom: isReactionToolbarOpen
          ? 'calc( var(--sizes-room-control-bar) + var(--sizes-room-reaction-toolbar-height) + calc(var(--lk-grid-gap) / 2))'
          : 'var(--sizes-room-control-bar)',
      }}
      aria-hidden={isClosed}
      aria-label={ariaLabel}
    >
      <div
        className={css({
          display: isClosed ? 'none' : 'flex',
          alignItems: 'center',
          gap: '0.25rem',
          minHeight: '3.25rem',
          paddingTop: '0.625rem',
          paddingBottom: '0.375rem',
          paddingLeft: '1.25rem',
          paddingRight: '0.75rem',
        })}
        style={isSubmenu ? { paddingLeft: '0.75rem' } : undefined}
      >
        {isSubmenu && (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={backButtonLabel}
            onPress={onBack}
          >
            <ArrowLeftIcon aria-hidden="true" />
          </Button>
        )}
        <Heading
          slot="title"
          level={1}
          className={css({
            flex: 1,
            minWidth: 0,
            margin: 0,
            fontSize: '1rem',
            lineHeight: '1.5rem',
            fontWeight: 600,
            letterSpacing: '-0.01em',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          })}
        >
          {title}
        </Heading>
        <Button
          variant="ghost"
          size="icon-sm"
          onPress={onClose}
          aria-label={closeButtonTooltip}
          tooltip={closeButtonTooltip}
        >
          <CloseIcon aria-hidden="true" />
        </Button>
      </div>
      <AppAppearanceProvider>{children}</AppAppearanceProvider>
    </aside>
  )
)

StyledSidePanel.displayName = 'StyledSidePanel'

type PanelProps = {
  isOpen: boolean
  children: React.ReactNode
  keepAlive?: boolean
}

const Panel = ({ isOpen, keepAlive = false, children }: PanelProps) => (
  <div
    style={{
      display: isOpen ? 'inherit' : 'none',
      flexDirection: 'column',
      overflow: 'hidden',
      flexGrow: 1,
    }}
  >
    {keepAlive || isOpen ? children : null}
  </div>
)

const MeetingConversationTabs = ({ isChatOpen }: { isChatOpen: boolean }) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'sidePanel.tabs' })
  const { toggleChat, openLiveTranscript } = useSidePanel()

  return (
    <Tabs
      selectedKey={isChatOpen ? PanelId.CHAT : SubPanelId.LIVE_TRANSCRIPT}
      onSelectionChange={(key) => {
        if (key === PanelId.CHAT) toggleChat()
        else openLiveTranscript()
      }}
      className={css({ flex: 1, minHeight: 0 })}
    >
      <TabList className={css({ marginX: '1.25rem', flexShrink: 0 })}>
        <Tab
          id={SubPanelId.LIVE_TRANSCRIPT}
          className={css({ flex: 1, textAlign: 'center' })}
        >
          {t('transcription')}
        </Tab>
        <Tab
          id={PanelId.CHAT}
          className={css({ flex: 1, textAlign: 'center' })}
        >
          {t('messages')}
        </Tab>
      </TabList>
      <TabPanel
        id={PanelId.CHAT}
        flex
        className={css({ padding: 0, marginTop: 0, minHeight: 0 })}
      >
        <Chat />
      </TabPanel>
      <TabPanel
        id={SubPanelId.LIVE_TRANSCRIPT}
        flex
        className={css({ padding: 0, marginTop: 0, minHeight: 0 })}
      >
        <LiveTranscriptSidePanel />
      </TabPanel>
    </Tabs>
  )
}

export const SidePanel = () => {
  const {
    activePanelId,
    isParticipantsOpen,
    isEffectsOpen,
    isChatOpen,
    isSidePanelOpen,
    isToolsOpen,
    isAdminOpen,
    isInfoOpen,
    isLiveTranscriptOpen,
    isSubPanelOpen,
    activeSubPanelId,
  } = useSidePanel()
  const { t } = useTranslation('rooms', { keyPrefix: 'sidePanel' })
  const title = t(`heading.${activeSubPanelId || activePanelId}`)

  useEffect(() => {
    layoutStore.activeSubPanelId = SubPanelId.LIVE_TRANSCRIPT
    layoutStore.activePanelId = PanelId.TOOLS
  }, [])

  const { isOpen: isReactionToolbarOpen } = useReactionsToolbar()

  const asideRef = useRef<HTMLElement>(null)

  const focusAside = useCallback(() => {
    requestAnimationFrame(() => {
      const activeElement = document.activeElement
      if (
        activeElement?.getAttribute('role') === 'tab' &&
        asideRef.current?.contains(activeElement)
      )
        return
      asideRef.current?.focus({ preventScroll: true })
    })
  }, [])

  const handlePanelOpened = useCallback(() => {
    if (activePanelId === PanelId.CHAT) return
    focusAside()
  }, [activePanelId, focusAside])

  useRestoreFocus(isSidePanelOpen, {
    onOpened: handlePanelOpened,
    preventScroll: true,
    activeKey:
      isChatOpen || isLiveTranscriptOpen ? PanelId.CHAT : activePanelId,
  })

  return (
    <StyledSidePanel
      ref={asideRef}
      title={title}
      ariaLabel={t('ariaLabel', { title })}
      onClose={closeSidePanel}
      closeButtonTooltip={t('closeButton', {
        content: t(`content.${activeSubPanelId || activePanelId}`),
      })}
      isClosed={!isSidePanelOpen}
      isSubmenu={isSubPanelOpen && !isLiveTranscriptOpen}
      isReactionToolbarOpen={isReactionToolbarOpen}
      backButtonLabel={t('backToTools')}
      onBack={() => (layoutStore.activeSubPanelId = null)}
    >
      <Panel isOpen={isParticipantsOpen}>
        <ParticipantsList />
      </Panel>
      <Panel isOpen={isEffectsOpen}>
        <Effects />
      </Panel>
      {(isChatOpen || isLiveTranscriptOpen) && (
        <MeetingConversationTabs isChatOpen={isChatOpen} />
      )}
      <Panel isOpen={isToolsOpen && !isLiveTranscriptOpen} keepAlive={true}>
        <Tools />
      </Panel>
      <Panel isOpen={isAdminOpen}>
        <Admin />
      </Panel>
      <Panel isOpen={isInfoOpen}>
        <Info />
      </Panel>
    </StyledSidePanel>
  )
}
