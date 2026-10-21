"""Private capability + signed RTC webhook provenance; no provider calls."""

# Imported fixtures deliberately reuse actual token issuance and signed webhooks.
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-import
# pylint: disable=no-member,unused-argument

import json
import time
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.core import signing
from django.test import override_settings
from django.utils import timezone

import jwt
import pytest
from asgiref.sync import async_to_sync
from livekit import api

from core import models
from core.factories import RoomFactory
from core.mastrao_live_track_provenance import epoch_references
from core.mastrao_live_worker_contract import CONTROL_PATH, PREPARE_PATH, SEND_PATH
from core.mastrao_live_worker_control import (
    CAPABILITY_SALT,
    live_control_metadata,
)
from core.mastrao_media_token_binding import generate_guest_media_config
from core.mastrao_recording_contract import RecordingContractRefused
from core.services.subtitle_reconciliation import (
    SubtitleReconciliationAmbiguous,
    _dispatch_metadata_json,
    _reconcile_provider_unlocked,
    reconcile_subtitle_control,
)
from core.tests.services.test_subtitle_reconciliation import mock_livekit_client
from core.tests.test_mastrao_media_token_binding import (
    _claims,
    binding,
    guest,
    host,
    isolated_binding_settings,
)
from core.tests.test_mastrao_room_adapter import _jwk_pair
from core.tests.test_mastrao_rtc_correlation import _assert_post, _event, _join, receipt

from meet.settings import scrub_mastrao_handoff_credentials

pytestmark = pytest.mark.django_db
SOURCE = "live_source_fixture0123456789"


@pytest.fixture
def gateway(settings, host, receipt, client):
    settings.MASTRAO_LIVE_WORKER_CONTROL_ENABLED = True
    settings.MASTRAO_LIVE_WORKER_CONTROL_URL = "http://backend:8000" + CONTROL_PATH
    settings.MASTRAO_LIVE_WORKER_CAPABILITY_MAX_AGE_SECONDS = 3600
    settings.MASTRAO_CORE_LIVE_PREPARE_ENDPOINT = (
        "http://cabinet-core:3911" + PREPARE_PATH
    )
    settings.MASTRAO_CORE_LIVE_SEND_ENDPOINT = "http://cabinet-core:3911" + SEND_PATH
    settings.ROOM_SUBTITLE_ENABLED = True
    private, jwk = _jwk_pair()
    settings.MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK = jwk["private"]
    settings.MASTRAO_RECORDING_RECEIPT_KEY_ID = "live-worker-test"
    settings.MASTRAO_RECORDING_RECEIPT_ISSUER = "meet-test"
    settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE = "core-test"
    host.organization_external_id = "organization_live_fixture"
    host.save(update_fields=["organization_external_id"])
    join = _join(receipt)
    _assert_post(client, settings, join)
    _assert_post(client, settings, _event(join))
    epoch = models.MastraoRtcTrackEpoch.objects.get()
    control = models.RoomSubtitleControl.objects.create(
        room=host.room_binding.room,
        room_sid="RM_fixture",
        provider="openai-live",
        desired_state="ON",
        control_generation=3,
    )
    metadata = live_control_metadata(control.room_sid, 3, "openai-live")
    return {
        "epoch": epoch,
        "control": control,
        "metadata": metadata,
        "key": private.public_key(),
    }


def command(action="prepare", **changes):
    body = {
        "version": 1,
        "action": action,
        "room_sid": "RM_fixture",
        "participant_sid": "PA_host",
        "track_sid": "TR_audio",
        "attempt_ref": "live_attempt_fixture0123456789",
        "worker_instance_ref": "live_worker_fixture0123456789",
    }
    if action != "prepare":
        body.update(source_ref=SOURCE, admission_ref=changes.pop("admission_ref"))
    return {**body, **changes}


def core_admission():
    return {
        "version": 1,
        "source_ref": SOURCE,
        "state": "collecting",
        "confirmed_sequence": 0,
        "final_event_count": 0,
        "incomplete": False,
        "meeting_coverage": "unverified",
    }


def core_send(body, *, state="active", start=False):
    return {
        "version": 1,
        **{
            name: body[name]
            for name in (
                "action",
                "source_ref",
                "admission_ref",
                "attempt_ref",
                "worker_instance_ref",
            )
        },
        "state": state,
        "lease_expires_at": int(time.time()) + 15 if state == "active" else None,
        "start_authorized": start,
        "send_authorized": state == "active",
        "replayed": False,
    }


