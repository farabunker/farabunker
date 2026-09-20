# Data migration (ADR 0009): seed a small set of default Categories so the
# library UI isn't empty on a fresh install. Categories are fully
# user-editable afterwards -- this is a starting point, not a fixed enum.
# Idempotent (get_or_create) so it's safe to run more than once (e.g. a
# squashed migration history, or manual re-application). Deliberately does
# NOT seed an "Uncategorized" row: null represents that pseudo-category
# (see Document.category / Category docstrings in tools/rag/models.py).
from django.db import migrations

DEFAULT_CATEGORIES = [
    "Medical",
    "Engineering",
    "Reference & Manuals",
    "Business",
]


def seed_default_categories(apps, schema_editor):
    Category = apps.get_model("rag", "Category")
    for name in DEFAULT_CATEGORIES:
        Category.objects.get_or_create(name=name)


def unseed_default_categories(apps, schema_editor):
    """Reverse: remove exactly the seeded defaults, but only the ones left
    unused (no Documents assigned) -- never delete a category an operator
    has actually put documents into, even if it happens to share a seeded
    name."""
    Category = apps.get_model("rag", "Category")
    Category.objects.filter(name__in=DEFAULT_CATEGORIES, documents__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("rag", "0003_category_document_category"),
    ]

    operations = [
        migrations.RunPython(seed_default_categories, unseed_default_categories),
    ]
