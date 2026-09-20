"""Unit tests for tools/rag/sidecar.py (T10 review MAJOR 1).

Pure functions, no Django DB needed -- `read_sidecar`/`display_segments`
never touch the database or the queue, so every test here is a plain
`tmp_path`-backed file write plus a function call, no `@pytest.mark.
django_db` anywhere in this file.
"""
from __future__ import annotations

from tools.rag import sidecar


# --- read_sidecar ------------------------------------------------------------


class TestReadSidecar:
    def test_missing_file_returns_none(self, tmp_path):
        assert sidecar.read_sidecar(tmp_path / "does_not_exist.json") is None

    def test_directory_instead_of_file_returns_none(self, tmp_path):
        """`IsADirectoryError` is an `OSError` subclass -- reading a path
        that's actually a directory must degrade the same way a missing
        file does, not raise."""
        a_directory = tmp_path / "a_directory"
        a_directory.mkdir()
        assert sidecar.read_sidecar(a_directory) is None

    def test_malformed_json_syntax_returns_none(self, tmp_path):
        path = tmp_path / "extract.json"
        path.write_text("{not: valid json,,,")
        assert sidecar.read_sidecar(path) is None

    def test_non_utf8_bytes_returns_none_not_500(self, tmp_path):
        """T10 review MAJOR 1's specific reproduction: `UnicodeDecodeError`
        is a `ValueError` subclass, not an `OSError` -- a caller that only
        catches `OSError` around the read still crashes on this. Latin-1
        byte 0xFF is not valid UTF-8 anywhere in that position."""
        path = tmp_path / "extract.json"
        path.write_bytes(b'{"segments": [{"text": "\xff\xfe not utf-8"}]}')
        assert sidecar.read_sidecar(path) is None

    def test_top_level_json_list_returns_none(self, tmp_path):
        path = tmp_path / "extract.json"
        path.write_text('["segments", "should", "be", "an", "object"]')
        assert sidecar.read_sidecar(path) is None

    def test_top_level_json_string_returns_none(self, tmp_path):
        path = tmp_path / "extract.json"
        path.write_text('"just a string"')
        assert sidecar.read_sidecar(path) is None

    def test_top_level_json_int_returns_none(self, tmp_path):
        path = tmp_path / "extract.json"
        path.write_text("42")
        assert sidecar.read_sidecar(path) is None

    def test_top_level_json_null_returns_none(self, tmp_path):
        path = tmp_path / "extract.json"
        path.write_text("null")
        assert sidecar.read_sidecar(path) is None

    def test_top_level_json_bool_returns_none(self, tmp_path):
        path = tmp_path / "extract.json"
        path.write_text("true")
        assert sidecar.read_sidecar(path) is None

    def test_well_formed_object_returns_it_verbatim(self, tmp_path):
        path = tmp_path / "extract.json"
        path.write_text('{"version": 1, "segments": [{"start": 1.0, "text": "hi"}]}')
        assert sidecar.read_sidecar(path) == {
            "version": 1,
            "segments": [{"start": 1.0, "text": "hi"}],
        }


# --- display_segments ---------------------------------------------------------


class TestDisplaySegments:
    def test_no_segments_key_returns_empty_list(self):
        assert sidecar.display_segments({}) == []

    def test_segments_value_is_a_dict_returns_empty_list(self):
        assert sidecar.display_segments({"segments": {"0": {"text": "x"}}}) == []

    def test_segments_value_is_a_string_returns_empty_list(self):
        assert sidecar.display_segments({"segments": "not a list"}) == []

    def test_segments_value_is_an_int_returns_empty_list(self):
        assert sidecar.display_segments({"segments": 5}) == []

    def test_segments_value_is_none_returns_empty_list(self):
        assert sidecar.display_segments({"segments": None}) == []

    def test_segments_value_is_nested_lists_skips_every_element(self):
        """Each element is itself a `list`, not a `dict` -- skipped, same
        as any other non-dict element, rather than crashing on `.get()`."""
        assert sidecar.display_segments({"segments": [["a", "b"], [1, 2]]}) == []

    def test_non_dict_elements_are_skipped_dict_elements_still_render(self):
        segments = [1, "not a segment", None, [1, 2], {"start": 5.0, "text": "kept"}]
        assert sidecar.display_segments({"segments": segments}) == [
            {"heading": "[0:05]", "text": "kept"},
        ]

    def test_timestamp_segment_renders_mm_ss_heading(self):
        lines = sidecar.display_segments({"segments": [{"start": 760.0, "text": "hello"}]})
        assert lines == [{"heading": "[12:40]", "text": "hello"}]

    def test_page_segment_renders_page_heading(self):
        lines = sidecar.display_segments({"segments": [{"page": 3, "text": "scanned text"}]})
        assert lines == [{"heading": "— p. 3 —", "text": "scanned text"}]

    def test_segment_missing_text_key_renders_blank_text(self):
        lines = sidecar.display_segments({"segments": [{"start": 0.0}]})
        assert lines == [{"heading": "[0:00]", "text": ""}]

    def test_segment_with_non_numeric_start_renders_blank_timecode_not_500(self):
        """`format_timecode` is already `None`/non-finite/negative safe --
        this just proves `display_segments` doesn't add its own numeric
        coercion on top that could raise for a bad `start` value."""
        lines = sidecar.display_segments({"segments": [{"start": "not a number", "text": "x"}]})
        assert lines == [{"heading": "[]", "text": "x"}]

    def test_empty_segments_list_returns_empty_list(self):
        assert sidecar.display_segments({"segments": []}) == []


class TestDescriptionSegmentsRenderWithTheirOwnHeading:
    """Preview UAT (2026-09-17). Without this the transcript page shows
    "— p. 1 —" twice for one image, which reads as two pages of a
    one-page thing. ADDITIVE and backward-compatible (steward condition
    S1): a segment with no `kind` renders exactly as it always has."""

    def test_a_description_segment_gets_the_description_heading(self):
        lines = sidecar.display_segments({"segments": [
            {"page": 1, "kind": "description", "text": "A red bicycle."},
            {"page": 1, "text": "CITY CYCLES"},
        ]})
        assert lines == [
            {"heading": "— description —", "text": "A red bicycle."},
            {"heading": "— p. 1 —", "text": "CITY CYCLES"},
        ]

    def test_an_old_page_segment_with_no_kind_is_byte_identical(self):
        assert sidecar.display_segments({"segments": [{"page": 3, "text": "x"}]}) == [
            {"heading": "— p. 3 —", "text": "x"},
        ]

    def test_an_unknown_kind_falls_back_to_the_page_heading(self):
        """Never invents a heading out of pipeline-supplied data: an
        unrecognised `kind` renders as the page it is on."""
        assert sidecar.display_segments(
            {"segments": [{"page": 2, "kind": "something-new", "text": "x"}]}
        ) == [{"heading": "— p. 2 —", "text": "x"}]

    def test_a_timecoded_segment_is_still_timecoded(self):
        assert sidecar.display_segments({"segments": [{"start": 65, "text": "spoken"}]}) == [
            {"heading": "[1:05]", "text": "spoken"},
        ]
