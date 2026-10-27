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
from core.mastrao_guest_invitation_share import create_guest_invitation_share
from core.mastrao_guest_share_link import consume_guest_share_link
from core.mastrao_host_handoff import consume_mastrao_host_handoff
from core.mastrao_host_invitation import (
    establish_mastrao_host_invitation_session,
    exchange_mastrao_host_invitation,
)
from core.mastrao_host_recovery import recover_meeting_host
from core.mastrao_meeting_assistant import meeting_assistant, meeting_assistant_citation
from core.mastrao_meeting_classification import meeting_classification, meeting_matters
from core.mastrao_meeting_history import (
    meeting_collection,
    meeting_history,
    meeting_history_detail,
    meeting_summary,
)
from core.mastrao_native_capture_adapter import start_native_capture
from core.mastrao_native_capture_stop import stop_native_capture
from core.mastrao_note_enrichment import personal_note_enrichment
from core.mastrao_personal_notes import history_personal_note, room_personal_note
from core.mastrao_recording_access import recording_access, recording_download
from core.mastrao_recording_adapter import (
    start_mastrao_recording,
    stop_mastrao_recording,
)
from core.mastrao_room_adapter import ensure_mastrao_room
from core.mastrao_room_audio_authority import dispatch_room_audio_track_effect
from core.mastrao_room_close_adapter import close_mastrao_room
from core.mastrao_speaker_evidence_adapter import capture_mastrao_speaker_evidence
from core.mastrao_transcription_adapter import transcribe_mastrao_recording
from core.mastrao_usage_budget import usage_budget
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
    path(
        "internal/mastrao/room-audio/tracks/dispatch/",
        dispatch_room_audio_track_effect,
        name="dispatch_room_audio_track_effect",
    ),
    path(
        "internal/mastrao/captures/native/stop/",
        stop_native_capture,
        name="stop_native_capture",
    ),
    path(
        "internal/mastrao/captures/native/start/",
        start_native_capture,
        name="start_native_capture",
    ),
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
        "handoff/guest/share/",
        consume_guest_share_link,
        name="consume_guest_share_link",
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
        "handoff/host/invitation/session/",
        establish_mastrao_host_invitation_session,
        name="establish_mastrao_host_invitation_session",
    ),
    path(
        "handoff/host/invitation/",
        exchange_mastrao_host_invitation,
        name="exchange_mastrao_host_invitation",
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
                path("usage-budget/", usage_budget, name="mastrao_usage_budget"),
                path(
                    "rooms/<str:room_ref>/personal-note/",
                    room_personal_note,
                    name="mastrao_room_personal_note",
                ),
                path(
                    "meetings/history/<str:meeting_ref>/personal-note/",
                    history_personal_note,
                    name="mastrao_history_personal_note",
                ),
                path(
                    "meetings/history/<str:meeting_ref>/personal-note/enrichment/",
                    personal_note_enrichment,
                    name="mastrao_personal_note_enrichment",
                ),
                path("meetings/", meeting_collection, name="mastrao_create_meeting"),
                path("matters/", meeting_matters, name="mastrao_meeting_matters"),
                path(
                    "meetings/history/<str:meeting_ref>/assistant/",
                    meeting_assistant,
                    name="mastrao_meeting_assistant",
                ),
                path(
                    "meetings/history/<str:meeting_ref>/assistant/citation/",
                    meeting_assistant_citation,
                    name="mastrao_meeting_assistant_citation",
                ),
                path(
                    "meetings/history/<str:meeting_ref>/classification/",
                    meeting_classification,
                    name="mastrao_meeting_classification",
                ),
                path(
                    "rooms/<str:room_ref>/host-handoff/",
                    recover_meeting_host,
                    name="recover_meeting_host",
                ),
                path(
                    "rooms/<str:room_ref>/guest-invitation/",
                    create_guest_invitation_share,
                    name="mastrao_create_guest_invitation_share",
                ),
                path(
                    "meetings/history/",
                    meeting_history,
                    name="mastrao_meeting_history",
                ),
                path(
                    "meetings/history/<str:meeting_ref>/",
                    meeting_history_detail,
                    name="mastrao_meeting_history_detail",
                ),
                path(
                    "meetings/history/<str:meeting_ref>/summary/",
                    meeting_summary,
                    name="mastrao_meeting_summary",
                ),
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
