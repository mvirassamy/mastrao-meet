"""Retain a stop latch and terminal observation independently of start receipts."""

from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [("core", "0042_mastraonativecapturestart")]

    operations = [
        migrations.AddField("mastraonativecapturestart", name, field)
        for name, field in [
            ("retention_expires_at", models.DateTimeField(null=True, blank=True)),
            ("stop_requested_at", models.DateTimeField(null=True, blank=True)),
            ("stop_reason", models.CharField(max_length=32, blank=True, default="")),
            ("observed_status", models.IntegerField(null=True, blank=True)),
            ("drained_at", models.DateTimeField(null=True, blank=True)),
            (
                "next_check_at",
                models.DateTimeField(default=timezone.now, db_index=True),
            ),
            ("drain_claim", models.UUIDField(null=True, blank=True)),
            ("drain_claim_until", models.DateTimeField(null=True, blank=True)),
        ]
    ]
