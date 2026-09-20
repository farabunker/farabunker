"""round-3 final wave: `tools.rag.store.assert_inside_inbox`/
`assert_inside_platform_dirs` used to raise a message carrying the
RESOLVED, ABSOLUTE host path -- `tools.rag.ingest.stage_document` wraps it
verbatim as `StageRefused`, `StageOutcome.reason` carries it unchanged,
and both upload doors (`tools.rag.views.document_upload`,
`tools.rag.services.stage_turn_attachments`) show it straight to whoever
is on the other end of the request: a flash message or an end-to-end
JSON/HTML response body, neither of which is the place for a host
filesystem detail. The fix keeps only the refused file's own NAME
(`Path(resolved).name`) in what a caller ever sees, and logs the
resolved path at error level so the operator still has it.

New module -- round-3 hardening constraint 26 (`tools/rag/tests/` is a
whole directory another session is editing): a task needing rag tests
here adds a new module rather than appending to any existing one, this
file included (`tools/rag/tests/test_store_containment.py`'s own B-3
classes are read for their fixture/trigger shapes, not opened).
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from tools.rag import store
from tools.rag.tests._helpers import client  # noqa: F401 -- `client` is a fixture, discovered by name

pytestmark = pytest.mark.django_db


@pytest.fixture
def _platform_dirs(tmp_path, settings):
    """Redirect the platform-owned directories to throwaway siblings of
    `tmp_path`, the same isolation `test_store_containment.py`'s own
    `_platform_dirs` fixture gives its tests -- duplicated here rather
    than imported across modules (this package's convention; see
    `tools.rag.tests._helpers.isolated_tool_registry`'s own docstring for
    why a shared fixture stays put)."""
    settings.INGEST_INBOX_DIR = tmp_path / "inbox"
    settings.INGEST_INBOX_DIR.mkdir()
    settings.CHAT_STAGING_DIR = tmp_path / "chat-attachments"
    settings.CHAT_STAGING_DIR.mkdir()
    settings.DOCUMENTS_DIR = tmp_path / "documents"
    settings.DOCUMENTS_DIR.mkdir()


class TestTheStoreRefusalNamesTheFileNotTheHostPath:
    """(a) The unit-level half: `assert_inside_platform_dirs` (the
    browser-upload door's own predicate; `assert_inside_inbox` shares the
    same fix, same shape, not separately re-proven here) raises a
    path-free message, and logs the resolved path at error level so an
    operator reading the log still has it."""

    def test_the_exception_names_the_file_and_omits_the_host_directory(
        self, tmp_path, _platform_dirs,
    ):
        outside_dir = tmp_path / "not-owned-by-this-platform"
        outside_dir.mkdir()
        stray = outside_dir / "shadow-report.pdf"
        stray.write_bytes(b"x")

        with pytest.raises(ValueError) as excinfo:
            store.assert_inside_platform_dirs(stray.resolve())

        message = str(excinfo.value)
        assert "shadow-report.pdf" in message
        assert str(outside_dir) not in message
        assert str(stray.resolve()) not in message

    def test_the_resolved_path_is_still_logged_for_the_operator(
        self, tmp_path, _platform_dirs, caplog,
    ):
        outside_dir = tmp_path / "not-owned-by-this-platform"
        outside_dir.mkdir()
        stray = outside_dir / "shadow-report.pdf"
        stray.write_bytes(b"x")

        with caplog.at_level(logging.ERROR):
            with pytest.raises(ValueError):
                store.assert_inside_platform_dirs(stray.resolve())

        assert any(str(stray.resolve()) in record.message for record in caplog.records)


class TestTheDocumentUploadFlashDoesNotLeakTheHostPath:
    """(b) The end-to-end half: `document_upload`'s own "refused" branch
    (`messages.error(request, outcome.reason)`) shows `StageOutcome.
    reason` verbatim -- proving it is path-free end to end means the
    request must actually hit a containment refusal. Reaching
    `assert_inside_platform_dirs` with an out-of-bounds resolved path
    through the live write door needs either a symlink (already covered,
    and refused earlier, by `TestB3TheLibraryUploadDoesNotWriteThroughA
    Link`'s own SymlinkRefused check) or a category-traversal (already
    refused earlier still, by this view's own `target_dir.is_relative_to`
    check) -- neither reaches this predicate at all. So this exercises the
    REAL predicate (the same one the unit test above proves path-free) by
    forcing what it sees on ONE call, the way `test_ingest_scan_bounds.
    py`'s own fallback-scan tests force a scan result rather than
    constructing a genuinely corrupt file: the resolved path a symlink
    escaping the write door's own pre-check would eventually hand it in
    production."""

    def test_the_flash_names_the_file_not_the_host_path(
        self, tmp_path, _platform_dirs, client,  # noqa: F811 -- fixture, not the stdlib module
    ):
        outside_dir = tmp_path / "not-owned-by-this-platform"
        outside_dir.mkdir()
        stray = outside_dir / "shadow-report.pdf"
        stray.write_bytes(b"x")

        real_assert_inside_platform_dirs = store.assert_inside_platform_dirs

        def _refuse_with_the_real_predicate(_resolved):
            # The write door already wrote a real, in-bounds file -- this
            # forces the SAME containment refusal a symlink escaping that
            # write would produce, through the real function (so this
            # test proves the real message-building code, not a
            # hand-written stand-in for it).
            real_assert_inside_platform_dirs(stray.resolve())

        upload = SimpleUploadedFile("report.md", b"hello", content_type="text/markdown")
        with patch.object(
            store, "assert_inside_platform_dirs", side_effect=_refuse_with_the_real_predicate,
        ):
            response = client.post(
                reverse("rag-document-upload"), data={"files": [upload]}, follow=True,
            )

        body = response.content.decode()
        assert "shadow-report.pdf" in body
        assert str(outside_dir) not in body
        assert str(stray.resolve()) not in body
