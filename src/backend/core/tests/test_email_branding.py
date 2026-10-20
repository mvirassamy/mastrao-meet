"""French transactional email rendering without SMTP or database access."""

import os
import re
import smtplib
import subprocess
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from django.core import mail
from django.utils.translation import get_language, override

import pytest

from core import models
from core.recording.event.notification import NotificationService
from core.services.invitation import InvitationError, InvitationService


@pytest.mark.parametrize(
    "sender",
    [
        "notifications@mastrao.com",
        "notifications@mastrao-staging.com",
        "notifications+meet@custom.example.com",
    ],
)
@pytest.mark.parametrize("interface_language", ["fr-fr", "en-us", "nl-nl", "de-de"])
def test_invitation_is_french_and_preserves_bare_sender(
    settings, sender, interface_language
):
    """Render both MIME alternatives in French without changing the UI language."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.EMAIL_FROM = sender
    settings.EMAIL_BRAND_NAME = "Mastrao Visio"
    settings.EMAIL_APP_BASE_URL = "https://visio.example.com"
    settings.EMAIL_DOMAIN = "visio.example.com"
    settings.EMAIL_LOGO_IMG = "https://visio.example.com/assets/mastrao-logo-icon.png"
    room = SimpleNamespace(slug="abc-def-ghi")
    organizer = SimpleNamespace(email="organisateur@example.com")

    with override(interface_language):
        InvitationService.invite_to_room(room, organizer, ["invite@example.com"])
        assert get_language() == interface_language

    message = mail.outbox[-1]
    html, mime_type = message.alternatives[0]
    assert (
        message.subject
        == "Visioconférence en cours : organisateur@example.com vous attend"
    )
    assert message.from_email == sender
    assert message.message()["From"] == sender
    assert message.to == []
    assert message.bcc == ["invite@example.com"]
    assert mime_type == "text/html"
    assert '<html lang="fr"' in html
    assert f"<title>{message.subject}</title>" in html
    for body in [message.body, html]:
        assert "Mastrao Visio" in body
        assert "vous invite à rejoindre une visioconférence" in body
        assert "REJOINDRE LA VISIOCONFÉRENCE" in body
        assert "Cet e-mail vous a été envoyé par" in body
        assert "https://visio.example.com/abc-def-ghi" in body
        assert "Merci d’utiliser Mastrao Visio." in body
        assert "Thank you" not in body
        assert "This mail" not in body
        assert "JOIN THE CALL" not in body
        assert "None" not in body
        assert "invite@example.com" not in body
    assert re.search(r"display:none[^>]*>" + re.escape(message.subject), html)


@pytest.mark.parametrize("interface_language", ["fr-fr", "en-us", "nl-nl", "de-de"])
def test_scheduled_invitation_is_french_and_preserves_choices(
    settings, interface_language
):
    """Keep personal links, meeting timezone and separate consent choices intact."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.EMAIL_FROM = "notifications+meet@custom.example.com"
    settings.EMAIL_BRAND_NAME = "Mastrao Visio"
    settings.EMAIL_LOGO_IMG = "https://visio.example.com/assets/mastrao-logo-icon.png"
    schedule = {
        "title": "Réunion <confidentielle>",
        "timezone": "Europe/Paris",
        "scheduled_start_at": datetime(
            2026, 10, 20, 14, 30, tzinfo=timezone.utc
        ).timestamp(),
        "scheduled_end_at": datetime(
            2026, 10, 20, 15, 30, tzinfo=timezone.utc
        ).timestamp(),
    }
    with override(interface_language):
        InvitationService.invite_to_scheduled_room(
            sender=SimpleNamespace(email="organisateur@example.com"),
            email="invite@example.com",
            schedule=schedule,
            room_url="https://visio.example.com/abc-def-ghi",
            choice_url="https://visio.example.com/video-choice/personal-link",
        )
        assert get_language() == interface_language

    message = mail.outbox[-1]
    html, mime_type = message.alternatives[0]
    assert message.subject == "Invitation à une réunion planifiée"
    assert message.message()["From"] == "notifications+meet@custom.example.com"
    assert message.to == ["invite@example.com"]
    assert message.bcc == []
    assert mime_type == "text/html"
    assert '<html lang="fr"' in html
    assert f"<title>{message.subject}</title>" in html
    assert "Réunion &lt;confidentielle&gt;" in html
    assert "Réunion <confidentielle>" in message.body
    for body in [message.body, html]:
        assert "20/10/2026 16:30 – 17:30 (Europe/Paris)" in body
        assert "https://visio.example.com/abc-def-ghi" in body
        assert "https://visio.example.com/video-choice/personal-link" in body
        assert "Tout refus empêche l’enregistrement vidéo" in body
        assert "et non une acceptation explicite" in body
        assert "jusqu’au démarrage effectif" in body
        assert "L’arrêt de l’enregistrement vidéo est définitif" in body
        assert "Refuser la vidéo ne signifie pas refuser la transcription audio" in body
        assert "Cet e-mail vous a été envoyé par" in body
        assert "Mastrao Visio" in body
        assert "Invitation to a scheduled meeting" not in body
        assert "None" not in body


