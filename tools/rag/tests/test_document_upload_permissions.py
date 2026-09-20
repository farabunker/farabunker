"""Regression tests for H13 review round 1, finding 4:
`tools.rag.views.document_upload` must never force-chmod a PRE-EXISTING
SHARED directory it did not itself create. `settings.INGEST_INBOX_DIR` is
exactly that when no category is given -- `target_dir` resolves to the
inbox root itself, shared across every category and every future upload,
not owned by this one call.

A NEW module -- `tools/rag/tests/` is a whole directory another session
edits concurrently (plan constraint 18) -- rather than an addition to
`test_views_upload_and_settings.py`.
"""
from __future__ import annotations

import os
import stat

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse


@pytest.fixture
def client():
    return Client()


@pytest.mark.django_db
class TestInboxDirectoryNeverForciblyTightened:
    def test_a_pre_existing_inbox_root_keeps_its_mode_on_a_no_category_upload(
        self, client, settings, tmp_path
    ):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        os.chmod(inbox, 0o755)
        settings.INGEST_INBOX_DIR = inbox

        f = SimpleUploadedFile("guide.md", b"# hello", content_type="text/markdown")
        resp = client.post(reverse("rag-document-upload"), data={"files": [f], "category": ""})

        assert resp.status_code == 302
        assert stat.S_IMODE(inbox.stat().st_mode) == 0o755

    def test_a_missing_inbox_root_is_still_created_and_tightened(self, client, settings, tmp_path):
        """`only_if_created=True` still creates AND tightens a leaf that
        doesn't exist yet -- it only skips the chmod for one that was
        already there."""
        inbox = tmp_path / "inbox"  # deliberately not created
        settings.INGEST_INBOX_DIR = inbox

        f = SimpleUploadedFile("guide.md", b"# hello", content_type="text/markdown")
        resp = client.post(reverse("rag-document-upload"), data={"files": [f], "category": ""})

        assert resp.status_code == 302
        assert stat.S_IMODE(inbox.stat().st_mode) == 0o700

    def test_a_fresh_per_category_leaf_is_still_tightened(self, client, settings, tmp_path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        os.chmod(inbox, 0o755)
        settings.INGEST_INBOX_DIR = inbox

        f = SimpleUploadedFile("guide.md", b"# hello", content_type="text/markdown")
        resp = client.post(
            reverse("rag-document-upload"), data={"files": [f], "category": "Medical"}
        )

        assert resp.status_code == 302
        assert stat.S_IMODE((inbox / "Medical").stat().st_mode) == 0o700
        assert stat.S_IMODE(inbox.stat().st_mode) == 0o755  # the shared root, untouched either way
