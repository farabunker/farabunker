"""Unit tests for the engine adapter seam (models/contracts/engines/).

External services are always mocked: `httpx` calls are patched so no real
Ollama server is ever contacted. `build_llm`/`build_embedder` construct
LlamaIndex objects directly (no network happens at construction time), so
those are exercised for real and asserted on their public attributes.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from models.registry.tests._helpers import fake_ollama_get, fake_ollama_show
from models.contracts import engines
from models.contracts.engines.base import InstalledModel
from models.contracts.engines.ollama import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_REQUEST_TIMEOUT,
    UNLOAD_TIMEOUT,
    OllamaEngine,
)

ENDPOINT = "http://ollama.local:11434"


# --- registry ---------------------------------------------------------


class TestRegistry:
    def test_get_engine_returns_registered_ollama_engine(self):
        engine = engines.get_engine("ollama")

        assert isinstance(engine, OllamaEngine)
        assert engine.name == "ollama"

    def test_get_engine_unknown_name_raises(self):
        with pytest.raises(ValueError):
            engines.get_engine("does-not-exist")

    def test_register_adds_engine_under_its_name(self):
        fake_engine = MagicMock()
        fake_engine.name = "fake"

        engines.register(fake_engine)
        try:
            assert engines.get_engine("fake") is fake_engine
        finally:
            del engines.ENGINES["fake"]


# --- OllamaEngine.is_healthy -------------------------------------------


class TestIsHealthy:
    @pytest.mark.parametrize("status_code, expected", [(200, True), (500, False)])
    @patch("models.contracts.engines.ollama.httpx.get")
    def test_returns_true_only_on_200(self, mock_get, status_code, expected):
        mock_get.return_value = MagicMock(status_code=status_code)

        assert OllamaEngine().is_healthy(ENDPOINT) is expected
        mock_get.assert_called_once_with(f"{ENDPOINT}/api/tags", timeout=pytest.approx(5.0))

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_returns_false_on_connection_error(self, mock_get):
        mock_get.side_effect = httpx.ConnectError("connection refused")

        assert OllamaEngine().is_healthy(ENDPOINT) is False

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_explicit_timeout_overrides_discovery_timeout(self, mock_get):
        """The server-scan feature passes its own short `PROBE_TIMEOUT` --
        existing callers omitting `timeout` are unaffected (see the test
        above, which asserts the 5.0s default unchanged)."""
        mock_get.return_value = MagicMock(status_code=200)

        assert OllamaEngine().is_healthy(ENDPOINT, timeout=0.5) is True
        mock_get.assert_called_once_with(f"{ENDPOINT}/api/tags", timeout=pytest.approx(0.5))


# --- OllamaEngine.well_known_ports --------------------------------------


class TestWellKnownPorts:
    def test_declares_ollamas_default_port(self):
        assert OllamaEngine().well_known_ports == (11434,)


# --- OllamaEngine.library_url --------------------------------------------


class TestLibraryUrl:
    """The console's starter section points at this instead of pinning a
    dated model list -- Ollama's own full, always-current library."""

    def test_declares_ollamas_library_url(self):
        assert OllamaEngine().library_url == "https://ollama.com/library"

    def test_library_url_is_read_via_getattr_with_empty_fallback(self):
        """The protocol declares a `library_url: str = ""` default and
        consumers read it the same defensive way `api_description` is read
        (`getattr(engine, "library_url", "")`) -- an adapter that hasn't
        set one yet must degrade to an empty string, never AttributeError."""

        class _BareEngine:
            name = "bare"

        assert getattr(_BareEngine(), "library_url", "") == ""


# --- OllamaEngine.install_cmd_template ------------------------------------


