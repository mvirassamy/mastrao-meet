import { MEETING_HISTORY_ENDPOINT } from '@/features/meetingHistory/api/meetingHistoryApi'

/*
 * PREVIEW ONLY — fictional meeting history served by the local preview entry
 * (preview.html). Never imported by the application routes or build: real
 * users only ever see data returned by the backend.
 */

const MINUTE = 60_000
const DAY = 24 * 60 * MINUTE
const PREVIEW_PEOPLE = [
  'Matthias Virassamy',
  'Camille Martin',
  'Karim Benali',
  'Anne Dubois',
  'Lucas Petit',
]

type PreviewMeeting = {
  id: string
  title: string | null
  startedAgoMs: number
  durationMs: number
  participant_count?: number
  summary_status: string
  transcript_status: string
}

const previewMeetings: PreviewMeeting[] = [
  {
    id: 'preview-meeting-available',
    title: 'Point hebdomadaire · test',
    startedAgoMs: 3 * 60 * MINUTE,
    durationMs: 45 * MINUTE,
    participant_count: 3,
    summary_status: 'available',
    transcript_status: 'available',
  },
  // Opening it sends the summary request, which the preview refuses (503).
  {
    id: 'preview-meeting-summary-request',
    title: 'Entretien · test',
    startedAgoMs: 5 * 60 * MINUTE,
    durationMs: 30 * MINUTE,
    participant_count: 2,
    summary_status: 'absent',
    transcript_status: 'available',
  },
  // Deliberately out of order: the interface sorts most recent first.
  {
    id: 'preview-meeting-absent',
    title: 'Appel rapide · test',
    startedAgoMs: DAY + 2 * 60 * MINUTE,
    durationMs: 12 * MINUTE,
    participant_count: 2,
    summary_status: 'absent',
    transcript_status: 'absent',
  },
  {
    id: 'preview-meeting-processing',
    title: 'Rendez-vous client · test',
    startedAgoMs: 70 * MINUTE,
    durationMs: 50 * MINUTE,
    participant_count: 2,
    summary_status: 'processing',
    transcript_status: 'processing',
  },
  {
    id: 'preview-meeting-summary-failed',
    title: 'Revue de dossier · test',
    startedAgoMs: 3 * DAY,
    durationMs: 95 * MINUTE,
    participant_count: 4,
    summary_status: 'failed',
    transcript_status: 'available',
  },
  {
    id: 'preview-meeting-error',
    title: null,
    startedAgoMs: 8 * DAY,
    durationMs: 30 * MINUTE,
    summary_status: 'available',
    transcript_status: 'available',
  },
]

const previewOlderMeetings: PreviewMeeting[] = [
  {
    id: 'preview-meeting-kickoff',
    title: 'Réunion de lancement · test',
    startedAgoMs: 35 * DAY,
    durationMs: 60 * MINUTE,
    participant_count: 5,
    summary_status: 'available',
    transcript_status: 'available',
  },
  {
    id: 'preview-meeting-workshop',
    title: 'Atelier · test',
    startedAgoMs: 41 * DAY,
    durationMs: 25 * MINUTE,
    summary_status: 'absent',
    transcript_status: 'absent',
  },
]

const toApiItem = (meeting: PreviewMeeting) => {
  const startedAt = new Date(Date.now() - meeting.startedAgoMs)
  return {
    id: meeting.id,
    title: meeting.title,
    started_at: startedAt.toISOString(),
    ended_at: new Date(startedAt.getTime() + meeting.durationMs).toISOString(),
    participant_count: meeting.participant_count,
    participant_names: PREVIEW_PEOPLE.slice(0, meeting.participant_count ?? 0),
    summary_status: meeting.summary_status,
    transcript_status: meeting.transcript_status,
  }
}

const previewSummary = {
  status: 'available',
  text: 'Données de test affichées uniquement dans l’aperçu local.\n\nL’équipe a fait le point sur l’avancement de la semaine et validé le calendrier de la prochaine livraison.',
  sections: [
    {
      title: 'Décisions',
      items: [
        'La livraison est maintenue au vendredi.',
        'Les retours des bêta testeurs seront regroupés dans un seul document.',
      ],
    },
    {
      title: 'Actions',
      items: [
        'Camille · test prépare la liste des testeurs.',
        'Karim · test vérifie l’accès sur mobile.',
      ],
    },
  ],
}

const previewTranscript = {
  status: 'available',
  segments: [
    {
      id: 's1',
      start_ms: 4_000,
      speaker: 'Vous · test',
      text: 'Bonjour à tous, on commence par le point d’avancement.',
    },
    {
      id: 's2',
      start_ms: 11_000,
      speaker: 'Vous · test',
      text: 'L’objectif est de valider la date de livraison.',
    },
    {
      id: 's3',
      start_ms: 19_000,
      speaker: 'Camille · test',
      text: 'De mon côté, la liste des testeurs est presque prête. Il manque deux contacts.',
    },
    {
      id: 's4',
      start_ms: 42_000,
      speaker: 'Karim · test',
      text: 'J’ai testé sur téléphone hier, l’accès fonctionne. Je referai un essai avec une connexion plus lente.',
    },
    {
      id: 's5',
      start_ms: 71_000,
      speaker: 'Vous · test',
      text: 'Parfait. On garde vendredi pour la livraison ?',
    },
    {
      id: 's6',
      start_ms: 76_000,
      speaker: 'Camille · test',
      text: 'Oui, vendredi me convient.',
    },
    {
      id: 's7',
      start_ms: 80_000,
      speaker: 'Karim · test',
      text: 'Je m’occupe du test mobile avant vendredi.',
    },
  ],
  truncated: false,
}

// Same shape as the Platform facade: null when the content is not available.
const detailFor = (meeting: PreviewMeeting) => ({
  ...toApiItem(meeting),
  summary: meeting.summary_status === 'available' ? previewSummary : null,
  transcript:
    meeting.transcript_status === 'available' ? previewTranscript : null,
})

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

const delayed = (response: Response, delayMs = 350) =>
  new Promise<Response>((resolve) =>
    setTimeout(() => resolve(response), delayMs)
  )

export const isPreviewMeetingHistoryRequest = (url: URL) =>
  url.pathname.includes(`/${MEETING_HISTORY_ENDPOINT}`)

/** PREVIEW ONLY — answers the provisional history endpoints locally. */
export const previewMeetingHistoryResponse = (
  url: URL,
  scenario: string
): Promise<Response> => {
  const [, rest = ''] = url.pathname.split(`/${MEETING_HISTORY_ENDPOINT}`)
  const meetingId = decodeURIComponent(rest.replace(/\/$/, ''))

  if (!meetingId) {
    if (scenario === 'history-loading')
      return new Promise<Response>(() => undefined)
    if (scenario === 'history-error') return delayed(json({}, 503))
    if (scenario === 'history-empty')
      return delayed(json({ results: [], next_cursor: null }))
    if (url.searchParams.get('cursor') === 'preview-page-2')
      return delayed(
        json({
          results: previewOlderMeetings.map(toApiItem),
          next_cursor: null,
        })
      )
    return delayed(
      json({
        results: previewMeetings.map(toApiItem),
        next_cursor: 'preview-page-2',
      })
    )
  }

  if (meetingId === 'preview-meeting-error') return delayed(json({}, 503))
  if (scenario === 'history-detail-loading')
    return new Promise<Response>(() => undefined)
  const meeting = [...previewMeetings, ...previewOlderMeetings].find(
    (candidate) => candidate.id === meetingId
  )
  return delayed(meeting ? json(detailFor(meeting)) : json({}, 404))
}
