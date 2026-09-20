"""B-2 (round-3 hardening): a library upload's identity stops being a
path every principal shares.

NEW MODULE, not an addition to `test_ingest.py` -- round-2 constraint 18
(restated here as this plan's own constraint 26) holds `tools/rag/tests/`
as a whole directory another session is editing; a task needing rag
tests adds a new module rather than touching an existing one.

`stage_document`'s re-stage branch used to reuse ANY existing `Document`
row at a changed `original_path` with no principal check at all --
`<inbox>/<category>/<basename>` carries no per-user prefix, so one
member's filename collides with another's by construction, and the
branch would delete the first uploader's chunks/stored file and restamp
THEIR labels onto the second uploader's bytes. This module pins the fix:
the re-stage branch is now an authorization decision
(`tools.rag.ingest._may_restage`), and the two callers that are not a
principal at all -- `actor=None` (the CLI, `manage.py ingest`, and the
notes-consolidation job) and `actor=SERVICE_PRINCIPAL` (the watcher's
REAL actor -- round-1 review finding 1: an earlier cut of this module
wrongly pinned `actor=None` as "the watch folder") -- both keep their
pre-existing "same path, new bytes = re-index in place" semantics
untouched.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.contracts.principals import SERVICE_PRINCIPAL
from tools.rag import ingest
from tools.rag.models import Document, DocumentEntitlement
from tools.rag.tests._helpers import (
    _workstream, grant, make_admin, make_entitlement, make_user, posture, reset_settings,
    seed_sweep_posture, user_principal,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


@pytest.fixture(autouse=True)
def _managed_store(tmp_path, settings):
    """Redirect the managed document store (ADR 0009) to a throwaway
    directory, distinct from any `tmp_path` a test uses for its own
    source files -- the same fixture `test_ingest.py` carries, copied
    rather than imported (that module is one of the held ones, round-2
    constraint 26).

    B-3 fixture fix (round-3 hardening; coordinator ruling waives
    constraint 26 for this collision), review round 1: `settings.
    INGEST_INBOX_DIR` and `settings.DOCUMENTS_DIR` are SIBLING
    subdirectories of `tmp_path` (`tmp_path / "inbox"`, `tmp_path /
    "documents"`) -- deliberately NOT one nested inside the other. The
    watcher's own containment check (`tools.rag.store.assert_inside_
    inbox`) now REFUSES a resolved path under `DOCUMENTS_DIR` for
    `actor == SERVICE_PRINCIPAL` (round-1 review ruling); nesting
    `DOCUMENTS_DIR` under `INGEST_INBOX_DIR`, as an earlier cut of this
    fixture did, would have made every managed-store path ALSO read as
    "inside the inbox" and silently defeated that refusal in every test
    here. `inbox_file` below writes under the redirected inbox to match."""
    inbox_root = tmp_path / "inbox"
    inbox_root.mkdir()
    store_root = tmp_path / "documents"
    store_root.mkdir()
    settings.INGEST_INBOX_DIR = inbox_root
    settings.DOCUMENTS_DIR = store_root
    return store_root


def make_member(username):
    """A signed-in, non-admin principal -- `stage_document`'s `actor`
    is a `Principal`, not a `User` row, so every test here mints one the
    same way `identity.request.principal_for_request` would for a real
    session."""
    return user_principal(make_user(username=username))


def make_superuser(username):
    """A signed-in administrator's principal -- `identity.access.
    is_admin`'s active-superuser branch."""
    return user_principal(make_admin(username=username))


def inbox_file(tmp_path, rel_path: str, content: bytes) -> Path:
    """A file at `tmp_path / "inbox" / rel_path`, parents created as
    needed -- `tmp_path / "inbox"` is exactly where this module's
    `_managed_store` fixture points `settings.INGEST_INBOX_DIR` (see its
    own docstring for why it's a SIBLING of `DOCUMENTS_DIR`, not a
    parent).

    Pre-B-3, `stage_document` took a bare path and did not itself require
    the real watched inbox directory -- only `tools.rag.views.
    document_upload`'s own containment check did. As of B-3 (round-3
    hardening), `stage_document` ALSO checks containment for any
    non-`None` actor (`tools.rag.store.assert_inside_inbox`/
    `assert_inside_platform_dirs`, depending on the actor) -- every file
    this helper writes now resolves inside the directory those checks
    admit."""
    path = tmp_path / "inbox" / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def label_document(document, *, entitlement):
    """One `DocumentEntitlement` row -- the same direct-create pattern
    `test_access_documents.py`/`test_views_documents.py` already use
    (`set_document_labels`'s own audit/re-stamp machinery is not this
    module's business)."""
    return DocumentEntitlement.objects.create(document=document, entitlement=entitlement)


class TestB2TheRestageBranchIsAnAuthorizationDecision:

    def test_a_member_may_not_take_over_another_members_row(self, tmp_path):
        with posture(POSTURE_ENTERPRISE):
            victim, attacker = make_member("victim"), make_member("attacker")
            path = inbox_file(tmp_path, "Finance/quarterly.pdf", b"original bytes")
            doc, _ = ingest.stage_document(str(path), category="Finance", move=False, actor=victim)
            path.write_bytes(b"attacker bytes")
            with pytest.raises(ingest.StageRefused) as excinfo:
                ingest.stage_document(str(path), category="Finance", move=False, actor=attacker)
            assert "already in the library" in str(excinfo.value)
            doc.refresh_from_db()
            assert doc.file_hash == sha256(b"original bytes").hexdigest()

    def test_the_owner_may_re_stage_their_own_row(self, tmp_path):
        with posture(POSTURE_ENTERPRISE):
            owner = make_member("owner")
            path = inbox_file(tmp_path, "Finance/quarterly.pdf", b"v1")
            doc, _ = ingest.stage_document(str(path), category="Finance", move=False, actor=owner)
            path.write_bytes(b"v2")
            again, _ = ingest.stage_document(str(path), category="Finance", move=False, actor=owner)
            assert again.id == doc.id

    def test_an_administrator_may_re_stage_anyones_row(self, tmp_path):
        with posture(POSTURE_ENTERPRISE):
            victim, admin = make_member("victim"), make_superuser("admin")
            path = inbox_file(tmp_path, "Finance/quarterly.pdf", b"v1")
            ingest.stage_document(str(path), category="Finance", move=False, actor=victim)
            path.write_bytes(b"v2")
            ingest.stage_document(str(path), category="Finance", move=False, actor=admin)  # no raise

    def test_the_cli_and_notes_job_door_keeps_its_re_index_in_place_semantics(self, tmp_path):
        """`actor=None` is `manage.py ingest` (`ingest.ingest_path`) and
        the notes-consolidation job (`tools.rag.jobs`'s own
        `stage_document` call) -- NEITHER of which is the watcher
        (round-1 review finding 1: the watcher's real actor is
        `SERVICE_PRINCIPAL`, pinned by the test right below). Renamed
        from "the watch folder door" (this module's own first cut wrongly
        pinned that name to `actor=None`). Refused-ness must not reach
        either of these two callers."""
        path = inbox_file(tmp_path, "Finance/quarterly.pdf", b"v1")
        doc, _ = ingest.stage_document(str(path), category="Finance", move=True, actor=None)
        path.write_bytes(b"v2")
        again, _ = ingest.stage_document(str(path), category="Finance", move=True, actor=None)
        assert again.id == doc.id

    def test_the_watch_folder_itself_re_indexes_a_browser_uploaded_row_in_place(self, tmp_path):
        """The watcher's REAL actor (round-1 review finding 1):
        `_IngestEventHandler.poll_once` calls `enqueue_ingest(...,
        actor=SERVICE_PRINCIPAL)`, never `actor=None`. It must keep
        re-indexing a changed file in place even when a MEMBER, not the
        watcher, uploaded the row it is about to overwrite -- an
        operator's own drop-folder workflow must not start failing
        merely because a browser upload happened to land at the same
        path first. Wrapped in `posture(POSTURE_ENTERPRISE)` so this is
        a real test of the exemption, not of `is_admin`'s open-box
        "everyone is an admin" branch."""
        with posture(POSTURE_ENTERPRISE):
            member = make_member("member")
            path = inbox_file(tmp_path, "Finance/quarterly.pdf", b"v1")
            doc, _ = ingest.stage_document(str(path), category="Finance", move=False, actor=member)
            path.write_bytes(b"v2")
            again, _ = ingest.stage_document(
                str(path), category="Finance", move=True, actor=SERVICE_PRINCIPAL)
            assert again.id == doc.id

    def test_entitlement_rows_are_cleared_when_an_admin_re_stages_someone_elses(self, tmp_path):
        """The other half of B-2: `_delete_existing_data` drops chunks
        and files but not labels, and `restamp_document_chunks` then
        restores the victim's labels onto the new bytes. An admin
        takeover is legitimate and still must not inherit a label
        nobody re-applied -- and (round-1 review finding 2) must not
        carry the victim's containment or attribution over either: the
        new bytes are the ADMIN'S, in the universal library, not the
        victim's workstream."""
        with posture(POSTURE_ENTERPRISE):
            victim, admin = make_member("victim"), make_superuser("admin")
            stream = _workstream()
            path = inbox_file(tmp_path, "Finance/quarterly.pdf", b"v1")
            doc, _ = ingest.stage_document(
                str(path), category="Finance", move=False, actor=victim,
                workstream_id=stream.id)
            label_document(doc, entitlement=make_entitlement(name="Finance"))
            path.write_bytes(b"v2")
            again, _ = ingest.stage_document(str(path), category="Finance", move=False, actor=admin)
            again.refresh_from_db()
            assert list(DocumentEntitlement.objects.filter(document_id=doc.id)) == []
            assert again.workstream_id is None
            assert again.scope == Document.Scope.UNIVERSAL
            assert again.owner_kind == admin.kind
            assert again.owner_key == admin.key

    def test_an_entitlement_owner_may_re_stage_but_a_mere_holder_may_not(self, tmp_path):
        """Round-1 review finding 2/3: `may_administer_document` admits
        an entitlement OWNER (`identity.access.owned_entitlement_ids`,
        `role=EntitlementGrant.Role.OWNER`), never a mere HOLDER
        (`role="member"`, the default `grant()` role) -- `may_label_
        document`'s own gate, unchanged by this task. An owner's
        authorized takeover clears the label, the containment, and
        re-stamps the row to the owner, exactly like an admin's."""
        with posture(POSTURE_ENTERPRISE):
            victim = make_member("victim")
            finance = make_entitlement(name="Finance")
            owner_user, holder_user = make_user(), make_user()
            grant(finance, user=owner_user, role="owner")
            grant(finance, user=holder_user, role="member")
            owner, holder = user_principal(owner_user), user_principal(holder_user)
            stream = _workstream()

            path = inbox_file(tmp_path, "Finance/quarterly.pdf", b"v1")
            doc, _ = ingest.stage_document(
                str(path), category="Finance", move=False, actor=victim,
                workstream_id=stream.id)
            label_document(doc, entitlement=finance)

            path.write_bytes(b"v2 from the holder")
            with pytest.raises(ingest.StageRefused):
                ingest.stage_document(str(path), category="Finance", move=False, actor=holder)

            path.write_bytes(b"v2 from the owner")
            again, _ = ingest.stage_document(str(path), category="Finance", move=False, actor=owner)
            again.refresh_from_db()
            assert again.id == doc.id
            assert list(DocumentEntitlement.objects.filter(document_id=doc.id)) == []
            assert again.workstream_id is None
            assert again.scope == Document.Scope.UNIVERSAL
            assert again.owner_kind == owner.kind
            assert again.owner_key == owner.key

    def test_a_byte_identical_reupload_by_a_stranger_is_the_existence_oracle_not_a_takeover(
        self, tmp_path,
    ):
        """Round-1 review minor: the same-hash early return (`existing.
        file_hash == file_hash`) is NOT gated by `_may_restage` at all --
        a stranger who happens to upload content byte-identical to
        somebody else's already-staged file at that path learns the row
        exists (an "unchanged" outcome, `changed=False`) but the row
        itself is never touched: no delete, no restamp, no label
        clearing, no containment clearing, no owner re-stamp. Pinning the
        NON-effect deliberately, so a future change to this branch that
        started mutating `existing` would be caught here."""
        with posture(POSTURE_ENTERPRISE):
            victim, stranger = make_member("victim"), make_member("stranger")
            path = inbox_file(tmp_path, "Finance/quarterly.pdf", b"identical bytes")
            doc, _ = ingest.stage_document(str(path), category="Finance", move=False, actor=victim)
            label_document(doc, entitlement=make_entitlement(name="Finance"))

            same, changed = ingest.stage_document(
                str(path), category="Finance", move=False, actor=stranger)
            assert changed is False
            assert same.id == doc.id
            assert same.owner_kind == victim.kind
            assert same.owner_key == victim.key
            assert list(DocumentEntitlement.objects.filter(document_id=doc.id)) != []