class TestInstallCmdTemplate:
    """The install SHAPE the console's "Getting models" facts line prints
    -- a placeholder template, never a runnable model-pinned command."""

    def test_declares_ollamas_install_shape_with_a_placeholder(self):
        assert OllamaEngine().install_cmd_template == "ollama pull <model-name>"
        assert "<model-name>" in OllamaEngine().install_cmd_template

    def test_install_cmd_template_is_read_via_getattr_with_empty_fallback(self):
        """Consumers read it the same defensive way `api_description`/
        `library_url` are read -- an adapter without one degrades to an
        empty string, never AttributeError."""

        class _BareEngine:
            name = "bare"

        assert getattr(_BareEngine(), "install_cmd_template", "") == ""


# --- OllamaEngine.list_installed ----------------------------------------


class TestListInstalled:
    @patch("models.contracts.engines.ollama.httpx.get")
    def test_parses_tags_and_marks_loaded_from_ps(self, mock_get):
        """A real `/api/ps` entry carries `size` (TOTAL bytes the running
        instance holds in memory) and `size_vram` (the VRAM-resident
        portion of that total) -- `loaded_size` captures the total;
        `size_vram` is deliberately not surfaced."""
        mock_get.side_effect = fake_ollama_get(
            tags_models=[
                {"name": "llama3:8b", "size": 4661224676},
                {"name": "nomic-embed-text", "size": 274290000},
            ],
            ps_models=[{"name": "llama3:8b", "size": 5137025024, "size_vram": 5137025024}],
        )

        result = OllamaEngine().list_installed(ENDPOINT)

        assert result == [
            InstalledModel(
                model_id="llama3:8b", size=4661224676, loaded=True, loaded_size=5137025024
            ),
            InstalledModel(
                model_id="nomic-embed-text", size=274290000, loaded=False, loaded_size=None
            ),
        ]

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_ps_entry_without_a_size_still_marks_loaded(self, mock_get):
        """A ps entry that omits `size` still marks the model loaded --
        `loaded_size` just stays None (the chip shows no size)."""
        mock_get.side_effect = fake_ollama_get(
            tags_models=[{"name": "llama3:8b", "size": 4661224676}],
            ps_models=[{"name": "llama3:8b"}],
        )

        result = OllamaEngine().list_installed(ENDPOINT)

        assert result[0].loaded is True
        assert result[0].loaded_size is None

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_no_installed_models_returns_empty_list(self, mock_get):
        mock_get.side_effect = fake_ollama_get(tags_models=[])

        assert OllamaEngine().list_installed(ENDPOINT) == []

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_parses_capabilities_and_embedding_length_from_tags(self, mock_get):
        mock_get.side_effect = fake_ollama_get(
            tags_models=[
                {
                    "name": "llama3.1:8b",
                    "capabilities": ["completion", "tools"],
                    "details": {"embedding_length": 4096},
                },
                {
                    "name": "nomic-embed-text:latest",
                    "capabilities": ["embedding"],
                    "details": {"embedding_length": 768},
                },
            ],
        )

        result = OllamaEngine().list_installed(ENDPOINT)

        assert result == [
            InstalledModel(
                model_id="llama3.1:8b",
                size=None,
                loaded=False,
                capabilities=("chat",),
                embedding_length=4096,
            ),
            InstalledModel(
                model_id="nomic-embed-text:latest",
                size=None,
                loaded=False,
                capabilities=("embeddings",),
                embedding_length=768,
            ),
        ]

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_missing_capabilities_and_details_parse_to_empty_and_none(self, mock_get):
        """Older Ollama builds omit `capabilities` and `details` entirely --
        parsing must tolerate that rather than raising."""
        mock_get.side_effect = fake_ollama_get(
            tags_models=[{"name": "llama3:8b", "size": 123}],
        )

        result = OllamaEngine().list_installed(ENDPOINT)

        assert result == [
            InstalledModel(
                model_id="llama3:8b",
                size=123,
                loaded=False,
                capabilities=(),
                embedding_length=None,
            )
        ]

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_completion_and_vision_both_map_to_known_capabilities(self, mock_get):
        """A model reporting several engine capabilities keeps all that map
        to a known platform capability."""
        mock_get.side_effect = fake_ollama_get(
            tags_models=[
                {"name": "llama3.2-vision:11b", "capabilities": ["completion", "vision"]}
            ],
        )

        result = OllamaEngine().list_installed(ENDPOINT)

        assert result[0].capabilities == ("chat", "vision")

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_unknown_engine_capability_is_dropped(self, mock_get):
        mock_get.side_effect = fake_ollama_get(
            tags_models=[{"name": "llama3.1:8b", "capabilities": ["completion", "tools"]}],
        )

        result = OllamaEngine().list_installed(ENDPOINT)

        # "tools" has no platform-capability equivalent and is dropped.
        assert result[0].capabilities == ("chat",)

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_non_2xx_tags_response_raises_http_status_error(self, mock_get):
        """`raise_for_status()` on a non-2xx `/api/tags` response must
        propagate as `httpx.HTTPStatusError` -- `discover()` catches the
        broader `httpx.HTTPError` around this call (see test_discovery.py's
        `TestDiscoverUnreachableEngine`), but `list_installed` itself does
        not swallow it."""
        response = MagicMock(status_code=500)
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "server error", request=MagicMock(), response=response
        )
        mock_get.return_value = response

        with pytest.raises(httpx.HTTPStatusError):
            OllamaEngine().list_installed(ENDPOINT)


