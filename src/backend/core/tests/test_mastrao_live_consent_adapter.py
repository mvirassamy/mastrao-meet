"""Live consent validates both boundaries without persistence or provider I/O."""

# Test names state each boundary invariant exercised by the shared host/guest fixture.
# pylint: disable=missing-function-docstring

import copy
import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import mastrao_live_consent as adapter
from core.mastrao_core_http import post_core_json
from core.mastrao_guest_contract import GUEST_BOOTSTRAP_TYPE, GuestHandoffRefused
from core.mastrao_host_contract import HOST_GRANT_TYPE, HostHandoffRefused
from core.mastrao_recording_contract import RecordingContractRefused, _sign
from core.mastrao_room_contract import (
    _base64url_decode,
    _base64url_encode,
    _canonical_json,
)

NOW = 2_000_000_000
GRANT_EXPIRES = NOW + 900
RETENTION_EXPIRES = NOW + 86_400
COMPACT_GRANT = "Z3JhbnQ.cGF5bG9hZA.c2ln"


@pytest.fixture(name="consent_peer", params=["host", "guest"])
def _consent_peer(request, settings, monkeypatch):
    """Keep local identity resolution mocked; the parent proves real sessions."""
    kind = request.param
    claims = {
        "type": HOST_GRANT_TYPE,
        "organization_external_id": "organization:test",
        "meeting_ref": "meeting_0123456789abcdef",
        "room_ref": "room_0123456789abcdef",
        "provider_binding_digest": "a" * 64,
        "grant_ref": "grant_0123456789abcdef",
        "credential_digest": "b" * 64,
        "platform_session_ref": "platform_0123456789abcdef",
        "expires_at": GRANT_EXPIRES,
        "host_ref": "host_0123456789abcdef",
    }
    ref = claims["host_ref"]
    if kind == "guest":
        claims["type"] = GUEST_BOOTSTRAP_TYPE
        ref = "guest_0123456789abcdef"
        claims["guest_ref"] = ref
        del claims["host_ref"]
        del claims["platform_session_ref"]
    room_binding = SimpleNamespace(
        pk="local-room-binding",
        meeting_ref=claims["meeting_ref"],
        room_ref=claims["room_ref"],
        provider_binding_digest=claims["provider_binding_digest"],
    )
    room = SimpleNamespace(mastrao_binding=room_binding)
    grant = SimpleNamespace(
        grant_ref=claims["grant_ref"],
        grant_digest=hashlib.sha256(COMPACT_GRANT.encode("ascii")).hexdigest(),
        credential_digest=claims["credential_digest"],
        meeting_ref=claims["meeting_ref"],
        room_ref=claims["room_ref"],
        provider_binding_digest=claims["provider_binding_digest"],
        room_binding_id=room_binding.pk,
        session_nonce_digest="c" * 64,
        expires_at=datetime.fromtimestamp(GRANT_EXPIRES, tz=UTC),
    )
    if kind == "host":
        grant.identity = SimpleNamespace(host_ref=ref)
        grant.platform_session_ref = claims["platform_session_ref"]
    else:
        grant.guest_ref = ref
        grant.organization_external_id = claims["organization_external_id"]
    participant = {
        "kind": kind,
        "ref": ref,
        "session_digest": grant.session_nonce_digest,
        "compact": COMPACT_GRANT,
        "claims": claims,
    }
    offer = {
        "version": 1,
        "organization_external_id": claims["organization_external_id"],
        "meeting_ref": claims["meeting_ref"],
        "room_ref": claims["room_ref"],
        "consent_ref": "consent_0123456789abcdef",
        "policy_ref": "policy_0123456789abcdef",
        "notice_version": adapter.NOTICE_VERSION,
        "notice_digest": adapter.NOTICE_DIGEST,
        "purpose": adapter.CONSENT_PURPOSE,
        "scope": adapter.CONSENT_SCOPE,
        "text": adapter.NOTICE_TEXT,
        "retention_expires_at": RETENTION_EXPIRES,
        "decision": "absent",
        "capture_authorized": False,
    }
    decision = {
        "decision_ref": "choice_0123456789abcdef",
        "decision": "accepted",
        **{
            name: offer[name]
            for name in (
                "consent_ref",
                "policy_ref",
                "notice_version",
                "notice_digest",
                "retention_expires_at",
            )
        },
    }
    browser = SimpleNamespace(session={"unchanged": "session-marker"})
    participant_mock = mock.Mock(return_value=participant)
    sign_mock = mock.Mock(return_value="c2lnbmVk.cmVxdWVzdA.c2ln")
    transport = mock.Mock(return_value=offer)
    monkeypatch.setattr(adapter, "_participant", participant_mock)
    monkeypatch.setattr(adapter, "_sign", sign_mock)
    monkeypatch.setattr(adapter, "post_core_json", transport)
    monkeypatch.setattr(adapter.time, "time", lambda: NOW)
    resolvers = {}
    for name in (
        "active_host_grant",
        "active_guest_grant",
        "active_host_close_grant",
        "active_guest_lifecycle_grant",
    ):
        resolver = mock.Mock(return_value=grant)
        monkeypatch.setattr(adapter, name, resolver)
        resolvers[name] = resolver
    settings.MASTRAO_RECORDING_RECEIPT_ISSUER = "meet:test"
    settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE = "core:test"
    settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS = 4
    settings.MASTRAO_CORE_LIVE_CONSENT_OFFER_ENDPOINT = (
        "http://cabinet-core:3911" + adapter.OFFER_PATH
    )
    settings.MASTRAO_CORE_LIVE_CONSENT_DECISION_ENDPOINT = (
        "http://cabinet-core:3911" + adapter.DECISION_PATH
    )
    return SimpleNamespace(
        kind=kind,
        request=browser,
        room=room,
        grant=grant,
        participant=participant,
        claims=claims,
        offer=offer,
        decision=decision,
        participant_mock=participant_mock,
        sign=sign_mock,
        transport=transport,
        resolvers=resolvers,
    )


