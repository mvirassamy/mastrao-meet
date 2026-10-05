import { useId, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog, Field, Form, Text } from '@/primitives'
import { AppInput } from '@/primitives/AppInput'
import { css } from '@/styled-system/css'
import {
  validateMeetingSchedule,
  type MeetingSchedule,
  type MeetingScheduleDraft,
  type ScheduleErrors,
} from '../utils/meetingSchedule'

const ScheduleInput = ({
  label,
  name,
  type,
  value,
  error,
  onChange,
  isDisabled,
}: {
  label: string
  name: string
  type: 'date' | 'time'
  value: string
  error?: string
  onChange: (value: string) => void
  isDisabled: boolean
}) => {
  const id = useId()
  const errorId = `${id}-error`
  return (
    <div className={css({ minWidth: 0 })}>
      <label
        htmlFor={id}
        className={css({ display: 'block', fontWeight: 500 })}
      >
        {label}
      </label>
      <AppInput
        id={id}
        name={name}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required
        disabled={isDisabled}
        data-invalid={error ? true : undefined}
        aria-invalid={!!error}
        aria-describedby={error ? errorId : undefined}
      />
      {error && (
        <Text
          id={errorId}
          variant="sm"
          className={css({ color: 'destructive' })}
        >
          {error}
        </Text>
      )}
    </div>
  )
}

export const ScheduleMeetingDialog = ({
  isOpen,
  onClose,
  onCreate,
}: {
  isOpen: boolean
  onClose: () => void
  onCreate: (schedule: MeetingSchedule) => Promise<void>
}) => {
  const { t } = useTranslation('home', { keyPrefix: 'scheduleMeetingDialog' })
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone
  const [draft, setDraft] = useState<MeetingScheduleDraft>({
    title: '',
    date: '',
    startTime: '',
    endTime: '',
  })
  const [errors, setErrors] = useState<ScheduleErrors>({})
  const [failed, setFailed] = useState(false)
  const [pending, setPending] = useState(false)
  const submitting = useRef(false)

  const change = (name: keyof MeetingScheduleDraft, value: string) => {
    setDraft((current) => ({ ...current, [name]: value }))
    setErrors({})
    setFailed(false)
  }
  const close = () => {
    if (!submitting.current) onClose()
  }
  const create = async (
    _data: Record<string, FormDataEntryValue>,
    event: FormEvent<HTMLFormElement>
  ) => {
    if (submitting.current) return
    const result = validateMeetingSchedule(draft)
    setErrors(result.errors)
    if (!('schedule' in result)) {
      const name = Object.keys(result.errors)[0]
      const input = event.currentTarget.elements.namedItem(name)
      if (input instanceof HTMLElement) input.focus()
      return
    }
    submitting.current = true
    setPending(true)
    setFailed(false)
    try {
      await onCreate(result.schedule)
    } catch {
      setFailed(true)
    } finally {
      submitting.current = false
      setPending(false)
    }
  }

  return (
    <Dialog
      isOpen={isOpen}
      appearance="app"
      title={t('heading')}
      onOpenChange={(open) => {
        if (!open) close()
      }}
    >
      <Form
        onSubmit={create}
        validationBehavior="aria"
        submitLabel={t('create')}
        submitButtonProps={{ loading: pending, isDisabled: pending }}
        cancelButtonProps={{ isDisabled: pending }}
        onCancelButtonPress={close}
      >
        <Field
          type="text"
          name="title"
          label={t('title')}
          value={draft.title}
          onChange={(value) => change('title', value)}
          isDisabled={pending}
        />
        <div className={css({ display: 'grid', gap: '1rem' })}>
          <ScheduleInput
            name="date"
            type="date"
            label={t('date')}
            value={draft.date}
            error={errors.date ? t(`errors.${errors.date}`) : undefined}
            onChange={(value) => change('date', value)}
            isDisabled={pending}
          />
          <div
            className={css({
              display: 'grid',
              gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)',
              gap: '1rem',
            })}
          >
            <ScheduleInput
              name="startTime"
              type="time"
              label={t('startTime')}
              value={draft.startTime}
              error={
                errors.startTime ? t(`errors.${errors.startTime}`) : undefined
              }
              onChange={(value) => change('startTime', value)}
              isDisabled={pending}
            />
            <ScheduleInput
              name="endTime"
              type="time"
              label={t('endTime')}
              value={draft.endTime}
              error={errors.endTime ? t(`errors.${errors.endTime}`) : undefined}
              onChange={(value) => change('endTime', value)}
              isDisabled={pending}
            />
          </div>
          <Text variant="sm" className={css({ color: 'muted-foreground' })}>
            {t('timeZone', { timeZone })}
          </Text>
          <Text variant="sm" className={css({ color: 'muted-foreground' })}>
            {t('access')}
          </Text>
          {failed && <Text role="alert">{t('error')}</Text>}
        </div>
      </Form>
    </Dialog>
  )
}
