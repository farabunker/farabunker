"""`agents.chat.rendering` -- what a turn MEANS, decided once, over
rows, with no request anywhere.

Every judgement the thread makes lives here so it can be tested without
HTTP and reused byte-identically by the poll view's fragment render. A
template that decided any of this itself would be a second copy that
drifts the first time one of them is edited.
"""
from __future__ import annotations

import pytest
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from agents.chat.rendering import (
    artifact_links, citations_of, render_answer, thread_cards, tool_card,
)
from agents.chat.tests._helpers import make_agent, make_conversation, make_turn
from agents.models import ToolInvocation, Turn
from agents.runtime.audit import RUNNING

pytestmark = pytest.mark.django_db


def _invocation(**overrides):
    fields = dict(
        principal_kind="resident_agent", principal_key="general",
        tool_key="rag.search", args={"query": "attention"},
        outcome=ToolInvocation.Outcome.OK, text="two results",
        finished_at=timezone.now(),
    )
    fields.update(overrides)
    return ToolInvocation.objects.create(**fields)


def _tool_turn(conversation, *, invocation=None, **overrides):
    fields = dict(
        conversation=conversation, role=Turn.Role.TOOL, text="two results",
        state=Turn.State.DONE, invocation=invocation,
        tool_call={"tool": "rag.search", "args": {"query": "attention"},
                   "agent": "general", "id": "", "discarded": []},
    )
    fields.update(overrides)
    return make_turn(**fields)


def _generation_turn(conversation, *, job_id=None, data=..., data_overrides=None,
                     **overrides):
    """A TOOL turn shaped like `tools/vision/tools.py::run_generate`'s
    own return: `tool_call["tool"]` is the image generator's key, and
    `Turn.data` is the `services.job_json` payload whose `"id"` is the
    GENERATION's uuid.

    Only the keys this module reads are filled in -- a fixture that
    copied all nineteen of `job_json`'s would be a second, drifting copy
    of a shape another column owns.
    """
    payload = {"id": job_id, "status": "done"} if data is ... else data
    if data_overrides and isinstance(payload, dict):
        payload = {**payload, **data_overrides}
    fields = dict(
        text=f"Generation {job_id} finished as done.",
        tool_call={"tool": "vision.generate", "args": {"prompt": "a cat"},
                   "agent": "general", "id": "", "discarded": []},
        data=payload,
    )
    fields.update(overrides)
    return _tool_turn(conversation, **fields)


