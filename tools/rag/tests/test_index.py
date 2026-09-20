"""Unit tests for tools/rag/index.py::delete_chunks_for_document,
::get_vector_store, ::live_embed_dim, and ::drop_chunk_table (ADR 0008,
ADR 0010).

The pgvector store (LlamaIndex's PGVectorStore) is always mocked -- these
tests never touch a real vector index. `live_embed_dim`/`drop_chunk_table`
work through Django's own DB connection against the real Postgres+pgvector
test database (the same one every django_db test uses); their tests create
the `data_rag_chunks` table with raw SQL rather than a live PGVectorStore.
This is the one shared helper module that ingest re-ingest cleanup,
services.delete_document, and services.reencode_all all delegate their
vector-store teardown to, so its filter shape, its "never raise"
defensiveness, and its schema introspection are covered here rather than in
each caller's tests.
"""
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection

from models.contracts.bindings import ResolvedModel
from tools.rag import index as rag_index
from tools.rag.models import RagSettings


class TestDeleteChunksForDocument:
    @patch("tools.rag.index.get_vector_store")
    def test_deletes_nodes_filtered_by_file_id(self, mock_get_vector_store):
        mock_vector_store = MagicMock()
        mock_get_vector_store.return_value = mock_vector_store

        rag_index.delete_chunks_for_document(42)

        mock_vector_store.delete_nodes.assert_called_once()
        _, call_kwargs = mock_vector_store.delete_nodes.call_args
        filters = call_kwargs["filters"]
        assert filters.filters[0].key == "file_id"
        # Always the string form of the id, matching what ingest writes.
        assert filters.filters[0].value == "42"

    @patch("tools.rag.index.get_vector_store")
    def test_store_failure_is_swallowed_not_raised(self, mock_get_vector_store):
        # e.g. the rag_chunks table doesn't exist yet, or the store is
        # unreachable -- must not block the caller (re-ingest / delete).
        mock_get_vector_store.side_effect = Exception("rag_chunks table missing")

        # Should not raise.
        rag_index.delete_chunks_for_document(7)

    @patch("tools.rag.index.get_vector_store")
    def test_delete_nodes_failure_is_swallowed(self, mock_get_vector_store):
        mock_vector_store = MagicMock()
        mock_vector_store.delete_nodes.side_effect = Exception("boom")
        mock_get_vector_store.return_value = mock_vector_store

        # Should not raise even if the delete itself fails.
        rag_index.delete_chunks_for_document(9)

    @patch("tools.rag.index.get_vector_store")
    def test_uses_a_pre_built_store_instead_of_building_its_own(self, mock_get_vector_store):
        """A pre-built store (reencode_all's bulk loop, threading one store
        through the whole run -- audit E1) is reused verbatim; get_vector_store()
        must not be called at all."""
        provided_store = MagicMock()

        rag_index.delete_chunks_for_document(42, vector_store=provided_store)

        mock_get_vector_store.assert_not_called()
        provided_store.delete_nodes.assert_called_once()


