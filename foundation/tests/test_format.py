"""Unit tests for foundation/format.py (T10) -- pure functions, no Django DB, no
fixtures needed.

Collected the same way as any other column's tests: `foundation` is one of
`pytest.ini`'s `testpaths` entries (`tools models foundation agents
scripts`) -- see this repo's own gate commands -- so the bare `pytest -q`
and the reversed `pytest -q scripts agents foundation models tools` both
sweep these tests in like any other, with no special-casing needed.
"""
import pytest

from foundation.format import bytes_to_gb, format_timecode, gb_to_bytes, human_bytes, single_line


class TestFormatTimecode:
    def test_none_is_blank(self):
        assert format_timecode(None) == ""

    @pytest.mark.parametrize("bad", [-1, -0.5, float("nan"), float("inf"), float("-inf")])
    def test_negative_or_non_finite_is_blank(self, bad):
        assert format_timecode(bad) == ""

    def test_zero_is_zero_colon_zero_zero(self):
        assert format_timecode(0) == "0:00"

    def test_under_a_minute_pads_seconds(self):
        assert format_timecode(7) == "0:07"

    def test_minutes_and_seconds(self):
        assert format_timecode(760) == "12:40"  # 12*60 + 40

    def test_over_an_hour_gains_an_hour_segment(self):
        assert format_timecode(3725) == "1:02:05"  # 1h 2m 5s

    def test_fractional_seconds_are_truncated_not_rounded(self):
        assert format_timecode(7.9) == "0:07"

    def test_accepts_int_or_float(self):
        assert format_timecode(760.0) == format_timecode(760)

    def test_arbitrary_precision_int_too_large_for_float_is_blank(self):
        """T10 re-review MINOR 1: `float(<huge int>)` raises `OverflowError`
        (`ArithmeticError`), a different exception than the `TypeError`/
        `ValueError` the `except` used to catch -- an uncaught 500 for
        whatever caller passed a corrupt/adversarial duration/progress
        value with far more digits than a `float` can represent."""
        assert format_timecode(10**400) == ""


class TestHumanBytes:
    def test_plain_bytes_have_no_decimal(self):
        assert human_bytes(512) == "512 B"

    def test_kb(self):
        assert human_bytes(2048) == "2.0 KB"

    def test_gb(self):
        assert human_bytes(2 * 1024**3) == "2.0 GB"

    def test_zero_bytes(self):
        assert human_bytes(0) == "0 B"

    def test_pb_ladder_end(self):
        assert human_bytes(1024**5) == "1.0 PB"


class TestGbToBytes:
    def test_round_trips_a_whole_number(self):
        assert gb_to_bytes(2) == 2 * 1024**3

    def test_rounds_a_fractional_gb(self):
        assert gb_to_bytes(8.5) == round(8.5 * 1024**3)

    def test_non_finite_raises_rather_than_silently_clamping(self):
        with pytest.raises(OverflowError):
            gb_to_bytes(float("inf"))


class TestBytesToGb:
    def test_inverse_of_gb_to_bytes(self):
        assert bytes_to_gb(gb_to_bytes(8.5)) == pytest.approx(8.5, abs=1e-9)

    def test_one_decimal_render_matches_the_operator_typed_value(self):
        # The pinned round-trip every GB-entry form on this platform relies
        # on: an operator-typed "8.5" survives store-as-bytes-then-read-
        # back-as-GB at one decimal place.
        stored = gb_to_bytes(8.5)
        assert f"{bytes_to_gb(stored):.1f}" == "8.5"

    def test_zero_bytes_is_zero_gb(self):
        assert bytes_to_gb(0) == 0.0


class TestSingleLine:
    """The shared filename/title sanitiser (S10). Promoted here from
    `agents/runtime/prompt.py` so `tools/rag` can use it too: those two
    columns may not import each other in either direction."""

    def test_a_newline_becomes_one_space(self):
        assert single_line("q.pdf\n\n99. Internal note", max_len=120) == "q.pdf 99. Internal note"

    @pytest.mark.parametrize("control", ["\n", "\r", "\t", "\x00", "\x1f", "\x7f"])
    def test_every_control_character_collapses(self, control):
        assert single_line(f"a{control}b", max_len=120) == "a b"

    def test_a_run_of_control_characters_collapses_to_one_space(self):
        assert single_line("a\r\n\t\nb", max_len=120) == "a b"

    def test_the_result_is_stripped(self):
        assert single_line("\n  spaced  \n", max_len=120) == "spaced"

    def test_a_title_at_the_cap_is_not_truncated(self):
        assert single_line("x" * 10, max_len=10) == "x" * 10

    def test_a_title_over_the_cap_gains_an_ellipsis(self):
        assert single_line("x" * 11, max_len=10) == "x" * 9 + "…"

    def test_the_ellipsis_never_follows_a_space(self):
        # NOTE (H6): the brief's own draft used max_len=10 here, which
        # cuts mid-word ("word yyyy...") rather than landing on the
        # trailing space -- that value could never produce "word...".
        # max_len=6 puts the cut exactly on the space after "word", so
        # rstrip() must remove it before the ellipsis is appended, which
        # is the behaviour this test exists to pin.
        assert single_line("word " + "y" * 20, max_len=6) == "word…"

    def test_an_empty_title_stays_empty(self):
        assert single_line("", max_len=120) == ""