class TestToolCardState:
    def test_an_unfinished_call_renders_as_running_not_as_an_error(self):
        """P2 ledger, the whole reason this module exists: the audit
        row is CREATED with `outcome=ERROR` as a placeholder, so a card
        that read `outcome` would show red for a working call."""
        turn = _tool_turn(make_conversation(),
                          invocation=_invocation(finished_at=None,
                                                 outcome=ToolInvocation.Outcome.ERROR))
        assert tool_card(turn)["state"] == RUNNING

    def test_a_failed_call_shows_the_recorded_error_when_there_is_no_turn_text(self):
        """P2 ledger: `ToolInvocation.text` is blank on every failure
        path, so a card reading `text` shows an empty box for the one
        card that most needs words."""
        turn = _tool_turn(
            make_conversation(), text="",
            invocation=_invocation(outcome=ToolInvocation.Outcome.ERROR,
                                   text="", error="the engine timed out"),
        )
        card = tool_card(turn)
        assert card["state"] == ToolInvocation.Outcome.ERROR
        assert card["message"] == "the engine timed out"

    def test_a_turn_with_no_invocation_at_all_is_unknown_not_failed(self):
        """`Turn.invocation` is SET_NULL. A pruned audit row leaves an
        unknown outcome, and rendering that as a failure invents one."""
        assert tool_card(_tool_turn(make_conversation()))["state"] == ""

    def test_discarded_calls_are_named_because_the_model_thinks_it_made_them(self):
        turn = _tool_turn(
            make_conversation(), invocation=_invocation(),
            tool_call={"tool": "rag.search", "args": {}, "agent": "general",
                       "id": "", "discarded": [{"tool": "rag.ask", "args": {}}]},
        )
        assert tool_card(turn)["discarded"] == ["rag.ask"]

    def test_a_long_argument_is_truncated_rather_than_dominating_the_thread(self):
        turn = _tool_turn(
            make_conversation(), invocation=_invocation(),
            tool_call={"tool": "rag.search", "args": {"query": "x" * 500},
                       "agent": "general", "id": "", "discarded": []},
        )
        key, value = tool_card(turn)["args"][0]
        assert key == "query"
        assert len(value) <= 204 and value.endswith("…")

    def test_null_and_empty_arguments_are_hidden_not_shown_inline(self):
        """U1: a union `ToolSpec` call can carry twenty parameters this
        operation ignores -- `None`, `""`, `[]`, `{}` -- and a card that
        showed all of them buried the one or two that mattered."""
        turn = _tool_turn(
            make_conversation(), invocation=_invocation(),
            tool_call={
                "tool": "vision.generate",
                "args": {"prompt": "a cat", "seed": None, "mask": "",
                         "reference_images": [], "extra": {}, "steps": 0,
                         "upscale": False},
                "agent": "general", "id": "", "discarded": [],
            },
        )
        card = tool_card(turn)
        shown_keys = {k for k, _ in card["args"]}
        hidden_keys = {k for k, _ in card["hidden_args"]}
        assert shown_keys == {"prompt", "steps", "upscale"}
        assert hidden_keys == {"seed", "mask", "reference_images", "extra"}

    def test_a_short_message_is_not_marked_collapsed(self):
        turn = _tool_turn(make_conversation(), invocation=_invocation(),
                          text="two results")
        assert tool_card(turn)["message_collapsed"] is False

    def test_a_long_message_is_marked_collapsed(self):
        """U1: the card decides the threshold, once, over the row --
        never the template, and never the whole point of `rendering.py`
        existing at all (see its own module docstring)."""
        from agents.chat.rendering import TOOL_OUTPUT_COLLAPSE_THRESHOLD

        turn = _tool_turn(
            make_conversation(),
            invocation=_invocation(text="x" * (TOOL_OUTPUT_COLLAPSE_THRESHOLD + 1)),
            text="x" * (TOOL_OUTPUT_COLLAPSE_THRESHOLD + 1),
        )
        assert tool_card(turn)["message_collapsed"] is True


class TestArtifacts:
    def test_a_vision_output_becomes_an_image_pointing_at_the_streaming_view(self):
        """`vision-output-file` streams strictly by primary key and
        clamps its Content-Type (`tools/vision/views.py:980-1000`), so
        the chat never learns or exposes a filesystem path."""
        images, files = artifact_links(["output:12"])
        assert files == []
        assert images[0]["url"] == reverse("vision-output-file", args=[12])

    def test_a_document_becomes_a_link_to_the_rag_file_view(self):
        images, files = artifact_links(["document:7"])
        assert images == []
        assert files[0]["url"] == reverse("rag-document-file", args=[7])

    def test_a_documents_title_is_read_off_the_reference_itself(self):
        """R5 (chat-polish P3.1, fix round 1): the title `mint_artifact`
        (`agents/contracts/artifacts.py`) embedded at mint time --
        `tools/rag/tools.py::_document_artifacts`'s job, never a second,
        cross-column lookup this module's own import-law ban forbids."""
        from agents.contracts.artifacts import mint_artifact

        reference = mint_artifact("document", 7, "Attention Is All You Need")
        _images, files = artifact_links([reference])
        assert files[0]["title"] == "Attention Is All You Need"

    def test_a_document_with_no_embedded_title_falls_back_to_a_numbered_placeholder(
        self,
    ):
        """A reference minted before R5 (or with a blank title) shows
        "Document <id>" -- never the bare, unlabelled `document:<id>`
        reference the link text used to be."""
        _images, files = artifact_links(["document:7"])
        assert files[0]["title"] == "Document 7"

    def test_an_images_title_is_always_blank(self):
        """Only `document` ever carries a display title -- an image
        renders as a thumbnail, never a named link."""
        images, _files = artifact_links(["output:12"])
        assert images[0]["title"] == ""

    def test_a_malformed_reference_is_dropped_not_rendered_as_a_broken_link(self):
        """`parse_artifact` raises for anything that is not
        `<kind>:<digits>`. This value reached the row from a tool
        runner, and a link that cannot resolve is worse than no link."""
        assert artifact_links(["nonsense", "output:", ":12", ""]) == ([], [])

    def test_a_reference_whose_url_is_not_mounted_still_renders_as_text(
        self, monkeypatch
    ):
        """THE VISION-OFF CASE. `vision-output-file` only exists when
        `"vision"` is in FARABUNKER_FEATURES (config/urls.py:19-20), so
        a conversation that recorded `output:12` on a box where vision
        was later turned off must still render.

        Patched at the renderer's own `reverse` rather than by
        overriding the flag: this test uses `reverse()` itself, and THE
        VISION-FLAG RULE makes a flag override here a process-wide URL
        resolution hazard for every later test in the run.
        """
        def _unmounted(*args, **kwargs):
            raise NoReverseMatch("vision is not mounted")

        monkeypatch.setattr("agents.chat.rendering.reverse", _unmounted)
        images, _files = artifact_links(["output:12"])
        assert images[0]["url"] == ""
        assert images[0]["reference"] == "output:12"


