from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("core", "0056_native_asr_delivery_recovery")]

    operations = [
        AddIndexConcurrently(
            model_name="mastraoroomclosure",
            index=models.Index(fields=["applied_at"], name="mastrao_close_applied_idx"),
        ),
        AddIndexConcurrently(
            model_name="mastraotranscriptionbinding",
            index=models.Index(
                fields=["state", "updated_at"], name="mastrao_tx_available_idx"
            ),
        ),
        AddIndexConcurrently(
            model_name="mastraotranscriptioneffect",
            index=models.Index(
                fields=["created_at"], name="mastrao_tx_effect_created_idx"
            ),
        ),
        AddIndexConcurrently(
            model_name="mastraotranscriptionproviderattempt",
            index=models.Index(
                fields=["created_at"], name="mastrao_tx_attempt_created_idx"
            ),
        ),
    ]
