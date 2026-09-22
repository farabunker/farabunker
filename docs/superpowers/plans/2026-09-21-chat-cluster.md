# Chat cluster Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship three independently-mergeable chat features on one branch — a context-usage meter, an agent create/edit utility mounted in two areas, and edit-a-past-prompt as a branch.

**Architecture:** Feature A adds one pure estimator module at the agents column root (`agents/usage.py`), one pure read in `models/contracts/bindings.py`, a server-rendered line on the thread page and three integers on the existing poll bodies. Feature B adds one audience column (`Agent.box_wide`), four writers/readers in `agents/visibility.py`, one form-context builder, one field-form fragment and one edit route mounted from two list pages — reusing the existing `parse_entitlement_diff` → `set_agent_labels` seam and the shared transfer panel rather than inventing a second labelling path. Feature C adds two provenance columns, a `branch_conversation` sibling of `duplicate_conversation` in the visibility module, and a `turn_edit` view that calls it and then `start_turn`.

**Tech Stack:** Django 5.2, PostgreSQL, server-rendered templates, no static pipeline, no new dependency.

**Spec:** `docs/superpowers/specs/2026-09-21-chat-cluster-design.md` — FINAL. §16 records the owner's ruling on all eight §13 flags ("accept all recommendations"): the meter measures what is sent with the truncation clause; `Agent.box_wide` lands as a column with a `box_wide = resident` data migration; edit-past-prompt is BRANCH; the provenance columns land; `llm_role` stays administrator-only; attachments do not follow a branch; share recipients cannot edit-and-branch; the member-installs-box-wide route stays open and is made legible. None of these is reopenable by an implementer.

## Global Constraints

Every task's requirements implicitly include this section.

- **Worktree.** All work happens in the worktree **directory** `.claude/worktrees/model-management-framework`, which holds the **branch** `specs-queue-and-chat-cluster`. The two names differ; `git worktree list` is the authority. The repository root checkout is production and is never touched — not to read a file, not to run a test, not to deploy outside an announced window.
- **Tests and documentation ship in the same commit as the change.** Not a follow-up, not a separate PR. Missing tests or a stale doc means the task is not done.
- **The four runs are the gate**, in both feature-flag states and both collection orders:
  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools
  ```
  **One pytest process per session, never two runs against one database name.** `farabunker_impl` on port 5433 is this executor's own name; do not point a run at a bare `test_farabunker`.
- **Plus the two posture sweeps**, for every task in Phase B and Phase C (both change visibility, and Phase B's audience mechanism is identity-adjacent):
  ```bash
  FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  ```
- **Zero new `<script>` tags on the thread page.** `agents/chat/tests/test_thread.py` pins `body.count("<script") == 4` with the attach door present and `== 3` without. Feature A's live update goes inside the existing poller block; features B and C add no script at all.
- **No prose composed in JavaScript.** User-facing sentences are declared once, in Python. The poller sets `textContent` on number spans and toggles `hidden` on a pre-rendered clause; it never builds a sentence and never uses `innerHTML`.
- **Never-500.** Every new route answers 404 / 403 / a re-rendered form, never a traceback. `agents/chat/tests/test_never_500.py` derives its route list from `agents.chat.urls.urlpatterns` and raises `KeyError` for an unswept name, so a `/chat/` route and its four drivers land in the same commit.
- **Route matrix.** A name absent from `identity/routes.py::ROUTE_RULES` is treated as ADMIN and logged, and `identity/tests/test_route_matrix.py` fails immediately. Every new name is classified and gets a `_DRIVERS` entry in that module too.
- **Column boundaries.** No module under `agents/chat/` may touch `Agent`, `Conversation`, `Flow`, `Share` or `Workstream` `.objects` directly — everything goes through `agents/visibility.py`. `Turn` is **not** on that list (`foundation/ops/tests/test_column_boundaries.py::_VISIBILITY_MODELS`), which is why `agents/chat/service.py` already writes `Turn` rows and why `turn_edit` may too.
- **Render-vs-gate at data seams.** Administrator-only data — the full agent list, the owner column, the "engine default applies" sentence, the box-wide reach control, the role select — is **never built** on a non-admin render, not merely hidden by a template `{% if %}`.
- **Query pins are non-vacuous.** Every new pin compares a one-row render against a many-row render (the equality-under-scale shape `test_thread.py` and `test_sidebar.py` already use), never an absolute count that drifts with unrelated work. A warm-up request precedes both captures.
- **CSS ownership.** A selector's home is the deepest template that is an ancestor of every template using it (`foundation/ops/tests/test_css_ownership.py`). A fragment never carries its own `<style>`.
- **Reuse, never respecify.** Entitlement panes and the add/remove diff go through the existing `agents/chat/service.py::entitlement_panes` + `parse_entitlement_diff` + `agents/labels.py::set_agent_labels` chain and `foundation/templates/_transfer_panel.html`. Settings registration is the three-place `SETTINGS_GROUPS` + own urls module + `config/urls.py` mount pattern `agents/chat/assistant_urls.py` already set.
- **No AI model or vendor names in committed prose, and no absolute local paths**, in code, docs, tests, or this plan file — `foundation/ops/tests/test_docs_model_names.py` walks `docs/superpowers/**` since 2026-09-20.
- **House style.** No `conftest.py`; shared fixtures live in each app's `tests/_helpers.py`. Docstrings on new code. Commit subjects are `type(scope): subject`, imperative, lower case, no trailing period. A change that deliberately re-pins an existing test says so, by name, in its commit message.
- **Migrations are 0012 and 0013**, verified against the real tree: `agents/migrations/` ends at `0011_turn_author.py`, and no other app gains a migration in this plan (`identity/routes.py` and `identity/contracts/actions.py` are pure Python tables).

---

## File structure

**Feature A**

| File | Responsibility |
|---|---|
| `models/contracts/bindings.py` | `effective_context_window(resolved)` — the operative window and where it came from. One pure read. |
| `agents/usage.py` | NEW. `CHARS_PER_TOKEN`, `estimate_tokens`, `ContextUsage`, `context_usage`, `meter_segments`, the declared sentences. Column root, no Django views, no `agents/chat` import. |
| `agents/chat/views/thread.py` | `thread_context` builds `context_usage` from `check.resolved`. |
| `agents/chat/service.py` | `visible_conversation_or_404` widens its single-row read to carry the workstream. |
| `agents/visibility.py` | `visible_turn`'s `select_related` widened to `("conversation", "conversation__agent", "conversation__workstream")`. |
| `agents/chat/views/turns.py` | `_queued_body` / `_done_body` carry `"context"` (three integers). |
| `agents/chat/templates/chat/conversation.html` | The line, the bar, the disclosure, the meter CSS in its own `chat_style` block, the poller's context updater inside the existing `<script>`. |

**Feature B**

| File | Responsibility |
|---|---|
| `agents/models.py` | `Agent.box_wide`. |
| `agents/migrations/0012_agent_box_wide.py` | Column plus the `box_wide = resident` data migration. |
| `agents/limits.py` | `MAX_STEPS_CEILING`. |
| `agents/defaults.py` | `install_default`'s agent `_fields()` stamps `box_wide=True`. |
| `agents/visibility.py` | `may_manage_agent`, `editable_agents`, `box_wide_agents_owned_by`, `create_agent`, `update_agent`, the refusal sentences; `visible_agents` / `installed_agent_slugs` swap the `resident` leg for `box_wide`. |
| `identity/contracts/actions.py` | `AGENT_CREATED`, `AGENT_EDITED`, registered in `AUDIT_ACTIONS`. |
| `agents/chat/agentform.py` | NEW. `agent_form_context(principal, *, agent=None, posted=None, settings_row=None)` — plain data, no manager access. |
| `agents/chat/views/agents.py` | NEW. `agent_list`, `agent_new`, `agent_edit` (two POST paths: fields, and labels). |
| `agents/chat/views/agents_admin.py` | NEW. The settings-area list. |
| `agents/chat/agent_admin_urls.py` | NEW. One route, the `assistant_urls.py` shape. |
| `agents/chat/templates/chat/_agent_form.html` | NEW. The one field form, a fragment: no `<style>`, no `<script>`. |
| `agents/chat/templates/chat/agent_edit.html` | NEW. The page both the create and the edit route render — the field form, and (on edit) the entitlement panel as its **sibling**. |
| `agents/chat/templates/chat/agents.html` | NEW. The user-facing list, plus the read-only box-wide section. |
| `agents/chat/templates/chat/agents_admin.html` | NEW. The settings-area list. |
| `foundation/templates/_settings.html`, `foundation/templates/_shell.html` | The settings sidebar link; the promoted `.transfer-*` rules. |
| `agents/chat/templates/chat/_sidebar.html` | The rail entry. |
| `foundation/settings_area.py` | One `Entry("Agent library", "settings-agents", ADMIN)`. |
| `agents/chat/urls.py`, `config/urls.py`, `identity/routes.py` | Three `/chat/` routes, one settings mount, five classifications. |

**Feature C**

| File | Responsibility |
|---|---|
| `agents/models.py` | `Conversation.branched_from`, `Conversation.branched_at_index`. |
| `agents/migrations/0013_conversation_branch.py` | Two nullable columns. |
| `agents/visibility.py` | `may_edit_turn`, `branch_conversation`; `duplicate_conversation` copies `author_id`. |
| `agents/chat/rendering.py` | `turn_card` / `thread_cards` / `turn_group_cards` carry `may_edit` and `may_attach_files`. |
| `agents/chat/views/turns.py` | `turn_edit` — validate, branch, `start_turn`, redirect. |
| `agents/chat/views/thread.py` | The provenance line's context. |
| `agents/chat/templates/chat/_turn_card.html`, `conversation.html`, `base.html` | The disclosure, the provenance line, the disclosure's CSS. |

---

## Deviations from the spec, and why

Two places this plan builds something other than what §3.6/§8 describe. Each was checked against the tree before deciding; an implementer does **not** get to re-open them, and each carries its own test. (A third deviation stood in the first draft of this plan — the Feature C edit disclosure sitting out the poller's `done` tick — and was **withdrawn** at review. See `## Plan review`, M1.)

1. **`context_usage` costs TWO queries, not one** (§8 says "exactly one"). The flat `values_list("role", "text")[:HISTORY_TURNS]` gives the replayed corpus, but `ContextUsage.total_turns` — which the owner-binding truncation clause of flag 1 renders as "the oldest R of T messages are no longer sent" — needs a real count of the replayable set, and a `LIMIT 20` slice cannot produce one. So the same filtered queryset is asked twice: once sliced, once `.count()`. Both are **constant in the conversation's length**, which is what the equality-under-scale pins actually assert. **No index claim is made for them**: `agents/models.py::Turn.Meta.indexes` is exactly `[models.Index(fields=["conversation", "index"], name="agents_turn_thread")]` and no migration adds another, so the `(state, depth)` half of this filter is a scan within one conversation's rows — bounded by the thread's own length, which is the only bound that matters here and is the same bound the page's existing per-turn render already pays. The alternative (a `Window(Count("*"))` annotation) would be one query and a materially harder thing for the next reader to verify.

2. **`visible_conversation_or_404` widens its read to `select_related("workstream")`.** §8 claims "the workstream row is already loaded on this render". It is not: `agents/visibility.py::visible_conversations` only `select_related("agent")`, and `agents/chat/views/thread.py` dereferences `conversation.workstream` **only** on the `may_upload_here` branch — its own comment says so in as many words. Without this widening, `context_usage` would add a lazy FK read for every reader who may not upload (a view-share recipient, a stream recipient who may not manage it). This is the exact shape of review R2's fix for `visible_turn`, applied to the page path: one more LEFT JOIN on a query this path already runs. It is scoped to the nine row-addressed `/chat/` views and changes no list query.


---

## Phase A — context usage in chat

### Task 1: The operative context window

**Files:**
- Modify: `models/contracts/bindings.py` (add `effective_context_window` after `embed_dim_from_fingerprint`)
- Modify: `models/registry/README.md` (the reconciliation section)
- Test: `models/contracts/tests/test_bindings.py`

**Interfaces:**
- Consumes: `models.contracts.bindings.ResolvedModel` (`engine: str`, `config: dict`); `models.contracts.engines.ollama.OllamaEngine.name`, `DEFAULT_CONTEXT_WINDOW = 8192` — both already imported or importable in that module.
- Produces: `effective_context_window(resolved) -> tuple[int, str]`, where the second element is one of `"connection"`, `"engine-default"`, `"unknown"`.

- [ ] **Step 1: Write the failing test**

Create `models/contracts/tests/test_bindings.py` if it does not exist; otherwise append this class.

```python
"""`effective_context_window` — the number the engine will actually be
asked to allocate, and where it came from."""
from __future__ import annotations

from models.contracts.bindings import ResolvedModel, effective_context_window
from models.contracts.engines.ollama import DEFAULT_CONTEXT_WINDOW, OllamaEngine


def _resolved(**config):
    return ResolvedModel(OllamaEngine.name, "a-chat-model", "http://localhost:11434",
                         config=config)


class TestTheEffectiveContextWindow:
    def test_an_operator_set_value_wins(self):
        assert effective_context_window(_resolved(context_window=32768)) == (32768, "connection")

    def test_an_unset_value_falls_back_to_the_engines_own_bounded_default(self):
        assert effective_context_window(_resolved()) == (
            DEFAULT_CONTEXT_WINDOW, "engine-default")

    def test_an_engine_that_declares_no_default_says_unknown(self):
        other = ResolvedModel("some-other-engine", "m", "http://localhost:1", config={})
        assert effective_context_window(other) == (0, "unknown")

    def test_a_value_that_will_not_cast_degrades_rather_than_raising(self):
        assert effective_context_window(_resolved(context_window="wide")) == (0, "unknown")

    def test_a_zero_or_negative_stored_value_is_not_a_ceiling(self):
        assert effective_context_window(_resolved(context_window=0)) == (0, "unknown")
        assert effective_context_window(_resolved(context_window=-1)) == (0, "unknown")

    def test_a_none_config_is_not_a_crash(self):
        bare = ResolvedModel(OllamaEngine.name, "m", "http://localhost:1")
        assert effective_context_window(bare) == (DEFAULT_CONTEXT_WINDOW, "engine-default")
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest -q models/contracts/tests/test_bindings.py -k EffectiveContextWindow`
Expected: FAIL — `ImportError: cannot import name 'effective_context_window'`.

- [ ] **Step 3: Write the implementation**

In `models/contracts/bindings.py`, add the import and the function (the module already imports `OllamaEngine`; widen that line to carry the constant too):

```python
from models.contracts.engines.ollama import DEFAULT_CONTEXT_WINDOW, OllamaEngine
```

```python
# THE ENGINES THAT DECLARE A BOUNDED DEFAULT WINDOW OF THEIR OWN. A
# future adapter adds itself here; the function grows a lookup, never a
# special case. An engine absent from this map answers "unknown" rather
# than borrowing somebody else's number.
_ENGINE_DEFAULT_WINDOWS: dict[str, int] = {OllamaEngine.name: DEFAULT_CONTEXT_WINDOW}


def effective_context_window(resolved: ResolvedModel) -> tuple[int, str]:
    """The window this binding will ask its engine to allocate, and where
    it came from.

    `(n, "connection")`      -- an operator set `ModelConnection.context_window`.
    `(n, "engine-default")`  -- the engine adapter's own bounded default applies.
    `(0, "unknown")`         -- this engine declares no default here; say so.

    A PURE READ OF A VALUE THE PLATFORM ALREADY RESOLVED. It never probes
    an engine for an architecture maximum -- ADR 0010's own fix sentence
    forbids exactly that, and a display probe is still a probe, one per
    page render, against a machine that may be asleep. It never shows a
    model's theoretical maximum either: if the engine is asked for 8,192
    then 8,192 is the number that truncates the conversation, and showing
    a larger one would tell the reader they have room they do not have.
    It writes nothing and sends nothing.

    THE `int()` CAST AND ITS DEGRADATION are the same defence
    `tools.rag.views._resolved_answer_context_window` already records for
    its own cast: nothing enforces that a stored `context_window` is an
    integer, and an uncastable one reaching an arithmetic comparison is a
    500 on a never-500 surface. A non-positive value is treated the same
    way -- it is not a ceiling anybody could act on.

    IT ANSWERS DIFFERENTLY FROM `tools.rag.views.
    _resolved_answer_context_window`, ON PURPOSE. That reader returns
    "unknown" where this one returns the adapter's bounded default,
    because it decides whether to run a top-k FIT CHECK at all: skipping
    a check on a guessed number is safe, and refusing a legitimate `k`
    for a reason the operator never configured is not. This one must
    DISPLAY something, and the adapter's default is what the engine will
    actually be asked for. The two also ask about different bindings.
    `models/registry/README.md` records the reconciliation.
    """
    raw = (resolved.config or {}).get("context_window")
    if raw is not None:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 0, "unknown"
        return (value, "connection") if value > 0 else (0, "unknown")
    default = _ENGINE_DEFAULT_WINDOWS.get(resolved.engine)
    if default is None:
        return 0, "unknown"
    return default, "engine-default"
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -q models/contracts/tests/test_bindings.py -k EffectiveContextWindow`
Expected: PASS, 6 passed.

- [ ] **Step 5: Update the registry README**

In `models/registry/README.md`, under the section that describes `ModelConnection`, add:

```markdown
### `context_window` is now read for display as well as sent

`ModelConnection.context_window` rides into `ResolvedModel.config` through
`resolved_from_connection` and is sent to the engine adapter. Since the chat
context meter it is also **read for display**, through
`models/contracts/bindings.py::effective_context_window`, which answers the
operator's value when there is one and the engine adapter's own bounded
default when there is not. **No engine probe was added**: nothing anywhere
reads an engine-reported architecture maximum, and ADR 0010's incident
write-up is why.

Two readers of the same column answer differently, deliberately.
`tools/rag/views.py::_resolved_answer_context_window` returns "unknown"
where `effective_context_window` returns the adapter's default: the first
decides whether to run a top-k fit check at all (skipping a check on a
guessed number is safe; refusing a legitimate `k` for a reason the operator
never configured is not), and the second must display something. They also
ask about different bindings — the fit check about the library's answer
role, the meter about the conversation's own chat binding or the picked
connection — so they are not two answers to one question.
```

- [ ] **Step 6: Run the module suite in both collection orders**

Run: `.venv/bin/pytest -q models`
Expected: PASS.
Run: `.venv/bin/pytest -q models tools foundation`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add models/contracts/bindings.py models/contracts/tests/test_bindings.py models/registry/README.md
git commit -m "feat(models): effective_context_window — the operative window, never an engine probe"
```

---

### Task 2: The estimator

**Files:**
- Create: `agents/usage.py`
- Create: `agents/tests/test_usage.py`
- Modify: `agents/README.md`

**Interfaces:**
- Consumes: `agents.limits.HISTORY_TURNS` (20); `agents.models.Turn` (`State.DONE`, `role`, `text`, `index`, `depth`, related name `conversation.turns`); `Agent.system_prompt`; `Conversation.workstream.instructions`.
- Produces:
  - `CHARS_PER_TOKEN = 4`
  - `estimate_tokens(text: str | None) -> int`
  - `ContextUsage` — frozen dataclass with `estimated_tokens: int`, `window: int`, `window_source: str`, `percent: int | None`, `band: str`, `replayed_turns: int`, `total_turns: int`, `truncated: bool`
  - `context_usage(conversation, agent, *, window: int, window_source: str) -> ContextUsage`
  - `meter_segments(usage: ContextUsage) -> MeterSegments` with fields `lead: str`, `middle: str`, `tail: str`, `has_percent: bool`
  - `truncation_clause(usage: ContextUsage) -> str`
  - Constants `WINDOW_SOURCE_CONNECTION`, `WINDOW_SOURCE_ENGINE_DEFAULT`, `WINDOW_SOURCE_UNKNOWN`, `WINDOW_SOURCE_UNBOUND`, `BAND_OK`, `BAND_HIGH`, `BAND_FULL`, `BAND_UNKNOWN`, `HIGH_PERCENT = 70`, `FULL_PERCENT = 90`, `FULL_CLAUSE`, `ENGINE_DEFAULT_SENTENCE`, `DISCLOSURE_SUMMARY`, `DISCLOSURE_BODY`.
- Consumed by: Task 3 (`thread_context`), Task 4 (`_queued_body`/`_done_body`).

- [ ] **Step 1: Write the failing test**

Create `agents/tests/test_usage.py`:

```python
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
    def _conversation_of(self, count):
        agent = make_agent(system_prompt="")
        conversation = make_conversation(agent=agent)
        for _ in range(count):
            _done(conversation, "x" * CHARS_PER_TOKEN)
        return conversation, agent

    def test_twenty_five_and_forty_turns_estimate_the_same(self):
        short, short_agent = self._conversation_of(HISTORY_TURNS + 5)
        long, long_agent = self._conversation_of(HISTORY_TURNS + 20)
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
                         tool_call={"tool": "rag.search", "tool_kwargs": {"q": "z" * 2000}})
        emitted = sum(len(m.content) for m in tool_turn_messages(turn))
        usage = context_usage(conversation, agent, window=8192,
                              window_source=WINDOW_SOURCE_CONNECTION)
        assert usage.estimated_tokens < estimate_tokens("x" * emitted)

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
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest -q agents/tests/test_usage.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.usage'`.

- [ ] **Step 3: Write the implementation**

Create `agents/usage.py`:

```python
"""What the next turn's prompt will carry, as an estimate, and how the
page says it.

IT LIVES AT THE COLUMN ROOT, not inside `agents/chat`, for the same
reason `agents/visibility.py` and `agents/labels.py` do: a future
management command and a future MCP edge need the same answer without
either of them being a view. `Turn` is deliberately NOT one of the
models `foundation/ops/tests/test_column_boundaries.py` fences off from
`agents/chat` (`_VISIBILITY_MODELS` is Conversation/Agent/Flow/Share/
Workstream) -- `agents/chat/service.py` already reads and writes `Turn`
rows -- so this module's placement is about reuse, not about a boundary.

ONE COUNTER, NOT TWO. `agents/limits.py::HISTORY_TURNS`' own docstring
warns that a token counter beside the replay cap would be "a second,
drifting one". `estimate_tokens` below is therefore the ONLY arithmetic
in this feature, and chat compaction -- out of scope -- will call this
same function rather than growing its own.

THE ESTIMATE IS AN UNDER-COUNT, AND THAT IS STATED RATHER THAN HIDDEN.
Six things the replayed prompt carries are not counted here:

  1. The ASSISTANT `ToolCallBlock` a TOOL turn replays. `agents.runtime.
     prompt.history_messages` does not emit `Turn.text` for a
     `role == "tool"` row -- it calls `tool_turn_messages`, which emits
     TWO messages, the first carrying the tool's wire name and the full
     `tool_kwargs` JSON. None of that is in `Turn.text`.
  2. The fence a replayed ASSISTANT turn carries: `_replay_assistant_
     text` re-derives a fenced version from `turn.data`.
  3. `_FOREIGN_TURN_HEADER`, wrapped around another person's USER turn.
  4. Attached-file text -- inlined extract and attachments block. It
     varies with the acting principal, costs extra queries, and belongs
     to the turn being sent rather than to the conversation's history.
  5. Tool results the turn has not produced yet.
  6. The stream instructions' own labelled header (`prompt.py::
     _instructions_block`). Counting it would mean importing
     `agents.runtime.prompt` -- and with it the chat-message type and
     the engine surface -- into a module every thread render calls,
     which is the exact weight `agents/limits.py`'s own docstring
     exists to keep out of the rendering path.

The clock line is not counted either, and that is a BUDGET decision:
`ChatSettings` is read nowhere on a thread render (`time_aware_now_
line()` reads the singleton inside `build_messages`, at turn time), so
counting it would cost a second new query on the page and another on
every poll tick, for the smallest term in the estimate.

Under-counting is the right direction to be wrong in for a "when should
I compact" signal ONLY IF THE READER IS TOLD. `DISCLOSURE_BODY` below
tells them, and names tool calls explicitly because that is the one
exclusion large enough to change a decision.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from agents.limits import HISTORY_TURNS
from agents.models import Turn

# Disclosed in the UI, never presented as exact. There is no tokenizer on
# this box: `requirements.txt` carries none, adding one for a status line
# would be a new dependency for a cosmetic number, and asking the engine
# to tokenize would be a network call per page render on a surface whose
# whole design avoids per-render engine traffic.
CHARS_PER_TOKEN = 4

# Where the ceiling came from. The first three are
# `models.contracts.bindings.effective_context_window`'s own vocabulary,
# read here rather than retyped as literals at the call sites. The
# fourth is the VIEW's: `check.resolved is None` -- an unbound role, an
# unregistered pick, a label refusal -- never reaches that function at
# all, and is a different thing to say.
WINDOW_SOURCE_CONNECTION = "connection"
WINDOW_SOURCE_ENGINE_DEFAULT = "engine-default"
WINDOW_SOURCE_UNKNOWN = "unknown"
WINDOW_SOURCE_UNBOUND = "unbound"

BAND_OK = "ok"
BAND_HIGH = "high"
BAND_FULL = "full"
BAND_UNKNOWN = "unknown"

HIGH_PERCENT = 70
FULL_PERCENT = 90


@dataclass(frozen=True)
class ContextUsage:
    """What the next turn will cost, and how much room there is.

    `window` is 0 and `percent` is None whenever there is no ceiling to
    divide by -- an engine that declares no default, or no bound model
    at all. `truncated` is `total_turns > replayed_turns`: the first
    place this platform tells a reader that their long conversation is
    already being shortened before it is sent.
    """

    estimated_tokens: int
    window: int
    window_source: str
    percent: int | None
    band: str
    replayed_turns: int
    total_turns: int
    truncated: bool


@dataclass(frozen=True)
class MeterSegments:
    """The meter's sentence, split around the two numbers the page
    renders as spans.

    DECLARED IN PYTHON, IN ONE PLACE, which is the house rule -- and
    split rather than formatted whole because the two numbers must be
    addressable elements for the poller to rewrite by `textContent`.
    The template concatenates `lead` + the token span + `middle` + the
    percent span + `tail`; nothing composes prose in JavaScript.
    """

    lead: str
    middle: str
    tail: str
    has_percent: bool


FULL_CLAUSE = "Start a new conversation to keep the model's full attention."
ENGINE_DEFAULT_SENTENCE = (
    "No limit is set for this connection; the engine's own default applies."
)
DISCLOSURE_SUMMARY = "How this is worked out."
DISCLOSURE_BODY = (
    f"Only the last {HISTORY_TURNS} messages of a conversation are sent to the model; "
    "a longer conversation is already shortened before it is sent. The number is an "
    f"estimate, about {CHARS_PER_TOKEN} characters to a token, of the words in those "
    "messages. It does not count files attached to a message, or what a tool was asked "
    "and answered. The limit is the one this connection is set to use."
)


def estimate_tokens(text: str | None) -> int:
    """Characters over `CHARS_PER_TOKEN`, rounded up. The one arithmetic
    in this feature -- see the module docstring on why there is only
    one."""
    return math.ceil(len(text or "") / CHARS_PER_TOKEN)


def context_usage(conversation, agent, *, window: int, window_source: str) -> ContextUsage:
    """What the next turn's prompt will carry, for `conversation`.

    TWO QUERIES, BOTH CONSTANT IN THE CONVERSATION'S LENGTH, over one
    filtered queryset: the newest `HISTORY_TURNS` replayable rows as a
    flat `values_list`, and a `.count()` of the same set. The count is
    what `truncated` and the truncation clause are made of, and a
    `LIMIT` slice cannot produce it -- the spec's "exactly one query" was
    written before that clause became binding. What the equality-under-
    scale pins in `agents/chat/tests/test_thread.py` actually assert is
    that a one-turn and a many-turn conversation cost the SAME, which
    both of these do.

    NEITHER IS AN INDEX HIT ON THE WHOLE FILTER, and that is said here
    rather than glossed: `Turn.Meta.indexes` is exactly
    `agents_turn_thread` on `(conversation, index)`, so the `state`/
    `depth` half is a scan WITHIN one conversation's rows. That bound is
    the thread's own length -- the same bound the page's per-turn render
    already pays -- and it is the honest statement. A plan that claimed a
    composite index here would have been copied forward by whoever read
    it next.

    THE FILTER MATCHES `agents.runtime.prompt.history_messages` EXACTLY
    -- `state="done"`, `depth=0` -- because a meter that measured a
    different corpus from the one that is sent would be false in the
    direction that matters.
    """
    rows = conversation.turns.filter(state=Turn.State.DONE, depth=0)
    recent = list(rows.order_by("-index").values_list("text", flat=True)[:HISTORY_TURNS])
    total_turns = rows.count()

    chars = len(agent.system_prompt or "")
    stream = conversation.workstream
    if stream is not None and (stream.instructions or "").strip():
        chars += len(stream.instructions.strip())
    chars += sum(len(text or "") for text in recent)

    estimated = math.ceil(chars / CHARS_PER_TOKEN)
    percent = round(100 * estimated / window) if window > 0 else None
    if percent is None:
        band = BAND_UNKNOWN
    elif percent >= FULL_PERCENT:
        band = BAND_FULL
    elif percent >= HIGH_PERCENT:
        band = BAND_HIGH
    else:
        band = BAND_OK
    replayed = len(recent)
    return ContextUsage(
        estimated_tokens=estimated,
        window=window,
        window_source=window_source,
        percent=percent,
        band=band,
        replayed_turns=replayed,
        total_turns=total_turns,
        truncated=total_turns > replayed,
    )


def meter_segments(usage: ContextUsage) -> MeterSegments:
    """The meter's sentence for this state -- see `MeterSegments`."""
    if usage.window > 0:
        return MeterSegments(
            lead="Context ~",
            middle=f" of {usage.window:,} tokens (",
            tail="%) · estimate",
            has_percent=True,
        )
    if usage.window_source == WINDOW_SOURCE_UNBOUND:
        # The page's existing `unavailable` banner already says why there
        # is no ceiling. The meter does not repeat it.
        return MeterSegments(lead="Context ~", middle="",
                             tail=" tokens · estimate", has_percent=False)
    return MeterSegments(
        lead="Context ~", middle="",
        tail=" tokens · estimate · no limit set for this connection",
        has_percent=False,
    )


def truncation_clause(usage: ContextUsage) -> str:
    """The one clause that can newly become true mid-session, which is
    why the page renders it once and hides it rather than composing it
    on a poll tick."""
    dropped = max(0, usage.total_turns - usage.replayed_turns)
    return f"the oldest {dropped} of {usage.total_turns} messages are no longer sent"
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -q agents/tests/test_usage.py`
Expected: PASS.

- [ ] **Step 5: Document the module's place in the column**

In `agents/README.md`, in the section that lists the column-root modules beside `visibility.py` and `labels.py`, add:

```markdown
- **`agents/usage.py`** — what the next turn's prompt will carry, as an
  estimate, and the declared sentences that say it. At the column root for the
  same reason `visibility.py` and `labels.py` are: a future management command
  or MCP edge needs the same answer without being a view. It holds the one
  token arithmetic on this platform (`estimate_tokens`); chat compaction, when
  it is built, consumes this rather than growing a second, drifting counter —
  which is exactly what `agents/limits.py::HISTORY_TURNS`' own docstring warns
  against. Its estimate is a deliberate **under-count**, with six named
  exclusions in the module docstring and a disclosure on the page that names
  the two a reader can act on.
```

- [ ] **Step 6: Run the agents suite in both collection orders**

Run: `.venv/bin/pytest -q agents`
Expected: PASS.
Run: `.venv/bin/pytest -q agents foundation models tools`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agents/usage.py agents/tests/test_usage.py agents/README.md
git commit -m "feat(agents): usage.py — the one token estimator, with its exclusions named"
```

---

### Task 3: The meter on the page

**Files:**
- Modify: `agents/chat/service.py` (`visible_conversation_or_404`)
- Modify: `agents/chat/views/thread.py` (`thread_context`)
- Modify: `agents/chat/templates/chat/conversation.html` (`chat_style` block, `.composer-block`)
- Test: `agents/chat/tests/test_thread.py` (new class `TestTheContextMeter`)
- Modify: `agents/chat/README.md`

**Interfaces:**
- Consumes: `agents.usage.context_usage / meter_segments / truncation_clause / ContextUsage / WINDOW_SOURCE_UNBOUND / WINDOW_SOURCE_ENGINE_DEFAULT / ENGINE_DEFAULT_SENTENCE / FULL_CLAUSE / DISCLOSURE_SUMMARY / DISCLOSURE_BODY / BAND_FULL` (Task 2); `models.contracts.bindings.effective_context_window` (Task 1); `agents.runtime.preflight.Preflight.resolved`, already in hand as `check.resolved` in `thread_context`.
- Produces: context keys `"context_usage"` (a `ContextUsage`), `"context_meter"` (a `MeterSegments`), `"context_truncation_clause"` (str), `"context_full_clause"` (str or `""`), `"context_engine_default_sentence"` (str or `""` — built only for an administrator), `"context_disclosure_summary"`, `"context_disclosure_body"`. Task 4 consumes none of these; it carries three integers of its own.

- [ ] **Step 1: Write the failing test**

Append to `agents/chat/tests/test_thread.py`. That module already imports `make_admin`, `make_user`, `posture`, `sign_in`, `user_principal`, `grant`, `make_entitlement`, `bound_chat_role`, `owner_fields`, `POSTURE_ENTERPRISE`, `_workstream`, `CaptureQueriesContext` and `connection` — reuse them rather than adding second imports.

```python
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
        marker = body[body.index('id="context-truncation"'):]
        assert "hidden" in marker[:200]

    def test_the_truncation_clause_is_unhidden_on_a_long_conversation(
        self, client, bound_chat_role
    ):
        from agents.limits import HISTORY_TURNS

        conversation = make_conversation(agent=make_agent(slug="meter-long"))
        for _ in range(HISTORY_TURNS + 3):
            make_turn(conversation=conversation, text="hi", state=Turn.State.DONE)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        marker = body[body.index('id="context-truncation"'):]
        assert "hidden" not in marker[:200]
        assert f"the oldest 3 of {HISTORY_TURNS + 3} messages are no longer sent" in body

    def test_at_ninety_percent_the_page_says_what_to_do_about_it(
        self, client, bound_chat_role
    ):
        from agents.usage import CHARS_PER_TOKEN, FULL_CLAUSE

        bound_chat_role.context_window = 100
        bound_chat_role.save()
        agent = self._long_prompt_agent(CHARS_PER_TOKEN * 95)
        conversation = make_conversation(agent=agent)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert FULL_CLAUSE in body
        assert 'data-band="full"' in body

    def test_below_ninety_percent_it_does_not(self, client, bound_chat_role):
        from agents.usage import CHARS_PER_TOKEN, FULL_CLAUSE

        bound_chat_role.context_window = 100
        bound_chat_role.save()
        agent = self._long_prompt_agent(CHARS_PER_TOKEN * 10)
        conversation = make_conversation(agent=agent)
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert FULL_CLAUSE not in body
        assert 'data-band="ok"' in body

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
        from agents.usage import ENGINE_DEFAULT_SENTENCE

        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation = make_conversation(
                agent=make_agent(slug="meter-default"),
                **owner_fields(user_principal(member)))
            sign_in(client, member)
            member_body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
            assert ENGINE_DEFAULT_SENTENCE not in member_body
            client.logout()
            sign_in(client, make_admin())
            admin_body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert ENGINE_DEFAULT_SENTENCE in admin_body

    def test_the_script_count_is_unchanged(self, client, bound_chat_role):
        conversation = make_conversation(agent=make_agent(slug="meter-scripts"))
        body = client.get(reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert body.count("<script") == 4

    def test_the_meter_costs_the_same_on_a_short_and_a_long_conversation(
        self, client, bound_chat_role
    ):
        """Equality under scale -- the pin this feature's query budget is
        actually made of. A new CONSTANT query is allowed here; a
        per-turn one is not."""
        from agents.limits import HISTORY_TURNS

        agent = make_agent(slug="meter-scale")
        short = make_conversation(agent=agent)
        make_turn(conversation=short, text="hi", state=Turn.State.DONE)
        long_one = make_conversation(agent=agent)
        for _ in range(HISTORY_TURNS * 3):
            make_turn(conversation=long_one, text="hi", state=Turn.State.DONE)
        client.get(reverse("chat-conversation", args=[short.id]))   # warm-up, unmeasured
        with CaptureQueriesContext(connection) as one:
            assert client.get(reverse("chat-conversation", args=[short.id])).status_code == 200
        with CaptureQueriesContext(connection) as many:
            assert client.get(
                reverse("chat-conversation", args=[long_one.id])).status_code == 200
        assert len(many) == len(one)

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

        def _standalone_stream_reads(captured):
            return sum(1 for q in captured.captured_queries
                       if "agents_workstream" in q["sql"].lower()
                       and " join " not in q["sql"].lower())

        assert _standalone_stream_reads(stream_queries) == _standalone_stream_reads(
            loose_queries)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest -q agents/chat/tests/test_thread.py -k TestTheContextMeter`
Expected: FAIL on the first assertion — `assert "Context ~" in body`, with no meter in the markup.

- [ ] **Step 3: Widen the single-row conversation read**

In `agents/chat/service.py::visible_conversation_or_404`, append this paragraph to the docstring and change the return statement:

```python
    THE WORKSTREAM RIDES ALONG (context meter, 2026-09-21).
    `agents.visibility.visible_conversations` `select_related`s the
    AGENT only, and `agents.chat.views.thread.thread_context`
    dereferences `conversation.workstream` on its `may_upload_here`
    branch alone -- its own comment says so. So every reader who may not
    upload (a `view`-share recipient; a stream recipient who may not
    manage it) would pay a LAZY FK READ the moment
    `agents.usage.context_usage` asked for the stream's instructions.
    One more LEFT JOIN on a query this path already runs, scoped to the
    row-addressed `/chat/` views and to nothing else -- the same fix
    review R2 made for `visible_turn`'s poll path, applied here. The
    LIST pages are untouched.
    """
    return get_object_or_404(
        chat_surface_conversations(principal).select_related("workstream"),
        pk=conversation_id,
    )
```

- [ ] **Step 4: Build the meter context**

In `agents/chat/views/thread.py`, add `is_admin` to the existing `from identity.access import ...` line, and add two imports:

```python
from agents.usage import (
    BAND_FULL, DISCLOSURE_BODY, DISCLOSURE_SUMMARY, ENGINE_DEFAULT_SENTENCE, FULL_CLAUSE,
    WINDOW_SOURCE_ENGINE_DEFAULT, WINDOW_SOURCE_UNBOUND, context_usage, meter_segments,
    truncation_clause,
)
from models.contracts.bindings import effective_context_window
```

Immediately after the `check = preflight_turn(...)` call and before the `?pending=` block:

```python
    # THE PAGE PAYS NOTHING FOR THE CEILING. `check.resolved` is the
    # `ResolvedModel` for this turn's bound model -- the PICKED
    # connection when the operator used the picker, this agent's role
    # binding otherwise -- and the preflight above already holds it.
    #
    # THE BRANCH IS `check.resolved is None`, NEVER `check.ok` (spec
    # review m1). `preflight_turn`'s NO_TOOL_CALLING leg returns a
    # refusal carrying a real `ResolvedModel`: a turn that cannot run
    # because the bound model will not call tools still has a known
    # ceiling, and the meter shows it. Only a genuinely absent binding
    # -- an unbound role, an unregistered pick, a label refusal --
    # renders the ceiling-free line, beside the `unavailable` banner
    # this page already carries.
    if check.resolved is None:
        window, window_source = 0, WINDOW_SOURCE_UNBOUND
    else:
        window, window_source = effective_context_window(check.resolved)
    context_meter_usage = context_usage(conversation, conversation.agent,
                                        window=window, window_source=window_source)
    # RENDER-VS-GATE: this sentence names OPERATOR CONFIGURATION, so a
    # non-admin's render never BUILDS it -- it is not a template `{% if
    # %}` over a string that was computed anyway. `settings_row` is the
    # one already fetched for this render.
    engine_default_sentence = (
        ENGINE_DEFAULT_SENTENCE
        if context_meter_usage.window_source == WINDOW_SOURCE_ENGINE_DEFAULT
        and is_admin(principal, settings_row=settings_row)
        else ""
    )
```

and add these keys to the returned dict, beside `"unavailable"`:

```python
        # FEATURE A. The line is server-rendered in full; the poller
        # rewrites two number spans and unhides one pre-rendered clause,
        # and composes no prose at all (spec decisions 21-22).
        "context_usage": context_meter_usage,
        "context_meter": meter_segments(context_meter_usage),
        "context_truncation_clause": truncation_clause(context_meter_usage),
        "context_full_clause": (FULL_CLAUSE if context_meter_usage.band == BAND_FULL
                                else ""),
        "context_engine_default_sentence": engine_default_sentence,
        "context_disclosure_summary": DISCLOSURE_SUMMARY,
        "context_disclosure_body": DISCLOSURE_BODY,
```

- [ ] **Step 5: Render it**

First check what the page already loads: `grep -n "{% load" agents/chat/templates/chat/conversation.html`. If `humanize` is **not** already loaded there, render the token count bare (`{{ context_usage.estimated_tokens }}`) rather than adding a template library and an `INSTALLED_APPS` entry for one number — `meter_segments` already groups the *window* in Python with `:,`, and the script's `toLocaleString()` groups the live value. Do not add `django.contrib.humanize`.

In `{% block chat_style %}` — the page's own block, **not** `chat/base.html`, because the meter's only consumer is this page's own body and `foundation/ops/tests/test_css_ownership.py`'s rule puts a page-only selector in the page's own style block (the placement `chat/_thread_actions.html`'s own comment records for its neighbour row on this very page) — add before `{% endblock %}`:

```css
    /* THE CONTEXT METER (feature A). Page-only selectors: its one
       consumer is this page's own body. Colour tokens are the shell's
       own; no new token is declared here. */
    .context-meter { margin-top: .5rem; font-size: .85rem; }
    .context-meter .context-line { margin: 0; color: var(--muted); }
    .context-bar { height: 3px; border-radius: 2px; background: var(--border);
                   margin: .3rem 0; overflow: hidden; }
    .context-bar > span { display: block; height: 100%;
                          width: var(--context-fill, 0%); background: var(--ok); }
    .context-meter[data-band="high"] .context-bar > span { background: var(--accent); }
    .context-meter[data-band="full"] .context-bar > span { background: var(--danger); }
    .context-meter details { margin-top: .25rem; }
    .context-meter details > summary { cursor: pointer; color: var(--muted); }
