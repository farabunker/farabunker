"""The artifact-reference vocabulary (spec section 4.5).

ONE DELIBERATE COLUMN CROSSING, and it is the point of the test that makes
it: `test_it_agrees_with_visions_own_parser_on_every_kind` imports
`tools.vision.services`. A test importing across a column is not the
import law's business -- rule 2 governs what PRODUCTION code may import,
and `agents/contracts/artifacts.py` itself imports nothing but the
standard library. The crossing exists precisely so the two parsers cannot
drift: a reference vision MINTED must parse identically here.

SINCE CHAT IMAGE ARTIFACTS (2026-09-16) THE DIRECTION IS REVERSED, AND
THE TEST IS STRONGER FOR IT. `parse_input_reference` no longer keeps its
own copy of the splitting rule -- it DELEGATES to `parse_artifact` -- so
the agreement is true by construction and the test's job is to prove the
delegation is really in place. It covers all THREE kinds now: vision
accepts `document:<id>` as a generation input (an image attached to a
conversation is the thing being edited, or a reference while editing
another), and a `document` reference may carry `mint_artifact`'s own
title suffix, which only one of the two parsers ever knew how to peel.

This module also calls `reverse()`. It overrides `FARABUNKER_FEATURES`
nowhere, so THE VISION-FLAG RULE is satisfied trivially -- both supported
gate states carry "vision", and the two vision routes exist in both.
"""
from __future__ import annotations

import dataclasses

import pytest
from django.urls import reverse

from agents.contracts.artifacts import (
    ARTIFACT_KINDS, ArtifactFile, ArtifactLabels, artifact_title, artifact_url_name,
    file_resolver_for, labels_resolver_for, mint_artifact, parse_artifact,
    register_artifact_file_resolver, register_artifact_labels,
)
from agents.contracts.tests._helpers import isolated_file_resolver_registry, isolated_labels_registry  # noqa: F401


class TestParseArtifact:
    @pytest.mark.parametrize(
        "reference,expected",
        [("output:12", ("output", 12)), ("input:3", ("input", 3)),
         ("document:451", ("document", 451))],
    )
    def test_it_splits_a_valid_reference(self, reference, expected):
        assert parse_artifact(reference) == expected

    def test_a_document_reference_may_carry_a_title_suffix(self):
        """R5 (chat-polish P3.1, fix round 1): `mint_artifact` embeds a
        display title behind a second `:` -- `parse_artifact` still
        returns just `(kind, pk)`, ignoring it."""
        assert parse_artifact("document:451:Attention%20Is%20All") == ("document", 451)

    def test_an_output_reference_with_a_title_shaped_suffix_is_still_refused(self):
        """The title suffix is `document`-only (see the module
        docstring) -- widening every kind at once would quietly accept
        a shape `"output:12:extra"` (the existing refused case, below)
        already proves nothing has ever minted."""
        with pytest.raises(ValueError):
            parse_artifact("output:12:some-title")

    @pytest.mark.parametrize(
        "reference",
        ["", "output", "output:", ":12", "output:abc", "output:-1", "output:1.5",
         "nope:12", "/etc/passwd", "output:12:extra", None],
    )
    def test_it_refuses_anything_else_naming_the_shape(self, reference):
        """This value arrives from a queue payload, a link, or a tool
        call, so it is untrusted input and a clear refusal beats a
        confusing failure later -- the same rule
        `tools/vision/services.py:384-389` already states."""
        with pytest.raises(ValueError) as excinfo:
            parse_artifact(reference)
        assert "output:<id>" in str(excinfo.value)


