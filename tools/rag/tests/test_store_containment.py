"""B-3 (round-3 hardening): the watcher refuses a symlink, and neither the
watcher's move nor the browser upload's write follows one either.

New module -- round-3 hardening constraint 26 (`tools/rag/tests/` is a
whole directory another session is editing): a task needing rag tests adds
a new module rather than appending to an existing one.

Four layers, five classes below (review round 1 split the containment
predicate in two -- the watcher and a browser upload no longer share ONE
set of owned roots, see `TestB3AssertInsideInbox`'s own docstring):

- `TestB3TheWatcherRefusesASymlink` -- the FRONT DOOR. `_IngestEventHandler`
  never even tracks a symlinked entry (`_is_regular_file_no_follow`), so it
  never reaches `enqueue_ingest` at all.
- `TestB3AssertInsideInbox` -- the pure predicate for the WATCHER's own,
  narrower door (`tools.rag.store.assert_inside_inbox`): admits the inbox
  only, refuses everything else -- `settings.DOCUMENTS_DIR`/
  `CHAT_STAGING_DIR` explicitly included, not merely "unrecognized".
- `TestB3AssertInsidePlatformDirs` -- the pure predicate for a BROWSER
  UPLOAD's own door (`tools.rag.store.assert_inside_platform_dirs`):
  admits the inbox and the chat-staging directory, refuses everything
  else, `settings.DOCUMENTS_DIR` included.
- `TestB3StageDocumentEnforcesContainmentForTheWatcherAndWebDoors` -- the
  BELT to the front door's BRACES: `stage_document` itself refuses an
  out-of-bounds resolved path for the watcher/web actor (as `StageRefused`,
  not the bare `ValueError` the two predicates above raise) even when
  nothing upstream is a symlink at all, and the CLI's own
  operator-names-their-own-file door stays exempt (`store_file`/
  `move_file`'s own "two named verbs" split, restated for containment).
- `TestB3TheLibraryUploadDoesNotWriteThroughALink` -- the WRITE DOOR:
  `tools.rag.ingest.stage_and_enqueue_one`'s one write site
  (`foundation.files.create_locked_file`) refuses a pre-planted symlink at
  the upload's own destination name rather than truncating whatever it
  points at.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from identity.contracts.principals import SERVICE_PRINCIPAL
from tools.rag import ingest, store
from tools.rag.models import Document
from tools.rag.tests._helpers import client  # noqa: F401 -- `client` is a fixture, discovered by name


def _fake_event(path: Path):
    return SimpleNamespace(is_directory=False, src_path=str(path))


@pytest.fixture
def _platform_dirs(tmp_path, settings):
    """Redirect the three directories this platform owns to SIBLING
    throwaway subdirectories of `tmp_path` -- never one nested inside
    another, so a test asserting one root is refused can't accidentally
    pass because it's nested inside an ADMITTED one -- so these tests
    never touch the real repo-local `data/` directory either. Same
    isolation `tools.rag.tests.test_ingest._managed_store` already gives
    `settings.DOCUMENTS_DIR` alone, extended to the other two roots."""
    settings.INGEST_INBOX_DIR = tmp_path / "inbox"
    settings.INGEST_INBOX_DIR.mkdir()
    settings.DOCUMENTS_DIR = tmp_path / "documents"
    settings.DOCUMENTS_DIR.mkdir()
    settings.CHAT_STAGING_DIR = tmp_path / "chat-attachments"
    settings.CHAT_STAGING_DIR.mkdir()
    return SimpleNamespace(
        inbox=settings.INGEST_INBOX_DIR,
        documents=settings.DOCUMENTS_DIR,
        chat=settings.CHAT_STAGING_DIR,
    )


@pytest.mark.django_db
class TestB3TheWatcherRefusesASymlink:
    def test_a_link_pointing_out_of_the_inbox_is_skipped(self, tmp_path, _platform_dirs):
        outside = tmp_path / "outside" / "victim.md"
        outside.parent.mkdir()
        outside.write_bytes(b"secret")
        link = _platform_dirs.inbox / "x.md"
        link.symlink_to(outside)
        handler = ingest._IngestEventHandler(_platform_dirs.inbox)

        handler.on_created(_fake_event(link))
        # B-3 review round 1 finding 2: `on_created` alone proves nothing
        # -- `poll_once` only ACTS on a pending entry once it has sat
        # unchanged for `STABLE_AFTER_SECONDS`, so calling it immediately
        # after `on_created` is a no-op regardless of whether the symlink
        # was ever tracked, and this test's assertions would pass even on
        # vulnerable code (`_track`'s guard removed) purely because
        # `poll_once` never got far enough to try. Seed `pending` directly
        # with a STALE timestamp, mirroring `test_an_ordinary_file_is_
        # still_staged`'s own shape, so `poll_once` actually attempts to
        # process this entry on THIS call -- `size` is the TARGET's own
        # size (what a bare, symlink-following `path.stat()` would read,
        # exactly what vulnerable code compares against), so a reverted
        # guard reaches the real `enqueue_ingest` call in this one pass,
        # not a second, silently-never-taken poll tick.
        handler.pending[str(link)] = (
            outside.stat().st_size, time.monotonic() - ingest.STABLE_AFTER_SECONDS - 1,
        )
        handler.poll_once()

        assert Document.objects.count() == 0
        assert outside.exists()
        assert outside.read_bytes() == b"secret"  # NOT moved

    def test_a_link_pointing_inside_the_inbox_is_skipped_too(self, _platform_dirs):
        """No "but it resolves somewhere safe" exception: a link is a
        second name for a file, and the watcher's job is to index files
        it was given, once."""
        real = _platform_dirs.inbox / "real.md"
        real.write_bytes(b"# hello")
        link = _platform_dirs.inbox / "alias.md"
        link.symlink_to(real)
        handler = ingest._IngestEventHandler(_platform_dirs.inbox)

        handler.on_created(_fake_event(link))

        assert str(link) not in handler.pending

    def test_an_ordinary_file_is_still_staged(self, _platform_dirs):
        f = _platform_dirs.inbox / "ok.md"
        f.write_bytes(b"# hello")
        handler = ingest._IngestEventHandler(_platform_dirs.inbox)
        handler.pending[str(f)] = (
            f.stat().st_size, time.monotonic() - ingest.STABLE_AFTER_SECONDS - 1,
        )

        handler.poll_once()  # the REAL enqueue_ingest -- no mock at all

        assert Document.objects.filter(original_path=str(f.resolve())).exists()


class TestB3AssertInsideInbox:
    """`tools.rag.store.assert_inside_inbox` -- the WATCHER's own door
    (review round 1 ruling): narrower than a browser upload's, and
    EXPLICITLY refuses the other two platform-owned directories rather
    than merely failing to recognize them -- the audit's own attack plants
    a symlink in the inbox pointing at a file already living in the
    managed store (or a staged chat attachment), and the watcher's own
    move-semantics enqueue would still relocate it a second time if either
    were admitted."""

    def test_admits_the_inbox(self, _platform_dirs):
        nested = _platform_dirs.inbox / "sub" / "file.txt"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"x")
        store.assert_inside_inbox(nested.resolve())  # does not raise

    def test_admits_the_inbox_itself(self, _platform_dirs):
        store.assert_inside_inbox(_platform_dirs.inbox.resolve())

    def test_refuses_the_managed_store(self, _platform_dirs):
        """The audit's own attack: a link in the inbox pointing at a file
        already in `DOCUMENTS_DIR` must not be admitted just because
        `DOCUMENTS_DIR` is ALSO a directory this platform owns."""
        nested = _platform_dirs.documents / "1" / "already-managed.pdf"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"x")
        with pytest.raises(ValueError):
            store.assert_inside_inbox(nested.resolve())

    def test_refuses_the_chat_staging_directory(self, _platform_dirs):
        nested = _platform_dirs.chat / "conv-1" / "already-staged.pdf"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"x")
        with pytest.raises(ValueError):
            store.assert_inside_inbox(nested.resolve())

    def test_refuses_a_path_outside_every_owned_directory(self, _platform_dirs):
        with pytest.raises(ValueError):
            store.assert_inside_inbox(Path("/etc/hosts"))


class TestB3AssertInsidePlatformDirs:
    """`tools.rag.store.assert_inside_platform_dirs` -- a BROWSER UPLOAD's
    own door (review round 1 ruling narrowed this from three roots to two):
    admits the inbox and the chat-staging directory, never
    `settings.DOCUMENTS_DIR` -- the managed store is not a valid
    destination for either upload door, so it's refused here too, the same
    as it is for the watcher's own, even narrower, `assert_inside_inbox`."""

    def test_admits_the_inbox_and_chat_staging(self, _platform_dirs):
        for root in (_platform_dirs.inbox, _platform_dirs.chat):
            nested = root / "sub" / "file.txt"
            nested.parent.mkdir(parents=True)
            nested.write_bytes(b"x")
            store.assert_inside_platform_dirs(nested.resolve())  # does not raise

    def test_admits_a_root_itself(self, _platform_dirs):
        store.assert_inside_platform_dirs(_platform_dirs.inbox.resolve())

    def test_refuses_the_managed_store(self, _platform_dirs):
        nested = _platform_dirs.documents / "1" / "already-managed.pdf"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"x")
        with pytest.raises(ValueError):
            store.assert_inside_platform_dirs(nested.resolve())

    def test_it_refuses_a_path_outside_both(self, _platform_dirs):
        with pytest.raises(ValueError):
            store.assert_inside_platform_dirs(Path("/etc/hosts"))

    def test_it_refuses_a_sibling_directory_that_merely_shares_a_prefix(self, _platform_dirs, tmp_path):
        """`is_relative_to`, not a bare string prefix check -- `<inbox>-evil`
        must not be admitted just because its name starts with `<inbox>`."""
        sibling = Path(str(_platform_dirs.inbox) + "-evil")
        sibling.mkdir()
        with pytest.raises(ValueError):
            store.assert_inside_platform_dirs((sibling / "x.txt").resolve())


