import '@livekit/components-styles'
import '@/styles/index.css'
import '@/i18n/init'
import './preview.css'
import { notifyAutoMutedOnJoin } from '@/features/notifications/utils'
import { useApplyA11yFonts } from '@/hooks/useApplyA11yFonts'
import { SemanticGallery } from './SemanticGallery'
import { SettingsDialogExtendedKey } from '@/features/settings/type'
import { openSettingsDialog, closeSettingsDialog } from '@/stores/settings'
import { Suspense, useEffect, useState } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { LiveKitRoom } from '@livekit/components-react'
import { ConnectionState, RemoteParticipant, Room } from 'livekit-client'
import { queryClient } from '@/api/queryClient'
import { Layout } from '@/layout/Layout'
import { Screen } from '@/layout/Screen'
import Home from '@/features/home/routes/Home'
import GuestInvitation from '@/features/rooms/routes/GuestInvitation'
import Feedback from '@/features/rooms/routes/Feedback'
import { Join } from '@/features/rooms/components/Join'
import { Permissions } from '@/features/rooms/components/Permissions'
import { RecordingConsent } from '@/features/rooms/components/RecordingConsent'
import { MeetingLifecycleProvider } from '@/features/rooms/contexts/MeetingLifecycleProvider'
import { VideoConference } from '@/features/rooms/livekit/prefabs/VideoConference'
import { userChoicesStore } from '@/stores/userChoices'
import { useWatchDeviceAvailability } from '@/features/rooms/hooks/useWatchDeviceAvailability'
import { previewRoomId, previewScenario } from './previewFixtures'

const screens = [
  ['components', 'Composants et états'],
  ['accessibility', 'Réunion · paramètres d’accessibilité'],
  ['join', 'Avant la réunion'],
  ['devices-off', 'Micro et caméra coupés'],
  ['reconnecting', 'Reconnexion (état visuel)'],
  ['recording-starting', 'Enregistrement en démarrage (état visuel)'],
  ['recording-active', 'Enregistrement actif (état visuel)'],
  ['recording-stopping', 'Enregistrement en arrêt (état visuel)'],
  ['room', 'Salle'],
  ['notifications', 'Notification de démonstration'],
  ['consent', 'Consentement'],
  ['invitation', 'Invitation'],
  ['feedback', 'Après la réunion'],
  ['error', 'Erreur d’accès'],
  ['loading', 'Chargement'],
  ['home', 'Accueil'],
] as const

const PreviewRoom = () => {
  useEffect(() => {
    if (previewScenario === 'notifications') notifyAutoMutedOnJoin()
    if (previewScenario === 'accessibility') {
      openSettingsDialog(SettingsDialogExtendedKey.ACCESSIBILITY)
      return closeSettingsDialog
    }
  }, [])
  const [room] = useState(() => {
    const instance = new Room()
    if (previewScenario === 'reconnecting')
      instance.state = ConnectionState.Reconnecting
    instance.localParticipant.identity = 'preview-local'
    instance.localParticipant.name = 'Vous · test'
    instance.remoteParticipants.set(
      'preview-remote',
      new RemoteParticipant(
        instance.engine.client,
        'PA_preview',
        'preview-remote',
        'Camille · test'
      )
    )
    return instance
  })
  return (
    <Screen header={false} footer={false}>
      <LiveKitRoom
        data-lk-theme="visio-light"
        room={room}
        serverUrl=""
        token=""
        connect={false}
        audio={false}
        video={false}
      >
        <VideoConference
          roomId={previewRoomId}
          recording={
            previewScenario.startsWith('recording-')
              ? {
                  mode: 'recorded',
                  decision: 'accepted',
                  recording_state:
                    previewScenario === 'recording-starting'
                      ? 'starting'
                      : previewScenario === 'recording-stopping'
                        ? 'stopping'
                        : 'active',
                }
              : { mode: 'disabled' }
          }
        />
      </LiveKitRoom>
    </Screen>
  )
}

const PreviewScreen = () => {
  useWatchDeviceAvailability()
  useEffect(() => {
    userChoicesStore.audioEnabled = false
    userChoicesStore.videoEnabled = false
  }, [])
  if (
    previewScenario === 'accessibility' ||
    previewScenario === 'room' ||
    previewScenario === 'notifications' ||
    previewScenario === 'reconnecting' ||
    previewScenario.startsWith('recording-')
  )
    return <PreviewRoom />
  if (previewScenario === 'components') return <SemanticGallery />
  if (previewScenario === 'home') return <Home />
  if (previewScenario === 'invitation') return <GuestInvitation />
  if (previewScenario === 'feedback') return <Feedback />
  if (previewScenario === 'consent')
    return (
      <Screen layout="centered" footer={false}>
        <section>
          <RecordingConsent
            roomId={previewRoomId}
            retentionExpiresAt={1790000000}
            participantKind="guest"
            transcriptionOffered
            recordingDecision="absent"
            transcriptionDecision="absent"
            onDecided={async () => undefined}
          />
        </section>
      </Screen>
    )
  return <Join roomId={previewRoomId} enterRoom={() => undefined} />
}

export const Preview = () => {
  useApplyA11yFonts()
  return (
    <QueryClientProvider client={queryClient}>
      <div className="preview-shell">
        <div className="preview-banner">
          <strong>Aperçu local</strong>
          <span>
            Données de test · aucun appel, enregistrement ou envoi réel
          </span>
          <label>
            Écran
            <select
              value={previewScenario}
              onChange={(event) =>
                location.assign(
                  `/preview.html?screen=${event.target.value}&outcome=ended`
                )
              }
            >
              {screens.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <Suspense fallback={<p role="status">Chargement de l’aperçu…</p>}>
          <MeetingLifecycleProvider roomId={previewRoomId}>
            <Layout>
              <Permissions />
              <PreviewScreen />
            </Layout>
          </MeetingLifecycleProvider>
        </Suspense>
      </div>
    </QueryClientProvider>
  )
}
