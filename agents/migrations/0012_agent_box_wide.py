# The audience column (chat cluster, feature B). Additive, and
# behaviour-preserving on landing day: the data migration below sets
# `box_wide = resident` for every existing row, and `visible_agents`
# swaps one leg for a column holding the same truth.
from django.db import migrations, models

from agents.migrations._0012_helpers import clear_box_wide, copy_resident_to_box_wide


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0011_turn_author"),
    ]

    operations = [
        migrations.AddField(
            model_name="agent",
            name="box_wide",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(copy_resident_to_box_wide, clear_box_wide),
    ]
