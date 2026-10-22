import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/primitives'
import {
  ASSISTANT_ICON,
  STATUS_COLORS,
} from '@/features/meetingHistory/components/meetingContent'
import { css } from '@/styled-system/css'

export type AssistantCommandError = 'unconfirmed' | 'conflict' | 'rejected'

/**
 * A request that did not go through, told by the assistant itself: its icon
 * greyed with a red mark, a short sentence and, when the outcome is unknown,
 * one way to try again.
 */
export function AssistantFailure({
  error,
  disabled,
  onRetry,
}: {
  error: AssistantCommandError
  disabled: boolean
  onRetry: () => void
}) {
  const { t } = useTranslation('meetingAssistant')
  const row = useRef<HTMLDivElement>(null)
  // It appears under the last message, so it brings itself into view.
  useEffect(() => {
    row.current?.scrollIntoView?.({ block: 'end' })
  }, [error])
  return (
    <div
      ref={row}
      role="alert"
      className={css({
        display: 'flex',
        alignItems: 'flex-start',
        gap: '0.625rem',
      })}
    >
      <span
        aria-hidden="true"
        className={css({
          position: 'relative',
          flexShrink: 0,
          width: '24px',
          height: '24px',
        })}
      >
        <img
          src={ASSISTANT_ICON}
          alt=""
          width={24}
          height={24}
          className={css({ filter: 'grayscale(1)', opacity: 0.6 })}
        />
        <span
          className={css({
            position: 'absolute',
            right: '-3px',
            bottom: '-2px',
            width: '11px',
            height: '11px',
            borderRadius: 'full',
            border: '2px solid token(colors.card)',
          })}
          style={{ backgroundColor: STATUS_COLORS.failed }}
        />
      </span>
      <div
        className={css({
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
          gap: '0.25rem',
          fontSize: '0.875rem',
          lineHeight: '1.6',
          color: 'muted-foreground',
        })}
      >
        <p className={css({ margin: 0 })}>{t(`failure.${error}`)}</p>
        {error === 'unconfirmed' && (
          <Button
            variant="link"
            size="xs"
            isDisabled={disabled}
            onPress={onRetry}
            className={css({ paddingX: '0!', minHeight: 'auto!' })}
          >
            {t('failure.retry')}
          </Button>
        )}
      </div>
    </div>
  )
}