class TestTheGenerationLink:
    """UI-3c: an image card links to its own submission on the image
    surface, not just to the picture.

    THE FLAG-OFF CASE IS THE REGRESSION THAT MATTERS. `vision-gallery`
    is mounted only while `"vision"` is in FARABUNKER_FEATURES, and a
    conversation outlives a feature flag -- an unguarded `{% url %}` in
    the card template would raise `NoReverseMatch` mid-render and 500
    the whole thread page. Patched at the renderer's own `reverse`
    rather than by overriding the flag, for the reason
    `test_a_reference_whose_url_is_not_mounted_still_renders_as_text`
    above gives: THE VISION-FLAG RULE makes a flag override here a
    process-wide URL resolution hazard for every later test in the run.
    """

    def test_a_finished_generation_links_to_the_gallery_filtered_and_anchored(self):
        """The exact href shape agreed with the image surface: the
        gallery's own `?job=` filter (what makes the link LAND on the
        submission when it is far down the list) plus the per-figure
        anchor (what scrolls to it once the page is there)."""
        job = "6d1a2f30-0c1b-4a55-9d0e-1f2a3b4c5d6e"
        turn = _generation_turn(make_conversation(), job_id=job)
        assert tool_card(turn)["generation_url"] == (
            f"{reverse('vision-gallery')}?job={job}#job-{job}"
        )

    def test_the_id_is_the_generation_uuid_not_the_queue_job_id(self):
        """Both are in the SAME payload (`services.job_json`), and they
        are different rows in different tables. The gallery is addressed
        by generation uuid."""
        job = "6d1a2f30-0c1b-4a55-9d0e-1f2a3b4c5d6e"
        turn = _generation_turn(make_conversation(), job_id=job,
                                data_overrides={"queue_job_id": 4321})
        url = tool_card(turn)["generation_url"]
        assert job in url
        assert "4321" not in url

    def test_it_is_read_from_the_structured_result_not_from_the_sentence(self):
        """The card's closing sentence and this link are formatted from
        the same payload. Parsing the prose back apart would make a copy
        edit in another column silently break the link -- so the text
        here names a DIFFERENT id than the data, and the data wins."""
        job = "6d1a2f30-0c1b-4a55-9d0e-1f2a3b4c5d6e"
        turn = _generation_turn(
            make_conversation(), job_id=job,
            text="Generation 00000000-0000-0000-0000-000000000000 finished as done.",
        )
        assert job in tool_card(turn)["generation_url"]

    def test_an_unfinished_generation_still_gets_the_link(self):
        """Regardless of final state: the gallery filter answers
        honestly for any state and any viewer, so there is no state this
        button would be lying about."""
        turn = _generation_turn(make_conversation(),
                                job_id="6d1a2f30-0c1b-4a55-9d0e-1f2a3b4c5d6e",
                                data_overrides={"status": "running"})
        assert tool_card(turn)["generation_url"]

    def test_a_result_with_no_generation_id_gets_no_link_and_does_not_raise(self):
        """A call that was refused or failed before it submitted
        anything has no submission to open."""
        turn = _generation_turn(make_conversation(), data={})
        assert tool_card(turn)["generation_url"] == ""

    def test_a_result_with_no_data_at_all_gets_no_link(self):
        turn = _generation_turn(make_conversation(), data=None)
        assert tool_card(turn)["generation_url"] == ""

    def test_a_non_uuid_id_is_refused_rather_than_spliced_into_a_url(self):
        """`Turn.data` is a JSON column written by a tool runner. The
        same guard, and the same reason, `_document_url` puts
        `isdecimal()` in front of its own id: a stray value must not
        mint a link nothing can resolve."""
        turn = _generation_turn(make_conversation(), job_id="not-a-uuid")
        assert tool_card(turn)["generation_url"] == ""

    def test_another_tool_with_an_id_in_its_data_gets_no_link(self):
        """Keyed on the TOOL, never on the shape of the payload -- a
        library call whose result happens to carry an `id` is not a
        generation."""
        turn = _tool_turn(
            make_conversation(),
            data={"id": "6d1a2f30-0c1b-4a55-9d0e-1f2a3b4c5d6e"},
        )
        assert tool_card(turn)["generation_url"] == ""

    def test_with_the_image_surface_unmounted_there_is_no_link_and_no_raise(
        self, monkeypatch
    ):
        """THE FLAG-OFF CASE, at the renderer. `""`, not an exception --
        `test_thread.py` asserts the other half, that the PAGE still
        renders."""
        def _unmounted(*args, **kwargs):
            raise NoReverseMatch("vision is not mounted")

        monkeypatch.setattr("agents.chat.rendering.reverse", _unmounted)
        turn = _generation_turn(make_conversation(),
                                job_id="6d1a2f30-0c1b-4a55-9d0e-1f2a3b4c5d6e")
        assert tool_card(turn)["generation_url"] == ""


