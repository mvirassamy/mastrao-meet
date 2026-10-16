"""Deterministic assistant transport and private-source boundary checks."""

import copy
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.conf import settings as django_settings
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory

import pytest
import requests

from core.mastrao_meeting_assistant import (
    ASSISTANT_TIMEOUT_SECONDS,
    MAX_ASSISTANT_RESPONSE_BYTES,
    MAX_COMMAND_BYTES,
    _citation_command,
    _delete_command,
    _send_command,
    _validate_opened,
    _validate_sent,
    _validate_thread,
)
from core.mastrao_platform_facade import PlatformFacadeError

MEETING = "meeting_0123456789abcdef"
PLATFORM = "https://platform.example.com"
TOKEN = "test-oidc-assistant-token-123456"
ASSISTANT_URL = f"/api/v1.0/meetings/history/{MEETING}/assistant/"
CITATION_URL = f"{ASSISTANT_URL}citation/"
COMMAND = {
    "version": 1,
    "meeting_ref": MEETING,
    "command_id": "command_0123456789abcdef",
    "operation": "question",
    "question": "Quel délai a été confirmé ?",
    "expected_thread_version": 0,
}
CITATION = {
    "citation_id": "citation_0123456789abcdef",
    "meeting_ref": MEETING,
    "source_kind": "provider_run",
    "source_ref": "transcription_0123456789abcdef",
    "source_version": 1,
    "source_digest": "a" * 64,
    "segment_id": "segment_1",
    "start_ms": 1000,
    "end_ms": 2000,
    "time_basis": "meeting_relative_ms",
}
COVERAGE = {
    "state": "partial",
    "source_coverage": "partial_sources",
    "selection": "relevant_passages",
    "total_segments": 10,
    "selected_segments": 2,
    "omitted_segments": 8,
}
RESULT = {
    "state": "answered",
    "text": "Le délai confirmé est vendredi.",
    "rationale_category": "supported",
    "citations": [CITATION],
    "actions": [],
    "email_draft": None,
    "publication_state": "private_proposal",
}
TURN = {
    "command_id": COMMAND["command_id"],
    "operation": "question",
    "question": COMMAND["question"],
    "state": "completed",
    "result": RESULT,
    "coverage": COVERAGE,
    "created_at": 1_800_000_000,
}
THREAD = {
    "version": 1,
    "meeting_ref": MEETING,
    "thread_ref": "thread_0123456789abcdef",
    "thread_version": 1,
    "visibility": "private",
    "deleted": False,
    "turns": [TURN],
    "has_more": False,
    "next_cursor": None,
}


@pytest.fixture(autouse=True)
def bridge_settings(settings):
    """Keep Django HTTP checks independent of secret files, databases and Redis."""

    settings.SECRET_KEY = "assistant-test-key"
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
    settings.MASTRAO_PLATFORM_API_BASE_URL = PLATFORM


@pytest.fixture(name="real_platform_core_postgres")
def _real_platform_core_postgres(settings):
    """Connect only to the explicit disposable qualification, without secrets.

    The external runner owns PostgreSQL and both HTTP listeners. Its userinfo,
    live identity authority, source passage and inference are synthetic. Django
    ingress uses Client; requests.Session and Platform/Core clients are unpatched.
    """

    path = os.environ.get("MASTRAO_MEETING_BRIDGE_QUALIFICATION_CONFIG")
    if not path:
        pytest.skip("Requires the disposable Platform/Core PostgreSQL qualification")
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    origin = config["platform_base_url"]
    assert origin.startswith("http://127.0.0.1:")
    settings.MASTRAO_PLATFORM_API_BASE_URL = origin
    return config


