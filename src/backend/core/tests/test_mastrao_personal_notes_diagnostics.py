"""Opaque notes refusals retain only safe, bounded Core diagnostics."""

import logging
from unittest.mock import patch

from django.test import Client

import pytest
import requests

from core.mastrao_core_http import read_bounded_core_json
from core.mastrao_guest_grant import SESSION_COMPACT_GRANT_KEY
from core.mastrao_host_grant import SESSION_COMPACT_GRANTS_KEY
from core.mastrao_personal_notes import PersonalNoteRefused
from core.mastrao_platform_facade import PlatformFacadeError
from core.tests.test_mastrao_personal_notes import (
    EMPTY_NOTE,
    NONCE,
    TOKEN,
    _history_url,
    _participant,
    _room_url,
    _save_session,
    _upstream,
    notes_core_transport,
    notes_settings,
    notes_signing_key,
    room_binding,
)

__all__ = [
    "notes_core_transport",
    "notes_settings",
    "notes_signing_key",
    "room_binding",
]

pytestmark = pytest.mark.django_db

LOGGER = "core.mastrao_personal_notes"
LOG_FORMAT = "meet_personal_note_refused cause_code=%s upstream_status=%s core_code=%s"
OPAQUE_BODY = '{"message": "Notes personnelles indisponibles"}'.encode()
PRIVATE_VALUES = (
    "SECRET_NOTE",
    "SECRET_GRANT",
    "SECRET_TOKEN",
    "SECRET_SESSION",
    "SECRET_REQUEST_ID",
    "SECRET_MESSAGE",
    "SECRET_HEADERS",
)


@pytest.fixture(autouse=True)
def capture_diagnostics(caplog):
    """Capture the dedicated logger regardless of the test logging settings."""

    caplog.set_level(logging.WARNING, logger=LOGGER)


@pytest.fixture(name="guest_client")
def notes_guest_client(binding, signing_key):
    """Use real guest authority with the existing isolated Core transport."""

    client = Client()
    _participant(client, binding, signing_key)
    return client


def _contract_error(code="invalid_assertion"):
    return {
        "code": code,
        "request_id": "SECRET_REQUEST_ID",
        "message": " ".join(PRIVATE_VALUES),
    }


def _diagnostics(caplog):
    return [record for record in caplog.records if record.name == LOGGER]


