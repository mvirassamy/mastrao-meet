"""Actual Django/JOSE/HTTP/SQL with a synthetic Core transport peer (not SFU).

Core's canonical ledger/RLS is separately exercised by native-capture-runtime.
No bearer is queued and no media provider is called by these tests.
"""

# Imported pytest fixtures and generated LiveKit protobuf members are resolved dynamically.
# pylint: disable=broad-exception-caught,invalid-name,missing-class-docstring
# pylint: disable=missing-function-docstring,redefined-outer-name,too-many-arguments
# pylint: disable=too-many-positional-arguments,too-many-statements,unused-argument
# pylint: disable=unused-import,use-implicit-booleaness-not-comparison

import json
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.test import Client
from django.utils import timezone

import jwt
import pytest

from core import models
from core.mastrao_media_token_binding import generate_guest_media_config
from core.mastrao_native_admission import (
    OBSERVED_JOSE,
    OBSERVED_PATH,
    _claim_next,
    _finish,
    _session_label,
    reconcile_native_admissions,
    wake_native_admissions,
)
from core.mastrao_native_authorization import (
    AUTHORIZATION_JOSE,
    AUTHORIZATION_PATH,
    _validate_authorization,
    authorize_native_policy,
)
from core.mastrao_recording_contract import (
    RecordingContractRefused,
    _sign,
    compact_digest,
)
from core.mastrao_recording_session import activate_recording, recording_session_status
from core.services.lobby import LobbyParticipant, LobbyParticipantStatus
from core.tests.test_mastrao_media_token_binding import (
    _claims,
    _host_config,
    _request,
    binding,
    guest,
    host,
    isolated_binding_settings,
)
from core.tests.test_mastrao_native_capture import signer
from core.tests.test_mastrao_native_grant_interop import _grant_claims
from core.tests.test_mastrao_rtc_correlation import _assert_post, _event, _join

pytestmark = pytest.mark.django_db(transaction=True)


def authorization_projection():
    return {
        "version": 1,
        "capture_authorized": False,
        "authorization": {
            "policy_ref": "native_policy_fixture",
            "policy_version": "native_policy_version_fixture",
            "policy_digest": "d" * 64,
            "purpose": "meeting_transcription_source_audio",
            "scope": "authorized_microphone_track_epoch",
            "retention_expires_at": int(time.time()) + 3600,
        },
    }


def video_projection():
    """Canonical video policy remains separate from transcription policy."""
    return {
        "consultation_source": "present",
        "decision": "absent",
        "decision_basis": "pending",
        "start_status": "pending",
        "decision_lock": "open",
        "started_at": None,
        "start_requested": False,
        "start_available": True,
    }


@pytest.fixture
def native_settings(settings, signer):
    settings.MASTRAO_NATIVE_PREENTRY_ENABLED = True
    settings.MASTRAO_MEETING_INTEGRATION_CONFIGURED = True
    settings.MASTRAO_GUEST_HANDOFF_ENABLED = True
    settings.MASTRAO_ROOM_EFFECT_PUBLIC_JWK = (
        settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK
    )
    settings.MASTRAO_ROOM_EFFECT_KEY_ID = settings.MASTRAO_RECORDING_RECEIPT_KEY_ID
    settings.MASTRAO_ROOM_EFFECT_ISSUER = "core-fixture"
    settings.MASTRAO_ROOM_EFFECT_AUDIENCE = "meet-fixture"
    return settings


def browser_session(client, grant, kind):
    compact = _sign(_grant_claims(grant, kind), f"mastrao-meeting-{kind}-grant+jws")
    grant.grant_digest = compact_digest(compact)
    grant.save(update_fields=["grant_digest", "updated_at"])
    if kind == "host":
        client.force_login(
            grant.identity.user,
            backend="core.authentication.handoff.MastraoHostAuthenticationBackend",
        )
    session = client.session
    if kind == "host":
        session.update(_request(grant).session)
        session["mastrao_host_compact_grants"] = {grant.grant_ref: compact}
    else:
        session.update(
            {
                "mastrao_guest_session_nonce": "guest-fixture-nonce-" * 3,
                "mastrao_guest_grant_ref": grant.grant_ref,
                "mastrao_guest_compact_grant": compact,
            }
        )
    session.save()


