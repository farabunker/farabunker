"""Unit tests for models/registry/discovery.py.

Discovery I/O is: HTTP via the Ollama engine (mocked at the `httpx.get`
layer per repo convention -- never by patching the engine's own methods
away, so `list_installed`'s real parsing is exercised) and the ORM for
`ModelConnection` rows (real DB, `@pytest.mark.django_db`).

`@pytest.mark.django_db` at class level (repo convention); no conftest.py.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from models.registry.discovery import (
    COMMON_HOSTS,
    DiscoveryRow,
    discover,
    norm_endpoint,
    scan_for_servers,
)
from models.registry.models import ModelConnection
from models.registry.tests._helpers import clear_seeded_rows, fake_ollama_get
from models.contracts import engines
from models.contracts.catalog import CATALOG, find
from models.contracts.engines import ENGINES
from models.contracts.engines.base import InstalledModel

ENDPOINT = "http://ollama.local:11434"


def _at(endpoint: str = ENDPOINT) -> dict[str, list[str]]:
    """Every CURRENTLY registered engine polled at one endpoint -- the
    pre-D11 single-endpoint behaviour expressed as `discover()`'s new map.
    Evaluated per call, so a stub engine a test registers is included
    automatically."""
    return {engine.name: [endpoint] for engine in engines.ENGINES.values()}


def _stub_engine(name="stubengine", port=9999, healthy_url=None, models=None):
    """A fake `InferenceEngine` registered ONLY for one test, then removed
    -- the anti-anchoring proof needs a SECOND engine whose port/hostname
    the scan loop has never heard of, to prove the loop reads
    `well_known_ports`/`.name` off the engine object rather than assuming
    Ollama's."""
    engine = MagicMock()
    engine.name = name
    engine.well_known_ports = (port,)
    healthy_url = healthy_url or f"http://localhost:{port}"
    engine.is_healthy.side_effect = lambda url, timeout=None: url == healthy_url
    engine.list_installed.return_value = models or []
    return engine


@pytest.fixture(autouse=True)
def _clear_seeded_rows(db):
    """Migration 0002 seeds two ModelConnection/RoleBinding rows at migrate
    time; clear them so these tests' assumptions about which DB rows exist
    hold, matching test_bindings.py's convention."""
    clear_seeded_rows()


_fake_get = fake_ollama_get


# --- catalog-only (engine unreachable, no DB rows) -----------------------


@pytest.mark.django_db
class TestDiscoverCatalogOnly:
    @patch("models.contracts.engines.ollama.httpx.get")
    def test_returns_one_row_per_catalog_entry(self, mock_get):
        mock_get.side_effect = httpx.ConnectError("connection refused")

        rows = discover(_at(), list(ModelConnection.objects.all()))

        assert len(rows) == len(CATALOG)
        assert all(row.in_catalog for row in rows)
        assert all(not row.installed for row in rows)
        assert all(not row.loaded for row in rows)
        assert all(not row.connected for row in rows)

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_catalog_metadata_is_carried_onto_the_row(self, mock_get):
        mock_get.side_effect = httpx.ConnectError("connection refused")
        entry = find("nomic-embed-text")

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "nomic-embed-text")
        assert row == DiscoveryRow(
            engine="ollama",
            model_id="nomic-embed-text",
            name=entry.name,
            capability="embeddings",
            in_catalog=True,
            installed=False,
            loaded=False,
            connected=False,
            size=None,
            embed_dim=768,
            capability_source="catalog",
            # T9: the catalog only ever knows one capability per entry, so
            # `capabilities` is that same value as a singleton tuple.
            capabilities=("embeddings",),
        )


# --- unreachable engine ----------------------------------------------------


