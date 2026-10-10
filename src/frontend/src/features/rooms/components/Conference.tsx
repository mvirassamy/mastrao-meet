import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { LiveKitRoom } from '@livekit/components-react'
import {
  DisconnectReason,
  MediaDeviceFailure,
  Room,
  type RoomOptions,
  VideoPresets,
} from 'livekit-client'
import { keys } from '@/api/queryKeys'
import { queryClient } from '@/api/queryClient'
import { Screen } from '@/layout/Screen'
import { QueryAware } from '@/components/QueryAware'
import { ErrorScreen } from '@/components/ErrorScreen'
import { fetchRoom } from '../api/fetchRoom'
import type { ApiRoom } from '../api/ApiRoom'
import { useCreateRoom } from '../api/createRoom'
import { InviteDialog } from './InviteDialog'
import { VideoConference } from '../livekit/prefabs/VideoConference'
import { css } from '@/styled-system/css'
import { BackgroundProcessorFactory } from '../livekit/components/blur'
import { userChoicesStore } from '@/stores/userChoices'
import { captureMediaEvent, reportError } from '@/features/analytics/telemetry'
import { useConfig } from '@/api/useConfig'
import { isFireFox } from '@/utils/livekit'
import { useIsMobile } from '@/utils/useIsMobile'
import { navigateTo } from '@/navigation/navigateTo'
import { connectionObserverStore } from '@/stores/connectionObserver'
import { PictureInPictureConference } from '@/features/pip/components/PictureInPictureConference'
import { notifyAutoMutedOnJoin } from '@/features/notifications/utils'
import { useSnapshot } from 'valtio'
import { userPreferencesStore } from '@/stores/userPreferences'
import { userStore } from '@/stores/user'
import { WatchMediaDeviceErrors } from './WatchMediaDeviceErrors'
import { useMeetingLifecycle } from '../contexts/MeetingLifecycleContext'
import {
  cachePlatformReturn,
  readCachedPlatformReturn,
} from '../platformReturn'
import { isMastraoRoomId } from '../utils/isRoomValid'

const ActiveInviteDialog = ({ mode }: { mode: 'join' | 'create' }) => {
  const { isEnding } = useMeetingLifecycle()
  return isEnding ? null : <InviteDialog mode={mode} />
}

