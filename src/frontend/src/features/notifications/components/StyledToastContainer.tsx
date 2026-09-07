import { styled } from '@/styled-system/jsx'

export const StyledToastContainer = styled('div', {
  base: {
    margin: 0.5,
    boxShadow:
      'var(--shadow-strong) 0px 4px 8px 0px, var(--shadow-strong) 0px 6px 20px 4px',
    backgroundColor: 'popover',
    color: 'popover-foreground',
    borderRadius: '8px',
    '&[data-entering]': { animation: 'fade 200ms' },
    '&[data-exiting]': { animation: 'fade 150ms reverse ease-in' },
    width: 'fit-content',
    marginLeft: 'auto',
  },
})