class TestCitations:
    def test_rag_ask_citations_are_read_from_the_citations_key(self):
        turn = _tool_turn(make_conversation(), invocation=_invocation(),
                          data={"citations": [{"document_id": "7", "title": "a.pdf",
                                               "locator_text": ", p. 5", "score": 0.81}]})
        entry = citations_of(turn)[0]
        assert entry["title"] == "a.pdf"
        assert entry["locator_text"] == ", p. 5"
        assert entry["score_display"] == "0.810"
        assert entry["url"] == reverse("rag-document-file", args=[7])

    def test_rag_search_results_are_read_from_the_results_key(self):
        """DEVIATION P3-D6, and a real spec inconsistency: section 8.4
        names only `data["citations"]`, but `tools/rag/tools.py::
        run_search` writes `data["results"]`. Reading only the first
        key would render every search card with no sources at all."""
        turn = _tool_turn(make_conversation(), invocation=_invocation(),
                          data={"results": [{"document_id": "7", "title": "a.pdf",
                                             "locator_text": "", "score": 0.5,
                                             "score_display": "0.500"}],
                                "hybrid": False})
        assert citations_of(turn)[0]["title"] == "a.pdf"

    def test_a_citation_with_a_non_numeric_document_id_renders_without_a_link(self):
        """`retrieval._document_url_for` guards the same way
        (retrieval.py:362-379): a stray `file_id` in stored chunk
        metadata must not mint a URL nothing can reverse."""
        turn = _tool_turn(make_conversation(), invocation=_invocation(),
                          data={"citations": [{"document_id": "not-an-id",
                                               "title": "a.pdf", "score": None}]})
        entry = citations_of(turn)[0]
        assert entry["url"] == "" and entry["title"] == "a.pdf"

    def test_a_turn_with_no_data_has_no_citations_and_does_not_raise(self):
        assert citations_of(_tool_turn(make_conversation())) == []

    def test_source_path_never_reaches_the_card(self):
        """`tools/rag/retrieval.py::_vector_citations` puts
        `source_path` -- a real filesystem path on the server -- in
        every citation dict, and `tools/rag/tools.py::run_ask` hands
        the dict back UNFILTERED, so it is genuinely in `Turn.data`.

        This function builds a FIXED FOUR-KEY dict, which is what keeps
        the path out of the page. Pinned rather than trusted: the
        `artifacts` vocabulary exists precisely so no layer below the
        view learns a path the caller cannot see
        (`agents/contracts/artifacts.py`), and a renderer that started
        spreading the source dict would undo that in one line."""
        turn = _tool_turn(
            make_conversation(), invocation=_invocation(),
            data={"citations": [{"document_id": "7", "title": "a.pdf",
                                 "source_path": "/srv/farabunker/media/a.pdf",
                                 "score": 0.8}]},
        )
        card = citations_of(turn)[0]
        assert set(card) == {"title", "locator_text", "score_display", "url"}
        assert "/srv/farabunker" not in repr(card)


