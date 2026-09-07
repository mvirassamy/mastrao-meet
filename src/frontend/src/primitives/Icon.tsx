import { HandRaisedFill } from '@/components/icons/HandRaisedFill'
import { cva, RecipeVariantProps } from '@/styled-system/css'
import { ComponentPropsWithoutRef } from 'react'

import {
  RiArrowRightSFill,
  RiRecordCircleFill,
  RiSpeakFill,
  RiTranslate2,
  RiArticleFill,
  RiLoginBoxFill,
  RiMailFill,
  RiDownloadCloudFill,
  RiVideoFill,
  RiInformationFill,
} from '@remixicon/react'

// Explicit native solid glyphs; do not fill outline SVGs through CSS.
const icons = {
  chevron_forward: RiArrowRightSFill,
  chevron_right: RiArrowRightSFill,
  mode_standby: RiRecordCircleFill,
  speech_to_text: RiSpeakFill,
  language: RiTranslate2,
  article: RiArticleFill,
  person_raised_hand: HandRaisedFill,
  login: RiLoginBoxFill,
  mail: RiMailFill,
  cloud_download: RiDownloadCloudFill,
  screen_record: RiVideoFill,
  info: RiInformationFill,
}

export type IconName = keyof typeof icons

const iconRecipe = cva({
  base: {
    display: 'inline-block',
    flexShrink: 0,
    lineHeight: 1,
    color: 'currentColor',
  },
  variants: {
    size: {
      sm: { width: '18px', height: '18px' },
      md: { width: '24px', height: '24px' },
      lg: { width: '32px', height: '32px' },
      xl: { width: '40px', height: '40px' },
    },
  },
  defaultVariants: {
    size: 'md',
  },
})

export type IconRecipeProps = RecipeVariantProps<typeof iconRecipe>

export type IconProps = IconRecipeProps &
  Omit<ComponentPropsWithoutRef<'svg'>, 'name' | 'children'> & {
    name: IconName
  }

export const Icon = ({ name, ...props }: IconProps) => {
  const [variantProps, componentProps] = iconRecipe.splitVariantProps(props)
  const { className, ...rest } = componentProps

  const SvgIcon = icons[name]

  if (!SvgIcon) {
    if (import.meta.env.NODE_ENV !== 'production') {
      console.warn(
        `[Icon] Unknown icon name: "${name}". Available: ${Object.keys(icons).join(', ')}`
      )
    }
    return null
  }

  return (
    <SvgIcon
      aria-hidden="true"
      focusable="false"
      className={[iconRecipe(variantProps), className]
        .filter(Boolean)
        .join(' ')}
      {...rest}
    />
  )
}
