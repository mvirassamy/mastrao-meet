import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchApi } from '@/api/fetchApi'
import { ScheduledMeetingEvent } from './ScheduledMeetingEvent'
import type { PlannedMeeting } from '../../api/plannedMeetings'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/api/fetchApi', () => ({ fetchApi: vi.fn() }))
const assign = vi.fn()
beforeEach(() => {
  vi.mocked(fetchApi).mockReset()
  assign.mockReset()
  sessionStorage.clear()
  vi.stubGlobal('location', { ...window.location, assign })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})
const meeting: PlannedMeeting = {
  id: 'meeting_0123456789abcdef0123456789abcdef',
  roomRef: 'room_0123456789abcdef0123456789abcdef',
  title: 'Réunion du 6 octobre',
  startsAt: new Date('2026-10-06T08:00:00Z'),
  endsAt: new Date('2026-10-06T09:15:00Z'),
  isClosed: false,
}
const renderMeeting = (item = meeting, now?: Date) =>
  render(
    <ul>
      <ScheduledMeetingEvent
        meeting={item}
        color="#2d5be3"
        locale="fr"
        timeZone="Europe/Paris"
        now={now}
      />
    </ul>
  )

describe('scheduled meeting event', () => {
  it('shows the canonical title and planned local time range', () => {
    renderMeeting()
    expect(screen.getByRole('heading', { name: meeting.title! })).toBeTruthy()
    expect(screen.getByText(/10:00.*11:15/)).toBeTruthy()
    expect(screen.getByText('dashboard.meetings.planned')).toBeTruthy()
  })
  it('confirms host access and opens the room directly', async () => {
    vi.mocked(fetchApi).mockResolvedValue({ room_url: `/${meeting.roomRef}` })
    renderMeeting()
    fireEvent.click(
      screen.getByRole('button', { name: 'dashboard.meetings.join' })
    )
    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith(`/${meeting.roomRef}`)
    )
    expect(vi.mocked(fetchApi).mock.calls[0][0]).toBe(
      `rooms/${meeting.roomRef}/host-handoff/`
    )
  })
  it('says so when the room cannot be opened', async () => {
    vi.mocked(fetchApi).mockRejectedValue(new Error('unavailable'))
    renderMeeting()
    fireEvent.click(
      screen.getByRole('button', { name: 'dashboard.meetings.join' })
    )
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(assign).not.toHaveBeenCalled()
  })
  it('stands out in the quarter hour before the start, with the time left', () => {
    renderMeeting(meeting, new Date('2026-10-06T07:50:00Z'))
    const join = screen.getByRole('button', { name: 'dashboard.meetings.join' })
    expect(join.querySelector('svg')).not.toBeNull()
    expect(screen.getByText('dashboard.meetings.plannedIn')).toBeTruthy()
  })
  it('stays discreet earlier in the day', () => {
    renderMeeting(meeting, new Date('2026-10-06T06:00:00Z'))
    const join = screen.getByRole('button', { name: 'dashboard.meetings.join' })
    expect(join.querySelector('svg')).toBeNull()
    expect(screen.getByText('dashboard.meetings.plannedIn')).toBeTruthy()
  })
  it('never offers reopening a canonically closed meeting', () => {
    renderMeeting({ ...meeting, isClosed: true })
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('dashboard.meetings.closed')).toBeTruthy()
  })
})
