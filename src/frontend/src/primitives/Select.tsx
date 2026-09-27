import { type ReactNode } from 'react'
import { styled, VisuallyHidden } from '@/styled-system/jsx'
import {
  RemixiconComponentType,
  RiArrowDownSFill,
  RiArrowDropDownFill,
  RiCheckFill,
} from '@remixicon/react'
import {
  Button,
  ListBox,
  ListBoxItem,
  Popover,
  Select as RACSelect,
  SelectProps as RACSelectProps,
  SelectValue,
} from 'react-aria-components'
import { useTranslation } from 'react-i18next'
import { Box } from './Box'
import { StyledPopover } from './StyledPopover'
import { menuRecipe } from '@/primitives/menuRecipe.ts'
import { css, cx } from '@/styled-system/css'
import type { Placement } from '@react-types/overlays'
import { useAppAppearance } from './useAppAppearance'

const StyledButton = styled(Button, {
  base: {
    width: 'full',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingY: 0.125,
    paddingX: 0.25,
    border: '1px solid',
    borderColor: 'control.border',
    color: 'control.text',
    borderRadius: 'control',
    boxShadow: '0 1px 2px var(--shadow-color)',
    '&[data-focus-visible]': {
      outline: '2px solid {colors.focusRing}',
      outlineOffset: '-1px',
    },
    '&[data-pressed]': {
      backgroundColor: 'control.hover',
    },
    // fixme disabled style is being overridden by placeholder one and needs refinement.
    '&[data-disabled]': {
      color: 'default.subtle-text',
      borderColor: 'input',
      boxShadow: '0 1px 2px var(--shadow-soft)',
    },
  },
  variants: {
    variant: {
      light: { backgroundColor: 'card' },
      dark: {
        backgroundColor: 'card',
        fontWeight: 'medium !important',
        color: 'card-foreground',
        '&[data-pressed]': {
          backgroundColor: 'selected',
          color: 'selected-foreground',
        },
        '&[data-hovered]': {
          backgroundColor: 'accent',
          color: 'card-foreground',
        },
        '&[data-selected]': {
          backgroundColor: 'selected !important',
          color: 'selected-foreground !important',
        },
      },
    },
  },
  defaultVariants: {
    variant: 'light',
  },
})

const StyledSelectValue = styled(SelectValue, {
  base: {
    textOverflow: 'ellipsis',
    overflow: 'hidden',
    textWrap: 'nowrap',
    '&[data-placeholder]': {
      color: 'default.subtle-text',
      fontStyle: 'italic',
    },
  },
})

const StyledIcon = styled('div', {
  base: {
    marginRight: '0.35rem',
    flexShrink: 0,
  },
})

export type SelectProps<T> = Omit<
  RACSelectProps<object>,
  'items' | 'label' | 'errors'
> & {
  iconComponent?: RemixiconComponentType
  label: ReactNode
  items: Array<{ value: T; label: ReactNode }>
  errors?: ReactNode
  placement?: Placement
  variant?: 'light' | 'dark'
  menuFooter?: ReactNode
}

export const Select = <T extends string | number>({
  label,
  iconComponent,
  items,
  errors,
  placement,
  variant = 'light',
  menuFooter,
  ...props
}: SelectProps<T>) => {
  const IconComponent = iconComponent
  const { t } = useTranslation('global')
  const isApp = useAppAppearance()
  if (isApp)
    return (
      <AppSelect
        label={label}
        iconComponent={iconComponent}
        items={items}
        errors={errors}
        placement={placement}
        menuFooter={menuFooter}
        {...props}
      />
    )
  return (
    <RACSelect {...props}>
      {({ isOpen }) => (
        <>
          {label}
          <StyledButton variant={variant}>
            {!!IconComponent && (
              <StyledIcon>
                <IconComponent size={18} />
              </StyledIcon>
            )}
            <StyledSelectValue />
            <RiArrowDropDownFill
              aria-hidden="true"
              className={css({ flexShrink: 0 })}
            />
          </StyledButton>
          <StyledPopover placement={placement}>
            <Box size="sm" type="popover" variant={variant}>
              <ListBox>
                {items.map((item) => (
                  <ListBoxItem
                    className={
                      menuRecipe({
                        extraPadding: true,
                        variant: variant,
                      }).item
                    }
                    id={item.value}
                    key={item.value}
                    textValue={
                      typeof item.label === 'string' ? item.label : undefined
                    }
                  >
                    {({ isSelected }) => (
                      <>
                        {item.label}
                        {isSelected && (
                          <VisuallyHidden>, {t('selected')}</VisuallyHidden>
                        )}
                      </>
                    )}
                  </ListBoxItem>
                ))}
              </ListBox>
              {isOpen && menuFooter}
            </Box>
          </StyledPopover>
          {errors}
        </>
      )}
    </RACSelect>
  )
}

