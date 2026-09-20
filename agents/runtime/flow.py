"""Running a declared flow: the same tool contract, no model.

Every step goes through the SAME `agents.runtime.invoke.invoke_tool`
an agent's loop uses -- same validation floor, same audit row, same
shared `StepBudget`, same depth, same five outcome classes. That is
what makes "a flow is the tool contract with no LLM" a fact about the
code rather than a claim about it.

A FLOW SPENDS NO STEPS. A step of the budget is an LLM CALL, and a flow
makes none; one whole flow is one step of whatever loop invoked it.
What it does obey is the DEADLINE, checked before each step -- the same
place and the same rule `agents/runtime/loop.py` checks it, and never
DURING a step, for the post-deadline-latency reason that module's
docstring gives in full.

A DEADLINE MID-FLOW IS `degraded`, NOT A FAILURE. The steps that ran
really ran; throwing their output away because the wall clock moved
would lose work that was actually done. The result says which step it
stopped at and carries the last completed step's own text, with
`data["degraded"] = True` -- the opt-in flag `invoke.py` classifies.
This module is that outcome class's first shipped producer.

A REFERENCE THAT DOES NOT RESOLVE RAISES, NAMING ITSELF. Never `None`
silently substituted: a silently-empty prompt is exactly the failure a
flow exists to prevent (spec section 6.5).

THE REFERENCE GRAMMAR IS `agents.defaults`'S OWN, EXACTLY --
`"$input.<key>"` and `"$steps.<N>.<field>"` (plural "steps", dotted
before the index -- the spec's own spelling, `docs/superpowers/specs/
2026-08-25-agents-and-tools-design.md:1490-1504,2144`).
`agents.defaults.validate_flow_json`'s `_STEP_REF` regex checks a row
against this shape at DECLARATION time (`Flow.save()`); this module
resolves the SAME shape at RUN time, against the same `FlowStep.args` a
row and a shipped default share. The two must agree byte-for-byte,
because `_run_steps` parses a row's JSON through
`agents.defaults.parse_flow_json` -- the identical function
`Flow.save()` calls -- on every run: a reference spelled any other way
would already have been refused when the row was saved, and one spelled
this way must actually resolve, or a legal row would fail every time it
ran.

IT IMPORTS NO `tools.*` MODULE. Every step's runner is reached through
`invoke_tool`, which resolves a dotted-path string (import-law rule 3).
"""
from __future__ import annotations

from agents.contracts.tools import ToolResult

# The one prefix `resolve_ref`/`resolve_args` recognise as an ATTEMPTED
# reference -- the same gate `agents.defaults._validate_ref_string` uses
# at declaration time: a string starting with "$input" or "$steps" must
# resolve fully or raise; anything else starting with "$" ("$5.00") is a
# plain value, never even offered to `resolve_ref`.
_REF = "$"
_ATTEMPTED_REF = ("$input", "$steps")


class FlowReferenceError(ValueError):
    """A `$`-prefixed reference in a step's `args` did not resolve.

    Raised naming the reference itself -- never `None` silently
    substituted (spec section 6.5): a resolver failure nobody can locate
    is barely better than a silently-empty prompt.
    """


