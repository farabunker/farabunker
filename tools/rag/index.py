"""
LlamaIndex <-> pgvector wiring for the RAG module.

This is the module's `vector:read` / `vector:write` seam onto the core Vector
Store service (Postgres + pgvector, ADR 0004). LlamaIndex's PGVectorStore
owns and manages its own table (`rag_chunks`) directly via SQLAlchemy — it is
deliberately NOT a Django model (see tools/rag/models.py docstring).

Connection params are derived from the same DATABASE_URL used by Django, so
there is one source of truth for "where is the database" (ADR 0006).
"""
import logging
from contextlib import contextmanager

from django.conf import settings
from django.db import connection as django_connection
from llama_index.core import VectorStoreIndex
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
from llama_index.vector_stores.postgres import PGVectorStore

from models.contracts.bindings import resolve
from models.contracts.roles import RAG_EMBED_ROLE
from tools.rag.models import RagSettings

logger = logging.getLogger(__name__)

VECTOR_TABLE_NAME = "rag_chunks"

# PGVectorStore prefixes the table name it is given with "data_" -- this is
# the actual Postgres table the chunks live in.
LIVE_TABLE_NAME = f"data_{VECTOR_TABLE_NAME}"

# W5 (ADR 0014 §18): the Postgres text-search dictionary the hybrid store's
# generated `text_search_tsv` column stems into. "english" is a
# POSTGRES-BUILTIN dictionary -- no download, no network, no operator setup
# -- which is exactly why it's the only one offered: the offline-first
# posture (ADR 0006) treats a language config that needed a downloaded
# dictionary as a hidden runtime dependency. Honest limitation, not a future
# knob: stemming is English-only, and non-English content still matches on
# exact tokens (no stemming, no synonym expansion) rather than failing
# outright. Baked into the generated column's own SQL expression at CREATE
# TABLE time (`_create_tables_if_not_exists`), so changing this later needs
# the same drop + `reencode_all` rebuild as `hybrid_search`/`use_jsonb`
# below, not a migration.
TEXT_SEARCH_CONFIG = "english"


def _db_params() -> dict:
    """Derive the pieces PGVectorStore.from_params wants from Django's parsed
    DATABASE_URL (config.settings.DATABASES["default"]), so there is one
    source of truth for connection info."""
    db = settings.DATABASES["default"]
    return {
        "database": db["NAME"],
        "host": db["HOST"] or "localhost",
        "password": db["PASSWORD"],
        "port": db["PORT"] or 5432,
        "user": db["USER"],
    }


def live_store_shape() -> dict | None:
    """Report the shape of the LIVE chunk table (`data_rag_chunks`) -- what
    Postgres actually has, not what today's toggle asks for -- as
    `{"hybrid": bool, "jsonb": bool}`, or `None` when the table doesn't
    exist yet (nothing prose has ever been ingested).

    `"hybrid"`: whether the generated `text_search_tsv` column
    (`PGVectorStore`'s own `hybrid_search=True` DDL) is present on the live
    table. `"jsonb"`: whether the live `metadata_` column's Postgres type is
    `jsonb` rather than `json`. Both are single-row `pg_attribute` lookups
    keyed on `LIVE_TABLE_NAME`.

    `None` (table missing) is explicitly NOT the same thing as "shape
    mismatch" to a caller like `tools.rag.services.reencode_all` -- there
    is nothing to drop, and the very next `get_vector_store()` call below
    creates the table fresh in the DESIRED shape, so `None` must never, by
    itself, trigger a rebuild (see that function's own `shape_changed`
    check).

    No `pg_indexes` check for `indexed_metadata_keys` here -- that option
    was evaluated and cut from this item (unverified benefit; see ADR 0014
    §18 and the item's own design notes), so there are no btree indexes a
    live/desired shape could ever disagree about. If a future EXPLAIN
    justifies reinstating it, this is where a `pg_indexes` lookup for it
    would join the two keys above.
    """
    with django_connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_attribute "
            "WHERE attrelid = to_regclass(%s) AND attname = 'text_search_tsv' "
            "AND NOT attisdropped), "
            "(SELECT atttypid = 'jsonb'::regtype FROM pg_attribute "
            "WHERE attrelid = to_regclass(%s) AND attname = 'metadata_' "
            "AND NOT attisdropped)",
            [LIVE_TABLE_NAME, LIVE_TABLE_NAME],
        )
        has_tsv, is_jsonb = cursor.fetchone()
    if is_jsonb is None:
        return None
    return {"hybrid": bool(has_tsv), "jsonb": bool(is_jsonb)}