```

Every token above is one `foundation/templates/_shell.html` really declares, verified at plan time: `--muted`, `--border`, `--accent`, `--ok`, `--danger`. It declares no `--rule` and no `--warn`; an earlier draft of this step used both, and neither exists in either the light or the dark block. Declare no new token here.

Then inside `.composer-block`, **after** the `{% if may_post %}…{% endif %}` composer block and **before** `{% include "chat/_thread_actions.html" %}`:

```html
  {% comment %}
  THE CONTEXT METER. Every sentence here is declared once in
  `agents/usage.py` and handed in by `thread_context`; this template
  concatenates the segments around two number spans and types no word
  of its own. `data-window` is written by the SERVER and is the only
  denominator the poller's script may read -- it never uses a number the
  response supplied, and the window never travels to the poll endpoint
  at all (spec decision 21).
  {% endcomment %}
  <div class="context-meter" id="context-meter" data-band="{{ context_usage.band }}"
       {% if context_usage.window %}data-window="{{ context_usage.window }}"{% endif %}
       style="--context-fill: {{ context_usage.percent|default_if_none:0 }}%;">
    <p class="context-line">{{ context_meter.lead }}<span id="context-tokens">{{ context_usage.estimated_tokens }}</span>{{ context_meter.middle }}{% if context_meter.has_percent %}<span id="context-percent">{{ context_usage.percent }}</span>{% endif %}{{ context_meter.tail }}</p>
    <div class="context-bar" aria-hidden="true"><span></span></div>
    <p class="context-line" id="context-truncation"{% if not context_usage.truncated %} hidden{% endif %}>{{ context_truncation_clause }}</p>
    {% if context_full_clause %}<p class="context-line">{{ context_full_clause }}</p>{% endif %}
    {% if context_engine_default_sentence %}
    <p class="context-line">{{ context_engine_default_sentence }}
      <a href="{{ setup_url }}">Models</a></p>
    {% endif %}
    <details>
      <summary>{{ context_disclosure_summary }}</summary>
      <p>{{ context_disclosure_body }}</p>
    </details>
  </div>
```

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -q agents/chat/tests/test_thread.py -k TestTheContextMeter`
Expected: PASS.
Run: `.venv/bin/pytest -q foundation/ops/tests/test_css_ownership.py agents/chat/tests/test_thread.py agents/chat/tests/test_never_500.py`
Expected: PASS — the CSS gate accepts page-only selectors in the page's own block, and the script counts are still 4 and 3.

- [ ] **Step 7: Document what the line means**

Add to `agents/chat/README.md`:

```markdown
## What the context line means

Below the composer, the thread page renders one line: the estimated size of the
prompt the **next** turn will carry, the ceiling that prompt will be given, and
the percentage between them.

It is an **estimate**, and the page says so every time it is shown. There is no
tokenizer on this box and no per-render engine call; the number is characters
over four, and the `<details>` beside the line discloses exactly that.

It measures **what is sent, not what the conversation holds.** Only the last
`agents/limits.py::HISTORY_TURNS` replayable turns reach the prompt, so a long
conversation's meter plateaus — and the line then says how many of how many
messages are no longer sent. That clause is the first place this platform tells
a reader their conversation is already being shortened before it is sent.

Six things it does not count, every one an under-count; `agents/usage.py`'s
module docstring names them all. The two a reader can act on — files attached to
a message, and what a tool was asked and answered — are named in the disclosure
on the page.

The ceiling is the **operative** window: what the engine will actually be asked
to allocate. An operator's own per-connection value when there is one, the engine
adapter's bounded default otherwise — with a sentence saying so that only an
administrator's render builds. Nothing probes an engine for an architecture
maximum.

The line refreshes live through the page's existing poller, at the two moments
the replayed corpus grows: when a turn is queued, and when it finishes. The poll
body carries three integers and nothing else. The window, the bands and every
sentence stay with the page, which is the only place that knows the picker's
current selection.
```

- [ ] **Step 8: Commit**

```
git add agents/chat/service.py agents/chat/views/thread.py agents/chat/templates/chat/conversation.html agents/chat/tests/test_thread.py agents/chat/README.md
git commit -m "feat(chat): the context meter on the thread page"
```

---

### Task 4: The meter, live

**Files:**
- Modify: `agents/visibility.py` (`visible_turn`)
- Modify: `agents/chat/views/turns.py` (`_context_body`, `_queued_body`, `_done_body`)
- Modify: `agents/chat/templates/chat/conversation.html` (inside the existing `<script>`)
- Test: `agents/chat/tests/test_thread.py` (new class `TestTheContextMeterOnThePollPath`)

**Interfaces:**
- Consumes: `agents.usage.context_usage`, `agents.usage.WINDOW_SOURCE_UNBOUND` (Task 2). Called with `window=0` because **the window never travels**; only `estimated_tokens`, `replayed_turns` and `total_turns` are read off the result.
- Produces: `_context_body(turn) -> dict` and the `"context"` key on the `queued` and `done` poll bodies — `{"estimated_tokens": int, "replayed_turns": int, "total_turns": int}` and nothing else. `running`, `failed` and `cancelled` carry no such key.

- [ ] **Step 1: Write the failing test**

Append to `agents/chat/tests/test_thread.py`:

```python
class TestTheContextMeterOnThePollPath:
    """Three integers, on two of the five bodies, with no binding
    resolved anywhere on this path (spec review M3, m7, R2)."""

    def _queued_pair(self):
        conversation = make_conversation(agent=make_agent(slug="poll-meter"))
        make_turn(conversation=conversation, text="hello", state=Turn.State.DONE)
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                              text="", state=Turn.State.QUEUED, queue_job_id=1)
        return conversation, assistant

    def test_a_queued_body_carries_the_three_integers_and_nothing_else(
        self, client, fake_queued_job
    ):
        _conversation, assistant = self._queued_pair()
        body = client.get(reverse("chat-turn-status", args=[assistant.pk])).json()
        assert set(body["context"]) == {"estimated_tokens", "replayed_turns", "total_turns"}
        assert all(isinstance(value, int) for value in body["context"].values())

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

    def test_no_window_no_percentage_and_no_sentence_ever_travel(self, client):
        conversation = make_conversation(agent=make_agent(slug="poll-window"))
        assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                              text="a", state=Turn.State.DONE, queue_job_id=3)
        serialized = str(client.get(
            reverse("chat-turn-status", args=[assistant.pk])).json())
        assert "window" not in serialized
        assert "percent" not in serialized
        assert "estimate" not in serialized

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
        assert set(body) == {"estimated_tokens", "replayed_turns", "total_turns"}
        assert len(captured) == 2, [q["sql"] for q in captured.captured_queries]

    def test_the_select_related_is_wide_enough_to_keep_the_budget_honest(self, client):
        """THE PIN THAT PROVES `visible_turn`'s widened `select_related`
        (spec review R2). Narrowing it back to `("conversation",)` adds
        two lazy FK reads to every tick of every open tab, and turns
        this red rather than quietly costing them."""
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
        assert len(stream_queries) == len(loose_queries)

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
```

If `?connection=<pk>` is not the picker's real parameter shape, read `agents/chat/pickers.py::chat_picker_options` and use whatever value that function accepts; do not invent one.

**These tests go in `test_thread.py`, not in the poll endpoint's own `agents/chat/tests/test_turn_status.py`, and that is a choice rather than an oversight.** That module exists and on module-ownership grounds would be the better home; spec §9 names `test_thread.py` instead, and this plan follows it for one reason beyond obedience: every assertion in this class is about *the relationship between the page and the tick* — the same ceiling, the same three integers, the same script count — and splitting the two halves across two modules is how they come to disagree. One line here so the next reader knows it was decided.

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest -q agents/chat/tests/test_thread.py -k TestTheContextMeterOnThePollPath`
Expected: FAIL — `KeyError: 'context'` on the first body assertion.

- [ ] **Step 3: Widen `visible_turn`**

In `agents/visibility.py::visible_turn`, append to the docstring and change the return:

```python
    THE `select_related` CARRIES THE AGENT AND THE WORKSTREAM, not the
    conversation alone (context meter, spec review R2).
    `agents.chat.views.turns`' queued and done bodies call
    `agents.usage.context_usage(turn.conversation, ...)`, which reads
    `agent.system_prompt` and `conversation.workstream.instructions` --
    neither of which is loaded on this path. Without these two JOINs the
    poll path would pay TWO LAZY FK READS on every tick of every open
    tab, for the price of two more JOINs on a query it already runs (a
    nullable FK, which Django resolves with a LEFT JOIN). The thread
    page is unaffected -- there both rows are genuinely already in hand.
    `agents/chat/tests/test_thread.py::TestTheContextMeterOnThePollPath
    ::test_the_select_related_is_wide_enough_to_keep_the_budget_honest`
    is what turns red if a later reader narrows it back.
    """
    return Turn.objects.filter(
        pk=turn_id, conversation__in=visible_conversations(principal)
    ).select_related("conversation", "conversation__agent",
                     "conversation__workstream").first()
```

- [ ] **Step 4: Carry the three integers**

In `agents/chat/views/turns.py`, add the import `from agents.usage import WINDOW_SOURCE_UNBOUND, context_usage` and this helper above `_queued_body`:

```python
def _context_body(turn) -> dict:
    """THREE INTEGERS AND NOTHING ELSE -- no window, no percentage, no
    sentence (spec decisions 21-22, review M3).

    This endpoint holds NO conversation-level render context: no
    `settings_row`, no wall, no `ToolAccess`, no `Preflight`, and
    critically NO PICKED CONNECTION, because the picker's selection
    lives in the thread page's `?connection=` query string, which
    nothing on this path ever sees. A body that computed its own
    denominator would have to run `preflight_turn` on every tick of
    every open tab -- the exact inverse of "the page pays nothing for
    it" -- and would answer with the AGENT's role binding where the page
    answered with the PICKED connection, so the ceiling would silently
    change mid-thread for anyone using the picker.

    So the window stays with the page. `window=0` here is not a value
    anybody renders: only `estimated_tokens`, `replayed_turns` and
    `total_turns` are read off the result, and the page's own
    server-written `data-window` is the only denominator that exists.
    """
    usage = context_usage(turn.conversation, turn.conversation.agent,
                          window=0, window_source=WINDOW_SOURCE_UNBOUND)
    return {
        "estimated_tokens": usage.estimated_tokens,
        "replayed_turns": usage.replayed_turns,
        "total_turns": usage.total_turns,
    }
```

Add this key to the dict `_queued_body` returns and to the dict `_done_body` returns, with this comment above it in both:

```python
        # TWO BODIES CARRY THE KEY, NOT ONE (spec review m7).
        # `agents.chat.service.start_turn` writes the USER turn DONE in
        # the SAME transaction as the QUEUED placeholder, so the
        # replayed corpus grows at QUEUE time, not at finish -- a meter
        # that waited for `done` would be stale for the whole in-flight
        # window, which is exactly when the reader is deciding whether
        # to compact, and is the same shape as round 13's stale-strip
        # lesson. `_running_body`, `_failed_body` and `_cancelled_body`
        # do NOT carry it: the corpus does not change between queue and
        # finish, and a failed or cancelled turn adds no replayable text.
        "context": _context_body(turn),
```

- [ ] **Step 5: Update the meter from the poller**

In `agents/chat/templates/chat/conversation.html`, inside the **existing** `{% block scripts %}` `<script>` — no new tag — add this function beside `showCardError`:

```javascript
  // THE CONTEXT METER'S LIVE HALF. No prose is composed here: the
  // server rendered every sentence, and this writes `textContent` on
  // two number spans, toggles `hidden` on one pre-rendered clause, and
  // sets the band and the bar width. No `innerHTML` -- the same rule
  // `showCardError` already keeps for text the page did not build. The
  // denominator is `data-window`, WRITTEN BY THE SERVER, never a number
  // the response supplied.
  function applyContext(context) {
    var meter = document.getElementById("context-meter");
    if (!meter || !context) { return; }
    var tokens = document.getElementById("context-tokens");
    if (tokens) { tokens.textContent = context.estimated_tokens.toLocaleString(); }
    var ceiling = parseInt(meter.getAttribute("data-window") || "", 10);
    var percentSpan = document.getElementById("context-percent");
    if (percentSpan && ceiling > 0) {
      var percent = Math.round((100 * context.estimated_tokens) / ceiling);
      percentSpan.textContent = String(percent);
      meter.setAttribute("data-band",
                         percent >= 90 ? "full" : (percent >= 70 ? "high" : "ok"));
      meter.style.setProperty("--context-fill", percent + "%");
    }
    var truncation = document.getElementById("context-truncation");
    if (truncation) { truncation.hidden = context.total_turns <= context.replayed_turns; }
  }
```

and call it in the one place both carrying bodies pass through — immediately after `var data = result.data;` and the `result.status === 503` early return:

```javascript
          applyContext(data.context);
```

This is a named helper of about a dozen lines rather than the spec's "four lines"; the budget that is actually pinned is the `<script>` **tag** count, which is unchanged at 4 and 3.

**This block is touched exactly twice in this plan, and this is the first time.** Task 13 adds a second small helper to the same `<script>` — `carryConnection`, four lines that copy the composer's own server-rendered hidden `connection` field into each swapped block, closing the one polled-vs-reload difference feature C would otherwise leave. It is specified there rather than here because the field it copies does not exist until Phase C, and dead code in Phase A would be worse than a forward reference. The tag count is unchanged by both.

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -q agents/chat/tests/test_thread.py`
Expected: PASS, including both meter classes and the existing script-count pins.
Run: `.venv/bin/pytest -q agents/chat agents/tests foundation/ops`
Expected: PASS.

- [ ] **Step 7: Run the four runs**

```
export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools
```
Expected: four green runs.

- [ ] **Step 8: Commit**

```
git add agents/visibility.py agents/chat/views/turns.py agents/chat/templates/chat/conversation.html agents/chat/tests/test_thread.py
git commit -m "feat(chat): the context meter refreshes on the existing poller"
```

---

## Phase B — the agent-edit utility

Every task in this phase runs the four runs **plus the two posture sweeps**: the audience mechanism is identity-adjacent, and `visible_agents` is a visibility function.

### Task 5: `Agent.box_wide` — the audience column

**Files:**
- Modify: `agents/models.py` (`Agent`)
- Create: `agents/migrations/0012_agent_box_wide.py`
- Modify: `agents/visibility.py` (`visible_agents`, `installed_agent_slugs`)
- Modify: `agents/defaults.py` (`install_default`'s agent `_fields()`)
- Modify: `agents/README.md`
- Test: `agents/tests/test_models.py` (or a new `agents/tests/test_agent_authoring.py` — put the audience tests there, since Tasks 6–10 extend that module)

**Interfaces:**
- Consumes: nothing new.
- Produces: `Agent.box_wide` — `BooleanField(default=False)`. Every existing row is migrated to `box_wide = resident`, so behaviour on the day this lands is byte-identical. Tasks 6–11 read and write it.

**Why a new column and not an overload of `resident`:** `Agent`'s own docstring records ruling 3 — `resident=True` is an **origin marker, not a lock** — and it has a second reader, `agents/visibility.py::resident_agent_tool_keys`, which filters on it to warn the tool-label page that labelling a tool weakens the shell path. Overloading it would make that warning fire for agents that were never shipped defaults, and would make the new form's audience control lie about where a row came from. Owner ruling (§16, flag 2) accepts the column.

- [ ] **Step 1: Write the failing test**

Create `agents/tests/test_agent_authoring.py`:

```python
"""`Agent.box_wide` -- the audience column, distinct from `resident`
the origin marker -- and the writers that read it."""
from __future__ import annotations

import pytest

from agents.models import Agent
from agents.tests._helpers import make_agent
from agents.visibility import installed_agent_slugs, visible_agents
from identity.contracts.principals import Principal

pytestmark = pytest.mark.django_db


class TestTheAudienceColumn:
    def test_a_new_agent_is_private_by_default(self):
        assert make_agent(slug="private-one").box_wide is False

    def test_box_wide_is_independent_of_resident(self):
        """`resident` records ORIGIN and keeps its other reader
        (`resident_agent_tool_keys`'s shell-path warning). `box_wide`
        records AUDIENCE. An administrator may make a shipped default
        private, or put an agent they wrote in front of everybody,
        without either act being a statement about where it came from."""
        shipped_but_private = make_agent(slug="shipped-private", resident=True,
                                         box_wide=False)
        home_grown_but_public = make_agent(slug="home-public", resident=False,
                                           box_wide=True)
        assert shipped_but_private.resident is True
        assert shipped_but_private.box_wide is False
        assert home_grown_but_public.resident is False
        assert home_grown_but_public.box_wide is True

    def test_visible_agents_reaches_a_box_wide_row_for_a_stranger(self):
        make_agent(slug="everyones", box_wide=True,
                   owner_kind="user", owner_key="99")
        stranger = Principal("user", "1")
        assert "everyones" in set(visible_agents(stranger).values_list("slug", flat=True))

    def test_visible_agents_does_NOT_reach_a_resident_row_that_is_not_box_wide(self):
        """The leg swapped, and it really swapped: a row marked only as
        a shipped default no longer reaches everybody by origin alone."""
        make_agent(slug="origin-only", resident=True, box_wide=False,
                   owner_kind="user", owner_key="99")
        stranger = Principal("user", "1")
        assert "origin-only" not in set(
            visible_agents(stranger).values_list("slug", flat=True))

    def test_installed_agent_slugs_moved_with_it(self):
        make_agent(slug="everyones-too", box_wide=True, enabled=False,
                   owner_kind="user", owner_key="99")
        stranger = Principal("user", "1")
        assert "everyones-too" in set(installed_agent_slugs(stranger))


class TestTheShippedCatalogueIsThePlatformsOffer:
    def test_installing_a_default_stamps_box_wide(self):
        from agents.defaults import DEFAULT_AGENTS, install_default

        slug = DEFAULT_AGENTS[0].slug
        row, created = install_default("agent", slug, Principal("open", "box"))
        assert created is True
        assert row.box_wide is True
        assert row.resident is True

    def test_a_reset_re_stamps_it_along_with_every_other_field(self):
        """`install_default`'s own rule: a reset is a FRESH ADOPTION of
        the shipped text, not a partial patch -- so every field a fresh
        install would set is set again, this one included."""
        from agents.defaults import DEFAULT_AGENTS, install_default

        slug = DEFAULT_AGENTS[0].slug
        row, _created = install_default("agent", slug, Principal("open", "box"))
        Agent.objects.filter(pk=row.pk).update(box_wide=False)
        again, created = install_default("agent", slug, Principal("open", "box"),
                                         reset=True)
        assert created is False
        assert again.box_wide is True

    def test_installing_a_flow_default_still_works(self):
        """`box_wide` is an AGENT column. `install_default`'s flow branch
        must not learn about it."""
        from agents.defaults import DEFAULT_FLOWS, install_default

        row, created = install_default("flow", DEFAULT_FLOWS[0].slug,
                                       Principal("open", "box"))
        assert created is True
        assert not hasattr(row, "box_wide")


class TestTheMigrationPreservesTodaysBehaviour:
    def test_the_data_migration_function_copies_resident_onto_box_wide(self):
        """Called directly against the live models rather than through a
        migration executor, so it stays a unit test: the function's
        contract is 'every row's audience becomes what its origin used
        to imply', which is what makes landing day byte-identical."""
        from agents.migrations import _0012_helpers

        make_agent(slug="was-resident", resident=True, box_wide=False)
        make_agent(slug="was-not", resident=False, box_wide=False)

        class _Apps:
            @staticmethod
            def get_model(app_label, model_name):
                assert (app_label, model_name) == ("agents", "Agent")
                return Agent

        _0012_helpers.copy_resident_to_box_wide(_Apps, None)
        assert Agent.objects.get(slug="was-resident").box_wide is True
        assert Agent.objects.get(slug="was-not").box_wide is False
```

Check `identity/contracts/principals.py` for the real constructor before using `Principal("user", "1")`; if the helper `agents/tests/_helpers.py::make_principal` fits better, use it.

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest -q agents/tests/test_agent_authoring.py`
Expected: FAIL — `TypeError: 'box_wide' is an invalid keyword argument for Agent`.

- [ ] **Step 3: Add the field**

In `agents/models.py`, immediately after the `resident` field on `Agent`:

```python
    # THE AUDIENCE, distinct from `resident` the ORIGIN MARKER above.
    # True means "everybody on this box may use this row"; False means
    # "only the people its ownership and its labels reach".
    #
    # A SEPARATE COLUMN RATHER THAN AN OVERLOAD OF `resident` (spec
    # decision 6, owner ruling flag 2). `resident` has a SECOND READER
    # -- `agents.visibility.resident_agent_tool_keys`, which warns the
    # tool-label page that labelling a tool a SHIPPED agent declares
    # makes the shell path silently weaker -- and overloading it would
    # make that warning fire for rows that were never shipped defaults,
    # while making the agent form's own audience control lie about where
    # a row came from.
    #
    # `visible_agents` AND-s the entitlement label clause onto its
    # ownership OR, so `box_wide=True` is not a bypass: a box-wide row
    # narrowed to an entitlement reaches everybody on this box WHO HOLDS
    # IT, which is the useful fourth row of §4.3.1's truth table.
    box_wide = models.BooleanField(default=False)
```

- [ ] **Step 4: Write the migration and its helper**

Create `agents/migrations/_0012_helpers.py` (the shape `identity/migrations/_0002_helpers.py` already sets — a testable function outside the `Migration` class):

```python
"""The data half of `0012_agent_box_wide`, importable by a test.

`identity/migrations/_0002_helpers.py` is the precedent: a `RunPython`
body that a unit test can call directly, so the thing that decides
whether landing day is byte-identical is proved by a test rather than by
reading a migration.
"""
from __future__ import annotations


def copy_resident_to_box_wide(apps, _schema_editor) -> None:
    """Every existing row's AUDIENCE becomes what its ORIGIN used to
    imply.

    Before this migration, `visible_agents`' third leg was
    `Q(resident=True)` -- the shipped defaults, visible to everybody as
    "the platform's own offer". After it, that leg reads `box_wide`. So
    every row that reached everybody through `resident` must reach
    exactly the same people through `box_wide`, or the migration would
    change who can see what, silently, on deploy. It does not.
    """
    Agent = apps.get_model("agents", "Agent")
    Agent.objects.filter(resident=True).update(box_wide=True)


def clear_box_wide(apps, _schema_editor) -> None:
    """Reverse. The column is dropped by the schema operation beside
    this one; clearing first keeps a partial rollback honest."""
    Agent = apps.get_model("agents", "Agent")
    Agent.objects.update(box_wide=False)
```

Create `agents/migrations/0012_agent_box_wide.py`:

```python
# The audience column (chat cluster, feature B). Additive, and
# behaviour-preserving on landing day: the data migration below sets
# `box_wide = resident` for every existing row, and `visible_agents`
# swaps one leg for a column holding the same truth.
from django.db import migrations, models

from agents.migrations._0012_helpers import clear_box_wide, copy_resident_to_box_wide


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0011_turn_author"),
    ]

    operations = [
        migrations.AddField(
            model_name="agent",
            name="box_wide",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(copy_resident_to_box_wide, clear_box_wide),
    ]
```

- [ ] **Step 5: Swap the visibility leg**

In `agents/visibility.py::visible_agents`, replace `Q(resident=True)` with `Q(box_wide=True)` and rewrite the opening paragraph of the docstring:

```python
    `box_wide=True` rows are visible to EVERYBODY on this box -- the
    shipped defaults start that way (`agents.defaults.install_default`
    stamps it, because a shipped default is the PLATFORM's offer, not
    the installing operator's private row), and an administrator may set
    or clear it on any agent from the agent form.

    IT USED TO BE `resident=True` (chat cluster, feature B). `resident`
    keeps its documented meaning -- an ORIGIN MARKER, `Agent`'s own
    ruling 3 -- and its second reader, `resident_agent_tool_keys`'s
    shell-path warning. Audience is now its own column, so making a
    shipped default private is not a claim that it was never shipped.
    Migration `0012_agent_box_wide` set `box_wide = resident` for every
    existing row, so the day it landed nothing changed for anybody.
```

Make the identical substitution in `installed_agent_slugs`, and add to its docstring:

```python
    Same visibility rule as `visible_agents`, minus the `enabled`
    filter, and it moved to `box_wide` with it -- the two must agree
    about who can see a row or the "Add the default X" offers would be
    computed against a different set from the one the index lists.
```

Leave `resident_agent_tool_keys` alone: it asks about origin, and origin is what it still means.

- [ ] **Step 6: Stamp the catalogue**

In `agents/defaults.py::install_default`, in the **agent** branch's `_fields()` only:

```python
        def _fields() -> dict:
            return dict(
                name=spec.name, description=spec.description,
                system_prompt=spec.system_prompt, llm_role=spec.llm_role,
                tool_keys=list(spec.tool_keys), max_steps=spec.max_steps,
                # THE SHIPPED CATALOGUE IS THE PLATFORM'S OFFER TO
                # EVERYBODY, so an installed default is box-wide --
                # UNCONDITIONALLY, never `is_admin(principal)` (spec
                # decision 23). `manage.py install_defaults` runs as
                # `OPEN_PRINCIPAL`, which it imports and never
                # constructs, AST-guarded; `identity.access.is_admin`
                # answers True for it on an OPEN box and False on an
                # accounts-on one, because `_user_row` returns None for
                # any `principal.kind != "user"`. Stamping by the
                # installer's authority would therefore leave the
                # canonical shell install working on an open box and
                # SILENTLY BREAK IT on an accounts-on one -- the posture
                # where a shipped default most needs to reach everybody.
                #
                # IN `_fields()` RATHER THAN BESIDE `resident=True`
                # BELOW, so the `--reset` path re-stamps it with every
                # other field (that function's own "a reset is a fresh
                # adoption" rule), and so the FLOW branch never learns
                # about a column `Flow` does not have.
                box_wide=True,
            )
```

- [ ] **Step 7: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -q agents/tests/test_agent_authoring.py`
Expected: PASS.
Run: `.venv/bin/pytest -q agents tools/rag/tests --no-header`
Expected: PASS — in particular `agents/tests/test_defaults.py`, `agents/tests/test_resident.py` and `agents/chat/tests/test_visibility.py`.
Run: `.venv/bin/python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`.

- [ ] **Step 8: Document the column**

In `agents/README.md`, beside the `Agent` field notes:

```markdown
### `box_wide` and `resident` are two different facts

`resident` records **where a row came from** — it started life as a shipped
default. It is an origin marker, not a lock (`Agent`'s own ruling 3), and it has
a second reader: the tool-label page's shell-path warning
(`agents/visibility.py::resident_agent_tool_keys`) names the shipped agents that
declare a tool an operator is about to label.

`box_wide` records **who may use it** — everybody on this box, or only the people
its ownership and its entitlement labels reach. `visible_agents` AND-s the label
clause onto its ownership OR, so a box-wide row narrowed to an entitlement
reaches everybody on this box *who holds it*; the two controls compose rather
than excluding each other.

Migration `0012_agent_box_wide` set `box_wide = resident` for every existing row,
so the swap changed nobody's access on the day it landed. `install_defaults`
stamps `box_wide=True` unconditionally, including on `--reset`: a shipped default
is the platform's offer, not the installing operator's private row.
```

- [ ] **Step 9: Run the four runs and both posture sweeps**

```
export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools
FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
```
Expected: six green runs.

- [ ] **Step 10: Commit**

```
git add agents/models.py agents/migrations/0012_agent_box_wide.py agents/migrations/_0012_helpers.py agents/visibility.py agents/defaults.py agents/tests/test_agent_authoring.py agents/README.md
git commit -m "feat(agents): box_wide — audience as its own column, resident stays the origin marker"
```

---

### Task 6: The agent writers

**Files:**
- Modify: `agents/limits.py` (`MAX_STEPS_CEILING`)
- Modify: `identity/contracts/actions.py` (`AGENT_CREATED`, `AGENT_EDITED`)
- Modify: `agents/visibility.py` (`may_manage_agent`, `editable_agents`, `box_wide_agents_owned_by`, `create_agent`, `update_agent`, `_derive_agent_slug`, the refusal sentences)
- Test: `agents/tests/test_agent_authoring.py`
- Modify: `agents/README.md`, `identity/README.md` (only if it enumerates audit actions — check first)

**Interfaces:**
- Consumes: `identity.access.is_admin / sees_all_content / owned_rows_q / may_read_owned_row / owner_fields`; `identity.audit.record`; `identity.contracts.actions`; `agents.limits.MAX_STEPS_DEFAULT`; `agents.defaults.catalogue`; `models.contracts.roles.CHAT_CONVERSE_ROLE`.
- Produces, all in `agents/visibility.py`:
  - `may_manage_agent(principal, agent, *, settings_row=None) -> bool`
  - `editable_agents(principal, *, settings_row=None)` — a queryset
  - `box_wide_agents_owned_by(principal, *, settings_row=None)` — a queryset
  - `create_agent(principal, fields: dict, *, settings_row=None) -> tuple[object | None, dict]`
  - `update_agent(principal, agent, fields: dict, *, settings_row=None) -> dict`
  - The refusal sentences `AGENT_NAME_REQUIRED`, `AGENT_NAME_TOO_LONG`, `AGENT_MAX_STEPS_OUT_OF_RANGE`
  - `agents.limits.MAX_STEPS_CEILING = 32`
- Consumed by: Tasks 7–11 (the form builder, both list pages, both edit POST paths).

`fields` is a plain dict with the keys `name`, `description`, `system_prompt`, `max_steps`, `enabled`, and optionally `llm_role` and `box_wide` (administrator-only, re-checked here and not merely omitted by the form). The return dict is `{}` on success or `{field_name: sentence}` on refusal.

- [ ] **Step 1: Write the failing test**

Append to `agents/tests/test_agent_authoring.py`:

```python
class TestMayManageAgent:
    def test_the_rows_own_owner_may(self):
        from agents.visibility import may_manage_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="mine", **owner_fields(user_principal(owner)))
            assert may_manage_agent(user_principal(owner), agent) is True

    def test_a_stranger_may_not(self):
        from agents.visibility import may_manage_agent

        owner, stranger = make_user(), make_user(username="other")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="theirs", **owner_fields(user_principal(owner)))
            assert may_manage_agent(user_principal(stranger), agent) is False

    def test_an_administrator_may_edit_any_agent(self):
        from agents.visibility import may_manage_agent

        owner, admin = make_user(), make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="somebodys", **owner_fields(user_principal(owner)))
            assert may_manage_agent(user_principal(admin), agent) is True

    def test_a_box_wide_row_refuses_even_its_own_non_admin_owner(self):
        """Editing a row EVERYBODY on the box can use is an
        administrator's act, whoever originally created it -- and
        `box_wide` short-circuits AHEAD of the ownership branch, which is
        what makes that true."""
        from agents.visibility import may_manage_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="everyones-row", box_wide=True,
                               **owner_fields(user_principal(owner)))
            assert may_manage_agent(user_principal(owner), agent) is False

    def test_on_an_open_box_whoever_is_at_the_keyboard_edits_everything(self):
        """Not a fallback: `is_admin` answers True for everybody on a box
        with no accounts, and that is the true statement about a
        household box."""
        from agents.visibility import may_manage_agent
        from identity.contracts.principals import OPEN_PRINCIPAL

        agent = make_agent(slug="open-box-row", box_wide=True)
        assert may_manage_agent(OPEN_PRINCIPAL, agent) is True


