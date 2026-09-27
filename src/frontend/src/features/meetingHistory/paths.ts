export const MEETING_HISTORY_PATH = '/reunions/historique'

export const meetingHistoryDetailPath = (meetingId: string) =>
  `${MEETING_HISTORY_PATH}/${encodeURIComponent(meetingId)}`

export const isMeetingHistoryPath = (location: string) =>
  location === MEETING_HISTORY_PATH ||
  location.startsWith(`${MEETING_HISTORY_PATH}/`)