def send_body(gateway, action="claim", **changes):
    return command(
        action,
        admission_ref=epoch_references(gateway["epoch"])["admission_ref"],
        **changes,
    )


def invoke(client, gateway, body, *, capability=None):
    return client.post(
        CONTROL_PATH,
        json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer "
        + (capability or gateway["metadata"]["capability"]),
    )


def test_prepare_signs_exact_server_provenance_without_recording_or_credentials(
    client, gateway, host
):
    assert not models.MastraoRecordingBinding.objects.exists()
    with mock.patch(
        "core.mastrao_live_worker_control.post_core_json", return_value=core_admission()
    ) as post:
        response = invoke(client, gateway, command())
    assert response.status_code == 200
    assert response.json() == {
        "version": 1,
        "source_ref": SOURCE,
        "admission_ref": epoch_references(gateway["epoch"])["admission_ref"],
    }
    assert response.headers["Cache-Control"] == "no-store"
    wire = post.call_args.kwargs["body"]
    assert set(wire) == {"request"}
    claims = jwt.decode(
        wire["request"],
        gateway["key"],
        algorithms=["EdDSA"],
        options={"verify_aud": False},
    )
    assert (
        jwt.get_unverified_header(wire["request"])["typ"]
        == "mastrao-live-track-prepare+jws"
    )
    assert claims["organization_external_id"] == host.organization_external_id
    assert claims["participant_ref"] == host.identity.host_ref
    assert claims["participant_session_digest"] == host.session_nonce_digest
    assert claims["grant_digest"] == host.grant_digest
    assert claims["provider"] == "openai"
    assert claims["model_ref"] == "gpt-live-transcribe"
    assert claims["leg_ref"] == epoch_references(gateway["epoch"])["leg_ref"]
    assert "consent_ref" not in claims
    assert "worker_instance_ref" not in claims
    assert set(gateway["metadata"]) == {"version", "url", "capability"}


@pytest.mark.parametrize("action", ["claim", "renew", "stopped"])
def test_send_preserves_exact_owner_and_core_flags(client, gateway, action):
    body = send_body(gateway, action)
    result = core_send(
        body,
        state="stopped" if action == "stopped" else "active",
        start=action == "claim",
    )
    with mock.patch(
        "core.mastrao_live_worker_control.post_core_json", return_value=result
    ) as post:
        response = invoke(client, gateway, body)
    assert response.status_code == 200
    assert response.json() == result
    claims = jwt.decode(
        post.call_args.kwargs["body"]["request"],
        gateway["key"],
        algorithms=["EdDSA"],
        options={"verify_aud": False},
    )
    assert claims["type"] == "mastrao.meet-live-transcript-send"
    assert all(claims[name] == body[name] for name in body if name != "version")


