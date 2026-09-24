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
] as const

export type PreviewContent =
  | 'gallery'
  | 'room'
  | 'home'
  | 'invitation'
  | 'feedback'
  | 'consent'
  | 'error'
  | 'loading'
  | 'join'

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
  if (scenario === 'invitation') return 'invitation'
  if (scenario === 'feedback') return 'feedback'
  if (scenario === 'consent') return 'consent'
  if (scenario === 'error') return 'error'
  if (scenario === 'loading') return 'loading'

  return 'join'
}
