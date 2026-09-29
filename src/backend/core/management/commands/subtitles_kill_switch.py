"""Disable subtitle providers and converge their affected rooms."""

from django.core.management.base import BaseCommand
from django.db.models import Q

from core import models
from core.services.subtitle_reconciliation import (
    SubtitleConvergenceBusy,
    reconcile_subtitle_control,
    request_subtitle_stop,
)


class Command(BaseCommand):
    """Request and verify provider-scoped subtitle shutdown."""

    help = "Stop and clean up subtitle sessions for one provider."

    def add_arguments(self, parser):
        parser.add_argument("--provider", required=True)
        parser.add_argument("--room-sid")

    def handle(self, *args, **options):
        provider = options["provider"]
        room_sid = options.get("room_sid")
        controls = models.RoomSubtitleControl.objects.filter(
            provider=provider,
            is_current=True,
        ).filter(
            Q(desired_state=models.RoomSubtitleControl.DesiredState.ON)
            | Q(observed_dispatch_ids__len__gt=0)
        )
        if room_sid:
            controls = controls.filter(room_sid=room_sid)

        converged = 0
        failed = 0
        for control in controls.select_related("room").iterator():
            try:
                request_subtitle_stop(
                    control.room,
                    room_sid=control.room_sid,
                    reason_code=models.RoomSubtitleControl.ReasonCode.PROVIDER_UNAVAILABLE,
                )
                result = reconcile_subtitle_control(control.room_sid)
                if result and result.public_state in {
                    models.RoomSubtitleControl.PublicState.INACTIVE,
                    models.RoomSubtitleControl.PublicState.STOPPED,
                }:
                    converged += 1
                    self.stdout.write(f"stopped {control.room_sid}")
                else:
                    failed += 1
                    self.stderr.write(f"stopping {control.room_sid}")
            except SubtitleConvergenceBusy:
                failed += 1
                self.stderr.write(f"busy {control.room_sid}")
            except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                failed += 1
                self.stderr.write(f"failed {control.room_sid}: {error}")

        self.stdout.write(f"provider={provider} converged={converged} failed={failed}")
