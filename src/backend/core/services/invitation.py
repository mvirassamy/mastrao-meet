"""Invitation Service."""

import smtplib
from datetime import datetime
from logging import getLogger
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import dateformat
from django.utils.translation import get_language, override
from django.utils.translation import gettext_lazy as _

logger = getLogger(__name__)

SCHEDULED_INVITATION_LANGUAGE = "fr"
UNTITLED_MEETING_TITLE = "Réunion visio"


class InvitationError(Exception):
    """Exception raised when invitation emails cannot be sent."""

    status_code = 500


def _single_line(text):
    """Collapse whitespace so a title can never break an e-mail header."""

    return " ".join(text.split())


def _timezone_label(timezone):
    if timezone == "Europe/Paris":
        return "heure de Paris"
    return timezone


def _origin(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class InvitationService:
    """Service for invitations to users."""

    @staticmethod
    def invite_to_scheduled_room(*, sender, email, schedule, room_url, choice_url):
        """Send one recipient their meeting link and personal video choice."""

        zone = ZoneInfo(schedule["timezone"])
        title = _single_line(schedule.get("title") or "") or UNTITLED_MEETING_TITLE
        starts_at = datetime.fromtimestamp(schedule["scheduled_start_at"], zone)
        context = {
            "title": title,
            "sender_name": sender.full_name or sender.email,
            "starts_at": starts_at,
            "ends_at": datetime.fromtimestamp(schedule["scheduled_end_at"], zone),
            "timezone_label": _timezone_label(schedule["timezone"]),
            "room_url": room_url,
            "choice_url": choice_url,
            # Images are served by the Meet frontend that also serves the room.
            "asset_origin": _origin(room_url),
        }
        with override(SCHEDULED_INVITATION_LANGUAGE):
            starts_label = dateformat.format(starts_at, "l j F, H:i")
            message = EmailMultiAlternatives(
                subject=f"Invitation : {title} · {starts_label}",
                body=render_to_string("invitations/scheduled.txt", context),
                from_email=settings.EMAIL_FROM,
                to=[email],
            )
            message.attach_alternative(
                render_to_string("invitations/scheduled.html", context), "text/html"
            )
        try:
            if message.send() != 1:
                raise InvitationError("Could not confirm invitation send")
        except smtplib.SMTPException as error:
            raise InvitationError("Could not confirm invitation send") from error

    @staticmethod
    def invite_to_room(room, sender, emails):
        """Send invitation emails to join a room."""

        language = get_language()

        context = {
            "brandname": settings.EMAIL_BRAND_NAME,
            "logo_img": settings.EMAIL_LOGO_IMG,
            "domain": settings.EMAIL_DOMAIN,
            "room_url": f"{settings.EMAIL_APP_BASE_URL}/{room.slug}",
            "room_link": f"{settings.EMAIL_DOMAIN}/{room.slug}",
            "sender_email": sender.email,
        }

        with override(language):
            msg_html = render_to_string("mail/html/invitation.html", context)
            msg_plain = render_to_string("mail/text/invitation.txt", context)
            subject = str(
                _(
                    f"Video call in progress: {sender.email} is waiting for you to connect"
                )
            )  # Force translation

            email = EmailMultiAlternatives(
                subject=subject,
                body=msg_plain,
                from_email=settings.EMAIL_FROM,
                to=[],
                bcc=emails,
            )

            email.attach_alternative(msg_html, "text/html")

            try:
                email.send()
            except smtplib.SMTPException as e:
                logger.error("invitations were not sent: %s", e)
                raise InvitationError("Could not send invitation") from e