def test_opt_in_real_platform_core_postgres_classification(real_platform_core_postgres):
    """Classify through real HTTP, replay once and reload authorized navigation."""

    config = real_platform_core_postgres
    meeting_ref = config["meeting_ref"]
    classification_url = f"/api/v1.0/meetings/history/{meeting_ref}/classification/"
    client = _client(token="synthetic-owner-oidc-token", csrf=True)
    initial = client.get(classification_url)
    assert initial.status_code == 200
    command = {
        "version": 1,
        "meeting_ref": meeting_ref,
        "command_id": str(uuid4()),
        "matter_ref": config["matter_ref"],
        "expected_classification_version": initial.json()["classification_version"],
    }
    assert _call(client, "POST", classification_url, command).status_code == 403
    token_request = RequestFactory().get("/")
    csrf_token = get_token(token_request)
    client.cookies[django_settings.CSRF_COOKIE_NAME] = token_request.META["CSRF_COOKIE"]
    headers = {"HTTP_X_CSRFTOKEN": csrf_token}
    classified = _call(client, "POST", classification_url, command, **headers)
    assert classified.status_code == 200
    assert classified.json()["idempotent_replay"] is False
    assert (
        classified.json()["matter_display_label"] == "Synthetic qualification dossier"
    )
    assert classified.json()["matter_path"] == config["matter_path"]
    replay = _call(client, "POST", classification_url, command, **headers)
    assert replay.status_code == 200
    assert replay.json() == {**classified.json(), "idempotent_replay": True}
    reloaded = client.get(classification_url)
    assert reloaded.status_code == 200
    assert reloaded.json() == {
        key: value
        for key, value in classified.json().items()
        if key != "idempotent_replay"
    }


def test_opt_in_real_platform_core_postgres_assistant(real_platform_core_postgres):
    """Persist a private question and reopen its bound synthetic passage over HTTP."""

    meeting_ref = real_platform_core_postgres["meeting_ref"]
    assistant_url = f"/api/v1.0/meetings/history/{meeting_ref}/assistant/"
    client = _client(token="synthetic-owner-oidc-token", csrf=True)
    token_request = RequestFactory().get("/")
    csrf_token = get_token(token_request)
    client.cookies[django_settings.CSRF_COOKIE_NAME] = token_request.META["CSRF_COOKIE"]
    headers = {"HTTP_X_CSRFTOKEN": csrf_token}
    question = {
        "version": 1,
        "meeting_ref": meeting_ref,
        "command_id": str(uuid4()),
        "operation": "question",
        "question": "Qui prépare le devis ?",
        "expected_thread_version": 0,
    }
    answered = _call(client, "POST", assistant_url, question, **headers)
    assert answered.status_code == 200
    body = answered.json()
    assert body["idempotent_replay"] is False
    assert body["turn"]["state"] == "completed"
    assert body["turn"]["result"]["text"] == "Alice prépare le devis."
    question_replay = _call(client, "POST", assistant_url, question, **headers)
    assert question_replay.status_code == 200
    assert question_replay.json() == {**body, "idempotent_replay": True}
    page_query = {"before_command_id": question["command_id"], "limit": "1"}
    page = client.get(assistant_url, page_query)
    assert page.status_code == 200
    assert page.json()["turns"] == []
    other_page = _client(token="synthetic-reader-oidc-token").get(
        assistant_url, page_query
    )
    assert other_page.status_code == 404
    assert question["question"] not in other_page.content.decode()
    citation = body["turn"]["result"]["citations"][0]
    citation_command = {
        "version": 1,
        "meeting_ref": meeting_ref,
        "command_id": str(uuid4()),
        "citation": citation,
    }
    opened = _call(
        client, "POST", f"{assistant_url}citation/", citation_command, **headers
    )
    assert opened.status_code == 200
    assert opened.json()["segment"]["citation"] == citation
    assert opened.json()["segment"]["text"] == "Alice prépare le devis."
    altered = {
        **citation_command,
        "command_id": str(uuid4()),
        "citation": {**citation, "source_digest": "b" * 64},
    }
    assert (
        _call(
            client, "POST", f"{assistant_url}citation/", altered, **headers
        ).status_code
        == 404
    )
    guest = _client(token=None)
    assert (
        guest.get(
            assistant_url, HTTP_AUTHORIZATION="Bearer synthetic-owner-oidc-token"
        ).status_code
        == 401
    )
    invalid = _client(token="synthetic-invalid-oidc-token")
    assert invalid.get(assistant_url).status_code == 401
    assert "oidc_access_token" not in invalid.session


def test_opt_in_real_platform_core_postgres_history(real_platform_core_postgres):
    """The default first history page must reach Core without a cursor."""

    meeting_ref = real_platform_core_postgres["meeting_ref"]
    assistant_url = f"/api/v1.0/meetings/history/{meeting_ref}/assistant/"
    history = _client(token="synthetic-owner-oidc-token").get(assistant_url)
    assert history.status_code == 200
    assert history.json()["visibility"] == "private"
    assert all(
        turn["question"] == "Qui prépare le devis ?" for turn in history.json()["turns"]
    )
    other_history = _client(token="synthetic-reader-oidc-token").get(assistant_url)
    assert other_history.status_code == 200
    assert other_history.json()["turns"] == []