@pytest.mark.django_db
class TestDiscoverUnreachableEngine:
    @patch("models.contracts.engines.ollama.httpx.get")
    def test_http_error_from_list_installed_does_not_propagate(self, mock_get):
        mock_get.side_effect = httpx.ConnectError("connection refused")

        # Must not raise -- discover() must render the catalog/DB state
        # even when Ollama is unreachable.
        rows = discover(_at(), list(ModelConnection.objects.all()))

        assert len(rows) == len(CATALOG)

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_http_status_error_from_list_installed_does_not_propagate(self, mock_get):
        """`list_installed` raises `httpx.HTTPStatusError` (a subclass of
        `httpx.HTTPError`) on a non-2xx response -- `discover()` must
        swallow that the same way it swallows a connection failure."""
        response = MagicMock(status_code=500)
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "server error", request=MagicMock(), response=response
        )
        mock_get.return_value = response

        rows = discover(_at(), list(ModelConnection.objects.all()))

        assert len(rows) == len(CATALOG)


# --- installed models merge -------------------------------------------


@pytest.mark.django_db
class TestDiscoverInstalled:
    @patch("models.contracts.engines.ollama.httpx.get")
    def test_installed_catalog_model_is_flagged_installed_and_loaded(self, mock_get):
        """The ps entry's in-memory `size` rides along as `loaded_size`
        (already in the same scan payload -- no new call)."""
        mock_get.side_effect = _fake_get(
            tags_models=[{"name": "nomic-embed-text", "size": 274290000}],
            ps_models=[{"name": "nomic-embed-text", "size": 400000000, "size_vram": 400000000}],
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "nomic-embed-text")
        assert row.in_catalog is True
        assert row.installed is True
        assert row.loaded is True
        assert row.size == 274290000
        assert row.loaded_size == 400000000

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_installed_model_not_loaded(self, mock_get):
        mock_get.side_effect = _fake_get(
            tags_models=[{"name": "nomic-embed-text", "size": 274290000}],
            ps_models=[],
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "nomic-embed-text")
        assert row.installed is True
        assert row.loaded is False
        assert row.loaded_size is None

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_installed_model_not_in_catalog_gets_its_own_row(self, mock_get):
        mock_get.side_effect = _fake_get(
            tags_models=[{"name": "some-custom-model:latest", "size": 123}],
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "some-custom-model:latest")
        assert row.in_catalog is False
        assert row.installed is True
        assert row.capability is None
        assert len(rows) == len(CATALOG) + 1

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_catalog_models_not_installed_stay_uninstalled(self, mock_get):
        mock_get.side_effect = _fake_get(tags_models=[])

        rows = discover(_at(), list(ModelConnection.objects.all()))

        assert all(not row.installed for row in rows)
        assert all(not row.loaded for row in rows)


# --- engine-reported metadata ----------------------------------------------


