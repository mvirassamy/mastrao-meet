"""Real Django close/authenticated RTC; explicit synthetic Core transport peer."""

# ruff: noqa: PLC0415 -- configure Django before loading models.

import base64
import hashlib
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

from native_capture_domain_support import (
    apply_synthetic_room_close,
    configure_domain_tests,
    configure_synthetic_signing,
)


def configure_live_tests(socket_directory=None):
    """Use only original interpreter and Core's disposable PostgreSQL helper."""
    configure_domain_tests(socket_directory)


def configure_live_signing():
    """Ephemeral fixture keys only; no env/application credential files."""
    from django.conf import settings

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    public = configure_synthetic_signing()
    core = Ed25519PrivateKey.generate()
    settings.MASTRAO_CORE_NATIVE_TRACK_OBSERVATION_ENDPOINT = ""
    settings.MASTRAO_NATIVE_PREENTRY_ENABLED = False
    settings.MASTRAO_MEETING_RECORDING_ENABLED = False
    settings.MASTRAO_LIVE_WORKER_CONTROL_ENABLED = False
    settings.ROOM_SUBTITLE_ENABLED = False
    settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK = json.dumps(
        {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": base64.urlsafe_b64encode(core.public_key().public_bytes_raw())
            .rstrip(b"=")
            .decode(),
        }
    )
    settings.MASTRAO_RECORDING_EFFECT_KEY_ID = "synthetic-live-core-key"
    settings.MASTRAO_RECORDING_EFFECT_ISSUER = "synthetic-live-core"
    settings.MASTRAO_RECORDING_EFFECT_AUDIENCE = "synthetic-live-meet"
    settings.MASTRAO_CORE_LIVE_OBSERVED_DOMAIN_CONTEXT_ENDPOINT = (
        "http://127.0.0.1:59999/internal/v1/meetings/live/observed-domain/context"
    )
    settings.MASTRAO_CORE_LIVE_OBSERVED_DOMAIN_ENDPOINT = (
        "http://127.0.0.1:59999/internal/v1/meetings/live/observed-domain"
    )
    return core, public


def room_binding():
    """No recording, no grant-derived organization authority."""
    from core import models

    reference = uuid4().hex
    user = models.User.objects.create(sub=reference, password="!synthetic-unusable")
    room = models.Room.objects.create(name=f"live_fixture_{reference}")
    return models.MastraoRoomBinding.objects.create(
        room=room,
        owner=user,
        effect_key=f"room_effect_{reference}",
        arguments_digest="a" * 64,
        meeting_ref=f"meeting_{reference}",
        room_ref=f"room_{reference}",
        owner_ref=f"owner_{reference}",
        provider_binding_digest="b" * 64,
    )


def close_room(binding):
    """Real signed close view/receipt and SDK/cache double, no Core close HTTP."""
    return apply_synthetic_room_close(
        binding, SimpleNamespace(organization_external_id="synthetic_live_org")
    )


def rtc(  # noqa: PLR0913 -- explicit server event fixture dimensions
    binding,
    event="track_published",
    *,
    participant="PA_live",
    track="TR_live",
    token=None,
    room_sid="RM_live",
    track_type=0,
    track_source=2,
    event_id=None,
):
    """LiveKit WebhookReceiver verifies signature AND body hash before real inbox."""
    from livekit import api

    from core.mastrao_rtc_observations import record_verified_rtc_observation

    payload = {
        "id": event_id or f"EV_{uuid4().hex}",
        "event": event,
        "createdAt": str(int(time.time())),
        "room": {"name": str(binding.room_id), "sid": room_sid},
    }
    if event != "room_finished":
        payload["participant"] = {
            "sid": participant,
            "identity": "synthetic_live_identity",
            "attributes": {},
        }
        if token is not None:
            payload["participant"]["attributes"]["mastrao.media_token_binding_ref"] = (
                str(token)
            )
        if event in {"track_published", "track_unpublished"}:
            payload["track"] = {
                "sid": track,
                "type": track_type,
                "source": track_source,
            }
    body = json.dumps(payload)
    key, secret = (
        "synthetic-rtc-key",
        "synthetic-rtc-secret-long-enough-for-local-verification",
    )
    signer = api.AccessToken(key, secret)
    signer.claims.sha256 = base64.b64encode(
        hashlib.sha256(body.encode()).digest()
    ).decode()
    receiver = api.WebhookReceiver(api.TokenVerifier(key, secret))
    verified = receiver.receive(body, signer.to_jwt())
    record_verified_rtc_observation(verified, body.encode())
    return payload


def core_assertion(private, jose, payload):
    """Fixture peer signing, deliberately separate from production Meet signing."""
    from django.conf import settings

    from core.mastrao_room_contract import _base64url_encode, _canonical_json

    header = {
        "alg": "EdDSA",
        "typ": jose,
        "kid": settings.MASTRAO_RECORDING_EFFECT_KEY_ID,
    }
    signing = f"{_base64url_encode(_canonical_json(header))}.{_base64url_encode(_canonical_json(payload))}"
    return signing + "." + _base64url_encode(private.sign(signing.encode()))


