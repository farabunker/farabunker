"""The thread page's CONTEXT METER -- the line, the bar, the two
clauses, and the same numbers on the poll tick.

SPLIT OUT OF `test_thread.py` (whole-branch review I-5), which the meter
carried past `foundation/ops/tests/test_column_boundaries.py`'s 2,100-line
split threshold; the branch grew that gate's dated exemption set instead,
and AGENTS.md non-negotiable 2 ends "A dated exemption list gets its
exempted code removed, not grown." The exemption entry is gone with this
module.

BOTH HALVES STAYED TOGETHER, which is the whole point of splitting here
rather than at the class boundary between them. Task 4's brief (spec 9)
put `TestTheContextMeterOnThePollPath` beside `TestTheContextMeter`
deliberately -- "every assertion in this class is about the relationship
between the page and the tick... splitting the two halves across two
modules is how they come to disagree" -- and that ruling is honoured, not
overruled: the seam this module was cut on is the meter's own edge, so
the page half and the tick half are still one file, now their own.

Every test below is a MOVE. No name changed.

NO FARABUNKER_FEATURES OVERRIDE (test_mount.py), as in `test_thread.py`.
"""
from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from agents.chat.tests._helpers import (   # noqa: F401
    bound_chat_role, fake_queued_job, fake_running_job, make_admin, make_agent,
    make_conversation, make_turn, make_user, posture, sign_in, user_principal,
)
from agents.models import Turn
from agents.tests._helpers import _workstream
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = pytest.mark.django_db




def _opening_tag(body: str, element_id: str) -> str:
    """JUST the opening tag of `#<element_id>`, never a fixed number of
    characters after it.

    RE-PINNED, NOT REWRITTEN (whole-branch review I-3). The two
    truncation-clause tests below used to slice 200 characters after
    `id="context-truncation"` and ask whether "hidden" was anywhere in
    them -- which stopped being a statement about that element the
    moment a SECOND hideable clause was added underneath it (the `full`
    band's own, three lines further down in `chat/conversation.html`).
    Their claim is unchanged; only the slice is, from "somewhere nearby"
    to "this tag".
    """
    start = body.index(f'id="{element_id}"')
    return body[start:body.index(">", start)]


def _element_text(body: str, element_id: str) -> str:
    """What `#<element_id>` actually CONTAINS -- the empty string for an
    element rendered with nothing in it, which is a different claim from
    "the string is not on the page" and the one I-3 turns on."""
    start = body.index(f'id="{element_id}"')
    return body[body.index(">", start) + 1:body.index("</p>", start)]


