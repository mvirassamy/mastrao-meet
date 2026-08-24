from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0034_mastrao_transcription_run_binding")]

    operations = [
        migrations.RemoveConstraint(
            model_name="mastraotranscriptionbinding",
            name="mastrao_tx_contract_version_closed",
        ),
        migrations.RemoveConstraint(
            model_name="mastraotranscriptionbinding",
            name="mastrao_tx_profile_complete_by_version",
        ),
        migrations.AddField(
            model_name="mastraotranscriptionbinding",
            name="campaign_ref",
            field=models.CharField(blank=True, max_length=160, null=True),
        ),
        migrations.AddField(
            model_name="mastraotranscriptionbinding",
            name="authorized_cost_ceiling_micros",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraotranscriptionbinding",
            name="currency",
            field=models.CharField(blank=True, max_length=8, null=True),
        ),
        migrations.AddField(
            model_name="mastraotranscriptionbinding",
            name="tariff_catalog_version",
            field=models.CharField(blank=True, max_length=160, null=True),
        ),
        migrations.AddField(
            model_name="mastraotranscriptionproviderattempt",
            name="grant_semantic_digest",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="mastraotranscriptionproviderattempt",
            name="authority_version",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraotranscriptionproviderattempt",
            name="execution_mode",
            field=models.CharField(
                blank=True,
                choices=[
                    ("send_allowed", "Send allowed"),
                    ("recover_only", "Recover only"),
                ],
                max_length=16,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="mastraotranscriptionproviderattempt",
            name="campaign_ref",
            field=models.CharField(blank=True, max_length=160, null=True),
        ),
        migrations.AddField(
            model_name="mastraotranscriptionproviderattempt",
            name="authorized_cost_ceiling_micros",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraotranscriptionproviderattempt",
            name="tariff_catalog_version",
            field=models.CharField(blank=True, max_length=160, null=True),
        ),
        migrations.AddField(
            model_name="mastraotranscriptionproviderattempt",
            name="provider_egress_opened_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraotranscriptionproviderattempt",
            name="provider_observed_model_ref",
            field=models.CharField(blank=True, max_length=160, null=True),
        ),
        migrations.AddField(
            model_name="mastraotranscriptionproviderattempt",
            name="provider_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mastraotranscriptionproviderattempt",
            name="terminal_outcome",
            field=models.CharField(
                blank=True,
                choices=[
                    ("failed_pre_egress", "Failed before egress"),
                    ("rate_limited", "Rate limited"),
                    ("rejected", "Rejected"),
                    ("unknown", "Unknown"),
                    ("deleted", "Deleted"),
                    ("conflict", "Conflict"),
                ],
                max_length=24,
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraotranscriptionbinding",
            constraint=models.CheckConstraint(
                condition=models.Q(contract_operation_version__in=[1, 2, 3]),
                name="mastrao_tx_contract_version_closed",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraotranscriptionbinding",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        contract_operation_version=1,
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
                        currency="USD",
                        tariff_catalog_version__isnull=False,
                    )
                ),
                name="mastrao_tx_profile_complete_by_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraotranscriptionproviderattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(grant_semantic_digest__isnull=True)
                | models.Q(grant_semantic_digest__regex=r"^[a-f0-9]{64}$"),
                name="mastrao_tx_attempt_grant_digest_format",
            ),
        ),
        migrations.AddConstraint(
            model_name="mastraotranscriptionproviderattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(provider_egress_opened_at__isnull=True)
                | models.Q(execution_mode__isnull=False),
                name="mastrao_tx_attempt_egress_has_mode",
            ),
        ),
    ]
