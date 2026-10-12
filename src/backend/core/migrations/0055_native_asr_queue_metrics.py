from django.db import migrations

CREATE_QUEUE_METRICS_VIEW = """
CREATE VIEW meet_mastrao_native_asr_queue_metrics AS
SELECT
    'ready'::text AS lane,
    COUNT(*)::bigint AS pending_count,
    COALESCE(FLOOR(EXTRACT(EPOCH FROM NOW() - MIN(capture.asr_next_at))), 0)::bigint
        AS oldest_due_age_seconds,
    COUNT(*) FILTER (
        WHERE capture.asr_dispatch_until > NOW() OR capture.asr_claim_until > NOW()
    )::bigint AS active_count
FROM meet_mastrao_native_capture_start AS capture
JOIN meet_mastrao_rtc_track_epoch AS epoch ON epoch.id = capture.epoch_id
JOIN meet_mastrao_rtc_connection AS connection ON connection.id = epoch.connection_id
WHERE capture.source_receipt IS NOT NULL
  AND capture.asr_acknowledged = FALSE
  AND capture.asr_next_at <= NOW()
  AND (capture.asr_attempts < 8 OR capture.asr_claim_until > NOW())
  AND capture.retention_expires_at > NOW()
  AND epoch.conflict = FALSE
  AND connection.correlation = 'correlated'
  AND (
      (
          capture.asr_dispatch_queue = 'mastrao-native-asr-ready'
          AND (capture.asr_dispatch_until > NOW() OR capture.asr_claim_until > NOW())
      )
      OR (
          (capture.asr_dispatch_until IS NULL OR capture.asr_dispatch_until <= NOW())
          AND (capture.asr_claim_until IS NULL OR capture.asr_claim_until <= NOW())
          AND capture.asr_attempts = 0
          AND capture.asr_error = ''
          AND capture.asr_receipt IS NULL
      )
  )
UNION ALL
SELECT
    'retry'::text AS lane,
    COUNT(*)::bigint AS pending_count,
    COALESCE(FLOOR(EXTRACT(EPOCH FROM NOW() - MIN(capture.asr_next_at))), 0)::bigint
        AS oldest_due_age_seconds,
    COUNT(*) FILTER (
        WHERE capture.asr_dispatch_until > NOW() OR capture.asr_claim_until > NOW()
    )::bigint AS active_count
FROM meet_mastrao_native_capture_start AS capture
JOIN meet_mastrao_rtc_track_epoch AS epoch ON epoch.id = capture.epoch_id
JOIN meet_mastrao_rtc_connection AS connection ON connection.id = epoch.connection_id
WHERE capture.source_receipt IS NOT NULL
  AND capture.asr_acknowledged = FALSE
  AND capture.asr_next_at <= NOW()
  AND (capture.asr_attempts < 8 OR capture.asr_claim_until > NOW())
  AND capture.retention_expires_at > NOW()
  AND epoch.conflict = FALSE
  AND connection.correlation = 'correlated'
  AND (
      (
          capture.asr_dispatch_queue = 'mastrao-native-asr-retry'
          AND (capture.asr_dispatch_until > NOW() OR capture.asr_claim_until > NOW())
      )
      OR (
          (capture.asr_dispatch_until IS NULL OR capture.asr_dispatch_until <= NOW())
          AND (capture.asr_claim_until IS NULL OR capture.asr_claim_until <= NOW())
          AND (
              capture.asr_attempts > 0
              OR capture.asr_error <> ''
              OR capture.asr_receipt IS NOT NULL
          )
      )
  );
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0054_native_asr_dispatch_queue")]

    operations = [
        migrations.RunSQL(
            sql=CREATE_QUEUE_METRICS_VIEW,
            reverse_sql="DROP VIEW meet_mastrao_native_asr_queue_metrics;",
        )
    ]
