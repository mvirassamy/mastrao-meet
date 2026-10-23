import { useTranslation } from 'react-i18next'
import { Button as RACButton, Menu, MenuItem } from 'react-aria-components'
import { DropdownIcon, ExternalLinkIcon, SwapIcon } from '@/icons'
import { Menu as MenuTrigger } from '@/primitives'
import { menuRecipe } from '@/primitives/menuRecipe'
import { css, cx } from '@/styled-system/css'

export const DOSSIER_ICON = '/assets/illustrations/dossier.webp'

const pill = css({
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.4375rem',
  minHeight: '30px',
  paddingInlineStart: '0.5625rem',
  paddingInlineEnd: '0.625rem',
  borderRadius: 'full',
  backgroundColor: 'var(--button-accent)',
  color: 'var(--heading-foreground)',
  fontSize: '0.8125rem',
  fontWeight: 500,
  overflowWrap: 'anywhere',
  '& svg': { width: '14px', height: '14px', color: 'muted-foreground' },
})

const pressablePill = css({
  cursor: 'pointer',
  _hover: {
    backgroundColor:
      'color-mix(in srgb, var(--button-accent), var(--primary) 8%)',
  },
  '&[data-focus-visible]': {
    outline: '2px solid',
    outlineColor: 'ring',
    outlineOffset: '2px',
  },
})

/** An unfiled meeting reads as a grey slot waiting for a dossier. */
const emptyPill = css({
  backgroundColor: 'var(--button-muted)',
  color: 'foreground',
  _hover: {
    backgroundColor:
      'color-mix(in srgb, var(--button-muted), var(--heading-foreground) 6%)',
  },
})

const menuItem = menuRecipe({ density: 'app', icon: true, variant: 'light' })

function DossierIcon() {
  return (
    <img src={DOSSIER_ICON} alt="" aria-hidden="true" width={16} height={16} />
  )
}

/** Filed: the dossier pill opens a menu to open the dossier or change it. */
export function FiledPill({
  name,
  accessibleName,
  matterUrl,
  canClassify,
  onClassify,
}: {
  name: string
  accessibleName: string
  matterUrl: string | null
  canClassify: boolean
  onClassify: () => void
}) {
  const { t } = useTranslation('meetingHistory')
  if (!matterUrl && !canClassify)
    return (
      <span className={pill}>
        <DossierIcon />
        {name}
      </span>
    )
  return (
    <MenuTrigger density="app">
      <RACButton
        aria-label={accessibleName}
        className={cx(pill, pressablePill)}
      >
        <DossierIcon />
        {name}
        <DropdownIcon aria-hidden="true" />
      </RACButton>
      <Menu className={menuItem.root}>
        {matterUrl && (
          <MenuItem
            className={menuItem.item}
            href={matterUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            <ExternalLinkIcon size={16} aria-hidden="true" />
            {t('classification.openMatter')}
          </MenuItem>
        )}
        {canClassify && (
          <MenuItem className={menuItem.item} onAction={onClassify}>
            <SwapIcon size={16} aria-hidden="true" />
            {t('classification.changeMatter')}
          </MenuItem>
        )}
      </Menu>
    </MenuTrigger>
  )
}

/** Not filed: a grey pill that starts filing, or says so when it cannot. */
export function UnfiledPill({
  canClassify,
  onClassify,
}: {
  canClassify: boolean
  onClassify: () => void
}) {
  const { t } = useTranslation('meetingHistory')
  if (!canClassify)
    return (
      <span className={cx(pill, emptyPill)}>
        <DossierIcon />
        {t('classification.unclassified')}
      </span>
    )
  return (
    <RACButton
      className={cx(pill, pressablePill, emptyPill)}
      onPress={onClassify}
    >
      <DossierIcon />
      {t('classification.title')}
    </RACButton>
  )
}
