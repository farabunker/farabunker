"""Unit tests for the binding resolver (models/contracts/bindings.py).

Pure settings + import_string resolution — no DB, no network. Django
settings are patched directly on `django.conf.settings` per test (inline,
no conftest.py per repo convention) and always restored.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from models.contracts import gateway
from models.contracts.bindings import (
    ResolvedModel,
    embed_dim_from_fingerprint,
    env_provider,
    resolve,
)
from models.contracts.engines.ollama import OllamaEngine
from models.contracts.roles import CHAT_CONVERSE_ROLE, RAG_ANSWER_ROLE, RAG_EMBED_ROLE
from models.registry.access import UNRESTRICTED_MODEL_ACCESS, ModelAccess
from models.registry.bindings import picker_options, resolve_connection_named
from models.registry.tests._helpers import make_chat_connection


# --- ResolvedModel.fingerprint ------------------------------------------


class TestFingerprint:
    def test_format_without_embed_dim(self):
        model = ResolvedModel("ollama", "llama3.1:8b", "http://localhost:11434")

        assert model.fingerprint == "ollama:llama3.1:8b:None"

    def test_format_with_embed_dim(self):
        model = ResolvedModel(
            "ollama", "nomic-embed-text", "http://localhost:11434", embed_dim=768
        )

        assert model.fingerprint == "ollama:nomic-embed-text:768"


# --- embed_dim_from_fingerprint (format + parse live together) ---


class TestEmbedDimFromFingerprint:
    def test_round_trips_with_fingerprint(self):
        model = ResolvedModel(
            "ollama", "nomic-embed-text", "http://localhost:11434", embed_dim=768
        )

        assert embed_dim_from_fingerprint(model.fingerprint) == "768"

    def test_round_trips_when_embed_dim_is_none(self):
        model = ResolvedModel("ollama", "llama3.1:8b", "http://localhost:11434")

        assert embed_dim_from_fingerprint(model.fingerprint) == "None"

    def test_model_id_containing_a_colon_does_not_confuse_the_parse(self):
        """`model_id` can itself contain colons (e.g. "qwen2.5:7b") -- a
        plain `split(":")` would misparse; `rsplit(":", 1)` recovers only
        the trailing embed_dim segment regardless."""
        model = ResolvedModel(
            "ollama", "qwen2.5:7b", "http://localhost:11434", embed_dim=1024
        )

        assert embed_dim_from_fingerprint(model.fingerprint) == "1024"


# --- env_provider ---------------------------------------------------------


class TestEnvProvider:
    @override_settings(
        LLM_MODEL="llama3.1:8b",
        OLLAMA_BASE_URL="http://ollama.local:11434",
    )
    def test_rag_answer_resolves_from_llm_settings(self):
        resolved = env_provider("rag.answer")

        assert resolved == ResolvedModel(
            "ollama", "llama3.1:8b", "http://ollama.local:11434"
        )

    @override_settings(
        EMBED_MODEL="nomic-embed-text",
        OLLAMA_BASE_URL="http://ollama.local:11434",
        EMBED_DIM=768,
    )
    def test_rag_embed_resolves_from_embed_settings_with_dim(self):
        resolved = env_provider("rag.embed")

        assert resolved == ResolvedModel(
            "ollama", "nomic-embed-text", "http://ollama.local:11434", embed_dim=768
        )

    def test_unknown_role_returns_none(self):
        assert env_provider("does.not.exist") is None

    @override_settings(LLM_MODEL=None, OLLAMA_BASE_URL="http://ollama.local:11434")
    def test_rag_answer_yields_nothing_when_no_override_is_set(self):
        """There is no baked default, so an unset `LLM_MODEL` means this
        provider has NOTHING to say about the role -- never a substituted
        model."""
        assert env_provider("rag.answer") is None

    @override_settings(
        EMBED_MODEL=None, EMBED_DIM=None, OLLAMA_BASE_URL="http://ollama.local:11434"
    )
    def test_rag_embed_yields_nothing_when_no_override_is_set(self):
        assert env_provider("rag.embed") is None

    @override_settings(LLM_MODEL="", EMBED_MODEL="", EMBED_DIM=None)
    def test_blank_overrides_are_treated_as_unset(self):
        """A blank value is the absence of a choice, not the empty model id."""
        assert env_provider("rag.answer") is None
        assert env_provider("rag.embed") is None

    @override_settings(
        EMBED_MODEL=None, EMBED_DIM=1024, OLLAMA_BASE_URL="http://ollama.local:11434"
    )
    def test_a_dimension_without_a_model_is_still_nothing(self):
        """`EMBED_DIM` alone names no model, so there is nothing to resolve
        -- a dimension must never be mistaken for a model choice, and must
        not conjure a binding out of a half-set override."""
        assert env_provider("rag.embed") is None

    @override_settings(
        EMBED_MODEL="operator-embed-choice",
        EMBED_DIM=None,
        OLLAMA_BASE_URL="http://ollama.local:11434",
    )
    def test_embed_override_without_a_dimension_resolves_with_embed_dim_none(self):
        """An override that names a model but no dimension is incomplete.
        Nothing is guessed here -- `embed_dim` passes through as None and
        the point of use (tools/rag/index.py) fails loudly."""
        resolved = env_provider("rag.embed")

        assert resolved == ResolvedModel(
            "ollama", "operator-embed-choice", "http://ollama.local:11434", embed_dim=None
        )

    @override_settings(
        LLM_MODEL="llama3.1:8b",
        OLLAMA_BASE_URL="http://ollama.local:11434",
    )
    def test_role_constants_and_engine_name_resolve_the_same_as_literals(self):
        """`env_provider` is keyed off `RAG_ANSWER_ROLE`/`RAG_EMBED_ROLE` and
        stamps `OllamaEngine.name` rather than hand-typed "rag.answer" /
        "ollama" literals -- pin that the constants still equal the literals
        the rest of this test class exercises, and that the resolved engine
        string is exactly `OllamaEngine.name`."""
        assert RAG_ANSWER_ROLE == "rag.answer"
        assert RAG_EMBED_ROLE == "rag.embed"

        resolved = env_provider(RAG_ANSWER_ROLE)

        assert resolved.engine == OllamaEngine.name


# --- resolve fallback chain ------------------------------------------------


class TestResolve:
    @override_settings(INFERENCE_BINDING_PROVIDER="")
    def test_no_provider_configured_uses_env_provider(self):
        with patch("models.contracts.bindings.env_provider") as mock_env_provider:
            mock_env_provider.return_value = ResolvedModel("ollama", "m", "http://e")

            resolved = resolve("rag.answer")

        mock_env_provider.assert_called_once_with("rag.answer")
        assert resolved == ResolvedModel("ollama", "m", "http://e")

    @override_settings(INFERENCE_BINDING_PROVIDER="fake.module.provider")
    def test_provider_returning_none_falls_back_to_env_provider(self):
        fake_provider = MagicMock(return_value=None)
        with (
            patch("models.contracts.bindings.import_string", return_value=fake_provider) as mock_import,
            patch("models.contracts.bindings.env_provider") as mock_env_provider,
        ):
            mock_env_provider.return_value = ResolvedModel("ollama", "fallback", "http://e")

            resolved = resolve("rag.answer")

        mock_import.assert_called_once_with("fake.module.provider")
        fake_provider.assert_called_once_with("rag.answer")
        mock_env_provider.assert_called_once_with("rag.answer")
        assert resolved == ResolvedModel("ollama", "fallback", "http://e")

    @override_settings(INFERENCE_BINDING_PROVIDER="fake.module.provider")
    def test_provider_returning_model_is_used_without_env_fallback(self):
        provided = ResolvedModel("vllm", "custom-model", "http://vllm.local")
        fake_provider = MagicMock(return_value=provided)
        with (
            patch("models.contracts.bindings.import_string", return_value=fake_provider),
            patch("models.contracts.bindings.env_provider") as mock_env_provider,
        ):
            resolved = resolve("rag.answer")

        mock_env_provider.assert_not_called()
        assert resolved is provided

    @override_settings(INFERENCE_BINDING_PROVIDER="")
    def test_unresolved_role_raises_clear_error(self):
        with pytest.raises(ValueError, match="does.not.exist"):
            resolve("does.not.exist")

    @override_settings(
        INFERENCE_BINDING_PROVIDER="",
        LLM_MODEL=None,
        EMBED_MODEL=None,
        EMBED_DIM=None,
    )
    def test_a_shipped_role_with_nothing_configured_raises_rather_than_guessing(self):
        """The state of a fresh box: no DB provider, no env override.
        `resolve()` must raise -- the Ask page turns that into its
        cause-accurate "isn't set up yet" 503, and the console into "No
        model assigned"."""
        with pytest.raises(ValueError, match="rag.answer"):
            resolve("rag.answer")
        with pytest.raises(ValueError, match="rag.embed"):
            resolve("rag.embed")


