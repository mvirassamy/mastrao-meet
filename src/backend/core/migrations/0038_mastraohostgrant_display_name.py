from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0037_mastraoguestgrant_display_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="mastraohostgrant",
            name="display_name",
            field=models.CharField(blank=True, max_length=160, null=True),
        ),
    ]
