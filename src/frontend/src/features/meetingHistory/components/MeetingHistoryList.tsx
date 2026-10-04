import { FALLBACK_LANGUAGE } from '@/i18n/languageDetection'
import { type ReactNode, useEffect, useMemo, useRef } from 'react'
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
  dayKey,
  formatDayLabel,
  formatMeetingDuration,
  formatMeetingTimeRange,
} from '../utils/meetingHistoryFormat'
import { MeetingContentStatusIcon } from './MeetingContentStatusIcon'
import { MeetingParticipantsStack } from './MeetingParticipantsStack'
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

  const items = useMemo(() => {
    const seen = new Set<string>()
    return (query.data?.pages ?? [])
      .flatMap((page) => page.items)
      .filter((item) => !seen.has(item.id) && Boolean(seen.add(item.id)))
      .sort((a, b) => b.startedAt.getTime() - a.startedAt.getTime())
  }, [query.data])
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

/**
 * Row cell drawn above the row link overlay so its tooltips can show. It only
 * forwards a mouse click to the row link; keyboard and screen reader users
 * reach the same link directly.
 */
const RowOverlayCell = ({
  onOpen,
  children,
}: {
  onOpen: () => void
  children: ReactNode
}) => (
  // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
  <div
    onClick={onOpen}
    className={css({
      position: 'relative',
      zIndex: 1,
      display: 'flex',
      gap: '0.625rem',
      cursor: 'pointer',
    })}
  >
    {children}
  </div>
)

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
  const linkRef = useRef<HTMLAnchorElement>(null)
  const duration = formatMeetingDuration(item.startedAt, item.endedAt, locale)
  const meta = [
    formatMeetingTimeRange(item.startedAt, item.endedAt, locale, timeZone),
    duration,
  ].filter(Boolean)

  return (
    <li
      className={css({
        position: 'relative',
        display: 'grid',
        gridTemplateColumns: {
          base: 'minmax(0, 1fr) auto auto auto',
          // The participants start in the middle of the row on wide screens.
          md: 'minmax(0, 1fr) minmax(5.5rem, 1fr) auto auto',
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
          ref={linkRef}
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
          {/* The status icons are decorative: their meaning is read here. */}
          <span className={css({ srOnly: true })}>
            {t(`status.summary.${item.summaryStatus}`)}
          </span>
          <span className={css({ srOnly: true })}>
            {t(`status.transcript.${item.transcriptStatus}`)}
          </span>
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
      <RowOverlayCell onOpen={() => linkRef.current?.click()}>
        <MeetingParticipantsStack
          count={item.participantCount}
          names={item.participantNames}
        />
      </RowOverlayCell>
      <RowOverlayCell onOpen={() => linkRef.current?.click()}>
        <MeetingContentStatusIcon kind="summary" status={item.summaryStatus} />
        <MeetingContentStatusIcon
          kind="transcript"
          status={item.transcriptStatus}
        />
      </RowOverlayCell>
      <ChevronRightIcon
        size={18}
        aria-hidden="true"
        className={css({
          color: 'muted-foreground',
        })}
      />
    </li>
  )
}