# --- OllamaEngine.build_llm / build_embedder ----------------------------


class TestBuildLlm:
    def test_builds_ollama_llm_with_model_and_endpoint(self):
        llm = OllamaEngine().build_llm("llama3:8b", ENDPOINT)

        assert llm.model == "llama3:8b"
        assert llm.base_url == ENDPOINT
        assert llm.request_timeout == DEFAULT_REQUEST_TIMEOUT

    def test_cfg_can_override_request_timeout(self):
        llm = OllamaEngine().build_llm("llama3:8b", ENDPOINT, request_timeout=12.0)

        assert llm.request_timeout == 12.0

    # --- context_window safety floor (qwen3:30b-rag incident) -----------
    #
    # LlamaIndex's `Ollama` defaults `context_window=-1`, which triggers an
    # architecture-max probe against the model server (`client.show()`) the
    # first time `metadata`/`_model_kwargs` is read -- NOT at construction.
    # These tests assert the constructed object's `context_window` attribute
    # is never left at -1, so that probe can never fire, without needing a
    # live server.

    def test_default_context_window_is_bounded_not_the_probing_sentinel(self):
        llm = OllamaEngine().build_llm("llama3:8b", ENDPOINT)

        assert llm.context_window == DEFAULT_CONTEXT_WINDOW
        assert llm.context_window != -1

    def test_cfg_context_window_override_wins_over_the_default(self):
        llm = OllamaEngine().build_llm("qwen3:30b-rag", ENDPOINT, context_window=16384)

        assert llm.context_window == 16384