@pytest.mark.django_db
class TestDiscoverEngineMetadata:
    @patch("models.contracts.engines.ollama.httpx.get")
    def test_installed_embedding_model_yields_embed_dim_with_no_catalog_entry(self, mock_get):
        mock_get.side_effect = _fake_get(
            tags_models=[
                {
                    "name": "some-embedder:latest",
                    "capabilities": ["embedding"],
                    "details": {"embedding_length": 512},
                }
            ],
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "some-embedder:latest")
        assert row.in_catalog is False
        assert row.installed is True
        assert row.capability == "embeddings"
        assert row.embed_dim == 512
        # The row records WHERE the metadata came from, so the console's
        # details disclosure can say "detected from engine".
        assert row.capability_source == "engine"

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_chat_model_embedding_length_never_becomes_embed_dim(self, mock_get):
        mock_get.side_effect = _fake_get(
            tags_models=[
                {
                    "name": "llama3.1:8b",
                    "capabilities": ["completion", "tools"],
                    "details": {"embedding_length": 4096},
                }
            ],
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "llama3.1:8b")
        assert row.capability == "chat"
        assert row.embed_dim is None

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_multi_capability_choice_is_order_independent_completion_then_embedding(
        self, mock_get
    ):
        """A model reporting both chat and embeddings capabilities is
        presented as an embedder -- and embed_dim tracks that same chosen
        capability -- regardless of which order Ollama listed them in."""
        mock_get.side_effect = _fake_get(
            tags_models=[
                {
                    "name": "dual-capability:latest",
                    "capabilities": ["completion", "embedding"],
                    "details": {"embedding_length": 999},
                }
            ],
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "dual-capability:latest")
        assert row.capability == "embeddings"
        assert row.embed_dim == 999

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_multi_capability_choice_is_order_independent_embedding_then_completion(
        self, mock_get
    ):
        mock_get.side_effect = _fake_get(
            tags_models=[
                {
                    "name": "dual-capability:latest",
                    "capabilities": ["embedding", "completion"],
                    "details": {"embedding_length": 999},
                }
            ],
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "dual-capability:latest")
        assert row.capability == "embeddings"
        assert row.embed_dim == 999

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_multi_capability_model_carries_the_full_set_forward(self, mock_get):
        """T9 (multi-capability registration): a llava/qwen-vl-class model
        reporting BOTH `completion` and `vision` must carry its FULL mapped
        capability set forward on `DiscoveryRow.capabilities` -- not just
        whichever one the `capability` tie-break chose as
        primary/display (still "chat", unaffected, per the existing
        embeddings > chat > first rule)."""
        mock_get.side_effect = _fake_get(
            tags_models=[
                {
                    "name": "llava:13b",
                    "capabilities": ["completion", "vision"],
                }
            ],
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "llava:13b")
        assert row.capability == "chat"  # tie-break unaffected
        assert row.capabilities == ("chat", "vision")

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_single_capability_model_still_carries_a_one_item_set(self, mock_get):
        """Back-compat: an ordinary single-capability model (e.g. plain
        chat) still populates `capabilities` -- as a one-item tuple
        matching `capability` -- not left empty."""
        mock_get.side_effect = _fake_get(
            tags_models=[{"name": "llama3.1:8b", "capabilities": ["completion"]}],
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "llama3.1:8b")
        assert row.capability == "chat"
        assert row.capabilities == ("chat",)

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_installed_model_with_no_engine_capabilities_never_inherits_stale_catalog_data(
        self, mock_get
    ):
        """An installed model matching a catalog entry, but whose engine
        report carries no `capabilities` at all (older Ollama build), must
        show capability/embed_dim as unknown (None) rather than silently
        presenting the catalog's guess as detected fact."""
        mock_get.side_effect = _fake_get(
            tags_models=[{"name": "nomic-embed-text:latest", "size": 274290000}],
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "nomic-embed-text:latest")
        assert row.installed is True
        assert row.in_catalog is True
        assert row.capability is None
        assert row.embed_dim is None
        # No engine report means no source either -- the stale catalog
        # stamp must not survive the installed overwrite.
        assert row.capability_source is None

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_catalog_bare_name_merges_with_installed_latest_tag(self, mock_get):
        mock_get.side_effect = _fake_get(
            tags_models=[
                {
                    "name": "nomic-embed-text:latest",
                    "capabilities": ["embedding"],
                    "details": {"embedding_length": 768},
                }
            ],
        )
        entry = find("nomic-embed-text")

        rows = discover(_at(), list(ModelConnection.objects.all()))

        matches = [r for r in rows if r.model_id in ("nomic-embed-text", "nomic-embed-text:latest")]
        assert len(matches) == 1
        row = matches[0]
        assert row.model_id == "nomic-embed-text:latest"  # engine's exact string, for API calls
        assert row.name == entry.name  # catalog's friendly name wins on a match
        assert row.in_catalog is True
        assert row.installed is True
        assert row.capability == "embeddings"
        assert row.embed_dim == 768
        assert len(rows) == len(CATALOG)

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_catalog_entry_with_explicit_tag_does_not_falsely_match_different_tag(
        self, mock_get
    ):
        # Catalog has "qwen2.5:7b"; an installed "qwen2.5:14b" is a
        # different, explicit tag and must not merge onto that row.
        mock_get.side_effect = _fake_get(
            tags_models=[{"name": "qwen2.5:14b", "capabilities": ["completion"]}],
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        catalog_row = next(r for r in rows if r.model_id == "qwen2.5:7b")
        assert catalog_row.installed is False

        installed_row = next(r for r in rows if r.model_id == "qwen2.5:14b")
        assert installed_row.in_catalog is False
        assert installed_row.installed is True

        assert len(rows) == len(CATALOG) + 1


# --- DB ModelConnection merge --------------------------------------------


@pytest.mark.django_db
class TestDiscoverConnections:
    @patch("models.contracts.engines.ollama.httpx.get")
    def test_connection_matching_catalog_entry_is_flagged_connected(self, mock_get):
        mock_get.side_effect = httpx.ConnectError("connection refused")
        ModelConnection.objects.create(
            name="workstation embed",
            engine="ollama",
            endpoint=ENDPOINT,
            model_id="nomic-embed-text",
            capabilities=["embeddings"],
            embed_dim=768,
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "nomic-embed-text")
        assert row.connected is True
        assert row.in_catalog is True
        assert row.capability == "embeddings"

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_connection_not_in_catalog_or_installed_gets_its_own_row(self, mock_get):
        mock_get.side_effect = httpx.ConnectError("connection refused")
        ModelConnection.objects.create(
            name="custom connection",
            engine="ollama",
            endpoint="http://other.local:11434",
            model_id="custom-model:latest",
            capabilities=["chat"],
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "custom-model:latest")
        assert row.connected is True
        assert row.in_catalog is False
        assert row.installed is False
        assert row.capability == "chat"
        # A capability filled from the registered connection is
        # operator-stored, not detected -- the source stamp says so.
        assert row.capability_source == "connection"
        assert len(rows) == len(CATALOG) + 1

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_connection_only_rows_capabilities_from_the_connections_full_list(self, mock_get):
        """T9: a connection-only row (not installed anywhere `discover()`
        scanned) carries the connection's WHOLE stored capability list on
        `capabilities`, not just the single tie-break `capability` value."""
        mock_get.side_effect = httpx.ConnectError("connection refused")
        ModelConnection.objects.create(
            name="dual custom connection",
            engine="ollama",
            endpoint="http://other.local:11434",
            model_id="custom-vlm:latest",
            capabilities=["chat", "vision"],
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "custom-vlm:latest")
        assert row.capability == "chat"
        assert row.capabilities == ("chat", "vision")

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_installed_rows_capabilities_win_over_the_connections_stored_list(self, mock_get):
        """T9: same installed-beats-connection precedence `capability`
        already has -- an installed row's own (richer, live) engine-
        reported capability set is never overwritten by a matching
        connection's stored one."""
        mock_get.side_effect = _fake_get(
            tags_models=[
                {
                    "name": "nomic-embed-text",
                    "capabilities": ["embedding"],
                    "details": {"embedding_length": 768},
                }
            ],
        )
        ModelConnection.objects.create(
            name="workstation embed",
            engine="ollama",
            endpoint=ENDPOINT,
            model_id="nomic-embed-text",
            # Deliberately a DIFFERENT (narrower) stored list than what the
            # engine actually reports, to prove the installed row's set
            # wins rather than being overwritten by this one.
            capabilities=["embeddings"],
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "nomic-embed-text")
        assert row.installed is True
        assert row.connected is True
        assert row.capabilities == ("embeddings",)

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_all_three_sources_merge_onto_one_row(self, mock_get):
        mock_get.side_effect = _fake_get(
            tags_models=[{"name": "nomic-embed-text", "size": 274290000}],
            ps_models=[{"name": "nomic-embed-text"}],
        )
        ModelConnection.objects.create(
            name="workstation embed",
            engine="ollama",
            endpoint=ENDPOINT,
            model_id="nomic-embed-text",
            capabilities=["embeddings"],
            embed_dim=768,
        )

        rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "nomic-embed-text")
        assert row.in_catalog is True
        assert row.installed is True
        assert row.loaded is True
        assert row.connected is True
        assert len(rows) == len(CATALOG)