@contextmanager
def core_peer(settings, *, lose_first=False, denied=False, session_policy=False):
    calls, errors, captures = [], [], {}
    authorization = authorization_projection()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/internal/v1/meetings/recording/session-status":
                    assert session_policy
                    assert set(body) == {
                        "participant_grant",
                        "participant_session_digest",
                    }
                    claims = jwt.decode(
                        body["participant_grant"],
                        jwt.algorithms.OKPAlgorithm.from_jwk(
                            settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK
                        ),
                        algorithms=["EdDSA"],
                    )
                    result = {
                        "version": 1,
                        **{
                            key: claims[key]
                            for key in (
                                "organization_external_id",
                                "meeting_ref",
                                "room_ref",
                            )
                        },
                        "mode": "recorded",
                        "recording_ref": "recording_native_fixture",
                        "policy_ref": "legacy_policy_fixture",
                        "notice_version": settings.MASTRAO_RECORDING_NOTICE_VERSION,
                        "notice_digest": settings.MASTRAO_RECORDING_NOTICE_DIGEST,
                        "purpose": "meeting_recording",
                        "scope": "room_composite_audio_video_screen",
                        "retention_expires_at": int(time.time()) + 3600,
                        "recording_state": "collecting",
                        "video": video_projection(),
                        "decision": "absent",
                        "transcription_mode": "transcribed",
                        "transcription_notice_version": "transcription_notice_fixture",
                        "transcription_notice_digest": "d" * 64,
                        "transcription_decision": "absent",
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(result).encode())
                    return
                compact = body.get("request") or body["candidate"]
                expected = (
                    AUTHORIZATION_JOSE
                    if self.path == AUTHORIZATION_PATH
                    else OBSERVED_JOSE
                )
                assert jwt.get_unverified_header(compact)["typ"] == expected
                claims = jwt.decode(
                    compact,
                    jwt.algorithms.OKPAlgorithm.from_jwk(
                        settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK
                    ),
                    algorithms=["EdDSA"],
                )
                assert (
                    claims["issuer"] == "meet-fixture"
                    and claims["audience"] == "core-fixture"
                )
                calls.append((self.path, claims, set(body)))
                code = 200
                if self.path == AUTHORIZATION_PATH:
                    assert set(body) == {"request", "participant_grant"}
                    assert "action" not in claims and "decision" not in claims
                    result = authorization
                else:
                    assert self.path == OBSERVED_PATH and set(body) == {"candidate"}
                    assert "consent" not in claims and "participant_grant" not in claims
                    capture = captures.setdefault(claims["epoch_ref"], str(uuid4()))
                    result = {
                        "version": 1,
                        "capture_ref": capture,
                        "epoch_ref": claims["epoch_ref"],
                        "state": "pending",
                        "media_durability_proven": False,
                    }
                    if denied:
                        code, result = 404, {"error": "unavailable"}
                    elif lose_first and len(calls) == 1:
                        code, result = 503, {"error": "synthetic lost acknowledgement"}
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
            except Exception as error:  # noqa: BLE001
                # Propagate peer assertion failures to the main test.
                errors.append(str(error))
                self.send_error(500)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    settings.MASTRAO_CORE_NATIVE_AUTHORIZATION_ENDPOINT = origin + AUTHORIZATION_PATH
    settings.MASTRAO_CORE_NATIVE_OBSERVED_ENDPOINT = origin + OBSERVED_PATH
    if session_policy:
        settings.MASTRAO_CORE_RECORDING_SESSION_STATUS_ENDPOINT = (
            origin + "/internal/v1/meetings/recording/session-status"
        )
    try:
        yield calls, authorization
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert errors == []


