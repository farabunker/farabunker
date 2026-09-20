"""ToolSpec -> the tool-calling JSON an LLM reads (spec section 4.6)."""
from __future__ import annotations

import json

import pytest

from agents.contracts.tests._helpers import make_spec
from agents.contracts.tools import TOOL_PARAM_KINDS, all_tools
from agents.contracts.toolschema import (
    key_from_wire_name,
    mcp_tool_dict,
    openai_tool_dict,
    wire_name,
)
from models.contracts.operations import Param


class TestWireName:
    def test_a_dot_becomes_a_double_underscore(self):
        """The OpenAI function-name grammar is `^[a-zA-Z0-9_-]{1,64}$` and
        models are trained against it; a dotted name is a needless
        correctness risk even where a server tolerates it."""
        assert wire_name("rag.search") == "rag__search"

    def test_it_round_trips(self):
        for key in ("rag.search", "rag.ask", "vision.generate", "models.status"):
            assert key_from_wire_name(wire_name(key)) == key

    def test_the_emitted_name_fits_the_openai_grammar(self):
        import re

        for key in ("rag.search", "vision.operations", "models.status"):
            assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", wire_name(key))


class TestOpenAIToolDict:
    def test_the_envelope_shape(self):
        spec = make_spec(key="rag.search", description="Search the library.")
        rendered = openai_tool_dict(spec)
        assert rendered["type"] == "function"
        assert rendered["function"]["name"] == "rag__search"
        assert rendered["function"]["description"] == "Search the library."
        assert rendered["function"]["parameters"]["type"] == "object"

    def test_it_is_json_serialisable(self):
        """It is posted as JSON to an HTTP API. A tuple or a dataclass
        leaking in here fails at request time, in a turn, on a worker."""
        rendered = openai_tool_dict(make_spec(params=(
            Param("mode", "choice", "Mode", choices=("a", "b")),
            Param("n", "int", "N", min=1, max=9),
        )))
        assert json.loads(json.dumps(rendered)) == rendered

    def test_each_param_kind_maps_to_its_json_type(self):
        """Iterates `TOOL_PARAM_KINDS` (`agents/contracts/tools.py:43`),
        not a hardcoded list of the kinds that exist today, so a kind
        added there without a matching entry in `toolschema.py`'s own
        `_JSON_TYPES` fails this suite instead of silently going
        untested. Two ways that can happen, both caught here: `expected`
        below drifting out of sync with `TOOL_PARAM_KINDS` (the `assert
        set(expected) ==` below), or `expected` and `TOOL_PARAM_KINDS`
        agreeing while `_JSON_TYPES` itself lacks the entry (`openai_
        tool_dict`'s own `_JSON_TYPES[p.kind]` lookup raises `KeyError`)."""
        expected = {
            "text": "string", "seed": "string", "asset": "string",
            "choice": "string", "int": "integer", "float": "number",
        }
        assert set(expected) == TOOL_PARAM_KINDS, (
            "TOOL_PARAM_KINDS grew a kind this test doesn't know the "
            "expected JSON type for -- add it to `expected` above."
        )
        spec = make_spec(params=tuple(
            Param(kind, kind, kind.title()) for kind in sorted(TOOL_PARAM_KINDS)
        ))
        props = openai_tool_dict(spec)["function"]["parameters"]["properties"]
        for kind, json_type in expected.items():
            assert props[kind]["type"] == json_type

    def test_description_falls_back_to_label(self):
        """`description` is the entire prompt surface a model gets for an
        argument (ruling R2 half one -- the field must never be removed,
        renamed, or made optional-by-omission)."""
        spec = make_spec(params=(
            Param("a", "text", "Alpha", description="What alpha means."),
            Param("b", "text", "Bravo"),
        ))
        props = openai_tool_dict(spec)["function"]["parameters"]["properties"]
        assert props["a"]["description"] == "What alpha means."
        assert props["b"]["description"] == "Bravo"

    def test_a_fixed_choice_param_emits_an_enum(self):
        spec = make_spec(params=(Param("mode", "choice", "Mode", choices=("a", "b")),))
        props = openai_tool_dict(spec)["function"]["parameters"]["properties"]
        assert props["mode"]["enum"] == ["a", "b"]

    def test_an_engine_owned_choice_param_emits_no_enum(self):
        """A `"choice"` param whose options the ENGINE owns (empty
        `choices`) emits no `enum` -- an honest "the schema does not
        know", exactly as `operations.describe` does
        (operations.py:161-163). Never a guessed list."""
        spec = make_spec(params=(Param("sampler", "choice", "Sampler"),))
        props = openai_tool_dict(spec)["function"]["parameters"]["properties"]
        assert "enum" not in props["sampler"]

    def test_numeric_bounds_are_emitted_and_none_is_omitted(self):
        spec = make_spec(params=(
            Param("bounded", "int", "Bounded", min=1, max=50),
            Param("open", "float", "Open"),
        ))
        props = openai_tool_dict(spec)["function"]["parameters"]["properties"]
        assert props["bounded"]["minimum"] == 1
        assert props["bounded"]["maximum"] == 50
        assert "minimum" not in props["open"]
        assert "maximum" not in props["open"]

    def test_a_multiple_param_becomes_an_array(self):
        """`validate_params` answers a `multiple` param with a LIST
        (operations.py:335-341). The schema must say so, or the model
        sends a bare string and the tool silently runs with one adapter
        where several were meant."""
        spec = make_spec(params=(
            Param("loras", "asset", "LoRAs", asset_kind="lora", multiple=True,
                  description="Adapters to apply."),
        ))
        prop = openai_tool_dict(spec)["function"]["parameters"]["properties"]["loras"]
        assert prop["type"] == "array"
        assert prop["items"]["type"] == "string"
        assert prop["description"] == "Adapters to apply."

    def test_required_lists_exactly_the_required_params(self):
        spec = make_spec(params=(
            Param("q", "text", "Q", required=True),
            Param("k", "int", "K"),
        ))
        assert openai_tool_dict(spec)["function"]["parameters"]["required"] == ["q"]

    def test_a_no_param_tool_renders_an_empty_object_schema(self):
        rendered = openai_tool_dict(make_spec(params=()))["function"]["parameters"]
        assert rendered == {"type": "object", "properties": {}, "required": []}

    def test_it_never_consults_describer(self):
        """Ruling R3: the schema handed to the model is built from the
        STATIC `ToolSpec.params` alone, on every turn. A describer whose
        dotted path does not even resolve must make no difference."""
        plain = openai_tool_dict(make_spec(params=(Param("a", "text", "A"),)))
        with_describer = openai_tool_dict(make_spec(
            params=(Param("a", "text", "A"),),
            describer="nowhere.at.all.describe",
        ))
        assert plain == with_describer

    def test_it_emits_no_live_facts_keys(self):
        """`supported`/`unsupported_reason`/`options` are reserved for
        R2/Phase 1.55 and are unreachable now."""
        props = openai_tool_dict(make_spec(params=(Param("a", "text", "A"),)))
        serialised = json.dumps(props)
        for reserved in ("supported", "unsupported_reason", "options"):
            assert reserved not in serialised


