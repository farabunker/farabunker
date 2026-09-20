"""Integration test for W5 hybrid keyword+vector search (ADR 0014 §18)
against the REAL pgvector test database -- the one test in the item that
proves the feature rather than the plumbing (every other W5 test mocks the
store). Builds a real `PGVectorStore` directly (bypassing `tools.rag.
index.get_vector_store`, which resolves `rag.embed` and reads `RagSettings`
-- neither is what's under test here) at a tiny `embed_dim` so the
hand-written embeddings below are trivial to reason about.

Unlike `tools.rag.tests.test_index`'s `TestLiveEmbedDim`/`TestDropChunkTable`
(which touch the chunk table through Django's own connection, inside the
test's transaction, and so are rolled back for free), `PGVectorStore` opens
its OWN SQLAlchemy engine/connection -- its `CREATE TABLE`/`INSERT` calls are
real, separate commits, not part of pytest-django's per-test transaction.
The table is therefore torn down explicitly (`rag_index.drop_chunk_table()`,
via Django's connection -- a plain `DROP TABLE IF EXISTS`, which works fine
against a table committed by a different connection) in a `finally`, not
relied on to vanish via rollback.

W5 review MAJOR 1: that teardown must actually survive test end. Plain
`@pytest.mark.django_db` wraps the test in a transaction pytest-django
rolls back afterward -- so a Django-connection `DROP TABLE IF EXISTS` run
inside it is rolled back right along with everything else, while
`PGVectorStore`'s own separate connection already committed the `CREATE
TABLE` for real. The table then survives the session, and the next file
to touch `data_rag_chunks` through a real transaction (`test_index.py`'s
`TestLiveEmbedDim`/`TestDropChunkTable`) finds a table it didn't expect.
`django_db(transaction=True)` gives this test a REAL (non-rolled-back)
transaction instead, so the explicit `DROP TABLE` in `finally` actually
commits.
"""
from llama_index.core.schema import TextNode
from llama_index.core.vector_stores.types import VectorStoreQuery, VectorStoreQueryMode
from llama_index.vector_stores.postgres import PGVectorStore

import pytest

from tools.rag import index as rag_index

EMBED_DIM = 4
# Orthogonal to the query embedding -- pgvector cosine similarity
# (`1 - cosine_distance`) between these is 0.0, the worst possible dense
# score -- node A can never surface in a `similarity_top_k=1` DEFAULT-mode
# query no matter how the rest of the library is shaped.
QUERY_EMBEDDING = [1.0, 0.0, 0.0, 0.0]
FAR_EMBEDDING = [0.0, 1.0, 0.0, 0.0]
# Identical to the query embedding -- cosine similarity 1.0, the best
# possible dense score -- this is what "semantically near" means here: a
# hand-written stand-in for a real embedding model's output, not a claim
# about the text itself.
NEAR_EMBEDDING = [1.0, 0.0, 0.0, 0.0]

KEYWORD_TOKEN = "Farabunker"


@pytest.mark.django_db(transaction=True)
class TestHybridSearchIntegration:
    def _build_hybrid_store(self) -> PGVectorStore:
        params = rag_index._db_params()
        return PGVectorStore.from_params(
            database=params["database"],
            host=params["host"],
            password=params["password"],
            port=params["port"],
            user=params["user"],
            table_name=rag_index.VECTOR_TABLE_NAME,
            embed_dim=EMBED_DIM,
            hybrid_search=True,
            use_jsonb=True,
            text_search_config=rag_index.TEXT_SEARCH_CONFIG,
        )

    def test_a_keyword_only_match_surfaces_in_hybrid_mode(self):
        """The one test that proves the feature rather than the plumbing
        (W5 measurement plan / test list): a rare literal token absent from
        the query embedding's dense neighborhood surfaces under HYBRID mode
        and NOT under DEFAULT mode, in the same test, as the direct
        contrast."""
        assert rag_index.live_store_shape() is None  # clean slate
        store = self._build_hybrid_store()
        try:
            keyword_node = TextNode(
                id_="node-keyword-only",
                text=f"An internal engineering memo mentions {KEYWORD_TOKEN} in passing.",
                embedding=FAR_EMBEDDING,
                metadata={"file_id": "1"},
            )
            semantic_node = TextNode(
                id_="node-semantic-near",
                text="A completely unrelated sentence about garden vegetables.",
                embedding=NEAR_EMBEDDING,
                metadata={"file_id": "2"},
            )
            store.add([keyword_node, semantic_node])

            assert rag_index.live_store_shape() == {"hybrid": True, "jsonb": True}

            hybrid_result = store.query(
                VectorStoreQuery(
                    query_embedding=QUERY_EMBEDDING,
                    query_str=KEYWORD_TOKEN,
                    mode=VectorStoreQueryMode.HYBRID,
                    similarity_top_k=1,
                    sparse_top_k=5,
                )
            )
            assert "node-keyword-only" in hybrid_result.ids

            # The contrast: the SAME query embedding, DEFAULT (dense-only)
            # mode -- the keyword-only node cannot surface here, at any
            # `similarity_top_k`, because it was written maximally far from
            # the query embedding on purpose.
            dense_result = store.query(
                VectorStoreQuery(
                    query_embedding=QUERY_EMBEDDING,
                    mode=VectorStoreQueryMode.DEFAULT,
                    similarity_top_k=1,
                )
            )
            assert "node-keyword-only" not in dense_result.ids
            assert "node-semantic-near" in dense_result.ids
        finally:
            rag_index.drop_chunk_table()
            # `PGVectorStore` opens its OWN SQLAlchemy engine/connection,
            # separate from Django's -- left open, it lingers past this
            # test and blocks pytest-django's end-of-session `DROP DATABASE
            # test_...` (a real "database is being accessed by other
            # users" failure, not a cosmetic warning). Dispose it
            # explicitly rather than relying on garbage collection timing.
            if store._engine is not None:
                store._engine.dispose()
