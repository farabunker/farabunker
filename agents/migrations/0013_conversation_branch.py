# Branch provenance (chat cluster, feature C). Two nullable columns,
# additive, nothing back-filled: every existing conversation was started
# rather than branched, and null is the honest value for it.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0012_agent_box_wide"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="branched_from",
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="branches", to="agents.conversation"),
        ),
        migrations.AddField(
            model_name="conversation",
            name="branched_at_index",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