@pytest.mark.django_db
class TestB3StageDocumentEnforcesContainmentForTheWatcherAndWebDoors:
    """Defense in depth beneath `TestB3TheWatcherRefusesASymlink`'s front
    door: `stage_document` refuses an out-of-bounds resolved path for the
    watcher/web actor even when nothing upstream is a symlink at all,
    raising `StageRefused` (the same exception B-2's re-stage refusal
    already uses -- review round 1 ruling), not the bare `ValueError` the
    two predicates above raise on their own. The CLI's own `actor=None`
    door stays exempt, exactly as `store.store_file`'s own docstring
    already keeps an operator's shell command trusted to name a path this
    platform doesn't itself own.
    """

    def test_the_watchers_actor_is_refused_outside_the_inbox(self, tmp_path, _platform_dirs):
        stray = tmp_path / "not-the-inbox.md"
        stray.write_bytes(b"# hello")

        with pytest.raises(ingest.StageRefused):
            ingest.stage_document(str(stray), move=False, actor=SERVICE_PRINCIPAL)

        assert Document.objects.count() == 0

    def test_the_watchers_actor_is_refused_a_path_under_the_managed_store(self, _platform_dirs):
        """The audit's own attack, through `stage_document` itself rather
        than the pure predicate: a symlink in the inbox resolving to a
        file already living in `DOCUMENTS_DIR` must be refused for the
        watcher's actor, not merely treated as "outside the inbox"."""
        already_managed = _platform_dirs.documents / "1" / "already-managed.pdf"
        already_managed.parent.mkdir(parents=True)
        already_managed.write_bytes(b"already a managed document")

        with pytest.raises(ingest.StageRefused):
            ingest.stage_document(str(already_managed), move=True, actor=SERVICE_PRINCIPAL)

        assert Document.objects.count() == 0
        assert already_managed.read_bytes() == b"already a managed document"  # NOT moved again

    def test_the_watchers_actor_stages_an_ordinary_inbox_path(self, _platform_dirs):
        f = _platform_dirs.inbox / "ok.md"
        f.write_bytes(b"# hello")

        doc, changed = ingest.stage_document(str(f), move=True, actor=SERVICE_PRINCIPAL)

        assert changed
        assert doc.original_path == str(f.resolve())

    def test_the_web_doors_actor_is_refused_outside_the_inbox_or_chat_staging(
        self, tmp_path, _platform_dirs,
    ):
        from identity.contracts.principals import Principal

        stray = tmp_path / "not-the-inbox.md"
        stray.write_bytes(b"# hello")

        with pytest.raises(ingest.StageRefused):
            ingest.stage_document(str(stray), move=False, actor=Principal("user", "1"))

        assert Document.objects.count() == 0

    def test_the_cli_operator_door_stays_exempt(self, tmp_path, _platform_dirs):
        """`manage.py ingest` (`actor=None`, `ingest_path`'s own call) names
        its OWN file directly -- containment does not apply to it, the same
        "an operator's shell command is trusted" rule `store.store_file`'s
        docstring already states."""
        stray = tmp_path / "operators-own-file.md"
        stray.write_bytes(b"# hello")

        doc, changed = ingest.stage_document(str(stray), move=False)

        assert changed
        assert doc.original_path == str(stray.resolve())


@pytest.mark.django_db
class TestB3TheLibraryUploadDoesNotWriteThroughALink:
    def test_the_library_upload_refuses_to_write_through_an_existing_link(
        self, tmp_path, client, _platform_dirs,  # noqa: F811 -- `client` fixture, not the stdlib module
    ):
        target = tmp_path / "outside" / "settings.env"
        target.parent.mkdir()
        target.write_bytes(b"KEY=x")
        (_platform_dirs.inbox / "report.md").symlink_to(target)

        upload = SimpleUploadedFile("report.md", b"attacker content", content_type="text/markdown")
        response = client.post(reverse("rag-document-upload"), data={"files": [upload]})

        assert response.status_code == 302  # an honest redirect, never a 500
        assert target.read_bytes() == b"KEY=x"  # untouched
        assert Document.objects.count() == 0
