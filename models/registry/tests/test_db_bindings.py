"""Unit tests for models/registry/bindings.py (db_provider) plus an
end-to-end check of the db -> env fallback chain implemented by
models.contracts.bindings.resolve().

`@pytest.mark.django_db` at class level (repo convention); no conftest.py.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import OperationalError
from django.test import override_settings

from models.registry.access import UNRESTRICTED_MODEL_ACCESS
from models.registry.bindings import (
    answer_role_primary,
    chat_connections_for_picker,
    connections_for_picker,
    db_provider,
    footprint_for,
    record_measured_footprint,
    resolve_connection,
    resolve_connection_named,
    role_primary,
)
from models.registry.models import ModelConnection, RoleBinding
from models.registry.tests._helpers import clear_seeded_rows
from models.contracts.bindings import ResolvedModel, resolve


@pytest.fixture(autouse=True)
def _clear_seeded_rows(db):
    """These tests exercise `db_provider`/`resolve()` against rows they
    create themselves, including asserting "no binding" behavior -- but
    migration 0002 seeds `rag.answer`/`rag.embed` RoleBindings at
    migrate time, and pytest-django applies migrations before any test
    runs. Clear the seeded rows first so this file's "empty table"
    assumptions still hold; seeding itself is covered by test_seed.py.
    """
    clear_seeded_rows()


@pytest.mark.django_db
class TestDbProviderNoRows:
    def test_no_binding_rows_returns_none(self):
        assert db_provider("rag.answer") is None

    def test_binding_with_no_connection_returns_none(self):
        # connection is nullable (SET_NULL) -- an unbound RoleBinding row
        # must not be mistaken for a resolved binding.
        RoleBinding.objects.create(role_key="rag.answer", connection=None)

        assert db_provider("rag.answer") is None

    def test_table_not_migrated_yet_returns_none(self):
        """`ProgrammingError`/`OperationalError` from the underlying table
        not existing yet (mid-`migrate`) must read as "no opinion", not
        raise -- resolve() then falls back to env_provider."""
        with patch("models.registry.models.RoleBinding.objects") as mock_objects:
            mock_objects.select_related.return_value.filter.return_value.first.side_effect = (
                OperationalError('relation "inference_rolebinding" does not exist')
            )

            assert db_provider("rag.answer") is None


@pytest.mark.django_db
class TestDbProviderResolvedBinding:
    def test_returns_resolved_model_from_bound_connection(self):
        connection = ModelConnection.objects.create(
            name="workstation llama",
            engine="ollama",
            endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b",
            capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        resolved = db_provider("rag.answer")

        assert resolved == ResolvedModel(
            "ollama", "llama3.1:8b", "http://ollama.local:11434"
        )

    def test_returns_embed_dim_from_bound_connection(self):
        connection = ModelConnection.objects.create(
            name="workstation embed",
            engine="ollama",
            endpoint="http://ollama.local:11434",
            model_id="nomic-embed-text",
            capabilities=["embeddings"],
            embed_dim=768,
        )
        RoleBinding.objects.create(role_key="rag.embed", connection=connection)

        resolved = db_provider("rag.embed")

        assert resolved.embed_dim == 768

    def test_role_key_lookup_is_case_insensitive(self):
        connection = ModelConnection.objects.create(
            name="workstation llama",
            engine="ollama",
            endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b",
        )
        RoleBinding.objects.create(role_key="RAG.Answer", connection=connection)

        assert db_provider("rag.answer") is not None

    def test_unrelated_role_key_returns_none(self):
        connection = ModelConnection.objects.create(
            name="workstation llama",
            engine="ollama",
            endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b",
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        assert db_provider("does.not.exist") is None

    def test_context_window_threads_into_config_when_set(self):
        """A per-connection `context_window` override (this task, the
        qwen3:30b-rag incident) rides through in `ResolvedModel.config`,
        which the gateway spreads straight into `build_llm`'s cfg."""
        connection = ModelConnection.objects.create(
            name="workstation llama",
            engine="ollama",
            endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b",
            context_window=16384,
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        resolved = db_provider("rag.answer")

        assert resolved.config == {"context_window": 16384}

    def test_unset_context_window_leaves_config_empty(self):
        """No opinion, not a guessed value -- `build_llm`'s own
        `DEFAULT_CONTEXT_WINDOW` applies via `cfg.setdefault`."""
        connection = ModelConnection.objects.create(
            name="workstation llama",
            engine="ollama",
            endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b",
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        resolved = db_provider("rag.answer")

        assert resolved.config == {}


@pytest.mark.django_db
class TestResolveEndToEnd:
    """Integration of resolve() with db_provider, wired via
    settings.INFERENCE_BINDING_PROVIDER."""

    @override_settings(
        INFERENCE_BINDING_PROVIDER="models.registry.bindings.db_provider",
        LLM_MODEL="llama3.1:8b",
        OLLAMA_BASE_URL="http://ollama.local:11434",
    )
    def test_resolve_falls_back_to_env_when_binding_table_is_empty(self):
        resolved = resolve("rag.answer")

        assert resolved == ResolvedModel(
            "ollama", "llama3.1:8b", "http://ollama.local:11434"
        )

    @override_settings(INFERENCE_BINDING_PROVIDER="models.registry.bindings.db_provider")
    def test_resolve_prefers_db_binding_over_env_when_present(self):
        connection = ModelConnection.objects.create(
            name="workstation llama",
            engine="ollama",
            endpoint="http://db-bound.local:11434",
            model_id="db-bound-model",
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        resolved = resolve("rag.answer")

        assert resolved == ResolvedModel(
            "ollama", "db-bound-model", "http://db-bound.local:11434"
        )

    @override_settings(INFERENCE_BINDING_PROVIDER="models.registry.bindings.db_provider")
    def test_registered_context_window_reaches_the_built_llm(self):
        """Full round-trip (the qwen3:30b-rag incident): a connection's
        stored `context_window` -> `resolve()` ->
        `models.contracts.gateway.get_llm()` -> the real, unmocked
        `Ollama` object's own `context_window` attribute. No network --
        `Ollama.__init__` does not contact the server."""
        from models.contracts.gateway import get_llm

        connection = ModelConnection.objects.create(
            name="capped chat model",
            engine="ollama",
            endpoint="http://ollama.local:11434",
            model_id="some-chat-model",
            context_window=16384,
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        llm = get_llm("rag.answer")

        assert llm.context_window == 16384


@pytest.mark.django_db
class TestResolveConnection:
    """Unit tests for `resolve_connection` -- the pk-addressed twin of
    `db_provider`, for a caller-supplied override (the Ask-time model
    picker) rather than a role binding."""

    def test_parity_with_db_provider_for_the_same_connection(self):
        """Both providers must build field-for-field identical
        `ResolvedModel`s for the same connection -- there is only one
        construction path (`resolved_from_connection`)."""
        connection = ModelConnection.objects.create(
            name="workstation llama",
            engine="ollama",
            endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b",
            capabilities=["chat"],
            embed_dim=768,
            context_window=16384,
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        via_role = db_provider("rag.answer")
        via_pk = resolve_connection(connection.pk, "chat", access=UNRESTRICTED_MODEL_ACCESS)

        assert via_pk == via_role

    def test_context_window_present_only_when_set(self):
        connection = ModelConnection.objects.create(
            name="uncapped llama",
            engine="ollama",
            endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b",
            capabilities=["chat"],
        )

        resolved = resolve_connection(connection.pk, "chat", access=UNRESTRICTED_MODEL_ACCESS)

        assert resolved.config == {}

    def test_missing_pk_raises_value_error(self):
        with pytest.raises(ValueError, match=r"99999"):
            resolve_connection(99999, "chat", access=UNRESTRICTED_MODEL_ACCESS)

    def test_wrong_capability_raises_value_error(self):
        connection = ModelConnection.objects.create(
            name="embed only",
            engine="ollama",
            endpoint="http://ollama.local:11434",
            model_id="nomic-embed-text",
            capabilities=["embeddings"],
        )

        with pytest.raises(ValueError, match="chat"):
            resolve_connection(connection.pk, "chat", access=UNRESTRICTED_MODEL_ACCESS)

    def test_unsupported_engine_raises_value_error(self):
        connection = ModelConnection.objects.create(
            name="future engine",
            engine="vllm-not-yet-registered",
            endpoint="http://somewhere:9000",
            model_id="some-model",
            capabilities=["chat"],
        )

        with pytest.raises(ValueError, match="vllm-not-yet-registered"):
            resolve_connection(connection.pk, "chat", access=UNRESTRICTED_MODEL_ACCESS)


# --- chat_connections_for_picker / answer_role_primary / resolve_connection_named --
#
# The Ask-time model picker reads (never resolves through)
# `chat_connections_for_picker`/`answer_role_primary` -- the picker's option
# list and which option is "(primary)". `resolve_connection_named` IS a
# resolution: it is what the override path itself resolves through, and it
# doubles as the source of the "answered by" name.


@pytest.mark.django_db
class TestChatConnectionsForPicker:
    def test_only_chat_capable_connections_are_returned(self):
        chat = ModelConnection.objects.create(
            name="chat conn", engine="ollama", endpoint="http://e:1", model_id="m",
            capabilities=["chat"],
        )
        ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint="http://e:1", model_id="m2",
            capabilities=["embeddings"], embed_dim=768,
        )

        result = chat_connections_for_picker()

        assert [c.pk for c in result] == [chat.pk]

    def test_empty_registry_returns_empty_list(self):
        assert chat_connections_for_picker() == []

    def test_multi_capability_connection_with_chat_is_included(self):
        """T9 review coverage gap: a connection registered with MORE than
        one capability (`["chat", "vision"]`, e.g. a multi-capability model
        registration) is still picked up by the `"chat" in
        connection.capabilities` filter -- this helper was never a
        capabilities-list-equals-["chat"] check, but nothing previously
        exercised a connection carrying a second capability alongside
        "chat"."""
        multi = ModelConnection.objects.create(
            name="multi-capability conn", engine="ollama", endpoint="http://e:1", model_id="m",
            capabilities=["chat", "vision"],
        )

        result = chat_connections_for_picker()

        assert [c.pk for c in result] == [multi.pk]

    def test_reuses_picker_order_rank_then_name(self):
        """Not a re-derivation of the ordering rule -- just proof this
        helper doesn't bypass `picker_order()`."""
        b = ModelConnection.objects.create(
            name="b conn", engine="ollama", endpoint="http://e:1", model_id="m",
            capabilities=["chat"], rank=2,
        )
        a = ModelConnection.objects.create(
            name="a conn", engine="ollama", endpoint="http://e:1", model_id="m",
            capabilities=["chat"], rank=1,
        )
        unranked = ModelConnection.objects.create(
            name="unranked conn", engine="ollama", endpoint="http://e:1", model_id="m",
            capabilities=["chat"],
        )

        result = chat_connections_for_picker()

        assert [c.pk for c in result] == [a.pk, b.pk, unranked.pk]


@pytest.mark.django_db
class TestAnswerRolePrimary:
    def test_nothing_answers_the_role_returns_blank(self):
        assert answer_role_primary() == ("", None)

    def test_bound_connection_returns_its_name_and_pk(self):
        connection = ModelConnection.objects.create(
            name="workstation llama", engine="ollama", endpoint="http://e:1",
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        assert answer_role_primary() == ("workstation llama", connection.pk)

    @override_settings(LLM_MODEL="llama3.1:8b", OLLAMA_BASE_URL="http://ollama.local:11434")
    def test_env_override_with_no_bound_connection_returns_labeled_model_id(self):
        name, pk = answer_role_primary()

        assert name == "llama3.1:8b (environment override)"
        assert pk is None

    @override_settings(LLM_MODEL="llama3.1:8b", OLLAMA_BASE_URL="http://ollama.local:11434")
    def test_bound_connection_takes_precedence_over_env_override(self):
        connection = ModelConnection.objects.create(
            name="workstation llama", engine="ollama", endpoint="http://e:1",
            model_id="a-different-model", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        assert answer_role_primary() == ("workstation llama", connection.pk)


@pytest.mark.django_db
class TestResolveConnectionNamed:
    """`resolve_connection_named` -- the pk-addressed resolution paired
    with the connection's display name in one query."""

    def test_returns_resolved_and_name_for_an_existing_pk(self):
        connection = ModelConnection.objects.create(
            name="workstation llama", engine="ollama", endpoint="http://e:1", model_id="m",
            capabilities=["chat"],
        )

        resolved, name = resolve_connection_named(connection.pk, "chat",
                                                  access=UNRESTRICTED_MODEL_ACCESS)

        assert name == "workstation llama"
        assert resolved == resolve_connection(connection.pk, "chat",
                                              access=UNRESTRICTED_MODEL_ACCESS)

    def test_missing_pk_raises_value_error(self):
        with pytest.raises(ValueError, match=r"99999"):
            resolve_connection_named(99999, "chat", access=UNRESTRICTED_MODEL_ACCESS)

    def test_wrong_capability_raises_value_error(self):
        connection = ModelConnection.objects.create(
            name="embed only", engine="ollama", endpoint="http://e:1", model_id="m",
            capabilities=["embeddings"],
        )

        with pytest.raises(ValueError, match="chat"):
            resolve_connection_named(connection.pk, "chat", access=UNRESTRICTED_MODEL_ACCESS)


@pytest.mark.django_db
class TestConnectionConfigRoundTrip:
    """D8: `ResolvedModel.config` already flowed into engine builders, but
    the DB row could not carry it. A multi-file family (Flux/SD3: UNet +
    CLIPs + VAE) needs it without a later schema change."""

    def test_config_reaches_the_resolved_model(self):
        connection = ModelConnection.objects.create(
            name="comfy sdxl", engine="comfyui", endpoint="http://localhost:8188",
            model_id="sdxl.safetensors", capabilities=["image-generation"],
            config={"vae": "sdxl_vae.safetensors"},
        )
        RoleBinding.objects.create(role_key="vision.generate", connection=connection)

        resolved = db_provider("vision.generate")

        assert resolved.config == {"vae": "sdxl_vae.safetensors"}

    def test_null_config_resolves_to_an_empty_dict(self):
        connection = ModelConnection.objects.create(
            name="plain", engine="ollama", endpoint="http://localhost:11434",
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        assert db_provider("rag.answer").config == {}

    def test_context_window_wins_over_a_same_named_config_key(self):
        """The dedicated column is authoritative: `config` seeds the dict,
        `context_window` is applied after it (see
        `models.registry.bindings.resolved_from_connection`)."""
        connection = ModelConnection.objects.create(
            name="both", engine="ollama", endpoint="http://localhost:11434",
            model_id="llama3.1:8b", capabilities=["chat"],
            config={"context_window": 111, "keep_alive": "5m"},
            context_window=4096,
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        assert db_provider("rag.answer").config == {
            "context_window": 4096,
            "keep_alive": "5m",
        }


# --- ModelConnection.effective_footprint_bytes / footprint_source (T2b) ---


@pytest.mark.django_db
class TestFootprintPrecedence:
    def test_neither_fact_set_is_none_and_no_source(self):
        connection = ModelConnection.objects.create(
            name="bare", engine="ollama", endpoint="http://e:1", model_id="m",
        )

        assert connection.effective_footprint_bytes is None
        assert connection.footprint_source is None

    def test_measured_only_is_measured_sourced(self):
        connection = ModelConnection.objects.create(
            name="measured", engine="ollama", endpoint="http://e:1", model_id="m",
            measured_footprint_bytes=123,
        )

        assert connection.effective_footprint_bytes == 123
        assert connection.footprint_source == "measured"

    def test_override_only_is_override_sourced(self):
        connection = ModelConnection.objects.create(
            name="overridden", engine="ollama", endpoint="http://e:1", model_id="m",
            footprint_override_bytes=456,
        )

        assert connection.effective_footprint_bytes == 456
        assert connection.footprint_source == "override"

    def test_override_wins_over_measured_when_both_set(self):
        """Owner ruling: the operator's declared value always wins,
        regardless of what was last measured."""
        connection = ModelConnection.objects.create(
            name="both set", engine="ollama", endpoint="http://e:1", model_id="m",
            measured_footprint_bytes=123,
            footprint_override_bytes=456,
        )

        assert connection.effective_footprint_bytes == 456
        assert connection.footprint_source == "override"

    def test_footprint_source_keys_match_the_footprint_label_vocabulary(self):
        """`footprint_source`'s three non-None values are exactly the keys
        of `_FOOTPRINT_SOURCE_LABELS` -- the footprint vocabulary, NOT
        `_SOURCE_LABELS`, which stays the capability/embed-dim disclosure's
        own map and is untouched by this track (spec §3.1).

        RE-PINNED by the queue memory-governance track: this used to
        assert `"connection"`/`"engine"` against `_SOURCE_LABELS`."""
        from models.registry.views import _FOOTPRINT_SOURCE_LABELS

        assert set(_FOOTPRINT_SOURCE_LABELS) >= {"override", "measured", "engine_reported"}

    def test_footprint_override_gb_round_trips(self):
        """8.5 GB stored as bytes reads back as exactly "8.5" -- the same
        round trip `connection_add`/`_connection_edit.html` rely on."""
        connection = ModelConnection.objects.create(
            name="gb round trip", engine="ollama", endpoint="http://e:1", model_id="m",
            footprint_override_bytes=round(8.5 * 1024**3),
        )

        assert connection.footprint_override_gb == "8.5"

    def test_footprint_override_gb_blank_when_unset(self):
        connection = ModelConnection.objects.create(
            name="unset", engine="ollama", endpoint="http://e:1", model_id="m",
        )

        assert connection.footprint_override_gb == ""


# --- footprint_for (T2b: the scheduler's one footprint lookup) ------------


@pytest.mark.django_db
class TestFootprintFor:
    def test_exact_match_returns_effective_footprint(self):
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b", footprint_override_bytes=999,
        )

        assert footprint_for("ollama", "http://ollama.local:11434", "llama3.1:8b") == 999

    def test_match_with_neither_fact_set_returns_none(self):
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b",
        )

        assert footprint_for("ollama", "http://ollama.local:11434", "llama3.1:8b") is None

    def test_endpoint_normalization_trailing_slash_on_stored_value(self):
        """A connection stored WITH a trailing slash still matches a
        lookup endpoint without one."""
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint="http://ollama.local:11434/",
            model_id="llama3.1:8b", measured_footprint_bytes=42,
        )

        assert footprint_for("ollama", "http://ollama.local:11434", "llama3.1:8b") == 42

    def test_endpoint_normalization_trailing_slash_on_lookup_value(self):
        """The reverse direction: a bare stored endpoint still matches a
        lookup endpoint WITH a trailing slash."""
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b", measured_footprint_bytes=42,
        )

        assert footprint_for("ollama", "http://ollama.local:11434/", "llama3.1:8b") == 42

    def test_no_matching_row_returns_none(self):
        assert footprint_for("ollama", "http://nowhere:1", "no-such-model") is None

    def test_multiple_matching_rows_returns_none(self):
        """An ambiguous ref must never silently pick one of several rows."""
        ModelConnection.objects.create(
            name="conn a", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b", footprint_override_bytes=111,
        )
        ModelConnection.objects.create(
            name="conn b", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b", footprint_override_bytes=222,
        )

        assert footprint_for("ollama", "http://ollama.local:11434", "llama3.1:8b") is None

    def test_model_id_match_is_exact_not_tag_normalized(self):
        """Unlike the catalog merge, `model_id` is matched verbatim here --
        a bare "llama3.1" must NOT match a stored ":latest" tagged row."""
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="llama3.1:latest", footprint_override_bytes=999,
        )

        assert footprint_for("ollama", "http://ollama.local:11434", "llama3.1") is None

    def test_different_engine_does_not_match(self):
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b", footprint_override_bytes=999,
        )

        assert footprint_for("vllm", "http://ollama.local:11434", "llama3.1:8b") is None

    def test_unmigrated_tables_tolerated(self):
        """`ProgrammingError`/`OperationalError` from the underlying table
        not existing yet (mid-`migrate`) reads as "unknown", not raise --
        same tolerance idiom `_bound_connection` already uses."""
        with patch("models.registry.models.ModelConnection.objects") as mock_objects:
            mock_objects.filter.side_effect = OperationalError(
                'relation "inference_modelconnection" does not exist'
            )

            assert footprint_for("ollama", "http://e:1", "m") is None


# --- record_measured_footprint (T4: footprint_for's write-side twin) -------


@pytest.mark.django_db
class TestRecordMeasuredFootprint:
    def test_writes_measured_bytes_and_timestamp_for_exact_match(self):
        connection = ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b",
        )

        record_measured_footprint("ollama", "http://ollama.local:11434", "llama3.1:8b", 555)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes == 555
        assert connection.measured_footprint_at is not None

    def test_endpoint_normalization_matches_footprint_for(self):
        connection = ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint="http://ollama.local:11434/",
            model_id="llama3.1:8b",
        )

        record_measured_footprint("ollama", "http://ollama.local:11434", "llama3.1:8b", 777)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes == 777

    def test_no_matching_row_is_a_silent_no_op(self):
        record_measured_footprint("ollama", "http://nowhere:1", "no-such-model", 123)  # must not raise

    def test_ambiguous_match_is_a_silent_no_op(self):
        a = ModelConnection.objects.create(
            name="conn a", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b",
        )
        b = ModelConnection.objects.create(
            name="conn b", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b",
        )

        record_measured_footprint("ollama", "http://ollama.local:11434", "llama3.1:8b", 999)

        a.refresh_from_db()
        b.refresh_from_db()
        assert a.measured_footprint_bytes is None
        assert b.measured_footprint_bytes is None

    def test_unmigrated_tables_tolerated(self):
        with patch("models.registry.models.ModelConnection.objects") as mock_objects:
            mock_objects.filter.side_effect = OperationalError(
                'relation "inference_modelconnection" does not exist'
            )

            record_measured_footprint("ollama", "http://e:1", "m", 42)  # must not raise

    def test_overwrites_a_previous_measurement(self):
        connection = ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b", measured_footprint_bytes=100,
        )

        record_measured_footprint("ollama", "http://ollama.local:11434", "llama3.1:8b", 200)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes == 200


