"""`manage.py relabel_chunks` -- the repair for the ingest-time failure
path."""
from __future__ import annotations

import json

import pytest
from django.core.management import CommandError, call_command
from django.db import connection

from tools.rag import index as rag_index
from tools.rag.models import DocumentEntitlement
from tools.rag.tests._helpers import make_document, make_entitlement

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["json", "jsonb"])
def chunk_table(request):
    """Parametrized over both real live shapes -- see the identical
    fixture in tools/rag/tests/test_labels.py for why (T10 review
    finding 5): the `jsonb` branch of `restamp_document_chunks`'s
    `{cast}` had zero coverage while every fixture only ever built
    `json`."""
    with connection.cursor() as cursor:
        cursor.execute(
            f"CREATE TABLE {rag_index.LIVE_TABLE_NAME} "
            f"(id bigserial primary key, metadata_ {request.param})")
    yield
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {rag_index.LIVE_TABLE_NAME}")


def _seed(doc_id):
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {rag_index.LIVE_TABLE_NAME} (metadata_) VALUES (%s)",
                       [json.dumps({"file_id": str(doc_id)})])


def _metadata(doc_id):
    # `::json`, not `::jsonb` -- see the identical comment in
    # tools/rag/tests/test_labels.py::_metadata: Django's psycopg3 backend
    # registers a `TextLoader` for the `jsonb` OID, so this read-only cast
    # is the one that still auto-parses to a dict in this stack.
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT metadata_::json FROM {rag_index.LIVE_TABLE_NAME} "
                       f"WHERE metadata_->>'file_id' = %s", [str(doc_id)])
        return cursor.fetchone()[0]


class TestRelabelChunks:
    def test_it_repairs_every_document_by_default(self, chunk_table, capsys):
        document = make_document()
        finance = make_entitlement(name="Finance")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        _seed(document.id)
        call_command("relabel_chunks")
        assert _metadata(document.id)["entitlements"] == [str(finance.pk)]

    def test_one_document_at_a_time(self, chunk_table):
        mine, theirs = make_document(), make_document()
        finance = make_entitlement(name="Finance")
        for document in (mine, theirs):
            DocumentEntitlement.objects.create(document=document, entitlement=finance)
            _seed(document.id)
        call_command("relabel_chunks", document=mine.id)
        assert "entitlements" in _metadata(mine.id)
        assert "entitlements" not in _metadata(theirs.id)

    def test_an_unknown_document_id_raises_a_command_error(self):
        """ORCHESTRATOR RULING (T10 review finding 4b), overriding the
        brief's original stderr+exit-0 text: the sibling `ingest` command
        raises `CommandError` for the same user-error class (a bad path),
        and `relabel_chunks --document 9999 && echo ok` must not print
        `ok` -- a script wrapping this repair needs a non-zero exit to
        notice the id was wrong."""
        with pytest.raises(CommandError, match="no document 9999"):
            call_command("relabel_chunks", document=9999)

    def test_a_store_failure_is_translated_to_a_command_error(self, chunk_table, monkeypatch):
        """review finding 4a: an unreachable store must surface an
        honest, operator-actionable message, not a raw psycopg
        traceback -- the same translation `manage.py ask` applies to a
        resolve() failure."""
        document = make_document()
        _seed(document.id)

        def _boom(*args, **kwargs):
            raise RuntimeError("the store is down")

        monkeypatch.setattr("tools.rag.management.commands.relabel_chunks.restamp_document_chunks",
                            _boom)
        with pytest.raises(CommandError, match="the store is down"):
            call_command("relabel_chunks")
