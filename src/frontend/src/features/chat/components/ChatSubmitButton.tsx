import React from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/primitives'
import { SendIcon } from '@/icons'
type ChatSubmitButtonProps = {
  handleSubmit: () => Promise<void>
  isDisabled: boolean
}

export const ChatSubmitButton = React.memo(
  ({ handleSubmit, isDisabled }: ChatSubmitButtonProps) => {
    const { t } = useTranslation('rooms', { keyPrefix: 'controls.chat.input' })
    return (
      <Button
        variant="ghost"
        size="icon-sm"
        onPress={handleSubmit}
        isDisabled={isDisabled}
        aria-label={t('button.label')}
      >
        <SendIcon />
      </Button>
    )
  }
)

ChatSubmitButton.displayName = 'ChatSubmitButton'