class TestEditableAgents:
    def test_it_lists_the_principals_own_and_excludes_box_wide(self):
        from agents.visibility import editable_agents

        owner, other = make_user(), make_user(username="other")
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="a-mine", **owner_fields(user_principal(owner)))
            make_agent(slug="b-theirs", **owner_fields(user_principal(other)))
            make_agent(slug="c-everyones", box_wide=True,
                       **owner_fields(user_principal(owner)))
            slugs = set(editable_agents(user_principal(owner))
                        .values_list("slug", flat=True))
        assert slugs == {"a-mine"}

    def test_on_an_open_box_the_list_and_the_predicate_agree(self):
        """Spec review M6. `owned_rows_q` has NO open-posture widening,
        so without the `sees_all_content` short-circuit a row stamped
        with a `user` principal is ABSENT from the list while
        `may_manage_agent` answers True for it -- the list and the
        predicate disagreeing about the same row."""
        from agents.visibility import editable_agents, may_manage_agent
        from identity.contracts.principals import OPEN_PRINCIPAL

        stamped = make_agent(slug="stamped-user", owner_kind="user", owner_key="7")
        assert "stamped-user" in set(
            editable_agents(OPEN_PRINCIPAL).values_list("slug", flat=True))
        assert may_manage_agent(OPEN_PRINCIPAL, stamped) is True

    def test_it_costs_zero_permission_queries_on_an_open_box(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from agents.visibility import editable_agents
        from identity.contracts.principals import OPEN_PRINCIPAL

        make_agent(slug="open-cost")
        with CaptureQueriesContext(connection) as captured:
            list(editable_agents(OPEN_PRINCIPAL))
        grant_reads = [q for q in captured.captured_queries
                       if "entitlementgrant" in q["sql"].lower()]
        assert grant_reads == []

    def test_an_administrator_with_the_content_setting_off_sees_their_own_rows_only(self):
        """Deliberate, and pinned so it is not read as a bug later:
        `/chat/agents/` is "the agents I work on"; `/settings/agents/` is
        the box-wide view, one click away."""
        from agents.visibility import editable_agents

        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="admins-own", **owner_fields(user_principal(admin)))
            make_agent(slug="members-own", **owner_fields(user_principal(member)))
            slugs = set(editable_agents(user_principal(admin))
                        .values_list("slug", flat=True))
        assert slugs == {"admins-own"}


class TestCreateAgent:
    def _fields(self, **overrides):
        base = dict(name="My helper", description="", system_prompt="be helpful",
                    max_steps=4, enabled=True)
        base.update(overrides)
        return base

    def test_it_stamps_the_creator_as_the_owner_and_derives_a_slug(self):
        from agents.visibility import create_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            row, errors = create_agent(user_principal(owner), self._fields())
        assert errors == {}
        assert row.slug == "my-helper"
        assert row.owner_kind == "user"
        assert row.box_wide is False
        assert row.resident is False
        assert row.tool_keys == []

    def test_a_blank_name_is_refused_and_writes_nothing(self):
        from agents.models import Agent
        from agents.visibility import AGENT_NAME_REQUIRED, create_agent

        before = Agent.objects.count()
        row, errors = create_agent(make_principal(), self._fields(name="   "))
        assert row is None
        assert errors == {"name": AGENT_NAME_REQUIRED}
        assert Agent.objects.count() == before

    def test_a_colliding_slug_is_uniquified_case_insensitively(self):
        from agents.visibility import create_agent

        make_agent(slug="My-Helper")
        row, errors = create_agent(make_principal(), self._fields())
        assert errors == {}
        assert row.slug == "my-helper-2"

    def test_a_slug_colliding_with_the_shipped_catalogue_is_uniquified_too(self):
        """A row whose slug collides with a catalogue entry would be
        silently overwritten by `install_defaults --reset <slug>`."""
        from agents.defaults import DEFAULT_AGENTS
        from agents.visibility import create_agent

        shipped = DEFAULT_AGENTS[0].slug
        row, errors = create_agent(make_principal(),
                                   self._fields(name=shipped.replace("-", " ")))
        assert errors == {}
        assert row.slug != shipped

    def test_a_punctuation_only_name_still_gets_a_usable_slug(self):
        from agents.visibility import create_agent

        row, errors = create_agent(make_principal(), self._fields(name="!!! ???"))
        assert errors == {}
        assert row.slug.startswith("agent-")

    def test_max_steps_is_refused_outside_the_declared_range(self):
        from agents.limits import MAX_STEPS_CEILING
        from agents.visibility import AGENT_MAX_STEPS_OUT_OF_RANGE, create_agent

        low, low_errors = create_agent(make_principal(), self._fields(max_steps=0))
        high, high_errors = create_agent(
            make_principal(), self._fields(max_steps=MAX_STEPS_CEILING + 1))
        assert low is None and high is None
        assert low_errors == {"max_steps": AGENT_MAX_STEPS_OUT_OF_RANGE}
        assert high_errors == {"max_steps": AGENT_MAX_STEPS_OUT_OF_RANGE}

    def test_it_writes_one_audit_row_naming_the_action(self):
        from identity.contracts import actions
        from identity.models import AuditEvent
        from agents.visibility import create_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            create_agent(user_principal(owner), self._fields())
            assert AuditEvent.objects.filter(action=actions.AGENT_CREATED).count() == 1


class TestUpdateAgent:
    def test_it_writes_the_editable_fields(self):
        from agents.visibility import update_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="editable", **owner_fields(user_principal(owner)))
            errors = update_agent(user_principal(owner), agent, dict(
                name="Renamed", description="d", system_prompt="new prompt",
                max_steps=3, enabled=False))
        agent.refresh_from_db()
        assert errors == {}
        assert (agent.name, agent.system_prompt, agent.max_steps, agent.enabled) == (
            "Renamed", "new prompt", 3, False)

    def test_the_slug_cannot_be_changed_and_the_attempt_is_not_a_500(self):
        """`Agent.save()` refuses a slug change because a slug is a KEY
        -- `flow.run` resolves one, an `agent.<slug>` grant names one,
        `--reset` matches on one. The form never offers the field, and
        this writer ignores it rather than reaching that refusal."""
        from agents.visibility import update_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="keep-me", **owner_fields(user_principal(owner)))
            errors = update_agent(user_principal(owner), agent, dict(
                name="n", description="", system_prompt="", max_steps=2, enabled=True,
                slug="something-else"))
        agent.refresh_from_db()
        assert errors == {}
        assert agent.slug == "keep-me"

    def test_a_non_admin_cannot_set_the_role_even_by_forging_the_field(self):
        from agents.visibility import update_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="role-locked", **owner_fields(user_principal(owner)))
            was = agent.llm_role
            update_agent(user_principal(owner), agent, dict(
                name="n", description="", system_prompt="", max_steps=2, enabled=True,
                llm_role="some.other.role"))
        agent.refresh_from_db()
        assert agent.llm_role == was

    def test_an_administrator_can(self):
        from agents.visibility import update_agent
        from models.contracts.roles import CHAT_CONVERSE_ROLE

        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="role-open", llm_role=CHAT_CONVERSE_ROLE,
                               **owner_fields(user_principal(admin)))
            update_agent(user_principal(admin), agent, dict(
                name="n", description="", system_prompt="", max_steps=2, enabled=True,
                llm_role=CHAT_CONVERSE_ROLE))
        agent.refresh_from_db()
        assert agent.llm_role == CHAT_CONVERSE_ROLE

    def test_a_non_admin_cannot_set_box_wide_even_by_forging_the_field(self):
        from agents.visibility import update_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="reach-locked", **owner_fields(user_principal(owner)))
            update_agent(user_principal(owner), agent, dict(
                name="n", description="", system_prompt="", max_steps=2, enabled=True,
                box_wide=True))
        agent.refresh_from_db()
        assert agent.box_wide is False

    def test_the_reach_write_touches_no_labels_at_all(self):
        """Control 1 and control 2 are independent, each gated by its own
        authority. Reach cannot widen anything past a label that is
        standing, because it writes no label."""
        from agents.labels import agent_label_ids, set_agent_labels
        from agents.visibility import update_agent

        admin = make_admin()
        entitlement = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="reach-and-labels",
                               **owner_fields(user_principal(admin)))
            set_agent_labels(user_principal(admin), agent, {entitlement.pk})
            update_agent(user_principal(admin), agent, dict(
                name="n", description="", system_prompt="", max_steps=2, enabled=True,
                box_wide=True))
        agent.refresh_from_db()
        assert agent.box_wide is True
        assert agent_label_ids(agent) == frozenset({entitlement.pk})

    def test_a_stranger_writes_nothing(self):
        from agents.visibility import update_agent

        owner, stranger = make_user(), make_user(username="other")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="not-yours", **owner_fields(user_principal(owner)))
            errors = update_agent(user_principal(stranger), agent, dict(
                name="Hijacked", description="", system_prompt="", max_steps=2,
                enabled=True))
        agent.refresh_from_db()
        assert errors != {}
        assert agent.name != "Hijacked"

    def test_the_audit_row_names_which_fields_changed_and_not_their_contents(self):
        """A system prompt is the operator's text, and an audit trail is
        not the place to copy it."""
        from identity.contracts import actions
        from identity.models import AuditEvent
        from agents.visibility import update_agent

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="audited", system_prompt="old secret",
                               **owner_fields(user_principal(owner)))
            update_agent(user_principal(owner), agent, dict(
                name=agent.name, description=agent.description,
                system_prompt="new secret", max_steps=agent.max_steps, enabled=True))
            row = AuditEvent.objects.filter(action=actions.AGENT_EDITED).get()
        serialized = str(row.__dict__)
        assert "system_prompt" in serialized
        assert "new secret" not in serialized
        assert "old secret" not in serialized
```

Add `make_principal` and `make_entitlement` to this module's imports from `agents/tests/_helpers.py` and `identity/testing.py` respectively; check `identity/models.py` for the real audit-row model name before asserting on `AuditEvent`, and use whatever `identity/audit.py::record` actually writes.

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest -q agents/tests/test_agent_authoring.py -k "MayManage or Editable or CreateAgent or UpdateAgent"`
Expected: FAIL — `ImportError: cannot import name 'may_manage_agent' from 'agents.visibility'`.

- [ ] **Step 3: Declare the ceiling and the two audit actions**

In `agents/limits.py`, immediately after `MAX_STEPS_DEFAULT`:

```python
# THE CEILING THE AGENT FORM REFUSES ABOVE. `Agent.max_steps` is a bare
# `PositiveIntegerField` and no constant named a ceiling before the agent
# form existed -- so the form may not invent one inline (spec review n2).
# Here, beside the default, for this module's own reason: both numbers
# bound the same field and a second home for one of them is a second
# place to change.
MAX_STEPS_CEILING = 32
```

In `identity/contracts/actions.py`, beside the four `agent.*`/`flow.*` labelling actions:

```python
# The agent form (chat cluster, feature B): a row created or edited from
# `/chat/agents/` or `/settings/agents/`. `agent.edited` names WHICH
# FIELDS changed and never their contents -- a system prompt is the
# operator's own text.
AGENT_CREATED = "agent.created"
AGENT_EDITED = "agent.edited"
```

and add `AGENT_CREATED, AGENT_EDITED,` to `AUDIT_ACTIONS`, on the line that already carries `AGENT_LABELLED, AGENT_UNLABELLED, ...`.

- [ ] **Step 4: Write the visibility functions**

In `agents/visibility.py`, add `is_admin` to the existing `from identity.access import (...)` line, and add after `labellable_flow`:

```python
AGENT_NAME_REQUIRED = "Give this agent a name."
AGENT_NAME_TOO_LONG = "That name is too long — the limit is 255 characters."
AGENT_MAX_STEPS_OUT_OF_RANGE = (
    f"Steps per turn must be between 1 and {MAX_STEPS_CEILING}."
)
AGENT_NOT_YOURS = "That agent is not yours to change."
_SLUG_MAX = 64


def may_manage_agent(principal, agent, *, settings_row=None) -> bool:
    """Whether `principal` may edit `agent`.

    ADMINISTRATORS MAY EDIT ANY AGENT -- the same call `labellable_agents`
    records for the sibling page, and what "the main chat should be owned
    by the admin" requires.

    A USER-CREATED AGENT NEEDS NO ADMINISTRATOR: its owner edits it.

    A BOX-WIDE AGENT IS ADMIN-ONLY, whoever originally created it, and
    the `box_wide` test SHORT-CIRCUITS AHEAD OF THE OWNERSHIP BRANCH,
    which is what makes that true for a member who installed a shipped
    default themselves (`chat-default-install` is class A; that row's
    owner is the member). `/chat/agents/` renders those rows in a
    read-only section with a sentence, so the refusal is never silent.

    AN OPEN BOX DEGRADES CORRECTLY WITH NO BRANCH: `is_admin` answers
    True for everybody on a box with no accounts, so whoever is at the
    keyboard edits everything. That is the true statement about a
    household box, not a fallback.

    `may_read_owned_row` is identity's own row-predicate mirror of
    `owned_rows_q`; nothing here restates the shape of the two owner
    columns.
    """
    if is_admin(principal, settings_row=settings_row):
        return True
    if agent.box_wide:
        return False
    return may_read_owned_row(principal, agent)


def editable_agents(principal, *, settings_row=None):
    """The agents this principal may edit, for `/chat/agents/`.

    THE COLUMN'S STANDARD SHAPE, open branch first -- not a bare
    `owned_rows_q` filter (spec review M6). `identity.access.
    owned_rows_q` has NO open-posture widening (it is
    `Q(owner_kind=..., owner_key=...) | Q(owner_kind="service")`) and it
    calls `is_admin`, which reads the `IdentitySettings` singleton.
    Without the short-circuit three things go wrong at once on an open
    box: a row stamped with a `user` principal (installed during an
    accounts-on period, or after an owner reassignment) is ABSENT from
    this list while `may_manage_agent` answers True for it, so the list
    and the predicate disagree; the "zero permission queries on an open
    box" rule is false; and "this is every non-box-wide agent" is false.

    ON AN ACCOUNTS-ON BOX AN ADMINISTRATOR SEES THEIR OWN ROWS ONLY,
    because `sees_all_content` is `is_admin AND admin_sees_content` and
    the content setting is usually off. That is deliberate:
    `/chat/agents/` is "the agents I work on"; `/settings/agents/` is
    the box-wide view, one click away.

    BOX-WIDE ROWS ARE EXCLUDED because `may_manage_agent` refuses them
    for a non-admin -- a list offering an edit the editor will 404 is
    worse than no list. `box_wide_agents_owned_by` below is how the page
    still says they exist.
    """
    qs = Agent.objects.exclude(box_wide=True).order_by("name")
    if sees_all_content(principal, settings_row=settings_row):
        return qs
    return qs.filter(owned_rows_q(principal, settings_row=settings_row))


def box_wide_agents_owned_by(principal, *, settings_row=None):
    """The box-wide rows THIS principal owns -- `/chat/agents/`'s
    read-only second section.

    `chat-default-install` is class A and `install_default` stamps
    `**owner_fields(principal)`, so a MEMBER installing an ordinary
    catalogue slug owns a row everybody on this box can use and that
    `may_manage_agent` refuses them. Without this section they would own
    a row that is absent from their list and refused by the editor, with
    nothing anywhere explaining why. The audience consequence itself is
    pre-existing -- a member's install reaches exactly the same people
    today as it did before `box_wide` existed -- and the spec's flag 8
    is the owner's ruling to leave the route open and make it legible.

    An administrator takes the same short-circuit every sibling here
    does, for the same reason.
    """
    qs = Agent.objects.filter(box_wide=True).order_by("name")
    if sees_all_content(principal, settings_row=settings_row):
        return qs
    return qs.filter(owned_rows_q(principal, settings_row=settings_row))


def _derive_agent_slug(name: str) -> str:
    """A unique, stable key for a new agent -- the user never types one.

    CHECKED AGAINST THE SHIPPED CATALOGUE AS WELL AS THE TABLE: a row
    whose slug collides with a catalogue entry would be silently
    overwritten by `install_defaults --reset <slug>`. Case-insensitively
    against the table, matching `Agent`'s own `uniq_agent_slug_ci`
    constraint rather than a stricter or looser comparison.

    A name that slugifies to nothing (punctuation only) falls back to
    `agent-<n>`: a key is required and a blank one is not a key.
    """
    from django.utils.text import slugify

    from agents.defaults import catalogue

    base = slugify(name or "")[:_SLUG_MAX]
    fell_back = not base
    if fell_back:
        base = "agent"
    taken = {slug.lower() for slug in Agent.objects.values_list("slug", flat=True)}
    taken |= {spec.slug.lower() for spec in catalogue("agent")}
    # THE GUARD IS THE FALLBACK, NOT THE STRING. An agent somebody
    # really named "Agent" takes `agent` when `agent` is free; only the
    # punctuation-only fallback -- where `base` is a placeholder rather
    # than anybody's chosen name -- always takes a suffix, so two such
    # rows never read as one row named twice.
    if base.lower() not in taken and not fell_back:
        return base
    suffix = 2
    while True:
        candidate = f"{base[:_SLUG_MAX - len(str(suffix)) - 1]}-{suffix}"
        if candidate.lower() not in taken:
            return candidate
        suffix += 1


def _validated_agent_fields(principal, fields, *, settings_row=None, existing=None):
    """`(clean, errors)` -- the shape both writers share.

    ADMIN-ONLY FIELDS ARE RE-CHECKED HERE, not merely omitted by the
    form: a POST that forges `llm_role` or `box_wide` is either a stale
    form or a hand-made request, and both get the same answer -- the
    field is DROPPED, silently, because it was never offered and there
    is nothing honest to say about a control the sender never saw.
    `slug` is dropped the same way: `Agent.save()` refuses a change, and
    a form that offered one would be offering a refusal.
    """
    errors: dict = {}
    name = (fields.get("name") or "").strip()
    if not name:
        errors["name"] = AGENT_NAME_REQUIRED
    elif len(name) > 255:
        errors["name"] = AGENT_NAME_TOO_LONG
    try:
        max_steps = int(fields.get("max_steps", MAX_STEPS_DEFAULT))
    except (TypeError, ValueError):
        max_steps = -1
    if not 1 <= max_steps <= MAX_STEPS_CEILING:
        errors["max_steps"] = AGENT_MAX_STEPS_OUT_OF_RANGE
    if errors:
        return {}, errors
    clean = {
        "name": name,
        "description": fields.get("description") or "",
        "system_prompt": fields.get("system_prompt") or "",
        "max_steps": max_steps,
        "enabled": bool(fields.get("enabled")),
    }
    if is_admin(principal, settings_row=settings_row):
        if fields.get("llm_role"):
            clean["llm_role"] = fields["llm_role"]
        if "box_wide" in fields:
            clean["box_wide"] = bool(fields["box_wide"])
    elif existing is not None:
        clean["llm_role"] = existing.llm_role
    return clean, {}


def create_agent(principal, fields, *, settings_row=None):
    """A new agent owned by `principal` -- `(row, {})`, or `(None, errors)`.

    THE CREATE LIVES HERE for the reason `create_conversation`'s own
    docstring gives: a view that could create a row could create one
    without `owner_fields`, and that row would be invisible to every
    filter identity added.

    `box_wide=False` AND `resident=False` ARE HARD-CODED. A new agent is
    nobody's shipped default and reaches nobody but its owner until its
    owner says otherwise -- which closes the creation route as an
    audience escape hatch by construction rather than by a check.
    `tool_keys=[]`: granting tools is a privilege question, not a form
    field (spec decision 8), and an agent with a prompt and a model is
    already useful.
    """
    from models.contracts.roles import CHAT_CONVERSE_ROLE

    clean, errors = _validated_agent_fields(principal, fields, settings_row=settings_row)
    if errors:
        return None, errors
    clean.setdefault("llm_role", CHAT_CONVERSE_ROLE)
    clean.pop("box_wide", None)
    with transaction.atomic():
        row = Agent.objects.create(
            slug=_derive_agent_slug(clean["name"]),
            tool_keys=[], resident=False, box_wide=False,
            **owner_fields(principal), **clean,
        )
        audit.record(principal, actions.AGENT_CREATED, target_type="agent",
                     target_key=str(row.pk), target_label=row.slug)
    return row, {}


def update_agent(principal, agent, fields, *, settings_row=None) -> dict:
    """Apply `fields` to `agent` if `principal` may. `{}` when written,
    `{field: sentence}` when refused.

    THE AUDIT ROW NAMES WHICH FIELDS CHANGED, NEVER THEIR CONTENTS. A
    system prompt is the operator's text, and an audit trail is not the
    place to copy it.

    A NO-OP EDIT WRITES NO AUDIT ROW, the same reason `agents/labels.py::
    _set_labels` writes the DIFFERENCE: a trail that recorded a change
    for a save that changed nothing is a trail whose interesting lines
    are invisible.
    """
    if not may_manage_agent(principal, agent, settings_row=settings_row):
        return {"name": AGENT_NOT_YOURS}
    clean, errors = _validated_agent_fields(principal, fields,
                                            settings_row=settings_row, existing=agent)
    if errors:
        return errors
    changed = sorted(key for key, value in clean.items()
                     if getattr(agent, key) != value)
    if not changed:
        return {}
    with transaction.atomic():
        for key, value in clean.items():
            setattr(agent, key, value)
        agent.save(update_fields=[*clean, "updated_at"])
        audit.record(principal, actions.AGENT_EDITED, target_type="agent",
                     target_key=str(agent.pk), target_label=agent.slug,
                     was=", ".join(changed))
    return {}
```

Add the imports this needs at the top of `agents/visibility.py`: `from agents.limits import MAX_STEPS_CEILING, MAX_STEPS_DEFAULT`.

`was=` needs no checking, and an earlier draft of this step framed it as if it did. `identity/audit.py::record(actor, action, *, target_type="", target_key="", target_label="", source=SOURCE_WEB, **detail)` takes **any** keyword into the row's JSON `detail` — there is no fixed vocabulary to match, and `agents/labels.py`'s own `entitlement_id=` is the house precedent for a per-row detail. What is load-bearing is the **value**: field *names*, comma-joined, never their contents.

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -q agents/tests/test_agent_authoring.py`
Expected: PASS.
Run: `.venv/bin/pytest -q agents identity foundation/ops`
Expected: PASS — `identity/tests` includes the audit-action registry drift test.

- [ ] **Step 6: Document the writers**

In `agents/README.md`, beside the existing note on `agents/visibility.py`:

```markdown
`may_manage_agent` / `editable_agents` / `box_wide_agents_owned_by` / `create_agent`
/ `update_agent` live here for the same reason `create_conversation`,
`rename_workstream` and `set_workstream_scope` do: this module is the column's one
owned-row read-and-write point, and a view that could create an agent could create
one without `owner_fields`. Audience is **two independent controls**, never one:
reach (`box_wide`, administrators only, re-checked at the write) is a plain field
on this row and touches no labels at all; entitlement labels go through
`agents/chat/service.py::parse_entitlement_diff` → `agents/labels.py::set_agent_labels`
and never through a whole-set write. There is deliberately **no single audience
writer** — one control writing both would either clobber a label its actor may not
touch or refuse an edit it should allow.
```

- [ ] **Step 7: Run the four runs and both posture sweeps**

As in Task 5, Step 9.

- [ ] **Step 8: Commit**

```
git add agents/limits.py agents/visibility.py identity/contracts/actions.py agents/tests/test_agent_authoring.py agents/README.md
git commit -m "feat(agents): the agent writers — may_manage_agent, editable_agents, create_agent, update_agent"
```

---

### Task 7: The form-context builder

**Files:**
- Create: `agents/chat/agentform.py`
- Create: `agents/chat/tests/test_agent_pages.py`
- Modify: `agents/chat/README.md`

**Interfaces:**
- Consumes: `agents.visibility.may_manage_agent / AGENT_NOT_YOURS`; `agents.labels.agent_label_ids`; `agents.chat.service.entitlement_panes / NO_ENTITLEMENTS_COPY`; `identity.access.labelling_entitlements / is_admin`; `agents.visibility.name_for_viewer`; `models.contracts.roles.all_roles`; `agents.limits.MAX_STEPS_CEILING / MAX_STEPS_DEFAULT`.
- Produces: `agent_form_context(principal, *, agent=None, posted=None, errors=None, settings_row=None) -> dict`, with the keys `agent`, `values`, `errors`, `may_choose_role`, `role_options`, `current_role_label`, `may_set_reach`, `box_wide`, `max_steps_ceiling`, `resident_warning`, `entitlement_panel` (`None`, or a dict with `available`, `active`, `available_count`, `active_count`, `total`, `fields`, `anchor`, `panel_anchor`, `no_entitlements` — the exact set `foundation/templates/_transfer_panel.html`'s parameter block requires, counts included), `foreign_label_sentence`.
- Consumed by: Tasks 8, 9 and 10 (both mounts and both POST paths).

**It never touches a manager.** `foundation/ops/tests/test_column_boundaries.py` forbids any module under `agents/chat/` from reaching `Agent`/`Conversation`/`Flow`/`Share`/`Workstream` `.objects`; this module calls the visibility functions and returns plain data, which is exactly what makes a third mount possible without a fourth spelling of the form.

- [ ] **Step 1: Write the failing test**

Create `agents/chat/tests/test_agent_pages.py`:

```python
"""The agent form's context builder, and the two pages that mount it."""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (   # noqa: F401
    grant, make_admin, make_agent, make_entitlement, make_user, posture, sign_in,
    user_principal,
)
from agents.labels import agent_label_ids, set_agent_labels
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = pytest.mark.django_db


class TestTheFormContext:
    def test_a_new_agent_starts_from_the_declared_defaults(self):
        from agents.chat.agentform import agent_form_context
        from agents.limits import MAX_STEPS_CEILING, MAX_STEPS_DEFAULT

        context = agent_form_context(user_principal(make_user()))
        assert context["agent"] is None
        assert context["values"]["max_steps"] == MAX_STEPS_DEFAULT
        assert context["values"]["enabled"] is True
        assert context["max_steps_ceiling"] == MAX_STEPS_CEILING

    def test_an_existing_agent_seeds_every_editable_field(self):
        from agents.chat.agentform import agent_form_context

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="seeded", name="Seeded", system_prompt="p",
                               description="d", max_steps=5,
                               **owner_fields(user_principal(owner)))
            values = agent_form_context(user_principal(owner), agent=agent)["values"]
        assert values == {"name": "Seeded", "description": "d", "system_prompt": "p",
                          "max_steps": 5, "enabled": True}

    def test_a_posted_body_survives_a_refusal_so_nothing_is_retyped(self):
        from agents.chat.agentform import agent_form_context

        context = agent_form_context(
            user_principal(make_user()),
            posted={"name": "", "system_prompt": "kept", "description": "",
                    "max_steps": "4", "enabled": "on"},
            errors={"name": "Give this agent a name."})
        assert context["values"]["system_prompt"] == "kept"
        assert context["errors"]["name"]

    def test_the_role_select_is_built_for_an_administrator_only(self):
        """RENDER-VS-GATE: a non-admin's context never BUILDS the
        options -- which model backs an agent is box policy, the same
        call the Chat and Job execution settings pages already record."""
        from agents.chat.agentform import agent_form_context

        member, admin = make_user(), make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="roles", **owner_fields(user_principal(member)))
            member_context = agent_form_context(user_principal(member), agent=agent)
            admin_context = agent_form_context(user_principal(admin), agent=agent)
        assert member_context["may_choose_role"] is False
        assert member_context["role_options"] == ()
        assert member_context["current_role_label"]
        assert admin_context["may_choose_role"] is True
        assert admin_context["role_options"]

    def test_the_role_options_are_chat_capable_roles_only(self):
        from agents.chat.agentform import agent_form_context
        from models.contracts.roles import all_roles

        with posture(POSTURE_ENTERPRISE):
            context = agent_form_context(user_principal(make_admin()))
        offered = {key for key, _label in context["role_options"]}
        assert offered == {r.key for r in all_roles() if r.capability == "chat"}

    def test_the_reach_control_is_built_for_an_administrator_only(self):
        from agents.chat.agentform import agent_form_context

        member, admin = make_user(), make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="reach", **owner_fields(user_principal(member)))
            assert agent_form_context(
                user_principal(member), agent=agent)["may_set_reach"] is False
            assert agent_form_context(
                user_principal(admin), agent=agent)["may_set_reach"] is True

    def test_on_an_open_box_the_entitlement_panel_does_not_render_at_all(self):
        """`labelling_entitlements` returns `()` with accounts off --
        there is nothing to label with and nothing to show."""
        from agents.chat.agentform import agent_form_context
        from identity.contracts.principals import OPEN_PRINCIPAL

        agent = make_agent(slug="open-panel")
        assert agent_form_context(OPEN_PRINCIPAL, agent=agent)["entitlement_panel"] is None

    def test_a_new_agent_has_no_entitlement_panel_either(self):
        """There is no row to label yet; the panel appears on the edit
        page, once the agent exists."""
        from agents.chat.agentform import agent_form_context

        with posture(POSTURE_ENTERPRISE):
            context = agent_form_context(user_principal(make_admin()))
        assert context["entitlement_panel"] is None

    def test_the_panel_is_the_shared_two_pane_shape_the_access_pages_render(self):
        from agents.chat.agentform import agent_form_context

        admin = make_admin()
        held = make_entitlement(name="Legal")
        other = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="panelled", **owner_fields(user_principal(admin)))
            set_agent_labels(user_principal(admin), agent, {held.pk})
            panel = agent_form_context(user_principal(admin),
                                       agent=agent)["entitlement_panel"]
        assert [row["id"] for row in panel["active"]] == [held.pk]
        assert [row["id"] for row in panel["available"]] == [other.pk]


class TestTheNonDisclosureGate:
    """Spec review R3. `entitlement_panes` builds BOTH panes from
    `choices`, so a member editing their own agent that an administrator
    labelled sees a panel with no trace of that label. Naming it would
    turn `identity/tests/test_route_matrix.py::
    test_no_route_other_than_the_dormant_share_page_names_an_entitlement
    _to_a_non_holder` red, correctly. So the form says HOW MANY, never
    WHICH."""

    def _member_owned_agent_labelled_by_an_admin(self, *, entitlement_name="Legal"):
        member, admin = make_user(), make_admin()
        entitlement = make_entitlement(name=entitlement_name)
        agent = make_agent(slug="labelled-by-admin",
                           **owner_fields(user_principal(member)))
        set_agent_labels(user_principal(admin), agent, {entitlement.pk})
        return member, admin, agent, entitlement

    def test_a_member_is_told_how_many_restrictions_they_cannot_change(self):
        from agents.chat.agentform import agent_form_context

        with posture(POSTURE_ENTERPRISE):
            member, _admin, agent, entitlement = (
                self._member_owned_agent_labelled_by_an_admin())
            sentence = agent_form_context(user_principal(member),
                                          agent=agent)["foreign_label_sentence"]
        assert "1 restriction" in sentence
        assert entitlement.name not in sentence

    def test_a_member_who_HOLDS_one_of_them_sees_it_named(self):
        """No new information: they hold it, they know its name, and the
        gate's own subject is an entitlement you NEITHER own NOR hold."""
        from agents.chat.agentform import agent_form_context

        with posture(POSTURE_ENTERPRISE):
            member, _admin, agent, entitlement = (
                self._member_owned_agent_labelled_by_an_admin())
            grant(entitlement, user=member)
            sentence = agent_form_context(user_principal(member),
                                          agent=agent)["foreign_label_sentence"]
        assert entitlement.name in sentence

    def test_an_administrator_sees_no_such_sentence_at_all(self):
        """`labelling_entitlements` offers them everything, so
        `missing_ids` is empty for them."""
        from agents.chat.agentform import agent_form_context

        with posture(POSTURE_ENTERPRISE):
            _member, admin, agent, _entitlement = (
                self._member_owned_agent_labelled_by_an_admin())
            assert agent_form_context(user_principal(admin),
                                      agent=agent)["foreign_label_sentence"] == ""

    def test_disclose_all_is_never_used_on_this_surface(self):
        """Its one caller is the dormant-share 403, where the reader
        holds a live `Share` row and owner decision 8 asks for the names
        in so many words. Neither condition holds on an agent form."""
        import inspect

        from agents.chat import agentform

        assert "disclose_all" not in inspect.getsource(agentform)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest -q agents/chat/tests/test_agent_pages.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.chat.agentform'`.

- [ ] **Step 3: Write the builder**

Create `agents/chat/agentform.py`:

```python
"""The agent form, as plain data.

ONE BUILDER, TWO MOUNTS, ZERO MANAGERS. `/chat/agents/` and
`/settings/agents/` differ only in which rows they list and who may open
them; nothing about EDITING an agent is written twice, which is the
whole of the owner's "not an entangled mess". A third mount -- a
workstream panel, a future MCP edge -- includes the same fragment and
posts to the same route because this function returns data rather than
markup.

IT NEVER TOUCHES A MANAGER. `foundation/ops/tests/
test_column_boundaries.py` forbids any module under `agents/chat/` from
reaching `Agent`/`Conversation`/`Flow`/`Share`/`Workstream` `.objects`;
everything here comes from `agents.visibility`, `agents.labels`,
`identity.access` and `models.contracts.roles`.

THE LABEL EDITOR IS NOT A NEW CONTROL. It is the two-pane transfer panel
`/chat/access/` and `/chat/tools/` already render
(`foundation/templates/_transfer_panel.html`, fed by
`agents.chat.service.entitlement_panes`), a third consumer of a shared
fragment rather than a fourth spelling of "this row carries entitlement
ids".
"""
from __future__ import annotations

from agents.chat.service import NO_ENTITLEMENTS_COPY, entitlement_panes
from agents.labels import agent_label_ids
from agents.limits import MAX_STEPS_CEILING, MAX_STEPS_DEFAULT
from agents.visibility import name_for_viewer
from identity.access import is_admin, labelling_entitlements
from models.contracts.roles import all_roles

_CHAT_CAPABILITY = "chat"

RESIDENT_WARNING = (
    "This agent was installed from the shipped catalogue. Running "
    "`install_defaults --reset` on it replaces everything below with the shipped text."
)


def _foreign_label_sentence(named, unnamed_count) -> str:
    """How many restrictions this actor cannot change here, and -- only
    for entitlements they already HOLD -- which.

    THE COUNT IS THE MOST THAT CAN BE SAID. `entitlement_panes` builds
    both panes from what this actor may LABEL WITH, so a label an
    administrator set is invisible in the panel; the member's agent is
    narrowed and the page they are standing on looks unlabelled. The
    omission is not a bug to fix by rendering the name -- the
    entitlement non-disclosure rule is a GATE, and
    `identity/tests/test_route_matrix.py` sweeps these routes for
    exactly that. So: counted for what they do not hold, named for what
    they do (no new information -- they hold it, they know its name).
    """
    if not named and not unnamed_count:
        return ""
    parts = []
    if named:
        parts.append(", ".join(name for _pk, name in named))
    if unnamed_count:
        noun = "restriction" if unnamed_count == 1 else "restrictions"
        parts.append(f"{unnamed_count} {noun}")
    carried = " and ".join(parts)
    return (f"This agent also carries {carried} set by an administrator, "
            "which you cannot change here.")


def agent_form_context(principal, *, agent=None, posted=None, errors=None,
                       settings_row=None) -> dict:
    """Everything `chat/_agent_form.html` needs, as plain data.

    `agent=None` is the CREATE form. `posted` is the raw POST body on a
    refused save, so nothing the operator typed is retyped; `errors` is
    the `{field: sentence}` map `agents.visibility.create_agent`/
    `update_agent` returned.

    RENDER-VS-GATE THROUGHOUT: the role options and the reach control
    are administrator-only DATA, and a non-admin's context never builds
    them. A template `{% if %}` over a value that was computed anyway is
    not the same thing.
    """
    admin = is_admin(principal, settings_row=settings_row)
    posted = posted or {}
    if posted:
        values = {
            "name": posted.get("name", ""),
            "description": posted.get("description", ""),
            "system_prompt": posted.get("system_prompt", ""),
            "max_steps": posted.get("max_steps", MAX_STEPS_DEFAULT),
            "enabled": bool(posted.get("enabled")),
        }
    elif agent is not None:
        values = {
            "name": agent.name, "description": agent.description,
            "system_prompt": agent.system_prompt, "max_steps": agent.max_steps,
            "enabled": agent.enabled,
        }
    else:
        values = {"name": "", "description": "", "system_prompt": "",
                  "max_steps": MAX_STEPS_DEFAULT, "enabled": True}

    roles = {role.key: role.label for role in all_roles()
             if role.capability == _CHAT_CAPABILITY}
    current_role = agent.llm_role if agent is not None else ""

    panel = None
    foreign_sentence = ""
    if agent is not None:
        choices = labelling_entitlements(principal, settings_row=settings_row)
        if choices:
            held = agent_label_ids(agent)
            available, active = entitlement_panes(choices, held)
            # THE KEYS `foundation/templates/_transfer_panel.html`
            # REALLY REQUIRES, named the way its own parameter block
            # names them. The two COUNTS are computed here, in the
            # builder, and not in the template, for the reason the
            # fragment states: they are "computed by the caller's own
            # view where the split is ... so a heading cannot disagree
            # with the list under it". `anchor` is the row's own id and
            # `panel_anchor` the panel's own id nested inside it -- one
            # element, one id, the shape `/chat/access/` already passes
            # as `tp_key` and `tp_anchor`.
            panel = {
                "available": available, "active": active,
                "available_count": len(available), "active_count": len(active),
                "total": len(choices),
                "fields": {"pk": agent.pk, "action": "labels"},
                "anchor": f"agent-{agent.pk}",
                "panel_anchor": f"panel-agent-{agent.pk}",
                "no_entitlements": NO_ENTITLEMENTS_COPY,
            }
            missing = frozenset(held) - {pk for pk, _name in choices}
            foreign_sentence = _foreign_label_sentence(
                *name_for_viewer(missing, principal))

    return {
        "agent": agent,
        "values": values,
        "errors": errors or {},
        "may_choose_role": admin,
        # ADMINISTRATORS ONLY (spec decision 7): which model backs an
        # agent is box policy, the same call `Chat` and `Job execution`
        # already record for being ADMIN rather than per-person. A
        # non-admin owner sees the current role as read-only text.
        "role_options": tuple(sorted(roles.items())) if admin else (),
        "current_role_label": roles.get(current_role, current_role),
        "may_set_reach": admin,
        "box_wide": bool(agent is not None and agent.box_wide),
        "max_steps_ceiling": MAX_STEPS_CEILING,
        # TRUE, AND NOTHING ELSE SAYS IT.
        "resident_warning": (RESIDENT_WARNING
                             if agent is not None and agent.resident else ""),
        "entitlement_panel": panel,
        "foreign_label_sentence": foreign_sentence,
    }
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -q agents/chat/tests/test_agent_pages.py`
Expected: PASS.
Run: `.venv/bin/pytest -q foundation/ops/tests/test_column_boundaries.py foundation/ops/tests/test_import_law.py`
Expected: PASS — the new module reaches no manager and imports nothing upward.