def response_envelope(assertion_type, operation):
    from django.conf import settings

    now = int(time.time())
    return {
        "version": 1,
        "issuer": settings.MASTRAO_RECORDING_EFFECT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_EFFECT_AUDIENCE,
        "type": assertion_type,
        "operation": operation,
        "operation_version": 1,
        "issued_at": now,
        "expires_at": now + 30,
        "jti": uuid4().hex,
    }


def context_claims(claim, *, reason=None, deadline=None):
    now = int(time.time())
    policy = {
        "policy_ref": "live_policy_fixture_01",
        "retention_policy_ref": "live_retention_fixture_01",
        "retention_expires_at": deadline or now + 3600,
    }
    result = {
        **response_envelope(
            "mastrao.core-live-observed-domain-context",
            "confirm_live_observed_domain_context",
        ),
        "request_jti": claim.jti.hex,
        "binding": claim.binding,
        "outcome": "eligible",
        "policy": policy,
    }
    if reason is not None:
        result.update(outcome="not_applicable", reason=reason)
        if reason != "retention_expired":
            result["policy"] = None
    return result


def ack_claims(claim, *, outcome="recorded"):
    return {
        **response_envelope(
            "mastrao.core-live-observed-domain-ack", "acknowledge_live_observed_domain"
        ),
        "request_jti": claim.jti.hex,
        "binding": claim.binding,
        "policy": claim.snapshot["policy"],
        "domain_ref": str(claim.domain_ref),
        "requested_revision": claim.revision,
        "requested_digest": claim.digest,
        "current_revision": claim.revision,
        "current_digest": claim.digest,
        "outcome": outcome,
    }


class LocalCorePeer:
    """Loopback signed metadata peer; NOT a Core authority/dispatcher proof.

    Its in-memory metadata ledger commits before deliberately dropping a reply.
    Parent must exercise genuine Core services/journals separately after freeze.
    """

    def __init__(self, private, meet_public):
        self.private = private
        self.meet_public = meet_public
        self.requests = []
        self.commits = {}
        self.drop_next = False
        self.on_publication = None
        self.error_status = None
        self.context_reason = None
        self.deadline = int(time.time()) + 3600
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                import jwt

                from core.mastrao_live_observed_domain_contract import (
                    ACK_JOSE,
                    CONTEXT_JOSE,
                )
                from core.mastrao_room_contract import (
                    _canonical_json,
                    _sha256_canonical,
                )

                length = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(length))
                compact = body["request"]
                payload = jwt.decode(
                    compact,
                    peer.meet_public,
                    algorithms=["EdDSA"],
                    options={"verify_aud": False},
                )
                header = jwt.get_unverified_header(compact)
                decoded = base64.urlsafe_b64decode(compact.split(".")[1] + "==")
                if decoded != _canonical_json(payload):
                    raise AssertionError("Producer bytes not canonical")
                peer.requests.append(
                    {"path": self.path, "payload": payload, "header": header}
                )
                if peer.error_status:
                    self.send_response(peer.error_status)
                    self.end_headers()
                    self.wfile.write(b"{}")
                    return
                if self.path.endswith("/context"):
                    claim = SimpleNamespace(
                        jti=SimpleNamespace(hex=payload["jti"]),
                        binding=payload["binding"],
                    )
                    value = context_claims(
                        claim, reason=peer.context_reason, deadline=peer.deadline
                    )
                    response = {
                        "context": core_assertion(peer.private, CONTEXT_JOSE, value)
                    }
                else:
                    snapshot = payload["snapshot"]
                    digest = _sha256_canonical(snapshot)
                    if digest != payload["snapshot_digest"]:
                        raise AssertionError("Incorrect snapshot digest")
                    key = (snapshot["domain_ref"], snapshot["producer_revision"])
                    outcome = "replayed" if key in peer.commits else "recorded"
                    if key in peer.commits and peer.commits[key] != snapshot:
                        raise AssertionError("Revision conflict")
                    peer.commits[key] = snapshot
                    if peer.on_publication:
                        peer.on_publication()
                    if peer.drop_next:
                        peer.drop_next = False
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    claim = SimpleNamespace(
                        jti=SimpleNamespace(hex=payload["jti"]),
                        binding=snapshot["binding"],
                        snapshot=snapshot,
                        domain_ref=snapshot["domain_ref"],
                        revision=snapshot["producer_revision"],
                        digest=digest,
                    )
                    response = {
                        "acknowledgement": core_assertion(
                            peer.private, ACK_JOSE, ack_claims(claim, outcome=outcome)
                        )
                    }
                data = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        from django.conf import settings

        port = self.server.server_address[1]
        settings.MASTRAO_CORE_LIVE_OBSERVED_DOMAIN_CONTEXT_ENDPOINT = (
            f"http://127.0.0.1:{port}/internal/v1/meetings/live/observed-domain/context"
        )
        settings.MASTRAO_CORE_LIVE_OBSERVED_DOMAIN_ENDPOINT = (
            f"http://127.0.0.1:{port}/internal/v1/meetings/live/observed-domain"
        )
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def prepare_live_observed_domain_export():
    """Parent bridge handshake: real close/RTC, zero recordings, public trust.

    Call configure_live_tests(socket_directory) first in the original -B Python
    process. No HTTP Core peer is created here. Parent can seed/confirm its real
    Core close from this verified receipt, then call continue_* in this process.
    """
    from importlib.metadata import version

    from django.conf import settings
    from django.core.management import call_command

    from core import mastrao_live_observed_domain as domain
    from core import models

    call_command("migrate", verbosity=0, interactive=False)
    _, public = configure_live_signing()
    binding = room_binding()
    rtc(binding, "participant_joined")
    rtc(binding)
    proof = close_room(binding)
    closure = models.MastraoRoomClosure.objects.get(room_binding=binding)
    cursor = models.MastraoLiveObservedDomain.objects.get(closure=closure)
    proof["persisted_claims"] = closure.receipt_claims
    proof["compact_digest"] = closure.receipt_digest
    return {
        "fixture": "live_observed_after_close_python_v1",
        "domain_ref": str(cursor.pk),
        "binding": domain._binding(closure),
        "close": proof,
        "trust": {
            "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
            "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
            "kid": settings.MASTRAO_RECORDING_RECEIPT_KEY_ID,
            "public_jwk": {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": base64.urlsafe_b64encode(public.public_bytes_raw())
                .rstrip(b"=")
                .decode(),
            },
        },
        "versions": {
            name: version(name)
            for name in ("Django", "PyJWT", "psycopg", "livekit-api")
        },
        "recording_bindings": models.MastraoRecordingBinding.objects.count(),
        "authenticated_rtc_observations": models.MastraoRtcObservation.objects.filter(
            room_binding=binding
        ).count(),
        "limitations": "Synthetic signed RTC payloads; real WebhookReceiver/inbox/correlation and Django close view. SDK/cache doubles, no deployed middleware or physical media. Core authority must be exercised by parent.",
    }


