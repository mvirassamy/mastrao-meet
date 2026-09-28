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
import { Route, Router, Switch } from 'wouter'
import { memoryLocation } from 'wouter/memory-location'
import { QueryClientProvider } from '@tanstack/react-query'
import { LiveKitRoom } from '@livekit/components-react'
import { ConnectionState, RemoteParticipant, Room } from 'livekit-client'
import { queryClient } from '@/api/queryClient'
import { Layout } from '@/layout/Layout'
import { Screen } from '@/layout/Screen'
import { ErrorScreen } from '@/components/ErrorScreen'
import { NotFoundScreen } from '@/components/NotFoundScreen'
import { LoadingScreen } from '@/components/LoadingScreen'
import Home from '@/features/home/routes/Home'
import { AuthenticatedHome } from '@/features/home/routes/AuthenticatedHome'
import { LaterMeetingDialog } from '@/features/home/components/LaterMeetingDialog'
import { ApiAccessLevel } from '@/features/rooms/api/ApiRoom'
import {
  MeetingHistoryMeetingPage,
  MeetingHistoryPage,
} from '@/features/meetingHistory/components/MeetingHistoryPages'
import { MEETING_HISTORY_PATH } from '@/features/meetingHistory/paths'
import GuestInvitation from '@/features/rooms/routes/GuestInvitation'
import Feedback from '@/features/rooms/routes/Feedback'
import { Join } from '@/features/rooms/components/Join'
import { Permissions } from '@/features/rooms/components/Permissions'
import { RecordingConsent } from '@/features/rooms/components/RecordingConsent'
import { MeetingLifecycleProvider } from '@/features/rooms/contexts/MeetingLifecycleProvider'
import { VideoConference } from '@/features/rooms/livekit/prefabs/VideoConference'
import { userChoicesStore } from '@/stores/userChoices'
import { useWatchDeviceAvailability } from '@/features/rooms/hooks/useWatchDeviceAvailability'
import {
  previewRoom,
  previewRoomId,
  previewScenario,
  previewUser,
} from './previewFixtures'
import {
  getPreviewContent,
  getPreviewWorkspacePath,
  previewScreens,
} from './previewScenarioRoute'

/**
 * Authenticated workspace with in-memory routing, so the sidebar, the
 * history list, a meeting detail and the way back stay inside the preview.
 */
const PreviewWorkspace = () => {
  const [location] = useState(() =>
    memoryLocation({ path: getPreviewWorkspacePath(previewScenario) })
  )
  // "Réunion pour plus tard" normally opens after a room is created; the
  // preview shows it directly with the fictional room.
  const [laterOpen, setLaterOpen] = useState(
    previewScenario === 'later-meeting-dialog'
  )
  return (
    <Router hook={location.hook}>
      <LaterMeetingDialog
        room={
          laterOpen
            ? { ...previewRoom, access_level: ApiAccessLevel.PUBLIC }
            : null
        }
        onOpenChange={(open) => !open && setLaterOpen(false)}
      />
      <Switch>
        <Route path={MEETING_HISTORY_PATH}>
          <MeetingHistoryPage user={previewUser} />
        </Route>
        <Route<{ meetingId: string }>
          path={`${MEETING_HISTORY_PATH}/:meetingId`}
        >
          {({ meetingId }) => (
            <MeetingHistoryMeetingPage
              user={previewUser}
              meetingId={decodeURIComponent(meetingId)}
            />
          )}
        </Route>
        <Route>
          <AuthenticatedHome user={previewUser} />
        </Route>
      </Switch>
    </Router>
  )
}

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
  const content = getPreviewContent(previewScenario)

  if (content === 'room') return <PreviewRoom />
  if (content === 'gallery') return <SemanticGallery />
  if (content === 'home') return <Home />
  if (content === 'authenticated-home' || content === 'meeting-history')
    return <PreviewWorkspace />
  if (content === 'invitation') return <GuestInvitation />
  if (content === 'feedback') return <Feedback />
  if (content === 'consent')
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
  if (content === 'error') return <ErrorScreen />
  if (content === 'not-found') return <NotFoundScreen />
  if (content === 'loading') return <LoadingScreen delay={0} />
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
              {previewScreens.map(([value, label]) => (
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
