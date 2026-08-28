"""URL configuration for the core app."""

from django.conf import settings
from django.urls import include, path

from lasuite.oidc_login.urls import urlpatterns as oidc_urls
from rest_framework.routers import DefaultRouter, SimpleRouter

from core.addons import viewsets as addons_viewsets
from core.api import get_frontend_configuration, viewsets
from core.external_api import viewsets as external_viewsets
from core.mastrao_guest_handoff import (
    consume_mastrao_guest_invitation,
    establish_mastrao_guest_session,
)
from core.mastrao_host_handoff import consume_mastrao_host_handoff
from core.mastrao_recording_access import recording_access, recording_download
from core.mastrao_recording_adapter import (
    start_mastrao_recording,
    stop_mastrao_recording,
)
from core.mastrao_room_adapter import ensure_mastrao_room
from core.mastrao_room_close_adapter import close_mastrao_room
from core.mastrao_speaker_evidence_adapter import capture_mastrao_speaker_evidence
from core.mastrao_transcription_adapter import transcribe_mastrao_recording
from core.roomkit import viewsets as roomkit_viewsets

# - Main endpoints
router = DefaultRouter()
router.register("users", viewsets.UserViewSet, basename="users")
router.register("rooms", viewsets.RoomViewSet, basename="rooms")
router.register("recordings", viewsets.RecordingViewSet, basename="recordings")
router.register("files", viewsets.FileViewSet, basename="files")
router.register(
    "resource-accesses", viewsets.ResourceAccessViewSet, basename="resource_accesses"
)
router.register(
    "roomkit",
    roomkit_viewsets.RoomKitViewSet,
    basename="roomkit",
)
router.register(
    "addons/sessions",
    addons_viewsets.SessionViewSet,
    basename="addons_sessions",
)
router.register(
    "diagnostics",
    viewsets.DiagnosticsViewSet,
    basename="diagnostics",
)

# - External API
external_router = SimpleRouter()
external_router.register(
    "application",
    external_viewsets.ApplicationViewSet,
    basename="external_application",
)
external_router.register(
    "rooms",
    external_viewsets.RoomViewSet,
    basename="external_room",
)

urlpatterns = [
    path("recordings/access/", recording_access, name="mastrao_recording_access"),
    path(
        "recordings/download/current",
        recording_download,
        name="mastrao_recording_download",
    ),
    path(
        "handoff/guest/session/",
        establish_mastrao_guest_session,
        name="establish_mastrao_guest_session",
    ),
    path(
        "handoff/guest/",
        consume_mastrao_guest_invitation,
        name="consume_mastrao_guest_invitation",
    ),
    path(
        "handoff/host/",
        consume_mastrao_host_handoff,
        name="consume_mastrao_host_handoff",
    ),
    path(
        "internal/mastrao/rooms/ensure/",
        ensure_mastrao_room,
        name="ensure_mastrao_room",
    ),
    path(
        "internal/mastrao/rooms/close/",
        close_mastrao_room,
        name="close_mastrao_room",
    ),
    path(
        "internal/mastrao/recordings/start/",
        start_mastrao_recording,
        name="start_mastrao_recording",
    ),
    path(
        "internal/mastrao/recordings/stop/",
        stop_mastrao_recording,
        name="stop_mastrao_recording",
    ),
    path(
        "internal/mastrao/transcriptions/transcribe/",
        transcribe_mastrao_recording,
        name="transcribe_mastrao_recording",
    ),
    path(
        "internal/mastrao/speaker-evidence/capture/",
        capture_mastrao_speaker_evidence,
        name="capture_mastrao_speaker_evidence",
    ),
    path(
        f"api/{settings.API_VERSION}/",
        include(
            [
                *router.urls,
                *oidc_urls,
                path("config/", get_frontend_configuration, name="config"),
            ]
        ),
    ),
]

if settings.EXTERNAL_API_ENABLED:
    urlpatterns.append(
        path(
            f"external-api/{settings.EXTERNAL_API_VERSION}/",
            include(
                [
                    *external_router.urls,
                ]
            ),
        )
    )