def resolve_ref(ref: str, *, input: dict, steps: list) -> object:
    """One reference, resolved against the inputs and the completed
    steps.

    `"$input.<key>"` -> that input value.
    `"$steps.<N>.text"` -> step N's `ToolResult.text`.
    `"$steps.<N>.data.<path>"` -> a dotted walk into step N's `data`,
    where an all-digit segment indexes a list.
    `"$steps.<N>.artifacts.<i>"` -> one artifact reference string.
    Raises `FlowReferenceError` naming the reference -- never `None`.

    THE GRAMMAR IS `agents.defaults`'S OWN (see this module's docstring):
    `"$steps.<N>.<field>"`, plural, dotted before the index -- the exact
    shape `agents.defaults._STEP_REF` checks at declaration time, because
    `_run_steps` parses a row through the SAME
    `agents.defaults.parse_flow_json` `Flow.save()` calls, on every run.

    TWO CASES THAT LOOK ALIKE AND ARE NOT (M7). An UNDECLARED input key
    is a declaration bug and RAISES. A DECLARED BUT UNSUPPLIED optional
    input resolves to its default and does not. The discriminator is
    membership in `input`, which is why `_run_steps` seeds that dict from
    the flow's `inputs` -- every declared key present, unsupplied ones
    holding `None` -- rather than passing the caller's args straight
    through. Without that seeding the two cases are indistinguishable and
    an optional input would raise the moment somebody left it out.

    An all-digit path segment INDEXES A LIST, which is what makes
    `$steps.0.data.results.0.title` -- the shape a search result
    actually has -- expressible at all.

    Every miss raises `FlowReferenceError` naming the reference AND the
    part of it that failed. A resolver failure nobody can locate is
    barely better than the silent `None` this refuses to return.
    """
    body = ref[len(_REF):]
    head, _, rest = body.partition(".")
    if head == "input":
        if rest not in input:
            raise FlowReferenceError(
                f"{ref!r} names no declared input of this flow."
            )
        return input[rest]
    if head != "steps":
        raise FlowReferenceError(f"{ref!r} is not a flow reference.")
    number, _, tail = rest.partition(".")
    if not number.isdecimal() or int(number) >= len(steps):
        raise FlowReferenceError(
            f"{ref!r} names step {number!r}, but only {len(steps)} step(s) have run."
        )
    result = steps[int(number)]
    field_name, _, path = tail.partition(".")
    if field_name == "text":
        return result.text
    if field_name == "data":
        return _walk(ref, result.data, path)
    if field_name == "artifacts":
        return _walk(ref, list(result.artifacts), path)
    raise FlowReferenceError(
        f"{ref!r} reads {field_name!r}, but a step result has text, data, artifacts."
    )


def _walk(ref: str, value, path: str):
    """A dotted walk, where an ALL-DIGIT segment indexes a list.

    That one rule is what makes `$steps.0.data.results.0.title` -- the
    shape a search result actually has -- expressible at all, and it
    costs a dict nothing: a JSON object's keys are strings, so a digit
    segment is never ambiguous against one.
    """
    if not path:
        return value
    for segment in path.split("."):
        try:
            value = value[int(segment)] if segment.isdecimal() else value[segment]
        except (KeyError, IndexError, TypeError) as exc:
            raise FlowReferenceError(
                f"{ref!r} could not be resolved: {segment!r} is not there."
            ) from exc
    return value