# --- discover(): de-anchored installed-scan --------------------------------


@pytest.mark.django_db
class TestDiscoverEngineDeAnchoring:
    """`discover()`'s installed-models merge used to call a hardcoded
    `get_engine("ollama")`; it now iterates `models.contracts.engines.ENGINES`
    -- these tests prove that de-anchoring is behavior-preserving. The
    production registry holds several adapters now (ollama, comfyui, ...),
    so tests that need the single-engine case PIN it explicitly with
    `patch.dict("models.contracts.engines.ENGINES", {"ollama": ...},
    clear=True)` rather than assuming it of the live registry.
    `test_only_ollama_registered_by_default` asserts that manufactured
    single-engine state, not a fact about production; the last test in this
    class proves a SECOND registered engine's models merge in too, tagged
    with its own name, with no changes to `discover()`."""

    def test_only_ollama_registered_by_default(self):
        # `discover()`'s de-anchoring is meaningless to test in isolation
        # unless the registry really does hold just one engine -- a second
        # adapter (e.g. ComfyUI) is also registered by default now, so this
        # PINS the single-engine precondition explicitly rather than
        # assuming it of the production registry. This is the precondition
        # every other test in this class (and test_discovery.py as a whole)
        # relies on implicitly.
        with patch.dict("models.contracts.engines.ENGINES", {"ollama": ENGINES["ollama"]}, clear=True):
            assert list(engines.ENGINES.keys()) == ["ollama"]

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_with_only_ollama_registered_result_is_unchanged(self, mock_get):
        mock_get.side_effect = fake_ollama_get(
            tags_models=[{"name": "llama3.1:8b", "size": 123}],
        )

        # `_at()` maps EVERY currently-registered engine to `ENDPOINT` --
        # without pinning the registry, `discover()` would also call the
        # real (unmocked) comfyui `httpx.get` against a nonexistent host.
        with patch.dict("models.contracts.engines.ENGINES", {"ollama": ENGINES["ollama"]}, clear=True):
            rows = discover(_at(), list(ModelConnection.objects.all()))

        row = next(r for r in rows if r.model_id == "llama3.1:8b")
        assert row.engine == "ollama"
        assert row.installed is True

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_a_second_registered_engines_installed_models_merge_in_tagged_with_its_own_name(
        self, mock_get
    ):
        mock_get.side_effect = httpx.ConnectError("connection refused")
        fake_model = MagicMock(
            model_id="stub-model:latest",
            size=1,
            loaded=False,
            capabilities=(),
            embedding_length=None,
        )
        stub = _stub_engine(models=[fake_model])
        engines.register(stub)
        try:
            rows = discover(_at(), list(ModelConnection.objects.all()))
        finally:
            del engines.ENGINES["stubengine"]

        row = next(r for r in rows if r.model_id == "stub-model:latest")
        assert row.engine == "stubengine"
        assert row.installed is True

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_one_engines_list_installed_failure_does_not_hide_anothers_models(self, mock_get):
        mock_get.side_effect = fake_ollama_get(
            tags_models=[{"name": "llama3.1:8b", "size": 123}],
        )
        broken = MagicMock()
        broken.name = "broken"
        broken.list_installed.side_effect = RuntimeError("boom")
        engines.register(broken)
        try:
            rows = discover(_at(), list(ModelConnection.objects.all()))
        finally:
            del engines.ENGINES["broken"]

        row = next(r for r in rows if r.model_id == "llama3.1:8b")
        assert row.installed is True


