"""`agents/defaults.py` -- the shipped-defaults catalogue, the shared
flow-JSON validator, and `install_default` (ruling 2, 2026-08-28).

`TestTheDeclarations` covers `DEFAULT_AGENTS` (moved here, renamed from
`RESIDENT_AGENTS` -- see `agents/tests/test_resident.py` for what stays
in `agents/resident.py` itself). `TestValidateFlowJson` and
`TestTheSameValidatorGuardsRowsAndCatalogue` cover `validate_flow_json`,
the ONE rule both a `Flow` row and a `FlowSpec` are checked by.
`TestInstallDefault` covers the one row writer. `TestPurity`,
`TestTheCatalogueIsNotARegistry`, and `TestNothingWritesRowsByItself`
are the permanent structural guards ruling 2 needs to be more than a
preference.
"""
from __future__ import annotations

import ast
import subprocess

import pytest

from agents.contracts.tools import grantable_tools
from agents.defaults import (
    DEFAULT_AGENTS,
    DEFAULT_FLOWS,
    SETTINGS_SURFACE_SLUGS,
    FlowDeclarationError,
    FlowInput,
    FlowSpec,
    FlowStep,
    catalogue,
    default_for,
    install_default,
    missing_defaults,
    validate_flow_json,
)
from agents.tests._helpers import (  # noqa: F401
    REPO_ROOT,
    _is_test_file,
    isolated_tool_registry,
)
from identity.contracts.principals import OPEN_PRINCIPAL, Principal
from models.contracts.operations import Param

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]

_DEFAULTS_PY = REPO_ROOT / "agents" / "defaults.py"
_APPS_PY = REPO_ROOT / "agents" / "apps.py"


# --- The declarations -------------------------------------------------


