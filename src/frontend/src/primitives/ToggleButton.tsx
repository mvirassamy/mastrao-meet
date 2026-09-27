import { forwardRef, ReactNode } from 'react'
import {
  ToggleButton as RACToggleButton,
  ToggleButtonProps as RACToggleButtonProps,
} from 'react-aria-components'
import { type ButtonRecipeProps, buttonRecipe } from './buttonRecipe'
import { TooltipWrapper, type TooltipWrapperProps } from './TooltipWrapper'
import { callControlAttribute } from './callControl'

export type ToggleButtonProps = RACToggleButtonProps &
  ButtonRecipeProps &
  TooltipWrapperProps & {
    // Use tooltip as description below the button.
    description?: boolean
  }

/**
 * React aria ToggleButton with our button styles, that can take a tooltip if needed
 */
export const ToggleButton = forwardRef<HTMLButtonElement, ToggleButtonProps>(
  ({ tooltip, tooltipType, ...props }, ref) => {
    const [variantProps, componentProps] = buttonRecipe.splitVariantProps(props)

    return (
      <TooltipWrapper tooltip={tooltip} tooltipType={tooltipType}>
        <RACToggleButton
          ref={ref}
          {...componentProps}
          className={[buttonRecipe(variantProps), props.className].join(' ')}
          data-call-control={callControlAttribute(variantProps)}
        >
          <>
            {componentProps.children as ReactNode}
            {props.description && <span>{tooltip}</span>}
          </>
        </RACToggleButton>
      </TooltipWrapper>
    )
  }
)

ToggleButton.displayName = 'ToggleButton'
