import type {
  MeetingRecording,
  MeetingRecordingAccess,
  MeetingRecordingStatus,
} from './types'

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** Missing additive fields belong to older responses, which had no video. */
export const normalizeRecordingStatus = (
  value: unknown
): MeetingRecordingStatus => {
  if (value === undefined) return 'absent'
  if (typeof value !== 'string') return 'unknown'
  switch (value) {
    case 'absent':
    case 'processing':
    case 'available':
    case 'expired':
    case 'failed':
    case 'unknown':
      return value
    default:
      return 'unknown'
  }
}

export const isRecordingAccessPath = (value: unknown): value is string =>
  typeof value === 'string' &&
  value.trim() === value &&
  /^\/api\/orgs\/[a-z0-9]+(?:-[a-z0-9]+)*\/meetings\/recording\/access$/u.test(
    value
  )

const isReference = (value: unknown): value is string =>
  typeof value === 'string' && /^[A-Za-z0-9_-]{16,160}$/u.test(value)

const normalizeAccess = (value: unknown): MeetingRecordingAccess | null => {
  if (
    !isRecord(value) ||
    !isRecordingAccessPath(value.action_path) ||
    !isReference(value.matter_ref) ||
    !isReference(value.meeting_ref) ||
    !isReference(value.recording_ref) ||
    !isReference(value.artifact_ref)
  ) {
    return null
  }
  return {
    actionPath: value.action_path,
    matterRef: value.matter_ref,
    meetingRef: value.meeting_ref,
    recordingRef: value.recording_ref,
    artifactRef: value.artifact_ref,
  }
}

const retentionDate = (value: unknown): Date | null => {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value <= 0) {
    return null
  }
  const date = new Date(value * 1_000)
  return Number.isNaN(date.getTime()) ? null : date
}

/** Video metadata never invalidates the meeting's text content. */
export const normalizeMeetingRecording = (value: unknown): MeetingRecording => {
  if (!isRecord(value)) {
    return {
      status: value === undefined ? 'absent' : 'unknown',
      retentionExpiresAt: null,
      access: null,
    }
  }
  const status = normalizeRecordingStatus(value.status ?? null)
  const retentionExpiresAt = retentionDate(value.retention_expires_at)
  let access: MeetingRecordingAccess | null = null
  if (status === 'available' && retentionExpiresAt) {
    access = normalizeAccess(value.access)
  }
  return { status, retentionExpiresAt, access }
}