@pytest.mark.parametrize(
    "fault",
    [
        "ended",
        "conflict",
        "connection_ended",
        "uncorrelated",
        "wrong_room",
        "wrong_participant",
        "wrong_track",
        "video",
        "screen",
        "publication_identity",
        "publication_room",
        "grant_digest",
        "session_digest",
        "grant_room",
        "grant_org_absent",
        "join_expired",
        "join_identity",
        "token_reused",
        "host_inactive",
        "closing",
    ],
)
def test_prepare_refuses_bad_rtc_provenance_before_core(  # noqa: PLR0912  # pylint: disable=too-many-branches
    client, gateway, host, receipt, fault
):
    epoch = gateway["epoch"]
    body = command()
    if fault in {"ended", "conflict"}:
        models.MastraoRtcTrackEpoch.objects.filter(pk=epoch.pk).update(**{fault: True})
    elif fault == "connection_ended":
        models.MastraoRtcConnection.objects.update(ended=True)
    elif fault == "uncorrelated":
        models.MastraoRtcConnection.objects.update(correlation="unmatched_issuance")
    elif fault in {"wrong_room", "wrong_participant", "wrong_track"}:
        body[
            {
                "wrong_room": "room_sid",
                "wrong_participant": "participant_sid",
                "wrong_track": "track_sid",
            }[fault]
        ] += "other"
    elif fault == "video":
        models.MastraoRtcObservation.objects.filter(
            pk=epoch.first_publication_id
        ).update(track_type=api.TrackType.VIDEO)
    elif fault == "screen":
        models.MastraoRtcObservation.objects.filter(
            pk=epoch.first_publication_id
        ).update(track_source=api.TrackSource.SCREEN_SHARE_AUDIO)
    elif fault == "publication_identity":
        models.MastraoRtcObservation.objects.filter(
            pk=epoch.first_publication_id
        ).update(rtc_identity="forged")
    elif fault == "publication_room":
        models.MastraoRtcObservation.objects.filter(
            pk=epoch.first_publication_id
        ).update(room_sid="RM_other")
    elif fault in {"grant_digest", "session_digest"}:
        name = "grant_digest" if fault == "grant_digest" else "session_nonce_digest"
        models.MastraoHostGrant.objects.filter(pk=host.pk).update(**{name: "d" * 64})
    elif fault == "grant_room":
        models.MastraoHostGrant.objects.filter(pk=host.pk).update(
            room_ref="room_other_fixture"
        )
    elif fault == "grant_org_absent":
        models.MastraoHostGrant.objects.filter(pk=host.pk).update(
            organization_external_id=None
        )
    elif fault == "join_expired":
        models.MastraoRtcObservation.objects.filter(
            event_type="participant_joined"
        ).update(event_time_seconds=int(receipt.expires_at.timestamp()))
    elif fault == "join_identity":
        models.MastraoRtcObservation.objects.filter(
            event_type="participant_joined"
        ).update(rtc_identity="forged")
    elif fault == "token_reused":
        models.MastraoRtcConnection.objects.create(
            room_binding=receipt.room_binding,
            room_sid="RM_fixture",
            participant_sid="PA_reused",
            media_token_binding=receipt,
        )
    elif fault == "host_inactive":
        models.User.objects.filter(pk=host.identity.user_id).update(is_active=False)
    else:
        models.MastraoRoomBinding.objects.filter(pk=host.room_binding_id).update(
            closing_at=timezone.now()
        )
    with mock.patch("core.mastrao_live_worker_control.post_core_json") as post:
        assert invoke(client, gateway, body).status_code == 404
        post.assert_not_called()


@pytest.mark.parametrize("action", ["prepare", "claim", "renew"])
@pytest.mark.parametrize(
    "fault", ["generation", "off", "provider", "not_current", "kill_switch"]
)
def test_live_actions_require_current_enabled_control(
    client, gateway, settings, action, fault
):
    changes = {
        "generation": {"control_generation": 4},
        "off": {"desired_state": "OFF"},
        "provider": {"provider": "legacy"},
        "not_current": {"is_current": False},
    }
    if fault == "kill_switch":
        settings.MASTRAO_LIVE_WORKER_CONTROL_ENABLED = False
    else:
        models.RoomSubtitleControl.objects.filter(pk=gateway["control"].pk).update(
            **changes[fault]
        )
    body = command() if action == "prepare" else send_body(gateway, action)
    with mock.patch("core.mastrao_live_worker_control.post_core_json") as post:
        assert invoke(client, gateway, body).status_code == 404
        post.assert_not_called()


def test_stopped_after_disable_ended_epoch_and_expired_join_credentials(
    client, gateway, host, receipt, settings
):
    settings.MASTRAO_LIVE_WORKER_CONTROL_ENABLED = False
    models.RoomSubtitleControl.objects.update(
        control_generation=4, desired_state="OFF", is_current=False
    )
    models.MastraoRtcTrackEpoch.objects.update(ended=True, conflict=True)
    models.MastraoRtcConnection.objects.update(ended=True, correlation="token_reused")
    now = int(time.time())
    # Move all original issuance/join facts back together; established correlation survives expiry.
    models.MastraoMediaTokenBinding.objects.filter(pk=receipt.pk).update(
        issued_at=timezone.now() - timezone.timedelta(hours=2),
        expires_at=timezone.now() - timezone.timedelta(hours=1),
    )
    models.MastraoHostGrant.objects.filter(pk=host.pk).update(
        issued_at=timezone.now() - timezone.timedelta(hours=2),
        expires_at=timezone.now() - timezone.timedelta(hours=1),
    )
    models.MastraoRtcObservation.objects.filter(event_type="participant_joined").update(
        event_time_seconds=now - 5000
    )
    body = send_body(gateway, "stopped")
    with mock.patch(
        "core.mastrao_live_worker_control.post_core_json",
        return_value=core_send(body, state="stopped"),
    ):
        assert invoke(client, gateway, body).status_code == 200
        bad = {**body, "admission_ref": "live_admission_wrong_epoch"}
        assert invoke(client, gateway, bad).status_code == 404