class TestMcpToolDict:
    def test_it_emits_exactly_name_description_and_input_schema(self):
        spec = make_spec(key="rag.search", description="Search the library.")
        assert set(mcp_tool_dict(spec)) == {"name", "description", "inputSchema"}

    def test_the_name_is_the_wire_name(self):
        """MCP tool names travel the same function-name grammar an
        OpenAI-style call does: no dots. `wire_name` is the one place
        that mapping lives, and `key_from_wire_name` is its inverse."""
        assert mcp_tool_dict(make_spec(key="rag.search"))["name"] == "rag__search"

    def test_it_never_serializes_runner_or_describer(self):
        """Implementation, never contract -- `describe_tool`'s own rule
        (`agents/contracts/tools.py:424-429`), applied to the second
        adapter so the two cannot disagree about it."""
        rendered = repr(mcp_tool_dict(make_spec(
            key="a.b", runner="agents.tests._helpers.stub_runner", describer="x.y",
        )))
        assert "stub_runner" not in rendered and "x.y" not in rendered


class TestTheTwoAdaptersCannotDrift:
    """The drift pin the addendum asks for. Parametrized over EVERY
    registered spec rather than one hand-written example, so a tool
    added in a later phase is covered the day it is registered."""

    @pytest.mark.parametrize("spec", all_tools(), ids=lambda s: s.key)
    def test_input_schema_equals_the_openai_parameters_block(self, spec):
        assert mcp_tool_dict(spec)["inputSchema"] == (
            openai_tool_dict(spec)["function"]["parameters"]
        )

    @pytest.mark.parametrize("spec", all_tools(), ids=lambda s: s.key)
    def test_name_and_description_agree(self, spec):
        mcp = mcp_tool_dict(spec)
        fn = openai_tool_dict(spec)["function"]
        assert (mcp["name"], mcp["description"]) == (fn["name"], fn["description"])

    def test_the_parametrization_is_not_vacuous(self):
        """A registry that happened to be empty at collection time would
        make both tests above pass by looking at nothing."""
        assert len(all_tools()) >= 4   # the four non-vision v1 tools, flag-independent

    def test_the_pin_would_catch_a_real_divergence(self):
        """Anti-vacuous: build a spec, render both, and prove the
        comparison is actually comparing the schema and not two copies
        of the same object."""
        spec = make_spec(key="drift.check", params=(
            Param("query", "text", "Query", required=True, description="d"),
        ))
        schema = mcp_tool_dict(spec)["inputSchema"]
        assert schema["required"] == ["query"]
        assert schema["properties"]["query"]["type"] == "string"