class TestTheDeclarations:
    def test_the_chat_surface_agents_are_exactly_the_three_named_ones(self):
        """NAMED EXPLICITLY, NOT COUNTED (Task 6 follow-up). The
        catalogue itself carries a fourth entry now -- `settings-helper`
        (Task 5) -- which is a real `DEFAULT_AGENTS` member, just not a
        CHAT-SURFACE one (`SETTINGS_SURFACE_SLUGS`, Task 6). A bare
        `len(DEFAULT_AGENTS) == 3` would go red the moment the catalogue
        adds a settings-only default, for a reason that has nothing to
        do with what this test actually means to pin -- so it names the
        chat-surface set by subtracting `SETTINGS_SURFACE_SLUGS`
        instead."""
        chat_surface = {s.slug for s in DEFAULT_AGENTS} - SETTINGS_SURFACE_SLUGS
        assert chat_surface == {"general", "library", "illustrator"}

    def test_no_default_agent_grants_a_mutating_tool(self):
        """ADR 0010:266-276. Checked against `grantable_tools()` rather
        than a hand-written list, so a tool that BECOMES mutating later
        fails here instead of at somebody's install."""
        from agents.contracts.tools import all_tools

        grantable = {spec.key for spec in grantable_tools()}
        registered = {spec.key for spec in all_tools()}
        for spec in DEFAULT_AGENTS:
            for key in spec.tool_keys:
                if key in registered:
                    assert key in grantable, f"{spec.slug} grants mutating {key}"

    def test_every_declared_key_is_registered_or_a_known_gap(self):
        """A typo still fails; a legal gap does not. The known gaps are
        exactly the feature-flagged keys, listed here by hand so adding
        one is a decision rather than an accident.

        `agent.library`/`agent.illustrator` are deliberately NOT listed:
        `AgentsConfig.ready()` registers them from `DEFAULT_AGENTS`
        itself, so they are always registered on every install and
        belong in the REGISTERED set, not the tolerated-gap one.
        """
        from agents.contracts.tools import all_tools

        known_later = {"vision.operations", "vision.generate"}
        registered = {spec.key for spec in all_tools()}
        for spec in DEFAULT_AGENTS:
            for key in spec.tool_keys:
                assert key in registered or key in known_later, f"{spec.slug}: {key}"

    def test_general_delegates_to_the_other_two(self):
        general = next(s for s in DEFAULT_AGENTS if s.slug == "general")
        assert {"agent.library", "agent.illustrator"} <= set(general.tool_keys)

    def test_only_the_two_specialists_are_exposed_as_tools(self):
        """`general` is the root an operator talks to, not a subroutine.
        Exposing it would let a delegate delegate back to the whole
        system for no gain the shared budget does not already bound."""
        assert {s.slug for s in DEFAULT_AGENTS if s.as_tool} == {"library", "illustrator"}

    def test_no_system_prompt_names_a_model(self):
        """ADR 0010's third amendment: the platform never names a model
        for the operator. A default's prompt says what it DOES."""
        for spec in DEFAULT_AGENTS:
            lowered = spec.system_prompt.lower()
            for banned in ("gpt", "llama", "claude", "qwen", "mistral", "gemma"):
                assert banned not in lowered

    def test_default_flows_carries_library_brief(self):
        """Task 13: `library-brief` proves `flow.run` end to end -- the
        first (and today, only) entry of a catalogue that used to be
        empty."""
        assert {s.slug for s in DEFAULT_FLOWS} == {"library-brief"}

    def test_general_grants_flow_run(self):
        general = next(s for s in DEFAULT_AGENTS if s.slug == "general")
        assert "flow.run" in general.tool_keys

    def test_generals_prompt_never_refuses_a_benign_request_for_lack_of_documents(self):
        """ROUND 19 (owner, verbatim: "if I ask it for some python code,
        it shouldn't reject my request"). The load-bearing NOT-RAG-ONLY
        phrase, pinned by source text -- the doctrine is in the PROMPT;
        actual local-model compliance is a preview E2E concern, not this
        test's."""
        general = next(s for s in DEFAULT_AGENTS if s.slug == "general")
        assert "never a reason to refuse a benign request" in general.system_prompt
        assert "Writing code, explaining a concept, drafting text" in general.system_prompt

    def test_generals_prompt_states_the_honest_not_in_library_answer(self):
        """ROUND 19 (owner, verbatim: "it should specify that it can't
        find it in it's document framework but here is an answer")."""
        general = next(s for s in DEFAULT_AGENTS if s.slug == "general")
        assert "say so in one honest line" in general.system_prompt
        assert "answer from your own general knowledge" in general.system_prompt
        assert "plainly not attributed to any document" in general.system_prompt

    def test_generals_prompt_still_forbids_fabricated_citations_and_results(self):
        """ROUND 19's own honesty invariant, carried forward from the
        pre-round-19 "do not invent a result" line rather than dropped
        alongside it."""
        general = next(s for s in DEFAULT_AGENTS if s.slug == "general")
        assert "do not invent a result or a citation" in general.system_prompt
        assert "Do not describe calling a tool instead of calling it" in general.system_prompt

    def test_generals_prompt_still_names_rag_first_for_library_relevant_asks(self):
        """The doctrine's OTHER half: RAG-first is not dropped in favour
        of never-refuse -- a library-relevant question still searches
        and grounds first."""
        general = next(s for s in DEFAULT_AGENTS if s.slug == "general")
        assert "search first and ground your answer in what you find" in general.system_prompt

    def test_generals_description_says_what_it_does_not_what_it_is_permitted(self):
        """U8 (chat polish): the shipped catalogue's own copy, read
        verbatim by `chat/index.html`'s start form
        (`{{ agents.0.description }}`) -- "Conversation with every safe
        tool on this box" read like an internal permissions note, not an
        answer to "what can I ask this for". Only the CATALOGUE text
        changes here; an already-installed row keeps whatever its own
        `description` column says (`install_default` is create-if-
        absent) until an operator re-installs with `--reset`."""
        general = next(s for s in DEFAULT_AGENTS if s.slug == "general")
        assert general.description == (
            "Answers with the library, image generation and model status."
        )
        assert "safe tool on this box" not in general.description