@pytest.mark.parametrize("kind", ["host", "guest"])
def test_first_native_entry_authorizes_policy_and_admits_without_video(
    client, native_settings, host, guest, kind
):
    """Real session verification, HTTP and PG; Core peer/RTC are synthetic."""
    native_settings.MASTRAO_MEETING_RECORDING_ENABLED = False
    native_settings.MASTRAO_RECORDING_NOTICE_VERSION = "legacy_notice_fixture"
    native_settings.MASTRAO_RECORDING_NOTICE_DIGEST = "a" * 64
    grant = host if kind == "host" else guest
    browser_session(client, grant, kind)
    request = SimpleNamespace(
        user=host.identity.user if kind == "host" else AnonymousUser(),
        session=client.session,
    )
    room = grant.room_binding.room
    assert not models.MastraoRecordingBinding.objects.exists()
    with core_peer(native_settings, session_policy=True) as (calls, _):
        status = recording_session_status(request, room)
        assert status is not None
        policy = models.MastraoRecordingBinding.objects.get(
            room_binding=grant.room_binding
        )
        assert policy.organization_external_id == "organization_media_fixture"
        assert policy.recording_id is None
        result = authorize_native_policy(request, room, status)
        assert result["authorization"]["scope"] == ("authorized_microphone_track_epoch")
        assert authorize_native_policy(request, room, status) == result
        changed_status = {**status, "transcription_notice_digest": "e" * 64}
        assert authorize_native_policy(request, room, changed_status) == result
        assert (
            recording_session_status(request, room)["recording_ref"]
            == policy.recording_ref
        )
        assert models.MastraoRecordingBinding.objects.count() == 1
        with pytest.raises(RecordingContractRefused):
            activate_recording(request, room, "activation_native_refused")
        with patch("core.mastrao_rtc_observations.wake_native_admissions"):
            epoch_for(client, native_settings, grant, kind)
        assert reconcile_native_admissions() == 1
        assert [call[0] for call in calls] == [
            AUTHORIZATION_PATH,
            AUTHORIZATION_PATH,
            OBSERVED_PATH,
        ]
        assert calls[0][1]["participant_kind"] == kind
        assert calls[1][1]["participant_kind"] == kind
        assert not models.Recording.objects.exists()


@pytest.mark.parametrize("kind", ["host", "guest"])
def test_native_preentry_has_no_browser_decision_or_public_projection(
    native_settings, host, guest, kind
):
    native_settings.MASTRAO_MEETING_RECORDING_ENABLED = False
    native_settings.MASTRAO_RECORDING_NOTICE_VERSION = "legacy_notice_fixture"
    native_settings.MASTRAO_RECORDING_NOTICE_DIGEST = "a" * 64
    client = Client(enforce_csrf_checks=True)
    grant = host if kind == "host" else guest
    browser_session(client, grant, kind)
    base = f"/api/v1.0/rooms/{grant.room_binding.room_id}/"
    with (
        core_peer(native_settings, session_policy=True) as (calls, _),
        patch("core.utils.notify_participants"),
        patch("core.services.lobby.ensure_livekit_room"),
    ):
        page = client.get(base)
        assert page.status_code == 200, page.content
        assert "native_capture" not in page.json()
        authorization_calls = [call for call in calls if call[0] == AUTHORIZATION_PATH]
        if kind == "host":
            assert len(authorization_calls) == 1
            refreshed = client.get(base)
            assert refreshed.status_code == 200, refreshed.content
            assert len([call for call in calls if call[0] == AUTHORIZATION_PATH]) == 1
        else:
            assert authorization_calls == []
            waiting = LobbyParticipant(
                status=LobbyParticipantStatus.WAITING,
                username="Guest",
                id=grant.guest_ref,
                color="#112233",
            )
            with patch(
                "core.api.viewsets.LobbyService.request_entry",
                return_value=(waiting, None),
            ):
                entry = client.post(base + "request-entry/", {"username": "Guest"})
            assert entry.status_code == 200, entry.content
            assert entry.json()["livekit"] is None
            assert [call for call in calls if call[0] == AUTHORIZATION_PATH] == []
            accepted = LobbyParticipant(
                status=LobbyParticipantStatus.ACCEPTED,
                username="Guest",
                id=grant.guest_ref,
                color="#112233",
            )
            with patch(
                "core.api.viewsets.LobbyService.request_entry",
                return_value=(accepted, {"token": "guest-livekit-token"}),
            ):
                admitted = client.post(base + "request-entry/", {"username": "Guest"})
            assert admitted.status_code == 200, admitted.content
            assert admitted.json()["livekit"]
            assert len([call for call in calls if call[0] == AUTHORIZATION_PATH]) == 1
            with patch(
                "core.api.viewsets.LobbyService.request_entry",
                return_value=(accepted, {"token": "refreshed-livekit-token"}),
            ):
                refreshed = client.post(base + "request-entry/", {"username": "Guest"})
            assert refreshed.status_code == 200, refreshed.content
            assert len([call for call in calls if call[0] == AUTHORIZATION_PATH]) == 1
        assert not models.MastraoNativeCaptureStart.objects.exists()


