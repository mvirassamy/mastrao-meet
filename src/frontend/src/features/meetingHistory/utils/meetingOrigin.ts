import { MEETING_HISTORY_PATH } from '../paths'

/**
 * History state carried by a meeting link, so the detail page can send the
 * user back to the list the meeting was opened from (home day or history).
 */
type MeetingOriginState = { meetingBackTo: string }

export const meetingOriginState = (
  location: string,
  search: string
): MeetingOriginState => ({
  meetingBackTo: search ? `${location}?${search}` : location,
})

/** The opening list, or the history when the detail was opened directly. */
export const meetingBackPath = (state: unknown) => {
  const backTo = (state as Partial<MeetingOriginState> | null)?.meetingBackTo
  const isAppPath = typeof backTo === 'string' && /^\/(?!\/)/.test(backTo)
  return isAppPath ? backTo : MEETING_HISTORY_PATH
}
