import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Button as RACButton,
  Calendar,
  CalendarCell,
  CalendarGrid,
  CalendarGridBody,
  CalendarGridHeader,
  CalendarHeaderCell,
  Dialog,
  DialogTrigger,
  Heading,
  ListBox,
  ListBoxItem,
  Popover,
  useLocale,
  type Selection,
} from 'react-aria-components'
import {
  type CalendarDate,
  getLocalTimeZone,
  parseDate,
  today,
} from '@internationalized/date'
import { ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon } from '@/icons'
import { css } from '@/styled-system/css'
import { Button } from './Button'
import { type DateShortcut, dateShortcuts } from './dateShortcuts'
import {
  pickerError,
  pickerLabel,
  pickerOption,
  pickerPopover,
  pickerShell,
} from './pickerField'

const parseDateValue = (value?: string) => {
  if (!value) return null
  try {
    return parseDate(value)
  } catch {
    return null
  }
}

/** Formats a calendar day without any time zone shift. */
const formatDay = (
  date: CalendarDate,
  locale: string,
  options: Intl.DateTimeFormatOptions
) =>
  new Intl.DateTimeFormat(locale, { ...options, timeZone: 'UTC' }).format(
    date.toDate('UTC')
  )

const calendarDay = css({
  display: 'grid',
  placeItems: 'center',
  width: '36px',
  height: '36px',
  borderRadius: 'full',
  color: 'foreground',
  fontSize: '0.8125rem',
  cursor: 'pointer',
  outline: 'none',
  '&[data-outside-month]': { color: 'muted-foreground', opacity: 0.5 },
  '&[data-hovered]': { backgroundColor: 'muted' },
  '&[data-today]': {
    boxShadow: 'inset 0 0 0 1.5px token(colors.primary)',
    color: 'primary',
    fontWeight: 600,
  },
  '&[data-selected]': {
    boxShadow: 'none',
    backgroundColor: 'primary',
    color: 'primary-foreground',
    fontWeight: 600,
  },
  '&[data-disabled], &[data-unavailable]': {
    color: 'muted-foreground',
    opacity: 0.45,
    cursor: 'default',
    backgroundColor: 'transparent',
  },
  '&[data-focus-visible]': {
    outline: '2px solid token(colors.ring)',
    outlineOffset: '2px',
  },
})

const DateShortcutList = ({
  selected,
  minDate,
  onChoose,
}: {
  selected: CalendarDate | null
  minDate?: CalendarDate
  onChoose: (date: CalendarDate) => void
}) => {
  const { t } = useTranslation()
  const { locale } = useLocale()
  const shortcuts = dateShortcuts(today(getLocalTimeZone()), minDate)
  const current = shortcuts.find(
    ({ date }) => selected && date.compare(selected) === 0
  )
  const choose = (keys: Selection) => {
    const shortcut = shortcuts.find(({ id }) => keys !== 'all' && keys.has(id))
    if (shortcut) onChoose(shortcut.date)
  }

  return (
    <ListBox
      aria-label={t('datePicker.shortcuts.label')}
      items={shortcuts}
      selectionMode="single"
      disallowEmptySelection
      selectedKeys={current ? [current.id] : []}
      onSelectionChange={choose}
      className={css({
        display: { base: 'none', sm: 'grid' },
        alignContent: 'start',
        gap: '2px',
        minWidth: '11.5rem',
        marginRight: '0.5rem',
        paddingRight: '0.5rem',
        borderRight: '1px solid token(colors.border)',
        outline: 'none',
      })}
    >
      {(shortcut: DateShortcut) => (
        <ListBoxItem
          id={shortcut.id}
          textValue={t(`datePicker.shortcuts.${shortcut.id}`)}
          className={pickerOption}
        >
          {t(`datePicker.shortcuts.${shortcut.id}`)}
          <span data-detail>
            {formatDay(shortcut.date, locale, {
              weekday: 'short',
              day: 'numeric',
            })}
          </span>
        </ListBoxItem>
      )}
    </ListBox>
  )
}

