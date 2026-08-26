import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { useSnapshot } from 'valtio'
import { css } from '@/styled-system/css'
import { VStack } from '@/styled-system/jsx'
import { H } from '@/primitives/H'
import { Field } from '@/primitives/Field'
import { Form, Text } from '@/primitives'
import { Spinner } from '@/primitives/Spinner'
import { keys } from '@/api/queryKeys'
import { queryClient } from '@/api/queryClient'
import { useLoginHint } from '@/hooks/useLoginHint'
import { useUser } from '@/features/auth/api/useUser'
import { useConfig } from '@/api/useConfig'
import { saveUsername, userStore } from '@/stores/user'
import { fetchRoom } from '../api/fetchRoom'
import { ApiAccessLevel, type ApiRoom } from '../api/ApiRoom'
import { ApiLobbyStatus, type ApiRequestEntry } from '../api/requestEntry'
import { useLobby } from '../hooks/useLobby'
import {
  isMastraoRoomId,
  shouldWaitForCanonicalRoom,
} from '../utils/isRoomValid'
import { RecordingConsent } from './RecordingConsent'
import { fetchRoomLifecycle } from '../api/fetchRoomLifecycle'
import { navigateTo } from '@/navigation/navigateTo'

export const Lobby = ({
  roomId,
  enterRoom,
}: {
  roomId: string
  enterRoom: () => void
}) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'join' })

  const { data: configData } = useConfig()
  const { isLoggedIn, user } = useUser()
  const { username } = useSnapshot(userStore)
  const [canonicalLifecycle, setCanonicalLifecycle] = useState<
    'checking' | 'open' | 'ending' | 'ended'
  >('open')

  // Room data strategy:
  // 1. Initial fetch is performed to check access and get LiveKit configuration
  // 2. Data remains valid for 6 hours to avoid unnecessary refetches
  // 3. State is manually updated via queryClient when a waiting participant is accepted
  // 4. No automatic refetching or revalidation occurs during this period
  const {
    data: roomData,
    error,
    isError,
    isPending,
    refetch: refetchRoom,
  } = useQuery({
    queryKey: [keys.room, roomId],
    queryFn: () => fetchRoom({ roomId, username: username || user?.full_name }),
    staleTime: 6 * 60 * 60 * 1000, // By default, LiveKit access tokens expire 6 hours after generation
    retry: false,
    enabled: isMastraoRoomId(roomId),
    refetchInterval: (query) => {
      const state = (query.state.data as ApiRoom | undefined)?.recording
        ?.recording_state
      return state &&
        [
          'collecting',
          'authorized',
          'starting',
          'active',
          'stopping',
          'processing',
        ].includes(state)
        ? 1000
        : false
    },
    refetchIntervalInBackground: true,
  })

  const handleAccepted = (response: ApiRequestEntry) => {
    queryClient.setQueryData([keys.room, roomId], {
      ...roomData,
      livekit: response.livekit,
      ...(response.recording ? { recording: response.recording } : {}),
    })
    enterRoom()
  }

  const { status, startWaiting } = useLobby({
    roomId,
    username: username || user?.full_name || 'anonymous',
    onAccepted: handleAccepted,
  })

  useEffect(() => {
    if (isError && ['404', '410'].includes(String(error?.statusCode))) {
      if (isMastraoRoomId(roomId)) {
        setCanonicalLifecycle('checking')
        let cancelled = false
        let timer: ReturnType<typeof setTimeout> | undefined
        const controller = new AbortController()
        const reconcile = async () => {
          try {
            const lifecycle = await fetchRoomLifecycle(
              roomId,
              controller.signal
            )
            if (cancelled) return
            setCanonicalLifecycle(lifecycle.state)
            if (lifecycle.state === 'ended') {
              navigateTo(
                'feedback',
                { outcome: 'ended', roomId },
                {
                  replace: true,
                  state: { room_id: roomId },
                }
              )
              return
            }
          } catch {
            // Preserve the safe waiting state while authority is unavailable.
          }
          if (!cancelled) timer = setTimeout(reconcile, 1000)
        }
        void reconcile()
        return () => {
          cancelled = true
          controller.abort()
          if (timer) clearTimeout(timer)
        }
      }
      // The room component will handle the room creation if the user is authenticated
      enterRoom()
    }
  }, [isError, error, enterRoom, roomId])

  const { openLoginHint } = useLoginHint()

  const handleSubmit = async () => {
    const { data, error: roomError } = await refetchRoom()

    if (
      ['404', '410'].includes(String(roomError?.statusCode)) &&
      isMastraoRoomId(roomId)
    ) {
      const lifecycle = await fetchRoomLifecycle(roomId).catch(() => null)
      if (lifecycle?.state === 'ended') {
        navigateTo(
          'feedback',
          { outcome: 'ended', roomId },
          {
            replace: true,
            state: { room_id: roomId },
          }
        )
      } else if (lifecycle) {
        setCanonicalLifecycle(lifecycle.state)
      }
      return
    }

    if (!data?.livekit) {
      // Display a message to inform the user that by logging in, they won't have to wait for room entry approval.
      if (data?.access_level == ApiAccessLevel.TRUSTED) {
        openLoginHint()
      }
      startWaiting()
      return
    }

    enterRoom()
  }

  if (shouldWaitForCanonicalRoom(roomId, isPending)) {
    return <Spinner />
  }

  if (canonicalLifecycle === 'checking' || canonicalLifecycle === 'ending') {
    return (
      <VStack alignItems="center" textAlign="center">
        <H lvl={1} margin={false} centered>
          {t('ended.title')}
        </H>
        <Spinner />
      </VStack>
    )
  }

  const recording = roomData?.recording
  if (
    recording?.mode === 'recorded' &&
    recording.recording_state === 'stopping'
  ) {
    return (
      <VStack alignItems="center" textAlign="center">
        <H lvl={1} margin={false} centered>
          {t('recordingStopping.title')}
        </H>
        <Text as="p" variant="note">
          {t('recordingStopping.body')}
        </Text>
        <Spinner />
      </VStack>
    )
  }

  if (
    recording?.mode === 'recorded' &&
    (recording.decision === 'absent' ||
      (recording.transcription_mode === 'transcribed' &&
        recording.transcription_decision === 'absent')) &&
    ['collecting', 'authorized', 'starting', 'active'].includes(
      recording.recording_state ?? ''
    ) &&
    recording.retention_expires_at
  ) {
    return (
      <RecordingConsent
        roomId={roomId}
        retentionExpiresAt={recording.retention_expires_at}
        participantKind={recording.participant_kind}
        transcriptionOffered={recording.transcription_mode === 'transcribed'}
        recordingDecision={recording.decision}
        transcriptionDecision={recording.transcription_decision}
        onDecided={async () => {
          await refetchRoom()
        }}
      />
    )
  }

  if (recording?.mode === 'unset') {
    return (
      <VStack alignItems="center" textAlign="center">
        <H lvl={1} margin={false} centered>
          {t('recordingUnavailable.title')}
        </H>
        <Text as="p" variant="note" role="alert">
          {t('recordingUnavailable.body')}
        </Text>
      </VStack>
    )
  }

  switch (status) {
    case ApiLobbyStatus.ENDED:
      return (
        <VStack alignItems="center" textAlign="center">
          <H lvl={1} margin={false} centered>
            {t('ended.title')}
          </H>
          <Text as="p" variant="note">
            {t('ended.body')}
          </Text>
        </VStack>
      )

    case ApiLobbyStatus.TIMEOUT:
      return (
        <VStack alignItems="center" textAlign="center">
          <H lvl={1} margin={false} centered>
            {t('timeoutInvite.title')}
          </H>
          <Text as="p" variant="note">
            {t('timeoutInvite.body')}
          </Text>
        </VStack>
      )

    case ApiLobbyStatus.DENIED:
      return (
        <VStack alignItems="center" textAlign="center">
          <H lvl={1} margin={false} centered>
            {t('denied.title')}
          </H>
          <Text as="p" variant="note">
            {t('denied.body')}
          </Text>
        </VStack>
      )

    case ApiLobbyStatus.WAITING:
      return (
        <VStack alignItems="center" textAlign="center">
          <H lvl={1} margin={false} centered>
            {t('waiting.title')}
          </H>
          <Text
            as="p"
            variant="note"
            className={css({ marginBottom: '1.5rem' })}
          >
            {t('waiting.body')}
          </Text>
          <Spinner />
        </VStack>
      )

    default:
      return (
        <Form
          onSubmit={handleSubmit}
          submitLabel={t('joinLabel')}
          submitButtonProps={{
            fullWidth: true,
          }}
        >
          <VStack marginBottom={1}>
            <H lvl={1} margin="sm" centered>
              {t('heading')}
            </H>
            {(!isLoggedIn ||
              configData?.authenticated_users_can_edit_display_name) && (
              <Field
                type="text"
                onChange={saveUsername}
                label={t('usernameLabel')}
                aria-label={t('usernameLabel')}
                id="input-name"
                defaultValue={username || user?.full_name}
                validate={(value) => !value && t('errors.usernameEmpty')}
                wrapperProps={{
                  noMargin: true,
                  fullWidth: true,
                }}
                autoComplete="name"
                maxLength={50}
              />
            )}
          </VStack>
        </Form>
      )
  }
}
