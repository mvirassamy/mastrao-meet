import { FALLBACK_LANGUAGE } from '@/i18n/languageDetection'
import { useEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { RetryIcon } from '@/icons'
import { CreateMeetingMenu } from '@/features/home/components/CreateMeetingMenu'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import type { MeetingHistoryItem } from '../api/types'
import { historyItemsOf } from '../api/historyItems'
import { useMeetingHistory } from '../api/useMeetingHistory'
import {
  isAuthRequiredError,
  useLoginRedirectOnAuthError,
} from '../api/authRedirect'
import { consumeOpenedMeeting } from '../utils/focusReturn'
import { dayKey, formatDayLabel } from '../utils/meetingHistoryFormat'
import { MeetingHistoryRows } from './MeetingHistoryRows'
import { MeetingHistoryStatePanel } from './MeetingHistoryStatePanel'
import { MeetingHistorySkeleton } from './MeetingHistorySkeleton'

type DayGroup = { key: string; label: string; items: MeetingHistoryItem[] }

/** Groups the meetings, already sorted most recent first, by calendar day. */
const groupByDay = (
  items: MeetingHistoryItem[],
  locale: string,
  timeZone?: string
) =>
  items.reduce<DayGroup[]>((groups, item) => {
    const key = dayKey(item.startedAt, timeZone)
    const current = groups[groups.length - 1]
    if (current?.key === key) current.items.push(item)
    else
      groups.push({
        key,
        label: formatDayLabel(item.startedAt, locale, timeZone),
        items: [item],
      })
    return groups
  }, [])

export const MeetingHistoryList = ({ timeZone }: { timeZone?: string }) => {
  const { t, i18n } = useTranslation(['meetingHistory', 'home'])
  const locale = i18n.resolvedLanguage || i18n.language || FALLBACK_LANGUAGE
  const query = useMeetingHistory()
  useLoginRedirectOnAuthError(query.error)
  const listRef = useRef<HTMLDivElement>(null)

  const items = useMemo(() => historyItemsOf(query.data?.pages), [query.data])
  const groups = useMemo(
    () => groupByDay(items, locale, timeZone),
    [items, locale, timeZone]
  )

  const hasItems = items.length > 0
  useEffect(() => {
    if (!hasItems) return
    const openedId = consumeOpenedMeeting()
    if (!openedId) return
    const link = Array.from(
      listRef.current?.querySelectorAll<HTMLAnchorElement>(
        'a[data-meeting-id]'
      ) ?? []
    ).find((element) => element.dataset.meetingId === openedId)
    link?.focus()
  }, [hasItems])

  return (
    <div
      className={css({
        width: '100%',
        maxWidth: '56rem',
        marginX: 'auto',
        padding: { base: '1.25rem 1rem 2rem', md: '2rem 2rem 3rem' },
      })}
    >
      <header className={css({ marginBottom: '1.25rem' })}>
        <h1
          className={css({
            margin: 0,
            fontSize: { base: '1.375rem', md: '1.5rem' },
            lineHeight: 1.25,
            fontWeight: 600,
            letterSpacing: '-0.025em',
          })}
        >
          {t('title')}
        </h1>
        <p
          className={css({
            marginTop: '0.375rem',
            marginBottom: 0,
            color: 'muted-foreground',
            fontSize: '0.875rem',
            lineHeight: '1.25rem',
          })}
        >
          {t('description')}
        </p>
      </header>

      {query.isPending || isAuthRequiredError(query.error) ? (
        <MeetingHistorySkeleton label={t('loading')} />
      ) : !query.data ? (
        <MeetingHistoryStatePanel
          role="alert"
          illustration="/assets/illustrations/historique-erreur.webp"
          title={t('error.title')}
          description={t('error.description')}
          action={
            <Button
              variant="secondary"
              icon={<RetryIcon aria-hidden="true" />}
              onPress={() => void query.refetch()}
            >
              {t('error.retry')}
            </Button>
          }
        />
      ) : !hasItems ? (
        <MeetingHistoryStatePanel
          illustration="/assets/illustrations/historique-empty.webp"
          title={t('empty.title')}
          description={t('empty.description')}
          action={
            <CreateMeetingMenu
              label={t('dashboard.newMeeting', { ns: 'home' })}
              showIcon
              buttonProps={{
                size: 'default',
                className: css({
                  minHeight: { base: '44px', md: '38px' },
                  paddingX: '1rem',
                }),
              }}
            />
          }
        />
      ) : (
        <div ref={listRef}>
          {groups.map((group) => (
            <section
              key={group.key}
              aria-labelledby={`meeting-history-${group.key}`}
              className={css({ '& + &': { marginTop: '1.5rem' } })}
            >
              <h2
                id={`meeting-history-${group.key}`}
                className={css({
                  margin: 0,
                  marginBottom: '0.5rem',
                  color: 'muted-foreground',
                  fontSize: '0.75rem',
                  lineHeight: '1rem',
                  fontWeight: 600,
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                })}
              >
                {group.label}
              </h2>
              <MeetingHistoryRows
                items={group.items}
                locale={locale}
                timeZone={timeZone}
              />
            </section>
          ))}

          {query.hasNextPage && (
            <div
              className={css({
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: '0.5rem',
                marginTop: '1.25rem',
              })}
            >
              {query.isFetchNextPageError && (
                <p
                  role="alert"
                  className={css({
                    margin: 0,
                    color: 'recording-foreground',
                    fontSize: '0.8125rem',
                  })}
                >
                  {t('loadMoreError')}
                </p>
              )}
              <Button
                variant="secondary"
                isDisabled={query.isFetchingNextPage}
                onPress={() => void query.fetchNextPage()}
              >
                {query.isFetchingNextPage
                  ? t('loadingMore')
                  : query.isFetchNextPageError
                    ? t('error.retry')
                    : t('loadMore')}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