def test_expired_token_does_not_revoke_established_connection(client, gateway, receipt):
    # The join happened during issuance validity; wall-clock expiry is not rechecked locally.
    with mock.patch(
        "core.mastrao_live_worker_control.time.time", return_value=time.time() + 3700
    ):
        fresh = live_control_metadata("RM_fixture", 3, "openai-live")
        with mock.patch(
            "core.mastrao_live_worker_control.post_core_json",
            return_value=core_admission(),
        ):
            assert (
                invoke(
                    client, gateway, command(), capability=fresh["capability"]
                ).status_code
                == 200
            )


@pytest.mark.parametrize(
    "fault",
    ["tampered", "wrong_salt", "expired", "future", "other_room", "old_timestamp"],
)
def test_capability_is_scoped_and_time_bounded(client, gateway, fault):
    capability = gateway["metadata"]["capability"]
    scope = signing.loads(capability, salt=CAPABILITY_SALT)
    if fault == "tampered":
        capability += "x"
    elif fault == "wrong_salt":
        capability = signing.dumps(scope, salt="other-purpose")
    elif fault == "expired":
        scope["issued_at"] -= 4000
        scope["expires_at"] -= 4000
        capability = signing.dumps(scope, salt=CAPABILITY_SALT)
    elif fault == "future":
        scope["issued_at"] += 4000
        scope["expires_at"] += 4000
        capability = signing.dumps(scope, salt=CAPABILITY_SALT)
    elif fault == "other_room":
        scope["room_sid"] = "RM_other"
        capability = signing.dumps(scope, salt=CAPABILITY_SALT)
    else:
        with mock.patch(
            "django.core.signing.time.time", return_value=time.time() - 4000
        ):
            capability = signing.dumps(scope, salt=CAPABILITY_SALT)
    with mock.patch("core.mastrao_live_worker_control.post_core_json") as post:
        assert (
            invoke(client, gateway, command(), capability=capability).status_code == 404
        )
        post.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"participant_grant": "secret"},
        {"consent_ref": "choice"},
        {"attempt_ref": True},
        {"track_sid": " browser "},
        {"action": []},
    ],
)
def test_body_does_not_accept_browser_authority_or_coercion(client, gateway, changes):
    with mock.patch("core.mastrao_live_worker_control.post_core_json") as post:
        assert invoke(client, gateway, {**command(), **changes}).status_code == 400
        post.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"source_ref": "wrong_source_fixture"},
        {"worker_instance_ref": "wrong_worker_fixture"},
        {"admission_ref": "wrong_admission_fixture"},
        {"attempt_ref": "wrong_attempt_fixture"},
        {"version": True},
        {"send_authorized": 1},
        {"start_authorized": True, "replayed": True},
        {"lease_expires_at": int(time.time()) - 1},
        {"lease_expires_at": int(time.time()) + 3600},
        {"unexpected": "secret"},
        {"state": []},
    ],
)
def test_malformed_core_send_never_reaches_worker(client, gateway, changes):
    body = send_body(gateway)
    result = {**core_send(body, start=True), **changes}
    with mock.patch(
        "core.mastrao_live_worker_control.post_core_json", return_value=result
    ):
        response = invoke(client, gateway, body)
    assert response.status_code == 503
    assert response.json() == {"message": "Unavailable"}


def test_renew_cannot_authorize_a_new_open(client, gateway):
    body = send_body(gateway, "renew")
    with mock.patch(
        "core.mastrao_live_worker_control.post_core_json",
        return_value=core_send(body, start=True),
    ):
        assert invoke(client, gateway, body).status_code == 503


def test_dispatch_disabled_default_retains_metadata(gateway, settings):
    settings.MASTRAO_LIVE_WORKER_CONTROL_ENABLED = False
    assert live_control_metadata("RM_fixture", 3, "openai-live") is None
    assert "liveControl" not in json.loads(
        _dispatch_metadata_json("RM_fixture", 3, "openai-live")
    )