export const Conference = ({
  roomId,
  initialRoomData,
  mode = 'join',
}: {
  roomId: string
  mode?: 'join' | 'create'
  initialRoomData?: ApiRoom
}) => {
  const { phase, isEnding, canonicalLifecycle, markEndingUncertain } =
    useMeetingLifecycle()
  const { data: apiConfig } = useConfig()

  const userConfig = useSnapshot(userChoicesStore)

  const { username } = useSnapshot(userStore)

  useEffect(() => {
    void captureMediaEvent('visit-room', { slug: roomId })
  }, [roomId])
  const fetchKey = [keys.room, roomId]

  const [isConnectionWarmedUp, setIsConnectionWarmedUp] = useState(false)

  const userPreferencesSnap = useSnapshot(userPreferencesStore)

  const {
    mutateAsync: createRoom,
    status: createStatus,
    isError: isCreateError,
  } = useCreateRoom({
    onSuccess: (data) => {
      queryClient.setQueryData(fetchKey, data)
    },
  })

  const {
    status: fetchStatus,
    isError: isFetchError,
    data,
    refetch: refetchRoom,
  } = useQuery({
    queryKey: fetchKey,
    staleTime: 6 * 60 * 60 * 1000, // By default, LiveKit access tokens expire 6 hours after generation
    initialData: initialRoomData,
    queryFn: async () => {
      try {
        return await fetchRoom({
          roomId: roomId as string,
          username: username,
        })
      } catch (error) {
        const statusCode = String(
          (error as { statusCode?: string | number }).statusCode
        )
        if (isMastraoRoomId(roomId) && ['404', '410'].includes(statusCode)) {
          markEndingUncertain()
          const currentRoom = queryClient.getQueryData<ApiRoom>(fetchKey)
          if (currentRoom) return currentRoom
          throw error
        }
        if (statusCode === '404') {
          return createRoom({ slug: roomId, username })
        }
        throw error
      }
    },
    retry: false,
    refetchInterval: (query) => {
      if (isEnding) return false
      const state = (query.state.data as ApiRoom | undefined)?.recording
        ?.recording_state
      if (state === 'active') return 2000
      if (
        !state ||
        ![
          'collecting',
          'authorized',
          'starting',
          'stopping',
          'processing',
        ].includes(state)
      )
        return false
      return 1000
    },
    refetchIntervalInBackground: true,
  })

  const refetchRecording = useCallback(async () => {
    const result = await refetchRoom()
    return result.data?.recording?.video
  }, [refetchRoom])

  useEffect(() => {
    const refetchAfterReconnect = () => {
      if (!isEnding) void refetchRoom()
    }
    window.addEventListener('online', refetchAfterReconnect)
    return () => window.removeEventListener('online', refetchAfterReconnect)
  }, [isEnding, refetchRoom])

  useEffect(() => {
    if (data?.platform_return) {
      cachePlatformReturn(
        roomId,
        data.platform_return,
        apiConfig?.mastrao_platform_origin
      )
    }
  }, [apiConfig?.mastrao_platform_origin, data?.platform_return, roomId])

  const navigateToEndedMeeting = useCallback(() => {
    queryClient.removeQueries({ queryKey: [keys.room, roomId], exact: true })
    const platformOrigin = apiConfig?.mastrao_platform_origin
    navigateTo(
      'feedback',
      { outcome: 'ended', roomId },
      {
        replace: true,
        state: {
          reason: DisconnectReason.ROOM_DELETED,
          room_id: roomId,
          platform_return:
            data?.platform_return ??
            (platformOrigin
              ? readCachedPlatformReturn(roomId, platformOrigin)
              : undefined),
        },
      }
    )
  }, [apiConfig?.mastrao_platform_origin, data?.platform_return, roomId])

  useEffect(() => {
    if (canonicalLifecycle?.state === 'ended' || phase === 'ended') {
      navigateToEndedMeeting()
    }
  }, [canonicalLifecycle, phase, navigateToEndedMeeting])

  // Device controls update active tracks themselves. Replacing Room when a
  // preference changes makes LiveKitRoom disconnect the ongoing conference.
  const [room] = useState(() => {
    const roomOptions: RoomOptions = {
      adaptiveStream: true,
      dynacast: true,
      publishDefaults: {
        videoCodec: 'h264',
      },
      videoCaptureDefaults: {
        deviceId: userConfig.videoDeviceId ?? undefined,
        resolution: userConfig.videoPublishResolution
          ? VideoPresets[userConfig.videoPublishResolution].resolution
          : undefined,
      },
      audioCaptureDefaults: {
        deviceId: userConfig.audioDeviceId ?? undefined,
      },
      audioOutput: {
        deviceId: userConfig.audioOutputDeviceId ?? undefined,
      },
    }
    return new Room(roomOptions)
  })

  useEffect(() => {
    /**
     * Warm up connection to LiveKit server before joining room
     * This prefetch helps reduce initial connection latency by establishing
     * an early HTTP connection to the WebRTC signaling server
     *
     * It should cache DNS and TLS keys.
     */
    const prepareConnection = async () => {
      if (!apiConfig || isConnectionWarmedUp) return
      await room.prepareConnection(apiConfig.livekit.url)

      if (isFireFox() && apiConfig.livekit.enable_firefox_proxy_workaround) {
        try {
          const wssUrl =
            apiConfig.livekit.url
              .replace('https://', 'wss://')
              .replace(/\/$/, '') + '/rtc'

          /**
           * FIREFOX + PROXY WORKAROUND:
           *
           * Issue: On Firefox behind proxy configurations, WebSocket signaling fails to establish.
           * Symptom: Client receives HTTP 200 instead of expected 101 (Switching Protocols).
           * Root Cause: Certificate/security issue where the initial request is considered unsecure.
           *
           * Solution: Pre-establish a WebSocket connection to the signaling server, which fails.
           * This "primes" the connection, allowing subsequent WebSocket establishments to work correctly.
           *
           * Note: This issue is reproducible on LiveKit's demo app.
           * Reference: livekit-examples/meet/issues/466
           */
          const ws = new WebSocket(wssUrl)
          // 401 unauthorized response is expected
          ws.onerror = () => ws.readyState <= 1 && ws.close()
        } catch (e) {
          console.debug('Firefox WebSocket workaround failed.', e)
        }
      }

      setIsConnectionWarmedUp(true)
    }
    prepareConnection()
  }, [room, apiConfig, isConnectionWarmedUp])

  const isMobile = useIsMobile()

  const hasAutoMutedRef = useRef(false)

  /*
   * Ensure stable WebSocket connection URL. This is critical for legacy browser compatibility
   * (Firefox <124, Chrome <125, Edge <125) where HTTPS URLs in WebSocket() constructor
   *  may fail - the force_wss_protocol flag allows explicit WSS protocol conversion
   */
  const serverUrl = useMemo(() => {
    const livekit_url = apiConfig?.livekit.url
    if (!livekit_url) return
    if (apiConfig?.livekit.force_wss_protocol) {
      return livekit_url.replace('https://', 'wss://')
    }
    return livekit_url
  }, [apiConfig?.livekit])

  const { t } = useTranslation('rooms')
  if (isCreateError) {
    // this error screen should be replaced by a proper waiting room for anonymous user.
    return (
      <ErrorScreen
        title={t('error.createRoom.heading')}
        body={t('error.createRoom.body')}
      />
    )
  }

  // Some clients (like DINUM) operate in bandwidth-constrained environments
  // These settings help ensure successful connections in poor network conditions
  const connectOptions = {
    maxRetries: 5, // Default: 1. Only for unreachable server scenarios
    peerConnectionTimeout: 60000, // Default: 15s. Extended for slow TURN/TLS negotiation
  }

  return (
    <QueryAware status={isFetchError ? createStatus : fetchStatus}>
      <Screen header={false} footer={false}>
        <LiveKitRoom
          room={room}
          serverUrl={serverUrl}
          token={data?.livekit?.token}
          connect={isConnectionWarmedUp}
          audio={userConfig.audioEnabled}
          video={
            userConfig.videoEnabled && {
              processor: BackgroundProcessorFactory.fromProcessorConfig(
                userConfig.processorConfig
              ),
            }
          }
          connectOptions={connectOptions}
          className={css({
            backgroundColor: 'background !important',
          })}
          onError={(e) => {
            reportError('livekit_room_error', e, {
              path: 'connect_publish',
              failure: MediaDeviceFailure.getFailure(e) ?? 'not-a-device-error',
            })
          }}
          onConnected={async () => {
            if (!apiConfig) return
            if (
              userPreferencesSnap.is_auto_mute_large_room_enabled &&
              !hasAutoMutedRef.current &&
              userConfig.audioEnabled &&
              room.numParticipants > apiConfig.auto_mute_on_join_threshold
            ) {
              hasAutoMutedRef.current = true
              await room.localParticipant.setMicrophoneEnabled(false)
              notifyAutoMutedOnJoin()
            }
          }}
          onDisconnected={(e) => {
            const metadata = {
              room_id: roomId,
              pc_publisher: connectionObserverStore.publisher && {
                ...connectionObserverStore.publisher,
              },
              pc_subscriber: connectionObserverStore.subscriber && {
                ...connectionObserverStore.subscriber,
              },
              pc_publisher_changes_count:
                connectionObserverStore.publisherChangesCount,
              pc_subscriber_changes_count:
                connectionObserverStore.subscriberChangesCount,
            }

            connectionObserverStore.publisher = null
            connectionObserverStore.publisherChangesCount = 0
            connectionObserverStore.subscriber = null
            connectionObserverStore.subscriberChangesCount = 0

            switch (e) {
              case DisconnectReason.ROOM_DELETED:
                if (!isMastraoRoomId(roomId)) {
                  queryClient.removeQueries({ queryKey: fetchKey, exact: true })
                  navigateTo(
                    'feedback',
                    { outcome: 'ended', roomId },
                    {
                      state: {
                        reason: e,
                        ...metadata,
                      },
                    }
                  )
                  return
                }
                markEndingUncertain()
                return
              case DisconnectReason.CLIENT_INITIATED:
                navigateTo(
                  'feedback',
                  { outcome: 'left', roomId },
                  {
                    state: {
                      platform_return:
                        data?.platform_return ??
                        readCachedPlatformReturn(
                          roomId,
                          apiConfig?.mastrao_platform_origin
                        ),
                      ...metadata,
                    },
                  }
                )
                return
              case DisconnectReason.DUPLICATE_IDENTITY:
                navigateTo(
                  'feedback',
                  { outcome: 'duplicate', roomId },
                  {
                    replace: true,
                    state: {
                      reason: e,
                      ...metadata,
                    },
                  }
                )
                return
              case DisconnectReason.PARTICIPANT_REMOVED:
                navigateTo(
                  'feedback',
                  { outcome: 'removed', roomId },
                  {
                    replace: true,
                    state: {
                      reason: e,
                      ...metadata,
                    },
                  }
                )
                return
            }
          }}
        >
          <WatchMediaDeviceErrors />
          <VideoConference
            roomId={roomId}
            canEnd={data?.can_end}
            recording={data?.recording}
            onRecordingChanged={refetchRecording}
          />
          {!isMobile && <ActiveInviteDialog mode={mode} />}
          <PictureInPictureConference
            roomId={roomId}
            canEnd={data?.can_end}
            onMeetingEnded={navigateToEndedMeeting}
          />
        </LiveKitRoom>
      </Screen>
    </QueryAware>
  )
}