class TestBuildEmbedder:
    def test_builds_ollama_embedder_with_model_and_endpoint(self):
        embedder = OllamaEngine().build_embedder("nomic-embed-text", ENDPOINT)

        assert embedder.model_name == "nomic-embed-text"
        assert embedder.base_url == ENDPOINT

    # --- stray context_window kwarg tolerance (ADR 0010, ~line 404) -----
    #
    # `gateway.get_embed_model` spreads `resolved.config` into
    # `build_embedder` unconditionally, so a `context_window` stored on an
    # embeddings-capable connection (a field the console never hides, per
    # the owner's nothing-hidden ruling, even though it's chat-only) reaches
    # `OllamaEmbedding(context_window=...)`. Today's installed llama-index
    # silently drops unknown kwargs (pydantic ignores extras) rather than
    # raising. This test pins that assumption: if a llama-index upgrade
    # ever makes construction strict, THIS test fails before the live embed
    # path does.
    def test_stray_context_window_kwarg_is_a_harmless_no_op(self):
        embedder = OllamaEngine().build_embedder(
            "nomic-embed-text", ENDPOINT, context_window=16384
        )

        assert embedder.model_name == "nomic-embed-text"
        assert not hasattr(embedder, "context_window")

    # --- request timeout (W6 review MAJOR 2) -----------------------------
    #
    # `build_llm` above bounds every chat/generate request via
    # `request_timeout`; `OllamaEmbedding`'s underlying `ollama.Client` used
    # to be built with NO timeout at all (the library default, `None` --
    # unbounded), so a caller talking to a wedged/hung embed server could
    # block its request thread FOREVER. That became load-bearing the moment
    # `tools.rag.views.SearchView` (W6) started calling an embed model
    # SYNCHRONOUSLY inside an HTTP request, with no queue between a stalled
    # engine and the thread serving that request. These tests read the
    # timeout back off the real `httpx.Client` the built embedder's own
    # `ollama.Client` wraps (`embedder._client._client.timeout`) rather than
    # mocking anything, the same "construct for real, assert on public
    # attributes" posture `TestBuildLlm` already takes.

    def test_builds_embedder_with_the_default_request_timeout(self):
        embedder = OllamaEngine().build_embedder("nomic-embed-text", ENDPOINT)

        assert embedder._client._client.timeout == httpx.Timeout(DEFAULT_REQUEST_TIMEOUT)
        assert embedder._async_client._client.timeout == httpx.Timeout(DEFAULT_REQUEST_TIMEOUT)

    def test_caller_supplied_client_kwargs_timeout_is_not_overridden(self):
        """`client_kwargs.setdefault`, not an outright overwrite -- a caller
        who already picked their own timeout (or any other client kwarg)
        keeps it."""
        embedder = OllamaEngine().build_embedder(
            "nomic-embed-text", ENDPOINT, client_kwargs={"timeout": 7.0}
        )

        assert embedder._client._client.timeout == httpx.Timeout(7.0)

    def test_other_client_kwargs_survive_alongside_the_default_timeout(self):
        """Setting the default `timeout` must not clobber other
        `client_kwargs` a caller already supplied."""
        embedder = OllamaEngine().build_embedder(
            "nomic-embed-text", ENDPOINT, client_kwargs={"headers": {"X-Test": "1"}}
        )

        assert embedder._client._client.timeout == httpx.Timeout(DEFAULT_REQUEST_TIMEOUT)
        assert embedder._client._client.headers["x-test"] == "1"


# --- OllamaEngine.loaded_footprint (T2b) ----------------------------------
#
# Shares `_ps_sizes` with `list_installed` (refactored above into one
# helper) -- these tests exercise the seam through the public method, not
# `_ps_sizes` directly, the same "mock at the HTTP layer" discipline as
# `TestListInstalled`.


class TestLoadedFootprint:
    @patch("models.contracts.engines.ollama.httpx.get")
    def test_returns_loaded_models_size(self, mock_get):
        mock_get.side_effect = fake_ollama_get(
            tags_models=[],
            ps_models=[{"name": "llama3:8b", "size": 5137025024}],
        )

        assert OllamaEngine().loaded_footprint(ENDPOINT, "llama3:8b") == 5137025024

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_model_not_loaded_returns_none(self, mock_get):
        mock_get.side_effect = fake_ollama_get(tags_models=[], ps_models=[])

        assert OllamaEngine().loaded_footprint(ENDPOINT, "llama3:8b") is None

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_ps_entry_without_a_size_returns_none(self, mock_get):
        mock_get.side_effect = fake_ollama_get(
            tags_models=[], ps_models=[{"name": "llama3:8b"}]
        )

        assert OllamaEngine().loaded_footprint(ENDPOINT, "llama3:8b") is None

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_http_error_degrades_to_none_not_raise(self, mock_get):
        """A measurement is opportunistic, never an operation that can fail
        its caller -- unlike `list_installed`, which lets the same error
        propagate."""
        mock_get.side_effect = httpx.ConnectError("connection refused")

        assert OllamaEngine().loaded_footprint(ENDPOINT, "llama3:8b") is None

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_non_2xx_ps_response_degrades_to_none(self, mock_get):
        response = MagicMock(status_code=500)
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "server error", request=MagicMock(), response=response
        )
        mock_get.return_value = response

        assert OllamaEngine().loaded_footprint(ENDPOINT, "llama3:8b") is None