- [ ] **Step 5: Document the two-control audience in the chat README**

In `agents/chat/README.md`:

```markdown
## The agent pages

`/chat/agents/` lists the agents this principal may edit; `/settings/agents/` lists
every agent on the box, for an administrator. **One edit route serves both** —
`agents/chat/views/agents.py`, rendering `chat/_agent_form.html` from
`agents/chat/agentform.py::agent_form_context`. Nothing about editing an agent is
written twice; the two mounts differ only in which rows they list and who may open
them.

### Audience is two controls, not one radio

**Reach** — "the people I give it to" / "everyone on this box" — writes
`Agent.box_wide` and **touches no labels at all**. It is administrators-only, and
the POST re-checks that rather than trusting the render.

**Entitlement labels** — the same two-pane transfer panel `/chat/access/` renders,
posting through the same `parse_entitlement_diff` → `set_agent_labels` path. It is
an **add/remove operation over what is there now**, never a whole submitted set: a
label the actor may not label with is never in `choices`, therefore never in either
pane, therefore never in `submitted`, so it survives both directions untouched. An
administrator's label stands whatever a member does with their own.

The two **compose**. `visible_agents` is `(owned | box_wide | shared) AND
label_permitted_q`, which is a truth table, not an exclusive choice — a box-wide
agent narrowed to a department is the useful fourth row. Because both panes are
built from what the actor may label with, a member cannot see a label an
administrator set; the form says **how many** such restrictions the row carries and
never which, except for ones the reader already holds. That silence is the
entitlement non-disclosure gate, and the route matrix sweeps these routes for it.
```

- [ ] **Step 6: Run the four runs and both posture sweeps**

As in Task 5, Step 9.

- [ ] **Step 7: Commit**

```
git add agents/chat/agentform.py agents/chat/tests/test_agent_pages.py agents/chat/README.md
git commit -m "feat(chat): agentform.py — one form context builder for both mounts"
```

---

### Task 8: `/chat/agents/` — the user-facing list and the create route

**Files:**
- Create: `agents/chat/views/agents.py`
- Create: `agents/chat/templates/chat/_agent_form.html`, `agents/chat/templates/chat/agent_edit.html`, `agents/chat/templates/chat/agents.html`
- Modify: `agents/chat/views/__init__.py`, `agents/chat/urls.py`, `identity/routes.py`
- Modify: `agents/chat/templates/chat/_sidebar.html` (the rail entry), `agents/chat/templates/chat/base.html` (the pages' shared CSS)
- Test: `agents/chat/tests/test_agent_pages.py`, `agents/chat/tests/test_never_500.py`, `identity/tests/test_route_matrix.py`

**Interfaces:**
- Consumes: `agents.chat.agentform.agent_form_context` (Task 7); `agents.visibility.editable_agents / box_wide_agents_owned_by / may_manage_agent / create_agent / update_agent` (Task 6); `agents.labels.agent_entitlement_ids`; `identity.access.labelling_entitlements`; `agents.chat.service.validated_next_url`; `agents.chat.sidebar.sidebar_context`.
- Produces: the views `agent_list`, `agent_new`, `agent_edit`; the route names `chat-agents` (A), `chat-agent-new` (A), `chat-agent-edit` (O). Task 9 adds the second POST path to `agent_edit`; Task 10 mounts the same edit route from the settings list.

- [ ] **Step 1: Write the failing test**

Append to `agents/chat/tests/test_agent_pages.py`:

```python
class TestTheUserFacingList:
    def test_it_lists_only_this_principals_own_agents(self, client):
        mine, theirs = make_user(), make_user(username="other")
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="a-mine", name="Mine", **owner_fields(user_principal(mine)))
            make_agent(slug="b-theirs", name="Theirs",
                       **owner_fields(user_principal(theirs)))
            sign_in(client, mine)
            body = client.get(reverse("chat-agents")).content.decode()
        assert "Mine" in body
        assert "Theirs" not in body

    def test_a_box_wide_row_this_member_owns_is_shown_read_only_with_a_reason(
        self, client
    ):
        """Spec §4.3.2. `chat-default-install` is class A, so a member
        can already own a row everybody on the box can use and that the
        editor refuses them. Without this section they would own a row
        that is absent from their list and refused by the editor, with
        nothing explaining why."""
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="everyones", name="Everyone's helper", box_wide=True,
                       **owner_fields(user_principal(member)))
            sign_in(client, member)
            body = client.get(reverse("chat-agents")).content.decode()
        assert "Everyone's helper" in body
        assert "available to everyone here" in body
        assert "New agent" in body

    def test_the_read_only_section_offers_no_edit_link_for_that_row(self, client):
        """`may_manage_agent` refuses it, so a link into the editor
        would be a link to a 404 -- which is the defect the section
        exists to prevent, shipped in a different shape."""
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="everyones-nolink", name="Everyone's helper",
                               box_wide=True, **owner_fields(user_principal(member)))
            sign_in(client, member)
            body = client.get(reverse("chat-agents")).content.decode()
        assert reverse("chat-agent-edit", args=[agent.pk]) not in body

    def test_the_page_renders_a_bare_restriction_count_and_never_a_name(self, client):
        """The LIST pages carry the bare count only, never
        `name_for_viewer`: a per-row held-entitlement read is the sidebar
        N+1 all over again, and a name here is the non-disclosure gate."""
        member, admin = make_user(), make_admin()
        entitlement = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="restricted", name="Restricted",
                               **owner_fields(user_principal(member)))
            set_agent_labels(user_principal(admin), agent, {entitlement.pk})
            sign_in(client, member)
            body = client.get(reverse("chat-agents")).content.decode()
        assert "Legal" not in body
        assert "1 restriction" in body

    def test_the_list_costs_the_same_at_one_agent_and_at_twenty_five(self, client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="only-one", **owner_fields(user_principal(member)))
            sign_in(client, member)
            client.get(reverse("chat-agents"))             # warm-up, unmeasured
            with CaptureQueriesContext(connection) as one:
                client.get(reverse("chat-agents"))
            for index in range(25):
                make_agent(slug=f"many-{index}", **owner_fields(user_principal(member)))
            with CaptureQueriesContext(connection) as many:
                client.get(reverse("chat-agents"))
        assert len(many) == len(one)

    def test_the_rail_links_to_it(self, client):
        conversation_free_body = client.get(reverse("chat-index")).content.decode()
        assert reverse("chat-agents") in conversation_free_body


class TestCreatingAnAgent:
    def test_a_member_creates_one_and_it_becomes_theirs(self, client):
        from agents.models import Agent

        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.post(reverse("chat-agent-new"), {
                "name": "My helper", "description": "", "system_prompt": "be helpful",
                "max_steps": "4", "enabled": "on"})
            row = Agent.objects.get(slug="my-helper")
        assert response.status_code == 302
        assert row.owner_kind == "user"
        assert row.box_wide is False

    def test_a_refused_create_re_renders_the_form_with_what_was_typed(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.post(reverse("chat-agent-new"), {
                "name": "", "description": "", "system_prompt": "do not lose me",
                "max_steps": "4", "enabled": "on"})
        body = response.content.decode()
        assert response.status_code == 200
        assert "do not lose me" in body
        assert "Give this agent a name." in body

    def test_the_create_form_never_offers_a_slug_field(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            body = client.get(reverse("chat-agent-new")).content.decode()
        assert 'name="slug"' not in body


class TestEditingAnAgent:
    def test_the_owner_edits_their_own(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="editable", **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "fields", "name": "Renamed", "description": "",
                "system_prompt": "changed", "max_steps": "3", "enabled": "on"})
            agent.refresh_from_db()
        assert response.status_code == 302
        assert agent.name == "Renamed"

    def test_a_stranger_gets_a_404_on_both_verbs_and_writes_nothing(self, client):
        owner, stranger = make_user(), make_user(username="other")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="not-yours", name="Original",
                               **owner_fields(user_principal(owner)))
            sign_in(client, stranger)
            get = client.get(reverse("chat-agent-edit", args=[agent.pk]))
            post = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "fields", "name": "Hijacked", "description": "",
                "system_prompt": "", "max_steps": "2", "enabled": "on"})
            agent.refresh_from_db()
        assert get.status_code == 404
        assert post.status_code == 404
        assert agent.name == "Original"

    def test_a_box_wide_row_refuses_its_own_non_admin_owner_with_a_404(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="everyones-edit", box_wide=True,
                               **owner_fields(user_principal(member)))
            sign_in(client, member)
            assert client.get(
                reverse("chat-agent-edit", args=[agent.pk])).status_code == 404

    def test_the_reach_control_is_absent_from_a_members_page_source(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="no-reach", **owner_fields(user_principal(member)))
            sign_in(client, member)
            body = client.get(reverse("chat-agent-edit", args=[agent.pk])).content.decode()
        assert "Everyone on this box" not in body
        assert 'name="box_wide"' not in body

    def test_a_forged_reach_post_from_a_member_is_refused(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="forged-reach", **owner_fields(user_principal(member)))
            sign_in(client, member)
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "fields", "name": "n", "description": "", "system_prompt": "",
                "max_steps": "2", "enabled": "on", "box_wide": "on"})
            agent.refresh_from_db()
        assert agent.box_wide is False

    def test_a_non_admin_sees_the_role_as_text_and_no_select(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="no-role-select",
                               **owner_fields(user_principal(member)))
            sign_in(client, member)
            body = client.get(reverse("chat-agent-edit", args=[agent.pk])).content.decode()
        assert 'name="llm_role"' not in body

    def test_a_resident_row_warns_about_reset(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="shipped-row", resident=True,
                               **owner_fields(user_principal(admin)))
            sign_in(client, admin)
            body = client.get(reverse("chat-agent-edit", args=[agent.pk])).content.decode()
        assert "installed from the shipped catalogue" in body

    def test_a_GET_next_is_echoed_into_a_hidden_field_and_redirected_nowhere(
        self, client
    ):
        """`validated_next_url` reads `request.POST` and nothing else, so
        a `?next=` arriving on a GET is NOT validated by it and a naive
        `request.GET["next"]` redirect would be an open redirect off-box
        (spec review m3)."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="next-echo", **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            url = reverse("chat-agent-edit", args=[agent.pk])
            response = client.get(f"{url}?next=https://elsewhere.example/steal")
        body = response.content.decode()
        assert response.status_code == 200
        assert 'name="next"' in body

    def test_an_off_origin_next_on_the_POST_falls_back_to_the_list(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="next-refused", **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "fields", "name": "n", "description": "", "system_prompt": "",
                "max_steps": "2", "enabled": "on",
                "next": "https://elsewhere.example/steal"})
        assert response.status_code == 302
        assert response["Location"] == reverse("chat-agents")

    def test_a_same_origin_next_on_the_POST_is_honoured(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="next-ok", **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "fields", "name": "n", "description": "", "system_prompt": "",
                "max_steps": "2", "enabled": "on", "next": reverse("chat-index")})
        assert response["Location"] == reverse("chat-index")

    def test_a_malformed_llm_role_still_renders_200(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="odd-role", llm_role="not.a.registered.role",
                               **owner_fields(user_principal(admin)))
            sign_in(client, admin)
            assert client.get(
                reverse("chat-agent-edit", args=[agent.pk])).status_code == 200

    def test_max_steps_is_refused_at_the_declared_bounds(self, client):
        from agents.limits import MAX_STEPS_CEILING

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="steps", **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            for value in ("0", str(MAX_STEPS_CEILING + 1)):
                response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                    "action": "fields", "name": "n", "description": "",
                    "system_prompt": "", "max_steps": value, "enabled": "on"})
                assert response.status_code == 200
                assert str(MAX_STEPS_CEILING) in response.content.decode()


class TestNoEntitlementNameLeaksFromTheseRoutes:
    """Asserted DIRECTLY, in this column's own tests, rather than left to
    `identity/tests/test_route_matrix.py`'s cross-cutting sweep to catch
    later (spec review R3)."""

    def test_none_of_the_three_routes_names_an_entitlement_to_a_non_holder(self, client):
        member, admin = make_user(), make_admin()
        entitlement = make_entitlement(name="Radioactive")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="swept", **owner_fields(user_principal(member)))
            set_agent_labels(user_principal(admin), agent, {entitlement.pk})
            sign_in(client, member)
            bodies = [
                client.get(reverse("chat-agents")).content.decode(),
                client.get(reverse("chat-agent-new")).content.decode(),
                client.get(reverse("chat-agent-edit", args=[agent.pk])).content.decode(),
            ]
        assert not any("Radioactive" in body for body in bodies)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest -q agents/chat/tests/test_agent_pages.py -k "UserFacingList or Creating or Editing or NoEntitlementName"`
Expected: FAIL — `NoReverseMatch: Reverse for 'chat-agents' not found`.

- [ ] **Step 3: Write the views**

Create `agents/chat/views/agents.py`:

```python
"""The agent pages: this principal's own list, creation, and the ONE
edit route both mounts share.

THE EDIT ROUTE IS CLASS O, NOT S -- admitted at the middleware, 404 in
the view for a row `may_manage_agent` refuses, which is the definition
of the class. An S route would refuse a non-admin at the middleware,
which is exactly the person this feature exists for.

THE LISTING GOES THROUGH `agents.visibility`, never `Agent.objects` --
`foundation/ops/tests/test_column_boundaries.py` fails the build
otherwise, and the two readers here (`editable_agents`,
`box_wide_agents_owned_by`) are the ones that know the posture rules.

`?next=` FOLLOWS THE HOUSE PATTERN EXACTLY. `agents.chat.service.
validated_next_url` reads `request.POST` and nothing else, so a `?next=`
arriving on a GET link is NOT validated by it and a naive
`request.GET["next"]` redirect would be an open redirect off this box.
The list link carries `?next=`, the GET ECHOES IT INTO A HIDDEN FIELD
and does nothing else with it, and the POST validates through
`validated_next_url`, falling back to this list.
"""
from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from agents.chat.agentform import agent_form_context
from agents.chat.service import validated_next_url
from agents.chat.sidebar import sidebar_context
from agents.labels import agent_entitlement_ids
from agents.visibility import (
    box_wide_agents_owned_by, create_agent, editable_agents, labellable_agent,
    may_manage_agent, update_agent,
)
from identity.access import labelling_entitlements
from identity.request import principal_for_request, settings_row_for

# TRUE, AND NOTHING ELSE ON THIS PAGE SAYS IT.
BOX_WIDE_SECTION_TITLE = "Agents everyone on this box can use"
BOX_WIDE_SECTION_NOTE = (
    "You installed this from the shipped catalogue, so it is available to everyone "
    "here. An administrator can change it."
)


def _restriction_counts(rows, principal, settings_row):
    """`{agent_id: n}` -- how many labels each row carries that this
    principal cannot manage.

    THE BARE COUNT ONLY, never `name_for_viewer`: naming one here would
    be the non-disclosure gate, and a per-row `held_entitlement_ids`
    read would be the sidebar N+1 all over again. TWO BATCH READS for
    the whole page -- `agent_entitlement_ids()` reads every row's labels
    in one `values_list` (the discipline `/chat/access/` documents) and
    `labelling_entitlements` is one more -- and the per-row number is a
    FOLD over them rather than a query of its own.
    """
    labels = agent_entitlement_ids()
    mine = {pk for pk, _name in labelling_entitlements(principal,
                                                       settings_row=settings_row)}
    return {row.pk: len(labels.get(row.pk, frozenset()) - mine) for row in rows}


@require_http_methods(["GET", "HEAD"])
def agent_list(request):
    """GET `/chat/agents/` -- the agents this principal may edit, plus
    the box-wide rows they own, read-only."""
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    rows = list(editable_agents(principal, settings_row=settings_row))
    box_wide = list(box_wide_agents_owned_by(principal, settings_row=settings_row))
    counts = _restriction_counts(rows + box_wide, principal, settings_row)
    return render(request, "chat/agents.html", {
        "rows": rows,
        "box_wide_rows": box_wide,
        "box_wide_section_title": BOX_WIDE_SECTION_TITLE,
        "box_wide_section_note": BOX_WIDE_SECTION_NOTE,
        "restriction_counts": counts,
        "next_url": request.get_full_path(),
        **sidebar_context(principal, settings_row=settings_row),
    })


@require_http_methods(["GET", "HEAD", "POST"])
def agent_new(request):
    """GET renders the same fragment with an empty agent; POST creates.

    CLASS A -- creation needs an authenticated principal and no row.
    """
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    if request.method != "POST":
        return render(request, "chat/agent_edit.html", {
            **agent_form_context(principal, settings_row=settings_row),
            "form_action": reverse("chat-agent-new"),
            "next_value": request.GET.get("next", ""),
            **sidebar_context(principal, settings_row=settings_row),
        })
    row, errors = create_agent(principal, request.POST.dict(),
                               settings_row=settings_row)
    if errors:
        return render(request, "chat/agent_edit.html", {
            **agent_form_context(principal, posted=request.POST.dict(), errors=errors,
                                 settings_row=settings_row),
            "form_action": reverse("chat-agent-new"),
            "next_value": request.POST.get("next", ""),
            **sidebar_context(principal, settings_row=settings_row),
        })
    messages.info(request, f"Created {row.name}.")
    return redirect(validated_next_url(request) or reverse("chat-agents"))


@require_http_methods(["GET", "HEAD", "POST"])
def agent_edit(request, pk: int):
    """One agent, edited. CLASS O: 404 unless `may_manage_agent`.

    TWO POST PATHS, told apart by an `action` field: `fields` writes the
    row through `update_agent`, and `labels` goes through
    `parse_entitlement_diff` -> `set_agent_labels` (Task 9). They are
    two forms on one page for the reason spec §4.3.1 gives: reach
    answers to the actor's own authority and a label answers to whether
    the actor may label with THAT entitlement, and one control writing
    both would either clobber a label it may not touch or refuse an edit
    it should allow.
    """
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    # `labellable_agent`, THE READER THAT ALREADY EXISTS -- not a second
    # name for `Agent.objects.filter(pk=pk).first()` in the same module.
    # It is a READER, never a gate: `may_manage_agent` below is the gate,
    # and the pair becomes this route's house 404.
    agent = labellable_agent(pk)
    if agent is None or not may_manage_agent(principal, agent,
                                             settings_row=settings_row):
        raise Http404(f"No agent {pk} you may change.")
    if request.method == "POST":
        return _save_fields(request, principal, agent, settings_row)
    return render(request, "chat/agent_edit.html", {
        **agent_form_context(principal, agent=agent, settings_row=settings_row),
        "form_action": reverse("chat-agent-edit", args=[agent.pk]),
        # THE GET ECHOES `?next=` AND DOES NOTHING ELSE WITH IT -- see
        # this module's docstring.
        "next_value": request.GET.get("next", ""),
        **sidebar_context(principal, settings_row=settings_row),
    })


def _save_fields(request, principal, agent, settings_row):
    errors = update_agent(principal, agent, request.POST.dict(),
                          settings_row=settings_row)
    if errors:
        return render(request, "chat/agent_edit.html", {
            **agent_form_context(principal, agent=agent, posted=request.POST.dict(),
                                 errors=errors, settings_row=settings_row),
            "form_action": reverse("chat-agent-edit", args=[agent.pk]),
            "next_value": request.POST.get("next", ""),
            **sidebar_context(principal, settings_row=settings_row),
        })
    messages.info(request, f"Saved {agent.name}.")
    return redirect(validated_next_url(request) or reverse("chat-agents"))
```

**No new pk reader.** `agents/visibility.py::labellable_agent(pk)` already is
`Agent.objects.filter(pk=pk).first()`, in this same module, for this same reason —
a first draft of this task added a byte-identical `visible_agent_for_edit` beside
it, which is two names for one query in one module and exactly what "not an
entangled mess" is about. Reuse it, and **widen its docstring** so the second
caller and its different gate are stated rather than silently inherited: today it
says *"CLASS S already means every caller here is an administrator"*, which is true
of `/chat/access/` and false of `chat-agent-edit` (class O). Replace that sentence
with:

```python
def labellable_agent(pk):
    """One `Agent` row by pk -- or `None`. A READER, never a gate.

    TWO CALLERS, TWO DIFFERENT GATES, and the gate is always the
    caller's. `/chat/access/`'s POST handler is CLASS S, so
    `IdentityGateMiddleware` has already refused anybody but an
    administrator before this is reached and there is no narrower
    principal to ask about -- the reason this reader is unfiltered by
    principal at all. `agents.chat.views.agents.agent_edit` (chat
    cluster, feature B) is CLASS O: it applies
    `may_manage_agent(principal, row)` to what this returns and turns a
    refusal into the house 404. An unfiltered reader is safe for both
    precisely because neither treats it as the permission check.

    A ONE-QUERY reader, not a `next((r for r in labellable_agents()
    if r.pk == pk), None)` full-table scan per POST -- the same N+1
    shape `tool_entitlement_ids`'s own docstring warns against, on the
    write side rather than the render side.
    """
    return Agent.objects.filter(pk=pk).first()
```

- [ ] **Step 4: Wire the routes**

In `agents/chat/views/__init__.py`, add `from agents.chat.views.agents import agent_edit, agent_list, agent_new` and the three names to `__all__`.

In `agents/chat/urls.py`, add to the import block and to `urlpatterns`, beside `/chat/w/`:

```python
    # THE AGENT PAGES (chat cluster, feature B). `agents/new/` BEFORE
    # `agents/<int:pk>/` reads clearly even though Django's `int`
    # converter would never match the literal "new" anyway -- the same
    # non-issue `w/new/`'s own comment already names.
    path("agents/", agent_list, name="chat-agents"),
    path("agents/new/", agent_new, name="chat-agent-new"),
    path("agents/<int:pk>/", agent_edit, name="chat-agent-edit"),
```

In `identity/routes.py::ROUTE_RULES`, beside `chat-agent-entitlements`:

```python
    # THE AGENT PAGES (chat cluster, feature B).
    # A: a signed-in person's own list; the rows are narrowed in the
    # view by `editable_agents`, so there is no row to be addressed by.
    "chat-agents": "A",
    # A: creation needs no row.
    "chat-agent-new": "A",
    # O, NOT S, and the difference is the whole feature: an S route
    # would refuse a non-admin at the middleware, which is exactly the
    # person this page exists for. Row-addressed, 404 in the view unless
    # `agents.visibility.may_manage_agent` -- the same shape
    # `chat-conversation-rename` and its siblings carry.
    "chat-agent-edit": "O",
```

In `identity/tests/test_route_matrix.py::_DRIVERS`, beside the other `/chat/` entries:

```python
    "chat-agents": lambda w: ("get", reverse("chat-agents"), {}),
    "chat-agent-new": lambda w: ("post", reverse("chat-agent-new"),
                                 {"name": "Driver agent", "description": "",
                                  "system_prompt": "", "max_steps": "2",
                                  "enabled": "on"}),
    # Row-addressed and owned by `other`, exactly like the conversation
    # menu's own four drivers -- the base O mapping (member 404, admin
    # 404 or admitted with the content toggle) is `may_manage_agent`'s
    # own answer, so no override set applies. The body names the fields
    # the view really reads; a driver naming fields it ignores would
    # exercise the "you sent nothing" branch and pass while testing the
    # wrong thing.
    "chat-agent-edit": lambda w: (
        "post", reverse("chat-agent-edit", args=[w.agent.pk]),
        {"action": "fields", "name": "Driven", "description": "",
         "system_prompt": "", "max_steps": "2", "enabled": "on"}),
```

In `agents/chat/tests/test_never_500.py`, write four drivers per route in the existing style (normal, no agents, queue down, malformed target) and register them in `_DRIVERS`:

```python
def _agents_list_normal(client, monkeypatch):
    make_agent(slug=_unique_slug("sweep"))
    return client.get(reverse("chat-agents"))


def _agents_list_no_agents(client, monkeypatch):
    return client.get(reverse("chat-agents"))


def _agents_list_queue_down(client, monkeypatch):
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    return client.get(reverse("chat-agents"))


def _agents_list_malformed_target(client, monkeypatch):
    return client.get(f"{reverse('chat-agents')}?open=%00")


def _agent_new_normal(client, monkeypatch):
    return client.post(reverse("chat-agent-new"), {
        "name": "Swept", "description": "", "system_prompt": "",
        "max_steps": "2", "enabled": "on"})


def _agent_new_no_agents(client, monkeypatch):
    return client.get(reverse("chat-agent-new"))


def _agent_new_queue_down(client, monkeypatch):
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    return client.get(reverse("chat-agent-new"))


def _agent_new_malformed_target(client, monkeypatch):
    """A blank name, a non-numeric step count and an off-box `next` in
    one body -- every refusal this route can reach, at once."""
    return client.post(reverse("chat-agent-new"), {
        "name": "", "description": "", "system_prompt": "", "max_steps": "not-a-number",
        "enabled": "on", "next": "https://elsewhere.example/steal"})


def _agent_edit_normal(client, monkeypatch):
    agent = make_agent(slug=_unique_slug("sweep-edit"))
    return client.post(reverse("chat-agent-edit", args=[agent.pk]), {
        "action": "fields", "name": "Swept", "description": "", "system_prompt": "",
        "max_steps": "2", "enabled": "on"})


def _agent_edit_no_agents(client, monkeypatch):
    return client.get(reverse("chat-agent-edit", args=[424242]))


def _agent_edit_queue_down(client, monkeypatch):
    agent = make_agent(slug=_unique_slug("sweep-edit-down"))
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    return client.get(reverse("chat-agent-edit", args=[agent.pk]))


def _agent_edit_malformed_target(client, monkeypatch):
    agent = make_agent(slug=_unique_slug("sweep-edit-bad"),
                       llm_role="not.a.registered.role")
    return client.post(reverse("chat-agent-edit", args=[agent.pk]), {
        "action": "nonsense", "name": "", "max_steps": "-1"})
```

```python
    "chat-agents": (_agents_list_normal, _agents_list_no_agents,
                    _agents_list_queue_down, _agents_list_malformed_target),
    "chat-agent-new": (_agent_new_normal, _agent_new_no_agents,
                       _agent_new_queue_down, _agent_new_malformed_target),
    "chat-agent-edit": (_agent_edit_normal, _agent_edit_no_agents,
                        _agent_edit_queue_down, _agent_edit_malformed_target),
```

- [ ] **Step 5: Write the templates**

Create `agents/chat/templates/chat/_agent_form.html` — the ONE field form, a fragment with no `<style>` and no `<script>`:

```html
{% comment %}
THE ONE AGENT FIELD FORM. Rendered by `chat/agent_edit.html` for both
mounts, from `agents/chat/agentform.py::agent_form_context`. Every
sentence it shows is declared in Python and handed in; this file types
labels and nothing else.

NO `slug` INPUT, EVER. `Agent.save()` refuses a slug change because a
slug is a KEY -- `flow.run` resolves one, an `agent.<slug>` grant names
one, `install_defaults --reset` matches on one -- so a form that offered
the field would be offering a refusal.

`llm_role` AND THE REACH RADIO ARE ADMINISTRATOR-ONLY DATA, and a
non-admin's CONTEXT never builds them (render-vs-gate) -- the `{% if %}`
below is the second belt, not the gate.
{% endcomment %}
<input type="hidden" name="action" value="fields">
<input type="hidden" name="next" value="{{ next_value }}">

{% if resident_warning %}<p class="muted">{{ resident_warning }}</p>{% endif %}

<p>
  <label for="agent-name">Name</label>
  <input id="agent-name" name="name" value="{{ values.name }}" maxlength="255" required>
  {% if errors.name %}<span class="form-error">{{ errors.name }}</span>{% endif %}
</p>
<p>
  <label for="agent-description">Description</label>
  <textarea id="agent-description" name="description" rows="2">{{ values.description }}</textarea>
</p>
<p>
  <label for="agent-system-prompt">System prompt</label>
  <textarea id="agent-system-prompt" name="system_prompt" rows="10">{{ values.system_prompt }}</textarea>
</p>
{% if may_choose_role %}
<p>
  <label for="agent-llm-role">Model role</label>
  <select id="agent-llm-role" name="llm_role">
    {% for key, label in role_options %}
    <option value="{{ key }}"{% if agent and agent.llm_role == key %} selected{% endif %}>{{ label }}</option>
    {% endfor %}
  </select>
</p>
{% elif agent %}
<p class="muted">Model role: {{ current_role_label }}</p>
{% endif %}
<p>
  <label for="agent-max-steps">Steps per turn</label>
  <input id="agent-max-steps" name="max_steps" type="number" min="1"
         max="{{ max_steps_ceiling }}" value="{{ values.max_steps }}">
  {% if errors.max_steps %}<span class="form-error">{{ errors.max_steps }}</span>{% endif %}
</p>
<p>
  <label><input type="checkbox" name="enabled"{% if values.enabled %} checked{% endif %}> Enabled</label>
</p>
{% if may_set_reach %}
<fieldset class="agent-reach">
  <legend>Reach</legend>
  <label><input type="radio" name="box_wide" value=""{% if not box_wide %} checked{% endif %}> The people I give it to</label>
  <label><input type="radio" name="box_wide" value="1"{% if box_wide %} checked{% endif %}> Everyone on this box</label>
</fieldset>
{% endif %}
```

`update_agent` reads `box_wide` as a truthy value; `""` and `"1"` give the two radio answers, and `bool("")` is False — no extra branch.

Create `agents/chat/templates/chat/agent_edit.html` — the page **both** `chat-agent-new` and `chat-agent-edit` render:

```html
{% extends "chat/base.html" %}
{% comment %}
THE ONE AGENT PAGE, rendered by the create route with `agent` unset and
by the edit route with it set. `form_action` decides which, so there is
no second template and no page-level branch over two nearly-identical
forms.

THE FIELD FORM AND THE ENTITLEMENT PANEL ARE SIBLINGS, NEVER NESTED.
`_transfer_panel.html` renders its own `<form>` (two, counting the inert
filter form), and its own header says it "must NOT be included inside
another one" -- nested `<form>` elements are illegal HTML and the inner
one silently does not submit. That is why the panel block sits AFTER
`</form>` below, and why the two POST paths are told apart by an
`action` field rather than by one form with two buttons.

Task 9 fills in the panel block; until then this page renders the field
form alone, which is all the create route ever needs.
{% endcomment %}
{% block title %}{% if agent %}{{ agent.name }}{% else %}New agent{% endif %} — Chat — farabunker{% endblock %}
{% block chat_sidebar %}{% include "chat/_sidebar.html" %}{% endblock %}
{% block chat_content %}
<h1>{% if agent %}{{ agent.name }}{% else %}New agent{% endif %}</h1>
{% include "chat/_messages.html" %}
<form method="post" action="{{ form_action }}">
  {% csrf_token %}
  {% include "chat/_agent_form.html" %}
  <p>
    <button type="submit">Save</button>
    <a href="{% url 'chat-agents' %}" class="muted">Cancel</a>
  </p>
</form>
{% endblock %}
```

Confirm `chat/_messages.html` is really the flash fragment the other chat pages include (`grep -n '_messages' agents/chat/templates/chat/*.html`) and use whatever they use; do not invent a name.

Create `agents/chat/templates/chat/agents.html`:

```html
{% extends "chat/base.html" %}
{% comment %}
"THE AGENTS I WORK ON" (`/chat/agents/`, class A). Two sections, and the
second exists because of a route that predates this page: `chat-default-
install` is class A and stamps the INSTALLING principal as the owner, so
a member can own a `box_wide` row that `may_manage_agent` refuses them.
Listing it read-only, with a sentence, is what stops that row from being
owned, absent and refused with nothing anywhere explaining why.

THE RESTRICTION COUNT IS A BARE NUMBER, NEVER A NAME. The entitlement
non-disclosure rule is a gate, and `identity/tests/test_route_matrix.py`
sweeps this route for it. The count is folded in the VIEW from two batch
reads -- one `agent_entitlement_ids()` and one `labelling_entitlements`
-- so twenty-five rows cost what one row costs, and no per-row
`held_entitlement_ids` read happens here (the sidebar N+1, avoided on
the way in rather than found later).
{% endcomment %}
{% block title %}Agents — Chat — farabunker{% endblock %}
{% block chat_sidebar %}{% include "chat/_sidebar.html" %}{% endblock %}
{% block chat_content %}
<h1>Agents</h1>
{% include "chat/_messages.html" %}
<p><a class="chat-rail-primary"
      href="{% url 'chat-agent-new' %}?next={{ next_url|urlencode }}">New agent</a></p>

{% for row in rows %}
<section class="row">
  <h2><a href="{% url 'chat-agent-edit' row.agent.pk %}?next={{ next_url|urlencode }}">{{ row.agent.name }}</a></h2>
  {% if row.agent.description %}<p class="muted">{{ row.agent.description }}</p>{% endif %}
  {% if not row.agent.enabled %}<p class="muted">Switched off</p>{% endif %}
  {% if row.restrictions %}<p class="muted">{{ row.restrictions }} restriction{{ row.restrictions|pluralize }}</p>{% endif %}
</section>
{% empty %}
<p class="muted">No agents of your own yet — <a href="{% url 'chat-agent-new' %}">make one</a>.</p>
{% endfor %}

{% if box_wide_rows %}
<h2 class="group-head">{{ box_wide_section_title }}</h2>
{% for row in box_wide_rows %}
{% comment %}
NO EDIT LINK HERE, deliberately: `may_manage_agent` refuses a box-wide
row for a non-admin owner, so a link would be a link to a 404 -- the
defect this section exists to prevent, shipped in a different shape.
{% endcomment %}
<section class="row">
  <h2>{{ row.agent.name }}</h2>
  <p class="muted">{{ box_wide_section_note }}</p>
  {% if row.restrictions %}<p class="muted">{{ row.restrictions }} restriction{{ row.restrictions|pluralize }}</p>{% endif %}
</section>
{% endfor %}
{% endif %}
{% endblock %}
```

Django has no `get_item` filter, which is why `agent_list` hands the template a list of dicts — `{"agent": <row>, "restrictions": <int>}` — rather than a parallel `restriction_counts` mapping keyed by pk. Change the view's `rows` and `box_wide_rows` build in Step 3 to that shape and drop the separate `"restriction_counts"` context key. `pluralize` is a built-in; no `{% load %}` is needed for it.

Put every selector these two pages need in `agents/chat/templates/chat/base.html`, not in either page's own block: `_agent_form.html` is a fragment with two page consumers, and the CSS gate's rule is that its rules belong in the nearest common ancestor from the day it is created.

- [ ] **Step 6: Add the rail entry**

In `agents/chat/templates/chat/_sidebar.html`, after the Workstreams `<details>` section:

```html
  {% comment %}
  THE AGENT PAGES (chat cluster, feature B). A plain link beside
  Workstreams rather than a `<details>` list: this rail lists ROWS a
  reader picks between, and "the agents I work on" is a page, not a
  picker.
  {% endcomment %}
  <a class="chat-nav-link" href="{% url 'chat-agents' %}">Agents</a>
```

- [ ] **Step 7: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -q agents/chat/tests/test_agent_pages.py`
Expected: PASS.
Run: `.venv/bin/pytest -q agents/chat/tests/test_never_500.py identity/tests/test_route_matrix.py foundation/ops`
Expected: PASS — including `test_every_route_has_a_driver`, the CSS gate and the column-boundary gate.

- [ ] **Step 8: Run the four runs and both posture sweeps**

As in Task 5, Step 9.

- [ ] **Step 9: Commit**

```
git add agents/chat/views/agents.py agents/chat/views/__init__.py agents/chat/urls.py agents/visibility.py agents/chat/templates/chat/_agent_form.html agents/chat/templates/chat/agent_edit.html agents/chat/templates/chat/agents.html agents/chat/templates/chat/base.html agents/chat/templates/chat/_sidebar.html identity/routes.py identity/tests/test_route_matrix.py agents/chat/tests/test_never_500.py agents/chat/tests/test_agent_pages.py
git commit -m "feat(chat): /chat/agents/ — the user-facing agent list, create and edit"
```

---

### Task 9: The label editor — the second POST path

**Files:**
- Modify: `agents/chat/views/agents.py` (`agent_edit`'s POST dispatch, `_save_labels`)
- Modify: `agents/chat/templates/chat/agent_edit.html` (the second form, in its own `<details>`)
- Test: `agents/chat/tests/test_agent_pages.py`

**Interfaces:**
- Consumes: `agents.chat.service.parse_entitlement_diff / entitlement_change_flash / validated_next_url`; `agents.labels.agent_label_ids / set_agent_labels`; `identity.request.user_for_request`; `agents.chat.agentform.agent_form_context`'s `entitlement_panel` key (Task 7).
- **Not** `agents.chat.service.entitlement_row_url`, which the two access pages use to land a refusal back on the row's own anchor: it builds `reverse(route_name)` with **no arguments**, and `chat-agent-edit` is row-addressed, so it cannot name this route at all. The refusal URL here is `reverse("chat-agent-edit", args=[agent.pk])`, which already *is* the row.
- Produces: `agent_edit`'s `action="labels"` branch. Nothing later consumes it.

**This is the whole of the M1 fix, and it is a reuse rather than a new mechanism.** `agents/labels.py::set_agent_labels` is a **raw writer**: it takes `actor` only to stamp the audit row, never consults `labelling_entitlements`, and its `remove` closure deletes any `AgentEntitlement` row the diff names. The enforcement lives one layer up, in `agents/chat/service.py::parse_entitlement_diff`, which parses an **add/remove operation over what is there now** and refuses any id outside `labelling_entitlements(principal)`. `agents/chat/views/access.py::_save` then computes `before | submitted` or `before - submitted`. Copy that shape exactly; do not write a whole set.

- [ ] **Step 1: Write the failing test**

Append to `agents/chat/tests/test_agent_pages.py`:

```python
class TestTheAudienceWriteInBothDirections:
    """Spec review M1's headline. A label the actor may not label with is
    never in `choices`, therefore never in either pane, therefore never
    in `submitted` -- so it survives BOTH directions untouched, by
    construction rather than by a check. A test for the ADD direction
    alone would never have caught the hole this closes."""

    def _member_owned_agent_an_admin_labelled(self):
        member, admin = make_user(), make_admin()
        legal = make_entitlement(name="Legal")
        agent = make_agent(slug="two-controls", **owner_fields(user_principal(member)))
        set_agent_labels(user_principal(admin), agent, {legal.pk})
        return member, agent, legal

    def test_the_panel_offers_the_administrators_label_in_NEITHER_pane(self, client):
        from agents.chat.agentform import agent_form_context

        with posture(POSTURE_ENTERPRISE):
            member, agent, legal = self._member_owned_agent_an_admin_labelled()
            panel = agent_form_context(user_principal(member),
                                       agent=agent)["entitlement_panel"]
        offered = {row["id"] for row in (panel or {"available": [], "active": []})[
            "available"]}
        offered |= {row["id"] for row in (panel or {"available": [], "active": []})[
            "active"]}
        assert legal.pk not in offered

    def test_a_forged_remove_is_refused_and_the_row_survives(self, client):
        with posture(POSTURE_ENTERPRISE):
            member, agent, legal = self._member_owned_agent_an_admin_labelled()
            sign_in(client, member)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "remove", "remove": str(legal.pk)})
            assert response.status_code == 302
            assert agent_label_ids(agent) == frozenset({legal.pk})

    def test_a_forged_add_is_refused_identically(self, client):
        with posture(POSTURE_ENTERPRISE):
            member, agent, _legal = self._member_owned_agent_an_admin_labelled()
            unowned = make_entitlement(name="Finance")
            sign_in(client, member)
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(unowned.pk)})
            assert unowned.pk not in agent_label_ids(agent)

    def test_the_member_adds_and_removes_their_OWN_and_the_admins_is_untouched(
        self, client
    ):
        with posture(POSTURE_ENTERPRISE):
            member, agent, legal = self._member_owned_agent_an_admin_labelled()
            # OWNING AN ENTITLEMENT IS AN `EntitlementGrant` WITH
            # `role="owner"`, not a column on the row.
            # `identity/models.py::Entitlement` carries `name`,
            # `description`, `created_by`, `created_at` and nothing
            # else, and `identity.access.labelling_entitlements` filters
            # on OWNED ids -- merely holding one puts it in neither
            # pane. Getting this wrong does not fail loudly: it would
            # quietly assert the member's own add/remove leg against an
            # entitlement they cannot label with, which is the other
            # half of this same test.
            mine = make_entitlement(name="Mine")
            grant(mine, user=member, role="owner")
            sign_in(client, member)
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(mine.pk)})
            assert agent_label_ids(agent) == frozenset({legal.pk, mine.pk})
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "remove", "remove": str(mine.pk)})
            assert agent_label_ids(agent) == frozenset({legal.pk})

    def test_a_stale_form_is_harmless_in_both_directions(self, client):
        """`op="remove"` naming a label already gone is a no-op with no
        audit row, and `op="add"` naming one already present likewise --
        the DIFF is the point (`agents/labels.py::_set_labels`)."""
        from identity.contracts import actions
        from identity.models import AuditEvent

        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            entitlement = make_entitlement(name="Present")
            agent = make_agent(slug="stale", **owner_fields(user_principal(admin)))
            set_agent_labels(user_principal(admin), agent, {entitlement.pk})
            before = AuditEvent.objects.filter(action=actions.AGENT_LABELLED).count()
            sign_in(client, admin)
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(entitlement.pk)})
            assert AuditEvent.objects.filter(
                action=actions.AGENT_LABELLED).count() == before

    def test_two_edits_to_different_labels_do_not_clobber_each_other(self, client):
        """The add/remove shape's own reason for existing: a whole
        submitted set clobbers."""
        with posture(POSTURE_ENTERPRISE):
            admin = make_admin()
            first, second = make_entitlement(name="A"), make_entitlement(name="B")
            agent = make_agent(slug="concurrent", **owner_fields(user_principal(admin)))
            sign_in(client, admin)
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(first.pk)})
            client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(second.pk)})
            assert agent_label_ids(agent) == frozenset({first.pk, second.pk})

    def test_the_label_path_is_unreachable_on_a_box_wide_row_for_a_non_admin(
        self, client
    ):
        """Both POST paths are on `chat-agent-edit`, class O, 404 unless
        `may_manage_agent` -- which short-circuits False on `box_wide`
        for a non-admin. The panel is not merely absent; the route is."""
        member = make_user()
        entitlement = make_entitlement(name="Anything")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="box-wide-labels", box_wide=True,
                               **owner_fields(user_principal(member)))
            sign_in(client, member)
            response = client.post(reverse("chat-agent-edit", args=[agent.pk]), {
                "action": "labels", "op": "add", "add": str(entitlement.pk)})
        assert response.status_code == 404
        assert agent_label_ids(agent) == frozenset()