class TestRenderAnswer:
    """U2: a small, dependency-free markdown SUBSET for assistant text,
    never a markdown library. No links are ever auto-created, and no
    raw HTML from the model ever survives -- both asserted explicitly,
    including an XSS case."""

    def test_plain_text_becomes_a_single_paragraph(self):
        assert render_answer("hello there") == "<p>hello there</p>"

    def test_a_script_tag_is_escaped_never_rendered(self):
        """THE XSS TEST. A raw `<script>` from the model must never
        reach the page as an actual tag."""
        html = str(render_answer("<script>alert(1)</script>"))
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_no_raw_html_passes_through_at_all(self):
        html = str(render_answer('<img src=x onerror="alert(1)">'))
        assert "<img" not in html
        assert "&lt;img" in html

    def test_bold_and_emphasis(self):
        html = str(render_answer("**bold** and *em*"))
        assert "<strong>bold</strong>" in html
        assert "<em>em</em>" in html

    def test_a_backtick_code_span(self):
        html = str(render_answer("run `pytest -q` first"))
        assert "<code>pytest -q</code>" in html

    def test_a_fenced_code_block_is_not_given_inline_formatting(self):
        """Bold/em markers inside a fenced block are LITERAL code, never
        formatting -- a code sample that happens to contain `*args`
        must not turn into emphasis."""
        html = str(render_answer("```\ndef f(*args):\n    return *args\n```"))
        assert "<pre><code>" in html
        assert "<em>" not in html
        assert "def f(*args):" in html

    def test_a_blockquote_line(self):
        html = str(render_answer("> a quoted line"))
        assert "<blockquote>" in html
        assert "a quoted line" in html

    def test_a_dash_list(self):
        html = str(render_answer("- first\n- second"))
        assert html.count("<li>") == 2
        assert "<ul>" in html

    def test_a_numbered_list(self):
        html = str(render_answer("1. first\n2. second"))
        assert "<ol>" in html
        assert html.count("<li>") == 2

    def test_three_or_more_blank_lines_collapse_to_one_paragraph_break(self):
        html = str(render_answer("first\n\n\n\nsecond"))
        assert html == "<p>first</p><p>second</p>"

    def test_no_link_is_ever_created_from_a_bare_url(self):
        html = str(render_answer("see https://example.com for more"))
        assert "<a " not in html
        assert "https://example.com" in html

    def test_no_link_is_created_from_markdown_link_syntax_either(self):
        html = str(render_answer("[the paper](https://example.com/a.pdf)"))
        assert "<a " not in html

    def test_blank_text_renders_to_nothing(self):
        assert str(render_answer("")) == ""

    def test_the_result_is_marked_safe(self):
        from django.utils.safestring import SafeString

        assert isinstance(render_answer("hi"), SafeString)