@pytest.mark.parametrize("send_result", [0, smtplib.SMTPException("synthetic failure")])
def test_scheduled_invitation_still_requires_confirmed_delivery(send_result):
    """Do not turn an unconfirmed send into a successful delivery receipt."""
    with mock.patch("core.services.invitation.EmailMultiAlternatives.send") as send:
        if isinstance(send_result, Exception):
            send.side_effect = send_result
        else:
            send.return_value = send_result
        with pytest.raises(InvitationError, match="Could not confirm invitation send"):
            InvitationService.invite_to_scheduled_room(
                sender=SimpleNamespace(email="organisateur@example.com"),
                email="invite@example.com",
                schedule={
                    "timezone": "Europe/Paris",
                    "scheduled_start_at": 1792506600,
                    "scheduled_end_at": 1792510200,
                },
                room_url="https://visio.example.com/abc-def-ghi",
                choice_url="https://visio.example.com/video-choice/personal-link",
            )


@pytest.mark.parametrize("interface_language", ["en-us", "nl-nl", "de-de"])
@pytest.mark.parametrize("support_email", [None, "assistance@example.com"])
def test_recording_notification_is_french(settings, interface_language, support_email):
    """Use French for non-French owners while retaining their timezone and sender."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.EMAIL_FROM = "notifications@mastrao-staging.com"
    settings.EMAIL_BRAND_NAME = "Mastrao Visio"
    settings.EMAIL_LOGO_IMG = "https://visio.example.com/assets/mastrao-logo-icon.png"
    settings.EMAIL_SUPPORT_EMAIL = support_email
    settings.RECORDING_DOWNLOAD_BASE_URL = "https://visio.example.com/recording"
    settings.SCREEN_RECORDING_BASE_URL = None
    settings.RECORDING_EXPIRATION_DAYS = 7
    user = SimpleNamespace(
        email="proprietaire@example.com",
        language=interface_language,
        timezone=ZoneInfo("America/Phoenix"),
    )
    recording = SimpleNamespace(
        id="recording-test",
        mode=models.RecordingModeChoices.SCREEN_RECORDING,
        options={},
        room=SimpleNamespace(name="Réunion de travail"),
        created_at=datetime(2026, 10, 20, 14, 30, tzinfo=timezone.utc),
    )
    with (
        override(interface_language),
        mock.patch(
            "core.recording.event.notification.models.RecordingAccess.objects"
        ) as accesses,
    ):
        accesses.select_related.return_value.filter.return_value.order_by.return_value = [
            SimpleNamespace(user=user)
        ]
        assert NotificationService().notify_external_services(recording) is True
        assert get_language() == interface_language

    message = mail.outbox[-1]
    html, _mime_type = message.alternatives[0]
    assert message.subject == "Votre enregistrement est prêt"
    assert message.from_email == "notifications@mastrao-staging.com"
    assert message.message()["From"] == "notifications@mastrao-staging.com"
    assert message.to == [user.email]
    assert '<html lang="fr"' in html
    assert f"<title>{message.subject}</title>" in html
    assert re.search(r"display:none[^>]*>" + re.escape(message.subject), html)
    for body in [message.body, html]:
        assert "Votre enregistrement est prêt" in body
        assert "Réunion de travail" in body
        assert "07:30" in body
        assert "expirera dans 7 jours" in body
        assert "https://visio.example.com/recording/recording-test" in body
        assert "Merci d’utiliser Mastrao Visio." in body
        assert "Your recording" not in body
        assert "Thank you" not in body
        assert "None" not in body
        if support_email:
            assert support_email in body
        else:
            assert "contactez votre assistance" not in body


@pytest.mark.parametrize(
    "overrides, expected",
    [
        (
            {},
            {
                "EMAIL_FROM": "notifications@mastrao.com",
                "EMAIL_BRAND_NAME": "Mastrao Visio",
                "EMAIL_APP_BASE_URL": "https://visio.example.com",
                "EMAIL_DOMAIN": "visio.example.com",
                "EMAIL_LOGO_IMG": "https://visio.example.com/assets/mastrao-logo-icon.png",
            },
        ),
        (
            {
                "DJANGO_EMAIL_FROM": "notifications@mastrao-staging.com",
                "DJANGO_EMAIL_BRAND_NAME": "Mastrao Visio staging",
                "DJANGO_EMAIL_APP_BASE_URL": "https://staging.example.com/",
                "DJANGO_EMAIL_DOMAIN": "custom.example.com",
                "DJANGO_EMAIL_LOGO_IMG": "https://custom.example.com/icon.png",
            },
            {
                "EMAIL_FROM": "notifications@mastrao-staging.com",
                "EMAIL_BRAND_NAME": "Mastrao Visio staging",
                "EMAIL_APP_BASE_URL": "https://staging.example.com",
                "EMAIL_DOMAIN": "custom.example.com",
                "EMAIL_LOGO_IMG": "https://custom.example.com/icon.png",
            },
        ),
    ],
)
def test_email_defaults_follow_frontend_origin_and_explicit_overrides(
    overrides, expected
):
    """Load the real settings without reading local deployment configuration."""
    environment = {
        "PATH": os.environ["PATH"],
        "DJANGO_SETTINGS_MODULE": "meet.settings",
        "DJANGO_CONFIGURATION": "Development",
        "APPLICATION_BASE_URL": "https://visio.example.com/",
        **overrides,
    }
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from unittest.mock import patch; "
                "patch('socket.gethostname', return_value='localhost').start(); "
                "from configurations import importer; importer.install(); "
                "from django.conf import settings; "
                f"expected = {expected!r}; "
                "assert {key: getattr(settings, key) for key in expected} == expected; "
                "assert settings.LANGUAGE_CODE == 'en-us'"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