def continue_live_observed_domain_export(
    domain_ref,
    *,
    core_trust,
    context_endpoint,
    publication_endpoint,
    dropped_reply_expected=False,
):
    """Consume real signed parent Core context and publish actual Python S.

    core_trust = {issuer,audience,kid,public_jwk}; endpoints are ephemeral loopback
    Core paths. No private Core material crosses this boundary. A requested drop
    must really fail after parent commit; the exact durable S is then retried.
    """
    from django.conf import settings
    from django.utils import timezone

    from core import mastrao_live_observed_domain as domain
    from core import models
    from core.mastrao_live_observed_domain_contract import publication_request

    settings.MASTRAO_RECORDING_EFFECT_ISSUER = core_trust["issuer"]
    settings.MASTRAO_RECORDING_EFFECT_AUDIENCE = core_trust["audience"]
    settings.MASTRAO_RECORDING_EFFECT_KEY_ID = core_trust["kid"]
    settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK = json.dumps(core_trust["public_jwk"])
    settings.MASTRAO_CORE_LIVE_OBSERVED_DOMAIN_CONTEXT_ENDPOINT = context_endpoint
    settings.MASTRAO_CORE_LIVE_OBSERVED_DOMAIN_ENDPOINT = publication_endpoint
    if not domain.deliver_live_observed_domain(domain_ref):
        raise AssertionError("Actual parent Core context did not qualify")
    prepared = domain.prepare_live_observed_domain(domain_ref)
    if prepared is None:
        raise AssertionError("No eligible durable Python snapshot")
    first_snapshot = prepared.snapshot
    delivered = domain.deliver_live_observed_domain(domain_ref)
    if dropped_reply_expected:
        if delivered:
            raise AssertionError("Parent did not actually drop the publication reply")
        models.MastraoLiveObservedDomain.objects.filter(pk=domain_ref).update(
            next_attempt_at=timezone.now()
        )
        if not domain.deliver_live_observed_domain(domain_ref):
            raise AssertionError("Exact durable publication retry was not acknowledged")
    elif not delivered:
        raise AssertionError("Actual parent Core did not acknowledge publication")
    cursor = models.MastraoLiveObservedDomain.objects.get(pk=domain_ref)
    if cursor.snapshot != first_snapshot or cursor.producer_revision != 1:
        raise AssertionError("Transport retry reconstructed or changed semantic S")
    return {
        "snapshot": cursor.snapshot,
        "snapshot_digest": cursor.snapshot_digest,
        "request": publication_request(cursor.snapshot, uuid4().hex),
        "producer_revision": cursor.producer_revision,
        "fingerprints": cursor.revisions.count(),
        "dirty_generation": cursor.dirty_generation,
        "acked_generation": cursor.acked_generation,
        "acknowledged_revision": cursor.acknowledged_revision,
        "recording_bindings": models.MastraoRecordingBinding.objects.count(),
    }
