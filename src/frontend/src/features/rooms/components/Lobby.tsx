import { useEffect, useRef } from 'react'
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
import { NativeRecordingConsent } from './NativeRecordingConsent'
import { ManagedTranscriptionNotice } from './ManagedTranscriptionNotice'
import { navigateTo } from '@/navigation/navigateTo'
import { useMeetingLifecycle } from '../contexts/MeetingLifecycleContext'

// An open lifecycle does not guarantee that the next lobby or room request
// succeeds (masked 404): pace the retry so a persistent 404 never loops hot.
const OPEN_LIFECYCLE_RETRY_MS = 1000

const navigateToEndedMeeting = (roomId: string) =>
  navigateTo(
    'feedback',
    { outcome: 'ended', roomId },
    {
      replace: true,
      state: { room_id: roomId },
    }
  )

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
  const { phase, canonicalLifecycle, reconcileLifecycle } =
    useMeetingLifecycle()
  const phaseRef = useRef(phase)
  phaseRef.current = phase

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
    if (phaseRef.current !== 'active') return
    queryClient.setQueryData([keys.room, roomId], {
      ...roomData,
      livekit: response.livekit,
      ...(response.recording ? { recording: response.recording } : {}),
      ...(response.native_capture
        ? { native_capture: response.native_capture }
        : {}),
    })
    enterRoom()
  }

  const { status, startWaiting } = useLobby({
    roomId,
    username: username || user?.full_name || 'anonymous',
    onAccepted: handleAccepted,
  })

  const isRoomMissing =
    isError && ['404', '410'].includes(String(error?.statusCode))
  const needsCanonicalCheck =
    isMastraoRoomId(roomId) &&
    (status === ApiLobbyStatus.ENDED || isRoomMissing)

  useEffect(() => {
    if (needsCanonicalCheck) reconcileLifecycle()
    else if (isRoomMissing) enterRoom()
  }, [
    needsCanonicalCheck,
    error,
    status,
    reconcileLifecycle,
    isRoomMissing,
    enterRoom,
  ])

  useEffect(() => {
    if (canonicalLifecycle?.state === 'ended' || phase === 'ended') {
      navigateToEndedMeeting(roomId)
      return
    }
    if (
      !needsCanonicalCheck ||
      canonicalLifecycle?.state !== 'open' ||
      phase !== 'active'
    )
      return
    // Pace admission retries even when the authority remains open behind a masked 404.
    const timer = setTimeout(() => {
      if (status === ApiLobbyStatus.ENDED) startWaiting()
      if (isRoomMissing) void refetchRoom()
    }, OPEN_LIFECYCLE_RETRY_MS)
    return () => clearTimeout(timer)
  }, [
    canonicalLifecycle,
    phase,
    needsCanonicalCheck,
    roomId,
    status,
    startWaiting,
    isRoomMissing,
    refetchRoom,
  ])

  const { openLoginHint } = useLoginHint()

  const handleSubmit = async () => {
    if (phase !== 'active') return

    const { data, error: roomError } = await refetchRoom()
    if (phaseRef.current !== 'active') return

    if (
      ['404', '410'].includes(String(roomError?.statusCode)) &&
      isMastraoRoomId(roomId)
    ) {
      reconcileLifecycle()
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

  if (
    phase !== 'active' ||
    (needsCanonicalCheck && !canonicalLifecycle) ||
    canonicalLifecycle?.state === 'ending'
  ) {
    return (
      <VStack alignItems="center" textAlign="center">
        <H lvl={1} margin={false} centered>
          {t('ending.title')}
        </H>
        <Text as="p" variant="note">
          {t('ending.body')}
        </Text>
        <Spinner />
      </VStack>
    )
  }

  if (shouldWaitForCanonicalRoom(roomId, isPending)) {
    return <Spinner />
  }

  const recording = roomData?.recording
  if (roomData?.native_capture && !roomData.native_capture.decision) {
    return (
      <NativeRecordingConsent
        roomId={roomId}
        projection={roomData.native_capture}
        transcriptionProfileRef={recording?.transcription_profile_ref}
        onDecided={async () => {
          const result = await refetchRoom()
          if (result.isError) throw result.error
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
      if (isMastraoRoomId(roomId)) {
        return (
          <VStack alignItems="center" textAlign="center">
            <H lvl={1} margin={false} centered>
              {t('ending.title')}
            </H>
            <Text as="p" variant="note">
              {t('ending.body')}
            </Text>
            <Spinner />
          </VStack>
        )
      }
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
            <ManagedTranscriptionNotice
              profileRef={recording?.transcription_profile_ref}
            />
          </VStack>
        </Form>
      )
  }
}
