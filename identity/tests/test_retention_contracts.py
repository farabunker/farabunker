"""The retention vocabulary and the registry namespace beside
`EntitlementCascade`. Pure: no database, no Django settings needed."""
from __future__ import annotations

import datetime

import pytest

from identity.contracts import cascades as cascades_module
from identity.contracts import retention
from identity.contracts.cascades import (
    ORDER_FILES, ORDER_ROWS, RetentionHandler, register_retention_handler,
    retention_handlers,
)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """THE REGISTRY IS A MODULE-LEVEL DICT with no reset path, exactly
    like `EntitlementCascade`'s own `_CASCADES` beside it -- so every
    test module that registers into it needs this, matching
    `identity/tests/test_cascades.py::_isolated_registry` and every
    other registry isolation in this codebase
    (`identity/tests/test_ownership.py`,
    `agents/tests/_helpers.py::isolated_tool_registry`).

    Without it, a handler registered here survives this module and
    reaches every later `run_retention()` in the same pytest process --
    including handlers whose dotted path names a function that does not
    exist, which would make every later purge raise `ImportError`. The
    suite runs in BOTH collection orders, so this is not something
    ordering luck can cover.
    """
    saved = dict(cascades_module._RETENTION)
    cascades_module._RETENTION.clear()
    yield
    cascades_module._RETENTION.clear()
    cascades_module._RETENTION.update(saved)


class TestTheKindVocabulary:
    def test_the_four_kinds_are_closed_and_unique(self):
        assert retention.RETENTION_KINDS == (
            retention.KIND_CONVERSATION, retention.KIND_DOCUMENT,
            retention.KIND_ASK, retention.KIND_VISION_JOB)
        assert len(set(retention.RETENTION_KINDS)) == 4

    def test_every_kind_has_a_plain_label(self):
        for kind in retention.RETENTION_KINDS:
            assert retention.KIND_LABELS[kind]

    def test_workstream_is_not_a_kind(self):
        """Spec section 10.2: a stream is a container whose contents each
        have their own cliff."""
        assert "workstream" not in retention.RETENTION_KINDS


class TestTheShippedDefaults:
    def test_a_box_that_configures_nothing_keeps_deleted_items_for_thirty_days(self):
        assert retention.RETENTION_DAYS_DEFAULT == 30

    def test_and_finished_queue_jobs_for_one_day(self):
        assert retention.QUEUE_RETENTION_DAYS_DEFAULT == 1

    def test_the_bounds_are_named_here_not_typed_at_a_call_site(self):
        assert retention.RETENTION_DAYS_MIN == 0
        assert retention.RETENTION_DAYS_MAX == 3650
        assert retention.QUEUE_RETENTION_DAYS_MIN == 1
        assert retention.QUEUE_RETENTION_DAYS_MAX == 3650


class TestTheCopyIsDeclaredOnceInPython:
    def test_the_seven_strings_read_as_plain_words(self):
        assert retention.PAGE_TITLE == "Deleted"
        assert retention.TAB_LOG == "Deletion log"
        assert retention.ACTION_RESTORE == "Restore"
        assert retention.ACTION_PURGE == "Delete permanently"
        assert retention.LABEL_RETENTION_DAYS == "Keep deleted items for"
        assert retention.LABEL_QUEUE_RETENTION_DAYS == "Keep finished queue jobs for"
        assert retention.LABEL_AUDIT_DETAIL == "Show item names in the deletion log"

    def test_the_date_line_is_a_promise_a_person_can_check(self):
        assert retention.purge_on_line(datetime.date(2026, 10, 21)) == "Purge on 21 October 2026"

    def test_the_copy_never_says_ticket_cliff_or_sweep(self):
        words = " ".join([
            retention.PAGE_TITLE, retention.TAB_LOG, retention.ACTION_RESTORE,
            retention.ACTION_PURGE, retention.LABEL_RETENTION_DAYS,
            retention.LABEL_QUEUE_RETENTION_DAYS, retention.LABEL_AUDIT_DETAIL,
            retention.purge_on_line(datetime.date(2026, 10, 21)),
        ]).lower()
        for jargon in ("ticket", "cliff", "sweep", "cascade", "retention"):
            assert jargon not in words


class TestTheRefusalType:
    def test_it_is_a_plain_exception_carrying_a_sentence(self):
        """Raised by a retention handler, caught by the Deleted page's
        POST view, and defined HERE so `models/queue` can raise it --
        that column may import `identity.contracts` and may not import
        `identity.services`."""
        assert issubclass(retention.RetentionRefused, Exception)
        assert str(retention.RetentionRefused("a worker holds this job")) \
            == "a worker holds this job"

    def test_it_lives_in_the_pure_module_so_every_column_can_raise_it(self):
        assert retention.RetentionRefused.__module__ == "identity.contracts.retention"


class TestTheRegistry:
    def test_a_handler_needs_a_known_kind(self):
        with pytest.raises(ValueError, match="not a retention kind"):
            RetentionHandler(kind="workstream", key="x.y", label="X",
                             handler="pkg.mod.fn")

    def test_a_handler_needs_a_dotted_path(self):
        with pytest.raises(ValueError, match="dotted path"):
            RetentionHandler(kind=retention.KIND_CONVERSATION, key="x.y",
                             label="X", handler="notdotted")

    def test_a_handler_needs_a_key_and_a_label(self):
        with pytest.raises(ValueError, match="non-blank key"):
            RetentionHandler(kind=retention.KIND_CONVERSATION, key="",
                             label="X", handler="pkg.mod.fn")
        with pytest.raises(ValueError, match="non-blank label"):
            RetentionHandler(kind=retention.KIND_CONVERSATION, key="x.y",
                             label="", handler="pkg.mod.fn")

    def test_registration_is_idempotent_like_every_sibling_registry(self):
        spec = RetentionHandler(kind=retention.KIND_ASK, key="test.ask",
                                label="Ask", handler="pkg.mod.fn")
        register_retention_handler(spec)
        register_retention_handler(spec)
        assert [h for h in retention_handlers(retention.KIND_ASK)
                if h.key == "test.ask"] == [spec]

    def test_rows_run_before_files_whatever_order_they_registered_in(self):
        files = RetentionHandler(kind=retention.KIND_DOCUMENT, key="test.files",
                                 label="Files", handler="pkg.mod.files",
                                 order=ORDER_FILES)
        rows = RetentionHandler(kind=retention.KIND_DOCUMENT, key="test.rows",
                                label="Rows", handler="pkg.mod.rows")
        register_retention_handler(files)
        register_retention_handler(rows)
        keys = [h.key for h in retention_handlers(retention.KIND_DOCUMENT)
                if h.key.startswith("test.")]
        assert keys == ["test.rows", "test.files"]

    def test_the_default_order_is_rows(self):
        assert RetentionHandler(kind=retention.KIND_ASK, key="a.b", label="A",
                                handler="p.m.f").order == ORDER_ROWS
        assert ORDER_ROWS < ORDER_FILES

    def test_an_unregistered_kind_answers_an_empty_list_not_an_error(self):
        assert retention_handlers("not-a-kind") == []


class TestTheChildrenField:
    def test_it_defaults_to_nothing(self):
        spec = RetentionHandler(kind=retention.KIND_CONVERSATION, key="k", label="L",
                                handler="a.b")
        assert spec.children is None

    def test_it_must_be_a_dotted_path_when_given(self):
        with pytest.raises(ValueError):
            RetentionHandler(kind=retention.KIND_CONVERSATION, key="k", label="L",
                             handler="a.b", children="notdotted")