def epoch_for(  # noqa: PLR0913 - fixture inputs plus event-order/reconnection cases
    client, settings, grant, kind="host", *, reordered=False, connection_suffix=""
):
    config = (
        _host_config(grant)
        if kind == "host"
        else generate_guest_media_config(
            grant,
            "f" * 64,
            room_id=str(grant.room_binding.room_id),
            user=AnonymousUser(),
            username="Same display name",
            participant_id=grant.guest_ref,
            expires_at=grant.expires_at,
        )
    )
    issued = models.MastraoMediaTokenBinding.objects.get(
        pk=_claims(config)["attributes"]["mastrao.media_token_binding_ref"]
    )
    join = _join(issued, participant_sid="PA_" + kind + connection_suffix)
    track = _event(join, track_sid="TR_" + kind + connection_suffix)
    for event in [track, join] if reordered else [join, track]:
        _assert_post(client, settings, event)
    return models.MastraoRtcTrackEpoch.objects.get(
        connection__media_token_binding=issued
    )


@pytest.fixture
def recording(binding):
    return models.MastraoRecordingBinding.objects.create(
        room_binding=binding,
        organization_external_id="organization_media_fixture",
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        provider_binding_digest=binding.provider_binding_digest,
        recording_ref="recording_native_fixture",
        policy_ref="legacy_policy_fixture",
        notice_version="legacy_notice_fixture",
        notice_digest="a" * 64,
        retention_expires_at=timezone.now() + timedelta(hours=1),
    )


def test_two_sources_reordered_deduplicated_and_no_browser_credentials(
    client, native_settings, host, guest, recording
):
    with core_peer(native_settings) as (calls, _):
        with patch("core.mastrao_rtc_observations.wake_native_admissions") as wake:
            h = epoch_for(client, native_settings, host)
            g = epoch_for(client, native_settings, guest, "guest", reordered=True)
        assert wake.call_count == 4
        assert calls == []  # webhook never does network admission synchronously
        assert reconcile_native_admissions() == 2
        assert reconcile_native_admissions() == 0
        h.refresh_from_db()
        g.refresh_from_db()
        assert h.native_admission_capture_ref != g.native_admission_capture_ref
        assert {c[1]["participant_kind"] for c in calls} == {"host", "guest"}
        assert {c[1]["epoch_ref"] for c in calls} == {str(h.pk), str(g.pk)}
        assert all(c[2] == {"candidate"} for c in calls)
        assert not models.MastraoNativeCaptureStart.objects.exists()


def test_lost_reply_retries_same_epoch_and_expired_claim_cannot_overwrite(
    client, native_settings, host, recording
):
    epoch = epoch_for(client, native_settings, host)
    with core_peer(native_settings, lose_first=True) as (calls, _):
        assert reconcile_native_admissions() == 0
        assert reconcile_native_admissions() == 0  # bounded delay
        models.MastraoRtcTrackEpoch.objects.filter(pk=epoch.pk).update(
            native_admission_next_at=timezone.now()
        )
        assert reconcile_native_admissions() == 1
        assert [c[1]["epoch_ref"] for c in calls] == [str(epoch.pk)] * 2
    epoch.refresh_from_db()
    capture = epoch.native_admission_capture_ref
    epoch.native_admission_claim = uuid4()
    assert _finish(epoch, None, True) == 0
    epoch.refresh_from_db()
    assert epoch.native_admission_capture_ref == capture


def test_native_session_labels_are_bound_to_host_and_guest_epochs(
    client, native_settings, host, guest, recording
):
    for grant, name in [(host, "Matt"), (guest, "Martine")]:
        grant.display_name = name
        grant.save(update_fields=["display_name"])
    h = epoch_for(client, native_settings, host)
    g = epoch_for(client, native_settings, guest, "guest")
    with core_peer(native_settings) as (calls, _):
        assert reconcile_native_admissions() == 2
        labels = {c[1]["epoch_ref"]: c[1] for c in calls}
        for epoch, grant, name, kind in [
            (h, host, "Matt", "host"),
            (g, guest, "Martine", "guest"),
        ]:
            expected = {"source": "meet_session_display_name", "display_name": name}
            epoch.refresh_from_db()
            assert epoch.native_participant_label == expected
            observed = labels[str(epoch.pk)]
            assert observed["participant_label"] == expected
            assert observed["participant_kind"] == kind
            assert observed["grant_ref"] == grant.grant_ref
            assert observed["participant_ref"] == (
                host.identity.host_ref if kind == "host" else guest.guest_ref
            )


