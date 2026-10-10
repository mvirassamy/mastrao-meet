import { isRecordingAccessPath } from '../api/meetingRecording'
import type { MeetingRecording } from '../api/types'

/** Only the configured Platform origin can receive recording references. */
export const recordingAccessAction = (
  recording: MeetingRecording,
  platformOrigin: unknown
): string | null => {
  if (
    recording.status !== 'available' ||
    !recording.retentionExpiresAt ||
    recording.retentionExpiresAt.getTime() <= Date.now() ||
    !recording.access ||
    !isRecordingAccessPath(recording.access.actionPath) ||
    typeof platformOrigin !== 'string'
  ) {
    return null
  }
  try {
    const origin = new URL(platformOrigin)
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(origin.hostname)
    if (
      (origin.protocol !== 'https:' &&
        !(local && origin.protocol === 'http:')) ||
      origin.username ||
      origin.password ||
      origin.pathname !== '/' ||
      origin.search ||
      origin.hash
    ) {
      return null
    }
    return `${origin.origin}${recording.access.actionPath}`
  } catch {
    return null
  }
}