class TestTheTruthTable:
    """`visible_agents` is `(owned | box_wide | shared) AND
    label_permitted_q`, which is a truth table, not an exclusive choice.
    An administrator with `admin_sees_content` ON sees every enabled row
    whatever this table says, because the `sees_all_content`
    short-circuit returns before the label clause is reached."""

    def test_all_four_rows(self):
        from agents.visibility import visible_agents

        holder, stranger, admin = (make_user(), make_user(username="stranger"),
                                   make_admin())
        legal = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            grant(legal, user=holder)
            owner_bits = owner_fields(user_principal(holder))
            private = make_agent(slug="r1-private", **owner_bits)
            private_labelled = make_agent(slug="r2-private-labelled", **owner_bits)
            wide = make_agent(slug="r3-wide", box_wide=True,
                              **owner_fields(user_principal(admin)))
            wide_labelled = make_agent(slug="r4-wide-labelled", box_wide=True,
                                       **owner_fields(user_principal(admin)))
            set_agent_labels(user_principal(admin), private_labelled, {legal.pk})
            set_agent_labels(user_principal(admin), wide_labelled, {legal.pk})

            holder_sees = set(visible_agents(user_principal(holder))
                              .values_list("slug", flat=True))
            stranger_sees = set(visible_agents(user_principal(stranger))
                                .values_list("slug", flat=True))
        assert private.slug in holder_sees and private.slug not in stranger_sees
        assert private_labelled.slug in holder_sees
        assert wide.slug in holder_sees and wide.slug in stranger_sees
        assert wide_labelled.slug in holder_sees
        assert wide_labelled.slug not in stranger_sees

    def test_the_AND_still_restricts_an_owner_of_a_labelled_row(self):
        """The AND applies to an owner too -- which is what makes
        labelling one's own agent actually restrict it rather than being
        bypassable by the person it is aimed at."""
        from agents.visibility import visible_agents

        owner, admin = make_user(), make_admin()
        legal = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="own-but-labelled",
                               **owner_fields(user_principal(owner)))
            set_agent_labels(user_principal(admin), agent, {legal.pk})
            sees = set(visible_agents(user_principal(owner))
                       .values_list("slug", flat=True))
        assert "own-but-labelled" not in sees


class TestTheInstallInteraction:
    """Spec review M5, pinned against today's behaviour rather than
    asserted about the new column alone."""

    def test_a_member_installing_a_catalogue_slug_owns_a_box_wide_row_they_cannot_edit(
        self, client
    ):
        from agents.defaults import DEFAULT_AGENTS
        from agents.models import Agent
        from agents.visibility import may_manage_agent

        slug = DEFAULT_AGENTS[0].slug
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            client.post(reverse("chat-default-install"), {"kind": "agent", "slug": slug})
            row = Agent.objects.get(slug=slug)
            assert row.box_wide is True
            assert row.owner_kind == "user"
            assert may_manage_agent(user_principal(member), row) is False
            body = client.get(reverse("chat-agents")).content.decode()
        assert "available to everyone here" in body
        assert row.name in body

    def test_the_row_still_reaches_everybody_exactly_as_it_did_before(self):
        from agents.defaults import DEFAULT_AGENTS, install_default
        from agents.visibility import visible_agents
        from identity.contracts.principals import Principal

        slug = DEFAULT_AGENTS[0].slug
        install_default("agent", slug, Principal("user", "5"))
        assert slug in set(visible_agents(Principal("user", "9"))
                           .values_list("slug", flat=True))
```

`identity/testing.py`'s real shapes, verified at plan time and used above: `make_entitlement(**overrides)` splats straight into `Entitlement.objects.create`, whose columns are `name`, `description`, `created_by`, `created_at` — there is no `owner` keyword — and `grant(entitlement, *, user=None, group=None, role="member")` is where ownership lives, as a row with `role="owner"`.

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest -q agents/chat/tests/test_agent_pages.py -k "AudienceWrite or TruthTable or InstallInteraction"`
Expected: FAIL — the forged-remove test, because `action="labels"` currently falls through to `_save_fields` and refuses on a blank name rather than through the diff gate.

- [ ] **Step 3: Dispatch the two POST paths**

In `agents/chat/views/agents.py::agent_edit`, replace the single POST line:

```python
    if request.method == "POST":
        # TWO FORMS, ONE ROUTE, told apart by `action`. Reach is a plain
        # field on the main form; labels go through the diff gate. A
        # single control writing both would either clobber a label its
        # actor may not touch (the hole spec review M1 found) or refuse
        # an edit it should allow.
        if request.POST.get("action") == "labels":
            return _save_labels(request, principal, agent, settings_row)
        return _save_fields(request, principal, agent, settings_row)
```

and add:

```python
def _save_labels(request, principal, agent, settings_row):
    """The entitlement panel's save -- the SAME four steps
    `agents/chat/views/access.py::_save` takes, reached from this page.

    `parse_entitlement_diff` IS THE GATE, and `set_agent_labels` is not.
    That writer is raw: it takes `actor` only to stamp the audit row,
    never consults `labelling_entitlements`, and its `remove` closure
    deletes any row the diff names. The check that a submitted id is one
    this principal may label with lives in the parser, over the SAME
    predicate the form rendered from, so a stale form or a hand-made
    request gets the identical honest refusal.

    THE NEW SET IS DERIVED FROM WHAT IS THERE NOW, never from what the
    form showed: a submitted whole set clobbers, and a label this actor
    may not label with is never in `submitted`, so `before | submitted`
    cannot add it and `before - submitted` cannot remove it. That is why
    an administrator's label stands whatever a member does with their
    own, by construction.
    """
    back = reverse("chat-agent-edit", args=[agent.pk])
    parsed = parse_entitlement_diff(request, principal, settings_row,
                                    redirect_url=back)
    if not isinstance(parsed, tuple):
        return parsed
    operation, submitted = parsed
    # `user_for_request`, NOT a bare `request.user` read: `agents/` is
    # one of the three columns `test_no_view_outside_identity_reads_
    # request_user` scans, and `AgentEntitlement.labelled_by` needs the
    # real `User` instance rather than the `Principal` value object.
    labelled_by = user_for_request(request)
    before = set(agent_label_ids(agent))
    wanted = before | submitted if operation == "add" else before - submitted
    set_agent_labels(principal, agent, wanted, labelled_by=labelled_by)
    messages.info(request, entitlement_change_flash(agent.slug, before, wanted))
    return redirect(validated_next_url(request) or back)
```

Add the imports: `from agents.chat.service import entitlement_change_flash, parse_entitlement_diff, validated_next_url`, `from agents.labels import agent_entitlement_ids, agent_label_ids, set_agent_labels`, `from identity.request import principal_for_request, settings_row_for, user_for_request`.

- [ ] **Step 4: Render the panel**

In `agents/chat/templates/chat/agent_edit.html`, after the main form:

```html
{% if entitlement_panel %}
{% comment %}
CONTROL 2 -- ENTITLEMENT LABELS. A SIBLING of the field form above,
never nested inside it: `_transfer_panel.html` renders its OWN `<form>`
(two of them, counting the inert filter form), and its own header says
"ONE FORM, NO NESTING -- this fragment renders its own `<form>`, so it
must NOT be included inside another one." Nested `<form>` elements are
illegal HTML and the inner one simply does not submit.

The include below is `chat/agent_entitlements.html`'s own line with two
values changed (`tp_fields`, and no `tp_action` because this route posts
to itself). `tp_label` is deliberately ABSENT -- the fragment's
parameter block says phase 2 omits it "because the `<details>` summary
it sits under already names the row", and this panel sits under one.
{% endcomment %}
<details class="agent-labels">
  <summary>Who may use this agent</summary>
  {% if foreign_label_sentence %}<p class="muted">{{ foreign_label_sentence }}</p>{% endif %}
  {% include "_transfer_panel.html" with tp_key=entitlement_panel.anchor tp_anchor=entitlement_panel.panel_anchor tp_fields=entitlement_panel.fields tp_bare=1 tp_available=entitlement_panel.available tp_active=entitlement_panel.active tp_available_count=entitlement_panel.available_count tp_active_count=entitlement_panel.active_count tp_total=entitlement_panel.total tp_empty_catalogue=entitlement_panel.no_entitlements %}
</details>
{% endif %}
```

Every parameter above is one the fragment's own block marks required, and the pane
lists are **`tp_available` / `tp_active`** — not `available` / `active`, which an
earlier draft of this step passed and which would have rendered two empty panes
with no error at all. `agents/chat/templates/chat/agent_entitlements.html` and
`chat/tool_entitlements.html` carry byte-identical includes; diff yours against one
of them before moving on.

**Promote the panel's CSS one tier, do not copy it.** `_transfer_panel.html`'s own
note says every `.transfer-*` rule lives in `foundation/templates/_settings.html`
*"because EVERY CONSUMER TODAY EXTENDS `_settings.html`"*, and states what happens
next: *"A consumer outside the settings area later promotes those rules one tier to
`_shell.html`, exactly as `.messages`/`.msg` were promoted when four non-settings
bases needed them; it does not copy them."* `chat/agent_edit.html` extends
`chat/base.html`, so this task **is** that consumer: move the `.transfer-*` block
from `_settings.html` to `foundation/templates/_shell.html` in this commit, and
delete it from `_settings.html` rather than leaving two copies.
`foundation/ops/tests/test_css_ownership.py` is what fails if it is copied instead
of moved, and the commit message says the promotion happened.

`_filter_rows_script.html` is included **beside** the panel by its existing
consumers; do the same, and note that this page is not the thread page — its script
count is not pinned at 4/3, and nothing in this plan adds such a pin.

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -q agents/chat/tests/test_agent_pages.py`
Expected: PASS.
Run: `.venv/bin/pytest -q agents identity foundation`
Expected: PASS.

- [ ] **Step 6: Run the four runs and both posture sweeps**

As in Task 5, Step 9.

- [ ] **Step 7: Commit**

```
git add agents/chat/views/agents.py agents/chat/templates/chat/agent_edit.html foundation/templates/_settings.html foundation/templates/_shell.html agents/chat/tests/test_agent_pages.py
git commit -m "feat(chat): the agent label editor — the existing add/remove diff gate, reused"
```

---

### Task 10: `/settings/agents/` — the administrator's list

**Files:**
- Create: `agents/chat/views/agents_admin.py`, `agents/chat/agent_admin_urls.py`, `agents/chat/templates/chat/agents_admin.html`
- Create: `agents/chat/tests/test_settings_agents.py`
- Modify: `agents/chat/views/__init__.py`, `config/urls.py`, `foundation/settings_area.py`, `identity/routes.py`
- Test: `identity/tests/test_route_matrix.py`, `foundation/tests/test_shell.py` (the settings drift test picks the entry up on its own — confirm, do not edit, unless it enumerates entries by name)
- Modify: `docs/EXTENDING.md`

**Interfaces:**
- Consumes: `agents.visibility.labellable_agents` (the existing unfiltered read, reused rather than re-spelled); `agents.labels.agent_entitlement_ids`; `identity.access.is_admin`; `foundation.settings_area.Entry / ADMIN`.
- Produces: the view `agents_admin_list`, the route name `settings-agents` (class S), the settings entry `Entry("Agent library", "settings-agents", ADMIN)`. It reuses `chat-agent-edit` from Task 8; it adds no second edit route.

**"Agent library", not "Agents"** (spec review n1): the Access group two rows down already carries **Agent access** (`chat-agent-entitlements`), and two entries sharing one noun for two different jobs is a nav an operator has to learn rather than read.

- [ ] **Step 1: Write the failing test**

Create `agents/chat/tests/test_settings_agents.py`:

```python
"""`/settings/agents/` -- the box-wide agent list.

ITS OWN NEVER-500 MODULE, because `config/urls.py` mounts this route from
`agents/chat/agent_admin_urls.py` and `agents/chat/tests/test_never_500.py`
derives its sweep from `agents.chat.urls.urlpatterns` -- which cannot see
it (spec review m4). Exactly the position the three `/settings/assistant/`
routes are in.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (   # noqa: F401
    make_admin, make_agent, make_entitlement, make_user, posture, sign_in, user_principal,
)
from agents.labels import set_agent_labels
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = pytest.mark.django_db

_NEVER_500_STATUSES = frozenset({200, 302, 403, 404})


class TestTheSettingsList:
    def test_it_lists_every_agent_for_an_administrator(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="members-row", name="Member's row",
                       **owner_fields(user_principal(member)))
            make_agent(slug="wide-row", name="Wide row", box_wide=True)
            sign_in(client, make_admin())
            body = client.get(reverse("settings-agents")).content.decode()
        assert "Member's row" in body
        assert "Wide row" in body

    def test_it_is_refused_to_a_member_at_the_gate(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            assert client.get(reverse("settings-agents")).status_code == 403

    def test_it_carries_its_own_is_admin_check_as_well_as_the_gate(self):
        """The gate is not the only way a view function can be reached
        -- the reasoning `agents/chat/views/assistant.py` records for its
        own three views."""
        import inspect

        from agents.chat.views import agents_admin

        assert "is_admin" in inspect.getsource(agents_admin)

    def test_it_shows_each_rows_audience_and_owner(self, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="audience-shown", name="Audience shown", box_wide=True,
                       **owner_fields(user_principal(member)))
            sign_in(client, make_admin())
            body = client.get(reverse("settings-agents")).content.decode()
        assert "Everyone on this box" in body

    def test_every_row_links_to_the_SAME_edit_route(self, client):
        with posture(POSTURE_ENTERPRISE):
            agent = make_agent(slug="linked", name="Linked")
            sign_in(client, make_admin())
            body = client.get(reverse("settings-agents")).content.decode()
        assert reverse("chat-agent-edit", args=[agent.pk]) in body

    def test_the_list_costs_the_same_at_one_agent_and_at_twenty_five(self, client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="settings-one")
            sign_in(client, make_admin())
            client.get(reverse("settings-agents"))         # warm-up, unmeasured
            with CaptureQueriesContext(connection) as one:
                client.get(reverse("settings-agents"))
            for index in range(25):
                make_agent(slug=f"settings-many-{index}")
            with CaptureQueriesContext(connection) as many:
                client.get(reverse("settings-agents"))
        assert len(many) == len(one)


class TestItNever500s:
    """The four conditions `agents/chat/tests/test_never_500.py` applies
    to every `/chat/` route, applied here because that sweep cannot see
    this one."""

    def _assert(self, response):
        assert response.status_code in _NEVER_500_STATUSES, response.status_code
        assert "Traceback" not in response.content.decode(errors="replace")
        return response

    def test_a_normal_read(self, client):
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="sweep-normal")
            sign_in(client, make_admin())
            self._assert(client.get(reverse("settings-agents")))

    def test_with_no_agents_at_all(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            self._assert(client.get(reverse("settings-agents")))

    def test_with_a_row_naming_an_unregistered_role(self, client):
        with posture(POSTURE_ENTERPRISE):
            make_agent(slug="sweep-role", llm_role="not.a.registered.role")
            sign_in(client, make_admin())
            self._assert(client.get(reverse("settings-agents")))

    def test_a_post_is_a_405_not_a_traceback(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            response = client.post(reverse("settings-agents"), {})
        assert response.status_code in _NEVER_500_STATUSES | {405}


class TestTheSettingsEntry:
    def test_the_nav_carries_it_under_its_own_noun(self):
        """"Agent library", not "Agents": the Access group already
        carries "Agent access" for a different job, and two entries
        sharing one noun is a nav an operator has to learn rather than
        read (spec review n1)."""
        from foundation.settings_area import SETTINGS_GROUPS

        labels = {entry.label for _group, entries in SETTINGS_GROUPS
                  for entry in entries}
        assert "Agent library" in labels
        assert "Agent access" in labels

    def test_it_sits_in_the_setup_group_and_is_admin_gated(self):
        from foundation.settings_area import ADMIN, SETTINGS_GROUPS

        setup = dict(SETTINGS_GROUPS)["Setup"]
        entry = next(e for e in setup if e.url_name == "settings-agents")
        assert entry.gate == ADMIN
        assert entry.feature is None
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest -q agents/chat/tests/test_settings_agents.py`
Expected: FAIL — `NoReverseMatch: Reverse for 'settings-agents' not found`.

- [ ] **Step 3: Write the view**

Create `agents/chat/views/agents_admin.py`:

```python
"""`/settings/agents/` -- every agent on this box, for an administrator.

CLASS S, AND IT CARRIES ITS OWN `is_admin` CHECK IN ADDITION to the
middleware gate, for the reason `agents/chat/views/assistant.py` records
for its own three views: the gate is not the only way a view function
can be reached.

IT LISTS THROUGH `agents.visibility.labellable_agents`, the EXISTING
unfiltered read, reused rather than re-spelled. That function's own
docstring gives the reasoning this page inherits unchanged: class S
already means every caller here is an administrator, so asking
`visible_agents(principal)` would wrongly hide a member's own agent from
a page that exists to manage the box's agents, whenever
`admin_sees_content` is off.

IT ADDS NO SECOND EDIT ROUTE. Every row links to `chat-agent-edit` with
its own `?next=`; one edit route serves both mounts, which is the whole
of the owner's "not an entangled mess".
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.views.decorators.http import require_safe

from agents.labels import agent_entitlement_ids
from agents.visibility import labellable_agents
from identity.access import is_admin
from identity.request import principal_for_request, settings_row_for

REACH_EVERYONE = "Everyone on this box"
REACH_GIVEN = "The people it is given to"


@require_safe
def agents_admin_list(request):
    """GET `/settings/agents/` -- every agent, its audience, its owner."""
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    if not is_admin(principal, settings_row=settings_row):
        raise PermissionDenied
    # ONE BATCH READ for every row's labels -- the discipline
    # `/chat/access/` documents -- so twenty-five agents cost one query
    # here, not twenty-five.
    labels = agent_entitlement_ids()
    rows = [{
        "agent": agent,
        "reach": REACH_EVERYONE if agent.box_wide else REACH_GIVEN,
        "label_count": len(labels.get(agent.pk, frozenset())),
    } for agent in labellable_agents()]
    return render(request, "chat/agents_admin.html", {
        "rows": rows,
        "next_url": request.get_full_path(),
    })
```

`labellable_agents()` returns a **list**, so the row build costs no query per agent. The page shows the **owner columns** (`agent.owner_kind`/`owner_key`) — administrator-only data, on an administrator-only route, which is the render-vs-gate answer here: the whole body is admin-only and the gate is the route class.

Create `agents/chat/agent_admin_urls.py`:

```python
"""The agent library's one route, mounted at `/settings/agents/` by
`config/urls.py`.

ITS OWN URLconf rather than one more entry in `agents/chat/urls.py`,
EXACTLY the `agents/chat/assistant_urls.py` precedent and for that
module's own stated reason: this is not a `/chat/` route, it is a
settings-area surface whose view happens to read `agents/` rows.
Mounting it beside `/settings/` is what makes the URL an operator sees
match the page they are on. The composition root is the one module that
already imports every column.
"""
from django.urls import path

from agents.chat.views import agents_admin_list

urlpatterns = [
    path("", agents_admin_list, name="settings-agents"),
]
```

- [ ] **Step 4: Register it in the four places**

`agents/chat/views/__init__.py`: `from agents.chat.views.agents_admin import agents_admin_list`, plus `__all__`.

`config/urls.py`, beside the assistant mount:

```python
    # The agent library (chat cluster, feature B). Mounted HERE rather
    # than under `/chat/`, the same call the settings assistant's own
    # three routes record: a settings-area surface whose view happens to
    # read `agents/` rows belongs beside `/settings/`.
    path("settings/agents/", include("agents.chat.agent_admin_urls")),
```

`foundation/templates/_settings.html` — **the place the `SETTINGS_GROUPS` entry does not cover.** That shell's sidebar is hand-written `<a>` elements, each with its own `side_current_*` block; it is **not** a walk over `SETTINGS_GROUPS`. Registering the `Entry` alone makes the page swept by `foundation/tests/test_shell.py` — which *does* walk the table and GETs every `entry.url_name` — while leaving it absent from the sidebar an operator actually clicks. Add the link to the **Setup** group, in the table's own order, beside Chat and Job execution:

```html
      <a href="{% url 'settings-agents' %}{{ assistant.open_query }}"
         class="{% block side_current_agent_library %}{% endblock %}">Agent library</a>
```

`foundation/settings_area.py::SETTINGS_GROUPS`, in the **Setup** group beside Models / Library / Chat / Job execution:

```python
        # The agent library (chat cluster, feature B): every agent on the
        # box, its audience and its owner, linking to the one edit route
        # `/chat/agents/` also mounts. "Agent library", NOT "Agents":
        # the Access group below already carries "Agent access"
        # (`chat-agent-entitlements`) for a different job, and two
        # entries sharing one noun is a nav an operator has to learn
        # rather than read.
        Entry("Agent library", "settings-agents", ADMIN),
```

`identity/routes.py::ROUTE_RULES`, beside `settings-assistant-*`:

```python
    # S: a page whose whole body is administrator-only listing -- the
    # call `chat-settings` and `chat-tool-entitlements` already record.
    "settings-agents": "S",
```

`identity/tests/test_route_matrix.py::_DRIVERS`:

```python
    "settings-agents": lambda w: ("get", reverse("settings-agents"), {}),
```

- [ ] **Step 5: Write the template**

Create `agents/chat/templates/chat/agents_admin.html`:

```html
{% extends "_settings.html" %}
{% comment %}
THE AGENT LIBRARY (`/settings/agents/`, class S).

EXTENDS `_settings.html`, NOT `chat/base.html` -- the same call
`chat/settings.html` records for itself: this is a settings-area page
and wants the settings shell's sidebar and two-column layout, not the
chat rail. Being outside `chat/base.html`'s frame also puts it outside
the chat CSS gate's scan; the gate that covers it is the same file's
`test_no_settings_page_retypes_a_rule_settings_html_already_owns`, so
nothing here re-types a rule `_settings.html` already owns -- this page
declares no style block at all.

EVERY ROW LINKS TO THE SAME EDIT ROUTE `/chat/agents/` links to. One
edit route, two list pages; there is no second editor and no second
form.

THE OWNER COLUMN IS ADMINISTRATOR-ONLY DATA on an administrator-only
route, which is where render-vs-gate lands for this page: the whole body
is admin-only and the route class is the gate, so there is no non-admin
render in which any of it is built.
{% endcomment %}
{% block title %}Agent library — farabunker{% endblock %}
{% block side_current_agent_library %}current{% endblock %}
{% block settings_content %}
<h1>Agent library</h1>
{% include "_messages.html" %}
<table>
  <thead>
    <tr><th>Name</th><th>Key</th><th>Reach</th><th>Owner</th><th>Restrictions</th></tr>
  </thead>
  <tbody>
  {% for row in rows %}
    <tr>
      <td><a href="{% url 'chat-agent-edit' row.agent.pk %}?next={{ next_url|urlencode }}">{{ row.agent.name }}</a></td>
      <td><code>{{ row.agent.slug }}</code></td>
      <td>{{ row.reach }}</td>
      <td>{{ row.agent.owner_kind }}:{{ row.agent.owner_key }}</td>
      <td>{{ row.label_count }}</td>
    </tr>
  {% empty %}
    <tr><td colspan="5" class="muted">No agents on this install yet.</td></tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

Confirm the `settings_content` block name and the flash-fragment include against `agents/chat/templates/chat/settings.html`, which is the nearest precedent for a chat-column page in the settings area, and use whatever it really uses.

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -q agents/chat/tests/test_settings_agents.py`
Expected: PASS.
Run: `.venv/bin/pytest -q foundation/tests/test_shell.py identity/tests/test_route_matrix.py agents/chat`
Expected: PASS — the settings drift test picks the new entry up on its own.

- [ ] **Step 7: Document the pattern**

In `docs/EXTENDING.md`, add a short section — feature B is the **second** instance of this pattern, so it is worth writing down:

```markdown
## Adding a settings-area page

A page that lives under `/settings/` but whose view reads another column's rows
registers in four places, never one:

1. **A link in `foundation/templates/_settings.html`'s sidebar**, with its own
   `side_current_*` block that the page overrides. That shell's nav is hand-written,
   **not** a walk over `SETTINGS_GROUPS` — registering the `Entry` alone gets the page
   swept by the drift tests while leaving it unreachable by clicking.
2. **Its own URLconf module** in the owning column — `agents/chat/assistant_urls.py`
   and `agents/chat/agent_admin_urls.py` are the two instances. It is not an entry in
   that column's own `/chat/` URLconf: the URL an operator sees should match the area
   they are in.
3. **A mount in `config/urls.py`**, the one module that already imports every column,
   so the composition stays in the composition root.
4. **An `Entry` in `foundation/settings_area.py::SETTINGS_GROUPS`**, with its gate
   (and its `feature` token, if the route is only mounted under a feature flag).

Then classify the route name in `identity/routes.py::ROUTE_RULES` and give it a
driver in `identity/tests/test_route_matrix.py`. **Its never-500 proof needs its own
test module**: `agents/chat/tests/test_never_500.py` derives its sweep from
`agents.chat.urls.urlpatterns` and cannot see a route mounted from anywhere else.
```

- [ ] **Step 8: Run the four runs and both posture sweeps**

As in Task 5, Step 9. Also run `.venv/bin/python manage.py check` and confirm it reports no issues.

- [ ] **Step 9: Commit**

```
git add agents/chat/views/agents_admin.py agents/chat/agent_admin_urls.py agents/chat/templates/chat/agents_admin.html agents/chat/views/__init__.py config/urls.py foundation/settings_area.py foundation/templates/_settings.html identity/routes.py identity/tests/test_route_matrix.py agents/chat/tests/test_settings_agents.py docs/EXTENDING.md
git commit -m "feat(chat): /settings/agents/ — the agent library, mounted from the composition root"
```

---

## Phase C — edit a past prompt

Feature C depends on nothing in A or B. Every task here runs the four runs **plus the two posture sweeps**: `may_edit_turn` is a visibility predicate and its share answers are the heart of the feature.

**The fork is settled.** §16 records the owner's ruling on flag 3: **BRANCH**. Editing an earlier prompt creates a **new conversation** holding everything before the edited message, with the edited message as its newest turn; the original is untouched. REWIND is not a variant of this design — it would need a turn-deletion path, a taint-removal design (a deferred item with an open design question), an attachment cleanup rule and a rule for shared conversations. No implementer reopens this.

### Task 11: The provenance columns

**Files:**
- Modify: `agents/models.py` (`Conversation`)
- Create: `agents/migrations/0013_conversation_branch.py`
- Test: `agents/tests/test_branch.py`
- Modify: `agents/README.md`

**Interfaces:**
- Produces: `Conversation.branched_from` — self-FK, `on_delete=SET_NULL`, `null=True`, `blank=True`, `related_name="branches"`; `Conversation.branched_at_index` — `PositiveIntegerField(null=True, blank=True)`, the **parent's** index of the edited turn. Task 12 writes them; Task 14 renders them.

- [ ] **Step 1: Write the failing test**

Create `agents/tests/test_branch.py`:

