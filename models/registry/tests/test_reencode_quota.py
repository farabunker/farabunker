"""`role_reencode`'s own `QueueQuotaExceeded` handling (C-7 review, fix
round 1).

A NEW MODULE, per the fix-round-1 brief: `role_reencode` had no handler
for this exception before this round -- there is no broad `except
Exception` around its own `enqueue()` call, only `except ValueError` and
`except QueueUnavailable`, so a superuser already at their own queue
quota would have hit an uncaught exception (a 500). The brief's test
placement is `models/registry/tests/test_reencode_quota.py` unless a
registry test module cannot be added without opening a held file; no
registry file is held (`implementer-common.md`'s hardening-branch
addition names none in `models/registry/`), so this is a fresh module
rather than an addition to `test_views_reencode_and_sections.py` (not
opened by this fix round).

Setup mirrors that file's own `TestRoleReencode.
test_queue_unavailable_becomes_the_migrations_copy_error` exactly (read,
not imported from, since that module is not opened): `@patch("models.
registry.views.enqueue")` with a `side_effect`, an embeddings connection
bound to `rag.embed` so `plan_reencode`'s own preflight `resolve()`
succeeds before the (mocked) `enqueue()` is ever reached.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse

from models.contracts.queue import QueueQuotaExceeded
from models.registry.models import RoleBinding
from models.registry.tests._helpers import (
    client,  # noqa: F401 -- requested by name as a fixture
    clean_probe_cache,
    clear_seeded_rows,
    make_embed_connection,
)


@pytest.fixture(autouse=True)
def _clear_seeded_rows(db):
    clear_seeded_rows()


@pytest.fixture(autouse=True)
def _clean_probe_cache():
    clean_probe_cache()
    yield
    clean_probe_cache()


@pytest.mark.django_db
class TestReencodeAnswersItsOwnQuotaHonestly:
    @patch("models.registry.views.enqueue")
    def test_a_quota_refusal_is_an_honest_message_never_a_500(self, mock_enqueue, client):
        embed = make_embed_connection()
        RoleBinding.objects.create(role_key="rag.embed", connection=embed)
        mock_enqueue.side_effect = QueueQuotaExceeded(
            "1 jobs are already queued or running for this account — the "
            "limit is 1. Wait for one to finish before starting another."
        )

        response = client.post(reverse("inference-role-reencode"), data={"role_key": "rag.embed"})

        # NEVER-500 (the shape every other refusal on this view already
        # gives): a redirect-after-POST with a `messages` entry, the same
        # as the `QueueUnavailable` sibling test, not an uncaught
        # exception the Django test client would otherwise re-raise.
        assert response.status_code == 302
        followed = client.get(response.url)
        body = followed.content.decode()
        assert "already queued or running" in body
        assert "Re-encode queued" not in body