class TestCatalogueAndDefaultFor:
    def test_catalogue_agent_is_default_agents(self):
        assert catalogue("agent") == DEFAULT_AGENTS

    def test_catalogue_flow_is_default_flows(self):
        assert catalogue("flow") == DEFAULT_FLOWS

    def test_catalogue_rejects_an_unknown_kind(self):
        with pytest.raises(ValueError):
            catalogue("robot")

    def test_default_for_finds_a_real_entry(self):
        spec = default_for("agent", "general")
        assert spec.slug == "general"

    def test_default_for_names_what_the_catalogue_holds_when_the_slug_is_unknown(self):
        with pytest.raises(ValueError) as exc:
            default_for("agent", "does-not-exist")
        message = str(exc.value)
        assert "does-not-exist" in message
        assert "general" in message and "library" in message and "illustrator" in message

    def test_missing_defaults_excludes_installed_slugs_case_insensitively(self):
        """`missing_defaults` answers from the WHOLE catalogue (Task 6
        follow-up): it is a catalogue-vs-installed question, not a
        chat-surface one, so `settings-helper` belongs in its answer
        exactly like any other not-yet-installed default. The chat-
        surface exclusion is a call-site concern
        (`agents/chat/views/conversations.py`'s own offers computation),
        never this function's -- narrowing it here would make it lie to
        a future non-chat caller that really does want every missing
        default."""
        missing = missing_defaults("agent", ["GENERAL"])
        assert {s.slug for s in missing} == {"library", "illustrator", "settings-helper"}

    def test_missing_defaults_returns_everything_when_nothing_is_installed(self):
        """Named explicitly rather than asserted against `DEFAULT_AGENTS`
        itself, so this test still means something the day a fifth
        default is added: the four the catalogue actually ships today,
        by name."""
        assert {s.slug for s in missing_defaults("agent", [])} == {
            "general", "library", "illustrator", "settings-helper",
        }


# --- validate_flow_json -------------------------------------------------