class TestTheContextMeter:
    """Feature A on the page: the line, the bar, the disclosure and the
    two clauses. Server-rendered, inside `.composer-block`, above the
    thread actions row."""

    def _long_prompt_agent(self, chars):
        return make_agent(slug=f"meter-{chars}", system_prompt="x" * chars)

    def test_the_line_renders_with_a_value_a_ceiling_and_a_percentage(
        self, client, bound_chat_role
    ):
        from agents.usage import CHARS_PER_TOKEN

        bound_chat_role.context_window = 1000
        bound_chat_role.save()
        agent = self._long_prompt_agent(CHARS_PER_TOKEN * 410)
        conversation = make_conversation(agent=agent)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "Context ~" in body
        assert "of 1,000 tokens" in body
        assert 'id="context-tokens"' in body
        assert 'id="context-percent"' in body
        assert "estimate" in body

    def test_the_disclosure_is_a_details_element_carrying_the_method(
        self, client, bound_chat_role
    ):
        from agents.usage import DISCLOSURE_BODY, DISCLOSURE_SUMMARY

        conversation = make_conversation(agent=make_agent(slug="meter-disclosure"))
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert DISCLOSURE_SUMMARY in body
        assert DISCLOSURE_BODY[:60] in body

    def test_the_bar_width_comes_from_the_server_never_the_client(
        self, client, bound_chat_role
    ):
        from agents.usage import CHARS_PER_TOKEN

        bound_chat_role.context_window = 100
        bound_chat_role.save()
        agent = self._long_prompt_agent(CHARS_PER_TOKEN * 41)
        conversation = make_conversation(agent=agent)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "--context-fill: 41%" in body
        assert 'data-window="100"' in body

    def test_the_truncation_clause_is_present_but_hidden_when_nothing_is_dropped(
        self, client, bound_chat_role
    ):
        conversation = make_conversation(agent=make_agent(slug="meter-short"))
        make_turn(conversation=conversation, text="hi", state=Turn.State.DONE)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert 'id="context-truncation"' in body
        assert "no longer sent" in body
        assert "hidden" in _opening_tag(body, "context-truncation")

    def test_the_truncation_clause_is_unhidden_on_a_long_conversation(
        self, client, bound_chat_role
    ):
        from agents.limits import HISTORY_TURNS

        conversation = make_conversation(agent=make_agent(slug="meter-long"))
        for _ in range(HISTORY_TURNS + 3):
            make_turn(conversation=conversation, text="hi", state=Turn.State.DONE)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "hidden" not in _opening_tag(body, "context-truncation")
        assert f"the oldest 3 of {HISTORY_TURNS + 3} messages are no longer sent" in body

    def test_at_ninety_percent_the_page_says_what_to_do_about_it(
        self, client, bound_chat_role
    ):
        # REVIEW FIX (task-3 review, finding 1): `{{ context_full_clause }}`
        # renders through Django's default autoescape -- no `|safe`, per
        # the repo's own gate against opting out of it on a rendered path
        # (`tools/rag/tests/test_views_upload_and_settings.py::
        # test_question_and_answer_are_escaped`). `FULL_CLAUSE` carries a
        # literal apostrophe, so the body holds `&#x27;`, not `'`; pinned
        # here through `django.utils.html.escape`, the same helper that
        # test uses, rather than hand-escaping or dropping the apostrophe.
        from django.utils.html import escape

        from agents.usage import CHARS_PER_TOKEN, FULL_CLAUSE

        bound_chat_role.context_window = 100
        bound_chat_role.save()
        agent = self._long_prompt_agent(CHARS_PER_TOKEN * 95)
        conversation = make_conversation(agent=agent)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert escape(FULL_CLAUSE) in body
        assert 'data-band="full"' in body

    def test_below_ninety_percent_it_does_not(self, client, bound_chat_role):
        # REVIEW FIX (task-3 review, finding 1): same escaped-form pin as
        # the ninety-percent test above, and just as load-bearing here --
        # pinning the raw (unescaped) `FULL_CLAUSE` would make this
        # negative assertion vacuously true forever, since the raw form
        # never appears in autoescaped output.
        from django.utils.html import escape

        from agents.usage import CHARS_PER_TOKEN, FULL_CLAUSE

        bound_chat_role.context_window = 100
        bound_chat_role.save()
        agent = self._long_prompt_agent(CHARS_PER_TOKEN * 10)
        conversation = make_conversation(agent=agent)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert escape(FULL_CLAUSE) not in body
        assert 'data-band="ok"' in body

    def test_below_the_band_the_element_is_there_but_empty_and_hidden(
        self, client, bound_chat_role
    ):
        """WHOLE-BRANCH REVIEW I-3. The band flips on a poll tick and the
        sentence explaining it used to wait for F5, because the clause
        was behind a plain server-side `{% if %}` with no element for the
        script to reach. There is now always an element -- and it is
        EMPTY below the band, not a hidden copy of the sentence, which is
        what keeps `test_below_ninety_percent_it_does_not` a real
        assertion rather than one about markup visibility."""
        from agents.usage import CHARS_PER_TOKEN

        bound_chat_role.context_window = 100
        bound_chat_role.save()
        agent = self._long_prompt_agent(CHARS_PER_TOKEN * 10)
        conversation = make_conversation(agent=agent)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "hidden" in _opening_tag(body, "context-full")
        assert _element_text(body, "context-full") == ""

    def test_at_the_band_the_same_element_carries_the_clause_unhidden(
        self, client, bound_chat_role
    ):
        """The other half, so "always empty" cannot pass both."""
        from django.utils.html import escape

        from agents.usage import CHARS_PER_TOKEN, FULL_CLAUSE

        bound_chat_role.context_window = 100
        bound_chat_role.save()
        agent = self._long_prompt_agent(CHARS_PER_TOKEN * 95)
        conversation = make_conversation(agent=agent)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "hidden" not in _opening_tag(body, "context-full")
        assert _element_text(body, "context-full") == escape(FULL_CLAUSE)

    def test_the_script_unhides_it_from_the_servers_own_text_never_its_own(
        self, client, bound_chat_role
    ):
        """DECISIONS 21-22 STILL HOLD. The script reaches the element by
        id, writes `context.full_clause` -- the SERVER's string, from the
        poll body -- with `textContent`, and composes no prose of its
        own. Same `innerHTML`-free slice idiom
        `test_turn_edit.py`'s `carryConnection` pin uses."""
        conversation = make_conversation(agent=make_agent(slug="meter-full-script"))
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        block = body.split("function applyContext(")[1]
        block = block[:block.index("\n  }")]
        assert 'getElementById("context-full")' in block
        assert "context.full_clause" in block
        assert "textContent" in block
        assert "innerHTML" not in block
        # NO PROSE TYPED HERE: the only quoted strings in the whole
        # function are element ids, attribute names and band keys --
        # never a sentence. The clause's own first word would be the
        # cheapest way to break that, so it is the one pinned.
        assert "Start a new conversation" not in block

    def test_a_refusal_that_still_has_a_binding_still_shows_a_ceiling(
        self, client, bound_chat_role, monkeypatch
    ):
        """`preflight_turn`'s NO_TOOL_CALLING leg returns `ok=False`
        with a REAL `resolved`. The branch is `check.resolved is None`,
        never `check.ok` (spec review m1)."""
        from agents.runtime.preflight import Preflight, preflight_turn

        bound_chat_role.context_window = 4096
        bound_chat_role.save()
        conversation = make_conversation(agent=make_agent(slug="meter-refused"))
        real = preflight_turn

        def refusing(agent, connection, **kwargs):
            check = real(agent, connection, **kwargs)
            return Preflight(False, "no_tool_calling",
                             "The bound model cannot call this agent's tools.",
                             check.resolved, check.answered_by, ())

        monkeypatch.setattr("agents.chat.views.thread.preflight_turn", refusing)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "of 4,096 tokens" in body

    def test_with_no_model_bound_the_page_is_200_with_a_ceiling_free_line(self, client):
        conversation = make_conversation(agent=make_agent(slug="meter-unbound"))
        response = client.get(reverse("chat-conversation", args=[conversation.id]))
        body = response.content.decode()
        assert response.status_code == 200
        assert "Context ~" in body
        assert "no limit set for this connection" not in body

    def test_the_engine_default_sentence_is_admin_only_and_never_built_for_a_member(
        self, client, bound_chat_role
    ):
        # REVIEW FIX (task-3 review, finding 1): `ENGINE_DEFAULT_SENTENCE`
        # also carries a literal apostrophe (`engine's`), autoescaped the
        # same way as `FULL_CLAUSE` above -- both assertions pinned
        # through `escape(...)`, the negative one included, for the same
        # vacuous-pass reason.
        from django.utils.html import escape

        from agents.usage import ENGINE_DEFAULT_SENTENCE

        member = make_user()
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            conversation = make_conversation(
                agent=make_agent(slug="meter-default"),
                **owner_fields(user_principal(member)))
            sign_in(client, member)
            member_body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
            assert escape(ENGINE_DEFAULT_SENTENCE) not in member_body
            client.logout()
            sign_in(client, make_admin())
            admin_body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert escape(ENGINE_DEFAULT_SENTENCE) in admin_body

    def test_the_script_count_is_unchanged(self, client, bound_chat_role):
        conversation = make_conversation(agent=make_agent(slug="meter-scripts"))
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert body.count("<script") == 4

    def test_the_meter_costs_the_same_on_a_short_and_a_long_conversation(
        self, client, bound_chat_role
    ):
        """Equality under scale -- the pin this feature's query budget is
        actually made of. A new CONSTANT query is allowed here; a
        per-turn one is not.

        CHAT CLUSTER, FEATURE C EXTENDS IT RATHER THAN ADDING A SECOND
        COPY: the per-turn edit disclosure asks a predicate that would be
        the obvious per-row N+1 on exactly this page, so it is asked ONCE
        per render (`agents.visibility.may_edit_any_turn`) and this
        equality is what would go red if a later reader moved it onto the
        card. The two assertions below the captures are what keep that
        non-vacuous: every turn here really does render the disclosure,
        so the flat cost is being measured with the predicate live rather
        than on a page that never asks it.

        TASK 14 EXTENDS IT AGAIN, THE SAME WAY: both threads are branches
        of the SAME parent, so the provenance banner's own one-query
        resolution (`thread.py`'s own `branched_from` lookup, threaded
        through the SAME `settings_row`) runs on both renders this test
        measures rather than on neither -- a bare `branched_from_id is
        None` conversation would leave that lookup entirely unexercised
        by this pin, and a later reader who moved it onto a per-turn
        path would have nothing here to catch it. The two assertions
        below the meter's own pair are what make that non-vacuous: the
        banner really renders on both pages.

        THE CLOSING WAVE EXTENDS IT ONCE MORE, for the same reason and in
        the same shape: whole-branch review I-2 added a SECOND bounded
        read to this page -- `agents.chat.service.branch_point_ordinal`,
        the parent-side `.count()` behind "at your message N" -- and it
        is bounded by the PARENT's length, not this conversation's, so
        equality under scale is exactly the right pin for it. The parent
        below now carries a real user turn so the ordinal is a live
        number on both renders rather than a count over nothing, and the
        two "at your message 1" assertions at the end are what keep that
        non-vacuous.
        """
        from agents.limits import HISTORY_TURNS

        agent = make_agent(slug="meter-scale")
        parent = make_conversation(agent=agent, title="Meter parent")
        make_turn(conversation=parent, text="parent message", state=Turn.State.DONE)
        short = make_conversation(agent=agent, branched_from=parent, branched_at_index=0)
        make_turn(conversation=short, text="hi", state=Turn.State.DONE)
        long_one = make_conversation(agent=agent, branched_from=parent, branched_at_index=0)
        for _ in range(HISTORY_TURNS * 3):
            make_turn(conversation=long_one, text="hi", state=Turn.State.DONE)
        client.get(reverse("chat-conversation", args=[short.id]))   # warm-up, unmeasured
        with CaptureQueriesContext(connection) as one:
            short_response = client.get(reverse("chat-conversation", args=[short.id]))
        with CaptureQueriesContext(connection) as many:
            long_response = client.get(reverse("chat-conversation", args=[long_one.id]))
        assert short_response.status_code == 200
        assert long_response.status_code == 200
        assert len(many) == len(one)
        short_body = short_response.content.decode()
        long_body = long_response.content.decode()
        assert short_body.count("Send from here") == 1
        assert long_body.count("Send from here") == HISTORY_TURNS * 3
        assert short_body.count("Branched from") == 1
        assert long_body.count("Branched from") == 1
        assert short_body.count("at your message 1") == 1
        assert long_body.count("at your message 1") == 1

    def test_a_reader_who_may_not_upload_pays_no_extra_query_for_the_stream(
        self, client, bound_chat_role
    ):
        """`visible_conversations` only `select_related("agent")`, and
        `thread_context` dereferences `conversation.workstream` ONLY on
        its `may_upload_here` branch -- so without
        `visible_conversation_or_404`'s widened read this render would
        pay a lazy FK read that the spec's own query budget did not
        allow for."""
        stream = _workstream(name="Meter stream", instructions="be brief")
        loose = make_conversation(agent=make_agent(slug="meter-loose"))
        in_stream = make_conversation(agent=make_agent(slug="meter-stream"),
                                      workstream=stream)
        client.get(reverse("chat-conversation", args=[loose.id]))   # warm-up, unmeasured
        with CaptureQueriesContext(connection) as loose_queries:
            client.get(reverse("chat-conversation", args=[loose.id]))
        with CaptureQueriesContext(connection) as stream_queries:
            client.get(reverse("chat-conversation", args=[in_stream.id]))

        # THE ONE EXCLUDED SHAPE IS PRE-EXISTING AND UNRELATED TO THIS
        # TASK: `thread_context`'s own `stream_scope =
        # scope_for_conversation(...)` call (present before this task;
        # it also decides `may_upload_here`, the wall, and the
        # attachments corpus) runs a real AUTHORIZATION check --
        # `workstream_scope` -> `_visible_row` ->
        # `visible_workstreams(principal).filter(pk=...).first()` -- for
        # any IN-STREAM conversation, whatever this task does. Its
        # shape, `... ORDER BY "agents_workstream"."updated_at" DESC
        # LIMIT 1`, is a `.first()` call, and it is DISTINCT from the
        # LAZY FK READ this task's Step 3 exists to remove: an
        # unguarded `conversation.workstream` dereference is a
        # `.get()`-shaped fetch, `... LIMIT 21`, with no `ORDER BY`.
        # Verified by temporarily reverting `visible_conversation_or_
        # 404`'s `select_related("workstream")`: the `LIMIT 21` read
        # reappears in the stream case and only there -- confirming it
        # is exactly what widening the read removes, and that the
        # `LIMIT 1` read survives regardless, because it was never this
        # task's lazy-FK defect to begin with.
        _EXCLUDED_SHAPE = 'order by "agents_workstream"."updated_at" desc limit 1'

        def _standalone_stream_reads(captured):
            # Excluding only that one known shape keeps this pin honest:
            # it still fails the moment `context_usage`'s own
            # dereference regresses into a NEW standalone read.
            return sum(
                1 for q in captured.captured_queries
                if "agents_workstream" in q["sql"].lower()
                and " join " not in q["sql"].lower()
                and _EXCLUDED_SHAPE not in q["sql"].lower()
            )

        # REVIEW FIX (task-3 review, finding 3): the exclusion above is a
        # negative string match, which would silently swallow a SECOND,
        # different authorization read too. Pin the assumption it
        # actually rests on -- exactly one pre-existing `scope_for_
        # conversation` read on the in-stream render, none on the loose
        # one (a loose conversation's `workstream_id` is `None`, and
        # `scope_for_conversation` returns `None` without querying) --
        # so a future second read is caught here rather than excluded.
        excluded_in_stream = [q for q in stream_queries.captured_queries
                              if _EXCLUDED_SHAPE in q["sql"].lower()]
        excluded_in_loose = [q for q in loose_queries.captured_queries
                             if _EXCLUDED_SHAPE in q["sql"].lower()]
        assert len(excluded_in_stream) == 1, (
            "exactly one pre-existing scope_for_conversation read expected")
        assert len(excluded_in_loose) == 0, (
            "a loose conversation should never run scope_for_conversation's query")

        assert _standalone_stream_reads(stream_queries) == _standalone_stream_reads(
            loose_queries)


