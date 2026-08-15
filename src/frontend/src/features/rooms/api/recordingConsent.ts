import { fetchApi } from '@/api/fetchApi'

export type RecordingDecision = 'accepted' | 'refused' | 'withdrawn'

export const decideRecording = (
  roomId: string,
  decision: RecordingDecision,
  decisionRequestId: string
) =>
  fetchApi(`/rooms/${roomId}/recording-decision/`, {
    method: 'POST',
    body: JSON.stringify({
      decision,
      decision_request_id: decisionRequestId,
    }),
  })

export const activateRecording = (
  roomId: string,
  activationRequestId: string
) =>
  fetchApi(`/rooms/${roomId}/recording-activate/`, {
    method: 'POST',
    body: JSON.stringify({ activation_request_id: activationRequestId }),
  })

export const stopRecording = (
  roomId: string,
  source: 'host' | 'refusal' | 'withdrawal',
  stopRequestId: string
) =>
  fetchApi(`/rooms/${roomId}/recording-stop/`, {
    method: 'POST',
    body: JSON.stringify({ source, stop_request_id: stopRequestId }),
  })
