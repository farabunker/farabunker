"""The footprint provenance ladder and its own label vocabulary (Q1/Q9).

A NEW module, not an addition to one of the six stewarded
`test_views_*.py` files: those are another session's split, and the
convention this repository already follows (see
`test_console_override_query_count.py`'s own module docstring) is that a
task needing registry tests adds a module rather than editing one of the
six. The only edits this track makes inside those six are the
deliberately re-pinned footprint assertions named in the plan's Task 3.
"""
from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

import pytest

from models.registry.bindings import (
    FOOTPRINT_DIP_WARNING_RATIO, record_engine_reported_footprint,
    record_measured_footprint,
)
from models.registry.models import ModelConnection


@pytest.mark.django_db
class TestTheFourRungLadder:
    """`override ?? measured ?? engine_reported ?? None`, and a
    `footprint_source` that names which rung answered. The rung-3
    columns exist so an engine-reported reading is never folded into the
    measured column: they are facts of different quality, and the
    console's label would be a lie again the moment they shared storage
    (spec §9.1)."""

    def _conn(self, **kwargs) -> ModelConnection:
        return ModelConnection.objects.create(
            name="c", engine="ollama", endpoint="http://e:1", model_id="m", **kwargs,
        )

    def test_nothing_set_is_unknown_and_has_no_source(self):
        connection = self._conn()

        assert connection.effective_footprint_bytes is None
        assert connection.footprint_source is None

    def test_engine_reported_alone_answers_and_names_itself(self):
        connection = self._conn(engine_reported_footprint_bytes=7)

        assert connection.effective_footprint_bytes == 7
        assert connection.footprint_source == "engine_reported"

    def test_measured_outranks_engine_reported(self):
        connection = self._conn(
            measured_footprint_bytes=11, engine_reported_footprint_bytes=7,
        )

        assert connection.effective_footprint_bytes == 11
        assert connection.footprint_source == "measured"

    def test_the_operators_word_outranks_both(self):
        connection = self._conn(
            footprint_override_bytes=3,
            measured_footprint_bytes=11,
            engine_reported_footprint_bytes=7,
        )

        assert connection.effective_footprint_bytes == 3
        assert connection.footprint_source == "override"

    def test_a_zero_engine_reported_reading_is_still_an_answer_not_unknown(self):
        """Zero is a value, `None` is the absence of one -- the ladder
        walks on `is not None`, never on truthiness, so a genuinely
        zero-byte reading cannot silently read as "unknown, run alone".

        THE SCHEDULER'S INTENT WINS OVER THE CONSOLE'S HERE, and the two
        genuinely differ: `models.registry.views._human_size(0)` returns
        `None` by its own documented falsy contract, so Task 3's
        `{% if item.footprint_display %}` renders NO footprint row for such
        a connection. That is accepted rather than worked around -- a
        zero-byte model is a theoretical shape no live engine reports, and
        the cost of getting it wrong in the SCHEDULER (treating it as
        unknown and running it alone for ever) is real while the cost in
        the CONSOLE (one missing disclosure row) is not. Recorded so a
        later reader does not "fix" the ladder to match the renderer."""
        connection = self._conn(engine_reported_footprint_bytes=0)

        assert connection.effective_footprint_bytes == 0
        assert connection.footprint_source == "engine_reported"

    def test_the_reported_timestamp_rides_beside_the_bytes(self):
        at = datetime(2026, 3, 4, tzinfo=dt_timezone.utc)
        connection = self._conn(
            engine_reported_footprint_bytes=7, engine_reported_footprint_at=at,
        )
        connection.refresh_from_db()

        assert connection.engine_reported_footprint_at == at


