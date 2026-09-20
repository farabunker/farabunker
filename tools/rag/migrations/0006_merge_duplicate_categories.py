"""Merge pre-existing case-insensitive duplicate categories into one row each,
so the case-insensitive unique constraint (0007) can be added and so the
library stops showing forked shelves (e.g. 'medical' vs seeded 'Medical').
Reassigns each duplicate's documents to the canonical row, then deletes it.
"""
from django.db import migrations

SEEDED_NAMES = {"Medical", "Engineering", "Reference & Manuals", "Business"}


def choose_canonical(members, seeded_names):
    """INLINED from the rag column's `categories.choose_canonical` as it
    stood at the time of this migration's original inlining (spec section
    3.6.2, the one deliberate content change in the P0 regroup; the rag
    column has since been renamed by that same regroup).

    A migration must be frozen against the app code it ran with, and this
    one was importing a live project module -- which Django imports at
    GRAPH-BUILD time, so a stale path there breaks `migrate` and
    `makemigrations` outright rather than only this one migration. The
    function is small and pure, so the honest fix is a copy that can
    never drift under the historical data it rewrote, not a corrected
    import.

    From >=1 Category-like objects sharing a normalized name, pick the
    row to keep: prefer one whose `.name` is a seeded default (its
    "pretty" casing), else the earliest by (created_at, id).
    """
    seeded = [c for c in members if c.name in seeded_names]
    pool = seeded or list(members)
    return min(pool, key=lambda c: (c.created_at, c.id))


def merge_dupes(apps, schema_editor):
    Category = apps.get_model("rag", "Category")
    Document = apps.get_model("rag", "Document")

    groups: dict[str, list] = {}
    for cat in Category.objects.all():
        groups.setdefault(cat.name.strip().lower(), []).append(cat)

    for members in groups.values():
        if len(members) < 2:
            continue
        canonical = choose_canonical(members, SEEDED_NAMES)
        for dup in members:
            if dup.pk == canonical.pk:
                continue
            Document.objects.filter(category=dup).update(category=canonical)
            dup.delete()


def noop(apps, schema_editor):
    # A merge cannot be safely un-merged.
    pass


class Migration(migrations.Migration):
    dependencies = [("rag", "0005_document_original_path")]
    operations = [migrations.RunPython(merge_dupes, noop)]
