"""`document:<id>` as a generation input (chat image artifacts,
2026-09-16).

THE COLUMN RULE THIS MODULE EXISTS TO KEEP HONEST: `tools/vision` may
not import `tools/rag`, in production OR here. Every test below
registers `tools.vision.tests._helpers.fake_document_resolver` -- this
column's OWN stand-in for whatever column owns the `document` kind on a
given box -- through `agents.contracts.artifacts.register_artifact_file_
resolver`, exactly the door production uses. The real resolver's own
contract (missing/file-less/invisible all raise `LookupError`) is pinned
where it lives, `tools/rag/tests/test_access_documents.py::
TestArtifactFileFor`.

THE TWO REFUSAL SENTENCES ARE BYTE-STABLE, and asserted with `==`, not
`in`: a model reads them to decide what to try next, and neither may
ever carry a document's TITLE or FILENAME -- an operator-facing string
built out of somebody's uploaded filename is how a filename becomes a
prompt.

THE THREE AUTOUSE FIXTURES BELOW ARE COPIED FROM `test_services.py:
49-71`, not inherited: this repo has no `conftest.py` anywhere (house
rule), so a fixture is only active in a module that defines it or
imports its BODY by name. `clear_bindings` (migration 0002 may seed a
connection/binding pair from an operator's environment, and
`TestASubmissionCopiesTheDocumentsBytes` assumes it owns the registry),
`probe_cache.invalidate()` (`_health_check` caches reachability for 30
seconds keyed on (engine, endpoint), and every vision test module binds
the SAME literal stub endpoint) and `reset_engine_caches()` (the
adapter's own per-endpoint memos) are each exactly as load-bearing here
as they are there.
"""
from __future__ import annotations

import pytest