def desired_store_shape() -> dict:
    """The shape today's `RagSettings.hybrid_search` toggle asks for --
    `{"hybrid": toggle, "jsonb": toggle}`. `use_jsonb` is bound to the SAME
    toggle, never set unconditionally (W5 review M2b): the only reason a
    JSONB `metadata_` column is wanted at all is that a rebuild is already
    happening for hybrid, and tying the two together means an install that
    has never touched `hybrid_search` always has `desired == live` --
    `reencode_all`'s drift rematerialize can never surprise-rebuild an
    operator who never asked for hybrid.

    ONE function, so `get_vector_store()` (below) and `tools.rag.
    services.reencode_all` can never independently build two different
    "desired" dicts that quietly disagree (W5 review N3)."""
    hybrid = RagSettings.get_solo().hybrid_search
    return {"hybrid": hybrid, "jsonb": hybrid}


def get_vector_store() -> PGVectorStore:
    """Return the pgvector-backed LlamaIndex vector store for document chunks.

    `embed_dim` is sourced from the `rag.embed` role's currently resolved
    binding (`models.contracts.bindings.resolve`). This is what makes a
    different-dimension embedding-model swap actually take effect on the
    pgvector table shape -- without it, the drift "rebuild" path
    (`models.registry.drift.run_rematerialize` ->
    `tools.rag.services.reencode_all`) would re-encode chunks but the
    store would still be pinned to the old dimension.

    A resolved binding with `embed_dim=None` is an incomplete choice: a
    DB-bound connection that never recorded a dimension, or an
    explicit `EMBED_MODEL` environment override set without its `EMBED_DIM`
    -- there are no baked defaults to fill either gap, and `settings.EMBED_DIM`
    is itself `None` unless an operator set it (ADR 0010 third amendment).
    There is no safe dimension to guess in that case -- a
    store built at a guessed width is corrupt-by-construction, every insert
    eventually failing a pgvector dimension mismatch -- so this fails fast
    instead of falling back to `settings.EMBED_DIM`. This function sits on
    the ordinary ask/ingest path too (`get_index`, `delete_chunks_for_document`),
    not just `reencode_all`'s rebuild path, so an unbound dimension now
    surfaces immediately as a clear, catchable error rather than silently
    building a wrong-width store and failing later at the DB layer.

    W5 (ADR 0014 §18) shape rule -- ONE rule, replacing three independent
    flags: the store built here ALWAYS describes the table that actually
    exists. `live_store_shape()` is `None` only when nothing has been
    ingested yet, in which case the table is about to be created fresh and
    `desired_store_shape()` (today's `hybrid_search` toggle) is exactly
    right; otherwise the LIVE shape wins, unconditionally, even if it
    disagrees with the current toggle -- a toggle flip changes nothing
    about what's actually queried until a re-encode (Inference -> rag.embed
    -> Re-encode) rebuilds the table, which is what makes the operator copy
    in `tools.rag.views._hybrid_search_update` literally true.
    Both `hybrid_search` and `use_jsonb` are read from that ONE resolved
    `shape` dict (never a second, independently-built one) so a JSONB-
    declared store can never be bound to a live `json` column (W5 review
    N3 -- an earlier draft built `desired` with only a `hybrid` key and
    passed `use_jsonb=shape["hybrid"]`, so `live["jsonb"]` was never
    actually read).

    Cost, stated honestly (W5 review M3/N2): this adds one `pg_attribute`
    lookup (`live_store_shape`) per call, alongside the `RagSettings.
    get_solo()` `desired_store_shape` pays when `live` is `None` -- neither
    memoized. A module-level memo of the live shape is UNSAFE here and
    deliberately not built: `drop_chunk_table()` runs in the WORKER process
    (the `rag.embed` rematerialize handler), so a memo cached in the WEB
    process would never see that drop -- an operator could enable hybrid,
    re-encode successfully, and every Ask would keep building a non-hybrid
    store (hybrid simply appearing not to work) until the web process
    restarted. Two cheap single-row reads on an already-open connection is
    the honest price for a value that must be read fresh across process
    boundaries.

    Raises:
        ValueError: the resolved `rag.embed` binding doesn't know its own
            embedding dimension.
    """
    params = _db_params()
    resolved = resolve(RAG_EMBED_ROLE)
    if resolved.embed_dim is None:
        raise ValueError(
            f"Embedding connection {resolved.model_id!r} ({resolved.engine}) has no "
            "recorded embedding dimension -- set one for this connection in the "
            "model console (Inference > rag.embed) before it can be used."
        )
    live = live_store_shape()
    shape = desired_store_shape() if live is None else live
    return PGVectorStore.from_params(
        database=params["database"],
        host=params["host"],
        password=params["password"],
        port=params["port"],
        user=params["user"],
        table_name=VECTOR_TABLE_NAME,
        embed_dim=resolved.embed_dim,
        hybrid_search=shape["hybrid"],
        use_jsonb=shape["jsonb"],
        text_search_config=TEXT_SEARCH_CONFIG,
    )


