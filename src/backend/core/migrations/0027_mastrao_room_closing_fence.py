# Generated for immediate fencing after Cabinet Core accepts a room close.
# pylint: disable=invalid-name,missing-class-docstring,missing-module-docstring

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0026_mastrao_room_closure")]

    operations = [
        migrations.AddField(
            model_name="mastraoroombinding",
            name="closing_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
