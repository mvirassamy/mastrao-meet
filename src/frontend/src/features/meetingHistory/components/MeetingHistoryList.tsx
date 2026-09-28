import { FALLBACK_LANGUAGE } from '@/i18n/languageDetection'
import { useEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'wouter'
import { ChevronRightIcon, RetryIcon } from '@/icons'
import { CreateMeetingMenu } from '@/features/home/components/CreateMeetingMenu'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import type { MeetingHistoryItem } from '../api/types'
import { useMeetingHistory } from '../api/useMeetingHistory'
import {
  isAuthRequiredError,
  useLoginRedirectOnAuthError,
} from '../api/authRedirect'
import { meetingHistoryDetailPath } from '../paths'
import {
  consumeOpenedMeeting,
  rememberOpenedMeeting,
} from '../utils/focusReturn'
import {
  formatMeetingDuration,
  formatMeetingShortDay,
  formatMeetingTimeRange,
  formatMonthLabel,
  monthKey,
} from '../utils/meetingHistoryFormat'
import { MeetingContentStatusBadge } from './MeetingContentStatusBadge'
import { MeetingHistoryStatePanel } from './MeetingHistoryStatePanel'
import { MeetingHistorySkeleton } from './MeetingHistorySkeleton'

type MonthGroup = { key: string; label: string; items: MeetingHistoryItem[] }

const groupByMonth = (
  items: MeetingHistoryItem[],
  locale: string,
  timeZone?: string
) =>
  items.reduce<MonthGroup[]>((groups, item) => {
    const key = monthKey(item.startedAt, timeZone)
    const current = groups[groups.length - 1]
    if (current?.key === key) current.items.push(item)
    else
      groups.push({
        key,
        label: formatMonthLabel(item.startedAt, locale, timeZone),
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

  const items = useMemo(() => {
    const seen = new Set<string>()
    return (query.data?.pages ?? [])
      .flatMap((page) => page.items)
      .filter((item) => !seen.has(item.id) && Boolean(seen.add(item.id)))
      .sort((a, b) => b.startedAt.getTime() - a.startedAt.getTime())
  }, [query.data])
  const groups = useMemo(
    () => groupByMonth(items, locale, timeZone),
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
                  marginBottom: '0.625rem',
                  color: 'foreground',
                  fontSize: '0.9375rem',
                  lineHeight: '1.375rem',
                  fontWeight: 600,
                  letterSpacing: '-0.01em',
                })}
              >
                {group.label}
              </h2>
              <ul
                className={css({
                  margin: 0,
                  padding: 0,
                  listStyle: 'none',
                  border: '1px solid token(colors.border)',
                  borderRadius: '12px',
                  backgroundColor: 'card',
                  overflow: 'hidden',
                })}
              >
                {group.items.map((item) => (
                  <MeetingHistoryRow
                    key={item.id}
                    item={item}
                    locale={locale}
                    timeZone={timeZone}
                  />
                ))}
              </ul>
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

const MeetingHistoryRow = ({
  item,
  locale,
  timeZone,
}: {
  item: MeetingHistoryItem
  locale: string
  timeZone?: string
}) => {
  const { t } = useTranslation('meetingHistory')
  const duration = formatMeetingDuration(item.startedAt, item.endedAt, locale)
  const meta = [
    formatMeetingShortDay(item.startedAt, locale, timeZone),
    formatMeetingTimeRange(item.startedAt, item.endedAt, locale, timeZone),
    duration,
    item.participantCount !== null
      ? t('participants', { count: item.participantCount })
      : null,
  ].filter(Boolean)

  return (
    <li
      className={css({
        position: 'relative',
        display: 'grid',
        gridTemplateColumns: {
          base: 'minmax(0, 1fr) auto',
          md: 'minmax(0, 1fr) auto auto',
        },
        alignItems: 'center',
        columnGap: '0.75rem',
        rowGap: '0.5rem',
        padding: {
          base: '0.875rem 0.75rem 0.875rem 1rem',
          md: '0.875rem 1rem',
        },
        transition: 'background 150ms',
        '&:not(:last-child)': {
          borderBottom: '1px solid token(colors.border)',
        },
        _hover: {
          backgroundColor: 'var(--workspace-paper, token(colors.muted))',
        },
      })}
    >
      <div className={css({ minWidth: 0 })}>
        <Link
          to={meetingHistoryDetailPath(item.id)}
          data-meeting-id={item.id}
          onClick={() => rememberOpenedMeeting(item.id)}
          className={css({
            display: 'block',
            overflow: 'hidden',
            color: 'foreground',
            fontSize: '0.9375rem',
            lineHeight: '1.375rem',
            fontWeight: 500,
            textDecoration: 'none',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            outline: 'none',
            _after: {
              content: '""',
              position: 'absolute',
              inset: 0,
            },
            '&:focus-visible::after': {
              outline: '2px solid token(colors.ring)',
              outlineOffset: '-2px',
              borderRadius: '11px',
            },
          })}
        >
          {item.title ?? t('untitled')}
        </Link>
        <p
          className={css({
            marginTop: '0.125rem',
            marginBottom: 0,
            color: 'muted-foreground',
            fontSize: '0.8125rem',
            lineHeight: '1.25rem',
          })}
        >
          {meta.join(' · ')}
        </p>
      </div>
      <div
        className={css({
          gridColumn: { base: '1 / -1', md: 'auto' },
          gridRow: { base: 2, md: 'auto' },
          display: 'flex',
          flexWrap: 'wrap',
          gap: '0.375rem',
        })}
      >
        <MeetingContentStatusBadge kind="summary" status={item.summaryStatus} />
        <MeetingContentStatusBadge
          kind="transcript"
          status={item.transcriptStatus}
        />
      </div>
      <ChevronRightIcon
        size={18}
        aria-hidden="true"
        className={css({
          gridColumn: { base: 2, md: 'auto' },
          gridRow: { base: 1, md: 'auto' },
          color: 'muted-foreground',
        })}
      />
    </li>
  )
}