def _assert_opaque_refusal(response, caplog, diagnostic, private_values=()):
    assert response.status_code == 404
    assert response.content == OPAQUE_BODY
    assert response.headers["Cache-Control"] == "private, no-store"
    records = _diagnostics(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.WARNING
    assert record.msg == LOG_FORMAT
    assert record.args == diagnostic
    assert record.getMessage() == LOG_FORMAT % diagnostic
    assert record.exc_info is None
    assert record.exc_text is None
    assert record.stack_info is None
    captured = repr(vars(record)) + response.content.decode()
    for value in (*PRIVATE_VALUES, *private_values):
        assert value not in captured


def test_local_refusal_defaults_have_no_core_metadata():
    """Constructing a refusal cannot imply that Core was contacted."""

    refusal = PersonalNoteRefused()
    assert refusal.status == 404
    assert refusal.core_http_status is None
    assert refusal.core_error_code is None


def test_missing_participant_is_logged_before_network(binding, core_transport, caplog):
    """Missing authority is diagnosed without attempting a Core request."""

    response = Client().get(_room_url(binding))

    _assert_opaque_refusal(response, caplog, ("local_before_core", None, None))
    core_transport.post.assert_not_called()


@pytest.mark.parametrize("kind", ["host", "guest"])
def test_invalid_participant_is_logged_before_network(
    binding, signing_key, core_transport, caplog, kind
):
    """Reject a changed bearer locally without disclosing its session projection."""

    client = Client()
    grant, compact = _participant(client, binding, signing_key, kind)
    if kind == "host":
        values = {SESSION_COMPACT_GRANTS_KEY: {grant.grant_ref: "SECRET_GRANT"}}
    else:
        values = {SESSION_COMPACT_GRANT_KEY: "SECRET_GRANT"}
    _save_session(client, values)

    response = client.get(_room_url(binding))

    _assert_opaque_refusal(
        response,
        caplog,
        ("local_before_core", None, None),
        (compact, grant.grant_ref, client.session.session_key, NONCE, TOKEN),
    )
    core_transport.post.assert_not_called()


@pytest.mark.parametrize(
    "participant_route",
    [
        pytest.param(("host", _room_url), id="host-room"),
        pytest.param(("guest", _room_url), id="guest-room"),
        pytest.param(("host", _history_url), id="host-history"),
        pytest.param(("guest", _history_url), id="guest-history"),
    ],
)
def test_core_contract_refusal_logs_only_safe_metadata(
    binding, signing_key, core_transport, caplog, participant_route
):
    """Real participant checks precede a closed diagnostic for an upstream refusal."""

    kind, url = participant_route
    client = Client()
    grant, compact = _participant(client, binding, signing_key, kind)
    upstream = _upstream(_contract_error(), 404)
    upstream.headers["authorization"] = f"Bearer {compact} SECRET_HEADERS"
    core_transport.post.return_value = upstream
    path = url(binding)

    response = client.put(
        path,
        data='{"content":"SECRET_NOTE","expected_revision":0}',
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer SECRET_TOKEN",
    )

    _assert_opaque_refusal(
        response,
        caplog,
        ("core_http_contract", 404, "invalid_assertion"),
        (
            compact,
            grant.grant_ref,
            grant.session_nonce_digest,
            client.session.session_key,
            binding.room_ref,
            binding.meeting_ref,
            path,
            NONCE,
            TOKEN,
        ),
    )
    core_transport.post.assert_called_once()
    upstream.close.assert_called_once()


@pytest.mark.parametrize(
    "code",
    [
        "invalid_assertion",
        "resource_unavailable",
        "runtime_busy",
        "service_unavailable",
    ],
)
def test_all_public_core_codes_are_recognized(
    guest_client, binding, core_transport, caplog, code
):
    """Only the fixed public code vocabulary can cross the diagnostic boundary."""

    core_transport.post.return_value = _upstream(_contract_error(code), 404)

    response = guest_client.get(_room_url(binding))

    _assert_opaque_refusal(response, caplog, ("core_http_contract", 404, code))


@pytest.mark.parametrize(
    "optional",
    [
        {"outcome": "failed"},
        {"reason_codes": []},
        {"outcome": "retry", "reason_codes": ["private_reason"]},
    ],
    ids=["outcome", "empty-reasons", "outcome-and-reasons"],
)
def test_valid_optional_contract_fields_are_not_logged(
    guest_client, binding, core_transport, caplog, optional
):
    """Recognize optional error fields without retaining their private values."""

    core_transport.post.return_value = _upstream({**_contract_error(), **optional}, 404)

    response = guest_client.get(_room_url(binding))

    _assert_opaque_refusal(
        response,
        caplog,
        ("core_http_contract", 404, "invalid_assertion"),
        ("private_reason",),
    )


@pytest.mark.parametrize("non_json", [False, True], ids=["generic-json", "non-json"])
def test_generic_core_404_is_distinct_from_local_and_contract_refusals(
    guest_client, binding, core_transport, caplog, non_json
):
    """Distinguish a non-contract HTTP response without claiming its route is absent."""

    upstream = _upstream({"message": " ".join(PRIVATE_VALUES)}, 404)
    if non_json:
        upstream.iter_content.return_value = [" ".join(PRIVATE_VALUES).encode()]
    upstream.headers["x-private"] = "SECRET_HEADERS"
    core_transport.post.return_value = upstream

    response = guest_client.get(_room_url(binding))

    _assert_opaque_refusal(response, caplog, ("core_http_non_contract", 404, None))
    core_transport.post.assert_called_once()
    upstream.close.assert_called_once()


@pytest.mark.parametrize(
    "code",
    ["SECRET_TOKEN", ["SECRET_GRANT"], {"private": "SECRET_NOTE"}],
    ids=["unknown-code", "list-code", "object-code"],
)
def test_unknown_or_unhashable_codes_are_never_logged(
    guest_client, binding, core_transport, caplog, code
):
    """Untrusted JSON codes cannot become log arguments or break error handling."""

    core_transport.post.return_value = _upstream(_contract_error(code), 404)

    response = guest_client.get(_room_url(binding))

    _assert_opaque_refusal(response, caplog, ("core_http_non_contract", 404, None))


@pytest.mark.parametrize(
    "fields",
    [
        {"request_id": None},
        {"message": "SECRET_MESSAGE" * 20},
        {"headers": {"authorization": "SECRET_TOKEN"}, "note": "SECRET_NOTE"},
    ],
    ids=["invalid-request-id", "oversized-message", "extra-private-fields"],
)
def test_allowlisted_code_requires_a_strict_core_envelope(
    guest_client, binding, core_transport, caplog, fields
):
    """An allowed code alone does not prove a valid Core error envelope."""

    core_transport.post.return_value = _upstream({**_contract_error(), **fields}, 404)

    response = guest_client.get(_room_url(binding))

    _assert_opaque_refusal(response, caplog, ("core_http_non_contract", 404, None))


@pytest.mark.parametrize(
    "optional",
    [
        {"outcome": ["SECRET_NOTE"]},
        {"reason_codes": "SECRET_TOKEN"},
        {"reason_codes": [{"private": "SECRET_GRANT"}]},
        {"reason_codes": ["private_reason"] * 21},
        {"reason_codes": ["x" * 81]},
    ],
    ids=["invalid-outcome", "scalar-reasons", "object-reason", "too-many", "too-long"],
)
def test_malformed_optional_fields_cannot_promote_a_contract_code(
    guest_client, binding, core_transport, caplog, optional
):
    """Malformed optional fields keep the upstream response unclassified."""

    core_transport.post.return_value = _upstream({**_contract_error(), **optional}, 404)

    response = guest_client.get(_room_url(binding))

    _assert_opaque_refusal(response, caplog, ("core_http_non_contract", 404, None))


def test_http_refusal_retains_only_public_metadata():
    """The exception carries no upstream message, request ID or body."""

    upstream = _upstream(_contract_error(), 404)

    with pytest.raises(PersonalNoteRefused) as caught:
        read_bounded_core_json(upstream, PersonalNoteRefused)

    refusal = caught.value
    assert refusal.core_http_status == 404
    assert refusal.core_error_code == "invalid_assertion"
    assert set(vars(refusal)) == {"status", "core_http_status", "core_error_code"}
    for value in PRIVATE_VALUES:
        assert value not in repr(vars(refusal)) + str(refusal)


def test_invalid_success_body_has_no_core_refusal_metadata():
    """An invalid successful response cannot be mistaken for an HTTP refusal."""

    with pytest.raises(PersonalNoteRefused) as caught:
        read_bounded_core_json(_upstream(["SECRET_NOTE"]), PersonalNoteRefused)

    assert caught.value.status == 503
    assert caught.value.core_http_status is None
    assert caught.value.core_error_code is None


def test_logging_handler_failure_preserves_opaque_response(
    guest_client, binding, core_transport
):
    """A broken handler cannot change the refusal or prevent response cleanup."""

    core_transport.post.return_value = _upstream(_contract_error(), 404)
    handler = logging.Handler()
    logger = logging.getLogger(LOGGER)
    with (
        patch.object(logger, "handlers", [handler]),
        patch.object(
            handler, "emit", side_effect=RuntimeError("SECRET_MESSAGE")
        ) as emit,
    ):
        response = guest_client.get(_room_url(binding))

    emit.assert_called_once()
    assert response.status_code == 404
    assert response.content == OPAQUE_BODY
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("status", [200, 409, 422, 500])
def test_success_and_non_404_refusals_do_not_emit_diagnostics(
    guest_client, binding, core_transport, caplog, status
):
    """Keep the event specific to the opaque 404 being investigated."""

    body = EMPTY_NOTE if status == 200 else _contract_error()
    core_transport.post.return_value = _upstream(body, status)

    response = guest_client.get(_room_url(binding))

    expected_status = 503 if status == 500 else status
    assert response.status_code == expected_status
    assert _diagnostics(caplog) == []


def test_transport_exception_does_not_emit_refusal_diagnostics(
    guest_client, binding, core_transport, caplog
):
    """A transport exception cannot manufacture evidence of an HTTP 404."""

    core_transport.post.side_effect = requests.Timeout("SECRET_TOKEN")

    response = guest_client.get(_room_url(binding))

    assert response.status_code == 503
    assert response.content == OPAQUE_BODY
    assert _diagnostics(caplog) == []


def test_platform_404_does_not_emit_core_refusal_diagnostics(binding, caplog):
    """Platform authority refusals remain distinct from the direct Core bridge."""

    client = Client()
    _save_session(client, {"oidc_access_token": TOKEN})
    with patch(
        "core.mastrao_personal_notes.request_platform",
        side_effect=PlatformFacadeError(status=404),
    ):
        response = client.get(_history_url(binding))

    assert response.status_code == 404
    assert response.content == OPAQUE_BODY
    assert _diagnostics(caplog) == []