@contextmanager
def disposing_vector_store(vector_store=None):
    """Yield a `PGVectorStore` and dispose its SQLAlchemy engine when the
    caller is done with it -- including when the caller raises (C-06).

    `PGVectorStore.from_params` opens its own engine and connection pool.
    The package's own `close()` is a coroutine (`llama_index/
    vector_stores/postgres/base.py`), so this synchronous column could
    never call it, and nothing disposed a store at all: one leaked pool
    per `/rag/search` request and per `rag.ask`/`rag.search` tool call.
    The synchronous half of what `close()` does is `self._engine.
    dispose()`, which is exactly what `tools/rag/tests/
    test_index_hybrid_integration.py` already reaches for by hand, in a
    `finally:`, to stop a leaked pool blocking `DROP DATABASE` at the end
    of a test session. This makes that the production idiom.

    `vector_store` lets a caller that was handed a store it does not own
    pass it through: this manager builds one only when given `None`, and
    disposes only what it built. Borrowing and disposing someone else's
    pool would be worse than the leak.

    NOT A CACHE. The per-call construction is deliberate and stays --
    `get_vector_store`'s own comment records why (the web process must be
    able to see a table a worker process dropped). Only the disposal was
    missing.
    """
    owned = vector_store is None
    store = get_vector_store() if owned else vector_store
    try:
        yield store
    finally:
        if owned:
            engine = getattr(store, "_engine", None)
            if engine is not None:
                engine.dispose()


def live_embed_dim() -> int | None:
    """Report the vector column width of the live chunk table
    (`data_rag_chunks`), or None if it can't be known.

    This is what the actual Postgres schema says, as opposed to what
    `get_vector_store()` would *ask for* today -- the two diverge exactly
    when an embeddings rebind changed the resolved `embed_dim` after the
    table was created, which is the condition
    `tools.rag.services.reencode_all` uses to decide between a full
    drop/recreate and an in-place per-document re-encode.

    Returns None when the table doesn't exist yet (nothing prose has ever
    been ingested) or the column's dimension is undeclared (`vector` with no
    typmod -- pgvector reports atttypmod -1). For pgvector, `atttypmod` on a
    `vector(n)` column is `n` itself.
    """
    with django_connection.cursor() as cursor:
        cursor.execute(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = to_regclass(%s) AND attname = 'embedding' "
            "AND NOT attisdropped",
            [LIVE_TABLE_NAME],
        )
        row = cursor.fetchone()
    if row is None or row[0] is None or row[0] <= 0:
        return None
    return row[0]


