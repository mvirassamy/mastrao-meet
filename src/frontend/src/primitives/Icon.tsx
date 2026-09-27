import { HandRaisedFill } from '@/components/icons/HandRaisedFill'
import { cva, RecipeVariantProps } from '@/styled-system/css'
import { ComponentPropsWithoutRef } from 'react'

import {
  ChevronRightIcon,
  RecordIcon,
  SpeakIcon,
  TranslateIcon,
  ArticleIcon,
  LoginIcon,
  MailIcon,
  DownloadCloudIcon,
  VideoIcon,
  InformationIcon,
} from '@/icons'
// Explicit native solid glyphs; do not fill outline SVGs through CSS.
const icons = {
  chevron_forward: ChevronRightIcon,
  chevron_right: ChevronRightIcon,
  mode_standby: RecordIcon,
  speech_to_text: SpeakIcon,
  language: TranslateIcon,
  article: ArticleIcon,
  person_raised_hand: HandRaisedFill,
  login: LoginIcon,
  mail: MailIcon,
  cloud_download: DownloadCloudIcon,
  screen_record: VideoIcon,
  info: InformationIcon,
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