@pytest.mark.parametrize(
    "name,value",
    [
        ("MASTRAO_LIVE_WORKER_CONTROL_URL", ""),
        ("MASTRAO_LIVE_WORKER_CONTROL_URL", "http://backend:8000/api/public"),
        (
            "MASTRAO_CORE_LIVE_PREPARE_ENDPOINT",
            "https://external.invalid" + PREPARE_PATH,
        ),
        ("MASTRAO_CORE_LIVE_SEND_ENDPOINT", "http://cabinet-core:3911/wrong"),
    ],
)
def test_enabled_canonical_dispatch_fails_closed_without_valid_config(
    gateway, settings, name, value
):
    setattr(settings, name, value)
    with pytest.raises(RecordingContractRefused):
        live_control_metadata("RM_fixture", 3, "openai-live")


def test_no_participant_session_authentication(client, gateway):
    response = client.post(
        CONTROL_PATH, json.dumps(command()), content_type="application/json"
    )
    assert response.status_code == 404
    assert client.get(CONTROL_PATH).status_code == 405


@pytest.mark.parametrize("revoked", [False, True])
def test_guest_provenance_uses_admitted_grant_organization(
    client, gateway, guest, settings, revoked
):
    config = generate_guest_media_config(
        guest,
        "f" * 64,
        room_id=str(guest.room_binding.room_id),
        user=AnonymousUser(),
        username="Same display name",
        participant_id=guest.guest_ref,
        expires_at=guest.expires_at,
    )
    issued = models.MastraoMediaTokenBinding.objects.get(
        pk=_claims(config)["attributes"]["mastrao.media_token_binding_ref"]
    )
    join = _join(issued, participant_sid="PA_guest")
    _assert_post(client, settings, join)
    _assert_post(client, settings, _event(join, track_sid="TR_guest"))
    if revoked:
        models.MastraoGuestGrant.objects.filter(pk=guest.pk).update(
            admission_state="denied", decision_allow=False
        )
    with mock.patch(
        "core.mastrao_live_worker_control.post_core_json", return_value=core_admission()
    ) as post:
        result = invoke(
            client, gateway, command(participant_sid="PA_guest", track_sid="TR_guest")
        )
    if revoked:
        assert result.status_code == 404
        post.assert_not_called()
    else:
        assert result.status_code == 200
        claims = jwt.decode(
            post.call_args.kwargs["body"]["request"],
            gateway["key"],
            algorithms=["EdDSA"],
            options={"verify_aud": False},
        )
        assert claims["participant_kind"] == "guest"
        assert claims["participant_ref"] == guest.guest_ref
        assert claims["organization_external_id"] == guest.organization_external_id


def test_capture_revoked_during_core_call_cannot_authorize_worker_start(
    client, gateway
):
    body = send_body(gateway)

    def revoke(**_kwargs):
        models.RoomSubtitleControl.objects.update(
            desired_state="OFF", control_generation=4
        )
        return core_send(body, start=True)

    with mock.patch(
        "core.mastrao_live_worker_control.post_core_json", side_effect=revoke
    ):
        assert invoke(client, gateway, body).status_code == 404


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"state": "open"},
        {"incomplete": 1},
        {"source_ref": False},
        {"confirmed_sequence": True},
        {"final_event_count": 1},
        {"meeting_coverage": "complete"},
        {"unexpected": "secret"},
    ],
)
def test_malformed_admission_response_is_opaque(client, gateway, changes):
    with mock.patch(
        "core.mastrao_live_worker_control.post_core_json",
        return_value={**core_admission(), **changes},
    ):
        response = invoke(client, gateway, command())
    assert response.status_code == 503
    assert response.json() == {"message": "Unavailable"}


@pytest.mark.parametrize(
    "url",
    [
        "http://external.example" + CONTROL_PATH,
        "http://8.8.8.8" + CONTROL_PATH,
        "http://backend:0" + CONTROL_PATH,
        "http://backend:99999" + CONTROL_PATH,
        "http://backend:bad" + CONTROL_PATH,
        "http://@backend" + CONTROL_PATH,
        "http://backend" + CONTROL_PATH + "?secret=1",
        "http://back end" + CONTROL_PATH,
    ],
)
def test_dispatch_url_rejects_public_or_malformed_targets(gateway, settings, url):
    settings.MASTRAO_LIVE_WORKER_CONTROL_URL = url
    with pytest.raises(RecordingContractRefused):
        live_control_metadata("RM_fixture", 3, "openai-live")