class TestValidateFlowJson:
    def test_it_requires_at_least_one_step(self):
        with pytest.raises(FlowDeclarationError):
            validate_flow_json([], [])

    def test_no_step_may_name_an_agent_tool(self):
        with pytest.raises(FlowDeclarationError):
            validate_flow_json([], [{"tool": "agent.library", "args": {}}])

    def test_no_step_may_name_a_flow_tool(self):
        """The recursion guard, closed at declaration time -- a flow
        calling an agent that could call the flow back is a cycle the
        shared step budget would only stop after spending it."""
        with pytest.raises(FlowDeclarationError):
            validate_flow_json([], [{"tool": "flow.other", "args": {}}])

    def test_a_reference_to_a_later_step_is_refused(self):
        steps = [
            {"tool": "rag.search", "args": {"query": "$steps.1.text"}},
            {"tool": "rag.ask", "args": {"query": "hi"}},
        ]
        with pytest.raises(FlowDeclarationError):
            validate_flow_json([], steps)

    def test_a_reference_to_the_same_step_is_refused(self):
        """§7.4: "resolvable against the steps that PRECEDE it" -- a
        step referencing its own not-yet-produced output is the same
        failure as referencing a later one."""
        steps = [{"tool": "rag.search", "args": {"query": "$steps.0.text"}}]
        with pytest.raises(FlowDeclarationError):
            validate_flow_json([], steps)

    def test_a_reference_to_an_earlier_step_is_fine(self):
        steps = [
            {"tool": "rag.search", "args": {"query": "hi"}},
            {"tool": "rag.ask", "args": {"query": "$steps.0.text"}},
        ]
        validate_flow_json([], steps)  # does not raise

    def test_an_input_reference_must_name_a_declared_input(self):
        with pytest.raises(FlowDeclarationError):
            validate_flow_json([], [{"tool": "rag.search", "args": {"query": "$input.topic"}}])

    def test_an_input_reference_to_a_declared_input_is_fine(self):
        inputs = [{"key": "topic", "label": "Topic"}]
        steps = [{"tool": "rag.search", "args": {"query": "$input.topic"}}]
        validate_flow_json(inputs, steps)  # does not raise

    def test_a_reference_may_target_text_data_or_artifacts(self):
        """`ToolResult`'s own three fields -- `agents.contracts.tools.
        ToolResult`."""
        steps = [
            {"tool": "rag.search", "args": {"query": "hi"}},
            {
                "tool": "rag.ask",
                "args": {
                    "text": "$steps.0.text",
                    "data": "$steps.0.data",
                    "artifacts": "$steps.0.artifacts",
                },
            },
        ]
        validate_flow_json([], steps)  # does not raise

    def test_a_reference_nested_in_a_list_is_validated(self):
        steps = [{"tool": "rag.search", "args": {"tags": ["ok", "$input.missing"]}}]
        with pytest.raises(FlowDeclarationError):
            validate_flow_json([], steps)

    def test_a_reference_nested_in_a_dict_is_validated(self):
        steps = [{"tool": "rag.search", "args": {"filter": {"topic": "$input.missing"}}}]
        with pytest.raises(FlowDeclarationError):
            validate_flow_json([], steps)

    def test_a_reference_nested_inside_both_a_list_and_a_dict_is_validated(self):
        steps = [{"tool": "rag.search", "args": {"filters": [{"topic": "$input.missing"}]}}]
        with pytest.raises(FlowDeclarationError):
            validate_flow_json([], steps)

    def test_a_dollar_prefixed_value_that_is_not_a_reference_is_a_legal_value(self):
        """`"$5.00"` is a VALUE, not a reference -- a validator that
        refused it would make a whole class of legitimate argument
        undeclarable."""
        steps = [{"tool": "rag.search", "args": {"query": "It costs $5.00"}}]
        validate_flow_json([], steps)  # does not raise

    def test_a_malformed_step_reference_raises(self):
        steps = [{"tool": "rag.search", "args": {"query": "$steps.0"}}]
        with pytest.raises(FlowDeclarationError):
            validate_flow_json([], steps)

    def test_a_malformed_input_reference_raises(self):
        steps = [{"tool": "rag.search", "args": {"query": "$input"}}]
        with pytest.raises(FlowDeclarationError):
            validate_flow_json([], steps)

    def test_it_accepts_flowinput_and_flowstep_instances_directly(self):
        """`FlowSpec.__post_init__` passes dataclass instances, not
        dicts -- the same validator must accept both shapes."""
        validate_flow_json(
            [FlowInput(key="topic", label="Topic")],
            [FlowStep(tool="rag.search", args={"query": "$input.topic"})],
        )  # does not raise


class TestTheSameValidatorGuardsRowsAndCatalogue:
    def test_a_flow_row_and_a_flowspec_raise_the_identical_message_for_the_same_bad_json(self):
        from agents.models import Flow

        bad_steps = [{"tool": "agent.library", "args": {}}]
        with pytest.raises(FlowDeclarationError) as row_exc:
            Flow.objects.create(slug="bad-flow-row", name="Bad", steps=bad_steps)

        with pytest.raises(FlowDeclarationError) as spec_exc:
            FlowSpec(
                slug="bad-flow-spec", name="Bad", description="",
                inputs=(), steps=(FlowStep(tool="agent.library"),),
            )

        assert str(row_exc.value) == str(spec_exc.value)


# --- install_default ------------------------------------------------------