# --- provider import is lazy per call (not cached at module level) --------


class TestLazyImport:
    def test_provider_is_imported_fresh_on_every_resolve_call(self):
        fake_provider = MagicMock(return_value=ResolvedModel("ollama", "m", "http://e"))

        with patch(
            "models.contracts.bindings.import_string", return_value=fake_provider
        ) as mock_import:
            with override_settings(INFERENCE_BINDING_PROVIDER="fake.module.provider"):
                resolve("rag.answer")
                resolve("rag.answer")

        assert mock_import.call_count == 2


# --- gateway delegation (models/contracts/gateway.py) ------------------------
#
# `resolve()` is the contract these tests actually verify against; `gateway`
# is exercised here only to confirm it delegates faithfully (resolved role ->
# engine lookup -> build_llm/build_embedder with the resolved values), never
# constructing an LLM/embedder itself.


class TestGatewayDelegatesToResolvedEngine:
    def test_get_llm_builds_via_resolved_engine_with_resolved_values(self):
        resolved = ResolvedModel(
            "ollama", "llama3.1:8b", "http://ollama.local:11434", config={"request_timeout": 12.0}
        )
        fake_engine = MagicMock()
        fake_llm = MagicMock()
        fake_engine.build_llm.return_value = fake_llm

        with (
            patch("models.contracts.gateway.resolve", return_value=resolved) as mock_resolve,
            patch("models.contracts.gateway.get_engine", return_value=fake_engine) as mock_get_engine,
        ):
            result = gateway.get_llm("rag.answer")

        mock_resolve.assert_called_once_with("rag.answer")
        mock_get_engine.assert_called_once_with("ollama")
        fake_engine.build_llm.assert_called_once_with(
            "llama3.1:8b", "http://ollama.local:11434", request_timeout=12.0
        )
        assert result is fake_llm

    def test_get_embed_model_builds_via_resolved_engine_with_resolved_values(self):
        resolved = ResolvedModel(
            "ollama", "nomic-embed-text", "http://ollama.local:11434", embed_dim=768
        )
        fake_engine = MagicMock()
        fake_embedder = MagicMock()
        fake_engine.build_embedder.return_value = fake_embedder

        with (
            patch("models.contracts.gateway.resolve", return_value=resolved) as mock_resolve,
            patch("models.contracts.gateway.get_engine", return_value=fake_engine) as mock_get_engine,
        ):
            result = gateway.get_embed_model("rag.embed")

        mock_resolve.assert_called_once_with("rag.embed")
        mock_get_engine.assert_called_once_with("ollama")
        fake_engine.build_embedder.assert_called_once_with(
            "nomic-embed-text", "http://ollama.local:11434"
        )
        assert result is fake_embedder

    def test_get_llm_defaults_to_rag_answer_role(self):
        with (
            patch(
                "models.contracts.gateway.resolve",
                return_value=ResolvedModel("ollama", "m", "http://e"),
            ) as mock_resolve,
            patch("models.contracts.gateway.get_engine", return_value=MagicMock()),
        ):
            gateway.get_llm()

        mock_resolve.assert_called_once_with("rag.answer")

    def test_get_embed_model_defaults_to_rag_embed_role(self):
        with (
            patch(
                "models.contracts.gateway.resolve",
                return_value=ResolvedModel("ollama", "m", "http://e"),
            ) as mock_resolve,
            patch("models.contracts.gateway.get_engine", return_value=MagicMock()),
        ):
            gateway.get_embed_model()

        mock_resolve.assert_called_once_with("rag.embed")