def test_offer_binds_exact_participant_and_fresh_server_envelope(consent_peer):
    peer = consent_peer
    result = adapter.offer_live_consent(peer.request, peer.room)
    assert result == peer.offer
    payload, jose = peer.sign.call_args.args
    assert jose == "mastrao-live-transcript-consent-offer+jws"
    assert payload == {
        "version": 1,
        "type": "mastrao.meet-live-transcript-consent-offer",
        "issuer": "meet:test",
        "audience": "core:test",
        "issued_at": NOW,
        "expires_at": NOW + 30,
        "jti": payload["jti"],
        "organization_external_id": peer.claims["organization_external_id"],
        "meeting_ref": peer.claims["meeting_ref"],
        "room_ref": peer.claims["room_ref"],
        "provider_binding_digest": peer.claims["provider_binding_digest"],
        "participant_kind": peer.kind,
        "participant_ref": peer.participant["ref"],
        "participant_session_digest": "c" * 64,
        "grant_ref": peer.grant.grant_ref,
        "grant_digest": peer.grant.grant_digest,
    }
    assert adapter.OPAQUE_REFERENCE.fullmatch(payload["jti"])
    peer.transport.assert_called_once_with(
        endpoint="http://cabinet-core:3911" + adapter.OFFER_PATH,
        expected_path=adapter.OFFER_PATH,
        body={
            "request": peer.sign.return_value,
            "participant_grant": COMPACT_GRANT,
        },
        timeout=4,
        refusal=RecordingContractRefused,
        passthrough_statuses={409},
    )
    peer.participant_mock.assert_called_once_with(peer.request, peer.room)
    assert result["capture_authorized"] is False
    assert peer.request.session == {"unchanged": "session-marker"}


def test_offer_retry_uses_new_jti_without_changing_binding(consent_peer):
    peer = consent_peer
    adapter.offer_live_consent(peer.request, peer.room)
    adapter.offer_live_consent(peer.request, peer.room)
    first = dict(peer.sign.call_args_list[0].args[0])
    second = dict(peer.sign.call_args_list[1].args[0])
    assert first.pop("jti") != second.pop("jti")
    assert first == second


