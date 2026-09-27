// Lets the history list restore keyboard focus on the meeting that was opened.
let lastOpenedMeetingId: string | null = null

export const rememberOpenedMeeting = (meetingId: string) => {
  lastOpenedMeetingId = meetingId
}

export const consumeOpenedMeeting = () => {
  const meetingId = lastOpenedMeetingId
  lastOpenedMeetingId = null
  return meetingId
}
