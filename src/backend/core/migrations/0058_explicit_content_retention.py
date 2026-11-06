"""Add explicit persistent content retention without rewriting historical claims."""

from importlib import import_module

from django.db import migrations, models

REPLACE_QUEUE_METRICS_VIEW = """
CREATE OR REPLACE VIEW meet_mastrao_native_asr_queue_metrics AS
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
  AND (
      (capture.retention_mode = 'bounded' AND capture.retention_expires_at > NOW())
      OR (capture.retention_mode = 'until_deleted' AND capture.retention_expires_at IS NULL)
  )
  AND NOT (capture.retention_mode = 'until_deleted' AND capture.asr_error = 'native_asr_rejected')
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
  AND (
      (capture.retention_mode = 'bounded' AND capture.retention_expires_at > NOW())
      OR (capture.retention_mode = 'until_deleted' AND capture.retention_expires_at IS NULL)
  )
  AND NOT (capture.retention_mode = 'until_deleted' AND capture.asr_error = 'native_asr_rejected')
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
    dependencies = [("core", "0057_observability_metrics_history_indexes")]

    operations = [
        migrations.AddField(
            model_name='mastraonativecapturestart',
            name='retention_mode',
            field=models.CharField(default='bounded', max_length=20),
        ),
        migrations.AddField(
            model_name='mastraorecordingbinding',
            name='retention_mode',
            field=models.CharField(default='bounded', max_length=20),
        ),
        migrations.AlterField(
            model_name='mastraorecordingbinding',
            name='retention_expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name='mastraonativecapturestart',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(retention_mode='bounded')
                    | models.Q(
                        retention_mode='until_deleted',
                        retention_expires_at__isnull=True,
                    )
                ),
                name='mastrao_native_retention_coherent',
            ),
        ),
        migrations.AddConstraint(
            model_name='mastraorecordingbinding',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        retention_mode='bounded',
                        retention_expires_at__isnull=False,
                    )
                    | models.Q(
                        retention_mode='until_deleted',
                        retention_expires_at__isnull=True,
                    )
                ),
                name='mastrao_recording_retention_coherent',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='mastraotranscriptionbinding',
            name='mastrao_tx_contract_version_closed',
        ),
        migrations.AddConstraint(
            model_name='mastraotranscriptionbinding',
            constraint=models.CheckConstraint(
                condition=models.Q(contract_operation_version__in=[1, 2, 3, 4, 5]),
                name='mastrao_tx_contract_version_closed',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='mastraotranscriptionbinding',
            name='mastrao_tx_profile_complete_by_version',
        ),
        migrations.AddConstraint(
            model_name='mastraotranscriptionbinding',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        contract_operation_version=1,
                        notice_version__isnull=True,
                        notice_digest__isnull=True,
                        authority_version__isnull=True,
                        maximum_audio_seconds__isnull=True,
                        maximum_audio_bytes__isnull=True,
                        asr_profile_ref__isnull=True,
                        asr_profile_digest__isnull=True,
                        asr_provider_ref__isnull=True,
                        requested_model_ref__isnull=True,
                        request_config_digest__isnull=True,
                        normalization_version__isnull=True,
                        processing_region_ref__isnull=True,
                        data_control_ref__isnull=True,
                        campaign_ref__isnull=True,
                        authorized_cost_ceiling_micros__isnull=True,
                        currency__isnull=True,
                        tariff_catalog_version__isnull=True,
                    )
                    | models.Q(
                        contract_operation_version=2,
                        notice_version__isnull=True,
                        notice_digest__isnull=True,
                        authority_version__isnull=True,
                        maximum_audio_seconds__isnull=True,
                        maximum_audio_bytes__isnull=True,
                        asr_profile_ref__isnull=False,
                        asr_profile_digest__isnull=False,
                        asr_provider_ref__isnull=False,
                        requested_model_ref__isnull=False,
                        request_config_digest__isnull=False,
                        normalization_version__isnull=False,
                        processing_region_ref__isnull=False,
                        data_control_ref__isnull=False,
                        campaign_ref__isnull=True,
                        authorized_cost_ceiling_micros__isnull=True,
                        currency__isnull=True,
                        tariff_catalog_version__isnull=True,
                    )
                    | models.Q(
                        contract_operation_version=3,
                        notice_version__isnull=True,
                        notice_digest__isnull=True,
                        authority_version__isnull=True,
                        maximum_audio_seconds__isnull=True,
                        maximum_audio_bytes__isnull=True,
                        asr_profile_ref__isnull=False,
                        asr_profile_digest__isnull=False,
                        asr_provider_ref__isnull=False,
                        requested_model_ref__isnull=False,
                        request_config_digest__isnull=False,
                        normalization_version__isnull=False,
                        processing_region_ref__isnull=False,
                        data_control_ref__isnull=False,
                        campaign_ref__isnull=False,
                        authorized_cost_ceiling_micros__isnull=False,
                        currency='USD',
                        tariff_catalog_version__isnull=False,
                    )
                    | models.Q(
                        contract_operation_version__in=[4, 5],
                        notice_version__isnull=False,
                        notice_digest__isnull=False,
                        notice_digest__regex='^[a-f0-9]{64}$',
                        asr_profile_ref__isnull=False,
                        asr_profile_digest__isnull=False,
                        asr_provider_ref__isnull=False,
                        requested_model_ref__isnull=False,
                        request_config_digest__isnull=False,
                        normalization_version__isnull=False,
                        processing_region_ref__isnull=False,
                        data_control_ref__isnull=False,
                        campaign_ref__isnull=True,
                        authorized_cost_ceiling_micros__isnull=False,
                        currency='USD',
                        tariff_catalog_version__isnull=False,
                        authority_version__isnull=False,
                        authority_version__gt=0,
                        maximum_audio_seconds__isnull=False,
                        maximum_audio_seconds__gt=0,
                        maximum_audio_bytes__isnull=False,
                        maximum_audio_bytes__gt=0,
                    )
                ),
                name='mastrao_tx_profile_complete_by_version',
            ),
        ),
        migrations.RunSQL(
            sql=REPLACE_QUEUE_METRICS_VIEW,
            reverse_sql=(
                import_module('core.migrations.0056_native_asr_delivery_recovery').REPLACE_QUEUE_METRICS_VIEW
            ),
        ),
    ]