def drop_chunk_table() -> None:
    """Drop the live chunk table (`data_rag_chunks`) so the next
    `get_vector_store()` call recreates it at the currently-resolved
    `embed_dim` -- the "full document store rebuild" teardown for a
    dimension-changing embeddings rebind (ADR 0010).

    No module-level vector-store/index state is cached in this module:
    every `get_vector_store()` call constructs a fresh `PGVectorStore`,
    which creates its table lazily on first use -- so dropping the table
    *is* the whole reset, and the very next ingest recreates it at the new
    width.
    """
    with django_connection.cursor() as cursor:
        cursor.execute(f'DROP TABLE IF EXISTS "{LIVE_TABLE_NAME}"')
    logger.info("dropped chunk table %s for full store rebuild", LIVE_TABLE_NAME)


def delete_chunks_for_document(doc_id, vector_store: PGVectorStore | None = None) -> None:
    """Delete every pgvector chunk whose `file_id` node metadata equals
    `str(doc_id)` -- the single place that knows how a Document's chunks are
    keyed in the store.

    Used by both re-ingest cleanup (tools/rag/ingest.py) and full document
    deletion (tools/rag/services.py). Defensive by design: the `rag_chunks`
    table may not exist yet (nothing prose has ever been ingested) or the
    store may be unreachable in a partial environment, and neither should
    block re-ingestion or deletion -- so failures are logged, not raised.

    `vector_store`: an optional pre-built store to reuse instead of calling
    `get_vector_store()` -- for a bulk caller (`tools.rag.services.reencode_all`)
    that builds one store for the whole run rather than re-resolving the
    binding and reconstructing `PGVectorStore` (which re-runs schema/
    extension/table-existence checks) once per document. Defaults to
    building its own, so every other caller is unaffected.
    """
    try:
        filters = MetadataFilters(filters=[MetadataFilter(key="file_id", value=str(doc_id))])
        store = vector_store if vector_store is not None else get_vector_store()
        store.delete_nodes(filters=filters)
    except Exception:
        logger.warning("could not delete vector chunks for Document %s", doc_id, exc_info=True)


def get_index(vector_store: PGVectorStore | None = None, *, embed_model=None) -> VectorStoreIndex:
    """Return a VectorStoreIndex over the existing pgvector chunk table.

    `embed_model`: an already-built LlamaIndex embedding model (e.g. from
    `models.contracts.gateway.get_embed_model_for`), passed straight through to
    `VectorStoreIndex.from_vector_store`. This is what makes the index build
    (and, transitively, every retrieval it powers -- including the query
    embedding `VectorIndexRetriever` computes at search time) never read the
    global `llama_index.core.Settings` at all: `VectorStoreIndex.__init__`
    only falls back to `Settings.embed_model` when its own `embed_model` is
    falsy, and `VectorIndexRetriever` only falls back to `Settings` via the
    index's own `_embed_model` when it isn't handed one directly -- so an
    explicit, non-None value here closes both fallbacks in one place
    (verified against the installed `llama_index-core`). `None` (the
    default) keeps today's ingest-only callers that don't need retrieval
    working unchanged for now, but every caller that will actually query
    this index must pass one explicitly.

    This function is deliberately LLM-free: unlike the embed model (which
    the index needs at read time, to embed the query), the answer LLM is
    only needed by the query engine built ON TOP of the index
    (`index.as_query_engine(llm=...)`), never by the index itself -- so
    callers thread their resolved LLM straight into `as_query_engine`
    instead of through here (see `tools.rag.retrieval.answer_question`).

    `vector_store`: an optional pre-built store to reuse instead of calling
    `get_vector_store()` (see `delete_chunks_for_document`).
    """
    store = vector_store if vector_store is not None else get_vector_store()
    return VectorStoreIndex.from_vector_store(store, embed_model=embed_model)