@pytest.mark.parametrize("decision", ["accepted", "refused", "withdrawn"])
def test_decision_forwards_displayed_snapshot_without_refresh(consent_peer, decision):
    peer = consent_peer
    peer.decision["decision"] = decision
    peer.offer["decision"] = decision
    result = adapter.record_live_consent(peer.request, peer.room, **peer.decision)
    assert result == peer.offer
    payload, jose = peer.sign.call_args.args
    assert jose == "mastrao-live-transcript-consent-decision+jws"
    assert payload["type"] == "mastrao.meet-live-transcript-consent-decision"
    assert payload["issued_at"] == NOW
    assert payload["expires_at"] == NOW + 30
    assert {name: payload[name] for name in peer.decision} == peer.decision
    assert payload["purpose"] == "meeting_live_transcription"
    assert payload["scope"] == "live_microphone_audio_transcript"
    assert "session_nonce_digest" not in payload
    assert payload["participant_session_digest"] == "c" * 64
    peer.transport.assert_called_once()
    call = peer.transport.call_args.kwargs
    assert call["expected_path"] == adapter.DECISION_PATH
    assert call["endpoint"] == "http://cabinet-core:3911" + adapter.DECISION_PATH
    assert call["body"] == {
        "request": peer.sign.return_value,
        "participant_grant": COMPACT_GRANT,
    }
    assert call["passthrough_statuses"] == {409}
    assert peer.request.session == {"unchanged": "session-marker"}
    if decision == "withdrawn":
        peer.participant_mock.assert_called_once_with(
            peer.request, peer.room, include_closed=True
        )
    else:
        peer.participant_mock.assert_called_once_with(peer.request, peer.room)


def test_accept_replay_can_return_authoritative_withdrawn_state(consent_peer):
    """Core returns the current choice when replaying an older acceptance."""
    peer = consent_peer
    peer.offer["decision"] = "withdrawn"
    result = adapter.record_live_consent(peer.request, peer.room, **peer.decision)
    assert result["decision"] == "withdrawn"
    assert result["capture_authorized"] is False


@pytest.mark.parametrize("decision", ["accepted", "refused", "withdrawn"])
def test_only_withdrawal_uses_closed_room_local_grant(consent_peer, decision):
    peer = consent_peer
    peer.decision["decision"] = decision
    peer.resolvers["active_host_grant"].return_value = None
    peer.resolvers["active_guest_grant"].return_value = None
    if decision == "withdrawn":
        adapter.record_live_consent(peer.request, peer.room, **peer.decision)
        name = "active_host_close_grant"
        if peer.kind == "guest":
            name = "active_guest_lifecycle_grant"
        peer.resolvers[name].assert_called_once_with(peer.request, peer.room)
    else:
        with pytest.raises(RecordingContractRefused):
            adapter.record_live_consent(peer.request, peer.room, **peer.decision)
        peer.transport.assert_not_called()
        peer.resolvers["active_host_close_grant"].assert_not_called()
        peer.resolvers["active_guest_lifecycle_grant"].assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "absent"),
        ("decision", True),
        ("decision", []),
        ("decision_ref", "short"),
        ("decision_ref", "d" * 161),
        ("consent_ref", "consent.with.dots"),
        ("consent_ref", None),
        ("policy_ref", "p" * 15),
        ("policy_ref", 123),
        ("notice_version", "another_live_notice_v2"),
        ("notice_digest", "d" * 64),
        ("retention_expires_at", True),
        ("retention_expires_at", NOW),
        ("retention_expires_at", -1),
        ("retention_expires_at", RETENTION_EXPIRES + 0.5),
        ("retention_expires_at", "2000086400"),
        ("retention_expires_at", 9_007_199_254_740_992),
    ],
)
def test_bad_decision_rejected_before_identity_signing_and_io(
    consent_peer, field, value
):
    peer = consent_peer
    peer.decision[field] = value
    with pytest.raises(RecordingContractRefused):
        adapter.record_live_consent(peer.request, peer.room, **peer.decision)
    peer.participant_mock.assert_not_called()
    peer.sign.assert_not_called()
    peer.transport.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("version", 1.0),
        ("version", 2),
        ("organization_external_id", "other-organization"),
        ("meeting_ref", "other_meeting_0123456789abcdef"),
        ("room_ref", "other_room_0123456789abcdef"),
        ("consent_ref", "short"),
        ("consent_ref", 123),
        ("policy_ref", "p" * 161),
        ("policy_ref", []),
        ("notice_version", "another_live_notice_v2"),
        ("notice_digest", "d" * 64),
        ("text", "A different notice"),
        ("purpose", "meeting_recording"),
        ("scope", "room_composite_audio_video_screen"),
        ("decision", "authorized"),
        ("decision", []),
        ("capture_authorized", True),
        ("capture_authorized", 0),
        ("retention_expires_at", True),
        ("retention_expires_at", NOW),
        ("retention_expires_at", 9_007_199_254_740_992),
        ("retention_expires_at", RETENTION_EXPIRES + 0.5),
    ],
)
def test_rejects_noncanonical_core_response(consent_peer, field, value):
    peer = consent_peer
    peer.offer[field] = value
    with pytest.raises(RecordingContractRefused) as error:
        adapter.offer_live_consent(peer.request, peer.room)
    assert error.value.status == 503


