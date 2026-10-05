"""Retry committed RTC facts without minting or renewing participant authority."""

from django.core.management.base import BaseCommand

from livekit.protocol.models import ParticipantInfo

from core import models
from core.mastrao_rtc_admissions import deliver_rtc_admission


class Command(BaseCommand):
    """Explicit recovery after a Core outage or process restart."""

    help = "Retry pending verified human RTC admissions to Cabinet Core."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        pending = models.MastraoRtcObservation.objects.filter(
            event_type="participant_joined",
            participant_kind=ParticipantInfo.STANDARD,  # pylint: disable=no-member
            admission_receipt__isnull=True,
        ).order_by("created_at")
        attempted = 0
        for observation_id in pending.values_list("pk", flat=True).iterator():
            if attempted >= max(0, options["limit"]):
                break
            attempted += int(deliver_rtc_admission(observation_id))
        self.stdout.write(f"RTC admissions attempted: {attempted}")