class TestThreadCards:
    def test_turns_come_back_in_index_order(self):
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER, text="q")
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="a",
                  state=Turn.State.DONE)
        assert [c["role"] for c in thread_cards(conversation)] == ["user", "assistant"]

    def test_a_user_cards_poll_block_matches_the_job_that_answers_it(self):
        """R1 (chat-polish P3.1, fix round 1): the UNFILTERED call --
        `thread_context`'s own whole-page render -- must stamp the
        USER card's `poll_block` to the SAME value as the job that
        answers it, or the full render and `turn_group_cards`'s own
        polled fragment disagree on the marker a poll's swap dedupes
        by (the exact bug this fix closes)."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER, text="q")
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="a",
                  state=Turn.State.DONE, queue_job_id=1)
        cards = thread_cards(conversation)
        assert cards[0]["role"] == "user" and cards[0]["poll_block"] == 1
        assert cards[1]["role"] == "assistant" and cards[1]["poll_block"] == 1

    def test_a_user_card_with_no_following_job_carries_no_marker(self):
        """The ordinary case: a lone USER turn (the thread's own message
        form, before anything is queued) must never carry a marker --
        there is no job yet to share one with."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER, text="q")
        assert thread_cards(conversation)[0]["poll_block"] is None

    def test_each_exchange_claims_its_own_user_card_only(self):
        """Two separate exchanges in one conversation must never cross-
        claim: the second USER card's marker is the SECOND job's id,
        never the first's."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER, text="q1")
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="a1",
                  state=Turn.State.DONE, queue_job_id=1)
        make_turn(conversation=conversation, role=Turn.Role.USER, text="q2")
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="a2",
                  state=Turn.State.DONE, queue_job_id=2)
        cards = thread_cards(conversation)
        user_cards = [c for c in cards if c["role"] == "user"]
        assert [c["poll_block"] for c in user_cards] == [1, 2]

    def test_a_filtered_call_is_unaffected_by_the_marker_stamping(self):
        """The stamping logic only ever runs inside the loop over
        WHATEVER rows the queryset returned -- for a `queue_job_id`-
        filtered call (`turn_group_cards`'s own use of this function)
        the USER row never even appears in the query, so this is a
        no-op there, not a second, competing marker assignment."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER, text="q")
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="a",
                  state=Turn.State.DONE, queue_job_id=1)
        cards = thread_cards(conversation, queue_job_id=1)
        assert [c["role"] for c in cards] == ["assistant"]

    def test_a_delegates_turns_nest_under_the_delegating_tool_turn(self):
        """The ORDER is the mechanism, and it comes from `run_loop`:
        `invoke_tool` runs the whole delegate loop (writing its depth-1
        turns) BEFORE the parent's own TOOL row is created. So the deep
        turns precede their parent, and the grouper buffers them rather
        than looking ahead."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER, text="q")
        _tool_turn(conversation, depth=1, text="library searched",
                   invocation=_invocation(principal_key="library"))
        parent = _tool_turn(
            conversation, depth=0, text="the library said…",
            invocation=_invocation(tool_key="agent.library"),
            tool_call={"tool": "agent.library", "args": {"task": "t"},
                       "agent": "general", "id": "", "discarded": []},
        )
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="a",
                  state=Turn.State.DONE)

        cards = thread_cards(conversation)

        assert [c["role"] for c in cards] == ["user", "tool", "assistant"]
        assert cards[1]["turn"].pk == parent.pk
        assert [n["tool"]["key"] for n in cards[1]["nested"]] == ["rag.search"]

    def test_an_orphaned_deep_turn_still_renders_rather_than_vanishing(self):
        """A delegate that crashed before its parent's TOOL row was
        written leaves depth-1 turns with nothing to nest under.
        Dropping them would hide work that really happened."""
        conversation = make_conversation()
        _tool_turn(conversation, depth=1, invocation=_invocation())
        cards = thread_cards(conversation)
        assert len(cards) == 1 and cards[0]["depth"] == 1

    def test_an_assistant_turns_text_html_is_rendered_through_render_answer(self):
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  text="**bold**", state=Turn.State.DONE)
        card = thread_cards(conversation)[-1]
        assert card["text_html"] == "<p><strong>bold</strong></p>"

    def test_a_user_turns_text_html_is_none_the_template_falls_back_to_linebreaks(self):
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER, text="**not bold**")
        card = thread_cards(conversation)[-1]
        assert card["text_html"] is None

    def test_a_queued_assistant_turn_is_marked_pending_and_carries_no_text(self):
        conversation = make_conversation()
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         state=Turn.State.QUEUED)
        card = thread_cards(conversation)[-1]
        assert card["pending"] is True and card["state"] == Turn.State.QUEUED

    def test_a_failed_turn_carries_its_error_and_never_a_traceback(self):
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.FAILED, error="the worker stopped responding")
        card = thread_cards(conversation)[-1]
        assert card["error"] == "the worker stopped responding"
        assert "Traceback" not in card["error"]

    def test_a_timed_out_turns_error_is_flagged_for_the_admin_hint(self):
        """The content-withheld pattern (one-timeout task, 2026-09-17):
        `error_is_timeout` is computed HERE, in Python, against the ONE
        shared constant -- `_turn_card.html`'s FAILED branch reads this
        fixed key rather than comparing a duplicated string literal."""
        from agents.runtime.loop import TURN_TIMEOUT_ERROR

        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.FAILED, error=TURN_TIMEOUT_ERROR)
        card = thread_cards(conversation)[-1]
        assert card["error_is_timeout"] is True

    def test_a_failed_turn_with_a_different_error_is_not_flagged(self):
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.FAILED, error="the worker stopped responding")
        card = thread_cards(conversation)[-1]
        assert card["error_is_timeout"] is False

    def test_it_issues_a_bounded_number_of_queries(self, django_assert_num_queries):
        """A thread renders every turn's invocation and every turn's
        tool spec. Without `select_related` that is one query per turn,
        which turns a long conversation into a slow page for no
        reason."""
        conversation = make_conversation()
        for _ in range(5):
            _tool_turn(conversation, invocation=_invocation())
        with django_assert_num_queries(1):
            thread_cards(conversation)
