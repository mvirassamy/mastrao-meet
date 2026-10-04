import { LimitReachedAlertDialog } from './LimitReachedAlertDialog'
import { RecordingStateToast } from './RecordingStateToast'
import { ErrorAlertDialog } from './ErrorAlertDialog'

export const RecordingProvider = ({
  hideVisual = false,
}: {
  hideVisual?: boolean
}) => {
  return (
    <>
      <RecordingStateToast hideVisual={hideVisual} />
      <LimitReachedAlertDialog />
      <ErrorAlertDialog />
    </>
  )
}
