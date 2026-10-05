import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { MeetingHistoryItem } from '../api/types'
import { meetingHistoryDetailPath, MEETING_HISTORY_PATH } from '../paths'
import { meetingBackPath } from '../utils/meetingOrigin'
import { MeetingRowLink } from './MeetingRowLink'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

const item = {
  id: 'meeting-1',
  title: 'Point client',
  startedAt: new Date('2026-10-05T11:00:00Z'),
  endedAt: null,
  participantCount: null,
  participantNames: [],
  summaryStatus: 'available',
  transcriptStatus: 'available',
} as unknown as MeetingHistoryItem

const openFrom = (path: string) => {
  window.history.replaceState(null, '', path)
  render(<MeetingRowLink item={item} linkRef={createRef()} />)
  fireEvent.click(screen.getByRole('link'))
}

afterEach(() => {
  cleanup()
  window.history.replaceState(null, '', '/')
})

describe('MeetingRowLink', () => {
  it('lets the detail return to the home day it was opened from', () => {
    openFrom('/?jour=2026-10-05')

    expect(window.location.pathname).toBe(meetingHistoryDetailPath('meeting-1'))
    expect(meetingBackPath(window.history.state)).toBe('/?jour=2026-10-05')
  })

  it('lets the detail return to the history it was opened from', () => {
    openFrom(MEETING_HISTORY_PATH)

    expect(meetingBackPath(window.history.state)).toBe(MEETING_HISTORY_PATH)
  })
})
