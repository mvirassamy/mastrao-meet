import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import {
  Button as RACButton,
  ComboBox,
  Group,
  Input,
  Label,
  ListBox,
  ListBoxItem,
  Popover,
  Text,
} from 'react-aria-components'
import { ChevronDownIcon } from '@/icons'
import { css, cx } from '@/styled-system/css'
import {
  pickerError,
  pickerLabel,
  pickerOption,
  pickerPopover,
  pickerShell,
} from './pickerField'
import { normalizeTime, type TimeSlot, timeSlots } from './timeSlots'

// Where a full-day list opens when no time is chosen: the working morning.
const WORKDAY_START = '09:00'

const formatDuration = (minutes: number, t: TFunction) => {
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  if (hours === 0) return t('timeSelect.duration.minutes', { minutes })
  if (rest === 0) return t('timeSelect.duration.hours', { hours })
  return t('timeSelect.duration.hoursMinutes', {
    hours,
    minutes: String(rest).padStart(2, '0'),
  })
}

/** The slot shown first when the list opens. */
const openingSlot = (slots: TimeSlot[], selectedKey: string | null) => {
  if (selectedKey) return selectedKey
  const isFullDay = slots[0]?.duration === null
  return isFullDay ? WORKDAY_START : null
}

/**
 * Time field of the application: type a time or pick a quarter hour. With
 * `durationFrom` (an end time), the list starts after that time and shows
 * each duration. The value is "HH:MM", or the text being typed.
 */
export const TimeSelectField = ({
  label,
  name,
  value,
  onChange,
  durationFrom,
  error,
  isDisabled,
}: {
  label: string
  name: string
  value: string
  onChange: (value: string) => void
  durationFrom?: string
  error?: string
  isDisabled?: boolean
}) => {
  const { t } = useTranslation()
  const listRef = useRef<HTMLDivElement>(null)
  const slots = timeSlots(durationFrom)
  const chosen = slots.find(({ time }) => time === value)
  const selectedKey = chosen ? chosen.time : null

  const revealOpeningSlot = (isOpen: boolean) => {
    const slot = openingSlot(slots, selectedKey)
    if (!isOpen || !slot) return
    requestAnimationFrame(() =>
      listRef.current
        ?.querySelector(`[data-time="${slot}"]`)
        ?.scrollIntoView?.({ block: 'start' })
    )
  }

  return (
    <ComboBox
      items={slots}
      inputValue={value}
      onInputChange={onChange}
      selectedKey={selectedKey}
      onSelectionChange={(key) => {
        if (key !== null) onChange(String(key))
      }}
      onBlur={() => onChange(normalizeTime(value))}
      onOpenChange={revealOpeningSlot}
      allowsCustomValue
      menuTrigger="focus"
      isDisabled={isDisabled}
      isInvalid={!!error}
      className={css({ minWidth: 0 })}
    >
      <Label className={pickerLabel}>{label}</Label>
      <Group className={pickerShell}>
        <Input
          name={name}
          placeholder="--:--"
          className={css({
            flex: 1,
            minWidth: 0,
            border: 0,
            outline: 0,
            background: 'transparent',
            color: 'foreground',
            fontSize: '0.875rem',
            _placeholder: { color: 'muted-foreground' },
          })}
        />
        {chosen && chosen.duration !== null && (
          <span
            className={css({
              color: 'muted-foreground',
              fontSize: '0.75rem',
              whiteSpace: 'nowrap',
            })}
          >
            {formatDuration(chosen.duration, t)}
          </span>
        )}
        <RACButton
          aria-label={t('timeSelect.showSlots')}
          className={css({
            display: 'grid',
            placeItems: 'center',
            color: 'muted-foreground',
            cursor: 'pointer',
            outline: 'none',
          })}
        >
          <ChevronDownIcon size={16} aria-hidden="true" />
        </RACButton>
      </Group>
      {error && (
        <Text slot="errorMessage" className={pickerError}>
          {error}
        </Text>
      )}
      <Popover
        placement="bottom start"
        className={cx(
          pickerPopover,
          css({ width: 'var(--trigger-width)', padding: '0.25rem' })
        )}
      >
        <ListBox
          ref={listRef}
          aria-label={t('timeSelect.slots')}
          className={css({
            maxHeight: '15rem',
            overflowY: 'auto',
            outline: 'none',
          })}
        >
          {(slot: TimeSlot) => (
            <ListBoxItem
              id={slot.time}
              textValue={slot.time}
              data-time={slot.time}
              className={pickerOption}
            >
              {slot.time}
              {slot.duration !== null && (
                <span data-detail>{formatDuration(slot.duration, t)}</span>
              )}
            </ListBoxItem>
          )}
        </ListBox>
      </Popover>
    </ComboBox>
  )
}
