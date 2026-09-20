"""Category name normalization + case-insensitive resolution (ADR 0009).

Category identity is case- and whitespace-insensitive: "Medical", "medical",
and " medical " denote the same shelf. This is the single place that
normalizes a raw name and resolves it to a Category row, so ingest and the
upload/rename endpoints cannot reintroduce casing duplicates. Top-level
imports stay dependency-free as a matter of hygiene -- migration 0006 used
to import `choose_canonical` from here and no longer does (it carries its
own frozen copy, per the P0 regroup), so nothing outside this module and
its tests depends on that property today.
"""
from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")


def normalize_category_name(raw: str) -> str:
    """Strip surrounding whitespace and collapse internal runs to single
    spaces. Returns "" for blank/whitespace-only input (caller treats that as
    Uncategorized)."""
    if not raw:
        return ""
    return _WHITESPACE.sub(" ", raw).strip()


def choose_canonical(members, seeded_names):
    """From >=1 Category-like objects sharing a normalized name, pick the row
    to keep: prefer one whose `.name` is a seeded default (its "pretty"
    casing), else the earliest by (created_at, id). Pure -- operates on the
    objects passed in."""
    seeded = [c for c in members if c.name in seeded_names]
    pool = seeded or list(members)
    return min(pool, key=lambda c: (c.created_at, c.id))


def get_or_create_category(raw: str):
    """Resolve `raw` to a Category, reusing any case-insensitive match (so
    "medical" reuses a seeded "Medical"). Returns None for blank input
    (Uncategorized). New rows keep the given (normalized) display casing;
    existing rows are never re-cased here."""
    from tools.rag.models import Category  # lazy: keep module import light

    name = normalize_category_name(raw)
    if not name:
        return None
    existing = Category.objects.filter(name__iexact=name).first()
    return existing or Category.objects.create(name=name)