def resolve_args(args: dict, *, input: dict, steps: list) -> dict:
    """`args` with every `$` reference replaced, at ANY nesting depth.

    Walks lists and dicts for the same reason
    `agents.defaults`'s own reference walk does at validation time (the
    one `validate_flow_json` runs over a row's JSON and over a
    `FlowSpec` alike): a reference one level down is still a
    reference, and the two walks must agree about what counts as one,
    or a row passes `Flow.save()` and then fails to run.

    A value is an ATTEMPTED reference under the same gate
    `agents.defaults._validate_ref_string` uses at declaration time --
    it starts with `"$input"` or `"$steps"`. Anything else starting with
    `"$"` (`"$5.00"`) is a plain value, left alone; the two walks must
    agree about what counts as an attempt, or a row that passed
    `Flow.save()` could still be silently mis-resolved here.
    """
    def _one(value):
        if isinstance(value, str):
            return resolve_ref(value, input=input, steps=steps) \
                if value.startswith(_ATTEMPTED_REF) else value
        if isinstance(value, dict):
            return {k: _one(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_one(v) for v in value]
        return value

    return {key: _one(value) for key, value in (args or {}).items()}


def run_flow(args: dict, ctx) -> ToolResult:
    """The runner behind the ONE registered `flow.run` tool (ruling 1).

    Loads the ROW named by `args["flow"]`, through
    `agents.visibility.visible_flows` -- the one place this column
    answers "which flows may this principal run" (ruling 4c). NOT the
    shipped catalogue: `agents/defaults.py` describes what the platform
    OFFERS and validates row JSON, and a runner that could execute a
    catalogue entry would make an uninstalled offer runnable and give
    the row a rival source of truth.

    `args["input"]` carries the flow's own inputs AS A JSON STRING, and
    is parsed here. That is not a compromise, it is what the schema can
    actually express: the tool contract has no object param kind and
    every kind maps to a JSON scalar, so a `"text"` param carrying JSON
    is the honest shape rather than a nested schema this contract
    cannot render. A dict is ALSO accepted unchanged -- an engine that
    hands back a parsed object should not be punished for being better
    than the wire format. Anything else, or malformed JSON, is a
    `ParamError` naming `input`, which is the repairable class section
    10.2 grants a retry for: a model that gets its own JSON slightly
    wrong can fix it when told which argument failed.

    A slug that names no visible, enabled row is `ToolRefused`, no
    retry. The model was handed an `enum` of real slugs, so
    reaching here means the row was disabled or deleted between the
    prompt and the call -- a race, and nothing the model can say brings
    it back.
    """
    from agents.contracts.tools import ToolRefused
    from agents.visibility import visible_flows

    slug = str(args.get("flow") or "").strip()
    flow = visible_flows(ctx.principal).filter(slug__iexact=slug).first()
    if flow is None:
        raise ToolRefused(
            f"There is no enabled flow called {slug!r} on this system."
        )
    return _run_steps(flow, _parsed_input(args.get("input"), flow), ctx)


def _parsed_input(raw, flow) -> dict:
    """`args["input"]` as a dict.

    A JSON STRING is the wire shape (see `run_flow`'s docstring: the
    tool contract has no object param kind). A dict passes through --
    an engine that already parsed it is not wrong. `None` or blank is
    an empty dict, because a flow whose inputs are all optional is
    legitimately called with none.

    `ParamError`, not `ToolRefused`: bad JSON is exactly the repairable
    failure section 10.2 grants one retry for, and `.errors` maps the
    param key to a reason the model can act on
    (`models/contracts/operations.py:228-236`).
    """
    import json

    from models.contracts.operations import ParamError

    if raw in (None, ""):
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise ParamError({"input": (
                f"Could not read the inputs for {flow.slug!r} as JSON ({exc}). "
                f'Send an object like {{"topic": "..."}}.'
            )}) from None
        if isinstance(parsed, dict):
            return parsed
    raise ParamError({"input": (
        f"{flow.slug!r} takes its inputs as a JSON object of named values, "
        f"not a bare value."
    )})


def _run_steps(flow, inputs: dict, ctx) -> ToolResult:
    """Spec section 6.5's `run_flow(flow_key, input, ctx)`, with the
    ROW already loaded (deviation P3-D5).

    Named `_run_steps` so the module has exactly one public runner and
    nobody has to work out which of two same-named functions a call
    reached -- the same reason `agents/runtime/jobs.py` does not
    re-export `run_turn`.

    `flow` is a `Flow` row. Its JSON is parsed into the SAME
    `FlowInput`/`FlowStep` dataclasses `agents.defaults` validates, by
    the same parser, so a row and a shipped default are read by
    identical code -- which is the only way "the same rules apply to
    both" stays true rather than being asserted twice.
    """
    from agents.contracts.tools import ToolRefused, get_tool
    from agents.defaults import parse_flow_json
    from agents.runtime.invoke import invoke_tool

    declared_inputs, steps = parse_flow_json(flow.inputs, flow.steps)

    # SEEDED FROM THE DECLARATION, not passed through (M7). Every
    # declared input is present, an unsupplied optional holding `None`,
    # so `resolve_ref` can tell "this flow has no such input" (a bug,
    # which raises) from "this optional was left out" (normal, which
    # resolves to the default). `invoke_tool` has already applied the
    # ToolSpec's own param defaults before this runs; this fills the
    # remaining holes rather than second-guessing that.
    declared_keys = {item.key for item in declared_inputs}
    seeded = {item.key: inputs.get(item.key) for item in declared_inputs}
    seeded.update({k: v for k, v in inputs.items() if k not in declared_keys})

    results = []
    artifacts: list[str] = []
    for index, step in enumerate(steps):
        if ctx.budget.expired:
            # Checked BEFORE the step, never during one -- the same
            # rule and the same reason as `loop.run_loop`.
            return _degraded(flow, steps, index, results, artifacts)
        try:
            tool = get_tool(step.tool)
        except ValueError:
            # Ruling R1: a step naming a feature-gated or not-yet-shipped
            # tool is a NOT-HERE, not a fault -- but this particular
            # flow cannot run, and nothing the calling model says will
            # change that. ToolRefused: honest, and no retry.
            raise ToolRefused(
                f"Flow {flow.slug!r} step {index} needs {step.tool!r}, which is not "
                f"available on this install."
            ) from None
        if tool.mutates:
            # LOAD-BEARING. `granted_tools` filters an AGENT's keys and
            # never sees a flow's steps, so without this a flow would be
            # a bypass around ADR 0010:266-276's "registered but not
            # grantable". A declaration-time check cannot do it: whether
            # a key is mutating is a property of the REGISTERED spec.
            #
            # This is the MUTATING half only. The ENTITLEMENT half
            # (which also carries the workstream wall's tool half) is
            # asked one layer down, in `invoke_tool` itself
            # (`agents/runtime/invoke.py`, C-2) -- the seam every caller
            # of a tool passes through, so no third caller can forget it
            # the way this loop once did.
            raise ToolRefused(
                f"Flow {flow.slug!r} step {index} calls {step.tool!r}, which changes "
                f"state that already exists. Such a tool is not callable until "
                f"Identity & Auth lands."
            )
        args = resolve_args(step.args, input=seeded, steps=results)
        outcome = invoke_tool(tool, args, ctx)
        if outcome.failed:
            if outcome.bars_retry:
                raise ToolRefused(
                    f"Flow {flow.slug!r} stopped at step {index} ({step.tool}): "
                    f"{outcome.text}"
                )
            # No per-step recovery (section 6.5): a flow is a declared
            # sequence and no model is present to reinterpret it. The
            # CALLING loop still gets its one recovery (section 10.2),
            # which will fail identically and end the turn honestly.
            raise ValueError(
                f"Flow {flow.slug!r} stopped at step {index} ({step.tool}): "
                f"{outcome.text}"
            )
        results.append(outcome.result)
        artifacts.extend(outcome.result.artifacts)
    return _finished(flow, steps, results, artifacts)


def _finished(flow, steps, results, artifacts) -> ToolResult:
    """The flow's own result.

    `text` is the LAST step's text (spec section 6.5). `data` is the
    last step's `data` MERGED UNDER the flow's own two keys -- which is
    what carries a closing `rag.ask`'s `citations` straight through to
    `agents/chat/rendering.py::citations_of`, so a flow card renders
    its sources exactly like the tool card it wraps. `artifacts` is
    every step's, deduped, first-seen order preserved, so an image
    generated mid-flow still renders even when a later step produced
    none.
    """
    last = results[-1]
    data = dict(last.data or {})
    data["flow"] = flow.slug
    data["steps"] = [
        {"tool": step.tool, "text": result.text}
        for step, result in zip(steps, results)
    ]
    return ToolResult(text=last.text, data=data,
                       artifacts=tuple(dict.fromkeys(artifacts)))


def _degraded(flow, steps, index, results, artifacts) -> ToolResult:
    """The turn ran out of time partway through.

    `data["degraded"] = True` is the OPT-IN flag
    `agents.runtime.invoke.invoke_tool` classifies -- so this is a
    `degraded` outcome, not a failure: the steps that ran really ran,
    it costs the calling loop no recovery, and the operator sees what
    was produced rather than a card saying the whole thing failed.
    This module is that outcome class's first shipped producer.

    Carries the LAST COMPLETED step's text, and says which step it
    stopped before. With no completed step at all, it says only that --
    never a trailer claiming a step "returned" something, which is the
    same rule `loop._honest_ending` holds.

    `data["steps"]` (H5 review round 1, CRITICAL): the SAME per-step
    summary `_finished` writes below, covering only the steps that
    actually completed (`steps[:len(results)]` -- `results` has exactly
    `index` entries here, one per completed step, by construction of the
    calling loop in `run_flow`). Without this, a degraded flow's own
    provenance was undiscoverable from the row alone: `agents.runtime.
    prompt._is_third_party_tool_result` reads `data["steps"][-1]["tool"]`
    to decide whether THIS flow's result text is a retrieval runner's
    own bytes wearing `flow.run`'s key, and a flow that ran out of time
    one step after `rag.search` must fence identically to one that
    finished normally.
    """
    done = f" Completed {index} of {len(steps)} step(s)." if index else ""
    text = (
        f"The turn ran out of time before step {index} of flow {flow.slug!r}."
        + done
    )
    if results:
        text = f"{text}\n\n{results[-1].text}"
    data = dict(results[-1].data or {}) if results else {}
    data["degraded"] = True
    data["flow"] = flow.slug
    data["stopped_at_step"] = index
    data["steps"] = [
        {"tool": step.tool, "text": result.text}
        for step, result in zip(steps[:len(results)], results)
    ]
    return ToolResult(text=text, data=data,
                       artifacts=tuple(dict.fromkeys(artifacts)))
