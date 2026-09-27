import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import { CreateMeetingMenu } from '../CreateMeetingMenu'
import { MeetingJoinField } from './MeetingJoinField'

/**
 * Join and create actions shared by every authenticated workspace page.
 * Desktop: the join field sits in the middle of the header, "Nouveau" on
 * the right. Mobile: "Nouveau" first, then the full-width join field.
 */
export const MeetWorkspaceToolbar = () => {
  const { t } = useTranslation('home')

  return (
    <div
      className={css({
        display: 'flex',
        minWidth: 0,
        flex: { base: '0 0 100%', md: 1 },
        flexWrap: 'wrap',
        alignItems: 'flex-start',
        justifyContent: 'flex-end',
        gap: '0.5rem',
        md: {
          display: 'grid',
          gridTemplateColumns: '1fr auto 1fr',
          alignItems: 'center',
          columnGap: '1rem',
        },
      })}
    >
      <div
        className={css({
          order: { base: 2, md: 1 },
          width: { base: '100%', md: 'auto' },
          gridColumn: { md: 2 },
          minWidth: 0,
        })}
      >
        <MeetingJoinField />
      </div>
      <div
        className={css({
          order: 1,
          display: 'flex',
          alignItems: 'center',
          width: { base: 'auto', md: 'auto' },
          gridColumn: { md: 3 },
          justifySelf: { md: 'end' },
        })}
      >
        <CreateMeetingMenu
          label={t('dashboard.newMeeting')}
          showIcon
          buttonProps={{
            size: 'default',
            className: css({
              minHeight: { base: '44px', md: '38px' },
            }),
          }}
        />
      </div>
    </div>
  )
}
