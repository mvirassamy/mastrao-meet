import { type ReactNode } from 'react'
import { styled, VisuallyHidden } from '@/styled-system/jsx'
import { RemixiconComponentType, RiArrowDropDownFill } from '@remixicon/react'
import {
  Button,
  ListBox,
  ListBoxItem,
  Select as RACSelect,
  SelectProps as RACSelectProps,
  SelectValue,
} from 'react-aria-components'
import { useTranslation } from 'react-i18next'
import { Box } from './Box'
import { StyledPopover } from './StyledPopover'
import { menuRecipe } from '@/primitives/menuRecipe.ts'
import { css } from '@/styled-system/css'
import type { Placement } from '@react-types/overlays'

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