```python
"""Edit a past prompt: the provenance columns, the gate, and what a
branch is made of."""
from __future__ import annotations

import pytest

from agents.models import Conversation, Turn
from agents.tests._helpers import make_agent, make_conversation, make_turn

pytestmark = pytest.mark.django_db


class TestTheProvenanceColumns:
    def test_a_plain_conversation_carries_neither(self):
        conversation = make_conversation(agent=make_agent(slug="plain"))
        assert conversation.branched_from_id is None
        assert conversation.branched_at_index is None

    def test_a_branch_names_its_parent_and_the_index_it_left_from(self):
        agent = make_agent(slug="parented")
        parent = make_conversation(agent=agent)
        child = make_conversation(agent=agent, branched_from=parent,
                                  branched_at_index=3)
        assert child.branched_from_id == parent.id
        assert child.branched_at_index == 3
        assert list(parent.branches.all()) == [child]

    def test_deleting_the_parent_leaves_the_branch_standing(self):
        """SET_NULL, not CASCADE: a branch is a conversation in its own
        right, and losing the row it came from is not a reason to lose
        it. The index survives so the line can still say WHERE it left
        from even when it can no longer say what from."""
        agent = make_agent(slug="orphaned")
        parent = make_conversation(agent=agent)
        child = make_conversation(agent=agent, branched_from=parent,
                                  branched_at_index=2)
        parent.delete()
        child.refresh_from_db()
        assert Conversation.objects.filter(pk=child.pk).exists()
        assert child.branched_from_id is None
        assert child.branched_at_index == 2

    def test_a_branch_of_a_branch_reports_its_immediate_parent(self):
        agent = make_agent(slug="chained")
        first = make_conversation(agent=agent)
        second = make_conversation(agent=agent, branched_from=first,
                                   branched_at_index=1)
        third = make_conversation(agent=agent, branched_from=second,
                                  branched_at_index=1)
        assert third.branched_from_id == second.id
```

`make_conversation` splats its overrides into `Conversation.objects.create`, so `branched_from=`/`branched_at_index=` work without touching the helper.

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest -q agents/tests/test_branch.py`
Expected: FAIL — `TypeError: 'branched_from' is an invalid keyword argument for Conversation`.

- [ ] **Step 3: Add the fields**

In `agents/models.py::Conversation`, after `consolidated_at`:

```python
    # PROVENANCE (chat cluster, feature C). Null for every conversation
    # that was started rather than branched.
    #
    # TWO COLUMNS RATHER THAN A TITLE SUFFIX (spec decision 16, owner
    # ruling flag 4). Two rows with the same name and no stated
    # relationship is exactly the sidebar an operator cannot explain to
    # themselves a week later, and a title suffix is a string nobody can
    # query. A relationship nobody can query is not a relationship.
    #
    # `SET_NULL`, NOT `CASCADE`: a branch is a conversation in its own
    # right, and losing the row it came from is not a reason to lose it.
    # `branched_at_index` survives that deletion deliberately, so the
    # provenance line can still say WHERE this thread left off even when
    # it can no longer say what it left.
    branched_from = models.ForeignKey("self", null=True, blank=True,
                                      on_delete=models.SET_NULL,
                                      related_name="branches")
    # THE PARENT'S index of the edited turn -- not this conversation's.
    # A branch renumbers its copied turns from zero, so an index read
    # against the branch would name the wrong message.
    branched_at_index = models.PositiveIntegerField(null=True, blank=True)