def _request(body):
    return RequestFactory().post("/", json.dumps(body), content_type="application/json")


@pytest.mark.parametrize("operation", ["question", "summary", "actions", "email_draft"])
def test_operations_keep_the_exact_command_identity_and_thread_version(operation):
    """Every F07/F08 operation shares Core's durable command envelope."""

    command = {**COMMAND, "operation": operation}
    assert _send_command(_request(command), MEETING) == command


@pytest.mark.parametrize(
    "changes",
    [
        {"actor_ref": "other"},
        {"organization_ref": "other"},
        {"participant_grant": "guest"},
        {"personal_notes": "private"},
        {"meeting_ref": "meeting_other0123456789"},
        {"thread_ref": "thread_other0123456789"},
        {"operation": "send_email"},
        {"operation": []},
        {"expected_thread_version": True},
        {"expected_thread_version": -1},
        {"command_id": "short"},
        {"question": " "},
        {"question": "x" * 1025},
        {"question": "\ud800"},
    ],
)
def test_browser_cannot_choose_identity_scope_or_external_actions(changes):
    """Refuse identity injection, unbound resources and unsupported operations."""

    with pytest.raises(PlatformFacadeError) as refused:
        _send_command(_request({**COMMAND, **changes}), MEETING)
    assert refused.value.status == 422


def test_command_body_remains_bounded_without_content_length():
    """Enforce the streamed JSON budget before transport can begin."""

    request = RequestFactory().post(
        "/", b" " * (MAX_COMMAND_BYTES + 1), content_type="application/json"
    )
    request.META.pop("CONTENT_LENGTH")
    with pytest.raises(PlatformFacadeError) as refused:
        _send_command(request, MEETING)
    assert refused.value.status == 422


def test_duplicate_command_fields_are_rejected():
    """Reject ambiguous command identity rather than keeping the last value."""

    raw = json.dumps(COMMAND).replace('"version": 1', '"version": 1, "version": 1')
    request = RequestFactory().post("/", raw, content_type="application/json")
    with pytest.raises(PlatformFacadeError) as refused:
        _send_command(request, MEETING)
    assert refused.value.status == 422


def test_delete_only_carries_the_private_thread_revision_and_command():
    """Deletion uses the authenticated author's current thread, without an author selector."""

    command = {
        field: COMMAND[field]
        for field in ("version", "meeting_ref", "command_id", "expected_thread_version")
    }
    assert _delete_command(_request(command), MEETING) == command


@pytest.mark.parametrize(
    "changes",
    [
        {"meeting_ref": "meeting_other0123456789"},
        {"source_url": "https://private.example"},
        {"source_digest": "wrong"},
        {"end_ms": 999},
        {"source_kind": []},
        {"source_version": True},
    ],
)
def test_citation_opening_cannot_cross_a_meeting_or_inject_a_private_url(changes):
    """Only a complete, version-bound citation can reach the source resolver."""

    command = {
        field: COMMAND[field] for field in ("version", "meeting_ref", "command_id")
    }
    command["citation"] = {**CITATION, **changes}
    with pytest.raises(PlatformFacadeError) as refused:
        _citation_command(_request(command), MEETING)
    assert refused.value.status == 422


def test_thread_preserves_partial_coverage_and_canonical_citation():
    """A response keeps useful coverage limits and the source's exact version."""

    value = copy.deepcopy(THREAD)
    _validate_thread(value, MEETING)
    assert value == THREAD


@pytest.mark.parametrize(
    "state", ["failed", "unknown", "processing", "sources_unavailable"]
)
def test_technical_and_access_states_are_not_factual_abstentions(state):
    """Keep technical turn states distinct from a successful insufficient-evidence answer."""

    value = copy.deepcopy(THREAD)
    value["turns"][0].update(state=state, result=None)
    _validate_thread(value, MEETING)
    assert value["turns"][0]["state"] == state
    assert value["turns"][0]["result"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"visibility": "shared"},
        {"organization_ref": "other"},
        {"meeting_ref": "meeting_other0123456789"},
        {"thread_version": True},
        {"next_cursor": "short"},
    ],
)
def test_unbound_or_uncontracted_private_history_is_not_forwarded(changes):
    """An upstream mismatch cannot disclose another scope's private thread."""

    with pytest.raises(PlatformFacadeError) as refused:
        _validate_thread({**THREAD, **changes}, MEETING)
    assert refused.value.status == 503