class TestGetVectorStore:
    """`embed_dim` is sourced from the `rag.embed` binding's resolved
    dimension, so a different-dimension embedding-model swap actually
    reshapes the pgvector table. The env provider always supplies a
    dimension (from `settings.EMBED_DIM`), so a resolved `embed_dim=None`
    can only be a DB-bound connection with no recorded dimension -- that's a
    fail-fast `ValueError`, not a `settings.EMBED_DIM` fallback (D1/E2 fix,
    ADR 0010)."""

    @patch("tools.rag.index.desired_store_shape")
    @patch("tools.rag.index.live_store_shape")
    @patch("tools.rag.index.PGVectorStore")
    @patch("tools.rag.index.resolve")
    def test_uses_resolved_embed_dim_when_available(
        self, mock_resolve, mock_pgvector_store, mock_live_shape, mock_desired_shape
    ):
        mock_resolve.return_value = ResolvedModel(
            "ollama", "mxbai-embed-large", "http://localhost:11434", embed_dim=1024
        )
        mock_live_shape.return_value = None
        mock_desired_shape.return_value = {"hybrid": False, "jsonb": False}

        rag_index.get_vector_store()

        mock_resolve.assert_called_once_with("rag.embed")
        _, call_kwargs = mock_pgvector_store.from_params.call_args
        assert call_kwargs["embed_dim"] == 1024

    @patch("tools.rag.index.PGVectorStore")
    @patch("tools.rag.index.resolve")
    def test_raises_when_resolved_binding_has_no_embed_dim(self, mock_resolve, mock_pgvector_store):
        """Rewritten from the old settings.EMBED_DIM-fallback test: an
        unknown dimension is no longer silently papered over -- it's a
        clear, catchable ValueError raised before any store is built. No
        shape lookup needed -- this raises before `live_store_shape()` is
        ever called (verified below), so this test needs no DB access."""
        mock_resolve.return_value = ResolvedModel(
            "ollama", "some-model", "http://localhost:11434", embed_dim=None
        )

        with pytest.raises(ValueError, match="some-model"):
            rag_index.get_vector_store()

        mock_pgvector_store.from_params.assert_not_called()

    @patch("tools.rag.index.desired_store_shape")
    @patch("tools.rag.index.live_store_shape")
    @patch("tools.rag.index.PGVectorStore")
    @patch("tools.rag.index.resolve")
    def test_uses_the_desired_shape_when_the_table_does_not_exist(
        self, mock_resolve, mock_pgvector_store, mock_live_shape, mock_desired_shape
    ):
        """W5 (ADR 0014 §18): `live_store_shape() is None` (nothing ingested
        yet) means the table is about to be created fresh, so the DESIRED
        shape (today's `hybrid_search` toggle) is what's built."""
        mock_resolve.return_value = ResolvedModel(
            "ollama", "mxbai-embed-large", "http://localhost:11434", embed_dim=1024
        )
        mock_live_shape.return_value = None
        mock_desired_shape.return_value = {"hybrid": True, "jsonb": True}

        rag_index.get_vector_store()

        _, call_kwargs = mock_pgvector_store.from_params.call_args
        assert call_kwargs["hybrid_search"] is True
        assert call_kwargs["use_jsonb"] is True
        assert call_kwargs["text_search_config"] == rag_index.TEXT_SEARCH_CONFIG

    @patch("tools.rag.index.desired_store_shape")
    @patch("tools.rag.index.live_store_shape")
    @patch("tools.rag.index.PGVectorStore")
    @patch("tools.rag.index.resolve")
    def test_uses_the_live_shape_when_the_table_exists(
        self, mock_resolve, mock_pgvector_store, mock_live_shape, mock_desired_shape
    ):
        """This is the safety gate (W5 review M2a): the store never
        describes a table that isn't actually there. The toggle is ON, but
        the live table is non-hybrid/json -- the store built must match
        the LIVE table, not the toggle."""
        mock_resolve.return_value = ResolvedModel(
            "ollama", "mxbai-embed-large", "http://localhost:11434", embed_dim=1024
        )
        mock_live_shape.return_value = {"hybrid": False, "jsonb": False}
        mock_desired_shape.return_value = {"hybrid": True, "jsonb": True}

        rag_index.get_vector_store()

        _, call_kwargs = mock_pgvector_store.from_params.call_args
        assert call_kwargs["hybrid_search"] is False
        assert call_kwargs["use_jsonb"] is False

    @patch("tools.rag.index.desired_store_shape")
    @patch("tools.rag.index.live_store_shape")
    @patch("tools.rag.index.PGVectorStore")
    @patch("tools.rag.index.resolve")
    def test_never_declares_jsonb_against_a_json_column(
        self, mock_resolve, mock_pgvector_store, mock_live_shape, mock_desired_shape
    ):
        """W5 review N3: the M2a invariant asserted directly, with the live
        flags DISAGREEING (`hybrid` True, `jsonb` False) -- a fixture where
        both flags are EQUAL passes identically whether the code reads
        `shape["jsonb"]` or `shape["hybrid"]`, so it would assert nothing
        about the bug this test exists for (an earlier draft built
        `desired` with only a `hybrid` key and passed `use_jsonb=
        shape["hybrid"]`, so `live["jsonb"]` was never actually read)."""
        mock_resolve.return_value = ResolvedModel(
            "ollama", "mxbai-embed-large", "http://localhost:11434", embed_dim=1024
        )
        mock_live_shape.return_value = {"hybrid": True, "jsonb": False}
        mock_desired_shape.return_value = {"hybrid": True, "jsonb": True}

        rag_index.get_vector_store()

        _, call_kwargs = mock_pgvector_store.from_params.call_args
        assert call_kwargs["hybrid_search"] is True
        assert call_kwargs["use_jsonb"] is False