class TestMintAndTitle:
    """R5 (chat-polish P3.1): `mint_artifact` is the one place a
    `"document:<id>"` reference may grow a display title, and
    `artifact_title` is the one place that reads it back."""

    def test_a_document_with_a_title_round_trips(self):
        reference = mint_artifact("document", 451, "Attention Is All You Need")
        assert parse_artifact(reference) == ("document", 451)
        assert artifact_title(reference) == "Attention Is All You Need"

    def test_a_blank_title_mints_the_bare_reference(self):
        assert mint_artifact("document", 451, "") == "document:451"
        assert mint_artifact("document", 451) == "document:451"

    def test_a_title_containing_a_colon_still_round_trips(self):
        """The whole reason the title is `quote()`d: a colon in the
        title itself must never be mistaken for the id/title separator
        `parse_artifact`'s second `partition(":")` looks for."""
        reference = mint_artifact("document", 7, "Attention: a survey")
        assert parse_artifact(reference) == ("document", 7)
        assert artifact_title(reference) == "Attention: a survey"

    def test_output_and_input_never_carry_a_title_even_if_asked(self):
        """`_TITLED_KINDS` is `document`-only -- an image never needs a
        name, and minting one for output/input would produce a shape
        `parse_artifact` refuses outright for those kinds."""
        assert mint_artifact("output", 12, "ignored") == "output:12"
        assert mint_artifact("input", 3, "ignored") == "input:3"

    def test_artifact_title_is_blank_for_a_reference_with_none(self):
        assert artifact_title("document:451") == ""

    def test_artifact_title_is_blank_for_a_non_document_kind(self):
        assert artifact_title("output:12") == ""

    def test_artifact_title_is_blank_for_a_malformed_reference(self):
        """Degrades to blank rather than raising -- `parse_artifact` is
        the one place a bad reference is a hard failure; a caller
        wanting a display label has already decided to tolerate less."""
        assert artifact_title("not-a-reference") == ""
        assert artifact_title("") == ""
        assert artifact_title(None) == ""

    def test_mint_artifact_refuses_an_unknown_kind(self):
        with pytest.raises(ValueError, match="nope"):
            mint_artifact("nope", 1)


class TestParseArtifactAgreement:
    def test_it_agrees_with_visions_own_parser_on_every_kind(self):
        """A reference that vision MINTED must parse identically here --
        and, since chat image artifacts (2026-09-16), the reverse too:
        `parse_input_reference` DELEGATES to `parse_artifact` for the
        split, so the two cannot drift by construction and this test
        proves the delegation is really in place rather than a second
        copy of the same rule."""
        from tools.vision.services import parse_input_reference

        for reference in ("output:12", "input:3", "document:451"):
            assert parse_artifact(reference) == parse_input_reference(reference)

    def test_a_titled_document_reference_parses_the_same_on_both_sides(self):
        from tools.vision.services import parse_input_reference

        titled = mint_artifact("document", 451, "Attention Is All You Need")
        assert parse_artifact(titled) == parse_input_reference(titled) == ("document", 451)


class TestArtifactUrlName:
    @pytest.mark.parametrize(
        "kind,name",
        [("output", "vision-output-file"), ("input", "vision-input-file"),
         ("document", "rag-document-file")],
    )
    def test_each_kind_names_the_view_that_serves_its_bytes(self, kind, name):
        assert artifact_url_name(kind) == name

    def test_it_refuses_an_unknown_kind(self):
        with pytest.raises(ValueError, match="nope"):
            artifact_url_name("nope")

    def test_every_declared_kind_has_a_url_name(self):
        assert {artifact_url_name(k) for k in ARTIFACT_KINDS} == {
            "vision-output-file", "vision-input-file", "rag-document-file",
        }

    def test_every_url_name_actually_reverses(self):
        """The names are string literals in a pure module, so nothing
        else proves they are real routes. This does.

        No `FARABUNKER_FEATURES` override anywhere in this file, so THE
        VISION-FLAG RULE is satisfied trivially: both supported gate
        states ('vision,media' and 'vision') carry "vision", and the two
        vision routes exist in both.
        """
        assert reverse("vision-output-file", args=[1]).endswith("/outputs/1/file/")
        assert reverse("vision-input-file", args=[1]).endswith("/inputs/1/file/")
        assert reverse("rag-document-file", args=[1]).endswith("/documents/1/file/")