from agents.contracts.artifacts import ArtifactFile
from identity.contracts.principals import OPEN_PRINCIPAL
from models.contracts.operations import EDIT
from tools.vision import probe_cache, services
from tools.vision.tests._helpers import (  # noqa: F401 -- fixtures, requested by name
    clear_bindings, document_resolver, no_document_resolver, reset_engine_caches,
    stub_generate_binding,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    probe_cache.invalidate()
    yield
    probe_cache.invalidate()


@pytest.fixture(autouse=True)
def _reset_engine_caches():
    reset_engine_caches()
    yield
    reset_engine_caches()


def _stored(tmp_path, name="photo.png", media_type="image/png", body=PNG_MAGIC):
    path = tmp_path / name
    path.write_bytes(body)
    return ArtifactFile(path=str(path), name=name, media_type=media_type)


class TestParsingTheThirdKind:
    def test_it_parses_a_document_reference(self):
        assert services.parse_input_reference("document:451") == ("document", 451)

    def test_it_parses_a_document_reference_carrying_a_title_suffix(self):
        """`agents.contracts.artifacts.mint_artifact` embeds a display
        title behind a SECOND colon for the `document` kind, and the
        attachments provider mints with one -- so the string a model is
        handed can carry it. Peeled off here, exactly as
        `parse_artifact` peels it, because this parser DELEGATES to that
        one rather than keeping a second copy of the rule."""
        assert services.parse_input_reference(
            "document:451:Attention%20Is%20All") == ("document", 451)

    def test_an_output_reference_with_a_third_segment_is_still_refused(self):
        """UNCHANGED: the title suffix is `document`-only."""
        with pytest.raises(ValueError):
            services.parse_input_reference("output:12:some-title")

    def test_the_shape_sentence_names_all_three_kinds(self):
        with pytest.raises(ValueError) as caught:
            services.parse_input_reference("nonsense")
        assert "output:<id>" in str(caught.value)
        assert "input:<id>" in str(caught.value)
        assert "document:<id>" in str(caught.value)

    def test_the_kind_tuple_is_exactly_three(self):
        assert services.INPUT_REFERENCE_KINDS == ("output", "input", "document")


@pytest.mark.django_db
class TestStoredInputResolvesADocument:
    def test_it_returns_the_documents_bytes_as_an_upload_shaped_file(
            self, tmp_path, document_resolver):
        document_resolver[42] = _stored(tmp_path)
        stored = services.stored_input("document:42", OPEN_PRINCIPAL)
        assert b"".join(stored.chunks()) == PNG_MAGIC
        assert stored.name == "photo.png"
        assert stored.content_type == "image/png"

    def test_a_titled_reference_resolves_the_same_row(self, tmp_path, document_resolver):
        document_resolver[42] = _stored(tmp_path)
        stored = services.stored_input("document:42:photo.png", OPEN_PRINCIPAL)
        assert b"".join(stored.chunks()) == PNG_MAGIC

    def test_a_lookup_error_becomes_the_standard_dead_reference_sentence(
            self, document_resolver):
        """THE SENTENCE IS THE ONE AN `output:` REFERENCE ALREADY GETS,
        VERBATIM -- a model that learned to recover from one recovers
        from the other with no new vocabulary."""
        with pytest.raises(ValueError) as caught:
            services.stored_input("document:42", OPEN_PRINCIPAL)
        assert str(caught.value) == "document:42 does not name a stored image."

    def test_the_dead_reference_sentence_never_carries_the_title(self, document_resolver):
        """A reference the caller handed us may carry a percent-encoded
        FILENAME. It must not come back out in an operator-facing
        string: the bare `document:<id>` form is what is echoed."""
        with pytest.raises(ValueError) as caught:
            services.stored_input("document:42:Confidential%20salaries.png", OPEN_PRINCIPAL)
        assert str(caught.value) == "document:42 does not name a stored image."

    def test_no_registered_resolver_refuses_with_the_same_sentence(
            self, no_document_resolver):
        """The document column is not installed on this box, so the
        reference cannot be live -- and that is indistinguishable, from
        here, from a row that is gone."""
        with pytest.raises(ValueError) as caught:
            services.stored_input("document:42", OPEN_PRINCIPAL)
        assert str(caught.value) == "document:42 does not name a stored image."

    def test_a_non_image_is_refused_by_name_byte_for_byte(self, tmp_path, document_resolver):
        document_resolver[12] = _stored(
            tmp_path, name="contract.pdf", media_type="application/pdf", body=b"%PDF-1.4")
        with pytest.raises(ValueError) as caught:
            services.stored_input("document:12", OPEN_PRINCIPAL)
        assert str(caught.value) == "document:12 is application/pdf, not an image."

    def test_the_non_image_refusal_never_carries_the_filename(
            self, tmp_path, document_resolver):
        document_resolver[12] = _stored(
            tmp_path, name="Q3-salaries-confidential.pdf",
            media_type="application/pdf", body=b"%PDF-1.4")
        with pytest.raises(ValueError) as caught:
            services.stored_input("document:12:Q3-salaries-confidential.pdf",
                                  OPEN_PRINCIPAL)
        assert str(caught.value) == "document:12 is application/pdf, not an image."
        assert "salaries" not in str(caught.value)

    def test_an_unknown_media_type_is_refused_without_inventing_one(
            self, tmp_path, document_resolver):
        document_resolver[12] = _stored(
            tmp_path, name="blob.bin", media_type="", body=b"\x00\x01")
        with pytest.raises(ValueError) as caught:
            services.stored_input("document:12", OPEN_PRINCIPAL)
        assert str(caught.value) == "document:12 is a file of unknown type, not an image."

    def test_a_file_that_vanished_between_resolve_and_read_says_so(
            self, tmp_path, document_resolver):
        """The resolver's own contract already refuses a file-less row --
        this is the RACE behind it (the row was deleted after the
        resolver looked and before this read), answered with the same
        sentence an `output:` reference gets for the same condition."""
        artifact = _stored(tmp_path)
        document_resolver[42] = artifact
        (tmp_path / "photo.png").unlink()
        with pytest.raises(ValueError) as caught:
            services.stored_input("document:42", OPEN_PRINCIPAL)
        assert str(caught.value) == "The file for document:42 is no longer on disk."


@pytest.mark.django_db
class TestTheThirdKindNeverLeaksIntoVisionsOwnRESOLVERS:
    """REVIEW ROUND 1, BLOCKER 1. Widening `parse_input_reference` alone
    would have re-pointed three OTHER resolvers at the wrong table:
    `_referenced_row` and `_visible_referenced_row` both do
    `GeneratedOutput if kind == "output" else JobInput`, so
    `document:42` would have resolved `JobInput` #42 -- a row that has
    nothing to do with document 42 and belongs to somebody else's job.

    THE THREE REAL CONSEQUENCES, each pinned below:
    - `stored_input_exists("document:42", ...)` would answer True off a
      foreign `JobInput`, and the create page would render
      `/vision/inputs/42/file/` as a thumbnail for it (`views.py::
      _stored_input_context`'s own `url_name` line).
    - `discard_staged_inputs` would DELETE staged `JobInput` #42, with
      its file, when an enqueue carrying `input_x=document:42` failed --
      destroying somebody else's staged upload.
    - `stored_input` would hand a generation the wrong bytes entirely.

    Closed by a KIND GUARD in both resolvers (`return None` for anything
    that is not `output`/`input`) plus a `continue` in
    `_stored_input_context`. The `document` kind has exactly one door,
    `_stored_document_input`, and these tests are what keeps that true.
    """

    def test_a_document_reference_never_resolves_a_job_input_row(
            self, tmp_path, document_resolver):
        from tools.vision.models import JobInput
        from tools.vision.tests._helpers import stored_output

        output = stored_output(tmp_path)
        source = tmp_path / "someone-elses-upload.png"
        source.write_bytes(PNG_MAGIC)
        foreign = JobInput.objects.create(
            job=output.job, param_key="init_image", path=str(source),
            media_type="image/png")

        # The fake resolver knows nothing about this pk -- so if the
        # document branch were bypassed and the pk fell through to
        # `JobInput`, THIS is the row it would find.
        assert services._visible_referenced_row(
            f"document:{foreign.pk}", OPEN_PRINCIPAL) is None
        assert services._referenced_row(f"document:{foreign.pk}") is None

    def test_stored_input_exists_is_false_for_a_document_reference(
            self, tmp_path, document_resolver):
        """The create page's own existence check runs through
        `_visible_referenced_row` too -- and it must never confirm a
        `document:` reference off a `JobInput` that happens to share the
        number, which would be an existence oracle AND a wrong preview."""
        from tools.vision.models import JobInput
        from tools.vision.tests._helpers import stored_output

        output = stored_output(tmp_path)
        source = tmp_path / "someone-elses-upload.png"
        source.write_bytes(PNG_MAGIC)
        foreign = JobInput.objects.create(
            job=output.job, param_key="init_image", path=str(source),
            media_type="image/png")
        assert services.stored_input_exists(
            f"document:{foreign.pk}", OPEN_PRINCIPAL) is False

    def test_discard_staged_inputs_never_deletes_on_a_document_reference(
            self, tmp_path, document_resolver):
        """THE DESTRUCTIVE ONE. A failed enqueue calls this with every
        reference the submission carried; a `document:` reference among
        them must be skipped in silence, exactly as a gallery output or
        a malformed string already is -- never matched onto a staged
        `JobInput` that shares its number and deleted with its file."""
        from pathlib import Path

        from tools.vision.models import JobInput

        source = tmp_path / "staged.png"
        source.write_bytes(PNG_MAGIC)
        staged = JobInput.objects.create(
            job=None, param_key="init_image", path=str(source),
            media_type="image/png")

        services.discard_staged_inputs([f"document:{staged.pk}"])

        assert JobInput.objects.filter(pk=staged.pk).exists()
        assert Path(source).is_file()

    def test_the_create_page_drops_a_document_reference_rather_than_previewing_it(
            self, document_resolver):
        """`views._stored_input_context` builds a preview URL from the
        kind (`vision-output-file` or `vision-input-file`) -- there is no
        third branch, and inventing one is out of scope: the create page
        is not where an attached document is fed in. Dropped silently,
        the same way a stale or foreign reference already is."""
        from tools.vision import views

        shown = views._stored_input_context(
            {"init_image": "document:42"}, EDIT, OPEN_PRINCIPAL)
        assert shown == []


@pytest.mark.django_db
class TestResolveInputsEndToEnd:
    """BOTH SLOTS, in one call -- the owner's own second outcome ("use
    it to modify another image"). `EDIT` declares `init_image` (its
    FIRST file param, the one the tool's shared `image` key collapses
    onto) and `reference_image`."""

    def test_a_document_in_both_the_first_and_second_file_slots(
            self, tmp_path, document_resolver):
        document_resolver[42] = _stored(tmp_path, name="subject.png")
        document_resolver[43] = _stored(tmp_path, name="style.png", body=b"\x89PNG\r\n\x1a\nSTYLE")
        files = services.resolve_inputs(
            EDIT,
            {"init_image": "document:42", "reference_image": "document:43"},
            OPEN_PRINCIPAL,
        )
        assert sorted(files) == ["init_image", "reference_image"]
        assert files["init_image"].name == "subject.png"
        assert b"".join(files["reference_image"].chunks()).endswith(b"STYLE")

    def test_a_document_and_a_stored_output_mix_freely_in_one_call(
            self, tmp_path, document_resolver):
        from tools.vision.tests._helpers import PNG, stored_output

        output = stored_output(tmp_path)
        document_resolver[43] = _stored(tmp_path, name="style.png")
        files = services.resolve_inputs(
            EDIT,
            {"init_image": f"output:{output.id}", "reference_image": "document:43"},
            OPEN_PRINCIPAL,
        )
        assert b"".join(files["init_image"].chunks()) == PNG
        assert files["reference_image"].name == "style.png"

    def test_a_refused_document_names_the_param_it_came_from(self, document_resolver):
        with pytest.raises(services.InputReferenceError) as caught:
            services.resolve_inputs(
                EDIT, {"reference_image": "document:42"}, OPEN_PRINCIPAL)
        assert caught.value.param_key == "reference_image"
        assert str(caught.value) == "document:42 does not name a stored image."


@pytest.mark.django_db
class TestASubmissionCopiesTheDocumentsBytes:
    """THE DURABILITY CLAIM (spec §2): the job gets its OWN copy under
    its OWN directory at submit time, so detaching or deleting the
    document afterwards never affects a queued, running or finished
    job."""

    def test_an_edit_submission_writes_the_bytes_into_the_jobs_input_folder(
            self, tmp_path, document_resolver, stub_generate_binding, settings):
        from pathlib import Path

        from tools.vision.models import JobInput

        settings.GENERATED_DIR = tmp_path / "generated"
        document_resolver[42] = _stored(tmp_path, name="subject.png")

        files = services.resolve_inputs(EDIT, {"init_image": "document:42"}, OPEN_PRINCIPAL)
        job = services.submit_job(
            "edit", {"instruction": "put a red hat on it", "guidance": 4.0},
            files=files, actor=OPEN_PRINCIPAL,
        )

        row = JobInput.objects.get(job=job, param_key="init_image")
        copied = Path(row.path)
        assert copied.is_file()
        assert copied.read_bytes() == PNG_MAGIC
        assert str(settings.GENERATED_DIR / str(job.id)) in str(copied)

        # The document goes away; the job's own copy does not.
        (tmp_path / "subject.png").unlink()
        document_resolver.clear()
        assert copied.read_bytes() == PNG_MAGIC


class TestTheSchemaNamesTheThirdKind:
    """The ONLY places in this column that spell the reference shape for
    a model are these three (verified by grep over `tools/vision` for
    `output:<id>`): `_IMAGE_PARAM`, `_file_ref_param`, and
    `_narrowed_image_description`. `vision.operations`'s catalog inherits
    its text from the operations' own `Param` descriptions and never
    spells a reference shape of its own, so there is nothing else to
    widen."""

    def test_every_file_reference_param_on_the_generate_spec_names_it(self):
        from models.contracts.operations import all_operations
        from tools.vision.tools import build_generate_spec

        spec = build_generate_spec()
        file_ref_keys = {"image"} | {
            param.key
            for operation in all_operations()
            for param in operation.file_params()[1:]
        }
        named = [p for p in spec.params if p.key in file_ref_keys]
        assert named, "the generate spec declares no file-reference params at all"
        for param in named:
            assert "document:<id>" in param.description, param.key
            assert "output:<id>" in param.description, param.key

    def test_the_narrowed_image_description_names_it_too(self):
        from models.contracts.operations import EDIT, TXT2IMG
        from tools.vision.tools import _narrowed_image_description

        sentence = _narrowed_image_description([EDIT, TXT2IMG])
        assert "document:<id>" in sentence
        assert "an image attached to this conversation" in sentence