class TestDisposingVectorStore:
    def test_a_borrowed_vector_store_is_disposed_when_the_caller_is_done(self):
        """C-06. `PGVectorStore.from_params` opens its own SQLAlchemy engine
        and pool. The package's `close()` is `async` and unreachable from this
        synchronous path, so nothing ever disposed one -- per `/rag/search`
        request and per `rag.ask`/`rag.search` tool call.
        `test_index_hybrid_integration.py:130` already reaches
        `store._engine.dispose()` by hand, in a `finally:`, for exactly this
        reason; this makes that the production idiom instead of a test's
        workaround."""
        with patch.object(rag_index, "get_vector_store") as build:
            store = build.return_value
            with rag_index.disposing_vector_store() as borrowed:
                assert borrowed is store
        store._engine.dispose.assert_called_once_with()

    def test_the_store_is_disposed_even_when_the_caller_raises(self):
        """The half that matters under load: a retrieval that blows up must
        not leak the pool it opened."""
        with patch.object(rag_index, "get_vector_store") as build:
            store = build.return_value
            with pytest.raises(RuntimeError):
                with rag_index.disposing_vector_store():
                    raise RuntimeError("boom")
        store._engine.dispose.assert_called_once_with()

    def test_a_store_passed_in_is_not_built_and_not_disposed(self):
        """The `vector_store` parameter is for a caller that was handed a
        store it does not own (e.g. a bulk caller threading one store
        through several calls) -- this manager must neither build a second
        one nor dispose the one it was lent."""
        provided_store = MagicMock()
        with patch.object(rag_index, "get_vector_store") as build:
            with rag_index.disposing_vector_store(provided_store) as borrowed:
                assert borrowed is provided_store
            build.assert_not_called()
        provided_store._engine.dispose.assert_not_called()

    def test_a_store_with_no_engine_attribute_is_not_an_error(self):
        """Some callers hand back a bare stub (e.g. a `SimpleNamespace`)
        with no `_engine` at all in tests -- the `None` guard means that's
        a no-op, not an `AttributeError`."""

        class BareStore:
            pass

        with patch.object(rag_index, "get_vector_store", return_value=BareStore()):
            with rag_index.disposing_vector_store():
                pass


def test_the_vector_store_still_exposes_the_private_engine_we_dispose():
    """PIN-1 (dependency audit). `disposing_vector_store` reaches for
    `PGVectorStore._engine` because the package's own `close()` is a
    coroutine and this path is synchronous. `getattr(store, "_engine",
    None)` would swallow the attribute's disappearance silently and we
    would leak the SQLAlchemy pool again -- the exact regression that
    function was written to fix. This test is the loud version of that
    check, and the `<0.10` ceiling in requirements.txt is why it should
    not fire unexpectedly."""
    import inspect

    from llama_index.vector_stores.postgres import base as pg_base

    assert "self._engine" in inspect.getsource(pg_base.PGVectorStore), (
        "PGVectorStore no longer sets _engine; tools/rag/index.py must be revisited"
    )


class TestGetIndex:
    """`get_index()` accepts an optional pre-built `vector_store` (audit E1
    -- `reencode_all`'s bulk loop builds one store for the whole run and
    threads it through instead of each document re-triggering
    `get_vector_store()`), and an optional `embed_model` (T5 -- an
    already-built LlamaIndex embedding model, threaded straight through to
    `VectorStoreIndex.from_vector_store` so the index never reads the
    global `llama_index.core.Settings`). `get_index` no longer configures
    anything itself -- there is no `gateway.configure_settings` to call
    anymore (T5, execution-queue refactor)."""

    @patch("tools.rag.index.VectorStoreIndex")
    @patch("tools.rag.index.get_vector_store")
    def test_builds_its_own_store_by_default(self, mock_get_vector_store, mock_vsi):
        rag_index.get_index()

        mock_get_vector_store.assert_called_once_with()
        mock_vsi.from_vector_store.assert_called_once_with(mock_get_vector_store.return_value, embed_model=None)

    @patch("tools.rag.index.VectorStoreIndex")
    @patch("tools.rag.index.get_vector_store")
    def test_embed_model_is_threaded_to_from_vector_store(self, mock_get_vector_store, mock_vsi):
        """An already-built embedder (e.g. from
        `models.contracts.gateway.get_embed_model_for`) is passed straight
        through to `VectorStoreIndex.from_vector_store` -- the seam that
        keeps the global `Settings.embed_model` out of the retrieval path
        entirely."""
        fake_embed_model = MagicMock()

        rag_index.get_index(embed_model=fake_embed_model)

        mock_vsi.from_vector_store.assert_called_once_with(
            mock_get_vector_store.return_value, embed_model=fake_embed_model
        )

    @patch("tools.rag.index.VectorStoreIndex")
    @patch("tools.rag.index.get_vector_store")
    def test_reuses_a_pre_built_store_when_given_one(self, mock_get_vector_store, mock_vsi):
        provided_store = MagicMock()

        rag_index.get_index(vector_store=provided_store)

        mock_get_vector_store.assert_not_called()
        mock_vsi.from_vector_store.assert_called_once_with(provided_store, embed_model=None)


