import { ReactNode, useId, useState } from 'react'
import { ChevronUpIcon } from '@/icons'
import { styled, HStack, VStack } from '@/styled-system/jsx'
import { css } from '@/styled-system/css'

const countClass = css({
  color: 'muted-foreground',
  fontWeight: 400,
  fontVariantNumeric: 'tabular-nums',
})

const Container = styled('div', {
  base: {
    border: '1px solid',
    borderColor: 'border',
    borderRadius: '10px',
    margin: '0 0.75rem 0.75rem',
  },
})

const Header = styled('button', {
  base: {
    minHeight: '2.25rem',
    paddingX: '0.75rem',
    paddingY: '0.375rem',
    gap: '0.5rem',
    cursor: 'pointer',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    width: '100%',
    fontSize: '0.875rem',
    lineHeight: '1.25rem',
    fontWeight: 500,
    transition: 'background 200ms',
    borderTopRadius: '9px', // container radius (10) minus its 1px border
    outline: 'none',
    _hover: { backgroundColor: 'muted' },
    _focusVisible: { boxShadow: 'inset 0 0 0 2px token(colors.ring)' },
  },
  variants: {
    isOpen: { false: { borderRadius: '9px' } },
  },
})

const Chevron = styled(ChevronUpIcon, {
  base: {
    transition: 'transform 200ms',
    flexShrink: 0,
    color: 'muted-foreground',
  },
  variants: {
    isOpen: { false: { transform: 'rotate(180deg)' } },
  },
})

const List = styled(VStack, {
  base: {
    borderTop: '1px solid',
    borderTopColor: 'border',
    alignItems: 'start',
    minHeight: 0,
    flexGrow: 1,
    paddingY: '0.375rem',
    paddingX: '0.75rem',
    gap: 0,
  },
})

export type ParticipantsCollapsibleSectionProps = {
  heading: string
  count: number
  action?: ReactNode
  children: ReactNode
}

export const ParticipantsCollapsibleSection = ({
  heading,
  count,
  action,
  children,
}: ParticipantsCollapsibleSectionProps) => {
  const [isOpen, setIsOpen] = useState(true)
  const listId = useId()
  return (
    <Container>
      <Header
        type="button"
        isOpen={isOpen}
        aria-expanded={isOpen}
        aria-controls={listId}
        onClick={() => setIsOpen((open) => !open)}
      >
        <HStack justify="space-between" width="100%">
          <span>{heading}</span>
          <span className={countClass}>{count}</span>
        </HStack>
        <Chevron size={16} isOpen={isOpen} aria-hidden />
      </Header>
      {isOpen && (
        <List id={listId} role="list">
          {action}
          {children}
        </List>
      )}
    </Container>
  )
}
