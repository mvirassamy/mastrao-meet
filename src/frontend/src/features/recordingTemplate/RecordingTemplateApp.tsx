import { LiveKitRoom, RoomAudioRenderer } from '@livekit/components-react'
import { readRecordingTemplateConfig } from './recordingTemplateConfig'
import { RecordingStage } from './RecordingStage'
import './recordingTemplate.css'

function RecordingTemplateError({ message }: { message: string }) {
  return (
    <main className="recording-template-error">
      <h1>Enregistrement indisponible</h1>
      <p>{message}</p>
    </main>
  )
}

export default function RecordingTemplateApp() {
  let config
  try {
    config = readRecordingTemplateConfig()
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown error.'
    return <RecordingTemplateError message={message} />
  }

  return (
    <LiveKitRoom
      className="recording-template-room"
      connect
      serverUrl={config.serverUrl}
      token={config.token}
      options={{ adaptiveStream: false, dynacast: false }}
    >
      <RecordingStage />
      <RoomAudioRenderer />
    </LiveKitRoom>
  )
}