@pytest.mark.parametrize(
    "hostname",
    [
        "127.0.0.1",
        "[::1]",
        "10.1.2.3",
        "meet-backend",
        "meet.internal",
        "meet.ns.svc.cluster.local",
    ],
)
def test_dispatch_url_is_exact_and_internal(gateway, settings, hostname):
    settings.MASTRAO_LIVE_WORKER_CONTROL_URL = (
        "http://" + hostname + ":8000" + CONTROL_PATH
    )
    assert (
        live_control_metadata("RM_fixture", 3, "openai-live")["url"]
        == settings.MASTRAO_LIVE_WORKER_CONTROL_URL
    )


def test_duplicate_json_fields_refused(client, gateway):
    raw = json.dumps(command())[:-1] + ',"version":1}'
    with mock.patch("core.mastrao_live_worker_control.post_core_json") as post:
        response = client.post(
            CONTROL_PATH,
            raw,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + gateway["metadata"]["capability"],
        )
    assert response.status_code == 400
    post.assert_not_called()


def test_worker_capability_scrubbed_from_error_capture():
    event = {
        "request": {
            "data": {"liveControl": {"capability": "secret"}},
            "headers": {"Authorization": "Bearer secret"},
        }
    }
    scrubbed = scrub_mastrao_handoff_credentials(event, {})
    assert "secret" not in str(scrubbed)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("enabled", [False, True])
def test_actual_dispatch_has_scoped_metadata_only_when_enabled(
    gateway, settings, mock_livekit_client, enabled
):
    settings.MASTRAO_LIVE_WORKER_CONTROL_ENABLED = enabled
    async_to_sync(_reconcile_provider_unlocked)(
        str(gateway["control"].room_id), "RM_fixture", "ON", 3, "openai-live"
    )
    request = mock_livekit_client.agent_dispatch.create_dispatch.call_args.args[0]
    metadata = json.loads(request.metadata)
    assert metadata["provider"] == "openai-live"
    if enabled:
        assert (
            metadata["liveControl"]["url"] == settings.MASTRAO_LIVE_WORKER_CONTROL_URL
        )
        scope = signing.loads(
            metadata["liveControl"]["capability"], salt=CAPABILITY_SALT
        )
        assert scope["generation"] == 3
        assert scope["room_sid"] == "RM_fixture"
    else:
        assert "liveControl" not in metadata


@pytest.mark.django_db(transaction=True)
def test_actual_canonical_dispatch_missing_url_never_creates_legacy_job(
    gateway, settings, mock_livekit_client
):
    settings.MASTRAO_LIVE_WORKER_CONTROL_URL = ""
    with pytest.raises(SubtitleReconciliationAmbiguous):
        async_to_sync(_reconcile_provider_unlocked)(
            str(gateway["control"].room_id), "RM_fixture", "ON", 3, "openai-live"
        )
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_noncanonical_dispatch_preserves_legacy_path(
    gateway, settings, mock_livekit_client
):
    room = RoomFactory()
    models.RoomSubtitleControl.objects.create(
        room=room,
        room_sid="RM_other",
        provider="openai-live",
        desired_state="ON",
        control_generation=1,
    )
    settings.MASTRAO_LIVE_WORKER_CONTROL_URL = ""
    async_to_sync(_reconcile_provider_unlocked)(
        str(room.pk), "RM_other", "ON", 1, "openai-live"
    )
    request = mock_livekit_client.agent_dispatch.create_dispatch.call_args.args[0]
    assert "liveControl" not in json.loads(request.metadata)


@pytest.mark.django_db(transaction=True)
def test_existing_uncontrolled_canonical_dispatch_is_never_adopted(
    gateway, mock_livekit_client
):
    dispatch = mock.Mock(
        id="AD_legacy",
        agent_name="multi-user-transcriber",
        metadata=_dispatch_metadata_json("RM_fixture", 3, "openai-live"),
    )
    mock_livekit_client.agent_dispatch.list_dispatch.return_value = [dispatch]
    with pytest.raises(SubtitleReconciliationAmbiguous):
        async_to_sync(_reconcile_provider_unlocked)(
            str(gateway["control"].room_id), "RM_fixture", "ON", 3, "openai-live"
        )
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()


