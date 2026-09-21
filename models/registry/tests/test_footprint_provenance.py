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
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse

from models.registry.bindings import (
    FOOTPRINT_DIP_WARNING_RATIO, record_engine_reported_footprint,
    record_measured_footprint,
)
from models.registry.models import ModelConnection
from models.registry.tests._helpers import (
    ENDPOINT,
    _mock_engine,
    clean_probe_cache,
    client,  # noqa: F401 -- requested by name as a fixture
    clear_seeded_rows,
)


@pytest.fixture(autouse=True)
def _clear_seeded_rows(db):
    """Clear the ModelConnection/RoleBinding rows migration 0002 seeds from
    env settings, so each test starts from a known-empty registry -- copied
    from `test_views_tables_and_picker.py`'s own header rather than
    imported (the six-module split's convention), needed here only by
    `TestTheEngineReportedRowRenders` below, harmless for the rest."""
    clear_seeded_rows()


@pytest.fixture(autouse=True)
def _clean_probe_cache():
    """`probe_cache` (C-07) is not automatically inert inside `django_db`
    tests -- see the identical fixture's own docstring in
    `test_views_tables_and_picker.py`, copied here rather than imported."""
    clean_probe_cache()
    yield
    clean_probe_cache()


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


@pytest.mark.django_db
class TestTheFootprintLabelVocabulary:
    """The footprint labels are their OWN map. `_SOURCE_LABELS` keeps
    labelling `DiscoveryRow.capability_source`, where "detected from the
    model server" is true; reusing it for a post-run delta measurement was
    the one place the console said something untrue (Q1)."""

    def test_every_footprint_source_value_has_a_label(self):
        from models.registry.views import _FOOTPRINT_SOURCE_LABELS

        assert _FOOTPRINT_SOURCE_LABELS["override"] == "set by the operator"
        assert _FOOTPRINT_SOURCE_LABELS["measured"] == "measured after a run"
        assert (
            _FOOTPRINT_SOURCE_LABELS["engine_reported"]
            == "from the engine's residency snapshot"
        )

    def test_the_capability_vocabulary_is_untouched(self):
        """A shipped, unrelated disclosure: relabelling it here would
        change what the console says about capability detection."""
        from models.registry.views import _SOURCE_LABELS

        assert _SOURCE_LABELS["engine"] == "detected from the model server"
        assert _SOURCE_LABELS["connection"] == "manual"
        assert "override" not in _SOURCE_LABELS

    def test_the_two_maps_are_not_the_same_object(self):
        from models.registry.views import _FOOTPRINT_SOURCE_LABELS, _SOURCE_LABELS

        assert _FOOTPRINT_SOURCE_LABELS is not _SOURCE_LABELS


@pytest.mark.django_db
class TestTheFootprintLabelDateIsLocalized:
    """`_footprint_source_label` must run its timestamp through
    `timezone.localtime` before formatting -- exactly what the template's
    own `{{ value|date:"N j, Y" }}` filter has always done -- or the
    rendered date silently moves by a day on any non-UTC box (Task 3
    brief). A regression back to bare `date_format` on the raw UTC value
    would pass every other assertion in this module and still be wrong."""

    @override_settings(TIME_ZONE="Pacific/Kiritimati")
    def test_a_late_evening_utc_timestamp_renders_the_local_date(self):
        from models.registry.views import _footprint_source_label

        # 22:00 UTC on March 4th; Kiritimati is UTC+14, so the local wall
        # clock has already turned over to March 5th.
        at = datetime(2026, 3, 4, 22, 0, tzinfo=dt_timezone.utc)

        label = _footprint_source_label("measured", at)

        assert label == "measured after a run on March 5, 2026"


@pytest.mark.django_db
class TestTheEngineReportedRowRenders:
    """The one rung the console has never shown before (T3) -- its label
    must be the steward's own wording, not `_SOURCE_LABELS`'s "detected
    from the model server", which the image engine's `list_installed`
    (`size=None` by design) would make untrue."""

    def _get(self, client):
        with patch("models.registry.views.discover") as mock_discover, patch(
            "models.registry.views.ENGINES"
        ) as mock_engines:
            mock_engines.values.return_value = [_mock_engine(healthy=True)]
            mock_discover.return_value = []
            return client.get(reverse("inference-console"))

    def test_an_engine_reported_only_connection_says_where_the_number_came_from(self, client):
        at = datetime(2026, 3, 4, tzinfo=dt_timezone.utc)
        connection = ModelConnection.objects.create(
            name="reported conn", engine="ollama", endpoint=ENDPOINT,
            model_id="a-chat-model", capabilities=["chat"],
            engine_reported_footprint_bytes=round(4.7 * 1024**3),
            engine_reported_footprint_at=at,
        )

        body = self._get(client).content.decode()
        start = body.index(f'id="conn-{connection.pk}"')
        disclosure_html = body[start:body.index("</details>", start)]

        # The brief's literal string carries a raw apostrophe; Django's
        # default autoescaping renders the label's "engine's" as
        # "engine&#x27;s". Pinned to what the page actually emits -- the
        # repo's own convention (test_views_reencode_and_sections.py
        # ~L190, test_views_ask.py ~L1337, test_users_page.py ~L234).
        assert (
            "<dt>Memory footprint</dt><dd>4.7 GB — from the engine&#x27;s residency "
            "snapshot on March 4, 2026</dd>" in disclosure_html
        )
