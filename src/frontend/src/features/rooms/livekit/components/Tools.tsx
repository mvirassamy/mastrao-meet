import { A, Button, Div, Icon, Text } from '@/primitives'
import { css } from '@/styled-system/css'
import { useTranslation } from 'react-i18next'
import { ReactNode } from 'react'
import { SubPanelId, useSidePanel } from '../hooks/useSidePanel'
import { useRestoreFocus } from '@/hooks/useRestoreFocus'
import {
  useIsRecordingModeEnabled,
  RecordingMode,
  TranscriptSidePanel,
  ScreenRecordingSidePanel,
} from '@/features/recording'
import { useConfig } from '@/api/useConfig'

export interface ToolsButtonProps {
  icon: ReactNode
  title: string
  description: string
  onPress: () => void
}

const ToolButton = ({
  icon,
  title,
  description,
  onPress,
}: ToolsButtonProps) => {
  return (
    <Button
      variant="outline"
      fullWidth
      // Layout only: a two-line tile instead of a single-line button.
      className={css({
        justifyContent: 'start',
        gap: '0.75rem',
        height: 'auto',
        padding: '0.625rem 0.75rem',
        whiteSpace: 'normal',
        textAlign: 'start',
      })}
      onPress={onPress}
    >
      <div
        aria-hidden="true"
        className={css({
          height: '32px',
          minWidth: '32px',
          borderRadius: '8px',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          position: 'relative',
          background: 'accent',
          color: 'primary',
        })}
      >
        {icon}
      </div>
      <div className={css({ minWidth: 0 })}>
        <Text
          margin={false}
          as="span"
          variant="inherits"
          className={css({
            display: 'flex',
            gap: 0.25,
            fontSize: '0.875rem',
            lineHeight: '1.25rem',
            fontWeight: 500,
            color: 'var(--heading-foreground)',
          })}
        >
          {title}
        </Text>
        <Text
          as="span"
          variant="note"
          wrap="pretty"
          className={css({
            display: 'block',
            fontSize: '0.8125rem',
            lineHeight: '1.125rem',
            fontWeight: 400,
          })}
        >
          {description}
        </Text>
      </div>
      <div
        aria-hidden="true"
        className={css({
          marginLeft: 'auto',
          height: '100%',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          color: 'muted-foreground',
        })}
      >
        <Icon name="chevron_forward" />
      </div>
    </Button>
  )
}

export const Tools = () => {
  const { data } = useConfig()
  const {
    openTranscript,
    openScreenRecording,
    activeSubPanelId,
    isToolsOpen,
    isSidePanelOpen,
  } = useSidePanel()
  const { t } = useTranslation('rooms', { keyPrefix: 'moreTools' })

  // Restore focus to the element that opened the Tools panel
  // following the same pattern as Chat.
  useRestoreFocus(isToolsOpen, {
    // If the active element is a MenuItem (DIV) that will be unmounted when the menu closes,
    // find the "more options" button ("Plus d'options") that opened the menu
    resolveTrigger: (activeEl) => {
      if (activeEl?.tagName === 'DIV') {
        return document.querySelector<HTMLElement>('#room-options-trigger')
      }
      // For direct button clicks (e.g. "Plus d'outils"), use the active element as is
      return activeEl
    },
    restoreFocusRaf: true,
    preventScroll: true,
    shouldRestoreOnClose: () => !isSidePanelOpen,
  })

  const isTranscriptEnabled = useIsRecordingModeEnabled(
    RecordingMode.Transcript
  )

  const isScreenRecordingEnabled = useIsRecordingModeEnabled(
    RecordingMode.ScreenRecording
  )

  switch (activeSubPanelId) {
    case SubPanelId.TRANSCRIPT:
      return <TranscriptSidePanel />
    case SubPanelId.SCREEN_RECORDING:
      return <ScreenRecordingSidePanel />
    default:
      break
  }

  return (
    <Div
      display="flex"
      overflowY="scroll"
      padding="0 1.25rem"
      flexGrow={1}
      flexDirection="column"
      alignItems="start"
      gap={0.5}
    >
      <Text
        variant="note"
        wrap="balance"
        className={css({
          fontSize: '0.8125rem',
          lineHeight: '1.25rem',
          marginBottom: '0.75rem',
        })}
      >
        {t('body')}{' '}
        {data?.support?.help_article_more_tools && (
          <A
            href={data.support.help_article_more_tools}
            target="_blank"
            rel="noopener noreferrer"
            externalIcon
            color="note"
            aria-label={t('linkAriaLabel')}
          >
            {t('moreLink')}
          </A>
        )}
      </Text>
      {isTranscriptEnabled && (
        <ToolButton
          icon={<Icon name="speech_to_text" />}
          title={t('tools.transcript.title')}
          description={t('tools.transcript.body')}
          onPress={() => openTranscript()}
        />
      )}
      {isScreenRecordingEnabled && (
        <ToolButton
          icon={<Icon name="mode_standby" />}
          title={t('tools.screenRecording.title')}
          description={t('tools.screenRecording.body')}
          onPress={() => openScreenRecording()}
        />
      )}
    </Div>
  )
}