# --- OllamaEngine.unload (T2b) ---------------------------------------------
#
# Ollama's documented immediate-unload mechanism: POST /api/generate with
# {"model": model_id, "keep_alive": 0} and no prompt.


class TestUnload:
    @patch("models.contracts.engines.ollama.httpx.post")
    def test_success_returns_true_and_posts_keep_alive_zero(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)

        assert OllamaEngine().unload(ENDPOINT, "llama3:8b") is True
        mock_post.assert_called_once_with(
            f"{ENDPOINT}/api/generate",
            json={"model": "llama3:8b", "keep_alive": 0},
            timeout=UNLOAD_TIMEOUT,
        )

    @patch("models.contracts.engines.ollama.httpx.post")
    def test_connection_error_returns_false(self, mock_post):
        mock_post.side_effect = httpx.ConnectError("connection refused")

        assert OllamaEngine().unload(ENDPOINT, "llama3:8b") is False

    @patch("models.contracts.engines.ollama.httpx.post")
    def test_non_2xx_response_returns_false(self, mock_post):
        response = MagicMock(status_code=500)
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "server error", request=MagicMock(), response=response
        )
        mock_post.return_value = response

        assert OllamaEngine().unload(ENDPOINT, "llama3:8b") is False

    @patch("models.contracts.engines.ollama.httpx.post")
    def test_uses_unload_timeout_not_discovery_timeout(self, mock_post):
        """A 5s discovery timeout is too short for a request that may
        briefly queue behind in-flight work -- `unload` uses its own,
        longer `UNLOAD_TIMEOUT`."""
        mock_post.return_value = MagicMock(status_code=200)

        OllamaEngine().unload(ENDPOINT, "llama3:8b")

        assert mock_post.call_args.kwargs["timeout"] == UNLOAD_TIMEOUT
        assert UNLOAD_TIMEOUT > 5.0