@pytest.mark.django_db
class TestPickerSourcesAreOneRule:
    def test_connections_for_picker_filters_by_capability_in_picker_order(self):
        ModelConnection.objects.create(
            name="zeta image", engine="comfyui", endpoint="http://c:8188",
            model_id="z.safetensors", capabilities=["image-generation"], rank=2,
        )
        ModelConnection.objects.create(
            name="alpha image", engine="comfyui", endpoint="http://c:8188",
            model_id="a.safetensors", capabilities=["image-generation"], rank=1,
        )
        ModelConnection.objects.create(
            name="a chat model", engine="ollama", endpoint="http://o:11434",
            model_id="llama", capabilities=["chat"],
        )

        picked = connections_for_picker("image-generation")

        assert [c.name for c in picked] == ["alpha image", "zeta image"]

    def test_a_multi_capability_connection_appears_in_every_picker_it_answers(self):
        """Registration stores a model's FULL reported capability list, so a
        model that both chats and generates belongs in both pickers -- and
        the filter must be membership, never `capabilities[0]`."""
        ModelConnection.objects.create(
            name="both", engine="comfyui", endpoint="http://c:8188",
            model_id="b.safetensors", capabilities=["chat", "image-generation"],
        )

        assert [c.name for c in connections_for_picker("image-generation")] == ["both"]
        assert [c.name for c in connections_for_picker("chat")] == ["both"]

    def test_chat_connections_for_picker_is_the_same_rule(self):
        ModelConnection.objects.create(
            name="a chat model", engine="ollama", endpoint="http://o:11434",
            model_id="llama", capabilities=["chat"],
        )
        assert chat_connections_for_picker() == connections_for_picker("chat")

    def test_role_primary_names_the_bound_connection_for_any_role(self):
        connection = ModelConnection.objects.create(
            name="bound image", engine="comfyui", endpoint="http://c:8188",
            model_id="b.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.update_or_create(
            role_key="vision.generate", defaults={"connection": connection}
        )

        assert role_primary("vision.generate") == ("bound image", connection.pk)

    def test_role_primary_is_empty_for_a_genuinely_unassigned_role(self):
        assert role_primary("vision.generate") == ("", None)

    def test_answer_role_primary_is_the_same_rule(self):
        connection = ModelConnection.objects.create(
            name="bound chat", engine="ollama", endpoint="http://o:11434",
            model_id="llama", capabilities=["chat"],
        )
        RoleBinding.objects.update_or_create(
            role_key="rag.answer", defaults={"connection": connection}
        )
        assert answer_role_primary() == role_primary("rag.answer")
