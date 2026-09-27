import { type DialogProps } from '@/primitives'
import { AppDialog } from '@/primitives/AppDialog'
import { Icon } from '@/primitives/Icon'
import { useTranslation } from 'react-i18next'
import {
  AccountIcon,
  NotificationIcon,
  SettingsIcon,
  SpeakerIcon,
  VideoOnIcon,
  EyeIcon,
  KeyboardIcon,
} from '@/icons'
import { AccountTab } from './tabs/AccountTab'
import { NotificationsTab } from './tabs/NotificationsTab'
import { GeneralTab } from './tabs/GeneralTab'
import { AudioTab } from './tabs/AudioTab'
import { VideoTab } from './tabs/VideoTab'
import { TranscriptionTab } from './tabs/TranscriptionTab'
import { ShortcutTab } from './tabs/ShortcutTab'
import { SettingsDialogExtendedKey } from '@/features/settings/type'
import { useIsAdminOrOwner } from '@/features/rooms/livekit/hooks/useIsAdminOrOwner'
import { AccessibilityTab } from './tabs/AccessibilityTab'
import { SettingsTabsLayout, type SettingsTabItem } from './SettingsTabsLayout'
import { settingsDialogBodyClass } from './settingsDialogStyles'

export type SettingsDialogExtended = Pick<
  DialogProps,
  'isOpen' | 'onOpenChange'
> & {
  defaultSelectedTab?: SettingsDialogExtendedKey
}

/**
 * In-room settings. Same dialog and tab layout as the workspace settings; the
 * backdrop is a plain dim layer so live video is not blurred on every frame.
 */
export const SettingsDialogExtended = (props: SettingsDialogExtended) => {
  const { t } = useTranslation('settings')
  const isAdminOrOwner = useIsAdminOrOwner()
  const tab = (
    id: SettingsDialogExtendedKey,
    icon: SettingsTabItem['icon']
  ) => ({
    id,
    label: t(`tabs.${id}`),
    icon,
  })

  const tabs: SettingsTabItem[] = [
    tab(SettingsDialogExtendedKey.ACCOUNT, <AccountIcon aria-hidden="true" />),
    tab(SettingsDialogExtendedKey.AUDIO, <SpeakerIcon aria-hidden="true" />),
    tab(SettingsDialogExtendedKey.VIDEO, <VideoOnIcon aria-hidden="true" />),
    tab(SettingsDialogExtendedKey.GENERAL, <SettingsIcon aria-hidden="true" />),
    tab(
      SettingsDialogExtendedKey.NOTIFICATIONS,
      <NotificationIcon aria-hidden="true" />
    ),
    tab(
      SettingsDialogExtendedKey.SHORTCUTS,
      <KeyboardIcon aria-hidden="true" />
    ),
    ...(isAdminOrOwner
      ? [
          tab(
            SettingsDialogExtendedKey.TRANSCRIPTION,
            <Icon name="speech_to_text" />
          ),
        ]
      : []),
    tab(
      SettingsDialogExtendedKey.ACCESSIBILITY,
      <EyeIcon aria-hidden="true" />
    ),
  ]

  return (
    <AppDialog
      title={t('dialog.heading')}
      size="lg"
      backdrop="dim"
      isOpen={props.isOpen}
      onOpenChange={props.onOpenChange}
      bodyClassName={settingsDialogBodyClass}
    >
      <SettingsTabsLayout
        label={t('dialog.heading')}
        tabs={tabs}
        defaultSelectedKey={props.defaultSelectedTab}
      >
        <AccountTab
          id={SettingsDialogExtendedKey.ACCOUNT}
          onOpenChange={props.onOpenChange}
        />
        <AudioTab id={SettingsDialogExtendedKey.AUDIO} />
        <VideoTab id={SettingsDialogExtendedKey.VIDEO} />
        <GeneralTab id={SettingsDialogExtendedKey.GENERAL} />
        <NotificationsTab id={SettingsDialogExtendedKey.NOTIFICATIONS} />
        <ShortcutTab id={SettingsDialogExtendedKey.SHORTCUTS} />
        {/* Transcription tab won't be accessible if the tab is not active in the tab list */}
        <TranscriptionTab id={SettingsDialogExtendedKey.TRANSCRIPTION} />
        <AccessibilityTab id={SettingsDialogExtendedKey.ACCESSIBILITY} />
      </SettingsTabsLayout>
    </AppDialog>
  )
}