# --- get_llm_for: explicit-ResolvedModel entry point ----------------------
#
# `get_llm_for` is the seam a caller with its own already-resolved
# `ResolvedModel` (e.g. `models.registry.bindings.resolve_connection`'s
# pk-addressed lookup) builds an LLM through, without going through
# `resolve()`/a role at all. `get_llm(role)` becomes a thin
# resolve-then-delegate wrapper around it.


class TestGetLlmFor:
    def test_builds_via_the_resolved_engine_with_threaded_config(self):
        resolved = ResolvedModel(
            "ollama",
            "llama3.1:8b",
            "http://ollama.local:11434",
            config={"context_window": 16384},
        )

        llm = gateway.get_llm_for(resolved)

        # Real, unmocked `Ollama` -- no network at construction time (see
        # test_engines.py) -- so its own attributes can be asserted
        # directly, same as test_db_bindings.py's context-window round-trip.
        assert llm.model == "llama3.1:8b"
        assert llm.base_url == "http://ollama.local:11434"
        assert llm.context_window == 16384

    def test_does_not_call_resolve(self):
        """`get_llm_for` takes an already-resolved binding -- it must never
        call `resolve()` itself, only the two callers of `resolve()`
        (`get_llm`, `get_embed_model`) should."""
        resolved = ResolvedModel("ollama", "m", "http://e")

        with patch("models.contracts.gateway.resolve") as mock_resolve:
            gateway.get_llm_for(resolved)

        mock_resolve.assert_not_called()

    def test_get_llm_delegates_to_get_llm_for_with_the_resolved_binding(self):
        resolved = ResolvedModel("ollama", "llama3.1:8b", "http://ollama.local:11434")
        fake_llm = MagicMock()

        with (
            patch("models.contracts.gateway.resolve", return_value=resolved) as mock_resolve,
            patch.object(gateway, "get_llm_for", return_value=fake_llm) as mock_get_llm_for,
        ):
            result = gateway.get_llm("rag.answer")

        mock_resolve.assert_called_once_with("rag.answer")
        mock_get_llm_for.assert_called_once_with(resolved)
        assert result is fake_llm

    # --- the competing-timeout pin (one-timeout task, 2026-09-17) -----------
    #
    # The incident this pins against: ollama's OWN hidden
    # `DEFAULT_REQUEST_TIMEOUT` (300s) used to fire before a turn's own
    # deadline ever could, because nothing upstream of `build_llm` ever
    # passed an explicit `request_timeout`. `get_llm_for`'s new keyword
    # closes that -- these tests build a REAL `Ollama` (no network at
    # construction time) and assert on its own `request_timeout`
    # attribute, the same "real object, no mock" style `TestGetLlmFor`
    # above already uses for `context_window`.

    def test_request_timeout_overrides_the_engine_default(self):
        from models.contracts.engines.ollama import DEFAULT_REQUEST_TIMEOUT

        resolved = ResolvedModel("ollama", "llama3.1:8b", "http://ollama.local:11434")

        llm = gateway.get_llm_for(resolved, request_timeout=456.0)

        assert llm.request_timeout == 456.0
        assert llm.request_timeout != DEFAULT_REQUEST_TIMEOUT

    def test_request_timeout_none_leaves_the_engine_default_untouched(self):
        from models.contracts.engines.ollama import DEFAULT_REQUEST_TIMEOUT

        resolved = ResolvedModel("ollama", "llama3.1:8b", "http://ollama.local:11434")

        llm = gateway.get_llm_for(resolved)

        assert llm.request_timeout == DEFAULT_REQUEST_TIMEOUT

    def test_request_timeout_wins_over_an_already_configured_value(self):
        """`resolved.config` might already carry its own `request_timeout`
        (an operator's per-connection override) -- the turn's own value
        still wins, set TO it rather than merely a fallback, so the
        engine's inner client can never be the one that ends a turn
        early."""
        resolved = ResolvedModel(
            "ollama", "llama3.1:8b", "http://ollama.local:11434",
            config={"request_timeout": 30.0},
        )

        llm = gateway.get_llm_for(resolved, request_timeout=456.0)

        assert llm.request_timeout == 456.0

    def test_request_timeout_does_not_mutate_the_resolved_models_own_config(self):
        resolved = ResolvedModel(
            "ollama", "llama3.1:8b", "http://ollama.local:11434", config={},
        )

        gateway.get_llm_for(resolved, request_timeout=456.0)

        assert resolved.config == {}