@pytest.mark.parametrize("shape", ["nested", "extra", "missing", "list", "null"])
def test_response_requires_exact_flat_fields(consent_peer, shape):
    peer = consent_peer
    result = copy.deepcopy(peer.offer)
    if shape == "nested":
        result = {"offer": result}
    elif shape == "extra":
        result["participant_grant"] = COMPACT_GRANT
    elif shape == "missing":
        del result["capture_authorized"]
    elif shape == "list":
        result = [result]
    else:
        result = None
    peer.transport.return_value = result
    with pytest.raises(RecordingContractRefused) as error:
        adapter.offer_live_consent(peer.request, peer.room)
    assert error.value.status == 503


@pytest.mark.parametrize(
    "field,value",
    [
        ("consent_ref", "consent_another_0123456789abcdef"),
        ("policy_ref", "policy_another_0123456789abcdef"),
        ("retention_expires_at", RETENTION_EXPIRES + 3600),
    ],
)
def test_decision_response_cannot_replace_browser_snapshot(consent_peer, field, value):
    peer = consent_peer
    peer.offer[field] = value
    with pytest.raises(RecordingContractRefused) as error:
        adapter.record_live_consent(peer.request, peer.room, **peer.decision)
    assert error.value.status == 503
    payload = peer.sign.call_args.args[0]
    assert payload[field] == peer.decision[field]
    peer.transport.assert_called_once()


@pytest.mark.parametrize(
    "field,value",
    [
        ("meeting_ref", "other_meeting_0123456789abcdef"),
        ("room_ref", "other_room_0123456789abcdef"),
        ("provider_binding_digest", "d" * 64),
        ("grant_ref", "other_grant_0123456789abcdef"),
        ("type", "mastrao.core-unrelated-grant"),
        ("credential_digest", "e" * 64),
        ("expires_at", GRANT_EXPIRES + 1),
    ],
)
def test_signed_claims_must_match_local_grant_and_room(consent_peer, field, value):
    peer = consent_peer
    peer.claims[field] = value
    with pytest.raises(RecordingContractRefused):
        adapter.offer_live_consent(peer.request, peer.room)
    peer.sign.assert_not_called()
    peer.transport.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("grant_ref", "other_grant_0123456789abcdef"),
        ("grant_digest", "e" * 64),
        ("session_nonce_digest", "e" * 64),
        ("room_binding_id", "different-local-room"),
        ("meeting_ref", "other_meeting_0123456789abcdef"),
        ("room_ref", "other_room_0123456789abcdef"),
        ("provider_binding_digest", "e" * 64),
    ],
)
def test_local_grant_cannot_be_substituted(consent_peer, field, value):
    peer = consent_peer
    setattr(peer.grant, field, value)
    with pytest.raises(RecordingContractRefused):
        adapter.offer_live_consent(peer.request, peer.room)
    peer.sign.assert_not_called()
    peer.transport.assert_not_called()


def test_local_identity_and_signed_reference_must_match(consent_peer):
    peer = consent_peer
    if peer.kind == "host":
        peer.grant.identity.host_ref = "other_host_0123456789abcdef"
    else:
        peer.grant.guest_ref = "other_guest_0123456789abcdef"
    with pytest.raises(RecordingContractRefused):
        adapter.offer_live_consent(peer.request, peer.room)
    peer.transport.assert_not_called()


def test_signed_reference_must_match_participant_projection(consent_peer):
    peer = consent_peer
    name = "host_ref"
    if peer.kind == "guest":
        name = "guest_ref"
    peer.claims[name] = "different_person_0123456789abcdef"
    with pytest.raises(RecordingContractRefused):
        adapter.offer_live_consent(peer.request, peer.room)
    peer.transport.assert_not_called()


def test_kind_specific_session_or_organization_mismatch_is_refused(consent_peer):
    peer = consent_peer
    if peer.kind == "host":
        peer.grant.platform_session_ref = "different_platform_0123456789abcdef"
    else:
        peer.grant.organization_external_id = "different:organization"
    with pytest.raises(RecordingContractRefused):
        adapter.offer_live_consent(peer.request, peer.room)
    peer.transport.assert_not_called()