class TestInstallDefault:
    def test_it_creates_the_row_and_reports_created_true(self):
        row, created = install_default("agent", "general", OPEN_PRINCIPAL)
        assert created is True
        assert row.slug == "general"
        assert row.resident is True
        assert row.enabled is True

    def test_a_second_call_reports_created_false_and_the_row_is_byte_identical(self):
        from agents.models import Agent

        row, _ = install_default("agent", "general", OPEN_PRINCIPAL)
        first_updated_at = Agent.objects.get(pk=row.pk).updated_at

        row_again, created_again = install_default("agent", "general", OPEN_PRINCIPAL)

        assert created_again is False
        assert row_again.updated_at == first_updated_at

    def test_an_operator_edit_survives_a_reinstall(self):
        from agents.models import Agent

        install_default("agent", "general", OPEN_PRINCIPAL)
        Agent.objects.filter(slug="general").update(system_prompt="the operator's own words")

        install_default("agent", "general", OPEN_PRINCIPAL)

        assert Agent.objects.get(slug="general").system_prompt == "the operator's own words"

    def test_reset_restores_the_shipped_text_and_logs(self, caplog):
        import logging

        from agents.models import Agent

        spec = default_for("agent", "general")
        install_default("agent", "general", OPEN_PRINCIPAL)
        Agent.objects.filter(slug="general").update(system_prompt="drifted")

        with caplog.at_level(logging.INFO):
            row, created = install_default("agent", "general", OPEN_PRINCIPAL, reset=True)

        assert created is False
        row.refresh_from_db()
        assert row.system_prompt == spec.system_prompt
        assert any("general" in record.getMessage() for record in caplog.records)

    def test_the_row_is_stamped_with_the_installing_principal(self):
        principal = Principal("user_agent", "someone-else")
        row, _ = install_default("agent", "library", principal)
        assert (row.owner_kind, row.owner_key) == ("user_agent", "someone-else")

    def test_an_unknown_slug_raises_naming_what_the_catalogue_holds(self):
        with pytest.raises(ValueError) as exc:
            install_default("agent", "does-not-exist", OPEN_PRINCIPAL)
        message = str(exc.value)
        assert "does-not-exist" in message
        assert "general" in message

    def test_resident_is_an_origin_marker_not_a_lock(self):
        """RULING 3: the row is editable afterwards, exactly like any
        other agent."""
        from agents.models import Agent

        row, _ = install_default("agent", "general", OPEN_PRINCIPAL)
        assert row.resident is True

        row.system_prompt = "an entirely ordinary edit"
        row.save()  # must not raise

        assert Agent.objects.get(slug="general").system_prompt == "an entirely ordinary edit"

    def test_it_installs_a_flow_default_too(self, monkeypatch):
        import agents.defaults as defaults_module
        from agents.models import Flow

        test_flow = FlowSpec(
            slug="test-flow-install", name="Test flow", description="a test flow",
            inputs=(FlowInput(key="topic", label="Topic"),),
            steps=(FlowStep(tool="rag.search", args={"query": "$input.topic"}),),
        )
        monkeypatch.setattr(defaults_module, "DEFAULT_FLOWS", (test_flow,))

        row, created = install_default("flow", "test-flow-install", OPEN_PRINCIPAL)

        assert created is True
        stored = Flow.objects.get(slug="test-flow-install")
        assert stored.steps == [{"tool": "rag.search", "args": {"query": "$input.topic"}}]
        assert stored.inputs == [
            {"key": "topic", "label": "Topic", "description": "", "required": False}
        ]

    def test_a_flow_reinstall_after_an_operator_edit_leaves_the_edit_alone(self, monkeypatch):
        import agents.defaults as defaults_module
        from agents.models import Flow

        test_flow = FlowSpec(
            slug="test-flow-reinstall", name="Test flow", description="",
            inputs=(), steps=(FlowStep(tool="rag.search", args={"query": "hi"}),),
        )
        monkeypatch.setattr(defaults_module, "DEFAULT_FLOWS", (test_flow,))

        install_default("flow", "test-flow-reinstall", OPEN_PRINCIPAL)
        Flow.objects.filter(slug="test-flow-reinstall").update(description="operator's own")

        install_default("flow", "test-flow-reinstall", OPEN_PRINCIPAL)

        assert Flow.objects.get(slug="test-flow-reinstall").description == "operator's own"


# --- library-brief, driven end to end through invoke_tool -----------------