@pytest.mark.django_db(transaction=True)
@override_settings(CELERY_ENABLED=False)
@pytest.mark.parametrize("desired_state,generation", [("ON", 3), ("OFF", 4)])
@pytest.mark.parametrize(
    "metadata",
    [
        "{}",
        json.dumps({"generation": 3, "provider": "openai-live"}),
        json.dumps({"generation": 2, "provider": "openai-live"}),
        *[
            json.dumps(
                {
                    "roomSid": room_sid,
                    "generation": 3,
                    "provider": "openai-live",
                    "agentName": "multi-user-transcriber",
                }
            )
            for room_sid in (None, [], "invalid", "RM_other")
        ],
        "[]",
        "{invalid",
    ],
    ids=[
        "empty_object",
        "missing_sid",
        "missing_sid_old_generation",
        "null_sid",
        "array_sid",
        "invalid_sid",
        "other_sid",
        "array_json",
        "invalid_json",
    ],
)
def test_unknown_dispatch_cannot_hide_behind_room_metadata(
    gateway, mock_livekit_client, desired_state, generation, metadata
):
    """The room-scoped provider list owns attribution, not the JSON room hint."""
    models.RoomSubtitleControl.objects.filter(pk=gateway["control"].pk).update(
        desired_state=desired_state, control_generation=generation
    )
    existing = mock.Mock(
        id="AD_unknown", agent_name="multi-user-transcriber", metadata=metadata
    )
    mock_livekit_client.agent_dispatch.list_dispatch.return_value = [existing]

    with pytest.raises(SubtitleReconciliationAmbiguous):
        reconcile_subtitle_control("RM_fixture")

    mock_livekit_client.agent_dispatch.list_dispatch.assert_awaited_once_with(
        str(gateway["control"].room_id)
    )
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_not_called()
    assert mock_livekit_client.agent_dispatch.list_dispatch.return_value == [existing]
    control = models.RoomSubtitleControl.objects.get(pk=gateway["control"].pk)
    assert control.public_state not in {"stopped", "live"}
    assert control.attempts == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "metadata", ["{}", '{"generation":3,"provider":"openai-live"}']
)
def test_off_relist_cannot_confirm_absence_while_unknown_dispatch_remains(
    gateway, mock_livekit_client, metadata
):
    """Even after a known deletion, the verification list cannot hide a worker."""
    models.RoomSubtitleControl.objects.filter(pk=gateway["control"].pk).update(
        desired_state="OFF", control_generation=4
    )
    known = mock.Mock(
        id="AD_legacy", agent_name="multi-user-transcriber", metadata=None
    )
    unknown = mock.Mock(
        id="AD_unknown", agent_name="multi-user-transcriber", metadata=metadata
    )
    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = [[known], [unknown]]

    with pytest.raises(SubtitleReconciliationAmbiguous):
        async_to_sync(_reconcile_provider_unlocked)(
            str(gateway["control"].room_id), "RM_fixture", "OFF", 4, "openai-live"
        )

    assert mock_livekit_client.agent_dispatch.list_dispatch.await_count == 2
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_awaited_once_with(
        dispatch_id="AD_legacy", room_name=str(gateway["control"].room_id)
    )
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "metadata", ["{}", '{"generation":3,"provider":"openai-live"}']
)
def test_on_deduplication_relist_cannot_hide_unknown_dispatch(
    gateway, mock_livekit_client, metadata
):
    """A valid current worker does not hide a malformed one in the relist."""
    controlled = _dispatch_metadata_json(
        "RM_fixture", 3, "openai-live", gateway["metadata"]
    )
    known = mock.Mock(
        id="AD_0", agent_name="multi-user-transcriber", metadata=controlled
    )
    duplicate = mock.Mock(
        id="AD_1", agent_name="multi-user-transcriber", metadata=controlled
    )
    unknown = mock.Mock(
        id="AD_unknown", agent_name="multi-user-transcriber", metadata=metadata
    )
    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = [
        [known, duplicate],
        [known, unknown],
    ]

    with pytest.raises(SubtitleReconciliationAmbiguous):
        async_to_sync(_reconcile_provider_unlocked)(
            str(gateway["control"].room_id), "RM_fixture", "ON", 3, "openai-live"
        )

    assert mock_livekit_client.agent_dispatch.list_dispatch.await_count == 2
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_awaited_once_with(
        dispatch_id="AD_1", room_name=str(gateway["control"].room_id)
    )
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()
