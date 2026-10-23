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
  // Video recording status; omitted for meetings recorded before video existed.
  recording_status?: string
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
    recording_status: 'available',
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
    recording_status: 'absent',
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
    recording_status: 'processing',
  },
  {
    id: 'preview-meeting-summary-failed',
    title: 'Revue de dossier · test',
    startedAgoMs: 3 * DAY,
    durationMs: 95 * MINUTE,
    participant_count: 4,
    summary_status: 'failed',
    transcript_status: 'available',
    recording_status: 'failed',
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
    recording_status: 'expired',
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
    ...(meeting.recording_status && {
      recording_status: meeting.recording_status,
    }),
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

const RETENTION_DAYS = 30

/** Fictional video metadata; opening the video stays refused by the preview. */
const previewRecording = (meeting: PreviewMeeting) => {
  const retentionMs =
    meeting.recording_status === 'expired'
      ? -DAY
      : RETENTION_DAYS * DAY - meeting.startedAgoMs
  return {
    status: meeting.recording_status,
    retention_expires_at: Math.floor((Date.now() + retentionMs) / 1000),
    access:
      meeting.recording_status === 'available'
        ? {
            action_path:
              '/api/orgs/organisation-test/meetings/recording/access',
            matter_ref: 'matter_preview_0001',
            meeting_ref: 'meeting_preview_0001',
            recording_ref: 'recording_preview_0001',
            artifact_ref: 'artifact_preview_0001',
          }
        : null,
  }
}

// Same shape as the Platform facade: null when the content is not available.
const detailFor = (meeting: PreviewMeeting) => ({
  ...toApiItem(meeting),
  summary: meeting.summary_status === 'available' ? previewSummary : null,
  transcript:
    meeting.transcript_status === 'available' ? previewTranscript : null,
  ...(meeting.recording_status && { recording: previewRecording(meeting) }),
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

const previewCitation = (meetingId: string, id: string, startMs: number) => ({
  citation_id: id,
  meeting_ref: meetingId,
  source_kind: 'provider_run',
  source_ref: 'source_preview_0001',
  source_version: 1,
  source_digest: 'a'.repeat(64),
  segment_id: id,
  start_ms: startMs,
  end_ms: startMs + 6_000,
  time_basis: 'meeting_relative_ms',
})

const previewCoverage = {
  state: 'full',
  source_coverage: 'reference_transcript',
  selection: 'complete',
  total_segments: 6,
  selected_segments: 6,
  omitted_segments: 0,
}

/** A fictional private thread: one question and one action extraction. */
const previewAssistantThread = (meetingId: string) => ({
  version: 1,
  meeting_ref: meetingId,
  thread_ref: 'thread_preview_0001',
  thread_version: 2,
  visibility: 'private',
  deleted: false,
  has_more: false,
  next_cursor: null,
  turns: [
    {
      command_id: 'command_preview_0001',
      operation: 'question',
      question: 'Quelle date a été retenue pour la livraison ?',
      state: 'completed',
      created_at: Math.floor(Date.now() / 1000) - 600,
      coverage: previewCoverage,
      result: {
        state: 'answered',
        text: 'La livraison est maintenue au vendredi. Camille l’a confirmé et Karim s’occupe du test mobile avant cette date.',
        rationale_category: 'supported',
        citations: [
          previewCitation(meetingId, 'segment_preview_0005', 76_000),
          previewCitation(meetingId, 'segment_preview_0006', 80_000),
        ],
        actions: [],
        email_draft: null,
        publication_state: 'private_proposal',
      },
    },
    {
      command_id: 'command_preview_0002',
      operation: 'actions',
      question: null,
      state: 'completed',
      created_at: Math.floor(Date.now() / 1000) - 300,
      coverage: previewCoverage,
      result: {
        state: 'answered',
        text: '',
        rationale_category: 'supported',
        citations: [previewCitation(meetingId, 'segment_preview_0003', 42_000)],
        actions: [
          {
            statement: 'Préparer la liste des testeurs.',
            owner: 'Camille · test',
            due_date: null,
            citation_ids: ['segment_preview_0003'],
          },
          {
            statement: 'Tester l’accès mobile avant vendredi.',
            owner: 'Karim · test',
            due_date: 'vendredi',
            citation_ids: ['segment_preview_0003'],
          },
        ],
        email_draft: null,
        publication_state: 'private_proposal',
      },
    },
    {
      command_id: 'command_preview_0003',
      operation: 'email_draft',
      question: null,
      state: 'completed',
      created_at: Math.floor(Date.now() / 1000) - 240,
      coverage: previewCoverage,
      result: {
        state: 'answered',
        text: 'Voici un brouillon de mail de suivi.',
        rationale_category: 'supported',
        citations: [previewCitation(meetingId, 'segment_preview_0003', 42_000)],
        actions: [],
        email_draft: {
          subject: 'Suivi du point hebdomadaire',
          body: 'Bonjour,\n\nMerci pour cet échange. Voici les points à retenir :\n\n- La livraison est maintenue au vendredi.\n- Camille prépare la liste des testeurs.\n- Karim teste l’accès mobile avant vendredi.\n\nBonne journée,',
        },
        publication_state: 'private_proposal',
      },
    },
  ],
})

/** The main preview meeting is filed; the others wait to be filed. */
const previewClassification = (meetingId: string) => {
  if (meetingId === 'preview-meeting-available')
    return {
      version: 1,
      meeting_ref: meetingId,
      matter_ref: 'matter_preview0123456789',
      matter_display_label: 'Succession Martin',
      matter_path: '/orgs/preview/matters/matter_preview0123456789',
      classification_version: 1,
      can_classify: true,
    }
  return {
    version: 1,
    meeting_ref: meetingId,
    matter_ref: null,
    matter_display_label: null,
    matter_path: null,
    classification_version: 0,
    can_classify: true,
  }
}

/** PREVIEW ONLY — answers the provisional history endpoints locally. */
export const previewMeetingHistoryResponse = (
  url: URL,
  scenario: string
): Promise<Response> => {
  const [, rest = ''] = url.pathname.split(`/${MEETING_HISTORY_ENDPOINT}`)
  const [meetingSegment, resource] = rest.split('/')
  if (resource === 'assistant')
    return delayed(
      json(previewAssistantThread(decodeURIComponent(meetingSegment)))
    )
  if (resource === 'classification')
    return delayed(
      json(previewClassification(decodeURIComponent(meetingSegment)))
    )
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
