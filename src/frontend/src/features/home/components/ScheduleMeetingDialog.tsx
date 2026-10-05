import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Form as RACForm } from 'react-aria-components'
import { getLocalTimeZone, today } from '@internationalized/date'
import {
  type AppIconComponent,
  CalendarIcon,
  FileTextIcon,
  InformationIcon,
  TimeIcon,
} from '@/icons'
import { Button, Field, Text } from '@/primitives'
import { AppDialog } from '@/primitives/AppDialog'
import { DatePickerField } from '@/primitives/DatePickerField'
import { TimeSelectField } from '@/primitives/TimeSelectField'
import { css } from '@/styled-system/css'
import {
  validateMeetingSchedule,
  type MeetingSchedule,
  type MeetingScheduleDraft,
  type ScheduleErrors,
} from '../utils/meetingSchedule'

/** "Europe/Paris" reads as "Paris". */
const timeZoneCity = (timeZone: string) =>
  (timeZone.split('/').pop() ?? timeZone).replaceAll('_', ' ')

/** One form row: a muted icon in the gutter, then the field(s). */
const FieldRow = ({
  Icon,
  children,
}: {
  Icon: AppIconComponent
  children: ReactNode
}) => (
  <div
    className={css({
      display: 'grid',
      gridTemplateColumns: '1.25rem minmax(0, 1fr)',
      columnGap: '0.75rem',
      alignItems: 'start',
    })}
  >
    <Icon
      size={18}
      aria-hidden="true"
      className={css({ marginTop: '2.125rem', color: 'muted-foreground' })}
    />
    {children}
  </div>
)

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
  const { t: tGlobal } = useTranslation()
  const formId = useId()
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
  const errorText = (name: keyof ScheduleErrors) => {
    const issue = errors[name]
    return issue ? t(`errors.${issue}`) : undefined
  }
  const close = () => {
    if (!submitting.current) onClose()
  }
  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting.current) return
    const result = validateMeetingSchedule(draft)
    setErrors(result.errors)
    if (!('schedule' in result)) {
      const name = Object.keys(result.errors)[0]
      const field = event.currentTarget.elements.namedItem(name)
      if (field instanceof HTMLElement) field.focus()
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
    <AppDialog
      isOpen={isOpen}
      size="md"
      title={t('heading')}
      description={t('subtitle')}
      illustration="/assets/illustrations/planifier-reunion.webp"
      onOpenChange={(open) => {
        if (!open) close()
      }}
      footer={
        <>
          <div
            className={css({
              display: 'flex',
              alignItems: 'center',
              gap: '0.25rem',
              marginRight: { sm: 'auto' },
              color: 'muted-foreground',
              fontSize: '0.8125rem',
            })}
          >
            <Button
              variant="ghost"
              size="icon"
              tooltip={t('access')}
              aria-label={t('access')}
            >
              <InformationIcon size={16} aria-hidden="true" />
            </Button>
            {t('timeZone', { timeZone: timeZoneCity(timeZone) })}
          </div>
          <Button variant="outline" onPress={close} isDisabled={pending}>
            {tGlobal('cancel')}
          </Button>
          <Button
            type="submit"
            form={formId}
            variant="default"
            loading={pending}
            isDisabled={pending}
          >
            {t('create')}
          </Button>
        </>
      }
    >
      <RACForm
        id={formId}
        onSubmit={create}
        validationBehavior="aria"
        className={css({ display: 'grid', gap: '1rem', paddingTop: '0.25rem' })}
      >
        <FieldRow Icon={FileTextIcon}>
          <Field
            type="text"
            name="title"
            label={t('title')}
            placeholder={t('titlePlaceholder')}
            value={draft.title}
            onChange={(value) => change('title', value)}
            isDisabled={pending}
            wrapperProps={{ noMargin: true }}
          />
        </FieldRow>
        <FieldRow Icon={CalendarIcon}>
          <DatePickerField
            name="date"
            label={t('date')}
            value={draft.date}
            minDate={today(getLocalTimeZone()).toString()}
            error={errorText('date')}
            onChange={(value) => change('date', value)}
            isDisabled={pending}
          />
        </FieldRow>
        <FieldRow Icon={TimeIcon}>
          <div
            className={css({
              display: 'grid',
              gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)',
              gap: '0.75rem',
            })}
          >
            <TimeSelectField
              name="startTime"
              label={t('startTime')}
              value={draft.startTime}
              error={errorText('startTime')}
              onChange={(value) => change('startTime', value)}
              isDisabled={pending}
            />
            <TimeSelectField
              name="endTime"
              label={t('endTime')}
              value={draft.endTime}
              durationFrom={draft.startTime}
              error={errorText('endTime')}
              onChange={(value) => change('endTime', value)}
              isDisabled={pending}
            />
          </div>
        </FieldRow>
        {failed && <Text role="alert">{t('error')}</Text>}
      </RACForm>
    </AppDialog>
  )
}
