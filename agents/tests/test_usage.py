"""`agents/usage.py` -- what the next turn's prompt will carry, as an
estimate, and how that is said."""
from __future__ import annotations

import pytest

from agents.limits import HISTORY_TURNS
from agents.models import Turn
from agents.tests._helpers import _workstream, make_agent, make_conversation, make_turn
from agents.usage import (
    BAND_FULL, BAND_HIGH, BAND_OK, BAND_UNKNOWN, CHARS_PER_TOKEN, DISCLOSURE_BODY,
    WINDOW_SOURCE_CONNECTION, WINDOW_SOURCE_UNBOUND, WINDOW_SOURCE_UNKNOWN,
    context_usage, estimate_tokens, meter_segments, truncation_clause,
)

pytestmark = pytest.mark.django_db


def _done(conversation, text, *, role=Turn.Role.USER, depth=0):
    return make_turn(conversation=conversation, role=role, text=text, depth=depth,
                     state=Turn.State.DONE)


class TestEstimateTokens:
    def test_empty_text_is_zero(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens(None) == 0

    def test_an_exact_multiple_divides_cleanly(self):
        assert estimate_tokens("x" * (CHARS_PER_TOKEN * 5)) == 5

    def test_a_remainder_rounds_up_never_down(self):
        assert estimate_tokens("x" * (CHARS_PER_TOKEN * 5 + 1)) == 6

    def test_the_divisor_is_read_from_the_constant(self):
        """Never a retyped 4: `HISTORY_TURNS`' own docstring warns
        against a second, drifting counter, and this is the same rule
        one level down."""
        assert estimate_tokens("x" * CHARS_PER_TOKEN) == 1


class TestWhatIsCounted:
    def test_the_system_prompt_the_instructions_and_the_turns_all_count(self):
        agent = make_agent(system_prompt="s" * CHARS_PER_TOKEN)
        stream = _workstream(instructions="i" * (CHARS_PER_TOKEN * 2))
        conversation = make_conversation(agent=agent, workstream=stream)
        _done(conversation, "t" * (CHARS_PER_TOKEN * 3))
        usage = context_usage(conversation, agent, window=8192,
                              window_source=WINDOW_SOURCE_CONNECTION)
        assert usage.estimated_tokens == 6

    def test_blank_instructions_contribute_nothing(self):
        agent = make_agent(system_prompt="")
        stream = _workstream(instructions="   ")
        conversation = make_conversation(agent=agent, workstream=stream)
        usage = context_usage(conversation, agent, window=8192,
                              window_source=WINDOW_SOURCE_CONNECTION)
        assert usage.estimated_tokens == 0

    def test_a_loose_conversation_reads_no_stream_at_all(self):
        agent = make_agent(system_prompt="")
        conversation = make_conversation(agent=agent)
        _done(conversation, "x" * CHARS_PER_TOKEN)
        assert context_usage(conversation, agent, window=100,
                             window_source=WINDOW_SOURCE_CONNECTION).estimated_tokens == 1

    def test_deep_and_unfinished_turns_are_excluded_matching_the_replay(self):
        """`agents.runtime.prompt.history_messages` replays `state="done",
        depth=0` only. This must exclude exactly the same rows."""
        agent = make_agent(system_prompt="")
        conversation = make_conversation(agent=agent)
        _done(conversation, "a" * CHARS_PER_TOKEN)
        _done(conversation, "b" * CHARS_PER_TOKEN, depth=1)
        make_turn(conversation=conversation, text="c" * CHARS_PER_TOKEN,
                  state=Turn.State.QUEUED)
        usage = context_usage(conversation, agent, window=100,
                              window_source=WINDOW_SOURCE_CONNECTION)
        assert usage.estimated_tokens == 1
        assert usage.replayed_turns == 1
        assert usage.total_turns == 1
        assert usage.truncated is False


class TestItStopsAtTheReplayWindow:
    def _conversation_of(self, count, *, slug="test-agent"):
        agent = make_agent(slug=slug, system_prompt="")
        conversation = make_conversation(agent=agent)
        for _ in range(count):
            _done(conversation, "x" * CHARS_PER_TOKEN)
        return conversation, agent

    def test_twenty_five_and_forty_turns_estimate_the_same(self):
        short, short_agent = self._conversation_of(HISTORY_TURNS + 5, slug="test-agent-short")
        long, long_agent = self._conversation_of(HISTORY_TURNS + 20, slug="test-agent-long")
        a = context_usage(short, short_agent, window=8192,
                          window_source=WINDOW_SOURCE_CONNECTION)
        b = context_usage(long, long_agent, window=8192,
                          window_source=WINDOW_SOURCE_CONNECTION)
        assert a.estimated_tokens == b.estimated_tokens == HISTORY_TURNS
        assert a.replayed_turns == b.replayed_turns == HISTORY_TURNS
        assert a.total_turns == HISTORY_TURNS + 5
        assert b.total_turns == HISTORY_TURNS + 20
        assert a.truncated is b.truncated is True

    def test_the_truncation_clause_counts_what_is_dropped(self):
        conversation, agent = self._conversation_of(HISTORY_TURNS + 5)
        usage = context_usage(conversation, agent, window=8192,
                              window_source=WINDOW_SOURCE_CONNECTION)
        assert truncation_clause(usage) == (
            f"the oldest 5 of {HISTORY_TURNS + 5} messages are no longer sent")

    def test_the_replayed_turns_are_the_NEWEST_ones(self):
        agent = make_agent(system_prompt="")
        conversation = make_conversation(agent=agent)
        for _ in range(HISTORY_TURNS):
            _done(conversation, "")
        _done(conversation, "x" * (CHARS_PER_TOKEN * 7))
        usage = context_usage(conversation, agent, window=8192,
                              window_source=WINDOW_SOURCE_CONNECTION)
        assert usage.estimated_tokens == 7


class TestTheKnownUnderCounts:
    """Every exclusion is an UNDER-count, and each is pinned BY NAME so
    the omission reads as documented behaviour rather than being
    rediscovered later as a bug (spec review M4)."""

    def test_a_tool_turns_replayed_call_block_is_not_counted(self):
        from agents.runtime.prompt import tool_turn_messages

        agent = make_agent(system_prompt="")
        conversation = make_conversation(agent=agent)
        turn = make_turn(conversation=conversation, role=Turn.Role.TOOL, text="ok",
                         state=Turn.State.DONE,
                         tool_call={"tool": "rag.search", "args": {"q": "z" * 2000}})
        block = tool_turn_messages(turn)[0].blocks[0]
        # The payload really does reach the replayed ASSISTANT message ...
        assert len(str(block.tool_kwargs)) > 2000
        usage = context_usage(conversation, agent, window=8192,
                              window_source=WINDOW_SOURCE_CONNECTION)
        # ... and none of it is in the estimate, which is turn.text and nothing else.
        assert usage.estimated_tokens == estimate_tokens(turn.text)
        assert usage.estimated_tokens < estimate_tokens(str(block.tool_kwargs))

    def test_a_foreign_user_turns_header_is_not_counted(self):
        from agents.runtime.prompt import _FOREIGN_TURN_HEADER

        agent = make_agent(system_prompt="")
        conversation = make_conversation(agent=agent)
        _done(conversation, "hello")
        usage = context_usage(conversation, agent, window=8192,
                              window_source=WINDOW_SOURCE_CONNECTION)
        assert usage.estimated_tokens < estimate_tokens(_FOREIGN_TURN_HEADER + "hello")

    def test_the_disclosure_names_the_two_exclusions_a_reader_can_act_on(self):
        assert "files attached" in DISCLOSURE_BODY
        assert "tool" in DISCLOSURE_BODY
        assert str(HISTORY_TURNS) in DISCLOSURE_BODY


class TestBandsAndPercent:
    def _at(self, percent):
        agent = make_agent(system_prompt="x" * (CHARS_PER_TOKEN * percent))
        conversation = make_conversation(agent=agent)
        return context_usage(conversation, agent, window=100,
                             window_source=WINDOW_SOURCE_CONNECTION)

    @pytest.mark.parametrize("percent,band", [
        (0, BAND_OK), (69, BAND_OK), (70, BAND_HIGH), (89, BAND_HIGH),
        (90, BAND_FULL), (250, BAND_FULL),
    ])
    def test_the_bands_land_on_their_thresholds(self, percent, band):
        usage = self._at(percent)
        assert usage.percent == percent
        assert usage.band == band

    def test_an_unknown_window_has_no_percentage_and_its_own_band(self):
        agent = make_agent(system_prompt="x")
        conversation = make_conversation(agent=agent)
        usage = context_usage(conversation, agent, window=0,
                              window_source=WINDOW_SOURCE_UNKNOWN)
        assert usage.percent is None
        assert usage.band == BAND_UNKNOWN


class TestTheDeclaredSentences:
    def _usage(self, window, source):
        agent = make_agent(system_prompt="x" * (CHARS_PER_TOKEN * 41))
        conversation = make_conversation(agent=agent)
        return context_usage(conversation, agent, window=window, window_source=source)

    def test_a_known_window_reads_as_a_value_a_ceiling_and_a_percentage(self):
        segments = meter_segments(self._usage(100, WINDOW_SOURCE_CONNECTION))
        assert segments.has_percent is True
        assert segments.lead == "Context ~"
        assert segments.middle == " of 100 tokens ("
        assert segments.tail == "%) · estimate"

    def test_an_unknown_window_says_so_and_carries_no_percent_sign(self):
        segments = meter_segments(self._usage(0, WINDOW_SOURCE_UNKNOWN))
        assert segments.has_percent is False
        assert "%" not in segments.lead + segments.middle + segments.tail
        assert segments.tail.endswith("no limit set for this connection")

    def test_an_unbound_role_says_nothing_about_a_ceiling(self):
        """The page's existing `unavailable` banner already says why
        there is none; the meter does not repeat it."""
        segments = meter_segments(self._usage(0, WINDOW_SOURCE_UNBOUND))
        assert segments.has_percent is False
        assert segments.tail == " tokens · estimate"
        assert "limit" not in segments.tail

    def test_a_large_number_is_grouped_for_reading(self):
        agent = make_agent(system_prompt="x" * (CHARS_PER_TOKEN * 3400))
        conversation = make_conversation(agent=agent)
        usage = context_usage(conversation, agent, window=8192,
                              window_source=WINDOW_SOURCE_CONNECTION)
        assert meter_segments(usage).middle == " of 8,192 tokens ("
