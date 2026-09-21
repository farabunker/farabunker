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
