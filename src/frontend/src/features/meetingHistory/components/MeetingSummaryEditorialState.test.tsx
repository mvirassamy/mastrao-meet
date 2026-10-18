import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import fr from '../../../locales/fr/meetingHistory.json'
import type { MeetingSummary } from '../api/types'
import { SummaryBody } from './MeetingResultSections'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const label = fr.summary[key.split('.')[1] as keyof typeof fr.summary]
      return typeof label === 'string' ? label : key
    },
  }),
}))

afterEach(cleanup)

describe('summary editorial state', () => {
  it.each([
    ['published_reviewed', 'Synthèse relue et publiée.'],
    ['generated_pending_review', 'Synthèse générée automatiquement, à relire.'],
    [
      'validated_ready_to_publish',
      'Synthèse validée, en attente de publication.',
    ],
    [
      'stale_draft',
      'La transcription source a changé. Cette synthèse est obsolète.',
    ],
    [
      undefined,
      'Synthèse issue de la transcription. État de relecture non renseigné.',
    ],
  ] as const)(
    'shows factual wording for %s with no accepted sections',
    (productState, label) => {
      const summary: MeetingSummary = {
        status: 'available',
        productState,
        projection: {
          version: 1,
          state: 'available',
          revision: 1,
          digest: null,
          source: null,
        },
        paragraphs: ['Résumé conservé.'],
        sections: [],
      }
      render(
        <SummaryBody summary={summary} view="available" onRetry={vi.fn()} />
      )

      expect(screen.getByText(label)).toBeTruthy()
      expect(screen.getByText('Résumé conservé.')).toBeTruthy()
      expect(screen.queryByText(/sans relecture humaine/)).toBeNull()
      expect(screen.queryByRole('heading')).toBeNull()
      if (productState !== 'published_reviewed') {
        expect(screen.queryByText('Synthèse relue et publiée.')).toBeNull()
      }
    }
  )
})
