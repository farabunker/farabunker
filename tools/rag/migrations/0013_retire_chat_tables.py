"""Drop the two write-only chat tables (agents spec section 7.6).

A DROP, not a data migration. Nothing ever READ these rows -- the only
production writer was `answer_question`'s `session_id` branch, and the
only shipped way to reach it was `manage.py ask --session` -- so nothing
loses a feature. A live box may hold rows from such a run; the recovery
path is the backup every deploy already takes
(`foundation/ops/backup.py`), and this drop is recorded in
docs/OPERATIONS.md.

Inventing a data migration into `agents.Turn` was considered and
rejected: it would fabricate conversations that never had an agent, a
tool call, or a depth, and a fabricated history is worse than none.

ChatMessage first: it holds the FK.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("rag", "0012_ragsettings_hybrid_search")]

    operations = [
        migrations.DeleteModel(name="ChatMessage"),
        migrations.DeleteModel(name="ChatSession"),
    ]