```

- [ ] **Step 4: Write the migration**

Create `agents/migrations/0013_conversation_branch.py`:

```python
# Branch provenance (chat cluster, feature C). Two nullable columns,
# additive, nothing back-filled: every existing conversation was started
# rather than branched, and null is the honest value for it.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0012_agent_box_wide"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="branched_from",
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="branches", to="agents.conversation"),
        ),
        migrations.AddField(
            model_name="conversation",
            name="branched_at_index",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
```

**One thing this number assumes.** `0013` and its `("agents", "0012_agent_box_wide")` dependency assume Phase B has landed. The spec's front matter says feature C "depends on nothing in A or B", and that is true **at the code level** — `branch_conversation` reads no `box_wide` and no meter — but it is not true of the migration graph, and "independently mergeable" is the kind of sentence an owner acts on. Landing all three phases in order on this one branch, which is the plan, makes it moot. If Phase C is ever split out and merged first, renumber it `0012_conversation_branch` with a `("agents", "0011_turn_author")` dependency and let Phase B take `0013`.

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -q agents/tests/test_branch.py`
Expected: PASS.
Run: `.venv/bin/python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`.

- [ ] **Step 6: Document it**

In `agents/README.md`, beside the `Conversation` notes:

```markdown
`branched_from` / `branched_at_index` record that this conversation was made by
editing a message in another one, and which message. Two nullable columns rather
than a title convention, because "where did this thread come from" should be a
fact you can query rather than a string you can only read. `SET_NULL`: deleting
the parent leaves the branch readable, with a provenance line that no longer
links.
```

- [ ] **Step 7: Run the four runs and both posture sweeps**

As in Task 5, Step 9.

- [ ] **Step 8: Commit**

```
git add agents/models.py agents/migrations/0013_conversation_branch.py agents/tests/test_branch.py agents/README.md
git commit -m "feat(agents): branch provenance columns on Conversation"
```

---

### Task 12: `may_edit_turn` and `branch_conversation`

**Files:**
- Modify: `agents/visibility.py` (`may_edit_turn`, `branch_conversation`, `duplicate_conversation`)
- Test: `agents/tests/test_branch.py`
- Modify: `agents/README.md`

**Interfaces:**
- Consumes: `may_manage_conversation`; `_TERMINAL_TURN_STATES`; `identity.access.owner_fields`; `agents.models.ConversationTaint`.
- Produces:
  - `may_edit_turn(principal, conversation, turn, *, settings_row=None) -> bool`
  - `branch_conversation(principal, conversation, turn, *, title: str)` → the new `Conversation`, or `None`
- Consumed by: Task 13 (`turn_edit`, the card predicate).

**`branch_conversation` does steps 1–2 only, and the import direction is why** (spec review M7). The dependency runs `agents/chat` → `agents/visibility`, one way: `agents/chat/service.py` imports `chat_surface_conversations`, and `agents/visibility.py`'s own import list is `agents.models`, `agents.shares` and `identity.*`, with nothing from `agents/chat`. Calling `start_turn` from here inverts that into a cycle, and a function-local import would work mechanically only by putting the queue, preflight, attachment staging and the messages framework behind a module the whole column treats as a leaf. A function that also redirects is a view. So this one knows nothing about HTTP, the queue, or where the reader goes next; `turn_edit` does steps 3–4.

**`duplicate_conversation` starts copying `author_id` in this same commit.** `agents/runtime/prompt.py::_is_foreign_user_turn` reads `author` to fence another person's words in a replay, so a copy that drops it changes how the model is shown a shared conversation's history. That is a pre-existing gap, not a difference of opinion — and the commit message **names the re-pin**, per the house rule.

- [ ] **Step 1: Write the failing test**

Append to `agents/tests/test_branch.py`:

```python
class TestMayEditTurn:
    def _own_thread(self, owner):
        conversation = make_conversation(agent=make_agent(slug="editable-thread"),
                                         **owner_fields(user_principal(owner)))
        turn = make_turn(conversation=conversation, role=Turn.Role.USER, text="first",
                         state=Turn.State.DONE)
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="answer",
                  state=Turn.State.DONE)
        return conversation, turn

    def test_the_owner_may_edit_their_own_finished_user_turn(self):
        from agents.visibility import may_edit_turn

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turn = self._own_thread(owner)
            assert may_edit_turn(user_principal(owner), conversation, turn) is True

    def test_an_assistant_turn_is_not_editable(self):
        from agents.visibility import may_edit_turn

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turn = self._own_thread(owner)
            assistant = conversation.turns.filter(role=Turn.Role.ASSISTANT).get()
            assert may_edit_turn(user_principal(owner), conversation, assistant) is False

    def test_a_delegates_turn_is_not_editable(self):
        from agents.visibility import may_edit_turn

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turn = self._own_thread(owner)
            deep = make_turn(conversation=conversation, role=Turn.Role.USER, text="d",
                             depth=1, state=Turn.State.DONE)
            assert may_edit_turn(user_principal(owner), conversation, deep) is False

    def test_a_turn_from_another_conversation_is_not_editable(self):
        from agents.visibility import may_edit_turn

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turn = self._own_thread(owner)
            elsewhere = make_turn(role=Turn.Role.USER, text="x", state=Turn.State.DONE)
            assert may_edit_turn(user_principal(owner), conversation,
                                 elsewhere) is False

    def test_any_in_flight_turn_in_the_conversation_blocks_every_edit(self):
        """Editing while an answer is in flight would branch from a
        conversation whose shape is still changing, and the job would
        write its answer back to the ORIGINAL's row anyway."""
        from agents.visibility import may_edit_turn

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turn = self._own_thread(owner)
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                      state=Turn.State.QUEUED)
            assert may_edit_turn(user_principal(owner), conversation, turn) is False


class TestTheShareAnswersArePinnedExplicitly:
    """Spec review M2. Gated on `may_manage_conversation`, where an
    earlier draft gated on the strictly wider `may_post_to`. Under that
    gate a share recipient could, with one click on somebody else's
    message, mint a NEW conversation THEY OWN holding the original's
    full terminal history -- stamped with their own `owner_fields`,
    surviving revocation of the share that permitted it, and neither
    visible nor deletable by the original owner. A branch IS a copy, so
    it answers to the copy predicate, and `duplicate_conversation`
    refuses exactly this today."""

    def _shared_thread(self, owner, recipient, *, level):
        from agents.models import Share
        from agents.visibility import share_conversation

        conversation = make_conversation(agent=make_agent(slug="shared-thread"),
                                         **owner_fields(user_principal(owner)))
        turn = make_turn(conversation=conversation, role=Turn.Role.USER,
                         text="theirs", state=Turn.State.DONE)
        share_conversation(user_principal(owner), conversation, user=recipient,
                           level=level)
        return conversation, turn

    def test_a_view_share_recipient_may_not(self):
        from agents.models import Share
        from agents.visibility import may_edit_turn

        owner, recipient = make_user(), make_user(username="recipient")
        with posture(POSTURE_ENTERPRISE):
            conversation, turn = self._shared_thread(owner, recipient,
                                                     level=Share.Level.VIEW)
            assert may_edit_turn(user_principal(recipient), conversation,
                                 turn) is False

    def test_a_use_share_recipient_may_not_either(self):
        from agents.models import Share
        from agents.visibility import may_edit_turn

        owner, recipient = make_user(), make_user(username="recipient")
        with posture(POSTURE_ENTERPRISE):
            conversation, turn = self._shared_thread(owner, recipient,
                                                     level=Share.Level.USE)
            assert may_edit_turn(user_principal(recipient), conversation,
                                 turn) is False

    def test_a_workstream_share_recipient_may_not(self):
        from agents.models import Share
        from agents.tests._helpers import _workstream
        from agents.visibility import may_edit_turn, share_workstream

        owner, recipient = make_user(), make_user(username="recipient")
        with posture(POSTURE_ENTERPRISE):
            stream = _workstream(**owner_fields(user_principal(owner)))
            conversation = make_conversation(agent=make_agent(slug="stream-thread"),
                                             workstream=stream,
                                             **owner_fields(user_principal(owner)))
            turn = make_turn(conversation=conversation, role=Turn.Role.USER, text="t",
                             state=Turn.State.DONE)
            share_workstream(user_principal(owner), stream, user=recipient,
                             level=Share.Level.USE)
            assert may_edit_turn(user_principal(recipient), conversation,
                                 turn) is False

    def test_the_streams_own_owner_may_because_they_own_it(self):
        from agents.tests._helpers import _workstream
        from agents.visibility import may_edit_turn

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            stream = _workstream(**owner_fields(user_principal(owner)))
            conversation = make_conversation(agent=make_agent(slug="own-stream"),
                                             workstream=stream,
                                             **owner_fields(user_principal(owner)))
            turn = make_turn(conversation=conversation, role=Turn.Role.USER, text="t",
                             state=Turn.State.DONE)
            assert may_edit_turn(user_principal(owner), conversation, turn) is True

    def test_an_administrator_with_content_access_may(self):
        from agents.visibility import may_edit_turn

        owner, admin = make_user(), make_admin()
        # A KEYWORD ON THE POSTURE HELPER, not a context manager of its
        # own: `identity/testing.py::posture(name, *,
        # admin_sees_content=None, library_posture=None)`. There is no
        # `identity.testing.admin_sees_content` to import, and importing
        # one raises at COLLECTION, which takes the whole module down.
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            conversation = make_conversation(agent=make_agent(slug="admin-reach"),
                                             **owner_fields(user_principal(owner)))
            turn = make_turn(conversation=conversation, role=Turn.Role.USER, text="t",
                             state=Turn.State.DONE)
            assert may_edit_turn(user_principal(admin), conversation, turn) is True


class TestBranchConversation:
    def _thread_of(self, owner, texts):
        conversation = make_conversation(agent=make_agent(slug="branchable"),
                                         **owner_fields(user_principal(owner)))
        turns = [make_turn(conversation=conversation, role=Turn.Role.USER, text=text,
                           state=Turn.State.DONE) for text in texts]
        return conversation, turns

    def test_it_copies_turns_strictly_BEFORE_the_edited_index(self):
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b", "c", "d"])
            branch = branch_conversation(user_principal(owner), conversation, turns[2],
                                         title="Branch")
            texts = list(branch.turns.order_by("index").values_list("text", flat=True))
        assert texts == ["a", "b"]

    def test_the_indexes_are_renumbered_from_zero(self):
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b", "c"])
            branch = branch_conversation(user_principal(owner), conversation, turns[2],
                                         title="Branch")
            indexes = list(branch.turns.order_by("index").values_list("index",
                                                                     flat=True))
        assert indexes == [0, 1]

    def test_the_branch_is_the_BRANCHERS_own_and_records_where_it_left(self):
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b"])
            branch = branch_conversation(user_principal(owner), conversation, turns[1],
                                         title="Branch")
        assert branch.owner_kind == "user"
        assert branch.branched_from_id == conversation.id
        assert branch.branched_at_index == turns[1].index

    def test_it_carries_the_agent_and_the_workstream(self):
        """Ruling D: a branch stays in the stream, or one click would
        launder labelled material out of every gate."""
        from agents.tests._helpers import _workstream
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            stream = _workstream(**owner_fields(user_principal(owner)))
            agent = make_agent(slug="streamed")
            conversation = make_conversation(agent=agent, workstream=stream,
                                             **owner_fields(user_principal(owner)))
            first = make_turn(conversation=conversation, role=Turn.Role.USER, text="a",
                              state=Turn.State.DONE)
            second = make_turn(conversation=conversation, role=Turn.Role.USER, text="b",
                               state=Turn.State.DONE)
            branch = branch_conversation(user_principal(owner), conversation, second,
                                         title="Branch")
        assert branch.workstream_id == stream.pk
        assert branch.agent_id == agent.pk

    def test_it_preserves_author_id_and_drops_invocation_and_queue_job_id(self):
        from agents.models import ToolInvocation
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation = make_conversation(agent=make_agent(slug="authored"),
                                             **owner_fields(user_principal(owner)))
            first = make_turn(conversation=conversation, role=Turn.Role.USER, text="a",
                              state=Turn.State.DONE, author=owner, queue_job_id=11)
            second = make_turn(conversation=conversation, role=Turn.Role.USER, text="b",
                               state=Turn.State.DONE)
            branch = branch_conversation(user_principal(owner), conversation, second,
                                         title="Branch")
            copied = branch.turns.order_by("index").first()
        assert copied.author_id == owner.pk
        assert copied.queue_job_id is None
        assert copied.invocation_id is None

    def test_non_terminal_turns_are_not_copied(self):
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a"])
            mid = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                            state=Turn.State.CANCELLED)
            last = make_turn(conversation=conversation, role=Turn.Role.USER, text="z",
                             state=Turn.State.DONE)
            # A queued turn would block the gate, so this asserts the
            # copier's own filter directly rather than through the view.
            branch = branch_conversation(user_principal(owner), conversation, last,
                                         title="Branch")
            texts = list(branch.turns.order_by("index").values_list("text", flat=True))
        assert texts == ["a", ""]

    def test_it_copies_every_taint_row_including_one_after_the_branch_point(self):
        """Over-tainting is safe; under-tainting is a leak. `first_turn`
        is a plain integer precisely so it can name the turn that really
        caused it, in the conversation where it really happened."""
        from agents.models import ConversationTaint
        from agents.visibility import branch_conversation

        owner = make_user()
        entitlement = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b", "c"])
            ConversationTaint.objects.create(conversation=conversation,
                                             entitlement=entitlement,
                                             first_turn=turns[2].index)
            branch = branch_conversation(user_principal(owner), conversation, turns[1],
                                         title="Branch")
        assert branch.taint_tags.count() == 1
        assert branch.taint_tags.get().first_turn == turns[2].index

    def test_it_writes_no_workstream_taint(self):
        """The branch stays in the parent's stream, and the stream's
        materialised union already holds every one of the parent's tags
        -- it was unioned upward when each was stamped. Nothing new
        enters the stream."""
        from agents.models import ConversationTaint, WorkstreamTaint
        from agents.tests._helpers import _workstream
        from agents.visibility import branch_conversation

        owner = make_user()
        entitlement = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            stream = _workstream(**owner_fields(user_principal(owner)))
            conversation = make_conversation(agent=make_agent(slug="tainted-stream"),
                                             workstream=stream,
                                             **owner_fields(user_principal(owner)))
            first = make_turn(conversation=conversation, role=Turn.Role.USER, text="a",
                              state=Turn.State.DONE)
            second = make_turn(conversation=conversation, role=Turn.Role.USER, text="b",
                               state=Turn.State.DONE)
            ConversationTaint.objects.create(conversation=conversation,
                                             entitlement=entitlement,
                                             first_turn=first.index)
            before = WorkstreamTaint.objects.count()
            branch_conversation(user_principal(owner), conversation, second,
                                title="Branch")
        assert WorkstreamTaint.objects.count() == before

    def test_it_copies_no_document_attachment_rows(self):
        """There is no copier seam -- the four registered attachment
        seams are provider, cleanup, uploader and detacher -- and
        inventing a fifth for v1 is not earned (owner ruling, flag 6)."""
        from tools.rag.models import DocumentAttachment
        from agents.tests._helpers import make_document
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b"])
            DocumentAttachment.objects.create(document=make_document(),
                                              conversation_id=conversation.id,
                                              turn_id=turns[0].pk)
            branch = branch_conversation(user_principal(owner), conversation, turns[1],
                                         title="Branch")
        assert not DocumentAttachment.objects.filter(
            conversation_id=branch.id).exists()

    def test_it_carries_no_shares_and_is_neither_pinned_nor_archived(self):
        from agents.models import Share
        from agents.visibility import branch_conversation, share_conversation

        owner, recipient = make_user(), make_user(username="recipient")
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b"])
            share_conversation(user_principal(owner), conversation, user=recipient,
                               level=Share.Level.VIEW)
            branch = branch_conversation(user_principal(owner), conversation, turns[1],
                                         title="Branch")
        assert branch.pinned_at is None
        assert branch.archived_at is None
        assert not Share.objects.filter(target_type=Share.Target.CONVERSATION,
                                        target_key=str(branch.id)).exists()

    def test_the_original_is_left_byte_identical(self):
        from agents.visibility import branch_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b", "c"])
            before = list(conversation.turns.order_by("index")
                          .values_list("index", "text", "state"))
            branch_conversation(user_principal(owner), conversation, turns[1],
                                title="Branch")
            conversation.refresh_from_db()
            after = list(conversation.turns.order_by("index")
                         .values_list("index", "text", "state"))
        assert after == before

    def test_a_principal_who_may_not_gets_None_and_writes_nothing(self):
        from agents.models import Conversation
        from agents.visibility import branch_conversation

        owner, stranger = make_user(), make_user(username="stranger")
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = self._thread_of(owner, ["a", "b"])
            before = Conversation.objects.count()
            result = branch_conversation(user_principal(stranger), conversation,
                                         turns[1], title="Branch")
        assert result is None
        assert Conversation.objects.count() == before

    def test_it_is_callable_with_no_HTTP_anywhere_in_reach(self):
        """Spec review M7. The import-law gate covers the general rule;
        this pins the specific direction the split exists to preserve:
        `agents/visibility.py` imports NOTHING from `agents/chat`, so
        this function cannot call `start_turn` and is not a view."""
        import inspect

        from agents import visibility

        source = inspect.getsource(visibility)
        assert "agents.chat" not in source
        assert "start_turn" not in source
        assert "from django.shortcuts import" not in source


class TestDuplicateConversationNowCopiesTheAuthor:
    """A DELIBERATE RE-PIN, named in the commit message.
    `agents/runtime/prompt.py::_is_foreign_user_turn` reads `author` to
    fence another person's words in a replay, so a copy that dropped it
    changed how the model was shown a shared conversation's history."""

    def test_the_author_survives_a_duplicate(self):
        from agents.visibility import duplicate_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation = make_conversation(agent=make_agent(slug="dup-author"),
                                             **owner_fields(user_principal(owner)))
            make_turn(conversation=conversation, role=Turn.Role.USER, text="a",
                      state=Turn.State.DONE, author=owner)
            copy = duplicate_conversation(user_principal(owner), conversation,
                                          title="Copy")
        assert copy.turns.get().author_id == owner.pk
```

Add this module's imports for `make_user`, `make_admin`, `make_entitlement`, `posture`, `user_principal`, `owner_fields` and `POSTURE_ENTERPRISE` in the same shape `agents/tests/test_workstream_sharing.py` already uses.

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest -q agents/tests/test_branch.py`
Expected: FAIL — `ImportError: cannot import name 'may_edit_turn' from 'agents.visibility'`.

- [ ] **Step 3: Write the predicate and the copier**

In `agents/visibility.py`, after `duplicate_conversation`:

```python
def may_edit_turn(principal, conversation, turn, *, settings_row=None) -> bool:
    """Whether `principal` may edit `turn` and carry on from there.

    MANAGE, NOT POST, AND THE DIFFERENCE IS THE WHOLE OF IT (spec review
    M2). `may_post_to` is strictly wider -- owner, `sees_all_content`, a
    `use`-level conversation share, ANY workstream-share recipient (a
    workstream share is `use`-by-construction), and the stream's owner.
    Under that gate a share recipient could, with one click on somebody
    else's message, mint a NEW CONVERSATION THEY OWN holding the
    original's full terminal history: stamped with their own
    `owner_fields`, surviving revocation of the share that permitted it,
    and neither visible, manageable nor deletable by the original owner.
    A BRANCH IS A COPY, so it answers to the copy predicate --
    `duplicate_conversation` refuses exactly that today, on
    `may_manage_conversation`, whose own docstring is explicit that
    "shared to somebody is not manageable by them".

    Since `may_manage_conversation` is a SUBSET of `may_post_to`, the
    right to write the new turn comes along with it and needs no second
    check.

    THE COST IS REAL AND IS THE OWNER'S CALL (flag 7, ruled): a
    `use`-share or workstream-share recipient cannot edit and branch in
    a conversation shared with them, even on their own message. What
    they are refused is a COPY, which costs them nothing they had.

    NO TURN IN THIS CONVERSATION MAY BE IN FLIGHT -- one flat
    `.exists()`. Editing while an answer is running would branch from a
    conversation whose shape is still changing, and the job would write
    its answer back to the ORIGINAL's row anyway.
    """
    if turn.conversation_id != conversation.id:
        return False
    if turn.role != Turn.Role.USER or turn.depth != 0 or turn.state != Turn.State.DONE:
        return False
    if not may_manage_conversation(principal, conversation, settings_row=settings_row):
        return False
    return not Turn.objects.filter(conversation=conversation).exclude(
        state__in=_TERMINAL_TURN_STATES).exists()


def branch_conversation(principal, conversation, turn, *, title: str):
    """A new conversation holding everything BEFORE `turn`, owned by
    `principal` -- or `None` if they may not.

    A SIBLING OF `duplicate_conversation`, NOT A PARAMETER ON IT (spec
    decision 14). The two answer different questions -- copy the whole
    thing / carry on from here -- and have different rules about the
    last turn and about the index bound. They share the same predicate
    and the same constants, which is an argument FOR the sibling shape
    rather than against it: one gate, two operations, neither reaching
    into the other's body.

    STEPS 1-2 ONLY. This function knows nothing about HTTP, the queue,
    or where the reader goes next: `agents/chat` imports
    `agents/visibility`, ONE WAY, and a function that also redirected
    would be a view. `agents.chat.views.turns.turn_edit` calls
    `start_turn` and redirects -- steps 3-4 -- after this returns.

    THE ADMINISTRATOR'S-COPY CONSEQUENCE `duplicate_conversation`
    RECORDS APPLIES HERE FOR THE SAME REASON, now that the gate is the
    same predicate: an administrator branching under
    `admin_sees_content` makes a copy they own BY OWNERSHIP, and it
    survives the content setting being switched back off.

    WHAT IS CARRIED, and each is `duplicate_conversation`'s own rule:
    `agent` and `workstream` (ruling D -- a branch stays in the stream,
    or one click launders labelled material out of every gate),
    `**owner_fields(principal)` (the brancher owns it), the same
    `title`, and the two provenance columns.

    WHAT IS COPIED PER TURN: `role`, `text`, `tool_call`, `data`,
    `artifacts`, `depth`, `state`, `error` -- AND `author_id`, which
    `duplicate_conversation` did not copy until this change and now
    does, in the same commit, because `agents.runtime.prompt.
    _is_foreign_user_turn` reads it to fence another person's words in a
    replay.

    WHAT IS NOT: `invocation` (an audit row belongs to exactly ONE
    conversation and is never re-pointed or re-invented),
    `queue_job_id` (a copy was produced by nothing), any non-terminal
    turn, any `DocumentAttachment` row (there is no copier seam, and the
    edit form says so before the button), any share, and any pin or
    archive state.

    THE TAINT IS COPIED VERBATIM, INCLUDING TAGS WHOSE `first_turn` LIES
    AFTER THE BRANCH POINT. Over-tainting is safe; under-tainting is a
    leak, and the field is a plain integer precisely so it can name the
    turn that really caused it, in the conversation where it really
    happened. No `WorkstreamTaint` is written: the branch stays in the
    parent's stream, whose materialised union already holds every one of
    the parent's tags.
    """
    if not may_edit_turn(principal, conversation, turn):
        return None
    with transaction.atomic():
        branch = Conversation.objects.create(
            agent=conversation.agent, title=title,
            workstream=conversation.workstream,      # THE BRANCH STAYS IN THE STREAM
            branched_from=conversation, branched_at_index=turn.index,
            **owner_fields(principal),
        )
        Turn.objects.bulk_create([
            Turn(
                conversation=branch, index=index, role=row.role, text=row.text,
                tool_call=row.tool_call, data=row.data, artifacts=row.artifacts,
                depth=row.depth, state=row.state, error=row.error,
                author_id=row.author_id,
            )
            for index, row in enumerate(
                Turn.objects.filter(conversation=conversation, index__lt=turn.index,
                                    state__in=_TERMINAL_TURN_STATES).order_by("index")
            )
        ])
        ConversationTaint.objects.bulk_create([
            ConversationTaint(conversation=branch, entitlement_id=tag.entitlement_id,
                              first_turn=tag.first_turn)
            for tag in conversation.taint_tags.all()
        ])
    return branch
```

- [ ] **Step 4: Re-pin `duplicate_conversation`**

Add `author_id=turn.author_id,` to the `Turn(...)` construction inside `duplicate_conversation`'s `bulk_create`, and add this paragraph to its docstring:

```python
    `author_id` IS COPIED, AND THAT CHANGED (chat cluster, feature C).
    It used to be dropped, silently -- a pre-existing gap rather than a
    decision. `agents/runtime/prompt.py::_is_foreign_user_turn` reads
    `author` to fence another person's words in a replay, so a copy that
    dropped it changed how the model was shown a shared conversation's
    history. `branch_conversation` below copies it too, and the two do
    it the same way on purpose.
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -q agents/tests/test_branch.py`
Expected: PASS.
Run: `.venv/bin/pytest -q agents foundation/ops`
Expected: PASS — including the existing `duplicate_conversation` tests and the import-law gate.

- [ ] **Step 6: Document both**

In `agents/README.md`, beside `duplicate_conversation`:

```markdown
`branch_conversation` is `duplicate_conversation`'s sibling: same gate
(`may_manage_conversation`, through `may_edit_turn`), same constants, same rules
about what an audit row and an attachment belong to — but bounded by an index
and stamped with provenance. Two operations, one gate, neither reaching into the
other's body. Both copy `author_id`; neither copies `invocation`, `queue_job_id`,
attachments or shares.

**It does steps 1–2 only.** It returns the new conversation and knows nothing
about HTTP, the queue, or where the reader goes next — `agents/chat` imports
`agents/visibility`, one way, and a function that also redirected would be a
view. `agents/chat/views/turns.py::turn_edit` calls `start_turn` and redirects.
```

- [ ] **Step 7: Run the four runs and both posture sweeps**

As in Task 5, Step 9.

- [ ] **Step 8: Commit**

```
git add agents/visibility.py agents/tests/test_branch.py agents/README.md
git commit -m "feat(agents): may_edit_turn and branch_conversation

Deliberately re-pins duplicate_conversation: it now copies Turn.author_id,
which it silently dropped before. prompt.py::_is_foreign_user_turn reads
that column to fence another person's words in a replay, so a copy that
dropped it changed how the model was shown a shared conversation's history."
```

---

### Task 13: `turn_edit` — the disclosure and the view

**Files:**
- Modify: `agents/chat/rendering.py` (`turn_card`, `thread_cards`, `turn_group_cards`)
- Modify: `agents/chat/views/turns.py` (`turn_edit`)
- Modify: `agents/chat/views/thread.py` (the `may_edit` and `may_attach_files` threading)
- Modify: `agents/chat/views/__init__.py`, `agents/chat/urls.py`, `identity/routes.py`
- Modify: `agents/chat/templates/chat/_turn_card.html`, `agents/chat/templates/chat/_attach_files.html` (the optional `attach_id`), `agents/chat/templates/chat/conversation.html` (the `carryConnection` helper, inside the existing script), `agents/chat/templates/chat/base.html`
- Test: `agents/chat/tests/test_turn_edit.py` (new), `agents/chat/tests/test_never_500.py`, `identity/tests/test_route_matrix.py`
- Modify: `agents/chat/README.md`

**Interfaces:**
- Consumes: `agents.visibility.may_edit_turn / branch_conversation / visible_turn` (Task 12); `agents.chat.service.start_turn / conversation_url / visible_conversation_or_404 / composer_attachment_fields / MAX_TURN_CHARS`; `agents.limits.MAX_TURN_CHARS`.
- Produces: the view `turn_edit`; the route name `chat-turn-edit` (class O); the card keys `may_edit` (bool) and `may_attach_files` (bool); `thread_cards(conversation, *, queue_job_id=None, attachments_by_turn=None, may_edit=False, may_attach_files=False)`.

**The edit form's file input is `chat/_attach_files.html` alone, never `chat/_composer.html`** (spec review m8). `_composer.html` ends with `{% if may_attach_files %}{% include "chat/_attach_dragdrop.html" %}{% endif %}` and an unconditional `{% include "chat/_enter_to_send.html" %}` — **two script blocks** — and this disclosure renders once per eligible user turn, so including the composer would multiply the page's script count by the number of editable messages. `_attach_files.html` is script-free. **No script is added**, so the pinned counts of 4 and 3 stand. The staged-file chip stack that `_attach_dragdrop.html` builds is therefore absent here; the plain `<input type="file">` is the whole control, which is the JS-off behaviour every surface already has.

**A polled swap shows exactly what a reload shows, and the `done` tick pays for it.** `_group_html` renders `chat/_turn_block.html` with the context `{"cards": cards}` alone — no principal-derived predicate and none of `thread_context`'s `attach_context` keys — so a first draft of this plan let `turn_group_cards` leave both card keys `False` and accepted that the just-answered bubble would lose its disclosure until reload. That breaks an invariant `agents/chat/views/turns.py` states in **four** separate docstrings (`_done_body`, `_group_html`, `turn_group_cards`, `_attachments_by_turn`), and the cost it was avoiding does not exist on the paths that tick:

- On `queued` and `running`, `may_edit_any_turn` is **provably `False`** — the turn being polled *is* a non-terminal turn in that conversation — so those two bodies compute nothing and the every-two-seconds path is untouched.
- On `done`, **once per finished turn**, the price is `may_manage_conversation` (at most one `_user_row` read, memoised on the settings row the middleware already stashed) plus one `.exists()`, against a body that already runs `get_job`, `_attachments_by_turn` and a full `render_to_string` of the group.
- The attach half costs one `tool_access_for(principal)`: `_attachments_by_turn` already resolves the stream scope through `agents.attachments.attachments_for`, so `may_upload` is nearly in hand on that same body.

So `_done_body` derives the principal and the settings row from `request`, computes both answers, and threads them into `turn_group_cards`. `_queued_body` and `_running_body` pass nothing, and a test pins that they pay nothing.

- [ ] **Step 1: Write the failing test**

Create `agents/chat/tests/test_turn_edit.py`:

```python
"""Edit a past prompt: the disclosure, the gate on the POST, and what
the branch a POST makes actually holds."""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (   # noqa: F401
    fake_turn_queue, make_admin, make_agent, make_conversation, make_turn, make_user,
    posture, sign_in, user_principal,
)
from agents.models import Conversation, Share, Turn
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = pytest.mark.django_db


def _own_thread(owner, *, texts=("first", "second")):
    conversation = make_conversation(agent=make_agent(slug="edit-thread"),
                                     **owner_fields(user_principal(owner)))
    turns = [make_turn(conversation=conversation, role=Turn.Role.USER, text=text,
                       state=Turn.State.DONE) for text in texts]
    return conversation, turns


class TestTheDisclosure:
    def test_an_own_finished_user_turn_offers_it(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "Edit and carry on from here" in body
        assert "Send from here" in body

    def test_it_says_what_will_happen_before_the_button(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "starts a new conversation with everything before this message" in body
        assert "The original stays as it is" in body
        assert "not carried over" in body

    def test_an_assistant_card_never_offers_it(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                      text="answer", state=Turn.State.DONE)
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert body.count("Send from here") == 2

    def test_a_view_share_recipient_is_never_offered_it(self, client):
        from agents.visibility import share_conversation

        owner, recipient = make_user(), make_user(username="recipient")
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            share_conversation(user_principal(owner), conversation, user=recipient,
                               level=Share.Level.VIEW)
            sign_in(client, recipient)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "Send from here" not in body

    def test_it_is_not_offered_while_a_turn_is_running(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                      state=Turn.State.QUEUED)
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "Send from here" not in body

    def test_it_carries_the_plain_attach_input_and_neither_script_fragment(
        self, client
    ):
        """Spec review m8. `chat/_composer.html` carries TWO script
        blocks and this disclosure renders once per eligible turn."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "attach-dragdrop" not in body
        assert "enter-to-send" not in body

    def test_ten_editable_messages_still_render_four_script_tags(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(
                owner, texts=tuple(f"m{i}" for i in range(10)))
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert body.count("<script") == 4

    def test_a_polled_done_swap_shows_exactly_what_a_reload_shows(self, client):
        """THE INVARIANT `agents/chat/views/turns.py` STATES IN FOUR
        DOCSTRINGS, and the reason a first draft of this plan got it
        wrong. `_done_body`: "a poller that swapped in less than that
        would show strictly less than the same thread shows after F5 --
        silently, and only for the operator who waited rather than
        reloading." `_group_html`: the queued, running and done bodies
        "can never render the group differently from one another or from
        a reload." `turn_group_cards`: "exactly what a full page reload
        already shows for this turn." `_attachments_by_turn`: "a poll
        swap's chips are NEVER stale relative to what a reload would
        show." That last one is round 13's own stale-strip lesson, which
        this feature's own meter task invokes correctly one task
        earlier."""
        from agents.tests._helpers import _workstream

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            # IN A STREAM THE OWNER MAY UPLOAD TO, deliberately: the
            # BUTTON alone would pass with `_edit_context` narrowed back
            # to two keys, and the placement chooser is the whole reason
            # `attach_workstream` travels. `chat/_attach_files.html`
            # gates the middle radio AND the remember-this-choice block
            # on it, so a two-key poll body renders two radios where a
            # reload renders three -- which is this test's real subject.
            stream = _workstream(**owner_fields(user_principal(owner)))
            conversation = make_conversation(agent=make_agent(slug="parity-thread"),
                                             workstream=stream,
                                             **owner_fields(user_principal(owner)))
            make_turn(conversation=conversation, role=Turn.Role.USER, text="first",
                      state=Turn.State.DONE)
            assistant = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                                  text="answer", state=Turn.State.DONE, queue_job_id=1)
            sign_in(client, owner)
            polled = client.get(
                reverse("chat-turn-status", args=[assistant.pk])).json()["html"]
            reloaded = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "Send from here" in polled
        assert "Send from here" in reloaded
        assert "This workstream only" in polled
        assert "This workstream only" in reloaded

    def test_the_poller_carries_the_pickers_selection_into_a_swapped_form(self, client):
        """The one value that cannot travel on the wire, carried across
        ON THE PAGE instead: the swapped block's `connection` field is
        rendered empty by the server, and the poller copies the
        COMPOSER's own server-rendered value into it. Asserted on the
        script rather than on a rendered DOM, because there is no
        JavaScript engine in this suite -- the same way every other
        poller behaviour on this surface is pinned."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "function carryConnection(" in body
        assert "carryConnection(refreshed)" in body
        assert 'input[name="connection"]' in body
        # No prose composed, nothing sent, no new tag.
        assert "innerHTML" not in body.split("function carryConnection(")[1][:600]
        assert body.count("<script") == 4

    def test_a_queued_and_a_running_tick_pay_nothing_for_the_predicate(
        self, client, fake_queued_job, fake_running_job
    ):
        """`may_edit_any_turn` is PROVABLY FALSE on those two paths --
        the turn being polled IS a non-terminal turn in that
        conversation -- so `_queued_body` and `_running_body` compute
        nothing at all and the every-two-seconds path is untouched. The
        cost lives on the once-per-turn `done` tick and nowhere else."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            queued = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                               text="", state=Turn.State.QUEUED, queue_job_id=2)
            sign_in(client, owner)
            body = client.get(
                reverse("chat-turn-status", args=[queued.pk])).json()["html"]
        assert "Send from here" not in body

    def test_the_done_tick_costs_at_most_the_budgeted_extra_reads(self, client):
        """The once-per-turn price of keeping the invariant, pinned so a
        later reader cannot quietly widen it. `_queued_body` is the
        control: the same group, rendered without the predicate."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            done = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                             text="answer", state=Turn.State.DONE, queue_job_id=3)
            other, _t = _own_thread(owner)
            other_done = make_turn(conversation=other, role=Turn.Role.ASSISTANT,
                                   text="answer", state=Turn.State.DONE, queue_job_id=4)
            sign_in(client, owner)
            client.get(reverse("chat-turn-status", args=[done.pk]))   # warm-up
            with CaptureQueriesContext(connection) as one:
                client.get(reverse("chat-turn-status", args=[done.pk]))
            with CaptureQueriesContext(connection) as two:
                client.get(reverse("chat-turn-status", args=[other_done.pk]))
        # Flat between two equivalent conversations -- the property that
        # matters. The absolute number is whatever the body already cost
        # plus the predicate's own bounded reads.
        assert len(two) == len(one)

    def test_two_editable_messages_render_no_duplicate_dom_id(self, client):
        """`chat/_attach_files.html` hardcodes `id="attach-files"` and a
        `<label for="attach-files">`, and the COMPOSER already renders
        one. Every `<label for=...>` resolves to the FIRST match in
        document order, so without a per-turn id every "+ Add files"
        inside an edit form would open the COMPOSER's picker and stage
        the chosen file onto a new turn instead of the branch. The input
        carries `class="attach-input"` (the label-button pattern), so
        the label is the only way to reach it -- this is a functional
        bug, not an HTML-validity nit."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner, texts=("a", "b", "c"))
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        # `== 1`, not `<= 1`: the bare count also passes at ZERO, which
        # would go green on a posture with no attach door at all and pin
        # nothing. This fixture has the composer's own door, so the
        # composer's is the one that must survive -- and each edit form
        # must carry its own suffixed id instead.
        assert body.count('id="attach-files"') == 1
        assert body.count('for="attach-files"') == 1
        assert body.count('id="attach-files-') == 3

    def test_the_edit_form_carries_the_pickers_current_selection(self, client):
        """Spec §5.4 step 3: the branch's first turn is started with the
        picker's current selection, not with the agent's role binding.
        `thread_context` already holds `selected`; without a hidden
        field the POST sends `""` and the branch silently answers from a
        different model than the thread the reader was in."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            sign_in(client, owner)
            url = reverse("chat-conversation", args=[conversation.id])
            body = client.get(f"{url}?connection=7").content.decode()
        assert 'name="connection" value="7"' in body


class TestThePost:
    def test_it_branches_and_redirects_to_the_new_conversation(
        self, client, fake_turn_queue
    ):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner)
            sign_in(client, owner)
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                {"text": "a better question"})
            branch = Conversation.objects.exclude(pk=conversation.pk).get()
        assert response.status_code == 302
        assert str(branch.id) in response["Location"]
        assert "pending=" in response["Location"]

    def test_the_branch_holds_everything_before_the_edit_plus_the_edit(
        self, client, fake_turn_queue
    ):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner, texts=("a", "b", "c"))
            sign_in(client, owner)
            client.post(reverse("chat-turn-edit", args=[conversation.id, turns[2].pk]),
                        {"text": "edited"})
            branch = Conversation.objects.exclude(pk=conversation.pk).get()
            texts = list(branch.turns.filter(role=Turn.Role.USER)
                         .order_by("index").values_list("text", flat=True))
        assert texts == ["a", "b", "edited"]

    def test_the_original_is_untouched(self, client, fake_turn_queue):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner, texts=("a", "b", "c"))
            before = list(conversation.turns.order_by("index")
                          .values_list("index", "text"))
            sign_in(client, owner)
            client.post(reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                        {"text": "edited"})
            after = list(conversation.turns.order_by("index")
                         .values_list("index", "text"))
        assert after == before

    def test_it_is_turn_edit_that_calls_start_turn_not_branch_conversation(
        self, client, fake_turn_queue
    ):
        import inspect

        from agents.chat.views import turns as turns_module

        source = inspect.getsource(turns_module.turn_edit)
        assert "branch_conversation" in source
        assert "start_turn" in source

    def test_a_blank_edit_writes_nothing_at_all(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner)
            before = Conversation.objects.count()
            sign_in(client, owner)
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                {"text": "   "})
        assert response.status_code in (302, 400)
        assert Conversation.objects.count() == before

    def test_an_over_length_edit_writes_nothing_at_all(self, client):
        from agents.limits import MAX_TURN_CHARS

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner)
            before = Conversation.objects.count()
            sign_in(client, owner)
            client.post(reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                        {"text": "x" * (MAX_TURN_CHARS + 1)})
        assert Conversation.objects.count() == before

    def test_a_start_turn_refusal_leaves_the_branch_readable_and_never_500s(
        self, client, fake_queue_down
    ):
        """Accepted and stated rather than papered over: the branch
        exists with its copied history and no answer, a state the thread
        page already renders honestly with its existing banner."""
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner)
            sign_in(client, owner)
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                {"text": "edited"})
            branch = Conversation.objects.exclude(pk=conversation.pk).first()
        assert response.status_code in (302, 503)
        assert "Traceback" not in response.content.decode(errors="replace")
        if branch is not None:
            page = client.get(reverse("chat-conversation", args=[branch.id]))
            assert page.status_code == 200

    def test_a_forged_post_from_each_refused_principal_is_a_404_writing_nothing(
        self, client, fake_turn_queue
    ):
        """No share recipient can mint a durable owned copy that
        outlives revocation (spec review M2)."""
        from agents.visibility import share_conversation

        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner)
            before = Conversation.objects.count()
            for index, level in enumerate((Share.Level.VIEW, Share.Level.USE)):
                recipient = make_user(username=f"recipient-{index}")
                share_conversation(user_principal(owner), conversation, user=recipient,
                                   level=level)
                sign_in(client, recipient)
                response = client.post(
                    reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                    {"text": "mine now"})
                assert response.status_code == 404
                client.logout()
        assert Conversation.objects.count() == before

    def test_a_forged_post_while_a_turn_is_in_flight_is_refused(
        self, client, fake_turn_queue
    ):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner)
            make_turn(conversation=conversation, role=Turn.Role.ASSISTANT, text="",
                      state=Turn.State.QUEUED)
            before = Conversation.objects.count()
            sign_in(client, owner)
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, turns[1].pk]),
                {"text": "edited"})
        assert response.status_code == 404
        assert Conversation.objects.count() == before

    def test_a_turn_id_from_another_conversation_is_a_404_not_a_500(
        self, client, fake_turn_queue
    ):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, _turns = _own_thread(owner)
            elsewhere, other_turns = _own_thread(owner)
            sign_in(client, owner)
            response = client.post(
                reverse("chat-turn-edit", args=[conversation.id, other_turns[1].pk]),
                {"text": "edited"})
        assert response.status_code == 404

    def test_a_branch_of_a_branch_works_and_reports_the_right_parent(
        self, client, fake_turn_queue
    ):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner, texts=("a", "b", "c"))
            sign_in(client, owner)
            client.post(reverse("chat-turn-edit", args=[conversation.id, turns[2].pk]),
                        {"text": "second thread"})
            first_branch = Conversation.objects.exclude(pk=conversation.pk).get()
            branch_turn = first_branch.turns.filter(
                role=Turn.Role.USER, state=Turn.State.DONE).order_by("index").first()
            client.post(
                reverse("chat-turn-edit", args=[first_branch.id, branch_turn.pk]),
                {"text": "third thread"})
            second_branch = Conversation.objects.exclude(
                pk__in=[conversation.pk, first_branch.pk]).get()
        assert second_branch.branched_from_id == first_branch.id
```

`_own_thread` builds only DONE user turns, so the in-flight gate passes; `fake_turn_queue` is the existing fixture in `agents/chat/tests/_helpers.py`. Read `start_turn`'s real return shape before asserting on the refusal path, and use `TurnStart.status` rather than guessing a status code.

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest -q agents/chat/tests/test_turn_edit.py`
Expected: FAIL — `NoReverseMatch: Reverse for 'chat-turn-edit' not found`.

- [ ] **Step 3: Carry the predicate on the card**

In `agents/chat/rendering.py::turn_card`, add two parameters and two fixed keys:

```python
def turn_card(turn, nested=None, attachments=None, attachment_detach_next=None,
              may_edit: bool = False, may_attach_files: bool = False,
              attach_workstream=None) -> dict:
```

```python
        # FEATURE C. `may_edit` is a CONVERSATION-level answer the
        # caller computed once (`may_edit_turn`'s own predicate minus
        # the per-row half, which the card already carries in `role`/
        # `depth`/`state`), so a thread of two hundred messages costs one
        # predicate rather than two hundred. `may_attach_files` and
        # `attach_workstream` are `composer_attach_context`'s two
        # answers, threaded so the edit form's file input needs no
        # second derivation -- and BOTH are needed, because
        # `chat/_attach_files.html` reads `attach_workstream` to decide
        # whether to offer the three-value placement chooser or the
        # two-value one. Passing only the boolean would render a form
        # on the poll path that differs from the reload's, which is the
        # very thing this threading exists to prevent.
        #
        # THEY DEFAULT FALSE/None ONLY FOR CALLERS THAT HAVE NO
        # PRINCIPAL. `agents.chat.views.turns._done_body` DOES compute
        # them and pass them through `turn_group_cards`, so a polled
        # swap shows exactly what a reload shows -- the invariant
        # `_done_body`, `_group_html`, `turn_group_cards` and
        # `_attachments_by_turn` each state in their own words.
        "may_edit": bool(may_edit and turn.role == Turn.Role.USER
                         and turn.depth == 0 and turn.state == Turn.State.DONE),
        "may_attach_files": bool(may_attach_files),
        "attach_workstream": attach_workstream,
        # THE PER-TURN DOM ID the edit form's file input and its
        # label button share. Built here rather than composed in the
        # template, so there is one spelling of it and a test can
        # assert on the same string the markup uses.
        "attach_id": f"attach-files-{turn.pk}",
```

Thread the three keywords through **both** builders — `thread_cards(conversation, *, queue_job_id=None, attachments_by_turn=None, may_edit=False, may_attach_files=False, attach_workstream=None)` and `turn_group_cards(turn, attachments_by_turn=None, *, may_edit=False, may_attach_files=False, attach_workstream=None)` — to every `turn_card(...)` call each of them makes, including the USER card `turn_group_cards` builds directly. Add to `turn_group_cards`' docstring:

```python
    `may_edit` / `may_attach_files` / `attach_workstream`, OPTIONAL
    (chat cluster, feature C): the same three keys `thread_cards`
    takes, threaded so a POLLED swap renders the edit disclosure
    identically to a full page reload. `agents.chat.views.turns.
    _done_body` is the one caller that supplies them -- the queued and
    running bodies leave them at their defaults, and are RIGHT to:
    `may_edit_any_turn` is provably False while a turn of this
    conversation is in flight, which on those two paths is the turn
    being polled. This is the same "never render the group differently
    from a reload" rule this function's own first paragraph states.
```

In `agents/chat/views/thread.py`, compute the conversation-level answer once and pass it:

```python
    # FEATURE C's conversation-level half, computed ONCE for the whole
    # render: may this principal manage the thread, and is nothing in
    # flight. The per-turn half (a finished, root-depth USER row) is
    # already on the card, so a thread of two hundred messages costs one
    # predicate rather than two hundred.
    may_edit_here = may_edit_any_turn(principal, conversation, settings_row=settings_row)
```

and change the `"cards"` key to:

```python
        "cards": thread_cards(conversation, attachments_by_turn=attachments_by_turn,
                              may_edit=may_edit_here,
                              may_attach_files=attach_context["may_attach_files"],
                              attach_workstream=attach_context["attach_workstream"]),
```

Both keys are always present — `composer_attach_context` returns exactly `{"may_attach_files", "attach_workstream"}` — so read them directly rather than through `.get(...)` with a default that would mask a rename.

Add the conversation-level predicate beside `may_edit_turn` in `agents/visibility.py`:

```python
def may_edit_any_turn(principal, conversation, *, settings_row=None) -> bool:
    """`may_edit_turn`'s CONVERSATION-level half, asked once per render.

    The per-turn half -- a finished, root-depth USER row -- is already on
    the card, so the thread page asks this once rather than once per
    message, and the two can never disagree because `may_edit_turn`
    itself calls this.
    """
    if not may_manage_conversation(principal, conversation, settings_row=settings_row):
        return False
    return not Turn.objects.filter(conversation=conversation).exclude(
        state__in=_TERMINAL_TURN_STATES).exists()
```

and rewrite `may_edit_turn`'s last two clauses to call it, so there is one definition:

```python
    return may_edit_any_turn(principal, conversation, settings_row=settings_row)
```

Then, in `agents/chat/views/turns.py`, make `_done_body` the one body that supplies them:

```python
def _edit_context(turn, request, *, scope=None) -> dict:
    """The three card keys the edit disclosure needs, for the ONE poll
    body that can honestly answer them.

    ON `queued` AND `running` THE ANSWER IS PROVABLY FALSE and this is
    never called: `may_edit_any_turn` refuses while any turn of the
    conversation is non-terminal, and on those two paths the turn being
    polled IS one. So the every-two-seconds tick pays nothing, and the
    price below is once per finished turn.

    THE PRICE, NAMED: `may_manage_conversation` (at most one `_user_row`
    read, memoised on the settings row the middleware already stashed)
    plus one `.exists()`, plus `composer_attach_context`'s own
    `tool_access_for`. Against a body that already runs `get_job`,
    `_attachments_by_turn` and a full `render_to_string`, that is the
    cost of keeping the invariant this module states four times over --
    that a polled swap never shows less than a reload.

    `attach_workstream` RIDES ALONG BECAUSE THE FRAGMENT READS IT:
    `chat/_attach_files.html` gates TWO things on it -- the middle radio
    of the placement chooser ("This workstream only") and the whole
    remember-this-choice block -- so passing the boolean alone would
    render a TWO-value chooser here where a reload renders three plus
    the remember control. That is the defect this function exists to
    prevent, in a smaller shape.

    `scope`, OPTIONAL: an already-resolved `WorkstreamScope` for this
    conversation. `_done_body` holds one -- `_attachments_by_turn` needs
    the identical scope for `attachments_for(..., stream=...)`, whose
    `stream=` keyword exists precisely so a caller holding one does not
    re-resolve it -- so the two share a single resolution rather than
    reading the same stream twice on the same tick. `None` re-resolves,
    for a caller that has none.

    THE FK IS STILL ONLY DEREFERENCED WHERE `thread_context`
    DEREFERENCES IT (`workstream=... if may_upload else None`,
    `workstream_id=` always), so the stream ROW is never returned to a
    principal who may not upload. Widening a poll body is exactly where
    a disclosure leak would hide; this one does not open it. The row
    itself costs nothing to reach: Task 4 widened `visible_turn`'s
    `select_related` to carry `conversation__workstream` for the meter,
    so it is already in hand on this path.
    """
    conversation = turn.conversation
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    if not may_edit_any_turn(principal, conversation, settings_row=settings_row):
        return {"may_edit": False, "may_attach_files": False, "attach_workstream": None}
    if scope is None:
        scope = scope_for_conversation(principal, conversation)
    may_upload = bool(scope is not None and scope.may_upload)
    attach = composer_attach_context(
        principal,
        workstream=conversation.workstream if may_upload else None,
        workstream_id=conversation.workstream_id,
        settings_row=settings_row, may_upload=may_upload,
    )
    return {"may_edit": True, **attach}
```

and in `_done_body`, resolve the stream scope **once** and hand it to both readers that need it:

```python
    # ONE SCOPE RESOLUTION PER TICK, not two. `_attachments_by_turn`
    # resolves this same scope for `attachments_for(..., stream=...)`
    # -- whose `stream=` keyword exists exactly so a caller holding one
    # does not re-resolve it -- and `_edit_context` needs the identical
    # answer a few lines later. The flat pin in
    # `test_the_done_tick_costs_at_most_the_budgeted_extra_reads` stays
    # green either way, which is precisely why this is said here rather
    # than left for a test not to catch.
    scope = scope_for_conversation(principal_for_request(request), turn.conversation)
    by_turn = _attachments_by_turn(turn, request, stream=scope)
    ...
    return {
        "state": turn.state,
        "html": _group_html(turn, request, by_turn,
                            **_edit_context(turn, request, scope=scope)),
        "attachments_pending": pending,
    }
```

This gives `_attachments_by_turn` an optional `stream=` keyword of its own, threaded into its existing `attachments_for(...)` call and defaulting to today's internal `scope_for_conversation(...)` so `_group_html`'s other callers are unchanged. `_group_html` gains the three keyword-only card parameters it passes straight through to `turn_group_cards`, defaulted so `_queued_body` and `_running_body` keep calling it exactly as they do today.

- [ ] **Step 4: Write the view and the route**

In `agents/chat/views/turns.py`, add `turn_edit` beside `turn_create`:

```python
# The declared sentences this route refuses with, in Python, once.
EDIT_LEAD = (
    "This starts a new conversation with everything before this message. The "
    "original stays as it is. Files attached earlier in this conversation are not "
    "carried over."
)


@require_POST
def turn_edit(request, conversation_id, turn_id: int):
    """POST `/chat/c/<uuid>/turns/<id>/edit/` -- steps 3 and 4 of a
    branch: validate, branch, start the turn, redirect.

    THE SPLIT IS AN IMPORT-DIRECTION FACT, not a preference (spec review
    M7). `agents/chat` imports `agents/visibility`, ONE WAY, so
    `branch_conversation` cannot call `start_turn` and cannot redirect;
    this view does both, after it returns.

    VALIDATE BEFORE CREATING. The text is checked against the SAME
    `MAX_TURN_CHARS` constant `start_turn` uses, BEFORE
    `branch_conversation` is called at all, so a refused edit writes
    nothing -- no conversation row, no turns, no taint.

    IF `start_turn` REFUSES AFTERWARDS (an unbound role, an unreachable
    engine), the branch exists with its copied history and no answer.
    That is a state the thread page already renders honestly with its
    existing banner, and it is accepted rather than papered over -- the
    alternative would be deleting a conversation the operator can see in
    their sidebar.

    CLASS O: a refused POST is a 404, never a 403 (which would confirm
    the row exists) and never a 500.
    """
    principal = principal_for_request(request)
    conversation = visible_conversation_or_404(principal, conversation_id)
    turn = visible_turn(principal, turn_id)
    if turn is None or not may_edit_turn(principal, conversation, turn):
        raise Http404(f"Turn {turn_id} is not one you may edit.")

    text = (request.POST.get("text") or "").strip()
    if not text or len(text) > MAX_TURN_CHARS:
        messages.error(request, _BLANK if not text else _TOO_LONG)
        return redirect(conversation_url(conversation))

    branch = branch_conversation(principal, conversation, turn, title=conversation.title)
    if branch is None:
        raise Http404(f"Turn {turn_id} is not one you may edit.")

    result = start_turn(branch, text, connection=request.POST.get("connection", ""),
                        actor=principal, **composer_attachment_fields(request))
    if not result.ok:
        messages.error(request, result.error)
        return redirect(conversation_url(branch))
    flash_attachment_outcomes(request, result)
    return redirect(conversation_url(branch, pending=result.turn.pk))
```

`_edit_context` needs `may_edit_any_turn` from `agents.visibility`, `scope_for_conversation` from `agents.workstreams` and `composer_attach_context` from `agents.chat.service` — `agents/chat/views/turns.py` already imports the second and third for `_attachments_by_turn` and for `turn_create`; check its import block and add only what is missing.

Import what `turn_edit` needs from `agents.chat.service` (`_BLANK`/`_TOO_LONG` are module-private there — either export them under public names or use `result.error` after the fact; prefer giving `service.py` two public names, `BLANK_MESSAGE` and `TOO_LONG_MESSAGE`, aliased to the existing private ones so nothing else changes) and from `agents.visibility` (`branch_conversation`, `may_edit_turn`, `visible_turn`).

Register it:

- `agents/chat/views/__init__.py`: export `turn_edit`.
- `agents/chat/urls.py`:

```python
    # FEATURE C. Row-addressed by TWO ids (conversation, turn), POST-only
    # -- the same shape `chat-attachment-detach` already takes.
    path("c/<uuid:conversation_id>/turns/<int:turn_id>/edit/", turn_edit,
         name="chat-turn-edit"),
```

- `identity/routes.py`:

```python
    # O: row-addressed, the same class `chat-turn` and
    # `chat-attachment-detach` carry. 404 unless
    # `agents.visibility.may_edit_turn`.
    "chat-turn-edit": "O",
```

- `identity/tests/test_route_matrix.py::_DRIVERS`:

```python
    "chat-turn-edit": lambda w: (
        "post", reverse("chat-turn-edit", args=[w.conversation.id, w.turn.pk]),
        {"text": "edited from the matrix"}),
```

- `agents/chat/tests/test_never_500.py`: four drivers in the existing style and a `_DRIVERS` entry, covering a normal edit, no agents, the queue down, and a `turn_id` from another conversation.

- [ ] **Step 5: Render the disclosure**

In `agents/chat/templates/chat/_turn_card.html`, inside the non-tool branch, after the attachments block and before the closing `</article>`:

```html
{% if card.may_edit %}
{% comment %}
FEATURE C. The house's zero-JS idiom, already used on this surface: a
`<details>` opening a plain form. `chat/_attach_files.html` ALONE, never
`chat/_composer.html` -- that fragment ends with two script blocks
(`_attach_dragdrop.html` and `_enter_to_send.html`) and this disclosure
renders once per eligible user turn, so including it would multiply the
page's pinned script count by the number of editable messages. Nothing
here adds a script, so the counts of 4 and 3 stand.

`card.attach_id` IS NOT COSMETIC. `_attach_files.html` hardcodes
`id="attach-files"` on its input and `for="attach-files"` on the label
button that is the ONLY way to reach it (the input carries
`class="attach-input"`, the label-button pattern) -- and the COMPOSER
already renders one. Every `<label for=...>` resolves to the FIRST match
in document order, so on a thread with ten editable messages every
"+ Add files" inside every edit form would open the COMPOSER's picker
and stage the chosen file onto a NEW TURN instead of onto the branch.
The per-turn id is what points each form's label at its own input. The
fragment gains `id="{{ attach_id|default:'attach-files' }}"` and the
matching `for=`, so its existing three call sites are unchanged.

`card.attach_workstream` IS PASSED EXPLICITLY, not inherited: the
fragment reads it to decide between the three-value placement chooser
and the two-value one, and on a polled swap the surrounding context
does not carry it.

THIS DISCLOSURE RENDERS IDENTICALLY ON A POLLED `done` SWAP, because
`_done_body` supplies `may_edit`, `may_attach_files` and
`attach_workstream` -- see that body's own `_edit_context`.

`selected_connection` IS THE PAGE'S OWN `?connection=` PICK, and the
poll endpoint cannot know it: the picker's selection lives in the thread
page's query string, which `chat-turn-status` never sees -- the same
structural fact spec review M3 turns into "the window never travels".
Rendered server-side here, this field is therefore EMPTY on a polled
swap, and the poller closes that gap itself by copying the composer's
own already-rendered hidden field into the swapped block (see the
`carryConnection` helper in `chat/conversation.html`'s existing script).
A no-JS page LOAD never lost the pick -- the server renders it into this
field -- so the polled swap was the only path that did, and it no longer
does. Pinned on both paths below.
{% endcomment %}
<details class="turn-edit">
  <summary>Edit and carry on from here</summary>
  <p class="muted">{{ edit_lead }}</p>
  <form method="post" enctype="multipart/form-data"
        action="{% url 'chat-turn-edit' card.turn.conversation_id card.turn.pk %}">
    {% csrf_token %}
    <input type="hidden" name="connection" value="{{ selected_connection }}">
    <textarea name="text" rows="4" required>{{ card.text }}</textarea>
    {% if card.may_attach_files %}
    {% include "chat/_attach_files.html" with attach_workstream=card.attach_workstream attach_id=card.attach_id %}
    {% endif %}
    <button type="submit">Send from here</button>
  </form>
</details>
{% endif %}
```

**Close the polled-swap gap in the poller, in four lines.** The disclosure's hidden `connection` renders empty on a polled swap, because `chat-turn-status` never sees the thread page's query string. The page does hold the value, though — the composer renders its own hidden `connection` field server-side — so the poller copies one field on the page into another field on the same page. Add beside `applyContext` in `chat/conversation.html`'s **existing** `<script>` (Task 4 introduced that helper into this same block; no new tag, so the pinned counts of 4 and 3 stand):

```javascript
  // THE PICKER'S SELECTION, CARRIED ACROSS A SWAP. `chat-turn-status`
  // is polled by turn pk and never sees this page's `?connection=`, so
  // a swapped block's edit form renders that field empty. This copies
  // the COMPOSER's own SERVER-RENDERED value into it -- one field on
  // this page into another field on this page. Not a number the client
  // invented, not prose, not `innerHTML`, and nothing sent to the
  // server: decisions 21 and 22 are untouched. With scripts off there
  // is no swap to fix, and the page-rendered form already carries the
  // pick.
  function carryConnection(block) {
    var pick = document.querySelector('#turn-form input[name="connection"]');
    if (!pick || !block) { return; }
    block.querySelectorAll('input[name="connection"]').forEach(function (field) {
      field.value = pick.value;
    });
  }
```

and call it on the one path that inserts a swapped group — immediately after each `swapBlock` call returns a truthy `refreshed`, beside the existing `card = refreshed;`:

```javascript
              carryConnection(refreshed);
```

Confirm `#turn-form` is really the composer form's id on this page (`grep -n 'composer_form_id' agents/chat/templates/chat/conversation.html` — `thread_context` passes `composer_form_id="turn-form"`), and use whatever it really is rather than assuming.

`edit_lead` comes from `thread_context` (`"edit_lead": EDIT_LEAD`) — a declared sentence in Python, not typed here. Put `.turn-edit`'s CSS in **`chat/base.html`**, not in `conversation.html`'s block: `_turn_card.html` is a fragment with more than one page consumer (`/chat/all/`'s preview pane renders it too), and the CSS gate's rule is the nearest common ancestor of every page that *could* render it.

**Give `chat/_attach_files.html` an optional id**, in this same commit — two lines, and every existing call site is unchanged because both default:

```html
    <label class="attach-label-button" for="{{ attach_id|default:'attach-files' }}">+ Add files</label>
    <input type="file" id="{{ attach_id|default:'attach-files' }}" name="files" multiple class="attach-input">
```

and add to that fragment's own header comment:

```
`attach_id`, OPTIONAL: the input's DOM id and its label's `for=`. The
three composer surfaces omit it and get `attach-files`, exactly as
before. The edit disclosure (`chat/_turn_card.html`, chat cluster
feature C) renders this fragment ONCE PER ELIGIBLE USER TURN on a page
that already carries the composer's copy, and a duplicate id would send
every one of those label buttons to the COMPOSER's input -- staging the
file onto a new turn instead of onto the branch.
```

The rest of the fragment's context contract is already satisfied: it reads `attach_workstream`, which the include passes explicitly from the card.

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -q agents/chat/tests/test_turn_edit.py`
Expected: PASS.
Run: `.venv/bin/pytest -q agents/chat identity/tests/test_route_matrix.py foundation/ops`
Expected: PASS — including `test_thread.py`'s script-count pins at 4 and 3, the never-500 sweep, the route matrix and the CSS gate.

- [ ] **Step 7: Document the flow and its named limitation**

In `agents/chat/README.md`:

```markdown
## Editing a past prompt

Any of your own earlier messages carries an **Edit and carry on from here**
disclosure. It opens a plain form — a textarea pre-filled with that message, an
optional file input, one button — and submitting it creates a **new conversation**
holding everything before that message, with your edited text as its newest turn.
The original is untouched.

It is a branch, not a rewind, and the reasons are recorded in the ADR: a rewind
cannot honestly un-taint (taint rows are additive-only by design, and deleting
one is precisely the laundering the platform forbids), it orphans audit rows and
attachments, and in a shared conversation it would destroy somebody else's work.
A branch you did not want is one delete away; a rewind you did not want is gone.

**What a branch does not carry:** attachments on the copied turns (there is no
copier seam; the form says so before the button), tool audit rows, pinned or
archived state, and shares. Files attached to the *edited* message itself work
normally — `start_turn` stages them exactly as any other send does.

**Who may:** the conversation's owner, or an administrator with content access.
**Not** a share recipient, even on their own message — a branch is a copy, so it
answers to the copy predicate, and a recipient minting a durable conversation they
own that survives revocation of the share is a different decision from letting
them post. Nobody may edit while a turn is in flight.

**The poller keeps up.** The `done` tick supplies the same three card keys the page
does, so a swapped exchange renders the disclosure exactly as a reload would — the
invariant `_done_body`, `_group_html`, `turn_group_cards` and `_attachments_by_turn`
each state in their own words. The queued and running ticks pay nothing for it: the
predicate is provably false while a turn of that conversation is in flight. The one value that cannot
travel on the wire is the picker's own selection, which lives in the thread page's
query string and never reaches the poll endpoint — so the poller carries it across
on the page instead, copying the composer's own server-rendered hidden field into
each swapped block. A no-JS page load never lost the pick; the polled swap was the
only path that did, and it no longer does.
```

- [ ] **Step 8: Run the four runs and both posture sweeps**

As in Task 5, Step 9.

- [ ] **Step 9: Commit**

```
git add agents/chat/views/turns.py agents/chat/views/thread.py agents/chat/views/__init__.py agents/chat/rendering.py agents/visibility.py agents/chat/urls.py agents/chat/templates/chat/_turn_card.html agents/chat/templates/chat/_attach_files.html agents/chat/templates/chat/conversation.html agents/chat/templates/chat/base.html identity/routes.py identity/tests/test_route_matrix.py agents/chat/tests/test_never_500.py agents/chat/tests/test_turn_edit.py agents/chat/README.md
git commit -m "feat(chat): edit a past prompt and carry on in a branch"
```

---

### Task 14: The provenance line

**Files:**
- Modify: `agents/chat/views/thread.py` (`thread_context`)
- Modify: `agents/chat/templates/chat/conversation.html`
- Test: `agents/chat/tests/test_turn_edit.py`

**Interfaces:**
- Consumes: `Conversation.branched_from / branched_at_index` (Task 11); `agents.visibility.visible_conversations`.
- Produces: the context keys `"branched_from"` (a `Conversation` or `None`) and `"branch_provenance"` (a declared sentence or `""`). Nothing later consumes them.

- [ ] **Step 1: Write the failing test**

Append to `agents/chat/tests/test_turn_edit.py`:

```python
class TestTheProvenanceLine:
    def test_a_branch_says_where_it_came_from_and_links_back(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            parent = make_conversation(agent=make_agent(slug="parent"),
                                       title="The original",
                                       **owner_fields(user_principal(owner)))
            branch = make_conversation(agent=parent.agent, title="The original",
                                       branched_from=parent, branched_at_index=3,
                                       **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[branch.id])).content.decode()
        assert "Branched from" in body
        assert "message 3" in body
        assert reverse("chat-conversation", args=[parent.id]) in body

    def test_a_plain_conversation_says_nothing(self, client):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation = make_conversation(agent=make_agent(slug="plain-thread"),
                                             **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "Branched from" not in body

    def test_a_deleted_parent_leaves_the_line_unlinked_and_the_page_readable(
        self, client
    ):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            parent = make_conversation(agent=make_agent(slug="doomed"),
                                       title="Gone",
                                       **owner_fields(user_principal(owner)))
            branch = make_conversation(agent=parent.agent, title="Survivor",
                                       branched_from=parent, branched_at_index=2,
                                       **owner_fields(user_principal(owner)))
            parent.delete()
            sign_in(client, owner)
            response = client.get(reverse("chat-conversation", args=[branch.id]))
        body = response.content.decode()
        assert response.status_code == 200
        assert "Branched from" in body
        assert "Gone" not in body

    def test_a_parent_this_principal_may_not_see_is_neither_named_nor_linked(
        self, client
    ):
        """Resolved through `visible_conversations`, never a bare pk
        read: a title is content, and a branch an administrator made of
        somebody's thread must not leak the original's title back to a
        member who was handed the branch."""
        owner, other = make_user(), make_user(username="other")
        with posture(POSTURE_ENTERPRISE):
            parent = make_conversation(agent=make_agent(slug="private-parent"),
                                       title="Confidential title",
                                       **owner_fields(user_principal(other)))
            branch = make_conversation(agent=parent.agent, title="Branch",
                                       branched_from=parent, branched_at_index=1,
                                       **owner_fields(user_principal(owner)))
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[branch.id])).content.decode()
        assert "Confidential title" not in body
        assert reverse("chat-conversation", args=[parent.id]) not in body

    def test_a_branch_carrying_a_tool_call_shows_the_tool_cards_and_no_audit_link(
        self, client, fake_turn_queue
    ):
        owner = make_user()
        with posture(POSTURE_ENTERPRISE):
            conversation, turns = _own_thread(owner, texts=("a", "b"))
            make_turn(conversation=conversation, role=Turn.Role.TOOL, text="result",
                      state=Turn.State.DONE, index=99,
                      tool_call={"tool": "rag.search", "tool_kwargs": {"q": "x"}})
            last = make_turn(conversation=conversation, role=Turn.Role.USER, text="c",
                             state=Turn.State.DONE)
            sign_in(client, owner)
            client.post(reverse("chat-turn-edit", args=[conversation.id, last.pk]),
                        {"text": "edited"})
            branch = Conversation.objects.exclude(pk=conversation.pk).get()
            body = client.get(
                reverse("chat-conversation", args=[branch.id])).content.decode()
        assert "rag.search" in body
        assert branch.turns.filter(invocation__isnull=False).count() == 0
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest -q agents/chat/tests/test_turn_edit.py -k Provenance`
Expected: FAIL — `assert "Branched from" in body`.

- [ ] **Step 3: Build the context**

In `agents/chat/views/thread.py`, before the returned dict:

```python
    # FEATURE C's provenance line. RESOLVED THROUGH
    # `visible_conversations`, NEVER A BARE PK READ: a title is content,
    # and a branch an administrator made of somebody's thread must not
    # leak the original's title back to whoever is reading the branch.
    # One query, and only for a conversation that actually has a parent.
    branched_from = None
    if conversation.branched_from_id is not None:
        branched_from = visible_conversations(principal, settings_row=settings_row) \
            .filter(pk=conversation.branched_from_id).first()
    branch_provenance = (branch_provenance_sentence(conversation.branched_at_index)
                         if conversation.branched_at_index is not None else "")
```

and the two keys in the dict:

```python
        "branched_from": branched_from,
        "branch_provenance": branch_provenance,
```

Declare the sentence in `agents/usage.py`? No — it belongs with the feature, so put it in `agents/visibility.py` beside `branch_conversation`:

```python
BRANCH_PROVENANCE_LEAD = "Branched from"


def branch_provenance_sentence(index: int) -> str:
    """The tail of the provenance line: where in the parent this thread
    left off. The parent's own title (or its absence) is the template's
    half, because whether it may be NAMED is a visibility question the
    view answers through `visible_conversations`."""
    return f"at message {index}"
```

- [ ] **Step 4: Render it**

In `agents/chat/templates/chat/conversation.html`, at the top of `{% block chat_content %}`, above the thread scroll region:

```html
{% if branch_provenance %}
{% comment %}
FEATURE C's provenance line. The title links back only while the parent
is still visible to THIS principal (resolved in the view through
`visible_conversations`); with the parent deleted or invisible the line
renders without a link and without the title, which is the honest
version of "this came from somewhere you cannot see".
{% endcomment %}
<p class="muted branch-provenance">{{ branch_provenance_lead }}
  {% if branched_from %}<a href="{% url 'chat-conversation' branched_from.id %}">{{ branched_from.title|default:branched_from.agent.name }}</a>{% endif %}
  {{ branch_provenance }}</p>
{% endif %}
```

Add `"branch_provenance_lead": BRANCH_PROVENANCE_LEAD` to `thread_context`. Put `.branch-provenance`'s CSS in `conversation.html`'s own `chat_style` block — its only consumer is this page's body.

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -q agents/chat/tests/test_turn_edit.py`
Expected: PASS.
Run: `.venv/bin/pytest -q agents/chat foundation/ops`
Expected: PASS.

- [ ] **Step 6: Run the four runs and both posture sweeps**

As in Task 5, Step 9.

- [ ] **Step 7: Commit**

```
git add agents/chat/views/thread.py agents/visibility.py agents/chat/templates/chat/conversation.html agents/chat/tests/test_turn_edit.py
git commit -m "feat(chat): the branch provenance line on the thread page"
```

---

## Task 15: The ADR, the whole-branch gate, and the deploy

**Files:**
- Create: `docs/adr/00NN-chat-cluster.md` — read `ls docs/adr/` and take the **next** number; do not guess.
- Modify: `README.md` only if it indexes ADRs by number (check).

- [ ] **Step 1: Write the ADR**

Three decisions, one record, no model or vendor names and no absolute paths:

```markdown
# NN. The chat cluster: an honest context meter, an audience column, and branching

**Status:** Accepted
**Date:** 2026-09-21

## Context

Three requests landed together: show how much of the model's context a
conversation is using; make the agents screen a real utility that users as well as
administrators can reach; and let somebody edit an earlier message and carry on
from there.

## Decision 1 — the operative window is the honest denominator

The meter divides by what the engine will actually be asked to allocate: the
operator's own per-connection value when there is one, and the engine adapter's
bounded default otherwise. **Not** a model's architecture maximum, and **not** a
probe. This amends ADR 0010's context-window entry with a *read* direction: that
column was written to be sent, and it is now also read for display, through one
pure function that writes nothing and sends nothing.

Showing a ceiling larger than what the engine is asked for would mislead in the
dangerous direction — the reader would believe they had room they do not have. And
a display probe is still a probe: one per page render, against a machine that may
be asleep, which is exactly what ADR 0010's own incident write-up forbids.

The meter also measures **what is sent**, not what the conversation holds: the
platform replays a fixed number of recent turns, so a long conversation is already
shortened before it is sent, and the line says so. Reporting the whole
conversation's size as "what this chat is using" would have been false.

## Decision 2 — `box_wide` is the audience column; `resident` stays the origin marker

`resident` records that a row started life as a shipped default. It is an origin
marker, not a lock, and it has a second reader that warns the tool-label page when
labelling a tool would weaken the shell path. Overloading it to mean "everybody may
see this" would have made that warning fire for rows that were never shipped
defaults, and made the new agent form's audience control lie about provenance.

So audience got its own boolean, and `visible_agents` swapped one leg for it. A data
migration set `box_wide = resident` for every existing row, so nobody's access
changed on the day it landed.

Audience is **two independent controls**, never one exclusive choice. Reach is a
plain field, administrators only, and it writes no label. Entitlement labels go
through the existing add/remove diff gate, which refuses any entitlement the actor
may not label with — so a label an administrator set survives anything a member
does with their own. One control writing both would either clobber a label it may
not touch or refuse an edit it should allow.

## Decision 3 — editing a past prompt branches; it does not rewind

Editing an earlier message creates a new conversation holding everything before it,
with the edited message as its newest turn. The original is untouched.

A rewind cannot honestly un-taint: taint rows are additive-only by design, and
deleting the turn that brought labelled material in would either leave a dangling
reference or perform exactly the laundering the platform forbids — edit the tainted
turn, the tag disappears, the share comes back. A rewind also orphans audit rows and
attachments, and in a conversation shared at a level that permits posting it would
destroy somebody else's work with no undo and no trace.

The cost of branching is sidebar clutter, and it is paid with provenance rather than
by destroying history: two nullable columns make "where did this come from" a fact
you can query. The asymmetry decided it — branching when a rewind was wanted costs
reversible clutter; rewinding when history was wanted destroys data irreversibly.

## Consequences

- One token estimator exists on this platform, and compaction will consume it rather
  than growing a second, drifting counter.
- The agent form is the first create/edit surface for agents; tool granting and
  per-person agent sharing remain deliberately out of scope.
- A share recipient cannot edit-and-branch in a conversation shared with them; what
  they are refused is a copy.
- Attachments do not follow a branch in v1. Carrying them means a fifth registered
  attachment seam — a copier — in the documents column.
```

- [ ] **Step 2: Confirm the whole plan's documentation is not stale**

Run: `.venv/bin/pytest -q foundation/ops/tests/test_docs_model_names.py foundation/ops/tests/test_agent_standards.py foundation/ops/tests/test_docs_sync.py`
Expected: PASS. `test_docs_model_names.py` walks `docs/superpowers/**` (its own anti-vacuous assertion proves it), so this plan file and the spec are in scope for the no-model-names rule; `test_agent_standards.py::test_no_tracked_document_names_a_local_home_directory` walks every **tracked** document for the no-absolute-paths rule, which is why the plan must be committed before that gate can see it.

- [ ] **Step 3: Merge current `dev` into the branch, in this worktree**

```
git fetch origin
git merge origin/dev
```
Resolve any conflict **here**, in the worktree directory `.claude/worktrees/model-management-framework`, never in the root checkout.

**`dev`, not `main`.** Since the single-repo cutover, `AGENTS.md` and `docs/DEV.md` state the flow as pull request → review → the owner's merge word → `dev` → the root checkout fast-forwarded to `origin/dev`. `main` takes only batched release pull requests from `dev` and is never pushed to, or merged into, from a feature branch. The merge-readiness checklist's own line is "Current `dev` is merged into the branch and resolved there."

- [ ] **Step 4: The full gate**

```
export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools
FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py check
```
Expected: six green runs, `No changes detected`, and `System check identified no issues`.

- [ ] **Step 5: Commit the ADR**

```
git add docs/adr
git commit -m "docs(adr): the chat cluster's three decisions"
```

---

## Smoke Checklist

Walked **by hand, in a browser, on this branch's own preview stack** — `scripts/preview up <branch> --port <p> --db-port <q>`, never the primary `:8000` stack — before any pull request. No success language until the last rung of `docs/DEV.md` §8 is reached on the owner's live box.

**Phase A**
- [ ] A fresh conversation shows a small number and a low percentage.
- [ ] Sending several long messages moves both **without a page reload** — the poller path.
- [ ] The disclosure opens and reads correctly.
- [ ] A conversation of more than twenty turns shows the truncation clause, and its estimate **stops growing** once the window is full. This is the proof that the meter measures what is sent.
- [ ] With a per-connection context window set on the bound connection, the denominator changes to that number on reload; cleared, the line reports that the engine's default applies — and that sentence is **absent from a member's page source** (view source, as a member).
- [ ] With no chat role bound, the page still renders 200 with the existing banner and a ceiling-free line.
- [ ] The page's HTML carries the same number of `<script>` tags as before the change.

**Phase B**
- [ ] As an administrator: `/settings/agents/` lists every agent; the shipped chat agent opens; its `--reset` warning is there; its system prompt is edited and saved; the change is visible in a **new conversation's answer**.
- [ ] As a member: `/chat/agents/` lists only their own; "New agent" creates one; it appears in the chat picker; `/settings/agents/` is refused.
- [ ] As a member: setting the audience to an entitlement they own makes the agent appear for a second member who holds it and **not** for a third who does not.
- [ ] "Everyone on this box" is offered to the administrator and **not present in the member's page source**.
- [ ] A member who owns a box-wide row they installed sees it in the read-only section with its sentence, and the editor 404s for them.
- [ ] On an open box, every agent is editable and the entitlement control does not render at all.
- [ ] Every existing conversation still opens, and its agent still answers, after the migration.

**Phase C**
- [ ] An earlier message is edited; a new conversation appears holding everything before it plus the edited message; the answer streams into it.
- [ ] The **original is opened again and is unchanged**.
- [ ] The branch carries its workstream and its provenance line, and the line links back.
- [ ] A branch of a conversation with a tool call shows the tool cards and no audit link.
- [ ] An edit is not offered while a turn is running, and a forged POST in that state is refused.
- [ ] The script count is unchanged.

**Then:** whole-branch review, every owner-reported issue closed and verified, a pull request **into `dev`**, the owner's merge word, `dev`, and the root checkout **fast-forwarded to `origin/dev`** in an announced deploy window (`git status --short` there first, and triage anything staged or modified rather than overwrite it), `migrate`, restart `watcher` and `worker`, and fresh pixels on the live box. Only then is any of this "done". `main` is not on this path at all: it takes only batched release pull requests from `dev`.

---

---

## Plan review

This plan went through one adversarial hygiene review — **AMEND, 5 Major / 9 minor / 4 nit** — against the real tree. **All eighteen findings are applied; none is disputed and none is skipped.** Three are applied *and extended*, where fixing what the review named exposed a second fact in the same file; each extension is marked and reasoned below. The owner ruled on the plan's three open concerns at the same time, and those rulings are recorded first because two of them changed the plan's shape.

### The three author concerns, as ruled

**A — the meter is two queries, not §8's "exactly one". ACCEPTED, with one correction.** `ContextUsage.total_turns` feeds the truncation clause that owner ruling flag 1 made binding, and a `LIMIT HISTORY_TURNS` slice cannot produce an exact total. What the correction fixes is the *justification*: the first draft called both reads "indexed reads on `(conversation, state, depth)`", and there is no such index — `Turn.Meta.indexes` is exactly `agents_turn_thread` on `(conversation, index)` and no migration adds another. The claim is gone from both places it appeared, replaced with the true statement: the `state`/`depth` half is a scan *within one conversation's rows*, bounded by the thread's own length, which is the same bound the page's per-turn render already pays. An untrue performance sentence is the kind that gets copied forward.

**B — the edit disclosure on the poller's just-answered bubble. REVERSED.** The first draft had `turn_group_cards` leave the card keys `False`, so a polled `done` swap dropped the disclosure until reload. That breaks an invariant `agents/chat/views/turns.py` states in **four** separate docstrings — `_done_body` ("a poller that swapped in less than that would show strictly less than the same thread shows after F5"), `_group_html` ("can never render the group differently from one another or from a reload"), `turn_group_cards` ("exactly what a full page reload already shows"), `_attachments_by_turn` ("NEVER stale relative to what a reload would show") — and the last of those is round 13's own stale-strip lesson, which this plan invokes *correctly* for the meter one task earlier and then argued against one task later. The cost it was avoiding does not exist on the ticking paths: `may_edit_any_turn` is provably `False` on `queued` and `running`, because the turn being polled *is* a non-terminal turn in that conversation. Task 13 now computes both answers in `_done_body` alone — ≤2 queries plus one `tool_access_for`, once per finished turn, on a body that already runs `get_job`, `_attachments_by_turn` and a full `render_to_string` — and pins that the other bodies pay nothing.

**C — the four "read this file first" steps. Two shipped code contradicting the contract they pointed at; one missed the real hazard; one was asking for a check that cannot fail.** All four are fixed: M3 (transfer-panel parameters), M4 (the DOM-id collision), n1 (two invented CSS tokens), and the `audit.record` instruction, which is **removed** rather than reworded — that function takes `**detail`, so there is no keyword to match.

### Major

| # | Finding | Disposition |
|---|---|---|
| **M1** | Deviation 3 breaks a four-times-stated invariant, for a cost that does not exist on the ticking paths | **Applied, extended.** Deviation 3 deleted; `_edit_context` added to `_done_body`; `turn_group_cards` and `_group_html` take the keys; three tests replace the one that pinned the limitation; the `agents/chat/README.md` paragraph documenting it is replaced by one describing the invariant. **Extension:** the review's sketch threads `may_edit` and `may_attach_files`; `chat/_attach_files.html` also reads **`attach_workstream`** to choose between the three-value and two-value placement chooser, so a poll body passing only the boolean would still render a form differing from the reload's — the same defect one size down. The card carries all three. **Residual — carried at round 1, CLOSED at round 2 (see m10 below):** the picker's own selection cannot travel on the wire, because it lives in the thread page's `?connection=` query string and `chat-turn-status` never sees it — the same fact spec review M3 turns into "the window never travels". It is now carried across **on the page** instead, by four lines in the existing poller. |
| **M2** | `visible_agent_for_edit` duplicates `labellable_agent` byte-for-byte in the same module | **Applied as directed.** The new function is deleted; `agent_edit` calls `labellable_agent(pk)`; that function's docstring is widened so the second caller and its **different class** are stated rather than silently inherited — its current "CLASS S already means every caller here is an administrator" is true of `/chat/access/` and false of `chat-agent-edit`, which is class O and applies `may_manage_agent` itself. |
| **M3** | The transfer-panel include contradicts the parameter block it tells the implementer to read | **Applied, extended.** The include is rewritten against `chat/agent_entitlements.html`'s real line: `tp_available`/`tp_active` (not `available`/`active`, which would have rendered two empty panes with no error), plus the five omitted required parameters `tp_key`, `tp_anchor`, `tp_available_count`, `tp_active_count`, `tp_total`; `tp_label` dropped per the documented convention that phase 2 omits it under a `<details>` summary. Task 7's `entitlement_panel` gains `available_count`, `active_count` and `anchor`, computed in the builder because the fragment says the counts belong "where the split is". **Extension:** the fragment's own note says every `.transfer-*` rule lives in `_settings.html` "because EVERY CONSUMER TODAY EXTENDS `_settings.html`", and states what the first consumer outside the settings area must do — *promote* those rules to `_shell.html`, not copy them. `chat/agent_edit.html` extends `chat/base.html`, so Task 9 **is** that consumer, and the step now says so. Also applied: the review's "ONE FORM, NO NESTING" check — the panel is a sibling of the field form, never nested, which is why the two POST paths are told apart by an `action` field. |
| **M4** | Including `_attach_files.html` once per editable turn collides on a hardcoded DOM id | **Applied as directed.** The fragment gains an optional `attach_id` (defaulting to today's value, so its three existing call sites are untouched); the card carries `attach_id` as a fixed key so the markup and the test assert the same string; a test pins that two editable messages render no duplicate `id="attach-files"`. This was a functional bug, not a validity nit: the input is `class="attach-input"` and the label is the only way to reach it, so every edit form's "+ Add files" would have opened the composer's picker and staged the file onto a new turn instead of the branch. |
| **M5** | Three of five new templates are prose-only, and the self-review says the opposite | **Applied, extended.** `agent_edit.html`, `agents.html` and `agents_admin.html` are written out in full; `agent_edit.html` is added to the File structure table and to Task 8's **Files** header, where it was missing entirely; self-review §2 is corrected. **Extension:** writing `agents_admin.html` surfaced a **fourth registration place** the plan had missed — `foundation/templates/_settings.html`'s sidebar is hand-written `<a>` elements with a `side_current_*` block each, **not** a walk over `SETTINGS_GROUPS`. Registering the `Entry` alone gets the page swept by `foundation/tests/test_shell.py` (which does walk the table) while leaving it absent from the nav an operator clicks. Task 10 Step 4 now registers in four places and `docs/EXTENDING.md` says four. |

### Minor and nit

| # | Finding | Disposition |
|---|---|---|
| **m1** | The plan names a worktree that does not exist (that string is the branch) | Applied. Global Constraints now state the **directory** `.claude/worktrees/model-management-framework` and the **branch** `specs-queue-and-chat-cluster`, and name `git worktree list` as the authority. |
| **m2** | `test_no_binding_is_resolved_on_the_poll_path` cannot fail | Applied as directed. It patched `agents.chat.views.turns.visible_turn` to itself and then `preflight_turn`, a name that module never imports. Rewritten to patch `models.contracts.bindings.resolve` — what `resolve_chat` really reaches — with a `models_`-table query sweep as the second belt. |
| **m3** | The false index claim | Applied. See ruling A. |
| **m4** | `identity.testing.admin_sees_content` does not exist | Applied as directed. It is a keyword on `posture(...)`; the import would have raised at collection and taken the whole module down. Now `posture(POSTURE_ENTERPRISE, admin_sees_content=True)`, with the reason in a comment. |
| **m5** | `make_entitlement(name=…, owner=…)` is not a valid call, in the headline M1 test | Applied as directed. `Entitlement` has no `owner`; ownership is an `EntitlementGrant` with `role="owner"`, and `labelling_entitlements` filters on **owned** ids — so the wrong call would have quietly asserted the member's own add/remove leg against an entitlement they cannot label with, which is the other half of the same test. Now `make_entitlement(name="Mine")` + `grant(mine, user=member, role="owner")`, and the speculative `create_entitlement` import line is deleted. |
| **m6** | The branch's first turn silently ignores the picker | Applied. The disclosure renders `<input type="hidden" name="connection" value="{{ selected_connection }}">` and a test pins it. Its poll-path residual is stated under M1 above and in the markup's own comment. |
| **m7** | Migration 0013 hard-depends on 0012, so Phase C is not independently mergeable | Applied as directed. Task 11 now says the number and the dependency assume Phase B has landed, that the independence claim is true at the code level and not of the migration graph, and what to renumber to if C is ever split out and merged first. |
| **m8** | A tautological assertion (`… or True`) | Applied. Deleted, and replaced with the assertion it was reaching for: the read-only box-wide section offers **no** edit link, because `may_manage_agent` refuses that row and a link would be a link to a 404. |
| **m9** | No absolute-delta pin replaces §8's "exactly one new query" | Applied as directed. `test_the_context_key_costs_exactly_the_two_reads_it_budgets` asks `_context_body` directly, over a turn loaded the way `visible_turn` loads it, and pins the count at 2 — equality-under-scale alone cannot notice a third constant query arriving later, which is the regression the pin exists to catch. |
| **n1** | Two invented CSS tokens (`--rule`, `--warn`) | Applied. The meter CSS now uses `--border`, `--ok`, `--accent` and `--danger`, all verified in `_shell.html`'s light and dark blocks, and the step says to declare no new token rather than telling the implementer to go and substitute. |
| **n2** | `_derive_agent_slug` uniquifies a free slug | Applied as directed. The guard is now the fallback (`fell_back`), not the string, so an agent somebody really named "Agent" takes `agent` when it is free while the punctuation-only placeholder always takes a suffix. |
| **n3** | An unused import in an Interfaces block | Applied. `entitlement_row_url` is dropped from Task 9's **Consumes** — and *why* is recorded, because it is the function the two access pages use for exactly this job: it builds `reverse(route_name)` with no arguments and `chat-agent-edit` is row-addressed, so it cannot name this route. The refusal URL is the row's own edit URL. |
| **n4** | The poll-path meter tests go to `test_thread.py`, not the poll endpoint's own module | Applied as a recorded decision. `agents/chat/tests/test_turn_status.py` exists and on module-ownership grounds is the better home; spec §9 names `test_thread.py`, and the plan follows it because every assertion in that class is about *the relationship between the page and the tick*, which is how the two halves come to disagree when they are split. One line in the plan so the next reader knows it was decided rather than defaulted. |

### Round 2 — scoped re-check: **CLEAN to proceed**, 0 Major / 1 minor / 2 nit

The re-check confirmed all eighteen round-1 findings landed without being weakened in the applying, **accepted all three extensions** (E3, the fourth settings registration place, judged the round's most valuable catch — and an argument for M5, since only writing the template out surfaced it), and called two corrections better than what it had asked for: the false-index fix, which replaced the claim with a true bound rather than deleting it, and the `labellable_agent` class-mismatch catch. Three residual findings, all applied here, plus one orchestrator adjudication.

| # | Finding | Disposition |
|---|---|---|
| **m10** | The residual's own sentence overstates its boundedness, and there is a cheap close | **Applied, both halves.** The wording is corrected everywhere it appeared — in the disclosure's markup comment, in `agents/chat/README.md`, and in M1's row above. The old sentence ("exactly as every no-JS path on this surface already does. Nothing the reader can see differs") was wrong twice: a no-JS page **load** carries the pick, because the server renders it into the field, so the polled swap was the only path that lost it; and "nothing the reader can see differs" was true of the markup and false of the outcome, since the branch's first turn would answer from a different binding. **And the residual is closed rather than documented**, per the orchestrator's adjudication: Task 13 adds `carryConnection` to the poller's existing `<script>` — four lines copying the composer's own **server-rendered** hidden field into each swapped block. One field on the page into another field on the same page: not a client-invented number, not prose, not `innerHTML`, nothing sent to the server, so decisions 21 and 22 are untouched and the tag count stays 4/3. Task 4 carries a forward reference, since it owns the first edit to that block; the code is specified in Task 13 because the field it copies does not exist until Phase C, and dead code in Phase A would be worse than a cross-reference. Pinned by `test_the_poller_carries_the_pickers_selection_into_a_swapped_form`. |
| **n5** | The invariant test asserts the button, not the parity its own extension exists for | **Applied as directed.** `test_a_polled_done_swap_shows_exactly_what_a_reload_shows` now builds its conversation in a stream the owner may upload to and asserts `"This workstream only"` in **both** bodies. E1's whole justification is the placement chooser — `_attach_files.html` gates the middle radio *and* the remember-this-choice block on `attach_workstream` — and asserting only `"Send from here"` left a later reader free to narrow `_edit_context` back to two keys with no test turning red. That is the exact failure mode the `select_related` pin (spec review R2) was written to prevent one task earlier. |
| **n6** | `scope_for_conversation` is resolved twice on the same `done` tick | **Applied as directed.** `_done_body` resolves the scope once and passes it to both readers: `_attachments_by_turn(turn, request, stream=scope)` — `attachments_for`'s `stream=` keyword exists precisely so a caller holding one does not re-resolve it — and `_edit_context(turn, request, scope=scope)`. Not blocking, and the flat pin stays green either way, which is exactly why it is said rather than left for a test not to catch. `_edit_context`'s cost docstring now names it. |
| *(uncounted note)* | `test_two_editable_messages_render_no_duplicate_dom_id` asserts `<= 1`, which also passes at zero | Applied. Tightened to `== 1` for both the id and the `for=`, plus `== 3` for the suffixed per-turn ids, so the assertion additionally pins that the composer's own door is still there. |

**No round 3.** The re-check's own closing is that m10 is a sentence, n5 is one assertion and n6 is an argument, none of which should hold the branch; all three are applied above, and the plan is ready to execute.

### Carried into the plan from the same round

The branch's own post-cutover flow changed mid-review: `AGENTS.md` and `docs/DEV.md` now state pull request **into `dev`**, and the root checkout fast-forwarded to **`origin/dev`**. Both ladder rungs — Task 15 Step 3 and the Smoke Checklist's closing paragraph — name `dev` on both sides, and record that `main` takes only batched release pull requests from `dev` and is never on a feature branch's path.

## Self-review

Run by the author against the spec, before handing this over.

**1. Spec coverage.** §3.1–§3.6 → Tasks 1–4. §4.1–§4.8 → Tasks 5–10. §5.1–§5.7 → Tasks 11–14. §6 (routes, postures, gating) → the five classifications and their drivers, spread across Tasks 8, 10 and 13, plus the posture sweeps on every Phase B and C task. §7 (migrations) → Tasks 5 and 11, with the numbers **verified against the real tree** (0012 and 0013; `agents/migrations/` ends at `0011_turn_author.py`, and no other app gains one). §8 (budgets) → the pins in Tasks 3, 4, 8 and 10, with the two-query correction stated above. §9 (tests) → every bullet has a named test; the by-name under-count pins, the both-directions audience test, the install interaction, the four truth-table rows, the five share answers and the `select_related` pin are all present. §10 (documentation) → `agents/README.md` (Tasks 2, 5, 6, 11, 12), `agents/chat/README.md` (Tasks 3, 7, 13), `models/registry/README.md` (Task 1), `docs/EXTENDING.md` (Task 10), the ADR (Task 15). §11 (phasing and done-when) → the Smoke Checklist, verbatim from the spec's own done-when lists. §12–§14 are reasoning and scope, carried into docstrings rather than into tasks. §15–§16 are the review record and the owner's rulings, and are not re-litigated anywhere in this plan.

**2. Placeholders.** No "TBD", no "similar to Task N", no "add appropriate error handling", no "write tests for the above". **Every template step now ships real markup** — the first draft of this plan asserted that while leaving `agent_edit.html`, `agents.html` and `agents_admin.html` as one-sentence descriptions, which review M5 caught and which is fixed in Tasks 8 and 10. Three steps still carry a *read-this-first* instruction — the flash-fragment include name, the `settings_content` block name, and the picker's real query parameter — each naming the exact file to read and forbidding invention; those are verification instructions against the tree, not deferred decisions. The fourth such instruction, on `identity/audit.py::record`'s keyword, was **removed**: that function takes `**detail`, so there is no keyword to match and the instruction was asking for a check that could not fail.

**3. Type consistency.** `ContextUsage`/`MeterSegments` field names match between Task 2, Task 3's template and Task 4's helper. `turn_card`'s four feature-C keys (`may_edit`, `may_attach_files`, `attach_workstream`, `attach_id`) are produced in Task 13 Step 3 and read in Task 13 Step 5 under those same names, and the two builders that set them — `thread_cards` and `turn_group_cards` — take the same three keyword-only parameters. `entitlement_panel`'s nine keys are produced in Task 7 and consumed by Task 9's include under the `tp_*` names `foundation/templates/_transfer_panel.html` documents. `window_source` takes exactly the four declared constants, and `WINDOW_SOURCE_UNBOUND` is the view's own value, never returned by `effective_context_window`. `create_agent` returns `(row, errors)` and `update_agent` returns `errors` — different shapes, deliberately, because only one of them can fail to produce a row; both are used that way at every call site. `agent_form_context`'s keys are produced in Task 7 and consumed in Tasks 8, 9 and 10 under the same names. `may_edit_turn` and `may_edit_any_turn` share one body for the conversation-level half, so the card and the POST gate cannot disagree. `thread_cards`' two new keyword-only parameters have the same names as the two card keys they set.

---

## Amendment (2026-09-22) — what actually landed, task by task

Appended after Tasks 1–14 landed and were reviewed, in the shape this archive's other plans
use: the plan text above is left as written, because it is the record of what was planned and
reviewed, and **where it and this section disagree, this section is what landed on the
branch.** Nothing here is a claim about a merge or a deploy; neither has happened.

**The headline.** Task 6's audit detail keyword is **`fields=`, not the `was=`** the task body
above prints. `was=` already means "the previous value" on the workstream rename action, so
one keyword would have carried two meanings in one closed vocabulary; the review ordered the
rename and verified no reader of a `was` detail key exists. Every occurrence of `was=` in
Task 6's body is superseded by `fields=`.

**Two execution rulings, recorded because the plan's own text is stale.** Global Constraints
name a worktree directory and branch that predate the merge of the specs branch; execution
happened on branch `chat-cluster`, each task in its own isolated checkout. And the executor
database name in every printed command is the *queue* track's, not this one's: the chat
implementer substituted its own name throughout, because two concurrent pytest runs must
never share a database name.

**The ADR is 0019** (`docs/adr/0019-chat-cluster.md`), the next free number at the time, and it
carries a fourth thing Task 15's body does not ask for: a dated pointer amendment on
**ADR 0010**, recording that its `context_window` column gained a read direction. The
owner-ordered consolidation-audit rule landed in `AGENTS.md` under Merge readiness, cited once
from `CONTRIBUTING.md`. The browser items a green suite cannot prove are collected in
[`2026-09-21-chat-cluster-smoke.md`](2026-09-21-chat-cluster-smoke.md), beside this file; the
`## Smoke Checklist` above stays the feature-level walk, and that file is the per-item detail
the task reports asked for.

### Every reviewed deviation

One line each, in task order. All were reviewed; none is an open question. Task 1 and Task 11
shipped with none.

| Task | Deviation | Why |
|---|---|---|
| 2 | The agent-slug fixture collision was worked around in the test, not the writer | Accepted: the collision is the slug rule working. |
| 2 | The tool-call schema key asserted is `args` | Accepted: that is the key the schema really carries. |
| 2 | A `repr()`-length size proxy was **rejected** and replaced by an equality pin against the estimator | A counting estimator would have scored green on the proxy; equality cannot. |
| 3 | The `safe` filter on the two clause renders was **rejected**; four assertions re-pinned through the escaping helper | The repo's autoescape gate forbids opting out on a rendered path — and the two *negative* assertions were re-pinned too, or they would have passed vacuously. |
| 3 | The engine-default-sentence test sets the administrator-content keyword | A brief omission: without it the administrator 404s before the sentence exists. |
| 3 | The stream-read query pin excludes one precisely-identified pre-existing authorization read | That read predates the task and is not reachable from either file it touches. |
| 3 | An existing composer query pin moved from one standalone workstream read to zero | The widened `select_related` folds that read into the conversation's own join — a strict improvement, named in the commit body. |
| 4 | The "no rendered sentence on the wire" assertion became a count, not a substring | The mandated key name *contains* the forbidden substring, so the assertion was unsatisfiable as written. |
| 4 | The `select_related` pin became shape-based (no lazy agent or workstream read on either side), not a fixed delta | A fixed delta could not see a partial narrowing that moved both sides equally. |
| 4 | The thread page's test module joined the split-threshold exemption list | It had already crossed the gate one task earlier, unnoticed, because that task's targeted runs did not include the ops gate. See G10 in the ADR. |
| 5 | Three brief assertions wrapped in a non-open posture | On an open box everyone is owner-equivalent, so two of them were vacuous and one was unsatisfiable. |
| 5 | Fifteen pre-existing tests gained the audience flag beside the origin marker | Direct row builds bypass the data migration; no expected value changed, only fixture inputs. Four of the fifteen were found in the re-check, already vacuous. |
| 5 | Four stale passages naming the origin marker as the visibility leg were corrected | The leg moved; prose that still named the old one would have been read as the rule. |
| 6 | **`fields=` supersedes `was=`** | See the headline above. |
| 6 | The closed audit vocabulary's count pin moved by two, with a dated paragraph in its docstring | The drift guard doing its job; both new names reuse an existing family prefix. |
| 6 | The administrator-writes-a-role test now writes a *different* role | As drafted it set the value the row already held, so it proved no write. |
| 6 | Two guard tests added (an over-long name; the create path's audience field) | Both paths were unpinned. |
| 6 | The writer-side role vocabulary was parked to Task 8 rather than added here | The spec puts the check on the form side, and Task 8 is where the form lives. |
| 7 | The foreign-label sentence is computed whenever there is a row, not only when the panel has something to offer | A member who owns no entitlement has no panel and is exactly the reader who needs telling. Brief bug, fixed as ordered. |
| 8 | The role vocabulary became one shared function rather than a second filter in the view | A refusal keyed on a second spelling of what the select offers is the render-versus-gate bug in its other direction. |
| 8 | A named override set in the route matrix, for the one class-O cell an administrator always reaches | The manage predicate short-circuits on administrator-ness by design; bending it to fit the base mapping would have been the wrong fix. |
| 8 | List rows are dicts folded through one closure, not a lookup mapping | The template language has no item-lookup filter; the fold also makes the two batch reads once for the whole page. |
| 8 | Page-prefixed CSS class names, not the bare ones the brief printed | The bare names already mean something else under the settings shell. |
| 8 | The create route's route-matrix driver probes with a GET | A POST driver would create a row as a side effect of proving a class boundary; the existing precedent probes with a GET too. |
| 8 | An apostrophe-bearing row name asserted through the escaping helper; the reset warning pinned whole | Autoescape again; and the warning constant had never been proven to reach a page. |
| 8 (fix) | The read-only box-wide section's sentence is chosen **per row**, and an administrator gets a link | As first shipped it told an administrator on an open box that "an administrator can change it" and offered no way in — the owner's headline ask, with no door. |
| 9 | The foreign-label sentence renders outside the panel's own condition | Nested under the panel, its one intended reader would never see it. |
| 9 | The shared type-to-filter script include is gated on the panel | Keeps the zero-script pin on this page passing unchanged rather than weakening it. |
| 9 | Two brief tests strengthened | Both were vacuous as drafted — one asked "in neither pane" of a panel that did not render. |
| 9 | The filter-input rule was promoted with the transfer-panel block | The fragment's own pane filters write that class; leaving it behind would have rendered them unstyled on the new consumer. |
| 9 | The access pages' summary rule deliberately did **not** move | Not one of the fragment's classes, and both its consumers still share the settings shell. See G8 in the ADR. |
| 9 | Two CSS-ownership tests re-pinned to the new home, plus a "nothing left behind" assertion | The rules **moved**; a promotion that copied instead of moving now fails. |
| 9 | `docs/EXTENDING.md` was touched although the brief's add list did not name it | Two of its sentences were made stale by this commit. |
| 10 | **Six** settings-registration places, not four | The drift tests named two more — the help-card table (symmetric, with a rendered-anchor requirement) and the page-name table — and both are real build failures. |
| 10 | The page carries its own style block, with the parent block first | A five-column table with no rules is not legible beside the two settings pages doing the same job. |
| 10 (fix) | The entitlement read on **both** agent pages is gated on accounts being on; the column is hidden, never zeroed | Ungated, the count's meaning inverts on an open box. A zero would say "none" where the truth is "not asked". Both routes joined the zero-query mount sweep as a result. |
| 10 (fix) | A source-inspection test replaced by a direct call that must raise | The old assertion matched the module's own docstring and could not fail. |
| 10 | The Owner column still prints a principal key | Backlog by ruling: no display-name reader exists to call. See G1 in the ADR. |
| 12 | The import-law test walks the module's syntax tree instead of grepping its text | The grep was red on a module obeying the rule perfectly — the column's own docstrings name the chat package in prose. The walk also catches function-local imports, which a header grep cannot. |
| 12 | No audit row and no new action in the closed vocabulary | Ruled correct: a branch is a copy the principal could already make, and the sibling copier writes none either. |
| 12 | `duplicate_conversation` re-pinned to copy the turn author | A real fence bug, not a refactor: a duplicate used to replay another person's words to the model unfenced. Named in the commit body; recorded in the ADR as a behaviour change. |
| 12 (fix) | One private copier extracted under both public writers | The eleven-field copy had been typed twice, and had already drifted once — which is how the author fence was lost. |
| 13 | The disclosure's declared sentence lives in the chat service module, not the poll view | The read view may not import the poll view; the shared leaf both already import is where the column's other declared sentences live. |
| 13 | The polled body carries that sentence too | Without it the swapped disclosure would render with an empty lead — the reload/poll divergence four docstrings forbid. |
| 13 | The poller's source selector is attribute-only, not input-qualified | On a page with a picker the field is a `select`, which is exactly the page where the pick can differ from the default. |
| 13 | The edit view threads the already-fetched settings row into both the predicate and the writer | That keyword exists for this caller, and has its own pin. |
| 13 | Three brief tests rewritten | Two asserted strings that occur nowhere in the repository, so they were green before a line was written; the third could not pass, because the first branch's own queued placeholder correctly blocks a second edit. |
| 13 | The done-tick budget test compares threads of *different* lengths | Equality between two identical threads stays green on a per-turn predicate. |
| 13 | The poller carry has no mutation proof | There is no JavaScript engine in this suite; it is a browser smoke item instead. |
| 13 (fix) | The per-turn row rule extracted into one predicate both the card and the gate call | It had been spelled twice, with no test that could notice them disagreeing. |
| 13 (fix) | Each edit box gained a real label and a per-turn id | A fixed id would point every label at the first box in document order. |
| 14 | The tool-card test asserts the tool's registered label, not its dotted key | The renderer renders the label for any key the registry still knows; the raw key never appears. |
| 3, 13, 14 | The thread page's flat-cost equality pin was **extended** three times, never copied | Each feature that added a per-render read had to be inside the pin's own measurement, or the pin would have been measuring a page that never asks. |
