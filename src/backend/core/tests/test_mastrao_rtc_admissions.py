"""Real SDK webhook verification, Core/Meet signatures and PostgreSQL delivery journal.

The Core HTTP peer below models its response only. Events in this module are
synthetic; actual SFU qualification is an explicit separate local test.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-import,no-member
# pylint: disable=too-many-arguments,too-many-positional-arguments

import copy
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

import jwt
import psycopg
import pytest
import requests
import responses
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from freezegun import freeze_time

from core import models
from core.mastrao_media_token_binding import generate_guest_media_config
from core.mastrao_rtc_admissions import OBSERVATION_JOSE_TYPE
from core.tests.test_mastrao_media_token_binding import (
    _claims,
    _host_config,
    _media_authority,
    _request,
    binding,
    guest,
    host,
    isolated_binding_settings,
)
from core.tests.test_mastrao_rtc_correlation import _event, _join
from core.tests.test_mastrao_rtc_observations import _post

pytestmark = pytest.mark.django_db(transaction=True)
CORE_URL = "http://127.0.0.1:8097/internal/v1/meetings/rtc-admissions"
OBSERVATION_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "purpose",
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "provider_binding_digest",
    "event_type",
    "event_id",
    "room_sid",
    "participant_sid",
    "participant_kind",
    "participant_ref",
    "token_binding_ref",
    "authority_kind",
    "authority_digest",
    "webhook_body_digest",
    "observed_at",
    "issued_at",
    "expires_at",
    "jti",
}


@pytest.fixture
def peer(settings):
    settings.MASTRAO_CORE_RTC_ADMISSION_ENDPOINT = CORE_URL
    envelopes = []
    facts = []
    private = json.loads(settings.MASTRAO_ROOM_RECEIPT_PRIVATE_JWK)
    public = Ed25519PrivateKey.from_private_bytes(
        jwt.utils.base64url_decode(private["d"])
    ).public_key()

    def accept(request):
        envelope = json.loads(request.body)
        assert set(envelope) == {"observation_assertion", "participant_authority"}
        compact = envelope["observation_assertion"]
        assert jwt.get_unverified_header(compact) == {
            "alg": "EdDSA",
            "kid": settings.MASTRAO_ROOM_RECEIPT_KEY_ID,
            "typ": OBSERVATION_JOSE_TYPE,
        }
        payload = jwt.decode(compact, public, algorithms=["EdDSA"])
        assert set(payload) == OBSERVATION_FIELDS
        assert payload["expires_at"] - payload["issued_at"] == 30
        assert (
            payload["authority_digest"]
            == hashlib.sha256(envelope["participant_authority"].encode()).hexdigest()
        )
        assert payload["purpose"] == "record_verified_meeting_start"
        assert payload["participant_kind"] == "human"
        envelopes.append(envelope)
        facts.append(payload)
        return (
            200,
            {},
            json.dumps(
                {
                    "version": 1,
                    "meeting_ref": payload["meeting_ref"],
                    "room_ref": payload["room_ref"],
                    "first_started_at": min(item["observed_at"] for item in facts),
                }
            ),
        )

    with responses.RequestsMock(assert_all_requests_are_fired=False) as http:
        http.add_callback(responses.POST, CORE_URL, callback=accept)
        yield http, envelopes, facts


def _issued(grant, *, with_config=False):
    if isinstance(grant, models.MastraoHostGrant):
        result = _host_config(grant)
    else:
        result = generate_guest_media_config(
            grant,
            _media_authority(grant),
            room_id=str(grant.room_binding.room_id),
            user=AnonymousUser(),
            username="Guest",
            participant_id=grant.guest_ref,
            expires_at=grant.expires_at,
        )
    receipt = models.MastraoMediaTokenBinding.objects.get(
        pk=_claims(result)["attributes"]["mastrao.media_token_binding_ref"]
    )
    return (receipt, result) if with_config else receipt


@pytest.mark.parametrize("kind", ["host", "guest"])
def test_human_entry_only_after_authenticated_join_and_exact_authority(  # noqa: PLR0913,PLR0917
    client, settings, host, guest, peer, kind
):
    grant = host if kind == "host" else guest
    issued = _issued(grant)
    _, envelopes, facts = peer
    assert envelopes == []  # Token issuance and guest approval do not start a meeting.
    independent = psycopg.connect(**connection.get_connection_params())
    try:
        with independent.cursor() as cursor:
            cursor.execute(
                "SELECT participant_authority FROM meet_mastrao_media_token_binding WHERE id = %s",
                [issued.pk],
            )
            assert cursor.fetchone() == (issued.participant_authority,)
    finally:
        independent.close()
    event = _join(issued)
    assert _post(client, settings, event, authenticated=False).status_code == 401
    assert (
        _post(
            client, settings, event, body=json.dumps({**event, "id": "tampered"})
        ).status_code
        == 400
    )
    assert envelopes == []
    assert _post(client, settings, event).status_code == 200
    row = models.MastraoRtcObservation.objects.get(event_id=event["id"])
    assert row.admission_attempts == 1 and row.admission_status == 200
    assert (
        row.admission_receipt["first_started_at"] == issued.issued_at.timestamp() // 1
    )
    assert envelopes[0]["participant_authority"] == issued.participant_authority
    assert (
        facts[0]["webhook_body_digest"]
        == hashlib.sha256(json.dumps(event).encode()).hexdigest()
    )
    assert facts[0]["authority_kind"] == kind
    assert facts[0]["participant_ref"] == (
        host.identity.host_ref if kind == "host" else guest.guest_ref
    )
    assert _post(client, settings, event).status_code == 200
    assert len(envelopes) == 1


@pytest.mark.parametrize(
    "participant_kind",
    ["EGRESS", "INGRESS", "SIP", "AGENT", "FUTURE_SERVICE", None, False, 0.0],
)
def test_service_even_with_copied_human_marker_never_signals(
    client, settings, host, peer, participant_kind
):
    issued = _issued(host)
    event = _join(issued)
    event["participant"]["kind"] = participant_kind
    assert _post(client, settings, event).status_code == 200
    assert peer[1] == []
    assert models.MastraoRtcObservation.objects.get().admission_attempts == 0


@pytest.mark.parametrize(
    "mismatch",
    [
        "identity",
        "marker",
        "room",
        "before",
        "expired",
        "future",
        "lobby",
        "unconfirmed",
    ],
)
def test_crossed_or_unadmitted_entry_does_not_signal(  # noqa: PLR0913,PLR0917
    client, settings, host, guest, peer, mismatch
):
    grant = guest if mismatch in {"lobby", "unconfirmed"} else host
    issued = _issued(grant)
    event = _join(issued)
    if mismatch == "identity":
        event["participant"]["identity"] = "another-human"
    elif mismatch == "marker":
        event["participant"]["attributes"] = {}
    elif mismatch == "room":
        event["room"]["name"] = str(uuid4())
    elif mismatch == "before":
        event["createdAt"] = str(int(issued.issued_at.timestamp()) - 1)
    elif mismatch == "expired":
        event["createdAt"] = str(int(issued.expires_at.timestamp()))
    elif mismatch == "future":
        event["createdAt"] = str(int(time.time()) + 5)
    elif mismatch == "lobby":
        models.MastraoMediaTokenBinding.objects.filter(pk=issued.pk).update(
            participant_authority="lobby.authority.only"
        )
    else:
        models.MastraoGuestGrant.objects.filter(pk=guest.pk).update(
            decision_confirmed_at=None
        )
    assert _post(client, settings, event).status_code == 200
    assert peer[1] == []


def test_response_loss_retry_survives_fresh_db_read_and_keeps_evidence(
    client, settings, host, peer
):
    issued = _issued(host)
    event = _join(issued)
    http, envelopes, facts = peer
    accept = http.registered()[0].callback

    def lost_response(request):
        accept(
            request
        )  # Core accepted the immutable fact before the response was lost.
        raise requests.Timeout("lost response")

    http.replace(
        responses.CallbackResponse(responses.POST, CORE_URL, callback=lost_response)
    )
    assert _post(client, settings, event).status_code == 200
    row = models.MastraoRtcObservation.objects.get()
    assert row.admission_status == 503 and row.admission_receipt is None
    first_digest = row.payload_digest
    connection.close()
    http.replace(responses.CallbackResponse(responses.POST, CORE_URL, callback=accept))
    call_command("retry_mastrao_rtc_admissions")
    row = models.MastraoRtcObservation.objects.get()
    assert row.admission_attempts == 2 and row.admission_status == 200
    assert row.payload_digest == first_digest
    assert (
        envelopes[0]["participant_authority"] == envelopes[1]["participant_authority"]
    )
    stable = OBSERVATION_FIELDS - {"jti", "issued_at", "expires_at"}
    assert {key: facts[0][key] for key in stable} == {
        key: facts[1][key] for key in stable
    }
    assert facts[0]["jti"] != facts[1]["jti"]
    call_command("retry_mastrao_rtc_admissions")
    assert len(envelopes) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429, 503])
def test_core_authz_or_conflict_refusal_never_marks_accepted(
    client, settings, host, peer, status
):
    issued = _issued(host)
    http, _, _ = peer
    http.replace(
        responses.Response(
            responses.POST, CORE_URL, status=status, json={"error": "refused"}
        )
    )
    assert _post(client, settings, _join(issued)).status_code == 200
    row = models.MastraoRtcObservation.objects.get()
    assert row.admission_status == status and row.admission_receipt is None
    assert models.MastraoRtcConnection.objects.get().correlation == "correlated"


def test_delayed_join_after_leave_and_expiry_preserves_actual_time(
    client, settings, host, peer
):
    issued = _issued(host)
    join = _join(issued)
    assert _post(client, settings, _event(join, "participant_left")).status_code == 200
    with mock.patch(
        "core.mastrao_host_contract.time.time",
        return_value=int(host.expires_at.timestamp()) + 10,
    ):
        assert _post(client, settings, join).status_code == 200
    assert peer[2][0]["observed_at"] == int(issued.issued_at.timestamp())
    assert models.MastraoRtcConnection.objects.get().ended is True


def test_leave_rejoin_fresh_token_keeps_first_start_and_original_fact(
    client, settings, host, peer
):
    first = _issued(host)
    join = _join(first, "PA_first")
    assert _post(client, settings, join).status_code == 200
    assert _post(client, settings, _event(join, "participant_left")).status_code == 200
    second = _issued(host)
    assert _post(client, settings, _join(second, "PA_second")).status_code == 200
    receipts = list(
        models.MastraoRtcObservation.objects.filter(
            event_type="participant_joined"
        ).values_list("admission_receipt", flat=True)
    )
    assert all(
        receipt["first_started_at"] == int(first.issued_at.timestamp())
        for receipt in receipts
    )
    changed = copy.deepcopy(join)
    changed["participant"]["sid"] = "PA_crossed"
    assert _post(client, settings, changed).status_code == 409
    assert len(peer[1]) == 2


def test_missing_endpoint_is_visible_durable_failure(client, settings, host):
    settings.MASTRAO_CORE_RTC_ADMISSION_ENDPOINT = ""
    issued = _issued(host)
    assert _post(client, settings, _join(issued)).status_code == 200
    row = models.MastraoRtcObservation.objects.get()
    assert row.admission_attempts == 1 and row.admission_status == 503
    assert row.admission_receipt is None


def test_wrong_compact_host_authority_refuses_token_before_return(host):
    request = _request(host)
    request.session["mastrao_host_compact_grants"][host.grant_ref] = (
        "forged.authority.value"
    )
    with pytest.raises(PermissionDenied):
        _host_config(host, request=request)
    assert not models.MastraoMediaTokenBinding.objects.exists()


def test_earlier_delayed_fact_refines_projection_without_rewriting_either_fact(
    client, settings, host, peer
):

    host.issued_at -= timedelta(seconds=60)
    host.save(update_fields=["issued_at"])
    with freeze_time(timezone.now() - timedelta(seconds=20)):
        earlier = _issued(host)
    later = _issued(host)
    assert _post(client, settings, _join(later, "PA_later")).status_code == 200
    assert _post(client, settings, _join(earlier, "PA_earlier")).status_code == 200
    assert peer[2][0]["observed_at"] > peer[2][1]["observed_at"]
    assert models.MastraoRtcObservation.objects.get(
        participant_sid="PA_earlier"
    ).admission_receipt["first_started_at"] == int(earlier.issued_at.timestamp())
    assert models.MastraoRtcObservation.objects.get(
        participant_sid="PA_later"
    ).event_time_seconds == int(later.issued_at.timestamp())


def test_restart_process_replays_exact_authority_from_postgres(  # pylint: disable=too-many-locals
    client, settings, host, peer
):

    issued = _issued(host)
    http, envelopes, _ = peer
    accept = http.registered()[0].callback
    http.replace(responses.Response(responses.POST, CORE_URL, status=503))
    assert _post(client, settings, _join(issued)).status_code == 200

    class Handler(BaseHTTPRequestHandler):
        """HTTP peer for the fresh retry process."""

        def do_POST(self):  # pylint: disable=invalid-name

            status, _, body = accept(
                SimpleNamespace(
                    body=self.rfile.read(int(self.headers["Content-Length"]))
                )
            )
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    overrides = {
        name: getattr(settings, name)
        for name in (
            "MASTRAO_ROOM_EFFECT_ISSUER",
            "MASTRAO_ROOM_EFFECT_AUDIENCE",
            "MASTRAO_ROOM_EFFECT_PUBLIC_JWK",
            "MASTRAO_ROOM_EFFECT_KEY_ID",
            "MASTRAO_ROOM_RECEIPT_ISSUER",
            "MASTRAO_ROOM_RECEIPT_AUDIENCE",
            "MASTRAO_ROOM_RECEIPT_PRIVATE_JWK",
            "MASTRAO_ROOM_RECEIPT_KEY_ID",
        )
    }
    overrides["MASTRAO_CORE_RTC_ADMISSION_ENDPOINT"] = (
        f"http://127.0.0.1:{server.server_port}/internal/v1/meetings/rtc-admissions"
    )
    program = """
import json, sys
import configurations
configurations.setup()
from django.conf import settings
from django.core.management import call_command
for key, value in json.load(sys.stdin).items():
    setattr(settings, key, value)
call_command('retry_mastrao_rtc_admissions', limit=1)
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", program],
            input=json.dumps(overrides),
            text=True,
            capture_output=True,
            timeout=20,
            check=True,
            env={**os.environ, "DB_NAME": connection.settings_dict["NAME"]},
        )
        assert "RTC admissions attempted: 1" in result.stdout
        assert envelopes[0]["participant_authority"] == issued.participant_authority
        row = models.MastraoRtcObservation.objects.get()
        assert row.admission_attempts == 2 and row.admission_receipt is not None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
