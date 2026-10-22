import { useTranslation } from 'react-i18next'
import { Button, Dialog } from '@/primitives'
import { MeetingAssistantPanel } from './MeetingAssistantPanel'
import type { MeetingAssistantAction } from './AssistantShortcuts'
import { AssistantFailure } from './AssistantFailure'
import { AssistantCitationDialog } from './AssistantCitationDialog'
import type { AssistantReadiness } from './assistantReadiness'
import { assistantTurnView } from './assistantView'
import { useAssistantCitation } from './useAssistantCitation'
import { useMeetingAssistant } from './useMeetingAssistant'

export function MeetingAssistant({
  meetingId,
  identityKey,
  readiness,
  onClose,
}: {
  meetingId: string
  identityKey: string
  readiness: AssistantReadiness
  onClose: () => void
}) {
  const { t } = useTranslation('meetingAssistant')
  const flow = useMeetingAssistant(
    meetingId,
    identityKey,
    // Read once processed, so an existing conversation stays clearable.
    readiness !== 'preparing'
  )
  const { query } = flow
  const citation = useAssistantCitation(query.dataUpdatedAt)
  const reading =
    query.isPending || (query.isRefetching && !query.isFetchingNextPage)
  function openCitation(turnId: string, id: string) {
    if (query.isFetching || query.isError) return
    const source = flow.turns
      .find((turn) => turn.command_id === turnId)
      ?.result?.citations.find((item) => item.citation_id === id)
    if (source) citation.open(source)
  }
  function action(operation: MeetingAssistantAction) {
    switch (operation) {
      case 'short-summary':
        flow.send('summary', t('prompt.summary'))
        return
      case 'actions':
        flow.send('actions', t('prompt.actions'))
        return
      case 'follow-up-email':
        flow.send('email_draft', t('prompt.email_draft'))
        return
    }
  }
  return (
    <>
      <MeetingAssistantPanel
        readiness={readiness}
        turns={flow.turns.map((turn) => assistantTurnView(turn, t))}
        arrivedTurnId={flow.arrivedCommandId}
        loading={reading}
        pending={flow.pending}
        error={query.isError}
        canSend={flow.canSend}
        onRetry={() => {
          citation.close()
          flow.refresh()
        }}
        onAsk={(question) => flow.send('question', question)}
        onAction={action}
        onOpenCitation={openCitation}
        onClear={flow.requestDelete}
        canClear={flow.canDelete}
        onClose={onClose}
      >
        {flow.error && (
          <AssistantFailure
            error={flow.error}
            disabled={flow.pending || query.isFetching}
            onRetry={flow.retry}
          />
        )}
        {/* A turn still processing is settled by reading the conversation
            again. */}
        {flow.hasProcessingTurn && !flow.error && !query.isError && (
          <Button
            variant="ghost"
            size="sm"
            isDisabled={flow.pending || query.isFetching}
            onPress={flow.refresh}
          >
            {t('refresh')}
          </Button>
        )}
        {!reading && !query.isError && query.hasNextPage && (
          <Button
            variant="ghost"
            isDisabled={query.isFetching || flow.pending}
            onPress={() => void query.fetchNextPage()}
          >
            {t('older')}
          </Button>
        )}
      </MeetingAssistantPanel>
      <AssistantCitationDialog
        state={
          query.isFetching || query.isError
            ? { phase: 'closed' }
            : citation.state
        }
        onClose={citation.close}
      />
      <Dialog
        isOpen={flow.confirmDelete && !query.isError}
        title={t('delete.title')}
        appearance="app"
        onOpenChange={(open) => {
          if (!open) flow.cancelDelete()
        }}
      >
        <p>{t('delete.description')}</p>
        {flow.error && (
          <>
            <p role="alert">{t(`command.${flow.error}`)}</p>
            <Button
              variant="secondary"
              isDisabled={flow.pending || query.isFetching}
              onPress={flow.retry}
            >
              {t('command.retry')}
            </Button>
          </>
        )}
        <Button
          variant="ghost"
          isDisabled={flow.pending}
          onPress={flow.cancelDelete}
        >
          {t('delete.cancel')}
        </Button>
        <Button
          variant="destructive"
          loading={flow.pending}
          isDisabled={flow.pending || query.isFetching || Boolean(flow.error)}
          onPress={flow.remove}
        >
          {t('delete.confirm')}
        </Button>
      </Dialog>
    </>
  )
}
