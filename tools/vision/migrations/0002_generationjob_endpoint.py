"""Record the endpoint a job was submitted to (D6's binding snapshot).

`engine`, `model_id`, `model_fingerprint`, and `model_config` already
described the binding; the ADDRESS was missing, which is what forced
`refresh_job` to re-resolve the role and risk polling a rebound engine's
queue for a job it never had.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("vision", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="generationjob",
            name="endpoint",
            field=models.CharField(blank=True, default="", max_length=512),
            preserve_default=False,
        ),
    ]