# --- scan_for_servers -------------------------------------------------------


class TestScanForServers:
    """The user-triggered sweep of each registered engine's well-known
    addresses. `models/registry/tests/test_views_machine_add_and_dropdowns.py::
    TestServerScanNeverRunsOnGet` covers that this is NEVER invoked on a
    page load -- these tests exercise the generic loop itself, in
    isolation from the view layer."""

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_anti_anchoring_generic_loop_discovers_a_registered_stub_engine(self, mock_get):
        """THE anti-anchoring proof: a fake engine, registered with its own
        (non-Ollama) port and name, is found purely by the generic loop
        reading THAT engine's own `well_known_ports`/`.name`/`is_healthy`/
        `list_installed` -- no change to `scan_for_servers` itself.

        Discovery isolation (whole-branch review, final wave): scoped to
        ollama + the stub via the same `patch.dict(..., clear=True)`
        pattern as `test_checked_list_records_every_non_default_candidate_
        for_ollama` below -- otherwise the production registry's other
        real engines (ComfyUI, whisper) are ALSO scanned by the generic
        loop and their `is_healthy` opens a real socket against
        `localhost`/`127.0.0.1` at their own well-known ports."""
        mock_get.side_effect = httpx.ConnectError("connection refused")
        stub = _stub_engine(models=[MagicMock(), MagicMock()])
        with patch.dict("models.contracts.engines.ENGINES", {"ollama": ENGINES["ollama"]}, clear=True):
            engines.register(stub)
            try:
                result = scan_for_servers(ENDPOINT)
            finally:
                del engines.ENGINES["stubengine"]

        hit = next(h for h in result.hits if h.engine == "stubengine")
        assert hit.endpoint == "http://localhost:9999"
        assert hit.model_count == 2

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_per_engine_failure_is_isolated(self, mock_get):
        """Discovery isolation: same scoping as the anti-anchoring test
        above -- only ollama plus the two stubs this test registers, so
        ComfyUI/whisper's `is_healthy` never opens a real socket."""
        mock_get.side_effect = httpx.ConnectError("connection refused")
        broken = MagicMock()
        broken.name = "broken"
        broken.well_known_ports = (1234,)
        broken.is_healthy.side_effect = RuntimeError("boom")
        working = _stub_engine(name="working", port=4321, models=[MagicMock()])
        with patch.dict("models.contracts.engines.ENGINES", {"ollama": ENGINES["ollama"]}, clear=True):
            engines.register(broken)
            engines.register(working)
            try:
                result = scan_for_servers(ENDPOINT)
            finally:
                del engines.ENGINES["broken"]
                del engines.ENGINES["working"]

        assert any(hit.engine == "working" for hit in result.hits)
        assert not any(hit.engine == "broken" for hit in result.hits)

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_hits_are_labeled_with_the_claiming_engines_name(self, mock_get):
        """Discovery isolation: same scoping as the two tests above."""
        mock_get.side_effect = httpx.ConnectError("connection refused")
        stub = _stub_engine(name="another-engine", port=8888, models=[MagicMock()])
        with patch.dict("models.contracts.engines.ENGINES", {"ollama": ENGINES["ollama"]}, clear=True):
            engines.register(stub)
            try:
                result = scan_for_servers(ENDPOINT)
            finally:
                del engines.ENGINES["another-engine"]

        stub_hit = next(h for h in result.hits if h.endpoint == "http://localhost:8888")
        assert stub_hit.engine == "another-engine"

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_default_endpoint_is_skipped_not_reprobed(self, mock_get):
        """Discovery isolation: scoped to ollama alone -- `scan_for_servers`
        with no registry restriction also scans the production registry's
        other real engines (ComfyUI, whisper), whose `is_healthy` opens a
        real socket against `localhost`/`127.0.0.1` at their own
        well-known ports."""
        mock_get.side_effect = httpx.ConnectError("connection refused")
        default = "http://localhost:11434"

        with patch.dict("models.contracts.engines.ENGINES", {"ollama": ENGINES["ollama"]}, clear=True):
            result = scan_for_servers(default)

        assert norm_endpoint(default) not in {norm_endpoint(u) for u in result.checked}
        assert not any(norm_endpoint(hit.endpoint) == norm_endpoint(default) for hit in result.hits)

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_trailing_slash_default_endpoint_is_still_skipped(self, mock_get):
        """Discovery isolation: same scoping as the test above."""
        mock_get.side_effect = httpx.ConnectError("connection refused")

        with patch.dict("models.contracts.engines.ENGINES", {"ollama": ENGINES["ollama"]}, clear=True):
            result = scan_for_servers("http://localhost:11434/")

        assert "http://localhost:11434" not in result.checked

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_checked_list_records_every_non_default_candidate_for_ollama(self, mock_get):
        mock_get.side_effect = httpx.ConnectError("connection refused")

        # Pinned explicitly (a second adapter, e.g. ComfyUI, is also
        # registered by default now) rather than assumed of the production
        # registry -- this count only holds with exactly ollama registered.
        with patch.dict("models.contracts.engines.ENGINES", {"ollama": ENGINES["ollama"]}, clear=True):
            result = scan_for_servers(ENDPOINT)

        # COMMON_HOSTS x 1 port, plus the engine's own name as a
        # compose-service hostname x 1 port -- with only ollama registered.
        assert len(result.checked) == (len(COMMON_HOSTS) + 1) * 1
        assert "http://localhost:11434" in result.checked
        assert "http://127.0.0.1:11434" in result.checked
        assert "http://host.docker.internal:11434" in result.checked
        assert "http://ollama:11434" in result.checked

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_nothing_found_still_returns_the_checked_hint(self, mock_get):
        """Discovery isolation (whole-branch review, final wave): scoped
        to ollama alone, same `patch.dict(..., clear=True)` pattern as
        `test_checked_list_records_every_non_default_candidate_for_ollama`
        above. Unscoped, this reached the real network -- the production
        registry's ComfyUI and whisper engines are ALSO scanned by the
        generic loop, and their `is_healthy` opens a real socket against
        `localhost`/`127.0.0.1` at their own well-known ports (ComfyUI
        :8188, whisper :8080), which can find an actually-listening
        service on a box running either one and turn `result.hits == []`
        false."""
        mock_get.side_effect = httpx.ConnectError("connection refused")

        with patch.dict("models.contracts.engines.ENGINES", {"ollama": ENGINES["ollama"]}, clear=True):
            result = scan_for_servers(ENDPOINT)

        assert result.hits == []
        assert result.checked  # the "here's what was ruled out" hint has content