@pytest.mark.parametrize(
    "field", ["meeting_ref", "room_ref", "provider_binding_digest"]
)
def test_signed_claims_cannot_redirect_to_another_local_room(consent_peer, field):
    peer = consent_peer
    setattr(peer.room.mastrao_binding, field, "different_local_binding")
    with pytest.raises(RecordingContractRefused):
        adapter.offer_live_consent(peer.request, peer.room)
    peer.transport.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("organization_external_id", "not an external id"),
        ("meeting_ref", "short"),
        ("grant_ref", "grant.with.dots_0123456789abcdef"),
        ("provider_binding_digest", "a" * 63),
    ],
)
def test_even_consistent_local_values_must_meet_schema(consent_peer, field, value):
    peer = consent_peer
    peer.claims[field] = value
    setattr(peer.grant, field, value)
    if hasattr(peer.room.mastrao_binding, field):
        setattr(peer.room.mastrao_binding, field, value)
    with pytest.raises(RecordingContractRefused):
        adapter.offer_live_consent(peer.request, peer.room)
    peer.sign.assert_not_called()
    peer.transport.assert_not_called()


@pytest.mark.parametrize("field,value", [("kind", "admin"), ("compact", "not-a-jws")])
def test_invalid_participant_projection_is_not_signed(consent_peer, field, value):
    peer = consent_peer
    peer.participant[field] = value
    with pytest.raises(RecordingContractRefused):
        adapter.offer_live_consent(peer.request, peer.room)
    peer.sign.assert_not_called()
    peer.transport.assert_not_called()


def test_missing_room_binding_is_refused_without_identity_lookup(consent_peer):
    peer = consent_peer
    with pytest.raises(RecordingContractRefused):
        adapter.offer_live_consent(peer.request, SimpleNamespace())
    peer.participant_mock.assert_not_called()
    peer.transport.assert_not_called()


@pytest.mark.parametrize("error_type", [HostHandoffRefused, GuestHandoffRefused])
def test_participant_errors_are_left_for_parent_http_boundary(consent_peer, error_type):
    peer = consent_peer
    peer.participant_mock.side_effect = error_type(status=404)
    with pytest.raises(error_type):
        adapter.offer_live_consent(peer.request, peer.room)
    peer.sign.assert_not_called()
    peer.transport.assert_not_called()


@pytest.mark.parametrize("action", ["offer", "decision"])
def test_core_conflict_survives_real_shared_transport(
    consent_peer, monkeypatch, action
):
    peer = consent_peer
    monkeypatch.setattr(adapter, "post_core_json", post_core_json)
    response = mock.Mock(
        status_code=409,
        headers={},
        iter_content=mock.Mock(return_value=[b"{}"]),
    )
    session = mock.MagicMock()
    session.__enter__.return_value = session
    session.post.return_value = response
    with mock.patch("core.mastrao_core_http.requests.Session", return_value=session):
        with pytest.raises(RecordingContractRefused) as error:
            if action == "offer":
                adapter.offer_live_consent(peer.request, peer.room)
            else:
                adapter.record_live_consent(peer.request, peer.room, **peer.decision)
    assert error.value.status == 409
    assert session.trust_env is False
    assert session.post.call_args.kwargs["allow_redirects"] is False
    response.close.assert_called_once_with()


@pytest.mark.parametrize("action", ["offer", "decision"])
def test_actual_receipt_signature_has_purpose_specific_jose(
    consent_peer, settings, monkeypatch, action
):
    peer = consent_peer
    key = Ed25519PrivateKey.generate()
    settings.MASTRAO_RECORDING_RECEIPT_KEY_ID = "live-consent-test-key"
    settings.MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK = json.dumps(
        {
            "kty": "OKP",
            "crv": "Ed25519",
            "d": _base64url_encode(key.private_bytes_raw()),
        }
    )
    monkeypatch.setattr(adapter, "_sign", _sign)
    expected_jose = adapter.OFFER_JOSE
    if action == "offer":
        adapter.offer_live_consent(peer.request, peer.room)
    else:
        expected_jose = adapter.DECISION_JOSE
        adapter.record_live_consent(peer.request, peer.room, **peer.decision)
    compact = peer.transport.call_args.kwargs["body"]["request"]
    protected, encoded, signature = compact.split(".")
    assert json.loads(_base64url_decode(protected)) == {
        "alg": "EdDSA",
        "kid": "live-consent-test-key",
        "typ": expected_jose,
    }
    key.public_key().verify(
        _base64url_decode(signature), f"{protected}.{encoded}".encode("ascii")
    )
    payload_bytes = _base64url_decode(encoded)
    payload = json.loads(payload_bytes)
    assert payload_bytes == _canonical_json(payload)
    assert payload["expires_at"] - payload["issued_at"] == 30
    assert payload["participant_session_digest"] == "c" * 64
    assert "session_nonce_digest" not in payload


