import { Input as RACInput, type InputProps } from 'react-aria-components'
import { css, cx } from '@/styled-system/css'

const appInput = css({
  width: '100%',
  minHeight: { base: '40px', md: '36px' },
  marginTop: '0.375rem',
  paddingX: '0.75rem',
  border: '1px solid token(colors.border)',
  borderRadius: '8px',
  backgroundColor: 'card',
  boxShadow: '0 1px 2px rgb(0 0 0 / 0.05)',
  color: 'foreground',
  fontSize: '0.875rem',
  lineHeight: '1.25rem',
  outline: 'none',
  transition: 'border-color 150ms, box-shadow 150ms',
  _placeholder: { color: 'muted-foreground' },
  '&[data-hovered]': { borderColor: 'input' },
  '&[data-focused]': {
    borderColor: 'ring',
    boxShadow: '0 0 0 3px rgb(45 91 227 / 0.2)',
  },
  '&[data-invalid]': {
    borderColor: 'destructive',
    boxShadow: '0 0 0 3px rgb(169 46 54 / 0.15)',
  },
  '&[data-disabled]': { cursor: 'not-allowed', opacity: 0.5 },
})

/** Mastrao application text input (authenticated workspace only). */
export const AppInput = ({ className, ...props }: InputProps) => (
  <RACInput
    {...props}
    className={cx(appInput, typeof className === 'string' ? className : '')}
  />
)
