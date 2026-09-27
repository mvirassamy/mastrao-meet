import {
  MEETING_HISTORY_PATH,
  meetingHistoryDetailPath,
} from '@/features/meetingHistory/paths'

export const previewScreens = [
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
  ['authenticated-home', 'Accueil connecté'],
  ['later-meeting-dialog', 'Accueil · réunion pour plus tard'],
  ['history', 'Historique · liste'],
  ['history-loading', 'Historique · chargement'],
  ['history-empty', 'Historique · vide'],
  ['history-error', 'Historique · erreur'],
  ['history-detail', 'Historique · synthèse et transcription'],
  ['history-detail-processing', 'Historique · traitement en cours'],
  ['history-detail-absent', 'Historique · contenu absent'],
  [
    'history-detail-summary-request',
    'Historique · demande de synthèse (refusée en aperçu)',
  ],
  ['history-detail-error', 'Historique · erreur du détail'],
] as const

export type PreviewContent =
  | 'gallery'
  | 'room'
  | 'home'
  | 'authenticated-home'
  | 'meeting-history'
  | 'invitation'
  | 'feedback'
  | 'consent'
  | 'error'
  | 'loading'
  | 'join'

const previewHistoryDetailIds: Record<string, string> = {
  'history-detail': 'preview-meeting-available',
  'history-detail-processing': 'preview-meeting-processing',
  'history-detail-absent': 'preview-meeting-absent',
  'history-detail-summary-request': 'preview-meeting-summary-request',
  'history-detail-error': 'preview-meeting-error',
}

/** Initial in-memory path of the authenticated workspace preview. */
export const getPreviewWorkspacePath = (scenario: string) => {
  if (!scenario.startsWith('history')) return '/'
  const meetingId = previewHistoryDetailIds[scenario]
  return meetingId ? meetingHistoryDetailPath(meetingId) : MEETING_HISTORY_PATH
}

export const getPreviewContent = (scenario: string): PreviewContent => {
  if (
    scenario === 'accessibility' ||
    scenario === 'room' ||
    scenario === 'notifications' ||
    scenario === 'reconnecting' ||
    scenario.startsWith('recording-')
  )
    return 'room'

  if (scenario === 'components') return 'gallery'
  if (scenario === 'home') return 'home'
  if (scenario === 'authenticated-home' || scenario === 'later-meeting-dialog')
    return 'authenticated-home'
  if (scenario === 'history' || scenario.startsWith('history-'))
    return 'meeting-history'
  if (scenario === 'invitation') return 'invitation'
  if (scenario === 'feedback') return 'feedback'
  if (scenario === 'consent') return 'consent'
  if (scenario === 'error') return 'error'
  if (scenario === 'loading') return 'loading'

  return 'join'
}
