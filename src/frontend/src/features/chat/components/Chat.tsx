import { useTranslation } from 'react-i18next'
import { Text } from '@/primitives'
import { ChatMessages } from './ChatMessages'
import { ChatTextArea } from './ChatTextArea'
import { styled } from '@/styled-system/jsx'

const ChatContainer = styled('div', {
  base: {
    display: 'flex',
    padding: '0 1.25rem',
    flexGrow: 1,
    flexDirection: 'column',
    minHeight: 0,
  },
})

const ChatMessagesContainer = styled('div', {
  base: {
    display: 'flex',
    flexDirection: 'column',
    flexGrow: 1,
    minHeight: 0,
  },
})

const TextContainer = styled('div', {
  base: {
    display: 'flex',
    padding: '0.625rem 0.75rem',
    backgroundColor: 'muted',
    borderRadius: '8px',
    color: 'muted-foreground',
    fontSize: '0.8125rem',
    lineHeight: '1.25rem',
    marginBottom: '0.75rem',
  },
})

export const Chat = () => {
  const { t } = useTranslation('rooms', { keyPrefix: 'chat' })

  return (
    <ChatContainer>
      <TextContainer>
        <Text variant="inherits">{t('disclaimer')}</Text>
      </TextContainer>
      <ChatMessagesContainer>
        <ChatMessages />
      </ChatMessagesContainer>
      <ChatTextArea />
    </ChatContainer>
  )
}