def test_notice_text_matches_its_canonical_contract_digest():
    assert hashlib.sha256(
        _canonical_json({"text": adapter.NOTICE_TEXT})
    ).hexdigest() == (
        "19a76d62a39b5c4c3211d17720908bf474fe3080eec010ea0c0470db8c40d249"
    )


@pytest.mark.parametrize(
    "notice_version,digest,disclosure",
    [
        (
            "live_microphone_fr_v1",
            "19a76d62a39b5c4c3211d17720908bf474fe3080eec010ea0c0470db8c40d249",
            "OpenAI, avec le modèle gpt-live-transcribe",
        ),
        (
            "live_microphone_deepgram_fr_v1",
            "075ce191193d455e6769a2691455c21eea171eca4ef6d0b8adfff9330241b8ed",
            "Deepgram, avec le modèle nova-3",
        ),
        (
            "live_microphone_mistral_fr_v1",
            "796222a034349c74ece96d66ace0632ff3ae26cba6038ac211c18f54a2c5b02e",
            "Mistral, avec le modèle voxtral-mini-transcribe-realtime-3",
        ),
    ],
)
@pytest.mark.parametrize("decision", ["accepted", "refused", "withdrawn"])
def test_provider_disclosure_roundtrip_keeps_displayed_notice(
    consent_peer, notice_version, digest, disclosure, decision
):
    peer = consent_peer
    text = adapter.NOTICE_TEXT.replace(
        "OpenAI, avec le modèle gpt-live-transcribe", disclosure
    )
    assert hashlib.sha256(_canonical_json({"text": text})).hexdigest() == digest
    peer.offer.update(notice_version=notice_version, notice_digest=digest, text=text)
    peer.decision.update(
        notice_version=notice_version, notice_digest=digest, decision=decision
    )
    displayed = adapter.offer_live_consent(peer.request, peer.room)
    assert displayed["text"] == text
    assert displayed["capture_authorized"] is False
    peer.offer["decision"] = decision
    result = adapter.record_live_consent(peer.request, peer.room, **peer.decision)
    assertion = peer.sign.call_args.args[0]
    assert assertion["notice_version"] == notice_version
    assert assertion["notice_digest"] == digest
    assert result["capture_authorized"] is False
    assert peer.request.session == {"unchanged": "session-marker"}


@pytest.mark.parametrize(
    "notice_version",
    [
        "live_microphone_deepgram_fr_v1",
        "live_microphone_mistral_fr_v1",
    ],
)
def test_rejects_mixed_provider_disclosure_or_missing_text(
    consent_peer, notice_version
):
    peer = consent_peer
    digest, text = adapter.APPROVED_NOTICES[notice_version]
    peer.offer.update(notice_version=notice_version, notice_digest=digest, text=text)
    for changes in (
        {"notice_digest": adapter.NOTICE_DIGEST},
        {"text": adapter.NOTICE_TEXT},
        {"text": None},
        {"text": text + " autre traitement"},
    ):
        peer.transport.return_value = {**peer.offer, **changes}
        with pytest.raises(RecordingContractRefused) as error:
            adapter.offer_live_consent(peer.request, peer.room)
        assert error.value.status == 503


@pytest.mark.parametrize("digest", [adapter.NOTICE_DIGEST, "f" * 64])
def test_mistral_forged_decision_digest_is_rejected_before_signing(
    consent_peer, digest
):
    peer = consent_peer
    peer.decision.update(
        notice_version="live_microphone_mistral_fr_v1", notice_digest=digest
    )
    with pytest.raises(RecordingContractRefused):
        adapter.record_live_consent(peer.request, peer.room, **peer.decision)
    peer.participant_mock.assert_not_called()
    peer.sign.assert_not_called()
    peer.transport.assert_not_called()


def test_decision_response_cannot_substitute_an_approved_mistral_notice(consent_peer):
    peer = consent_peer
    digest, text = adapter.APPROVED_NOTICES["live_microphone_mistral_fr_v1"]
    peer.offer.update(
        notice_version="live_microphone_mistral_fr_v1", notice_digest=digest, text=text
    )
    with pytest.raises(RecordingContractRefused) as error:
        adapter.record_live_consent(peer.request, peer.room, **peer.decision)
    assert error.value.status == 503
    assert peer.sign.call_args.args[0]["notice_version"] == "live_microphone_fr_v1"
