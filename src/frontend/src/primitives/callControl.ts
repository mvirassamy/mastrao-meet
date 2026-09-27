/** Variants whose dimensions and icon sizes belong to the call controls. */
const CALL_CONTROL_VARIANTS = new Set([
  'whiteCircle',
  'errorCircle',
  'bigSquare',
  'permission',
])

/**
 * Call controls keep their own icon size instead of the Platform
 * `[&_svg]:size-*` rule applied by the button sizes. Buttons showing their
 * tooltip as a caption (mobile call menu tiles) keep it too.
 */
export const callControlAttribute = ({
  shape,
  variant,
  description,
}: {
  shape?: string | null
  variant?: string | null
  description?: boolean | null
}) =>
  shape === 'circle' ||
  !!description ||
  (!!variant && CALL_CONTROL_VARIANTS.has(variant))
    ? ''
    : undefined
