"""Reconcile a bounded batch of canonical Mastrao recordings."""

from django.core.management.base import BaseCommand, CommandError

from core.mastrao_recording_reconciler import reconcile_mastrao_recordings


class Command(BaseCommand):
    """Expose provider convergence to the deployment scheduler."""

    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1 or limit > 100:
            raise CommandError("limit must be between 1 and 100")
        count = reconcile_mastrao_recordings(limit=limit)
        self.stdout.write(f"Reconciled {count} Mastrao recording(s).")