class TestArtifactLabels:
    """The registry `agents/runtime/taint.py::stamp_turn_taint` reads
    from (Task 16, spec §7.4). A rule-1 pure leaf -- a dataclass and a
    dict, no Django -- so it lives beside the vocabulary it keys on
    rather than in a new module."""

    def test_it_refuses_a_kind_that_is_not_an_artifact_kind(self):
        with pytest.raises(ValueError, match="nope"):
            ArtifactLabels("nope", "a.b.c")

    def test_it_refuses_a_resolver_with_no_dotted_path(self):
        with pytest.raises(ValueError, match="dotted path"):
            ArtifactLabels("document", "not_dotted")

    def test_registering_it_makes_the_resolver_findable_by_kind(self, isolated_labels_registry):
        register_artifact_labels(ArtifactLabels("document", "tools.rag.labels.entitlement_ids_for"))
        assert labels_resolver_for("document") == "tools.rag.labels.entitlement_ids_for"

    def test_an_unregistered_kind_answers_none_not_an_error(self, isolated_labels_registry):
        assert labels_resolver_for("output") is None

    def test_registering_the_same_kind_twice_replaces_rather_than_stacks(
            self, isolated_labels_registry):
        """Idempotent, like every sibling registry in this codebase."""
        register_artifact_labels(ArtifactLabels("document", "a.b"))
        register_artifact_labels(ArtifactLabels("document", "c.d"))
        assert labels_resolver_for("document") == "c.d"


class TestArtifactFileResolvers:
    """The SIBLING of `TestArtifactLabels` above, and deliberately the
    same shape: one dict keyed by artifact kind, holding a DOTTED PATH
    resolved at call time by whoever needs the bytes. `tools/vision`
    may not import `tools/rag` and `agents/` may not import either, so
    a tool with a file input asks this registry which function owns the
    kind it was handed, rather than importing the column that owns it."""

    def test_it_refuses_a_kind_that_is_not_an_artifact_kind(self):
        with pytest.raises(ValueError, match="nope"):
            register_artifact_file_resolver("nope", "a.b.c")

    def test_it_refuses_a_resolver_with_no_dotted_path(self):
        with pytest.raises(ValueError, match="dotted path"):
            register_artifact_file_resolver("document", "not_dotted")

    def test_registering_it_makes_the_resolver_findable_by_kind(
            self, isolated_file_resolver_registry):
        register_artifact_file_resolver("document", "tools.rag.access.artifact_file_for")
        assert file_resolver_for("document") == "tools.rag.access.artifact_file_for"

    def test_an_unregistered_kind_answers_none_not_an_error(
            self, isolated_file_resolver_registry):
        assert file_resolver_for("output") is None

    def test_an_unknown_kind_answers_none_rather_than_raising(
            self, isolated_file_resolver_registry):
        """READING is permissive where WRITING is strict: a caller
        holding an unparsed string must be able to ask without first
        proving the kind is real."""
        assert file_resolver_for("nope") is None

    def test_registering_the_same_kind_twice_replaces_rather_than_stacks(
            self, isolated_file_resolver_registry):
        register_artifact_file_resolver("document", "a.b")
        register_artifact_file_resolver("document", "c.d")
        assert file_resolver_for("document") == "c.d"


class TestArtifactFileShape:
    def test_it_carries_a_path_a_name_and_a_media_type(self):
        artifact = ArtifactFile(path="/store/7/photo.png", name="photo.png",
                                media_type="image/png")
        assert artifact.path == "/store/7/photo.png"
        assert artifact.name == "photo.png"
        assert artifact.media_type == "image/png"

    def test_media_type_defaults_to_blank_never_none(self):
        """`""`, not `None`: every consumer does `media_type.startswith
        ("image/")` on it, and a `None` there is an AttributeError at the
        one moment a refusal is being composed."""
        assert ArtifactFile(path="/store/7/x.bin", name="x.bin").media_type == ""

    def test_it_is_frozen(self):
        artifact = ArtifactFile(path="/store/7/photo.png", name="photo.png")
        with pytest.raises(dataclasses.FrozenInstanceError):
            artifact.path = "/etc/passwd"
