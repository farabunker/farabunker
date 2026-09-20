"""Repoint `django_admin_log.user_id` at the new user model.

Django's autodetector may not write migrations into a third-party app,
so `django.contrib.admin`'s own FK to `auth_user` has to be moved by
hand. The bodies live in `_0002_helpers.py` so they can be unit-tested
without driving the migration executor.

IRREVERSIBLE BY DESIGN: `reverse_code` is a documented no-op, because
the reversal of a user-model swap is restoring the backup.
"""
from django.db import migrations

from identity.migrations import _0002_helpers as helpers


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0001_initial"),
        ("admin", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(helpers.repoint, helpers.noop),
    ]
