import { TextArea } from '@/primitives'
import { styled } from '@/styled-system/jsx'
import { css } from '@/styled-system/css'
import React, { useCallback, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useSnapshot } from 'valtio'
import {
  chatStore,
  clearTextAreaValue,
  persistTextAreaValue,
} from '@/stores/chat'
import { ChatSubmitButton } from './ChatSubmitButton'

const StyledContainer = styled('div', {
  base: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: '0.25rem',
    margin: '0.75rem 0 1.5rem',
    padding: '0.25rem 0.25rem 0.25rem 0.125rem',
    border: '1px solid token(colors.border)',
    borderRadius: '8px',
    backgroundColor: 'card',
    boxShadow: '0 1px 2px rgb(0 0 0 / 0.05)',
    transition: 'border-color 150ms, box-shadow 150ms',
    _hover: { borderColor: 'input' },
    _focusWithin: {
      borderColor: 'ring',
      boxShadow: '0 0 0 3px rgb(45 91 227 / 0.2)',
    },
  },
})

const textAreaClassName = css({
  backgroundColor: 'transparent',
  color: 'foreground',
  fontSize: '0.875rem',
  outline: 'none',
  _placeholder: { color: 'muted-foreground', opacity: 1 },
})

export const ChatTextArea = () => {
  const { isSending, send, textAreaValue } = useSnapshot(chatStore)

  const { t } = useTranslation('rooms', { keyPrefix: 'controls.chat.input' })

  const inputRef = React.useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    const raf = requestAnimationFrame(() => {
      if (document.activeElement?.getAttribute('role') === 'tab') return
      el.focus({ preventScroll: true })
      const end = el.value.length
      el.setSelectionRange(end, end)
    })
    return () => cancelAnimationFrame(raf)
  }, [])

  const handleSubmit = useCallback(async () => {
    const text = chatStore.textAreaValue
    if (!send || !text) return
    await send(text)
    inputRef?.current?.focus({ preventScroll: true })
    clearTextAreaValue()
  }, [send, inputRef])

  const isDisabled = !textAreaValue.trim() || isSending

  const onKeyDown = async (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    e.stopPropagation()
    if (e.key !== 'Enter' || (e.key === 'Enter' && e.shiftKey) || isDisabled)
      return
    e.preventDefault()
    await handleSubmit()
  }

  const onKeyUp = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    e.stopPropagation()
  }

  return (
    <StyledContainer>
      <TextArea
        ref={inputRef}
        value={textAreaValue}
        onKeyDown={onKeyDown}
        onKeyUp={onKeyUp}
        onChange={(e) => {
          persistTextAreaValue(e.target.value)
        }}
        fieldSizing={'content'}
        className={textAreaClassName}
        style={{
          border: 'none',
          resize: 'none',
          height: 'auto',
          maxHeight: '240px',
          minHeight: '36px',
          lineHeight: '1.25rem',
          padding: '8px 10px',
        }}
        spellCheck={false}
        maxLength={2000}
        placeholder={t('textArea.placeholder')}
        aria-label={t('textArea.label')}
      />
      <ChatSubmitButton handleSubmit={handleSubmit} isDisabled={isDisabled} />
    </StyledContainer>
  )
}