@pytest.mark.parametrize("location", ["result", "citation", "coverage", "turn"])
def test_nested_uncontracted_fields_are_not_forwarded(location):
    """Reject private URLs and unknown fields even when nested in otherwise valid output."""

    value = copy.deepcopy(THREAD)
    turn = value["turns"][0]
    locations = {
        "result": turn["result"],
        "citation": turn["result"]["citations"][0],
        "coverage": turn["coverage"],
        "turn": turn,
    }
    locations[location]["private_url"] = "https://private.example"
    with pytest.raises(PlatformFacadeError) as refused:
        _validate_thread(value, MEETING)
    assert refused.value.status == 503


def test_send_receipt_must_match_the_command_and_operation():
    """An unrelated durable turn cannot be shown as this command's result."""

    value = {
        "version": 1,
        "thread": THREAD,
        "turn": {**TURN, "operation": "email_draft"},
        "idempotent_replay": False,
    }
    with pytest.raises(PlatformFacadeError) as refused:
        _validate_sent(value, MEETING, COMMAND)
    assert refused.value.status == 503


def test_source_response_must_match_the_requested_version_and_passage():
    """Open real text only after the resolver returns the exact authorized citation."""

    value = {
        "version": 1,
        "segment": {
            "citation": CITATION,
            "text": "Le délai est vendredi.",
            "speaker": None,
            "status": "transcript",
        },
    }
    _validate_opened(value, MEETING, CITATION)
    value["segment"]["citation"] = {**CITATION, "source_version": 2}
    with pytest.raises(PlatformFacadeError) as refused:
        _validate_opened(value, MEETING, CITATION)
    assert refused.value.status == 503


def _client(*, token=TOKEN, csrf=False):
    client = Client(enforce_csrf_checks=csrf)
    session = client.session
    if token is not None:
        session["oidc_access_token"] = token
    session["mastrao_guest_grant_ref"] = "guest_0123456789abcdef"
    session.save()
    client.cookies[django_settings.SESSION_COOKIE_NAME] = session.session_key
    return client


def _upstream(body, status=200):
    response = MagicMock()
    payload = json.dumps(body).encode()
    response.status_code = status
    response.headers = {"content-length": str(len(payload))}
    response.iter_content.return_value = [payload]
    session = MagicMock()
    session.__enter__.return_value = session
    session.request.return_value = response
    return session


def _delete_payload():
    return {
        field: COMMAND[field]
        for field in ("version", "meeting_ref", "command_id", "expected_thread_version")
    }


def _citation_payload():
    return {
        "version": 1,
        "meeting_ref": MEETING,
        "command_id": COMMAND["command_id"],
        "citation": CITATION,
    }


def _send_receipt(operation="question", replay=False):
    turn = {**TURN, "operation": operation}
    return {
        "version": 1,
        "thread": {**THREAD, "turns": [turn]},
        "turn": turn,
        "idempotent_replay": replay,
    }


def _call(client, method, url=ASSISTANT_URL, payload=None, **headers):
    if method == "GET":
        return client.get(url, **headers)
    return getattr(client, method.lower())(
        url, json.dumps(payload), content_type="application/json", **headers
    )


def test_http_history_forwards_only_bounded_pagination_and_session_bearer():
    """Read the durable private history through the real Django URL and facade."""

    session = _upstream(THREAD)
    query = f"before_command_id={COMMAND['command_id']}&limit=10"
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _client().get(f"{ASSISTANT_URL}?{query}")
    assert response.status_code == 200
    assert response.json() == THREAD
    session.request.assert_called_once_with(
        "GET",
        f"{PLATFORM}/api/meet/meetings/history/{MEETING}/assistant?{query}",
        headers={"authorization": f"Bearer {TOKEN}"},
        timeout=ASSISTANT_TIMEOUT_SECONDS,
        allow_redirects=False,
        stream=True,
    )
    assert session.trust_env is False
    assert response.headers["Cache-Control"] == "private, no-store"
    assert TOKEN not in response.content.decode()