# --- get_embed_model_for: explicit-ResolvedModel entry point ---------------
#
# The embeddings twin of `TestGetLlmFor` above (T5): `get_embed_model_for`
# is the seam a caller with its own already-resolved `ResolvedModel` (e.g.
# `tools.rag.jobs.run_ask`'s pre-run re-check) builds an embedder through,
# without going through `resolve()`/a role at all. `get_embed_model(role)`
# becomes a thin resolve-then-delegate wrapper around it -- replacing the
# deleted `configure_settings`'s embeddings half.


class TestGetEmbedModelFor:
    def test_builds_via_the_resolved_engine_with_threaded_config(self):
        resolved = ResolvedModel(
            "ollama",
            "nomic-embed-text",
            "http://ollama.local:11434",
            embed_dim=768,
        )

        embedder = gateway.get_embed_model_for(resolved)

        # Real, unmocked `OllamaEmbedding` -- no network at construction
        # time (see test_engines.py) -- so its own attributes can be
        # asserted directly, same as `TestGetLlmFor`.
        assert embedder.model_name == "nomic-embed-text"
        assert embedder.base_url == "http://ollama.local:11434"

    def test_does_not_call_resolve(self):
        """`get_embed_model_for` takes an already-resolved binding -- it
        must never call `resolve()` itself, only `get_embed_model` should."""
        resolved = ResolvedModel("ollama", "m", "http://e")

        with patch("models.contracts.gateway.resolve") as mock_resolve:
            gateway.get_embed_model_for(resolved)

        mock_resolve.assert_not_called()

    def test_get_embed_model_delegates_to_get_embed_model_for_with_the_resolved_binding(self):
        resolved = ResolvedModel("ollama", "nomic-embed-text", "http://ollama.local:11434")
        fake_embedder = MagicMock()

        with (
            patch("models.contracts.gateway.resolve", return_value=resolved) as mock_resolve,
            patch.object(gateway, "get_embed_model_for", return_value=fake_embedder) as mock_get_embed_model_for,
        ):
            result = gateway.get_embed_model("rag.embed")

        mock_resolve.assert_called_once_with("rag.embed")
        mock_get_embed_model_for.assert_called_once_with(resolved)
        assert result is fake_embedder

    # --- the competing-timeout pin, embeddings side (final review I-3,
    # fix round 3) --------------------------------------------------------
    #
    # `OllamaEmbedding` has no bare `request_timeout` field the way `Ollama`
    # does -- its adapter reads a nested `client_kwargs={"timeout": ...}`
    # instead -- so these inspect the underlying `ollama.Client`'s own
    # httpx timeout directly, the same "real object, no mock" style
    # `TestGetLlmFor` already uses for the chat LLM.

    def test_request_timeout_lands_in_client_kwargs_before_the_engine_default(self):
        import httpx

        resolved = ResolvedModel("ollama", "nomic-embed-text", "http://ollama.local:11434")

        embedder = gateway.get_embed_model_for(resolved, request_timeout=45.0)

        assert embedder._client._client.timeout == httpx.Timeout(45.0)

    def test_request_timeout_none_leaves_the_engine_default_untouched(self):
        import httpx

        from models.contracts.engines.ollama import DEFAULT_REQUEST_TIMEOUT

        resolved = ResolvedModel("ollama", "nomic-embed-text", "http://ollama.local:11434")

        embedder = gateway.get_embed_model_for(resolved)

        assert embedder._client._client.timeout == httpx.Timeout(DEFAULT_REQUEST_TIMEOUT)

    def test_request_timeout_wins_over_an_already_configured_client_kwargs_timeout(self):
        import httpx

        resolved = ResolvedModel(
            "ollama", "nomic-embed-text", "http://ollama.local:11434",
            config={"client_kwargs": {"timeout": 30.0}},
        )

        embedder = gateway.get_embed_model_for(resolved, request_timeout=45.0)

        assert embedder._client._client.timeout == httpx.Timeout(45.0)

    def test_request_timeout_does_not_mutate_the_resolved_models_own_config(self):
        resolved = ResolvedModel(
            "ollama", "nomic-embed-text", "http://ollama.local:11434", config={},
        )

        gateway.get_embed_model_for(resolved, request_timeout=45.0)

        assert resolved.config == {}


