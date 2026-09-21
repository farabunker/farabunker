"""`identity.cascades.run_retention` -- the one runner, and the two
properties the whole feature rests on: it never swallows, and the
savepoint leaves the connection usable afterwards."""
from __future__ import annotations

import pytest
from django.db import connection, transaction

from identity.cascades import run_retention
from identity.contracts import cascades as cascades_module
from identity.contracts.cascades import (
    ORDER_FILES, RetentionHandler, register_retention_handler,
)
from identity.contracts.retention import KIND_ASK
from identity.models import DeletionTicket

pytestmark = pytest.mark.django_db

CALLED: list[str] = []


def rows_handler(key: str) -> int:
    CALLED.append(f"rows:{key}")
    return 2


def files_handler(key: str) -> int:
    CALLED.append(f"files:{key}")
    return 1


def raising_handler(key: str) -> int:
    CALLED.append(f"raise:{key}")
    raise RuntimeError("this column cannot finish")


def db_error_handler(key: str) -> int:
    """A DATABASE-level error, not a Python one -- the case the savepoint
    exists for: on Postgres this poisons the connection's current
    transaction, not merely the Python call."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM a_table_that_does_not_exist")
    return 0


@pytest.fixture(autouse=True)
def _isolated_registry():
    """THE REGISTRY IS A MODULE-LEVEL DICT with no reset path, exactly
    like `EntitlementCascade`'s own `_CASCADES` beside it -- so every
    test module that registers into it needs this, matching
    `identity/tests/test_retention_contracts.py::_isolated_registry` and
    every other registry isolation in this codebase.

    THIS MODULE IS THE ONE THAT PROVES WHY. `t.missing` below names a
    function that does not exist; without this fixture it survives the
    module and makes EVERY later `run_retention()` in the same pytest
    process raise `ImportError` -- the Deleted page's purge view, the
    demo, and the whole owner column of the route matrix. The suite runs
    in BOTH collection orders, so ordering luck cannot cover it.
    """
    saved = dict(cascades_module._RETENTION)
    cascades_module._RETENTION.clear()
    CALLED.clear()
    yield
    CALLED.clear()
    cascades_module._RETENTION.clear()
    cascades_module._RETENTION.update(saved)


class TestTheRunner:
    def test_rows_run_before_files_and_the_counts_come_back_by_label(self):
        register_retention_handler(RetentionHandler(
            kind=KIND_ASK, key="t.files", label="Files",
            handler=f"{__name__}.files_handler", order=ORDER_FILES))
        register_retention_handler(RetentionHandler(
            kind=KIND_ASK, key="t.rows", label="Rows",
            handler=f"{__name__}.rows_handler"))

        removed = run_retention(KIND_ASK, "77")

        assert CALLED == ["rows:77", "files:77"]
        assert removed == {"Rows": 2, "Files": 1}

    def test_a_kind_with_no_handlers_removes_nothing_and_does_not_raise(self):
        assert run_retention("document", "1") == {}

    def test_it_never_swallows_a_handler_that_raises(self):
        register_retention_handler(RetentionHandler(
            kind=KIND_ASK, key="t.raise", label="Boom",
            handler=f"{__name__}.raising_handler"))
        with pytest.raises(RuntimeError, match="cannot finish"):
            run_retention(KIND_ASK, "1")

    def test_it_never_swallows_a_handler_that_cannot_be_imported(self):
        register_retention_handler(RetentionHandler(
            kind=KIND_ASK, key="t.missing", label="Missing",
            handler="identity.nope.not_a_function"))
        with pytest.raises(ImportError):
            run_retention(KIND_ASK, "1")

    def test_the_savepoint_leaves_the_connection_usable_after_a_db_error(self):
        """THE ONLY WAY THIS SAVEPOINT'S PURPOSE IS ACTUALLY TESTED: a
        real query after the failure. Without the nested atomic block the
        connection stays poisoned and this write raises "current
        transaction is aborted" instead of succeeding."""
        register_retention_handler(RetentionHandler(
            kind=KIND_ASK, key="t.dberror", label="Broken",
            handler=f"{__name__}.db_error_handler"))
        with transaction.atomic():
            with pytest.raises(Exception):
                run_retention(KIND_ASK, "1")
            # The connection is healthy again: this is a real write.
            assert DeletionTicket.objects.count() == 0