class TestOllamaSupportsToolCalling:
    """`Ollama.metadata.is_function_calling_model` is hardcoded True
    upstream (llama_index/llms/ollama/base.py:127-130,189-198, with its
    own `# TODO: Detect...` at :196). It is a DECLARATION, not a
    detection, and this platform must not use it as a check. The real
    signal is `POST /api/show`'s `capabilities` list."""

    @patch("models.contracts.engines.ollama.httpx.post")
    def test_a_model_reporting_tools_is_true(self, mock_post):
        mock_post.side_effect = fake_ollama_show(["completion", "tools"])
        assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is True

    @patch("models.contracts.engines.ollama.httpx.post")
    def test_a_model_not_reporting_tools_is_false(self, mock_post):
        mock_post.side_effect = fake_ollama_show(["completion", "vision"])
        assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is False

    @patch("models.contracts.engines.ollama.httpx.post")
    def test_an_embedding_model_is_false(self, mock_post):
        mock_post.side_effect = fake_ollama_show(["embedding"])
        assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is False

    @patch("models.contracts.engines.ollama.httpx.post")
    def test_a_response_without_a_capabilities_key_is_none_not_false(self, mock_post):
        """`None` means "this engine does not report it", which is a
        different fact from "it reports that it cannot". The chat
        preflight refuses on False and RUNS on None (section 4.7)."""
        mock_post.side_effect = fake_ollama_show(omit_capabilities=True)
        assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is None

    @patch("models.contracts.engines.ollama.httpx.post")
    def test_an_unreachable_engine_is_none_never_an_exception(self, mock_post):
        mock_post.side_effect = fake_ollama_show(connect_error=True)
        assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is None

    @patch("models.contracts.engines.ollama.httpx.post")
    def test_a_non_2xx_response_is_none_never_an_exception(self, mock_post):
        mock_post.side_effect = fake_ollama_show(status_error=True)
        assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is None

    @patch("models.contracts.engines.ollama.httpx.post")
    def test_a_non_list_capabilities_value_is_none_not_an_exception(self, mock_post):
        """`capabilities` present but not a list (a malformed/unexpected
        response shape) must degrade the same way an HTTP failure does --
        `None`, never a `TypeError` out of the `"tools" in reported`
        membership test."""
        mock_post.side_effect = fake_ollama_show(12345)
        assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is None

    @patch("models.contracts.engines.ollama.httpx.post")
    def test_a_non_dict_body_is_none_never_an_exception(self, mock_post):
        """Valid JSON that decodes to something other than an object (a
        bare list, here) has no `.get` -- the shape must be checked
        BEFORE `.get("capabilities")` is called on it, or this raises
        `AttributeError` and escapes the never-raise contract instead of
        degrading to `None` like every other malformed-response case."""
        mock_post.side_effect = fake_ollama_show(non_dict_body=[1, 2, 3])
        assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is None

    @patch("models.contracts.engines.ollama.httpx.post")
    def test_it_reads_the_raw_string_and_never_the_platform_vocabulary(self, mock_post):
        """It must not route through `_map_capabilities`, which DROPS
        "tools" because that string has no platform-capability
        equivalent. Routing through it would make this method always
        False."""
        from models.contracts.engines.ollama import _map_capabilities

        assert _map_capabilities(["completion", "tools"]) == ("chat",)
        mock_post.side_effect = fake_ollama_show(["completion", "tools"])
        assert OllamaEngine().supports_tool_calling("m", ENDPOINT) is True

    def test_tools_is_not_in_the_platform_capability_vocabulary(self):
        """`CAPABILITIES` is the ROLE vocabulary and, per ADR
        0010:137-141, the manifest vocabulary. "Can call tools" is a
        model trait, not a purpose a role can name -- adding it would let
        someone register a nonsensical RoleSpec(capability="tools")."""
        from models.contracts.roles import CAPABILITIES

        assert "tools" not in CAPABILITIES


class TestSupportsToolCallingIsOptional:
    def test_an_adapter_without_it_degrades_to_none_not_attributeerror(self):
        """Callers read it defensively via `getattr(engine,
        "supports_tool_calling", None)`, the same shape
        `loaded_footprint`/`unload` already use (base.py:400-406, the block
        comment that states the rule), so a
        third-party or stub adapter that predates this method degrades to
        "no measurement" rather than crashing a preflight."""
        from models.contracts.engines import get_engine

        # `InferenceEngine` is a Protocol (base.py:274) and no adapter
        # subclasses it -- typing here is purely structural -- so an
        # adapter that has not implemented the method really does have no
        # such attribute, and `getattr(..., None)` is the whole guard.
        for name in ("comfyui", "whisper"):
            assert getattr(get_engine(name), "supports_tool_calling", None) is None

        assert callable(getattr(get_engine("ollama"), "supports_tool_calling", None))

    def test_the_protocol_declares_it_so_an_adapter_author_can_find_it(self):
        """Declaring it on the Protocol (rather than leaving it
        undocumented) is what lets a future adapter's author discover the
        seam at all -- the reason base.py:400-406 gives for declaring
        `loaded_footprint`/`unload` there."""
        from models.contracts.engines.base import InferenceEngine

        assert "supports_tool_calling" in InferenceEngine.__dict__