def _create_chunk_table(dim: int, *, jsonb: bool = False, hybrid: bool = False) -> None:
    """Create a minimal stand-in for PGVectorStore's `data_rag_chunks` table
    with a `vector(dim)` embedding column (the pgvector extension is enabled
    by migration 0002), a `metadata_` column (always present on the real
    table -- `json` by default, `jsonb` when `jsonb=True`), and, when
    `hybrid=True`, a `text_search_tsv` column standing in for the real
    table's GENERATED tsvector column (W5, ADR 0014 §18) -- `live_store_
    shape` only checks the column's PRESENCE, not that it's actually
    generated, so a plain `tsvector` column is a faithful enough stand-in."""
    metadata_type = "jsonb" if jsonb else "json"
    with connection.cursor() as cursor:
        cursor.execute(
            f'CREATE TABLE "{rag_index.LIVE_TABLE_NAME}" '
            f"(id bigserial PRIMARY KEY, embedding vector({dim}), metadata_ {metadata_type})"
        )
        if hybrid:
            cursor.execute(f'ALTER TABLE "{rag_index.LIVE_TABLE_NAME}" ADD COLUMN text_search_tsv tsvector')


@pytest.mark.django_db
class TestLiveEmbedDim:
    """`live_embed_dim` reports what the actual Postgres schema says the
    chunk table's vector width is -- the input to `reencode_all`'s
    drop-vs-in-place decision -- so it's tested against the real test
    database, not a mock."""

    def test_missing_table_reports_none(self):
        assert rag_index.live_embed_dim() is None

    def test_reports_the_vector_columns_declared_dimension(self):
        _create_chunk_table(768)

        assert rag_index.live_embed_dim() == 768

    def test_reports_a_non_default_dimension(self):
        _create_chunk_table(1024)

        assert rag_index.live_embed_dim() == 1024


@pytest.mark.django_db
class TestLiveStoreShape:
    """`live_store_shape` reports what the actual Postgres schema says the
    chunk table's SHAPE is (W5, ADR 0014 §18) -- `hybrid_search` (the
    generated `text_search_tsv` column's presence) and `use_jsonb` (the
    `metadata_` column's Postgres type) -- against the real test database,
    the same convention as `TestLiveEmbedDim` above."""

    def test_missing_table_reports_none(self):
        assert rag_index.live_store_shape() is None

    def test_reports_a_plain_dense_only_json_table(self):
        _create_chunk_table(768, jsonb=False, hybrid=False)

        assert rag_index.live_store_shape() == {"hybrid": False, "jsonb": False}

    def test_reports_a_hybrid_jsonb_table(self):
        _create_chunk_table(768, jsonb=True, hybrid=True)

        assert rag_index.live_store_shape() == {"hybrid": True, "jsonb": True}

    def test_reports_disagreeing_flags(self):
        """The shape rule reads the two flags independently -- a table can
        be hybrid with a json (not jsonb) metadata_ column (W5 review N3's
        own fixture shape)."""
        _create_chunk_table(768, jsonb=False, hybrid=True)

        assert rag_index.live_store_shape() == {"hybrid": True, "jsonb": False}


@pytest.mark.django_db
class TestDesiredStoreShape:
    """`desired_store_shape` is what today's `RagSettings.hybrid_search`
    toggle asks for -- `use_jsonb` tied to the SAME toggle (W5 review M2b),
    never its own independent knob."""

    def test_toggle_off_is_todays_shape(self):
        """M2b's guarantee, asserted directly: an untouched install (the
        toggle's own default) can never be told its shape mismatches --
        both keys come back `False`, matching a plain dense-only table."""
        RagSettings.objects.create(pk=1, hybrid_search=False)

        assert rag_index.desired_store_shape() == {"hybrid": False, "jsonb": False}

    def test_toggle_on_wants_both_hybrid_and_jsonb(self):
        RagSettings.objects.create(pk=1, hybrid_search=True)

        assert rag_index.desired_store_shape() == {"hybrid": True, "jsonb": True}


@pytest.mark.django_db
class TestDropChunkTable:
    def test_drops_the_live_table(self):
        _create_chunk_table(768)
        assert rag_index.live_embed_dim() == 768

        rag_index.drop_chunk_table()

        assert rag_index.live_embed_dim() is None

    def test_is_a_no_op_when_the_table_does_not_exist(self):
        # DROP TABLE IF EXISTS: must not raise on a store that was never
        # created (nothing prose ever ingested).
        rag_index.drop_chunk_table()

        assert rag_index.live_embed_dim() is None


def test_the_index_module_exposes_no_unused_storage_context_builder():
    """C-35. `get_storage_context` had no caller in this repo or on any
    open branch. `get_vector_store()` is the one door to the store."""
    assert not hasattr(rag_index, "get_storage_context")