@pytest.mark.parametrize(
    "query",
    [
        "actor_ref=other",
        "limit=21",
        "limit=0",
        "limit=1&limit=2",
        "before_command_id=short",
        "before_command_id=a&before_command_id=b",
        "limit=" + "1" * 5000,
    ],
)
def test_http_history_rejects_scope_selection_and_ambiguous_pagination(query):
    """Invalid browser pagination cannot broaden the private read scope."""

    with patch("core.mastrao_platform_facade.requests.Session") as upstream:
        response = _client().get(f"{ASSISTANT_URL}?{query}")
    assert response.status_code == 422
    upstream.assert_not_called()


@pytest.mark.parametrize(
    "method,url,payload",
    [
        ("GET", ASSISTANT_URL, None),
        ("POST", ASSISTANT_URL, COMMAND),
        ("DELETE", ASSISTANT_URL, _delete_payload()),
        ("POST", CITATION_URL, _citation_payload()),
    ],
)
def test_http_guest_grants_and_browser_bearers_do_not_authorize_business_access(
    method, url, payload
):
    """All assistant surfaces require the server session's OIDC credential."""

    with patch("core.mastrao_platform_facade.requests.Session") as upstream:
        response = _call(
            _client(token=None),
            method,
            url,
            payload,
            HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
        )
    assert response.status_code == 401
    upstream.assert_not_called()


@pytest.mark.parametrize("operation", ["question", "summary", "actions", "email_draft"])
def test_http_f07_f08_forward_exact_command_and_keep_draft_output(operation):
    """Questions and all three actions use the published Platform POST contract."""

    command = {**COMMAND, "operation": operation}
    receipt = copy.deepcopy(_send_receipt(operation))
    if operation == "email_draft":
        receipt["turn"]["result"]["email_draft"] = {
            "subject": "Suite de notre réunion",
            "body": "Voici le suivi proposé.",
        }
    if operation == "actions":
        receipt["turn"]["result"]["actions"] = [
            {
                "statement": "Confirmer le suivi.",
                "owner": None,
                "due_date": None,
                "citation_ids": [CITATION["citation_id"]],
            }
        ]
    session = _upstream(receipt)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _call(_client(), "POST", payload=command)
    assert response.status_code == 200
    assert response.json() == receipt
    session.request.assert_called_once_with(
        "POST",
        f"{PLATFORM}/api/meet/meetings/history/{MEETING}/assistant",
        headers={"authorization": f"Bearer {TOKEN}"},
        timeout=ASSISTANT_TIMEOUT_SECONDS,
        allow_redirects=False,
        stream=True,
        json=command,
    )


def test_http_replay_uses_the_same_durable_command_without_transport_retry():
    """Explicit resubmission preserves the command identity owned by Core."""

    session = _upstream(_send_receipt())
    client = _client()
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        first = _call(client, "POST", payload=COMMAND)
        session.request.return_value = _upstream(
            _send_receipt(replay=True)
        ).request.return_value
        replay = _call(client, "POST", payload=COMMAND)
    assert first.status_code == replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert session.request.call_count == 2
    assert all(
        call.kwargs["json"] == COMMAND for call in session.request.call_args_list
    )


def test_http_delete_uses_the_current_private_thread_command():
    """DELETE forwards the exact revision command and validates the tombstone response."""

    result = {"version": 1, "deleted": True, "thread_version": 2}
    session = _upstream(result)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _call(_client(), "DELETE", payload=_delete_payload())
    assert response.status_code == 200
    assert response.json() == result
    assert session.request.call_args.args == (
        "DELETE",
        f"{PLATFORM}/api/meet/meetings/history/{MEETING}/assistant",
    )
    assert session.request.call_args.kwargs["json"] == _delete_payload()


@pytest.mark.parametrize("source_status", ["transcript", "published_proposal"])
def test_http_citation_opens_the_authorized_passage_without_a_file_url(source_status):
    """The source resolver returns actual passage text with its editorial status."""

    result = {
        "version": 1,
        "segment": {
            "citation": CITATION,
            "text": "Le délai est vendredi.",
            "speaker": None,
            "status": source_status,
        },
    }
    session = _upstream(result)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _call(_client(), "POST", CITATION_URL, _citation_payload())
    assert response.status_code == 200
    assert response.json() == result
    assert session.request.call_args.args == (
        "POST",
        f"{PLATFORM}/api/meet/meetings/history/{MEETING}/assistant/citation",
    )
    assert session.request.call_args.kwargs["json"] == _citation_payload()


