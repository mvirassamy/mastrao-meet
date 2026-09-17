"""Derived delivery state on existing epochs; no consent or bearer replication."""

from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [("core", "0043_native_capture_drain")]
    operations = [
        migrations.AddField("mastraortctrackepoch", name, field)
        for name, field in [
            ("native_admission_capture_ref", models.UUIDField(null=True, blank=True)),
            ("native_admission_blocked", models.BooleanField(default=False)),
            (
                "native_admission_next_at",
                models.DateTimeField(default=timezone.now, db_index=True),
            ),
            ("native_admission_claim", models.UUIDField(null=True, blank=True)),
            (
                "native_admission_claim_until",
                models.DateTimeField(null=True, blank=True),
            ),
        ]
    ]