@pytest.mark.django_db
class TestThePickerAndTheResolverObeyModelAccess:
    """TWO FUNCTIONS, EVERY SEAM. `picker_options` is what is OFFERED and
    `resolve_connection_named` is what is ACCEPTED; every user-selectable
    model choice on this box goes through one or the other, which is what
    makes this rule enforceable in two places rather than nine.
    """

    def test_a_restricted_connection_is_dropped_from_the_options(self):
        restricted, plain = make_chat_connection(), make_chat_connection()
        access = ModelAccess(required={restricted.pk: frozenset({1})},
                             held=frozenset(), unrestricted=False)
        values = [option["value"]
                  for option in picker_options("chat", CHAT_CONVERSE_ROLE, access=access)]
        assert str(plain.pk) in values
        assert str(restricted.pk) not in values

    def test_an_unrestricted_access_offers_everything_exactly_as_today(self):
        one, two = make_chat_connection(), make_chat_connection()
        values = [option["value"] for option in picker_options(
            "chat", CHAT_CONVERSE_ROLE, access=UNRESTRICTED_MODEL_ACCESS)]
        assert {str(one.pk), str(two.pk)} <= set(values)

    def test_the_option_list_collapses_to_None_when_nothing_is_permitted(self):
        """`picker_options` already answers `None` -- no `<select>` at all
        -- when there is nothing to pick from. A principal permitted none
        of the registered connections is that same case, not a new one."""
        restricted = make_chat_connection()
        access = ModelAccess(required={restricted.pk: frozenset({1})},
                             held=frozenset(), unrestricted=False)
        assert picker_options("chat", CHAT_CONVERSE_ROLE, access=access) is None

    def test_resolving_a_forbidden_connection_raises_the_same_ValueError(self):
        """`ValueError`, not a new exception type: every caller already
        handles it as "that model is not usable", and a second class would
        mean editing six `except` blocks to say the same thing. The
        MESSAGE differs, because the two causes do."""
        restricted = make_chat_connection()
        access = ModelAccess(required={restricted.pk: frozenset({1})},
                             held=frozenset(), unrestricted=False)
        with pytest.raises(ValueError) as excinfo:
            resolve_connection_named(restricted.pk, "chat", access=access)
        assert "entitlement" in str(excinfo.value).lower()

    def test_access_is_required_and_keyword_only_on_all_three(self):
        """REQUIRED, NOT DEFAULTED, for the reason the `visibility`
        argument at the retrieval filter point is: a default of "may use
        everything" is a fail-open default, and a caller that forgets it
        should fail at signature-checking time rather than at a security
        review two years later."""
        import inspect
        from models.registry import bindings
        for name in ("picker_options", "resolve_connection", "resolve_connection_named"):
            parameter = inspect.signature(getattr(bindings, name)).parameters["access"]
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
            assert parameter.default is inspect.Parameter.empty
