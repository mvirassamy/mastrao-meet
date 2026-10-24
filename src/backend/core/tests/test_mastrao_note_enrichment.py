"""Private enrichment bridge: exact approved bytes, bounded data and no guest access."""

import copy
import json
from unittest.mock import MagicMock, patch

from django.conf import settings as django_settings
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory

import pytest

from core.mastrao_note_enrichment import _command, _validate_state
from core.mastrao_personal_notes import PersonalNoteRefused
from core.mastrao_platform_facade import PlatformFacadeError

MEETING = "meeting_0123456789abcdef"
URL = f"/api/v1.0/meetings/history/{MEETING}/personal-note/enrichment/"
COMMAND = {
    "version": 1,
    "meeting_ref": MEETING,
    "command_id": "command_0123456789abcdef",
    "action": "generate",
    "expected_revision": 2,
    "expected_state_version": 0,
}
STATE = {
    "version": 1,
    "meeting_ref": MEETING,
    "state_version": 0,
    "eligible": True,
    "reason": None,
    "proposal": None,
    "last_applied": None,
    "receipt": None,
}


@pytest.fixture(autouse=True)
def bridge_settings(settings):
    """Synthetic signed-cookie sessions; no real accounts or database."""
    settings.SECRET_KEY = "note-enrichment-test-key"
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
    settings.MASTRAO_PLATFORM_API_BASE_URL = "https://platform.example.com"


@pytest.fixture(name="history_authors", autouse=True)
def patched_history_authors():
    """Default to account notes; matching participant cases override these."""
    with (
        patch(
            "core.mastrao_note_enrichment._history_host_participant",
            return_value=None,
        ) as host,
        patch(
            "core.mastrao_note_enrichment._history_guest_participant",
            return_value=None,
        ) as guest,
    ):
        yield host, guest


def _client(*, token=True, csrf=False):
    client = Client(enforce_csrf_checks=csrf)
    session = client.session
    if token:
        session["oidc_access_token"] = "synthetic-enrichment-owner-token"
    session["mastrao_guest_grant_ref"] = "guest_0123456789abcdef"
    session.save()
    client.cookies[django_settings.SESSION_COOKIE_NAME] = session.session_key
    return client


def _upstream(body):
    response = MagicMock()
    payload = json.dumps(body).encode()
    response.status_code = 200
    response.headers = {"content-length": str(len(payload))}
    response.iter_content.return_value = [payload]
    session = MagicMock()
    session.__enter__.return_value = session
    session.request.return_value = response
    return session


def test_read_uses_oidc_and_private_no_store_headers():
    """Send only account authority and mark the response private."""
    session = _upstream(STATE)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        result = _client().get(URL)
    assert result.status_code == 200
    assert result.json() == STATE
    assert result.headers["Cache-Control"] == "private, no-store"
    assert session.request.call_args.args[:2] == (
        "GET",
        "https://platform.example.com/api/meet/meetings/history/"
        f"{MEETING}/personal-note/enrichment",
    )
    assert (
        session.request.call_args.kwargs["headers"]["authorization"]
        == "Bearer synthetic-enrichment-owner-token"
    )


def test_guest_grant_does_not_authorize_enrichment():
    """A guest grant cannot replace a signed-in account session."""
    with patch("core.mastrao_platform_facade.requests.Session") as upstream:
        result = _client(token=False).get(URL)
    assert result.status_code == 401
    upstream.assert_not_called()


@pytest.mark.parametrize("action", ["read", "generate", "apply", "discard", "undo"])
def test_matching_guest_author_cannot_enrich_an_account_note(history_authors, action):
    """Every action rejects a guest note before contacting Platform."""
    _, guest = history_authors
    guest.return_value = {"participant_grant": "synthetic-guest-grant"}
    command = {**COMMAND, "action": action}
    if action in ("apply", "discard", "undo"):
        command["proposal_id"] = "proposal_0123456789abcdef"
    if action == "discard":
        del command["expected_revision"]
    if action == "apply":
        command["items"] = [{"index": 0, "text": "Approved complement"}]
    with patch(
        "core.mastrao_note_enrichment.request_platform", return_value=(STATE, 200)
    ) as upstream:
        client = _client()
        if action == "read":
            result = client.get(URL)
        else:
            result = client.post(
                URL, data=json.dumps(command), content_type="application/json"
            )
    assert result.status_code == 403
    upstream.assert_not_called()