class TestTheContextMeterOnThePollPath:
    """Three integers, on two of the five bodies, with no binding
    resolved anywhere on this path (spec review M3, m7, R2)."""

    def _queued_pair(self):
        conversation = make_conversation(agent=make_agent(slug="poll-meter"))
        make_turn(conversation=conversation, text="hello", state=Turn.State.DONE)
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                              text="", state=Turn.State.QUEUED, queue_job_id=1)
        return conversation, assistant

    def test_a_queued_body_carries_the_three_integers_and_one_declared_clause(
        self, client, fake_queued_job
    ):
        """DELIBERATE RE-PIN (whole-branch review I-3), named in the
        commit body. The fourth key is not a fourth number and not a
        decision: it is `agents.usage.FULL_CLAUSE` verbatim, the same
        declared object the thread page renders, carried so the script
        can put the `full` band's sentence on the page at the moment the
        band flips rather than at the next F5. Every NUMBER on this body
        is still an integer, which is the half of the old assertion that
        was load-bearing."""
        from agents.usage import FULL_CLAUSE

        _conversation, assistant = self._queued_pair()
        body = client.get(reverse("chat-turn-status", args=[assistant.pk])).json()
        assert set(body["context"]) == {"estimated_tokens", "replayed_turns",
                                        "total_turns", "full_clause"}
        assert all(isinstance(body["context"][key], int)
                   for key in ("estimated_tokens", "replayed_turns", "total_turns"))
        assert body["context"]["full_clause"] == FULL_CLAUSE

    def test_the_corpus_grows_at_QUEUE_time_not_only_at_finish(
        self, client, fake_queued_job
    ):
        """`agents.chat.service.start_turn` writes the USER turn DONE in
        the same transaction as the QUEUED placeholder, so a meter that
        waited for `done` would be stale for the whole in-flight window
        -- precisely when the reader is deciding whether to compact."""
        _conversation, assistant = self._queued_pair()
        body = client.get(reverse("chat-turn-status", args=[assistant.pk])).json()
        assert body["context"]["replayed_turns"] == 1
        assert body["context"]["estimated_tokens"] > 0

    def test_a_done_body_carries_it_too(self, client):
        conversation = make_conversation(agent=make_agent(slug="poll-done"))
        make_turn(conversation=conversation, text="hello", state=Turn.State.DONE)
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                              text="the answer", state=Turn.State.DONE, queue_job_id=2)
        body = client.get(reverse("chat-turn-status", args=[assistant.pk])).json()
        assert body["context"]["replayed_turns"] == 2

    def test_no_window_and_no_COMPOSED_sentence_ever_travel(self, client):
        """DEVIATION FROM THE BRIEF: the brief's own `assert "estimate"
        not in serialized` cannot pass alongside the interface's own
        mandated key name -- `"estimated_tokens"` starts with the
        eight letters "estimate", so the substring is present in EVERY
        legal body. Re-pinned to the assertion's real intent by counting
        occurrences: "estimate" may appear only as the fixed prefix of
        the key name itself, never as a free word a second time.

        DELIBERATE RE-PIN AND RENAME (whole-branch review I-3), named in
        the commit body. The old name said "no sentence"; one sentence
        now does travel, and the honest invariant is narrower and
        sharper: no sentence COMPOSED for this response. `FULL_CLAUSE`
        is a module constant with no number, no window and no percentage
        in it -- the assertions below still hold over it verbatim --
        whereas a "~30% estimate" clause, or the meter's own segments,
        would be the page's prose re-derived on a path that deliberately
        holds no render context. The equality against the declared
        object is what stops it drifting into a second spelling."""
        from agents.usage import FULL_CLAUSE

        conversation = make_conversation(agent=make_agent(slug="poll-window"))
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                              text="a", state=Turn.State.DONE, queue_job_id=3)
        payload = client.get(reverse("chat-turn-status", args=[assistant.pk])).json()
        serialized = str(payload)
        assert "window" not in serialized
        assert "percent" not in serialized
        assert serialized.count("estimate") == serialized.count("estimated_tokens")
        assert payload["context"]["full_clause"] == FULL_CLAUSE
        assert not any(character.isdigit() for character in FULL_CLAUSE)

    def test_the_clause_the_poll_body_carries_is_the_page_s_own(
        self, client, bound_chat_role
    ):
        """PARITY, whole-branch review I-3: the sentence a reader sees
        after a poll tick and the sentence a reader sees after F5 are the
        SAME object, not two spellings that agree today. One declared
        constant, rendered by the page and carried by the body."""
        from django.utils.html import escape

        from agents.usage import CHARS_PER_TOKEN, FULL_CLAUSE

        bound_chat_role.context_window = 100
        bound_chat_role.save()
        conversation = make_conversation(
            agent=make_agent(slug="poll-full-parity",
                             system_prompt="x" * (CHARS_PER_TOKEN * 95)))
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                              text="a", state=Turn.State.DONE, queue_job_id=14)
        page = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        polled = client.get(
            reverse("chat-turn-status", args=[assistant.pk])).json()["context"]
        assert 'data-band="full"' in page, "the page must really be at the band"
        assert escape(FULL_CLAUSE) in page
        assert polled["full_clause"] == FULL_CLAUSE

    def test_a_running_body_carries_no_context_key(self, client, fake_running_job):
        conversation = make_conversation(agent=make_agent(slug="poll-running"))
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                              text="", state=Turn.State.RUNNING, queue_job_id=4)
        assert "context" not in client.get(
            reverse("chat-turn-status", args=[assistant.pk])).json()

    def test_a_failed_and_a_cancelled_body_carry_no_context_key(self, client):
        conversation = make_conversation(agent=make_agent(slug="poll-terminal"))
        failed = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                           state=Turn.State.FAILED, error="nope", queue_job_id=5)
        cancelled = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                              state=Turn.State.CANCELLED, error="stopped", queue_job_id=6)
        assert "context" not in client.get(
            reverse("chat-turn-status", args=[failed.pk])).json()
        assert "context" not in client.get(
            reverse("chat-turn-status", args=[cancelled.pk])).json()

    def test_no_binding_is_resolved_on_the_poll_path(self, client, monkeypatch):
        """No `preflight_turn`, no `resolve_chat`, no wall read, no
        tool-access read. The inverse of "the page pays nothing for it"
        would be every open tab paying for it every two seconds.

        PATCHED AT THE LEAF, NOT AT A NAME THIS MODULE NEVER IMPORTS.
        `agents/chat/views/turns.py`'s import block does not carry
        `preflight_turn` at all, so patching THAT name intercepts
        nothing and the assertion holds whatever the implementation
        does -- a test that pins nothing.
        `models.contracts.bindings.resolve` is what `resolve_chat`
        really reaches, and the query sweep beside it is the second
        belt: a binding resolution cannot happen without touching a
        `models_`-prefixed table."""
        called = []
        monkeypatch.setattr("models.contracts.bindings.resolve",
                            lambda *a, **k: called.append("resolve"))
        conversation = make_conversation(agent=make_agent(slug="poll-nobind"))
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                              text="a", state=Turn.State.DONE, queue_job_id=7)
        with CaptureQueriesContext(connection) as captured:
            client.get(reverse("chat-turn-status", args=[assistant.pk]))
        assert called == []
        assert [q for q in captured.captured_queries
                if "models_" in q["sql"].lower()] == []

    def test_the_context_key_costs_exactly_the_two_reads_it_budgets(self, client):
        """THE ABSOLUTE-DELTA PIN spec §9 asks for, re-budgeted from one
        read to two (see "Deviations from the spec", §1).

        Equality-under-scale alone cannot notice a THIRD constant query
        arriving later, which is exactly the regression this pin exists
        to catch. `_context_body` is asked DIRECTLY, over a turn loaded
        the way `visible_turn` loads it, so the number is the feature's
        own rather than the surrounding body's."""
        from agents.chat.views.turns import _context_body

        conversation = make_conversation(agent=make_agent(slug="poll-delta"))
        make_turn(conversation=conversation, text="hello", state=Turn.State.DONE)
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="a",
                         state=Turn.State.DONE, queue_job_id=13)
        turn = Turn.objects.select_related(
            "conversation", "conversation__agent", "conversation__workstream").get(
                pk=turn.pk)
        with CaptureQueriesContext(connection) as captured:
            body = _context_body(turn)
        assert set(body) == {"estimated_tokens", "replayed_turns", "total_turns",
                             "full_clause"}
        assert len(captured) == 2, [q["sql"] for q in captured.captured_queries]

    def test_the_select_related_is_wide_enough_to_keep_the_budget_honest(self, client):
        """THE PIN THAT PROVES `visible_turn`'s widened `select_related`
        (spec review R2). Narrowing it back to `("conversation",)` -- or
        narrowing it PARTIALLY, dropping only one of the two JOINs --
        adds a lazy FK read to every tick of every open tab, and this
        pin is what turns red rather than quietly costing them.

        DEVIATION FROM THE BRIEF (task-4 review, ruling 2 + finding 5):
        the brief's own assertion is bare equality
        (`len(stream_queries) == len(loose_queries)`), which this
        endpoint has never satisfied -- `_attachments_by_turn`'s own
        `scope_for_conversation` call (round 13, present since before
        this task) resolves an in-stream conversation's wall through
        `workstream_scope`, at a real, pre-existing, five-query cost
        (one `agents_workstream` `.first()` authorization read, three
        `identity_identitysettings` ownership reads, one
        `rag_workstreampin` read) that is not reachable from
        `agents/visibility.py` or `agents/chat/views/turns.py`, the only
        two production files this task touches -- SEE THE WHOLE-BRANCH
        BACKLOG ITEM BELOW. A first re-pin fixed that gap at the magic
        constant 5, which worked but coupled this task's OWN regression
        pin to a cost this task does not own: any future change to
        `_attachments_by_turn`/`attachments_for` would turn this red for
        the wrong reason. Re-expressed instead in the SAME shape terms
        `TestTheContextMeter::test_a_reader_who_may_not_upload_pays_no_
        extra_query_for_the_stream` (task 3) already established for
        this exact ambiguity: a lazy FK fetch is a `.get()`-shaped
        query, `... LIMIT 21`, no `ORDER BY`; the pre-existing
        authorization read is a `.first()`-shaped query, `... ORDER BY
        "agents_workstream"."updated_at" DESC LIMIT 1`. Counting
        STANDALONE (non-JOIN) reads of EACH shape, on EACH table
        (`agents_agent` for the `conversation__agent` JOIN,
        `agents_workstream` for `conversation__workstream`), asserts
        zero lazy reads on both sides without needing to know or state
        the authorization surcharge's size at all -- so a future change
        to that unrelated code path can move the total query count
        however it likes without touching this pin.

        Verified by temporarily narrowing `visible_turn`'s own
        `select_related` twice and restoring it after each: dropping
        `"conversation__agent"` alone puts a `... FROM "agents_agent"
        WHERE "agents_agent"."id" = <id> LIMIT 21` read on BOTH the
        loose and the stream side (the FK is non-null, so both turns
        pay it) -- caught by the `agents_agent` half below. Dropping
        `"conversation__workstream"` alone puts a `... FROM
        "agents_workstream" WHERE "agents_workstream"."id" = <id> LIMIT
        21` read on the STREAM side only (a loose conversation's
        `workstream_id` is `None`, so Django resolves `None` with no
        query) -- caught by the `agents_workstream` half below, which
        the `_EXCLUDED_SHAPE` guard keeps distinct from the pre-existing
        `.first()` authorization read so narrowing this JOIN turns the
        test red instead of hiding behind that read's own presence.
        """
        stream = _workstream(name="Poll stream", instructions="be brief")
        loose = make_conversation(agent=make_agent(slug="poll-loose"))
        in_stream = make_conversation(agent=make_agent(slug="poll-stream"),
                                      workstream=stream)
        loose_turn = make_turn(conversation=loose, role=Turn.Role.ASSISTANT, text="a",
                               state=Turn.State.DONE, queue_job_id=8)
        stream_turn = make_turn(conversation=in_stream, role=Turn.Role.ASSISTANT, text="a",
                                state=Turn.State.DONE, queue_job_id=9)
        client.get(reverse("chat-turn-status", args=[loose_turn.pk]))   # warm-up
        with CaptureQueriesContext(connection) as loose_queries:
            client.get(reverse("chat-turn-status", args=[loose_turn.pk]))
        with CaptureQueriesContext(connection) as stream_queries:
            client.get(reverse("chat-turn-status", args=[stream_turn.pk]))

        # `.first()`-shaped: the pre-existing, task-unrelated
        # `scope_for_conversation` authorization read (round 13) --
        # named here, not fixed at a count, because THIS test does not
        # own its size. WHOLE-BRANCH BACKLOG (task-4 review, finding 4):
        # spec review R2's intent is that the poll path pays NOTHING
        # extra for an in-stream conversation, and today it pays this
        # authorization read plus its own downstream ownership/pin
        # reads on every tick of every open in-stream tab -- a real gap
        # against R2, not this task's file list to close.
        _EXCLUDED_SHAPE = 'order by "agents_workstream"."updated_at" desc limit 1'

        def _standalone_lazy_reads(captured, table: str) -> int:
            # `.get()`-shaped: `... LIMIT 21`, no `ORDER BY` -- the lazy
            # FK fetch a narrowed `select_related` would reintroduce.
            # Never a JOIN (part of the SAME query the view already
            # runs) and never the excluded `.first()` authorization
            # shape above.
            return sum(
                1 for q in captured.captured_queries
                if table in q["sql"].lower()
                and " join " not in q["sql"].lower()
                and _EXCLUDED_SHAPE not in q["sql"].lower()
            )

        # THE COUNTED-EXCLUSION GUARD (same discipline as task 3's own
        # `test_a_reader_who_may_not_upload_pays_no_extra_query_for_the_
        # stream`, review fix finding 3 there): a bare negative string
        # match would silently swallow a SECOND, different `.first()`-
        # shaped authorization read too. Pin the assumption
        # `_standalone_lazy_reads` actually rests on -- exactly one
        # pre-existing `scope_for_conversation` read on the in-stream
        # tick, none on the loose one (a loose conversation's
        # `workstream_id` is `None`, and `scope_for_conversation`
        # returns `None` without querying) -- so a future second read is
        # caught here rather than excluded away.
        excluded_in_stream = [q for q in stream_queries.captured_queries
                              if _EXCLUDED_SHAPE in q["sql"].lower()]
        excluded_in_loose = [q for q in loose_queries.captured_queries
                             if _EXCLUDED_SHAPE in q["sql"].lower()]
        assert len(excluded_in_stream) == 1, (
            "exactly one pre-existing scope_for_conversation read expected")
        assert len(excluded_in_loose) == 0, (
            "a loose conversation should never run scope_for_conversation's query")

        for table in ("agents_agent", "agents_workstream"):
            assert _standalone_lazy_reads(loose_queries, table) == 0, (
                f"a loose conversation's poll tick should read no standalone {table} row")
            assert _standalone_lazy_reads(stream_queries, table) == 0, (
                f"an in-stream conversation's poll tick should read no standalone "
                f"lazy {table} row (the excluded .first() authorization read aside)")

    def test_the_poll_cost_does_not_scale_with_the_conversations_length(self, client):
        from agents.limits import HISTORY_TURNS

        agent = make_agent(slug="poll-scale")
        short = make_conversation(agent=agent)
        long_one = make_conversation(agent=agent)
        for _ in range(HISTORY_TURNS * 3):
            make_turn(conversation=long_one, text="hi", state=Turn.State.DONE)
        short_turn = make_turn(conversation=short, role=Turn.Role.ASSISTANT, text="a",
                               state=Turn.State.DONE, queue_job_id=10)
        long_turn = make_turn(conversation=long_one, role=Turn.Role.ASSISTANT, text="a",
                              state=Turn.State.DONE, queue_job_id=11)
        client.get(reverse("chat-turn-status", args=[short_turn.pk]))   # warm-up
        with CaptureQueriesContext(connection) as one:
            client.get(reverse("chat-turn-status", args=[short_turn.pk]))
        with CaptureQueriesContext(connection) as many:
            client.get(reverse("chat-turn-status", args=[long_turn.pk]))
        assert len(many) == len(one)

    def test_the_page_and_a_poll_tick_cannot_disagree_about_the_ceiling(
        self, client, bound_chat_role
    ):
        """The regression spec review M3 names: a poll body that computed
        its own denominator would answer with the AGENT's role binding
        where the page answered with the PICKED connection. The window
        never travels, so the page's `data-window` is the only one there
        is, and a tick cannot contradict it."""
        from models.registry.models import ModelConnection

        picked = ModelConnection.objects.create(
            name="picked", engine="ollama", endpoint="http://localhost:11434",
            model_id="another-chat-model", capabilities=["chat"], context_window=2048)
        conversation = make_conversation(agent=make_agent(slug="poll-picked"))
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="a",
                              state=Turn.State.DONE, queue_job_id=12)
        page = client.get(
            f"{reverse('chat-conversation', args=[conversation.id])}?connection={picked.pk}"
        ).content.decode()
        body = client.get(reverse("chat-turn-status", args=[assistant.pk])).json()
        assert 'data-window="2048"' in page
        assert "window" not in str(body)

    def test_the_script_count_is_still_unchanged(self, client, bound_chat_role):
        conversation = make_conversation(agent=make_agent(slug="poll-scripts"))
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert body.count("<script") == 4