# --- discover(): per-engine endpoints (spec §4.6 / D11) -------------------


@pytest.mark.django_db
class TestDiscoverPerEngineEndpoints:
    """`discover()` takes a per-engine endpoint map: each engine is polled
    only at ITS OWN addresses. Before this, every engine was polled at the
    single Ollama endpoint, so a ComfyUI checkpoint could never appear."""

    def test_each_engine_is_polled_only_at_its_own_endpoints(self):
        alpha = _stub_engine(name="alpha", models=[InstalledModel(model_id="a-model")])
        beta = _stub_engine(name="beta", models=[InstalledModel(model_id="b-model")])
        with patch.dict(engines.ENGINES, {"alpha": alpha, "beta": beta}, clear=True):
            rows = discover(
                {"alpha": ["http://alpha:1111"], "beta": ["http://beta:2222"]},
                list(ModelConnection.objects.all()),
            )

        alpha.list_installed.assert_called_once_with("http://alpha:1111")
        beta.list_installed.assert_called_once_with("http://beta:2222")
        by_id = {(row.engine, row.model_id): row for row in rows}
        assert by_id[("alpha", "a-model")].endpoint == "http://alpha:1111"
        assert by_id[("beta", "b-model")].endpoint == "http://beta:2222"

    def test_an_engine_absent_from_the_map_is_not_polled(self):
        alpha = _stub_engine(name="alpha", models=[InstalledModel(model_id="a-model")])
        with patch.dict(engines.ENGINES, {"alpha": alpha}, clear=True):
            rows = discover({}, list(ModelConnection.objects.all()))

        alpha.list_installed.assert_not_called()
        assert [row for row in rows if row.installed] == []

    def test_one_unreachable_endpoint_does_not_hide_the_others(self):
        alpha = _stub_engine(name="alpha")
        alpha.list_installed.side_effect = [
            httpx.ConnectError("down"),
            [InstalledModel(model_id="a-model")],
        ]
        with patch.dict(engines.ENGINES, {"alpha": alpha}, clear=True):
            rows = discover(
                {"alpha": ["http://dead:1111", "http://alive:1111"]},
                list(ModelConnection.objects.all()),
            )

        installed = [row for row in rows if row.installed]
        assert [row.model_id for row in installed] == ["a-model"]
        assert installed[0].endpoint == "http://alive:1111"

    @patch("models.contracts.engines.ollama.httpx.get")
    def test_one_model_at_two_endpoints_keeps_the_first_and_unions_loaded(self, mock_get):
        """A model installed at TWO endpoints of one engine merges into one
        row: the FIRST sighting's endpoint (and facts) wins -- a later one
        must not silently retarget the row, since `endpoint` is what "Add
        to registered" registers against -- but `loaded` is unioned,
        because a model loaded at either address is loaded right now."""
        first_endpoint = "http://first:11434"
        second_endpoint = "http://second:11434"
        at_first = _fake_get([{"name": "llama3.1:8b", "size": 100}])
        at_second = _fake_get(
            [{"name": "llama3.1:8b", "size": 999}],
            ps_models=[{"name": "llama3.1:8b", "size": 4242}],
        )
        mock_get.side_effect = lambda url, timeout: (
            at_first if url.startswith(first_endpoint) else at_second
        )(url, timeout)

        rows = discover(
            {"ollama": [first_endpoint, second_endpoint]},
            list(ModelConnection.objects.all()),
        )

        row = next(row for row in rows if row.model_id == "llama3.1:8b")
        assert row.endpoint == first_endpoint
        assert row.size == 100  # the first sighting's facts survive
        assert row.loaded is True
        assert row.loaded_size == 4242

    def test_duplicate_endpoints_are_polled_once(self):
        alpha = _stub_engine(name="alpha", models=[InstalledModel(model_id="a-model")])
        with patch.dict(engines.ENGINES, {"alpha": alpha}, clear=True):
            discover(
                {"alpha": ["http://alpha:1111", "http://alpha:1111/"]},
                list(ModelConnection.objects.all()),
            )

        assert alpha.list_installed.call_count == 1

    def test_a_row_carries_the_loader_that_reported_the_model(self):
        """The console must be able to tell a single-file checkpoint from a
        diffusion-model file that needs companions, and the loader node is
        the only honest signal for that."""
        engine = _stub_engine(
            name="comfyui",
            models=[
                InstalledModel(
                    model_id="family-a-Q4_K_S.gguf",
                    capabilities=("image-generation",),
                    loader="UnetLoaderGGUF",
                )
            ],
        )
        with patch.dict(engines.ENGINES, {"comfyui": engine}, clear=True):
            rows = discover({"comfyui": ["http://comfy.test:8188"]}, [])

        row = next(r for r in rows if r.model_id == "family-a-Q4_K_S.gguf")
        assert row.loader == "UnetLoaderGGUF"

    def test_an_adapter_that_reports_no_loader_degrades_to_blank(self):
        """`loader` is read with `getattr`, like `loaded_size` before it, so
        a third-party adapter that predates the field is never an
        AttributeError."""
        engine = _stub_engine(
            name="comfyui",
            models=[InstalledModel(model_id="sdxl.safetensors", capabilities=("image-generation",))],
        )
        with patch.dict(engines.ENGINES, {"comfyui": engine}, clear=True):
            rows = discover({"comfyui": ["http://comfy.test:8188"]}, [])

        row = next(r for r in rows if r.model_id == "sdxl.safetensors")
        assert row.loader == ""