const MonthCalendar = ({
  selected,
  minDate,
  onChoose,
}: {
  selected: CalendarDate | null
  minDate?: CalendarDate
  onChoose: (date: CalendarDate) => void
}) => (
  <Calendar
    value={selected}
    minValue={minDate}
    onChange={onChoose}
    className={css({ outline: 'none' })}
  >
    <header
      className={css({
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '0.5rem',
        marginBottom: '0.25rem',
        paddingLeft: '0.375rem',
      })}
    >
      <Heading
        className={css({
          margin: 0,
          fontSize: '0.875rem',
          lineHeight: '1.25rem',
          fontWeight: 600,
          textTransform: 'capitalize',
        })}
      />
      <div className={css({ display: 'flex', gap: '2px' })}>
        <Button slot="previous" variant="ghost" size="icon">
          <ChevronLeftIcon size={16} aria-hidden="true" />
        </Button>
        <Button slot="next" variant="ghost" size="icon">
          <ChevronRightIcon size={16} aria-hidden="true" />
        </Button>
      </div>
    </header>
    <CalendarGrid className={css({ borderCollapse: 'collapse' })}>
      <CalendarGridHeader>
        {(day) => (
          <CalendarHeaderCell
            className={css({
              height: '28px',
              color: 'muted-foreground',
              fontSize: '0.6875rem',
              fontWeight: 500,
            })}
          >
            {day}
          </CalendarHeaderCell>
        )}
      </CalendarGridHeader>
      <CalendarGridBody>
        {(date) => <CalendarCell date={date} className={calendarDay} />}
      </CalendarGridBody>
    </CalendarGrid>
  </Calendar>
)

/**
 * Date field of the application: a button showing the chosen day, opening
 * a calendar with quick picks. The value is an ISO day ("2026-10-08") or "".
 */
export const DatePickerField = ({
  label,
  name,
  value,
  onChange,
  minDate,
  error,
  isDisabled,
}: {
  label: string
  name: string
  value: string
  onChange: (value: string) => void
  /** Earliest day that can be chosen, as an ISO day. */
  minDate?: string
  error?: string
  isDisabled?: boolean
}) => {
  const { t } = useTranslation()
  const { locale } = useLocale()
  const [isOpen, setIsOpen] = useState(false)
  const id = useId()
  const labelId = `${id}-label`
  const triggerId = `${id}-trigger`
  const errorId = `${id}-error`
  const selected = parseDateValue(value)
  const earliest = parseDateValue(minDate) ?? undefined

  const choose = (date: CalendarDate) => {
    onChange(date.toString())
    setIsOpen(false)
  }

  return (
    <div className={css({ minWidth: 0 })}>
      <span id={labelId} className={pickerLabel}>
        {label}
      </span>
      <DialogTrigger isOpen={isOpen} onOpenChange={setIsOpen}>
        <RACButton
          id={triggerId}
          name={name}
          isDisabled={isDisabled}
          aria-labelledby={`${labelId} ${triggerId}`}
          data-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          className={pickerShell}
        >
          <span
            data-empty={selected ? undefined : true}
            className={css({
              flex: 1,
              minWidth: 0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              '&[data-empty]': { color: 'muted-foreground' },
            })}
          >
            {selected
              ? formatDay(selected, locale, { dateStyle: 'full' })
              : t('datePicker.placeholder')}
          </span>
          <ChevronDownIcon
            size={16}
            aria-hidden="true"
            className={css({ color: 'muted-foreground' })}
          />
        </RACButton>
        <Popover placement="bottom start" className={pickerPopover}>
          <Dialog
            aria-label={label}
            className={css({ display: 'flex', outline: 'none' })}
          >
            <DateShortcutList
              selected={selected}
              minDate={earliest}
              onChoose={choose}
            />
            <MonthCalendar
              selected={selected}
              minDate={earliest}
              onChoose={choose}
            />
          </Dialog>
        </Popover>
      </DialogTrigger>
      {error && (
        <p id={errorId} className={pickerError}>
          {error}
        </p>
      )}
    </div>
  )
}