def test_native_label_is_frozen_before_network_and_survives_lost_reply(
    client, native_settings, host, recording
):
    host.display_name = "Matt"
    host.save(update_fields=["display_name"])
    epoch = epoch_for(client, native_settings, host)
    with core_peer(native_settings, lose_first=True) as (calls, _):
        assert reconcile_native_admissions() == 0
        epoch.refresh_from_db()
        assert epoch.native_participant_label["display_name"] == "Matt"
        host.display_name = "Changed after first delivery"
        host.save(update_fields=["display_name"])
        models.MastraoRtcTrackEpoch.objects.filter(pk=epoch.pk).update(
            native_admission_next_at=timezone.now()
        )
        assert reconcile_native_admissions() == 1
        assert [c[1]["participant_label"]["display_name"] for c in calls] == [
            "Matt",
            "Matt",
        ]


def test_guest_reconnection_keeps_distinct_epoch_label_evidence(
    client, native_settings, guest, recording
):
    guest.display_name = "Martine"
    guest.save(update_fields=["display_name"])
    first = epoch_for(client, native_settings, guest, "guest")
    with core_peer(native_settings) as (calls, _):
        assert reconcile_native_admissions() == 1
        models.MastraoRtcTrackEpoch.objects.filter(pk=first.pk).update(ended=True)
        models.MastraoRtcConnection.objects.filter(pk=first.connection_id).update(
            ended=True
        )
        guest.display_name = "Martine reconnectée"
        guest.save(update_fields=["display_name"])
        second = epoch_for(
            client, native_settings, guest, "guest", connection_suffix="reconnect"
        )
        assert reconcile_native_admissions() == 1
        assert first.pk != second.pk
        assert [c[1]["participant_ref"] for c in calls] == [guest.guest_ref] * 2
        assert [c[1]["participant_label"]["display_name"] for c in calls] == [
            "Martine",
            "Martine reconnectée",
        ]
        first.refresh_from_db()
        assert first.native_participant_label["display_name"] == "Martine"


@pytest.mark.parametrize("name", [None, "", "   ", "Bad\u202ename"])
def test_native_unknown_or_unsafe_session_label_does_not_invent_identity(
    client, native_settings, host, recording, name
):
    host.display_name = name
    host.save(update_fields=["display_name"])
    epoch_for(client, native_settings, host)
    with core_peer(native_settings) as (calls, _):
        assert reconcile_native_admissions() == 1
        assert calls[0][1]["participant_label"] == {
            "source": "meet_session_display_name",
            "display_name": None,
        }


@pytest.mark.parametrize("name", ["Bad\x00name", "a" * 161, "\ud800", "😀" * 81])
def test_invalid_in_memory_label_is_unknown(name):
    issued = SimpleNamespace(
        host_grant=SimpleNamespace(display_name=name), guest_grant=None
    )
    epoch = SimpleNamespace(connection=SimpleNamespace(media_token_binding=issued))
    assert _session_label(epoch)["display_name"] is None


@pytest.mark.parametrize(
    "case",
    [
        "ended",
        "conflict",
        "room_closed",
        "rotated",
        "flag_off",
        "guest_pending",
        "video",
    ],
)
def test_invalid_epochs_never_send(  # noqa: PLR0913,PLR0917
    client, native_settings, host, guest, recording, case
):
    grant = guest if case == "guest_pending" else host
    epoch = epoch_for(
        client, native_settings, grant, "guest" if case == "guest_pending" else "host"
    )
    if case in ("ended", "conflict"):
        models.MastraoRtcTrackEpoch.objects.filter(pk=epoch.pk).update(**{case: True})
    elif case == "room_closed":
        models.MastraoRoomBinding.objects.filter(pk=grant.room_binding_id).update(
            closing_at=timezone.now()
        )
    elif case == "rotated":
        type(grant).objects.filter(pk=grant.pk).update(session_nonce_digest="e" * 64)
    elif case == "flag_off":
        native_settings.MASTRAO_NATIVE_PREENTRY_ENABLED = False
    elif case == "guest_pending":
        type(grant).objects.filter(pk=grant.pk).update(
            admission_state="waiting",
            decision_allow=None,
            decision_ref=None,
            decision_grant_digest=None,
            decision_receipt_digest=None,
            decision_confirmed_at=None,
        )
    elif case == "video":
        models.MastraoRtcObservation.objects.filter(
            pk=epoch.first_publication_id
        ).update(track_type=1)
    with core_peer(native_settings) as (calls, _):
        assert reconcile_native_admissions() == 0
        assert calls == []