class TestLibraryBriefFlow:
    def test_the_installed_rows_steps_round_trip_through_parse_flow_json_unchanged(self):
        from agents.defaults import parse_flow_json

        spec = default_for("flow", "library-brief")
        row, _ = install_default("flow", "library-brief", OPEN_PRINCIPAL)
        inputs, steps = parse_flow_json(row.inputs, row.steps)
        assert steps == list(spec.steps)
        assert inputs == list(spec.inputs)

    def test_it_runs_with_topic_alone_and_neither_step_raises(self):
        """M7, and `library-brief`'s most ordinary call: `category` is an
        optional declared input, so `$input.category` resolves to `None`
        rather than raising -- the case a naive `input[key]` lookup
        breaks. Driven through `invoke_tool` with stub `rag.*` runners,
        so this proves the real prompt-facing seam (`flow.run` ->
        `run_flow` -> `_run_steps` -> `resolve_args`) rather than
        `_run_steps` in isolation.
        """
        from agents.contracts.tools import ToolSpec, get_tool, register_tool
        from agents.runtime.flowtool import FLOW_RUN
        from agents.runtime.invoke import invoke_tool
        from agents.tests._helpers import CALLS, make_tool_ctx

        register_tool(FLOW_RUN)
        for key, required_key in (("rag.search", "query"), ("rag.ask", "question")):
            register_tool(ToolSpec(
                key=key, label=key, description="stub",
                params=(
                    Param(required_key, "text", required_key, required=True),
                    Param("category", "text", "Category"),
                ),
                runner="agents.tests._helpers.stub_runner",
            ))
        install_default("flow", "library-brief", OPEN_PRINCIPAL)
        CALLS.clear()

        outcome = invoke_tool(
            get_tool("flow.run"),
            {"flow": "library-brief", "input": '{"topic": "attention"}'},
            make_tool_ctx(),
        )

        assert not outcome.failed
        assert len(CALLS) == 2
        for args, _ctx in CALLS:
            assert args["category"] is None


# --- Purity: agents/defaults.py imports no Django (module scope) and ------
# --- no tools.* anywhere ---------------------------------------------------


class TestPurity:
    def _tree(self):
        return ast.parse(_DEFAULTS_PY.read_text())

    def test_no_django_import_at_module_scope(self):
        """`install_default` imports Django INSIDE its own body, which is
        what lets `Flow.save()` and `AppConfig`-adjacent code import the
        declarations without paying for the app registry."""
        for node in self._tree().body:
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("django")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("django")

    def test_no_tools_import_anywhere(self):
        """Import-law rule 3, walked over the WHOLE tree (`ast.walk`,
        not just `tree.body`) so a lazy in-body import is caught too."""
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "tools" and not node.module.startswith("tools.")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "tools" and not alias.name.startswith("tools.")

    def test_the_module_scope_gate_would_actually_catch_a_django_import(self):
        planted = ast.parse("from django.db import models\n")
        found = any(
            isinstance(node, ast.ImportFrom) and node.module
            and node.module.startswith("django")
            for node in planted.body
        )
        assert found

    def test_the_whole_tree_gate_would_actually_catch_a_lazy_tools_import(self):
        lazy = "def f():\n    from tools.rag import retrieval\n    return retrieval\n"
        found = any(
            isinstance(node, ast.ImportFrom) and node.module
            and node.module.startswith("tools.")
            for node in ast.walk(ast.parse(lazy))
        )
        assert found


# --- The catalogue is not a registry ---------------------------------------


class TestTheCatalogueIsNotARegistry:
    def test_defaults_py_contains_no_register_tool_call(self):
        tree = ast.parse(_DEFAULTS_PY.read_text())
        calls = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "register_tool" not in calls

    def test_apps_ready_does_not_import_defaults_py(self):
        tree = ast.parse(_APPS_PY.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "agents.defaults"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "agents.defaults"

    def test_the_gate_would_actually_catch_a_register_tool_call(self):
        planted = ast.parse("register_tool(spec)\n")
        calls = {
            node.func.id for node in ast.walk(planted)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "register_tool" in calls


# --- Nothing writes a row by itself ----------------------------------------


_ALLOWED_INSTALL_DEFAULT_CALLERS = frozenset({
    "agents/management/commands/install_defaults.py",
    "agents/chat/views/defaults.py",
})


def _install_default_call_sites() -> dict[str, int]:
    out = subprocess.run(
        ["git", "ls-files", "--", "agents"], cwd=REPO_ROOT,
        capture_output=True, text=True, check=True,
    )
    hits: dict[str, int] = {}
    for relative in out.stdout.splitlines():
        if not relative.endswith(".py") or _is_test_file(relative):
            continue
        tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
        count = sum(
            1 for node in ast.walk(tree)
            if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id == "install_default")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "install_default")
            )
        )
        if count:
            hits[relative] = count
    return hits


