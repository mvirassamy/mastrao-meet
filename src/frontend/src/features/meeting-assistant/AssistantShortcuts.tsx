import { useTranslation } from 'react-i18next'
import { ChevronDownIcon } from '@/icons'
import { Button, Menu, MenuList } from '@/primitives'
import { ASSISTANT_ICON } from '@/features/meetingHistory/components/meetingContent'
import { css } from '@/styled-system/css'

export type MeetingAssistantAction =
  | 'short-summary'
  | 'actions'
  | 'follow-up-email'

const SHORTCUT_ICONS = {
  'short-summary': '/assets/illustrations/section-synthese.webp',
  actions: '/assets/illustrations/actions.webp',
  'follow-up-email': '/assets/illustrations/email.webp',
} as const satisfies Record<MeetingAssistantAction, string>

const SHORTCUTS = Object.keys(SHORTCUT_ICONS) as MeetingAssistantAction[]

/** The assistant's ready-made requests, gathered in one menu of the composer. */
export function AssistantShortcuts({
  disabled,
  onAction,
}: {
  disabled: boolean
  onAction: (action: MeetingAssistantAction) => void
}) {
  const { t } = useTranslation('meetingAssistant')
  return (
    <Menu density="app" placement="top start">
      <Button
        variant="secondary"
        size="xs"
        isDisabled={disabled}
        className={css({ borderRadius: 'full!', paddingLeft: '0.25rem!' })}
      >
        <img
          src={ASSISTANT_ICON}
          alt=""
          aria-hidden="true"
          width={16}
          height={16}
        />
        {t('actions.title')}
        <ChevronDownIcon size={14} aria-hidden="true" />
      </Button>
      <MenuList
        aria-label={t('actions.title')}
        extraPadding={false}
        onAction={(key) => {
          const action = SHORTCUTS.find((shortcut) => shortcut === key)
          if (action) onAction(action)
        }}
        items={SHORTCUTS.map((action) => ({
          value: action,
          label: (
            <span
              className={css({
                display: 'flex',
                alignItems: 'center',
                gap: '0.625rem',
                paddingY: '0.25rem',
                minWidth: '14rem',
                fontSize: '0.875rem',
              })}
            >
              <img
                src={SHORTCUT_ICONS[action]}
                alt=""
                aria-hidden="true"
                width={24}
                height={24}
              />
              <span
                className={css({ display: 'flex', flexDirection: 'column' })}
              >
                {t(`actions.${action}`)}
                <span
                  className={css({
                    fontSize: '0.75rem',
                    color: 'muted-foreground',
                  })}
                >
                  {t(`actions.hint.${action}`)}
                </span>
              </span>
            </span>
          ),
        }))}
      />
    </Menu>
  )
}
