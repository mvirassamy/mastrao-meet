"""Invitation Service."""

import smtplib
from datetime import datetime
from logging import getLogger
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.translation import override

logger = getLogger(__name__)


class InvitationError(Exception):
    """Exception raised when invitation emails cannot be sent."""

    status_code = 500


class InvitationService:
    """Service for invitations to users."""

    @staticmethod
    def invite_to_scheduled_room(*, sender, email, schedule, room_url, choice_url):
        """Send one recipient their meeting link and personal video choice."""

        zone = ZoneInfo(schedule["timezone"])
        subject = "Invitation à une réunion planifiée"
        context = {
            "subject": subject,
            "title": schedule.get("title", ""),
            "brandname": settings.EMAIL_BRAND_NAME,
            "logo_img": settings.EMAIL_LOGO_IMG,
            "sender_email": sender.email,
            "starts_at": datetime.fromtimestamp(schedule["scheduled_start_at"], zone),
            "ends_at": datetime.fromtimestamp(schedule["scheduled_end_at"], zone),
            "timezone": schedule["timezone"],
            "room_url": room_url,
            "choice_url": choice_url,
        }
        with override("fr-fr"):
            message = EmailMultiAlternatives(
                subject=subject,
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

        subject = f"Visioconférence en cours : {sender.email} vous attend"
        context = {
            "title": subject,
            "brandname": settings.EMAIL_BRAND_NAME,
            "logo_img": settings.EMAIL_LOGO_IMG,
            "domain": settings.EMAIL_DOMAIN,
            "room_url": f"{settings.EMAIL_APP_BASE_URL}/{room.slug}",
            "room_link": f"{settings.EMAIL_DOMAIN}/{room.slug}",
            "sender_email": sender.email,
        }

        # Email language is independent of the request and interface language.
        with override("fr-fr"):
            msg_html = render_to_string("mail/html/invitation.html", context)
            msg_plain = render_to_string("mail/text/invitation.txt", context)
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
