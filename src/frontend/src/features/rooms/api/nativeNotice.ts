import { fetchApi } from '@/api/fetchApi'

export type NativeNoticeSnapshot = {
  policy_ref: string
  notice_version: string
  notice_digest: string
  purpose: 'meeting_transcription_source_audio'
  scope: 'consented_microphone_track_epoch'
  retention_expires_at: number
}

export type NativeNoticeProjection = {
  version: 1
  text: string
  notice: NativeNoticeSnapshot
  decision:
    | (NativeNoticeSnapshot & {
        decision: 'accepted' | 'refused'
        decision_ref: string
        decided_at: number
      })
    | null
  capture_authorized: false
}

export const decideNativeNotice = (
  roomId: string,
  decision: 'accepted' | 'refused',
  notice: NativeNoticeSnapshot
) =>
  fetchApi<NativeNoticeProjection>(`/rooms/${roomId}/native-notice-decision/`, {
    method: 'POST',
    body: JSON.stringify({ decision, notice }),
  })