def test_matching_host_takes_precedence_over_guest_author(history_authors):
    """History notes and enrichment choose the same host author first."""
    host, guest = history_authors
    host.return_value = {"participant_grant": "synthetic-host-grant"}
    with patch(
        "core.mastrao_note_enrichment.request_platform", return_value=(STATE, 200)
    ) as upstream:
        result = _client().get(URL)
    assert result.status_code == 200
    host.assert_called_once()
    guest.assert_not_called()
    upstream.assert_called_once()


@pytest.mark.parametrize("author", ["host", "guest"])
def test_invalid_history_author_never_falls_back_to_oidc(history_authors, author):
    """Invalid participant authority never falls back to another note."""
    host, guest = history_authors
    resolver = host if author == "host" else guest
    resolver.side_effect = PersonalNoteRefused(status=404)
    with patch(
        "core.mastrao_note_enrichment.request_platform", return_value=(STATE, 200)
    ) as upstream:
        result = _client().get(URL)
    assert result.status_code == 404
    upstream.assert_not_called()


def test_post_requires_csrf_before_egress():
    """Reject missing CSRF before sending any command to Platform."""
    client = _client(csrf=True)
    with patch("core.mastrao_note_enrichment.request_platform") as upstream:
        denied = client.post(
            URL, data=json.dumps(COMMAND), content_type="application/json"
        )
        assert denied.status_code == 403
        upstream.assert_not_called()
        token_request = RequestFactory().get("/")
        csrf_token = get_token(token_request)
        client.cookies[django_settings.CSRF_COOKIE_NAME] = token_request.META[
            "CSRF_COOKIE"
        ]
        upstream.return_value = (STATE, 200)
        result = client.post(
            URL,
            data=json.dumps(COMMAND),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
    assert result.status_code == 200
    assert upstream.call_args.kwargs["options"]["json"] == COMMAND


def test_apply_keeps_exact_approved_unicode():
    """Validation preserves approved Unicode and whitespace byte for byte."""
    text = "  De\u0301cision : à confirmer. 📝\n"
    command = {
        **COMMAND,
        "action": "apply",
        "proposal_id": "proposal_0123456789abcdef",
        "items": [{"index": 2, "text": text}, {"index": 0, "text": "- Relancer"}],
    }
    request = RequestFactory().post(
        URL, data=json.dumps(command), content_type="application/json"
    )
    validated = _command(request, MEETING)["items"]
    assert validated[0]["text"].encode() == text.encode()
    assert validated[1] == {"index": 0, "text": "- Relancer"}


APPLY = {"action": "apply", "proposal_id": "proposal_0123456789abcdef"}


@pytest.mark.parametrize(
    "patch_value",
    [
        {"meeting_ref": "meeting_other0123456789"},
        {"owner_id": "actor_other0123456789"},
        {"expected_state_version": True},
        {"action": "read"},
        {"version": True},
        {**APPLY, "items": [{"index": 0, "text": "\x00"}]},
        {**APPLY, "items": [{"index": 0, "text": " "}]},
        {**APPLY, "items": []},
        {**APPLY, "items": "Complément"},
        {**APPLY, "items": [{"index": -1, "text": "x"}]},
        {**APPLY, "items": [{"index": 32, "text": "x"}]},
        {**APPLY, "items": [{"index": True, "text": "x"}]},
        {**APPLY, "items": [{"index": 0, "text": "x"}, {"index": 0, "text": "y"}]},
        {**APPLY, "items": [{"index": 0, "text": "x", "after_line": 1}]},
        {
            **APPLY,
            "items": [
                {"index": 0, "text": "x" * 8000},
                {"index": 1, "text": "y"},
            ],
        },
        {**APPLY, "additions": "Ancien format"},
    ],
)
def test_bad_commands_refused_without_platform_call(patch_value):
    """Reject invalid command fields at the Meet boundary."""
    with patch("core.mastrao_note_enrichment.request_platform") as upstream:
        result = _client().post(
            URL,
            data=json.dumps({**COMMAND, **patch_value}),
            content_type="application/json",
        )
    assert result.status_code == 422
    upstream.assert_not_called()


def test_duplicate_fields_and_oversized_command_refused():
    """Bound request bytes and reject duplicate keys or query fields."""
    with patch("core.mastrao_note_enrichment.request_platform") as upstream:
        client = _client()
        duplicate = json.dumps(COMMAND)[:-1] + ', "action": "undo"}'
        assert (
            client.post(
                URL, data=duplicate, content_type="application/json"
            ).status_code
            == 422
        )
        assert (
            client.post(
                URL, data=" " * 60001, content_type="application/json"
            ).status_code
            == 422
        )
        assert client.get(URL + "?owner=other").status_code == 422
    upstream.assert_not_called()


@pytest.mark.parametrize(
    "patch_value",
    [
        {"meeting_ref": "meeting_other0123456789"},
        {"eligible": "true"},
        {"reason": []},
        {"private_original": "must-not-escape"},
        {"state_version": True},
        {"version": True},
    ],
)
def test_invalid_upstream_dto_is_sanitized(patch_value):
    """Malformed private responses become a safe public error."""
    with patch(
        "core.mastrao_note_enrichment.request_platform",
        return_value=({**STATE, **patch_value}, 200),
    ):
        result = _client().get(URL)
    assert result.status_code == 503
    assert "must-not-escape" not in result.content.decode()
    assert result.headers["Cache-Control"] == "private, no-store"


PROPOSAL = {
    "id": "proposal_0123456789abcdef",
    "command_id": COMMAND["command_id"],
    "status": "ready",
    "base_revision": 2,
    "base_digest": "a" * 64,
    "items": [
        {
            "after_line": 3,
            "text": "- Complément",
            "citation_ids": ["citation_0123456789abcdef"],
        }
    ],
    "coverage": None,
    "evidence": [],
}


def test_anchored_proposal_is_accepted():
    """A ready proposal carries anchored complements."""
    _validate_state({**STATE, "proposal": PROPOSAL}, MEETING)


@pytest.mark.parametrize(
    "item_patch",
    [
        {"after_line": 0},
        {"after_line": True},
        {"text": " "},
        {"citation_ids": []},
        {"citation_ids": ["citation_0123456789abcdef"] * 2},
        {"section": "Décisions"},
    ],
)
def test_invalid_anchored_items_are_sanitized(item_patch):
    """Malformed complements never reach the browser."""
    item = {**PROPOSAL["items"][0], **item_patch}
    with pytest.raises(PlatformFacadeError):
        _validate_state({**STATE, "proposal": {**PROPOSAL, "items": [item]}}, MEETING)


def test_former_single_text_proposal_is_sanitized():
    """The former single-block proposal is no longer a valid projection."""
    former = {key: value for key, value in PROPOSAL.items() if key != "items"}
    with pytest.raises(PlatformFacadeError):
        _validate_state(
            {**STATE, "proposal": {**former, "text": "Complément"}}, MEETING
        )


def test_published_brief_is_not_note_enrichment_evidence():
    """Only transcript passages can justify the proposed additions."""
    value = copy.deepcopy(STATE)
    value["proposal"] = {
        **PROPOSAL,
        "evidence": [
            {
                "citation": {
                    "citation_id": "citation_0123456789abcdef",
                    "meeting_ref": MEETING,
                    "source_kind": "published_brief",
                    "source_ref": "source_0123456789abcdef",
                    "source_version": 1,
                    "source_digest": "b" * 64,
                    "segment_id": "segment_1",
                    "start_ms": 0,
                    "end_ms": 100,
                    "time_basis": "meeting_relative_ms",
                },
                "text": "private source",
                "speaker": None,
                "status": "transcript",
            }
        ],
    }
    with pytest.raises(PlatformFacadeError):
        _validate_state(value, MEETING)
