"""Unit tests for the known-models catalog (models/contracts/catalog.py).

Pure in-memory data — no DB, no Django, no conftest fixtures required
(other than the settings-link tests below, which read Django settings but
never write to the DB).

The catalog is enrichment data only -- the console renders nothing from
it. `install_cmd`/`source_url`/`notes` and the display helpers
`by_capability`/`CATALOG_CURATED` are REMOVED (their only consumers were
the deleted starter-model cards), and these tests pin that removal.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

import config.settings as settings_module
from models.contracts import catalog as catalog_module
from models.contracts.catalog import CATALOG, CatalogEntry, find
from models.contracts.roles import CAPABILITIES


# --- Catalog structure ---


class TestCatalogStructure:
    def test_catalog_is_list_of_entries(self):
        assert isinstance(CATALOG, list)
        assert all(isinstance(entry, CatalogEntry) for entry in CATALOG)

    def test_catalog_is_not_empty(self):
        assert len(CATALOG) > 0


# --- Display-only surface removed ----------------------------------------


class TestDisplayFieldsRemoved:
    """The starter-model cards are gone; the fields and helpers that
    existed only to feed them must be gone too -- not lingering as dead
    data that would silently age."""

    def test_entry_has_no_install_cmd_source_url_or_notes(self):
        field_names = {field.name for field in dataclasses.fields(CatalogEntry)}
        assert field_names == {
            "name", "engine", "model_id", "capability", "embed_dim", "config", "digest",
            "accepts",
        }

    def test_by_capability_and_catalog_curated_are_gone(self):
        assert not hasattr(catalog_module, "by_capability")
        assert not hasattr(catalog_module, "CATALOG_CURATED")

    def test_accepts_defaults_to_empty_tuple(self):
        """chat-attachments' capability gate (`agents/runtime/prompt.
        py::native_media_types`): every entry that predates the field --
        which is every entry but the two vision ones stamped below --
        must read as text-only, not as an unset/None ambiguity."""
        entry = CatalogEntry(name="T", engine="ollama", model_id="t:1b", capability="chat")
        assert entry.accepts == ()


# --- CatalogEntry validation ---


class TestCatalogEntryValidation:
    def test_all_entries_have_model_id(self):
        for entry in CATALOG:
            assert entry.model_id, "Entry has empty model_id"
            assert isinstance(entry.model_id, str)

    def test_all_entries_have_capability(self):
        for entry in CATALOG:
            assert entry.capability, f"{entry.model_id} has empty capability"
            assert entry.capability in CAPABILITIES

    def test_unknown_capability_rejected(self):
        with pytest.raises(ValueError):
            CatalogEntry(
                name="Bogus",
                engine="ollama",
                model_id="bogus:1b",
                capability="not-a-capability",
            )

    @pytest.mark.parametrize("capability", sorted(CAPABILITIES))
    def test_valid_capabilities_accepted(self, capability):
        entry = CatalogEntry(
            name="Test Model",
            engine="ollama",
            model_id="test:1b",
            capability=capability,
        )
        assert entry.capability == capability


# --- Catalog seeding ---


class TestCatalogSeeding:
    def test_has_chat_models(self):
        chat = [e for e in CATALOG if e.capability == "chat"]
        assert len(chat) >= 3
        model_ids = {e.model_id for e in chat}
        assert "qwen2.5:7b" in model_ids
        assert "llama3.1:8b" in model_ids
        assert "phi3:mini" in model_ids

    def test_has_embedding_models_with_dims(self):
        embeddings = [e for e in CATALOG if e.capability == "embeddings"]
        assert len(embeddings) >= 2

        # Verify nomic-embed-text
        nomic = find("nomic-embed-text")
        assert nomic is not None
        assert nomic.capability == "embeddings"
        assert nomic.embed_dim == 768

        # Verify mxbai-embed-large
        mxbai = find("mxbai-embed-large")
        assert mxbai is not None
        assert mxbai.capability == "embeddings"
        assert mxbai.embed_dim == 1024

    def test_has_vision_models(self):
        vision = [e for e in CATALOG if e.capability == "vision"]
        assert len(vision) >= 2
        model_ids = {e.model_id for e in vision}
        assert "llava:7b" in model_ids
        assert "llama3.2-vision:11b" in model_ids


# --- find ---


class TestFind:
    def test_find_exact_match(self):
        entry = find("qwen2.5:7b")
        assert entry is not None
        assert entry.model_id == "qwen2.5:7b"
        assert entry.name == "Qwen 2.5 7B"

    def test_find_embedding_model(self):
        entry = find("nomic-embed-text")
        assert entry is not None
        assert entry.capability == "embeddings"
        assert entry.embed_dim == 768

    def test_find_vision_model(self):
        entry = find("llava:7b")
        assert entry is not None
        assert entry.capability == "vision"

    def test_find_installed_vision_language_model(self):
        entry = find("qwen2.5vl:7b")
        assert entry is not None
        assert entry.accepts == ("image",)
        assert entry.capability == "vision"

    def test_find_miss_returns_none(self):
        entry = find("does-not-exist:latest")
        assert entry is None

    def test_find_empty_string_returns_none(self):
        entry = find("")
        assert entry is None

    def test_find_is_case_sensitive(self):
        # Model IDs are case-sensitive in ollama
        entry = find("QWEN2.5:7B")
        assert entry is None

    def test_find_all_catalog_entries_are_findable(self):
        for entry in CATALOG:
            found = find(entry.model_id)
            assert found is not None
            assert found is entry  # Same object

    # --- Tag-normalized matching (final-review F1) -------------------------
    # Engines report tagged ids (`nomic-embed-text:latest`) while both
    # embedding catalog entries are bare -- the console's catalog-dimension
    # fallback exists for exactly those models, so `find()` must match a
    # tagged query against a bare entry (and vice versa), while an
    # explicitly tagged entry still only matches its exact tag.

    def test_find_latest_tagged_id_matches_bare_catalog_entry(self):
        entry = find("nomic-embed-text:latest")
        assert entry is not None
        assert entry.model_id == "nomic-embed-text"
        assert entry.embed_dim == 768

    def test_find_latest_tagged_id_matches_the_other_bare_embedding_entry(self):
        entry = find("mxbai-embed-large:latest")
        assert entry is not None
        assert entry.model_id == "mxbai-embed-large"
        assert entry.embed_dim == 1024

    def test_find_explicitly_tagged_entry_still_matches_exactly(self):
        entry = find("llama3.1:8b")
        assert entry is not None
        assert entry.name == "Llama 3.1 8B"

    def test_find_never_falsely_matches_a_different_tag(self):
        assert find("llama3.1:70b") is None
        assert find("phi3:latest") is None  # catalog entry is phi3:mini

    def test_find_bare_id_of_an_explicitly_tagged_entry_does_not_match(self):
        # "llama3.1" normalizes to "llama3.1:latest", not "llama3.1:8b".
        assert find("llama3.1") is None


# --- Settings ship NO baked model defaults --------------------------------
#
# A fresh install must never presume which model to bind to: "default
# shouldn't be in the env files, there should be no default in the env
# files as there isn't a default to setup." So the invariant to pin is that
# `LLM_MODEL`/`EMBED_MODEL`/`EMBED_DIM` have NO fallback value at all, in
# source or at runtime, and any attempt to reintroduce one fails here.


def _settings_source() -> str:
    """config/settings.py's source text.

    Read as SOURCE, not via `django.conf.settings` -- in this test process
    (or CI, or an operator's dev shell) these variables may legitimately be
    set by a real env var, which is an operator's explicit override and says
    nothing about what the file itself bakes in. Only the source can answer
    "does a default ship here?". (Reloading the settings module with a
    scrubbed environment is the other option and is still unsupported:
    Django settings are a lazy singleton configured once per process.)
    """
    return Path(settings_module.__file__).read_text()


class TestSettingsShipNoModelDefaults:
    """No model default anywhere in `config/settings.py` (owner ruling, ADR
    0010 third amendment). `OLLAMA_BASE_URL` deliberately keeps its default
    -- a server *location* is deploy convention, not a model choice."""

    @pytest.mark.parametrize("name", ["LLM_MODEL", "EMBED_MODEL", "EMBED_DIM"])
    def test_no_environ_get_fallback_value_in_source(self, name):
        source = _settings_source()
        pattern = rf'{name}\s*=\s*.*?os\.environ\.get\(\s*"{name}"\s*,'
        assert re.search(pattern, source) is None, (
            f"{name} has an os.environ.get(...) default again in config/settings.py "
            "-- the platform must never presume a model"
        )

    def test_no_catalog_model_name_appears_as_a_settings_fallback(self):
        """Belt-and-braces against a hand-rolled `or "some-model"` fallback:
        no catalog model id may appear anywhere in the settings source."""
        source = _settings_source()
        for entry in CATALOG:
            assert entry.model_id not in source, (
                f"catalog model id {entry.model_id!r} appears in config/settings.py "
                "-- no model name belongs in config as a suggested or fallback value"
            )

    def test_ollama_base_url_keeps_its_location_default(self):
        """The one default the ruling explicitly preserves."""
        assert re.search(
            r'OLLAMA_BASE_URL\s*=\s*os\.environ\.get\(\s*"OLLAMA_BASE_URL"\s*,\s*"[^"]+"\s*\)',
            _settings_source(),
        )

    def test_unset_variables_resolve_to_none_at_runtime(self, monkeypatch):
        """The runtime half of the same invariant, exercised through the
        module's own parsing helpers rather than the already-configured
        singleton (which may carry this machine's real env)."""
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("EMBED_MODEL", raising=False)
        monkeypatch.delenv("EMBED_DIM", raising=False)

        assert settings_module._optional_str("LLM_MODEL") is None
        assert settings_module._optional_str("EMBED_MODEL") is None
        assert settings_module._optional_int("EMBED_DIM") is None

    def test_blank_and_malformed_values_read_as_unset_not_as_a_guess(self, monkeypatch):
        """A blank line left in a .env is not a model choice, and a typo'd
        dimension must never become an invented number -- it reads as unset
        and fails loudly at the point of use instead."""
        monkeypatch.setenv("LLM_MODEL", "   ")
        monkeypatch.setenv("EMBED_DIM", "seven-hundred")

        assert settings_module._optional_str("LLM_MODEL") is None
        assert settings_module._optional_int("EMBED_DIM") is None

    @pytest.mark.parametrize("raw", ["0", "-5", "-1"])
    def test_non_positive_embed_dim_reads_as_unset(self, monkeypatch, raw):
        """A vector width of 0 or less is malformed, not merely odd -- and
        only `None` trips the fail-fast in
        `tools/rag/index.py`. A `0` that survived parsing would sail past
        that guard into `PGVectorStore.from_params(embed_dim=0)` and surface
        as an opaque low-level error instead of the clean, actionable
        "no recorded embedding dimension" one."""
        monkeypatch.setenv("EMBED_DIM", raw)

        assert settings_module._optional_int("EMBED_DIM") is None

    def test_explicitly_set_values_are_read_as_given(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "operator-choice")
        monkeypatch.setenv("EMBED_DIM", "1024")

        assert settings_module._optional_str("LLM_MODEL") == "operator-choice"
        assert settings_module._optional_int("EMBED_DIM") == 1024
