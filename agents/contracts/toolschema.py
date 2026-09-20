"""A `ToolSpec` as the tool-calling JSON both Ollama's HTTP API and every
OpenAI-compatible API accept -- and the wire-name round trip back.

Verified against the installed packages (llama-index-core 0.14.24,
llama-index-llms-ollama 0.10.1, ollama 0.6.2): all four chat paths pop
`tools` from kwargs and forward it to the SDK, and the SDK's `tools`
parameter accepts raw mappings. So this design emits the dict itself and
calls `llm.chat(messages, tools=[...])`.

It deliberately does NOT build `FunctionTool` objects and does NOT use
`predict_and_call`:

1. `FunctionTool.from_defaults` requires a LIVE Python callable and
   synthesizes a pydantic schema from it -- which would force an eager
   import of every tool module every time a prompt is built, the exact
   property `ToolSpec.runner`'s dotted-path design exists to avoid.
2. It would install pydantic as a second validation floor beside
   `validate_params`, which ADR 0012:676-679 rules out.
3. `predict_and_call` runs the tool itself, which would hide the step
   budget, the depth guard, and the recovery policy this design owns.

Pure: no Django, no I/O, no engine.
"""
from __future__ import annotations

from agents.contracts.tools import ToolSpec

_JSON_TYPES = {
    "text": "string", "seed": "string", "asset": "string",
    "choice": "string", "int": "integer", "float": "number",
}


def wire_name(key: str) -> str:
    """`"rag.search"` -> `"rag__search"`.

    The OpenAI function-name grammar is `^[a-zA-Z0-9_-]{1,64}$` and models
    are trained against it; a dotted name is a needless correctness risk
    even where a server tolerates it. `ToolSpec.__post_init__` rejects a
    key containing `"__"`, which is what makes this round trip
    unambiguous.
    """
    return key.replace(".", "__")


def key_from_wire_name(name: str) -> str:
    """The inverse. A name that round-trips through neither direction is
    an unknown tool -- handled as a tool error (section 10.3), never
    guessed at, fuzzy-matched, or silently ignored."""
    return name.replace("__", ".")


def _input_schema(spec: ToolSpec) -> dict:
    """`spec.params` as one JSON Schema object.

    The single schema builder BOTH adapters call. Factored out when the
    second adapter arrived, not written speculatively: what a tool
    accepts must not depend on who is asking, or an external caller and
    an internal prompt disagree about the same tool and the
    disagreement surfaces as a validation error nobody can reproduce.

    Built from the STATIC `spec.params` alone. It does not consult
    `spec.describer` and does not filter params by any live `supported`
    fact (ruling R3).
    """
    props: dict[str, dict] = {}
    required: list[str] = []
    for p in spec.params:
        prop: dict = {"type": _JSON_TYPES[p.kind], "description": p.description or p.label}
        if p.kind == "choice" and p.choices:
            prop["enum"] = list(p.choices)
        if p.kind in ("int", "float"):
            if p.min is not None:
                prop["minimum"] = p.min
            if p.max is not None:
                prop["maximum"] = p.max
        if p.multiple:
            # `validate_params` answers a `multiple` param with a LIST
            # (operations.py:335-341). The schema must say so, or the
            # model sends a bare string and the tool silently runs with
            # one adapter where several were meant.
            prop = {"type": "array", "items": prop, "description": prop["description"]}
        props[p.key] = prop
        if p.required:
            required.append(p.key)
    return {"type": "object", "properties": props, "required": required}


def openai_tool_dict(spec: ToolSpec) -> dict:
    """`spec` as the tool-calling JSON an LLM reads.

    Output is unchanged from P1; only the schema half moved into
    `_input_schema`, which `mcp_tool_dict` now shares.
    """
    return {
        "type": "function",
        "function": {
            "name": wire_name(spec.key),
            "description": spec.description,
            "parameters": _input_schema(spec),
        },
    }


def mcp_tool_dict(spec: ToolSpec) -> dict:
    """`spec` as an MCP `tools/list` entry.

    The SAME schema object `openai_tool_dict` puts under
    `function.parameters`, under MCP's own key name. Two adapters, one
    builder -- pinned by `test_toolschema.py`'s drift test over every
    registered spec.

    Written in P2 although the MCP EDGE is a later phase (2026-08-27
    addendum). The point of writing it now is that there is currently
    one schema builder to share; writing it after an edge exists would
    mean reconciling two that had already drifted. `agents/contracts`
    stays transport-agnostic: this is an adapter, and the HTTP views,
    session handling, and auth check that would use it live outside
    this package entirely.

    `runner` and `describer` are not serialized, exactly as
    `describe_tool` refuses to serialize them: one is a dotted path into
    this codebase and the other is reserved, and neither belongs in a
    contract handed to an external caller.
    """
    return {
        "name": wire_name(spec.key),
        "description": spec.description,
        "inputSchema": _input_schema(spec),
    }
