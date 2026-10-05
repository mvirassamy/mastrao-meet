import { css } from '@/styled-system/css'

/** Shared look of DatePickerField and TimeSelectField. */

export const pickerLabel = css({
  display: 'block',
  color: 'foreground',
  fontSize: '0.875rem',
  lineHeight: '1.25rem',
  fontWeight: 500,
})

/** Field outline: the same look as AppInput. */
export const pickerShell = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.5rem',
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
  textAlign: 'left',
  outline: 'none',
  cursor: 'pointer',
  transition: 'border-color 150ms, box-shadow 150ms',
  '&[data-hovered]': { borderColor: 'input' },
  '&[data-focus-visible], &[data-focus-within], &[aria-expanded=true]': {
    borderColor: 'ring',
    boxShadow: '0 0 0 3px rgb(45 91 227 / 0.2)',
  },
  '&[data-invalid]': {
    borderColor: 'destructive',
    boxShadow: '0 0 0 3px rgb(169 46 54 / 0.15)',
  },
  '&[data-disabled]': { cursor: 'not-allowed', opacity: 0.5 },
})

export const pickerPopover = css({
  padding: '0.5rem',
  border: '1px solid token(colors.border)',
  borderRadius: '12px',
  backgroundColor: 'popover',
  color: 'popover-foreground',
  boxShadow: '0 12px 32px rgb(8 20 46 / 0.12), 0 2px 6px rgb(8 20 46 / 0.06)',
  outline: 'none',
})

/** A choice in a picker list; its secondary text is marked data-detail. */
export const pickerOption = css({
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  gap: '0.75rem',
  paddingX: '0.625rem',
  paddingY: '0.5rem',
  borderRadius: '8px',
  color: 'foreground',
  fontSize: '0.8125rem',
  lineHeight: '1.25rem',
  whiteSpace: 'nowrap',
  cursor: 'pointer',
  outline: 'none',
  '& [data-detail]': { color: 'muted-foreground', fontSize: '0.75rem' },
  '&[data-hovered], &[data-focused]': { backgroundColor: 'muted' },
  '&[data-selected]': {
    backgroundColor: 'accent',
    color: 'primary',
    fontWeight: 500,
    '& [data-detail]': { color: 'primary' },
  },
})

export const pickerError = css({
  marginTop: '0.25rem',
  color: 'destructive',
  fontSize: '0.75rem',
  lineHeight: '1rem',
})