@pytest.mark.parametrize(
    "method,url,payload,body",
    [
        ("POST", ASSISTANT_URL, COMMAND, _send_receipt()),
        (
            "DELETE",
            ASSISTANT_URL,
            _delete_payload(),
            {"version": 1, "deleted": True, "thread_version": 2},
        ),
        (
            "POST",
            CITATION_URL,
            _citation_payload(),
            {
                "version": 1,
                "segment": {
                    "citation": CITATION,
                    "text": "Passage.",
                    "speaker": None,
                    "status": "transcript",
                },
            },
        ),
    ],
)
def test_http_mutations_require_real_csrf_and_accept_a_valid_token(
    method, url, payload, body
):
    """CSRF protects send, private deletion and citation requests alike."""

    client = _client(csrf=True)
    with patch("core.mastrao_platform_facade.requests.Session") as upstream:
        response = _call(client, method, url, payload)
    assert response.status_code == 403
    upstream.assert_not_called()
    token_request = RequestFactory().get("/")
    token = get_token(token_request)
    client.cookies[django_settings.CSRF_COOKIE_NAME] = token_request.META["CSRF_COOKIE"]
    with patch(
        "core.mastrao_platform_facade.requests.Session", return_value=_upstream(body)
    ):
        response = _call(client, method, url, payload, HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 200


@pytest.mark.parametrize(
    "upstream_status", [401, 403, 404, 409, 422, 429, 500, 503, 302]
)
def test_http_preserves_access_conflict_and_failure_status_without_private_details(
    upstream_status,
):
    """A technical upstream failure is never transformed into a factual answer."""

    client = _client()
    session = _upstream(
        {"private_url": "https://private.example", "token": TOKEN}, upstream_status
    )
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _call(client, "POST", payload=COMMAND)
    expected = 503 if upstream_status in {500, 302} else upstream_status
    assert response.status_code == expected
    assert response.json() == {"message": "Assistant de réunion indisponible."}
    if upstream_status == 401:
        assert "oidc_access_token" not in client.session
    assert TOKEN not in response.content.decode()
    assert "private.example" not in response.content.decode()
    session.request.assert_called_once()


@pytest.mark.parametrize("declared", [True, False])
def test_http_respects_the_platform_assistant_response_budget(declared):
    """Bound streamed and declared responses to the existing 480 kB assistant limit."""

    session = _upstream(THREAD)
    upstream = session.request.return_value
    upstream.headers = {}
    if declared:
        upstream.headers["content-length"] = str(MAX_ASSISTANT_RESPONSE_BYTES + 1)
    else:
        upstream.iter_content.return_value = [b" " * MAX_ASSISTANT_RESPONSE_BYTES, b" "]
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _client().get(ASSISTANT_URL)
    assert response.status_code == 503
    upstream.close.assert_called_once()


def test_http_timeout_does_not_dispatch_a_second_command():
    """The client can reread or explicitly replay a timed-out durable command."""

    session = _upstream(THREAD)
    session.request.side_effect = requests.Timeout()
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _call(_client(), "POST", payload=COMMAND)
    assert response.status_code == 503
    session.request.assert_called_once()


@pytest.mark.parametrize("raw", [b"not-json", b"[" * 1500 + b"]" * 1500])
def test_http_malformed_upstream_json_is_a_closed_technical_failure(raw):
    """Invalid or excessively nested JSON cannot bypass response validation."""

    session = _upstream(THREAD)
    upstream = session.request.return_value
    upstream.headers = {"content-length": str(len(raw))}
    upstream.iter_content.return_value = [raw]
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _client().get(ASSISTANT_URL)
    assert response.status_code == 503
    upstream.close.assert_called_once()


@pytest.mark.parametrize("question,valid", [("😀" * 512, True), ("😀" * 513, False)])
def test_question_limits_match_zod_utf16_units(question, valid):
    """Astral characters consume two units in the actual TypeScript string bound."""

    request = _request({**COMMAND, "question": question})
    if valid:
        assert _send_command(request, MEETING)["question"] == question
        return
    with pytest.raises(PlatformFacadeError) as refused:
        _send_command(request, MEETING)
    assert refused.value.status == 422