const appTrigger = css({
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
  cursor: 'pointer',
  outline: 'none',
  transition: 'border-color 150ms, box-shadow 150ms',
  '&[data-hovered]': { borderColor: 'input' },
  '&[data-focus-visible]': {
    borderColor: 'ring',
    boxShadow: '0 0 0 3px rgb(45 91 227 / 0.2)',
  },
  '[data-open] > &': {
    borderColor: 'ring',
    boxShadow: '0 0 0 3px rgb(45 91 227 / 0.2)',
    '& > .chevron': { transform: 'rotate(180deg)' },
  },
  '&[data-disabled]': { cursor: 'not-allowed', opacity: 0.5 },
  '& > .chevron': {
    flexShrink: 0,
    marginLeft: 'auto',
    color: 'muted-foreground',
    transition: 'transform 150ms',
  },
})

const appValue = css({
  minWidth: 0,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  // Nothing selected yet: a quiet, regular placeholder.
  '&[data-placeholder]': { color: 'muted-foreground' },
})

const appPopover = css({
  minWidth: 'var(--trigger-width)',
  maxHeight: '18rem',
  overflowY: 'auto',
  padding: '0.25rem',
  border: '1px solid token(colors.border)',
  borderRadius: '8px',
  backgroundColor: 'popover',
  color: 'popover-foreground',
  boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)',
  outline: 'none',
  transition: 'opacity 100ms ease, transform 100ms ease',
  '&[data-entering], &[data-exiting]': {
    opacity: 0,
    transform: 'scale(0.96)',
  },
  _motionReduce: { transition: 'none' },
})

const appItem = css({
  position: 'relative',
  display: 'flex',
  alignItems: 'center',
  minHeight: { base: '40px', md: '32px' },
  paddingY: '0.375rem',
  paddingLeft: '0.5rem',
  paddingRight: '2rem',
  borderRadius: '6px',
  fontSize: '0.875rem',
  lineHeight: '1.25rem',
  cursor: 'default',
  outline: 'none',
  '&[data-focused]': { backgroundColor: 'accent' },
  '&[data-selected]': { fontWeight: 500 },
  '&[data-disabled]': { opacity: 0.5 },
  '& > .check': {
    position: 'absolute',
    right: '0.5rem',
    color: 'primary',
  },
})

/** Mastrao application select, used inside the authenticated workspace. */
const AppSelect = <T extends string | number>({
  label,
  iconComponent: IconComponent,
  items,
  errors,
  placement,
  menuFooter,
  ...props
}: Omit<SelectProps<T>, 'variant'>) => {
  const { t } = useTranslation('global')
  return (
    <RACSelect {...props}>
      {({ isOpen }) => (
        <>
          {label}
          <Button className={appTrigger}>
            {!!IconComponent && (
              <IconComponent
                size={16}
                aria-hidden="true"
                className={css({ flexShrink: 0, color: 'muted-foreground' })}
              />
            )}
            <SelectValue className={appValue} />
            <RiArrowDownSFill
              size={16}
              aria-hidden="true"
              className="chevron"
            />
          </Button>
          <Popover
            placement={placement}
            offset={4}
            // Popovers render in a portal: carry the workspace palette along.
            className={cx('authenticated-meet-workspace', appPopover)}
          >
            <ListBox className={css({ outline: 'none' })}>
              {items.map((item) => (
                <ListBoxItem
                  className={appItem}
                  id={item.value}
                  key={item.value}
                  textValue={
                    typeof item.label === 'string' ? item.label : undefined
                  }
                >
                  {({ isSelected }) => (
                    <>
                      {item.label}
                      {isSelected && (
                        <>
                          <RiCheckFill
                            size={16}
                            aria-hidden="true"
                            className="check"
                          />
                          <VisuallyHidden>, {t('selected')}</VisuallyHidden>
                        </>
                      )}
                    </>
                  )}
                </ListBoxItem>
              ))}
            </ListBox>
            {isOpen && menuFooter}
          </Popover>
          {errors}
        </>
      )}
    </RACSelect>
  )
}
