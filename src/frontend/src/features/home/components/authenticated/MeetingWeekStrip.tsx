import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronLeftIcon, ChevronRightIcon, CalendarIcon } from '@/icons'
import { Button } from '@/primitives'
import { css } from '@/styled-system/css'
import {
  addCalendarDays,
  formatCalendarDate,
  isSameCalendarDay,
  startOfMondayWeek,
} from '../../utils/authenticatedHomeDate'

type MeetingWeekStripProps = {
  selectedDate: Date
  today: Date
  onSelectDate: (date: Date) => void
}

export const MeetingWeekStrip = ({
  selectedDate,
  today,
  onSelectDate,
}: MeetingWeekStripProps) => {
  const { t, i18n } = useTranslation('home')
  const locale = i18n.resolvedLanguage || i18n.language || 'fr'
  const selectedButtonRef = useRef<HTMLButtonElement>(null)
  const focusSelectedDayRef = useRef(false)
  const isTodaySelected = isSameCalendarDay(selectedDate, today)
  const weekStart = startOfMondayWeek(selectedDate)
  const days = Array.from({ length: 7 }, (_, index) =>
    addCalendarDays(weekStart, index)
  )

  const changeWeek = (offset: number) =>
    onSelectDate(addCalendarDays(selectedDate, offset * 7))

  useEffect(() => {
    selectedButtonRef.current?.scrollIntoView?.({
      block: 'nearest',
      inline: 'center',
    })
    // After "Aujourd’hui", keyboard focus follows to today's day button
    // instead of being lost with the button that just disappeared.
    if (focusSelectedDayRef.current) {
      focusSelectedDayRef.current = false
      selectedButtonRef.current?.focus()
    }
  }, [selectedDate])

  const goToToday = () => {
    focusSelectedDayRef.current = true
    onSelectDate(today)
  }

  return (
    <section
      aria-label={t('dashboard.calendar.label')}
      className={css({
        display: 'grid',
        gridTemplateColumns: { base: '1fr', lg: 'minmax(15rem, 1fr) auto' },
        alignItems: 'center',
        gap: { base: '0.75rem', lg: '1.25rem' },
        padding: { base: '0.625rem 1rem', md: '0.75rem 1rem' },
        borderBottomWidth: '1px',
        borderBottomStyle: 'solid',
        borderBottomColor: 'border',
      })}
    >
      <div
        className={css({
          display: 'flex',
          alignItems: 'center',
          gap: '0.375rem',
          minHeight: { base: '36px', md: '32px' },
          color: 'var(--workspace-ink-soft, var(--foreground))',
          fontSize: '0.875rem',
          lineHeight: '1.25rem',
          fontWeight: 500,
        })}
      >
        <CalendarIcon size={16} aria-hidden="true" />
        <span>
          {formatCalendarDate(selectedDate, locale, {
            weekday: 'short',
            day: 'numeric',
            month: 'short',
          })}
        </span>
        {!isTodaySelected && (
          <Button
            size="sm"
            variant="outline"
            onPress={goToToday}
            className={css({
              minHeight: { base: '36px', md: '30px' },
              marginLeft: '0.5rem',
              paddingX: '0.875rem',
              borderRadius: '999px',
              color: 'foreground',
              fontWeight: 500,
            })}
          >
            {t('dashboard.calendar.today')}
          </Button>
        )}
      </div>

      <div
        className={css({
          display: 'flex',
          alignItems: 'center',
          minWidth: 0,
          width: '100%',
        })}
      >
        <Button
          size="icon"
          variant="ghost"
          aria-label={t('dashboard.calendar.previousWeek')}
          tooltip={t('dashboard.calendar.previousWeek')}
          onPress={() => changeWeek(-1)}
          className={css({
            width: { base: '44px', md: '36px' },
            height: { base: '44px', md: '36px' },
            minWidth: { base: '44px', md: '36px' },
            minHeight: { base: '44px', md: '36px' },
            paddingX: '0!',
          })}
        >
          <ChevronLeftIcon aria-hidden="true" />
        </Button>
        <div
          className={css({
            minWidth: 0,
            flex: 1,
            overflowX: { base: 'auto', md: 'visible' },
            overscrollBehaviorX: 'contain',
          })}
        >
          <div
            className={css({
              display: 'grid',
              gridTemplateColumns: {
                base: 'repeat(7, 44px)',
                md: 'repeat(7, minmax(2.5rem, 1fr))',
              },
              gap: { base: '0.25rem', md: '0.5rem' },
              minWidth: { base: '332px', md: 0 },
            })}
          >
            {days.map((day) => {
              const selected = isSameCalendarDay(day, selectedDate)
              const current = isSameCalendarDay(day, today)
              return (
                <button
                  type="button"
                  ref={selected ? selectedButtonRef : undefined}
                  key={day.toISOString()}
                  aria-pressed={selected}
                  aria-current={current ? 'date' : undefined}
                  aria-label={formatCalendarDate(day, locale, {
                    weekday: 'long',
                    day: 'numeric',
                    month: 'long',
                  })}
                  onClick={() => onSelectDate(day)}
                  className={css({
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '0.25rem',
                    minHeight: { base: '48px', md: '46px' },
                    border: 0,
                    borderRadius: 'control',
                    backgroundColor: selected ? 'primary' : 'transparent',
                    color: selected ? 'primary-foreground' : 'foreground',
                    cursor: 'pointer',
                    _hover: {
                      backgroundColor: selected ? 'primary.hover' : 'muted',
                    },
                    _focusVisible: {
                      outline: '2px solid',
                      outlineColor: 'ring',
                      outlineOffset: '2px',
                    },
                  })}
                >
                  <span
                    className={css({
                      color:
                        current && !selected
                          ? 'primary'
                          : selected
                            ? 'primary-foreground'
                            : 'muted-foreground',
                      fontSize: '0.6875rem',
                      lineHeight: 1,
                      fontWeight: 500,
                      letterSpacing: '0.04em',
                      textTransform: 'uppercase',
                    })}
                  >
                    {formatCalendarDate(day, locale, { weekday: 'short' })}
                  </span>
                  <span
                    className={css({
                      fontSize: '0.8125rem',
                      lineHeight: 1,
                      fontWeight: 500,
                    })}
                  >
                    {formatCalendarDate(day, locale, { day: 'numeric' })}
                  </span>
                </button>
              )
            })}
          </div>
        </div>
        <Button
          size="icon"
          variant="ghost"
          aria-label={t('dashboard.calendar.nextWeek')}
          tooltip={t('dashboard.calendar.nextWeek')}
          onPress={() => changeWeek(1)}
          className={css({
            width: { base: '44px', md: '36px' },
            height: { base: '44px', md: '36px' },
            minWidth: { base: '44px', md: '36px' },
            minHeight: { base: '44px', md: '36px' },
            paddingX: '0!',
          })}
        >
          <ChevronRightIcon aria-hidden="true" />
        </Button>
      </div>
    </section>
  )
}