def test_established_epoch_survives_join_grant_expiry(
    client, native_settings, host, recording
):
    """Token expiry cannot revoke a connection already correlated by the server."""
    epoch = epoch_for(client, native_settings, host)
    type(host).objects.filter(pk=host.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1),
        issued_at=timezone.now() - timedelta(hours=1),
    )
    with core_peer(native_settings) as (calls, _):
        assert reconcile_native_admissions() == 1
        assert [call[1]["epoch_ref"] for call in calls] == [str(epoch.pk)]


def test_denied_core_is_not_retried_forever(client, native_settings, host, recording):
    epoch = epoch_for(client, native_settings, host)
    with core_peer(native_settings, denied=True) as (calls, _):
        assert reconcile_native_admissions() == 0
        epoch.refresh_from_db()
        assert epoch.native_admission_blocked
        assert reconcile_native_admissions() == 0
        assert len(calls) == 1


def test_claim_fence_and_broker_failure_preserve_pending_epoch(
    client, native_settings, host, recording
):
    epoch = epoch_for(client, native_settings, host)
    native_settings.CELERY_ENABLED = True
    with patch(
        "core.tasks.native_capture.process_native_admissions.delay",
        side_effect=RuntimeError("synthetic broker outage"),
    ):
        wake_native_admissions(epoch.connection_id)
    first = _claim_next()
    assert first is not None and _claim_next() is None
    models.MastraoRtcTrackEpoch.objects.filter(pk=epoch.pk).update(
        native_admission_claim_until=timezone.now() - timedelta(seconds=1)
    )
    second = _claim_next()
    assert second.native_admission_claim != first.native_admission_claim
    assert _finish(first, uuid4(), False) == 0
    assert _finish(second, uuid4(), False) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("capture_authorized", True),
        ("authorization", {"scope": "authorized_microphone_track_epoch"}),
    ],
)
def test_authorization_peer_drift_fails_closed(field, value):
    with pytest.raises(RecordingContractRefused):
        _validate_authorization({**authorization_projection(), field: value})


@pytest.mark.parametrize("route", ["retrieve", "request-entry"])
def test_native_policy_authorization_keeps_media_entry_available(
    client, native_settings, host, route
):
    browser_session(client, host, "host")
    base = f"/api/v1.0/rooms/{host.room_binding.room_id}/"
    status = {
        "mode": "recorded",
        "recording_state": "collecting",
        "decision": "refused",
        "video": {
            **video_projection(),
            "decision": "refused",
            "start_status": "refused",
        },
        "transcription_mode": "transcribed",
        "transcription_decision": "absent",
        "recording_ref": "recording_fixture",
        "notice_version": "legacy_notice_fixture",
        "notice_digest": "a" * 64,
        "purpose": "meeting_recording",
        "scope": "room_composite_audio_video_screen",
        "retention_expires_at": int(time.time()) + 3600,
        "participant_kind": "host",
        "transcription_notice_version": "legacy_transcription_fixture",
        "transcription_notice_digest": "b" * 64,
    }
    with (
        core_peer(native_settings) as (calls, _),
        patch("core.api.serializers.recording_session_status", return_value=status),
        patch("core.api.viewsets.recording_session_status", return_value=status),
        patch("core.api.serializers.ensure_livekit_room"),
        patch("core.services.lobby.ensure_livekit_room"),
        patch("core.utils.notify_participants"),
    ):
        result = (
            client.get(base)
            if route == "retrieve"
            else client.post(base + "request-entry/", {"username": "Matt"})
        )
        assert result.status_code == 200, result.content
        assert result["Cache-Control"] == "private, no-store"
        assert result.json()["livekit"]
        assert "native_capture" not in result.json()
        authorization = [call for call in calls if call[0] == AUTHORIZATION_PATH]
        assert len(authorization) == 1
        assert authorization[0][2] == {"request", "participant_grant"}
        assert "action" not in authorization[0][1]
        assert not models.Recording.objects.exists()


def test_preentry_off_preserves_room_and_does_not_contact_core(
    client, native_settings, host
):
    native_settings.MASTRAO_NATIVE_PREENTRY_ENABLED = False
    browser_session(client, host, "host")
    request = _request(host)
    with core_peer(native_settings) as (calls, _):
        assert (
            authorize_native_policy(
                request,
                host.room_binding.room,
                {"mode": "recorded", "transcription_mode": "transcribed"},
            )
            is None
        )
        result = client.get(f"/api/v1.0/rooms/{host.room_binding.room_id}/")
        assert result.status_code == 200
        assert "native_capture" not in result.json()
        assert calls == []
