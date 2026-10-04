import { type RefObject, useEffect } from 'react'

// Lets a meeting list restore keyboard focus on the meeting that was opened.
let lastOpenedMeetingId: string | null = null

export const rememberOpenedMeeting = (meetingId: string) => {
  lastOpenedMeetingId = meetingId
}

const consumeOpenedMeeting = () => {
  const meetingId = lastOpenedMeetingId
  lastOpenedMeetingId = null
  return meetingId
}

/**
 * Once the meetings are shown, focuses the link of the meeting opened from
 * this list, if any. The remembered meeting is used only once.
 */
export const useRestoreOpenedMeetingFocus = (
  containerRef: RefObject<HTMLElement | null>,
  ready: boolean
) => {
  useEffect(() => {
    if (!ready) return
    const openedId = consumeOpenedMeeting()
    if (!openedId) return
    const link = Array.from(
      containerRef.current?.querySelectorAll<HTMLAnchorElement>(
        'a[data-meeting-id]'
      ) ?? []
    ).find((element) => element.dataset.meetingId === openedId)
    link?.focus()
  }, [ready, containerRef])
}
