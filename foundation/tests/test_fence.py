"""Unit tests for foundation/fence.py -- pure functions, no Django DB, no
fixtures needed. Moved here (H5 review round 1, CRITICAL) alongside the
functions themselves, out of `agents/runtime/tests/test_prompt.py`,
which pinned them only indirectly (through `build_messages`/
`_tool_content`) and still does -- these tests are the direct,
unit-level pin on the two functions' own behaviour.

Collected the same way as any other column's tests: `foundation` is one
of `pytest.ini`'s `testpaths` entries, so the bare `pytest -q` and the
reversed `pytest -q scripts agents foundation models tools` both sweep
these tests in like any other, with no special-casing needed.
"""
import re

from foundation.fence import carrying_delimiter, neutralize_fence_lines


class TestNeutralizeFenceLines:
    def test_a_bare_dash_run_line_is_escaped(self):
        assert neutralize_fence_lines("a\n---\nb") == "a\n\\---\nb"

    def test_a_longer_dash_run_is_escaped_too(self):
        assert neutralize_fence_lines("-----") == "\\-----"

    def test_surrounding_whitespace_on_the_line_is_stripped_and_escaped(self):
        assert neutralize_fence_lines("  ---  ") == "\\---"

    def test_a_two_dash_run_is_not_a_fence_line(self):
        """Fewer than three dashes is not the Markdown thematic-break/
        fence shape and is left completely alone."""
        assert neutralize_fence_lines("--") == "--"

    def test_a_dash_run_with_other_text_on_the_same_line_is_untouched(self):
        """Only a dash run ALONE on its own line is fence-shaped; the
        identical run of dashes mid-sentence is ordinary punctuation."""
        assert neutralize_fence_lines("a --- b") == "a --- b"

    def test_multiple_fence_like_lines_are_all_escaped(self):
        assert neutralize_fence_lines("---\ntext\n----") == "\\---\ntext\n\\----"

    def test_text_with_no_dash_runs_is_untouched(self):
        assert neutralize_fence_lines("hello world") == "hello world"

    def test_an_empty_string_is_untouched(self):
        assert neutralize_fence_lines("") == ""


class TestCarryingDelimiter:
    def test_returns_a_lowercase_hex_string(self):
        assert re.fullmatch(r"[0-9a-f]+", carrying_delimiter())

    def test_is_unpredictable_between_calls(self):
        """A fixed marker is one a hostile body could pre-guess and
        close early -- the whole reason this function exists rather
        than a module-level literal."""
        assert carrying_delimiter() != carrying_delimiter()