class TestNothingWritesRowsByItself:
    def test_install_default_is_called_only_from_the_allowed_sites(self):
        """`install_default` is called from at most the two places
        ruling 2 names: `agents/management/commands/install_defaults.py`
        and `agents/chat/views/defaults.py` (the Add button) -- both
        allowed call sites exist in the tree today; the guard remains an
        upper bound, not a promise that both must exist."""
        offenders = {
            path: count for path, count in _install_default_call_sites().items()
            if path not in _ALLOWED_INSTALL_DEFAULT_CALLERS
        }
        assert offenders == {}, offenders

    def test_at_least_one_allowed_site_actually_calls_it(self):
        """Anti-vacuous: a guard that allows an empty set of call sites
        while none exist proves nothing about the ones that DO exist."""
        assert set(_install_default_call_sites()) & _ALLOWED_INSTALL_DEFAULT_CALLERS

    def test_apps_py_never_calls_install_default(self):
        assert "agents/apps.py" not in _install_default_call_sites()

    def test_the_gate_would_actually_catch_a_call_site(self):
        planted = ast.parse("install_default('agent', 'x', principal)\n")
        found = any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "install_default"
            for node in ast.walk(planted)
        )
        assert found


# --- `manage.py install_defaults` ------------------------------------------


class TestTheInstallDefaultsCommand:
    def test_it_installs_every_default_and_reports_created(self, capsys):
        """Named explicitly rather than counted (Task 6 follow-up): the
        catalogue now ships four defaults, one of them
        (`settings-helper`) off the chat surface but still a resident
        row `install_defaults` must create like any other -- a bare
        `count() == 3` would have gone red the day Task 5 added it for a
        reason unrelated to this command's own contract, which is
        "every catalogue entry gets a row", not "exactly three rows"."""
        from django.core.management import call_command

        from agents.models import Agent

        call_command("install_defaults")

        assert set(Agent.objects.filter(resident=True).values_list("slug", flat=True)) == {
            "general", "library", "illustrator", "settings-helper",
        }
        out = capsys.readouterr().out
        assert "general" in out
        assert "created" in out

    def test_running_it_twice_reports_already_present_and_writes_nothing_new(self, capsys):
        from django.core.management import call_command

        from agents.models import Agent

        call_command("install_defaults")
        call_command("install_defaults")

        assert Agent.objects.filter(slug="general").count() == 1
        out = capsys.readouterr().out
        assert "already present" in out

    def test_it_stamps_rows_with_open_principal(self):
        from django.core.management import call_command

        from agents.models import Agent

        call_command("install_defaults")

        general = Agent.objects.get(slug="general")
        assert (general.owner_kind, general.owner_key) == ("open", "box")

    def test_reset_restores_the_shipped_text_over_an_edit(self):
        from django.core.management import call_command

        from agents.models import Agent

        call_command("install_defaults")
        Agent.objects.filter(slug="general").update(system_prompt="drifted")

        call_command("install_defaults", reset="general")

        spec = default_for("agent", "general")
        assert Agent.objects.get(slug="general").system_prompt == spec.system_prompt

    def test_reset_of_an_unknown_slug_raises_a_command_error(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("install_defaults", reset="does-not-exist")

    def test_the_command_module_never_constructs_a_principal(self):
        """`identity.contracts.principals.OPEN_PRINCIPAL` is IMPORTED,
        never constructed here -- the same object `identity.request.
        principal_for_request` hands out, so a row installed from the
        shell and one installed from the page are owned identically."""
        path = REPO_ROOT / "agents" / "management" / "commands" / "install_defaults.py"
        tree = ast.parse(path.read_text())
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "Principal"
        ]
        assert calls == []