@pytest.mark.django_db
class TestTheRecorderKeepsTheMaximum:
    """Live evidence, twice: a measurement taken while another model was
    resident, and one taken after a restart lost the residency memo, each
    LOWERED a standing footprint (26.4 -> 6.4 GB; 15.2 -> 11.1 GB). The
    next admission planned against the lowered number, evicted nothing,
    and the machine went down.

    No tolerance band, deliberately (spec §3.2): a percentage band
    against the STANDING value ratchets geometrically, and the condition
    is recurring rather than adversarial -- that model under-measures
    every time it is warm. Keeping the maximum makes the standing value a
    high-water mark by construction, with no extra column and no constant
    to tune."""

    def _conn(self, **kwargs) -> ModelConnection:
        return ModelConnection.objects.create(
            name="c", engine="ollama", endpoint="http://e:1/", model_id="m", **kwargs,
        )

    def test_a_first_reading_is_written(self):
        connection = self._conn()

        record_measured_footprint("ollama", "http://e:1", "m", 100)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes == 100
        assert connection.measured_footprint_at is not None

    def test_a_larger_reading_is_believed(self):
        connection = self._conn(measured_footprint_bytes=100)

        record_measured_footprint("ollama", "http://e:1", "m", 250)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes == 250

    def test_an_equal_reading_is_written_and_refreshes_the_timestamp(self):
        connection = self._conn(measured_footprint_bytes=100)

        record_measured_footprint("ollama", "http://e:1", "m", 100)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes == 100
        assert connection.measured_footprint_at is not None

    def test_a_lower_reading_writes_nothing_at_all(self):
        """Not the bytes, and not the timestamp: a refused reading must
        not leave a fresher date standing beside an older number, which
        would read on the console as "we measured this recently"."""
        at = datetime(2026, 3, 4, tzinfo=dt_timezone.utc)
        connection = self._conn(measured_footprint_bytes=100, measured_footprint_at=at)

        record_measured_footprint("ollama", "http://e:1", "m", 40)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes == 100
        assert connection.measured_footprint_at == at

    def test_three_successive_08x_readings_do_not_walk_the_standing_value_down(self):
        """The review's own pinned test (round 1, M5), adopted verbatim:
        this is the exact shape a tolerance band would have permitted."""
        connection = self._conn(measured_footprint_bytes=1000)

        for reading in (800, 640, 512):
            record_measured_footprint("ollama", "http://e:1", "m", reading)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes == 1000

    def test_a_badly_lower_reading_warns_and_names_the_override(self, caplog):
        connection = self._conn(measured_footprint_bytes=1000)

        with caplog.at_level("INFO", logger="models.registry.bindings"):
            record_measured_footprint("ollama", "http://e:1", "m", 100)

        line = "".join(record.getMessage() for record in caplog.records)
        assert "1000" in line and "100" in line
        assert str(connection.pk) in line
        assert "override" in line
        assert any(record.levelname == "WARNING" for record in caplog.records)

    def test_a_small_dip_is_informational_not_a_warning(self, caplog):
        self._conn(measured_footprint_bytes=1000)
        just_above = int(1000 * FOOTPRINT_DIP_WARNING_RATIO) + 1

        with caplog.at_level("INFO", logger="models.registry.bindings"):
            record_measured_footprint("ollama", "http://e:1", "m", just_above)

        assert caplog.records
        assert all(record.levelname == "INFO" for record in caplog.records)

    def test_the_engine_reported_column_obeys_the_same_rule(self):
        connection = self._conn(engine_reported_footprint_bytes=1000)

        record_engine_reported_footprint("ollama", "http://e:1", "m", 400)
        record_engine_reported_footprint("ollama", "http://e:1", "m", 1500)

        connection.refresh_from_db()
        assert connection.engine_reported_footprint_bytes == 1500

    def test_the_two_columns_do_not_compare_against_each_other(self):
        """A rung-3 reading is measured against rung 3's own standing
        value, never against rung 2's -- they are facts of different
        quality and a cross-rung comparison would refuse honest writes."""
        connection = self._conn(measured_footprint_bytes=1000)

        record_engine_reported_footprint("ollama", "http://e:1", "m", 40)

        connection.refresh_from_db()
        assert connection.engine_reported_footprint_bytes == 40
        assert connection.measured_footprint_bytes == 1000

    def test_an_ambiguous_ref_is_still_a_silent_no_op(self):
        self._conn()
        ModelConnection.objects.create(
            name="twin", engine="ollama", endpoint="http://e:1", model_id="m",
        )

        record_measured_footprint("ollama", "http://e:1", "m", 500)

        assert not ModelConnection.objects.filter(
            measured_footprint_bytes__isnull=False).exists()
