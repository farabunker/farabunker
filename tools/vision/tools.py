"""The vision tools an agent may call (spec section 5). VISION-OWNED.

Two `ToolSpec`s, and each runner calls the SAME service function the page
calls -- not a parallel copy of that logic:

- `vision.operations` -> `services.operation_catalog` -- exactly what
  `GET /vision/operations/` serves.
- `vision.generate`   -> `services.submit_job` + `services.wait_for` --
  exactly what `tools.vision.jobs.run_generate` (the queued job handler)
  already does, and the seam ADR 0012:748-752 names for a future
  chatbot tool.

MODULE-SCOPE IMPORTS STAY PURE, AND THAT IS LOAD BEARING. `VisionConfig.
ready()` imports this module to register the two specs below, and
`ready()` must not pull in the service layer (no DB, no HTTP at startup --
see that method's own docstring). So every runner does its service-layer
imports LAZILY, inside the function body, the same way `tools/rag/tools.py`
already does. THREE module-scope imports are the exception, all pure
stdlib with no Django and no I/O: `time` (for the wait-budget clamp
below), `dataclasses.replace` (for `_tool_param`'s `required` strip),
and `logging` (`narrowed_generate_spec`'s own fail-open guard, review
round 1 finding 2 -- it never raises out, so a caught failure must be
LOGGED or it vanishes silently).

`build_generate_spec()` is a FUNCTION, not a module constant: its params
are COMPUTED from `models.contracts.operations.all_operations()`, which is
only populated AFTER `VisionConfig.ready()` has run its five
`register_operation` calls. A constant would be evaluated at import time,
before any operation is registered, and would ship an empty param list.

THE UNION (ruling, superseding this task's original brief -- the brief's
own literal param list did not match what any real operation declares,
which meant `run_generate` could not submit ANY registered operation).
Every operation's params, deduplicated by key -- first operation's
declaration of a key wins, registration order txt2img, img2img, inpaint,
upscale, edit -- translated onto `agents.contracts.tools.TOOL_PARAM_KINDS`
(which has no `"file"`):

- A `"file"` param becomes a TEXT artifact-reference param (a tool call is
  JSON and cannot carry an upload object, ADR 0012:140). Every operation's
  FIRST file param (`Operation.file_params()`'s declaration-order rule,
  operations.py:103-111) collapses onto ONE shared key, `image` -- it
  always means "the picture this generation starts from or edits",
  whichever operation is picked. Every OTHER file param (`mask_image` on
  `inpaint`, `reference_image` on `edit`) keeps its own key: those name
  something a caller supplies IN ADDITION to `image`, not instead of it.
- A `"choice"` param (sampler, scheduler -- ENGINE-owned, no fixed
  `choices`) becomes `"text"`: `validate_params` requires a non-blank
  value for ANY `"choice"` param, required or not (operations.py:317-319),
  so a `"choice"` tool param would reject a call that simply omits it --
  the identical trade `tools/rag/tools.py:35-41`'s own `_CATEGORY` makes
  for the identical reason.
- `"asset"` (the LoRA selection, the upscale model) passes through
  exactly as declared -- it is already in `TOOL_PARAM_KINDS`.
- Every OTHER param (`prompt`, `cfg_scale`, `denoise`, `guidance`,
  `instruction`, ...) keeps its real key, kind, description, default,
  bounds, and `choices` verbatim -- no invented generic name (there is no
  `"cfg"`; the real key is `cfg_scale`).
- ONLY `"operation"` is `required=True` at this schema's level. Every
  other param's per-operation `required` is real but LOCAL -- `prompt` is
  required for `txt2img`, meaningless for `upscale` -- and this is a UNION
  schema one operation's requirement cannot be projected onto. Forcing it
  here would make every call carry a value for every other operation's
  required params too. The real enforcement happens where the real
  operation is known: `submit_job`'s own `validate_params`, downstream of
  narrowing.

`vision.generate`'s schema is therefore the union of every operation's
params, but `submit_job` validates against the ONE operation the caller
picked, and `validate_params` rejects a key that operation does not
declare (operations.py:276-279). So `run_generate` NARROWS the caller's
own arguments down to what the picked operation declares before calling
`submit_job` -- and it ANNOUNCES every argument it drops in
`ToolResult.text`. Never silently, and only for what the CALLER actually
supplied -- read off `ctx.supplied_keys`, the caller's TRUE raw argument
keys, which `invoke_tool` stamps onto the `ToolContext` before this ever
runs (`agents/contracts/tools.py::ToolContext.supplied_keys`), because
key presence in the VALIDATED dict cannot answer this (`clean` fills
every union key regardless of whether the caller sent it) and comparing
values against a declared default cannot either (fix round 1: an
"asset" param's blank-fill ignores its `default` entirely, and a
caller's explicit value can legitimately equal some OTHER operation's
default for a shared key). `_narrow_for_operation`'s own docstring is
the authority on the mechanics. A union param nothing ever touched
(`guidance` on a `txt2img` call) was never dropped, it simply never
applied.

Two more behaviours this narrowing pulls in, both real operation bugs the
original brief's literal param list papered over:

- An ENGINE-owned param (`sampler`, `scheduler`) the caller leaves blank
  is filled by `services.fill_engine_blanks` -- the same function the page
  uses for the params a model IGNORES -- with this module's own, wider key
  set: the model's reported default, else the first entry
  `services.live_options` reports, never left blank, which
  `validate_params` would refuse for ANY `"choice"`-kind param regardless
  of `required`.
- `image` and any supplied second file key the picked operation declares
  are resolved together, in ONE `services.resolve_inputs` call, and its
  return value is handed to `submit_job(files=...)` UNCHANGED -- no
  remapping. `resolve_inputs` is asked for exactly the keys the picked
  operation itself names, so it always answers under those same keys.

Finally: `services.wait_for` polls the IMAGE ENGINE about a generation
already submitted (`refresh_job`, never `models.contracts.queue.get_job`)
-- so waiting on the `GenerationJob` this runner submits does not violate
the never-block-on-a-queue-job rule; that rule is about the QUEUE, and
this seam never touches it (ADR 0012:748-752). The wait is clamped to
whatever remains of the turn's own step budget
(`ctx.budget.deadline_monotonic`), never past it, even though
`GENERATE_WAIT_TIMEOUT_SECONDS` alone could ask for far longer.

No describer (ruling R3): the vision `describer` is struck from P1
entirely.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace

from agents.contracts.tools import ToolContext, ToolResult, ToolSpec, validate_tool_args
from models.contracts.operations import Operation, Param, all_operations, operations_for
from models.contracts.roles import IMAGE_GENERATION_CAPABILITY

# ONLY used to log a caught failure inside `narrowed_generate_spec`'s own
# fail-open guard (review round 1, finding 2) -- never for anything that
# would make this module's own import touch Django or I/O; `logging` is
# pure stdlib, the same carve-out `time` and `dataclasses.replace` above
# already have (see this module's own docstring).
logger = logging.getLogger(__name__)

# THE KEY, SPELLED ONCE. `tools/vision/views.py` gates its own page on
# this tool's entitlement (spec section 9.3), and a view that retyped the
# literal would be a second spelling of a key the registry matches
# exactly.
VISION_GENERATE_TOOL_KEY = "vision.generate"

VISION_OPERATIONS = ToolSpec(
    key="vision.operations",
    label="List generation modes",
    description=(
        "List every image-generation mode the currently bound model can run, "
        "with each mode's parameters. Call this before vision.generate when "
        "unsure which operation or parameters to use."
    ),
    params=(),
    roles=(),
    runner="tools.vision.tools.run_operations",
)

# The one param every picked operation's FIRST file param collapses onto
# (see this module's own docstring). Synthesised, not taken from any
# operation's own `Param` -- no single operation's file param is right for
# every operation (some declare none at all), so this carries its own
# description rather than one operation's.
_IMAGE_PARAM = Param(
    "image", "text", "Source image",
    description=(
        "An artifact reference to a stored image to work from, in the form "
        "output:<id>, input:<id> or document:<id> (an image attached to this "
        "conversation). Required by the image-to-image family, "
        "ignored by text-to-image."
    ),
)


def _tool_param(param: Param) -> Param:
    """`param`, translated onto `TOOL_PARAM_KINDS` and stripped of any
    per-operation `required` -- see this module's own docstring for why a
    union schema cannot carry one operation's requirement.

    A `"choice"` param becomes `"text"`; everything else keeps its real
    key, kind, description, default, bounds, and `choices` verbatim.

    The `"choice"` branch below deliberately drops `param.choices` --
    every `"choice"` param any operation declares TODAY (`sampler`,
    `scheduler`) is engine-owned and already carries an EMPTY `choices`
    tuple (`SAMPLING_PARAMS`, operations.py), so nothing is lost yet. If
    an operation ever declares a `"choice"` param with a FIXED, non-empty
    `choices` tuple, this translation would drop that list from the tool
    schema too -- membership would then be enforced only downstream, by
    `submit_job`'s own `validate_params` against the picked operation, not
    by an `enum` in the LLM-facing schema. Acceptable for the same reason
    `tools/rag/tools.py`'s own `_CATEGORY` accepts it: a schema that must
    validate against ONE operation cannot honestly offer a fixed list that
    is only sometimes right.
    """
    if param.kind == "choice":
        return Param(
            param.key, "text", param.label, description=param.description or param.label,
        )
    if param.required:
        return replace(param, required=False)
    return param


def _file_ref_param(param: Param) -> Param:
    """A non-first `"file"` param (`mask_image`, `reference_image`), as a
    text artifact-reference param under its OWN key -- the same
    `output:<id>`/`input:<id>`/`document:<id>` vocabulary `image`
    carries, because this names something a caller supplies IN ADDITION
    to `image`, not instead of it."""
    description = param.description or param.label
    return Param(
        param.key, "text", param.label,
        description=(
            f"{description} An artifact reference: output:<id>, input:<id> or "
            "document:<id> (an image attached to this conversation)."
        ).strip(),
    )


def build_generate_spec() -> ToolSpec:
    """`vision.generate`'s `ToolSpec`, computed from `all_operations()` at
    CALL time (registration time -- see this module's own docstring for
    why a constant cannot do this and for the union rules this follows).
    """
    operations = all_operations()

    params: list[Param] = [
        Param("operation", "choice", "Operation", required=True,
              choices=tuple(op.key for op in operations),
              description="Which generation mode to run."),
    ]
    file_ref_params: list[Param] = []
    seen = {"operation", "image"}

    for operation in operations:
        file_params = operation.file_params()
        first_file_key = file_params[0].key if file_params else None
        for param in operation.params:
            if param.key in seen:
                continue
            if param.kind == "file":
                if param.key == first_file_key:
                    continue  # collapses onto the shared `image` param below
                file_ref_params.append(_file_ref_param(param))
                seen.add(param.key)
                continue
            params.append(_tool_param(param))
            seen.add(param.key)

    params.append(_IMAGE_PARAM)
    params.extend(file_ref_params)

    return ToolSpec(
        key=VISION_GENERATE_TOOL_KEY,
        label="Generate an image",
        description=(
            "Generate or edit an image. Pick an operation (call "
            "vision.operations first if unsure which one fits, or what "
            "parameters it takes) and describe what to produce. Waits for "
            "the generation to finish or time out before returning."
        ),
        params=tuple(params),
        roles=("vision.generate",),
        runner="tools.vision.tools.run_generate",
    )


def _narrowed_image_description(supported: list[Operation]) -> str:
    """`_IMAGE_PARAM.description`, rewritten to name only the SURVIVING
    operations -- the incident's own steer (image-model-trace.md's
    Follow-up 3): the union's static "Required by the image-to-image
    family" line told a model bound to a family with no img2img graph
    to pick `img2img` anyway, because nothing in the schema said it
    couldn't. `operation.file_params()`, not a guess at the operation's
    name, is what decides which side an operation falls on.
    """
    uses = [operation.key for operation in supported if operation.file_params()]
    skips = [operation.key for operation in supported if not operation.file_params()]
    sentence = (
        "An artifact reference to a stored image to work from, in the form "
        "output:<id>, input:<id> or document:<id> (an image attached to this "
        "conversation)."
    )
    if uses:
        sentence += f" Required by: {', '.join(uses)}."
    if skips:
        sentence += f" Ignored by: {', '.join(skips)}."
    return sentence


def narrowed_generate_spec(spec: ToolSpec) -> ToolSpec:
    """`spec`, narrowed to what the BOUND model can actually run, THIS
    turn -- the per-turn seam `agents.runtime.loop.available_tools`
    calls beside `agents.runtime.flowtool.narrowed_flow_spec` (Fix 1b),
    same shape and placement, with ONE deliberate difference: this
    NEVER returns `None`. `services.operations_for_model` already
    treats "the engine has no opinion" as "offer everything" (its own
    docstring) -- there is always something a caller could legally ask
    for, so unlike a flow tool with zero enabled rows, there is no
    honest case for withholding `vision.generate` outright. Lazy
    `services` import, this module's own module-purity discipline (see
    the module docstring) -- `VisionConfig.ready()` calls
    `build_generate_spec()`, never this.

    THE WHOLE BODY IS ONE `try`/`except Exception` (review round 1,
    finding 2's internal half): a narrowing failure -- a broken engine
    adapter, an import error, a bug in this function itself -- must
    NEVER cost the turn its tools. It degrades to the exact fallback
    every branch below already returns for a milder reason (the union
    spec, unchanged), logged rather than silent: a swallowed failure
    here would silently recreate the kind of gap this fix exists to
    close. `agents.runtime.loop.available_tools`'s own callsite carries
    a SECOND, independent guard around the dotted-path call itself
    (loop.py's own docstring) -- layered defence, different jobs: that
    one also covers `resolve_dotted_path` failing to resolve this
    function at all, which is a failure this function's own `try`
    cannot see.

    UNCHANGED (never narrowed, never raises) when: preflight is not
    `ready` for ANY reason -- unbound, or bound but the engine is
    unreachable -- because there is nothing LIVE to narrow from either
    way; or the model's engine reports it can run every registered
    operation, which is indistinguishable here from an adapter with no
    opinion at all (`services.operations_for_model`'s own "no opinion ->
    full list" rule) -- narrowing to the full set would be a no-op
    dressed up as one. A model that genuinely supports NOTHING is left
    to Fix 3's submit-time refusal to explain why: handing back a spec
    whose only required param has an empty enum would be worse than the
    union it replaces. The only added per-turn COST is the one
    `services.preflight` health round trip this makes -- `available_
    tools` already resolves the role for `_roles_resolve` regardless,
    cheaply; shaving the health check itself with a short-lived cache
    is real future work, not built here (tools/vision/README.md's
    "Known limits" section carries it).

    Otherwise: (a) `operation`'s enum narrows to
    `services.operations_for_model`'s own list; (b) every other union
    param is kept only if a SURVIVING operation still declares it
    (`operation.param(key)`, checked against every survivor -- a key
    the union's first-ever declarer no longer offers can still survive
    through a different supported operation); (c) `image` is dropped
    outright when NO surviving operation takes a file param at all
    (review round 1, finding 4 -- a slot nothing can use is an orphaned
    param like any other), otherwise its description is rebuilt from
    the survivors' own file params (`_narrowed_image_description`), so
    it never steers toward an operation this model cannot run; (d)
    every surviving param whose key `services.live_defaults` reports a
    value for, on the FIRST surviving operation to declare it, has its
    schema `default` overwritten with that model's own number and its
    `description` gains an APPENDED sentence naming it -- the param's
    own explanation of what it MEANS stays (review round 1, finding 3:
    replacing it would have thrown away real information, not just the
    union's generic "5-8 suits most checkpoints" range advice that
    taught the model to supply the wrong ones, image-model-trace.md's
    numeric-defaults finding); (e) the supported list is appended to
    the description (fix iv, free here).
    """
    try:
        from tools.vision import services

        check = services.preflight(resolved=None)
        if not check.ready:
            return spec
        resolved = check.resolved

        supported = services.operations_for_model(resolved)
        # `operations_for(IMAGE_GENERATION_CAPABILITY)`, NOT
        # `all_operations()` (review round 1, finding 8):
        # `all_operations()` is the GLOBAL cross-capability registry --
        # `operations_for_model` only ever returns image-generation
        # operations, so comparing it against the WHOLE registry is a
        # latent break the moment any other capability registers an
        # operation elsewhere: the two sets would then never match
        # again, permanently defeating the no-opinion short-circuit
        # below even for a model that truly has no narrower opinion.
        all_keys = {
            operation.key for operation in operations_for(IMAGE_GENERATION_CAPABILITY)
        }
        supported_keys = {operation.key for operation in supported}
        if not supported or supported_keys == all_keys:
            return spec

        takes_a_file = any(operation.file_params() for operation in supported)

        def owner(key: str) -> Operation | None:
            for operation in supported:
                if operation.param(key) is not None:
                    return operation
            return None

        def narrow(param: Param) -> Param | None:
            if param.key == "operation":
                return replace(param, choices=tuple(operation.key for operation in supported))
            if param.key == "image":
                if not takes_a_file:
                    return None  # no survivor can use it -- dropped, not just re-described
                return replace(param, description=_narrowed_image_description(supported))
            found = owner(param.key)
            if found is None:
                return None  # no surviving operation declares this key -- dropped
            live_value = services.live_defaults(found, resolved).get(param.key)
            if live_value is None:
                return param
            addition = f"This model's own default is {live_value!r}; omit this to use it."
            description = f"{param.description} {addition}".strip() if param.description else addition
            return replace(param, default=live_value, description=description)

        params = tuple(
            kept for kept in (narrow(param) for param in spec.params) if kept is not None
        )
        supported_line = ", ".join(operation.key for operation in supported)
        return replace(
            spec, params=params,
            description=f"{spec.description}\n\nThis model runs: {supported_line}.",
        )
    except Exception:  # noqa: BLE001 -- fail OPEN to the union spec, never cost the turn its tools
        logger.exception(
            "vision.generate: narrowed_generate_spec failed; offering the union spec unchanged"
        )
        return spec


def run_operations(args: dict, ctx: ToolContext) -> ToolResult:
    """List every operation the bound model can run, with live options
    filled in -- exactly what `GET /vision/operations/` serves.

    Validated BEFORE the service-layer import below: an invalid call is
    rejected without ever paying for the import, matching this module's
    own module-scope-stays-pure discipline one level down.

    `resolved=None`: the role binding answers, the only model a tool call
    has. `operation_catalog` runs its own preflight ONCE for the whole
    catalog (services.py:319-324), so this never pays a health round trip
    per operation.
    """
    validate_tool_args(VISION_OPERATIONS, args)

    from tools.vision import services

    catalog = services.operation_catalog(resolved=None)

    lines = []
    for entry in catalog:
        line = f"{entry['key']} — {entry['label']}"
        if not entry.get("supported", True):
            line += f" — not runnable here: {entry['unsupported_reason']}"
        lines.append(line)
    text = "\n".join(lines) if lines else "No generation modes are available."
    return ToolResult(text=text, data={"operations": catalog})


def _narrow_for_operation(clean: dict, operation, supplied: set[str]) -> tuple[dict, dict, list[str]]:
    """Narrow the union schema's validated `clean` dict down to what
    `operation` actually declares.

    `supplied` is the set of keys the CALLER actually sent (never
    including `"operation"`) -- computed by `run_generate`, not here.
    Fix round 1 (F1/F2): two earlier approaches both broke. Key presence
    in `clean` cannot answer it, because `clean` is `validate_tool_args`'s
    own output and fills EVERY declared union key whether or not the
    caller sent it. Comparing each value against the union's declared
    default came closer, but broke on two real cases, both reproduced by
    executing the code: an `"asset"` param's blank-fill is `[]`/`None`
    regardless of its declared `default` (operations.py's own
    `validate_params`), so a sparse `upscale` call always saw its
    (undeclared) `loras` key as "supplied"; and a caller who explicitly
    sends a value that happens to equal some OTHER operation's default
    for a shared key (`denoise=0.6`, `img2img`'s default and therefore
    the union's) was indistinguishable from one who sent nothing,
    silently overriding an explicit choice. `run_generate` instead reads
    the caller's TRUE raw keys off `ctx.supplied_keys`
    (`agents/contracts/tools.py::ToolContext.supplied_keys`), which
    `invoke_tool` stamps from the args it actually received, before
    validation ever touched them -- the one production path every call
    takes (`agents/runtime/invoke.py:143-146`). A direct call that builds no
    such context (every test in this module) falls back to its own raw
    `args`' keys instead, which is the exact shape this function always
    took before CQ-1.

    Returns `(raw_params, references, dropped)`:

    - `raw_params` -- every NON-file key `operation` declares, taken from
      `clean` (the tool schema's own coerced values). Never includes
      `"operation"`, `"image"`, or any file key -- those are files, not
      params, and travel through `references` instead.
    - `references` -- `{param key: artifact reference}` for `image`
      (mapped onto `operation.file_params()[0]`, when it has one) and any
      OTHER file key the CALLER supplied that `operation` also declares --
      unvalidated reference strings, exactly the shape
      `services.resolve_inputs` takes.
    - `dropped` -- every key the CALLER actually supplied that `operation`
      does not use, sorted. Only what the caller supplied is ever
      announced: a union key nothing ever set was never dropped, it
      simply never applied.
    """
    file_keys = operation.file_param_keys()
    file_params = operation.file_params()
    first_file_key = file_params[0].key if file_params else None

    references: dict[str, str] = {}
    if first_file_key and clean.get("image"):
        references[first_file_key] = clean["image"]
    for key in file_keys:
        if key == first_file_key:
            continue
        if key in supplied and clean.get(key):
            references[key] = clean[key]

    dropped = []
    for key in supplied:
        if key == "image":
            if first_file_key is None:
                dropped.append("image")
            continue
        if key in file_keys:
            continue  # named as a reference above, not dropped
        if operation.param(key) is None:
            dropped.append(key)

    # ONLY what the caller actually SUPPLIED is forwarded. `clean` is the
    # union schema's own always-filled dict -- every param the UNION
    # declares carries a value, even one the caller never set, filled
    # from the UNION's own default (the first operation to declare that
    # key). Forwarding an untouched key would forward THAT operation's
    # default into a DIFFERENT operation's submission -- inpaint would
    # run at img2img's denoise (0.6), never its own (1.0); edit would run
    # at txt2img's steps (25), never its own (20). `validate_params`
    # fills each operation's OWN default for every key this dict omits,
    # and resolves an absent seed itself -- omission is what makes that
    # correct fallback run at all.
    raw_params = {
        key: value for key, value in clean.items()
        if key in supplied
        and key not in ("operation", "image")
        and key not in file_keys
        and operation.param(key) is not None
    }
    return raw_params, references, sorted(dropped)


def _fill_engine_params(operation, raw_params: dict, resolved) -> dict:
    """Fill every blank ENGINE-owned param (`"choice"`-kind with no fixed
    `choices` -- sampler, scheduler) through the same filler the page uses,
    `services.fill_engine_blanks` (ADR 0012 D-EDIT-13): the model's
    reported default, else the first entry the engine offers. The page's
    own rule fills only params the model IGNORES; this call passes its own
    wider key set of everything left blank, since a tool caller has no
    disabled widget to distinguish "ignored" from "simply omitted."

    A blank `"choice"` param is refused by `validate_params` regardless of
    `required` (operations.py:317-319) -- this is what makes submitting a
    txt2img/img2img/inpaint job through this tool possible at all without
    the caller having to know the bound engine's own vocabulary.

    `resolved` is `run_generate`'s OWN preflight's binding, already
    confirmed `ready` before this is ever called -- passed in rather than
    resolved again here. This function used to preflight for itself
    (raising `ToolRefused` when not `ready`, because `live_defaults`/
    `live_options` can only ever answer `{}` for an unbound role or a
    dead engine, which would otherwise leave the blank param blank and
    let `submit_job`'s own `validate_params` raise a bogus, RETRYABLE
    `ParamError` for a failure no retry could fix); `run_generate` now
    preflights exactly once, before calling this, and refuses there on
    the platform's own copy -- the same verbatim message this used to
    raise on its own, just one call site up.

    Two independent fills happen here, both keyed off what `resolved`
    reports for THIS bound model, never a schema constant:

    - ENGINE-owned `"choice"` params (`sampler`, `scheduler`: no FIXED
      `choices` -- their vocabulary is read live, from the engine) left
      BLANK -- unchanged from before this item, see the docstring on
      the shared filler call below.
    - Every OTHER numeric (`"int"`/`"float"`) OR `"choice"`-with-a-
      fixed-vocabulary param the caller OMITTED entirely (2026-09-03
      fix batch, item A -- the tool-path 6x slowdown; widened to
      `"choice"` in review, per the brief's own "numeric/choice"
      wording -- every param declared today is numeric, so this is
      exercised with a synthetic operation in tests). `raw_params`
      already carries only the keys the caller actually SUPPLIED
      (`_narrow_for_operation`'s own contract), so "not in `raw_params`"
      IS "omitted" here. The page never has this problem: its GET
      spreads `services.live_defaults` straight into the unbound form's
      `initial` (`views.py` ~586), so the family's real opening value --
      not the schema's -- is what a submission that never touched the
      field carries. The tool path had no equivalent, so an omitted
      `steps`/`cfg_scale` fell through to `submit_job`'s own
      `validate_params`, which fills the STATIC SCHEMA default
      (`operations.py` `SAMPLING_PARAMS`: steps=25, cfg_scale=7.0) --
      correct for the checkpoint family the schema was written against,
      six times too slow (and mis-guided) for a distilled family whose
      graph wants far fewer steps and near-unity guidance. Reading
      `live_defaults` here, the SAME function the page reads, is the
      one source of truth this item routes through rather than
      duplicating as a second table. A key the caller explicitly
      supplied -- even a value that happens to equal the schema's own
      default -- is never touched: only a genuinely omitted key is ever
      filled, and only when the model actually reports a value for it;
      one it does not report is left omitted, exactly as it always was,
      for `validate_params` to fill from the operation's own schema
      default downstream. Excludes the engine-owned keys above (already
      filled by the first bullet, from the identical `live_defaults`
      source, so there is nothing left for this second pass to add) --
      the distinction is real vocabulary ownership, not kind alone.
    """
    engine_keys = {
        param.key for param in operation.params
        if param.kind == "choice" and not param.choices
    }
    blank_keys = {
        key for key in engine_keys if not str(raw_params.get(key) or "").strip()
    }

    from tools.vision import services

    # ONE filler, shared with the page (`services.fill_engine_blanks`,
    # ADR 0012 D-EDIT-13). The page's own rule fills only the params the
    # model IGNORES -- an enabled field it left blank is a real
    # validation failure. A TOOL caller has no form and no disabled
    # widget, so it may simply have omitted `sampler`; this passes its
    # own, wider key set and keeps the same value order (the model's
    # reported default, else the engine's first option).
    filled = (
        services.fill_engine_blanks(operation, raw_params, resolved, keys=blank_keys)
        if blank_keys else dict(raw_params)
    )

    omitted_fillable_keys = {
        param.key for param in operation.params
        if param.kind in ("int", "float", "choice")
        and param.key not in engine_keys
        and param.key not in raw_params
    }
    if not omitted_fillable_keys:
        return filled

    live = services.live_defaults(operation, resolved)
    for key in omitted_fillable_keys:
        if key in live:
            filled[key] = live[key]
    return filled


def run_generate(args: dict, ctx: ToolContext) -> ToolResult:
    """Submit one generation and wait for it to finish or time out.

    Order: validate against THIS tool's own union schema, resolve the
    picked operation, narrow the arguments to what it declares
    (announcing every drop), resolve `image` (and any second file key)
    onto `operation.file_params()`, preflight ONCE, fill any blank
    engine-owned param, submit, wait (clamped to the turn's remaining
    budget), and report.

    ONE preflight, not two. This used to run twice for any operation with
    a blank engine-owned param -- `_fill_engine_params` preflighted for
    itself (it had to: filling from `{}` would otherwise leave the param
    blank and let `submit_job` raise a bogus, retryable `ParamError`
    instead), then `submit_job` preflighted again internally, resolving
    the role from the database a second time for the same call. Now
    `run_generate` preflights itself, once, right where `_fill_engine_
    params`'s own check used to sit -- AFTER `resolve_inputs` (a dead or
    malformed reference is a ValueError the model can repair, section
    10.2's one retry, and it must surface before this pays a health round
    trip for a generation that was never going to run anyway) and before
    the engine-param fill and the submit -- and threads its `resolved`
    binding through to both: `_fill_engine_params(..., resolved=...)`
    (no preflight of its own now) and `submit_job(..., resolved=...)`.

    `submit_job` still preflights internally regardless -- changing that
    is outside this item's scope, since it is `services`' own contract,
    shared with the page's submission path, and this item's job is only
    to stop `tools.py` asking twice. Passing `resolved` (not `None`)
    means that internal call resolves to `_health_check(resolved)`
    (services.py's own `preflight`) rather than re-resolving the role
    from scratch, so it costs a health round trip, not a full duplicate
    lookup -- and the `except VisionUnavailable` below stays, covering
    the (unlikely, but real) case where the engine that answered this
    preflight a moment ago stops answering before `submit_job`'s own
    check runs.

    Accepted ordering trade (review): `submit_job`'s own docstring is
    explicit that IT preflights AFTER `validate_params` -- schema
    validation first, so an invalid submission gets the 400-shaped,
    RETRYABLE `ParamError` regardless of engine state. This function's
    single preflight now runs BEFORE `submit_job` is ever reached, which
    reverses that for one narrow case: a call that is both missing a
    required param and facing an unbound (or unreachable) role now gets
    `ToolRefused` (no retry) instead of `ParamError`. Accepted rather
    than preserved -- one unconditional preflight, at one call site, is
    worth more than a conditional second one that existed only to keep
    this ordering intact, and `ToolRefused` is still an honest answer:
    the role really is unbound, and no retry with a different or more
    complete set of params would have helped either.
    `TestVisionGenerateRunner::test_an_unbound_role_wins_over_a_missing_
    required_param` pins it.
    """
    from agents.contracts.tools import ToolRefused
    from models.contracts.operations import get_operation
    from tools.vision import services
    from tools.vision.jobs import GENERATE_WAIT_TIMEOUT_SECONDS

    spec = build_generate_spec()
    clean = validate_tool_args(spec, args)
    operation_key = clean["operation"]
    operation = get_operation(operation_key)
    if operation is None:
        raise ValueError(f"Unknown operation {operation_key!r}")

    # The caller's TRUE raw keys, off `ctx.supplied_keys` -- stamped by
    # `invoke_tool` from the args it actually received (fix round 1,
    # F1/F2). Empty when this runner is called directly, without going
    # through `invoke_tool` (every test in this module): falls back to
    # `args`' own keys, the shape this function always used before CQ-1.
    supplied = (
        set(ctx.supplied_keys) if ctx.supplied_keys else set(args)
    ) - {"operation"}
    raw_params, references, dropped = _narrow_for_operation(clean, operation, supplied)

    # Resolved BEFORE the preflight below: a dead or malformed reference
    # is a ValueError the model can repair (section 10.2's one retry), and
    # it should surface before this pays a health round trip for a
    # generation that was never going to run anyway.
    files = services.resolve_inputs(operation, references, ctx.principal) if references else None

    check = services.preflight(resolved=None)
    if not check.ready:
        raise ToolRefused(check.message)

    # Fix 3 (image-model-trace.md, Follow-up 3): refuse an operation the
    # BOUND model cannot run BEFORE any row exists, not after. Without
    # this, `submit_job` still creates a real `GenerationJob`, stamps
    # the bound model onto it, and only the engine's own template
    # lookup refuses -- a `ValueError` this function never sees,
    # because `submit_job` catches it and finalizes the row `failed`
    # instead of raising. A plain, RETRYABLE `ValueError` (never
    # `ToolRefused`: a DIFFERENT operation genuinely can succeed, so
    # this is repairable, not refused) carrying `operation_states`'s
    # own honest reason plus the supported list -- the same sentence
    # Fix 1's per-turn narrowing already tries to keep the model from
    # reaching this at all, and this is the defence behind it for the
    # paths that narrowing does not cover (a direct runner call, a
    # queued job, a model that never re-fetched its tool list).
    states = {state.operation.key: state for state in services.operation_states(check.resolved)}
    state = states.get(operation_key)
    if state is not None and not state.supported:
        supported_line = ", ".join(key for key, s in states.items() if s.supported) or "none"
        # Conditional period (review round 1, finding 6):
        # `operation_states`' own reasons already end in "." (both of
        # its branches do) -- appending another sentence with a bare
        # `f"{reason} ..."` never double-punctuates TODAY, but nothing
        # in `OperationState.reason`'s own contract guarantees that
        # forever, and stacking a second period ("..") the moment it
        # doesn't would be a silent, cosmetic typo in an error message a
        # model reads to decide what to retry. Add one only if the
        # reason does not already end with terminal punctuation.
        reason = state.reason.rstrip()
        if reason and not reason.endswith((".", "!", "?")):
            reason += "."
        raise ValueError(f"{reason} This model runs: {supported_line}.")

    raw_params = _fill_engine_params(operation, raw_params, check.resolved)

    try:
        job = services.submit_job(
            operation_key, raw_params, files=files, resolved=check.resolved,
            actor=ctx.principal,
        )
    except services.VisionUnavailable as exc:
        # The message is the platform's OWN copy (`services.
        # role_unbound_message()` or the engine-unreachable sentence
        # `preflight` already wrote) -- preserved verbatim, never
        # reworded. Nothing the model can say fixes an unbound role or a
        # dead engine, so this gets no retry (ToolRefused, section 10.1).
        raise ToolRefused(exc.message) from exc

    # Clamped to whatever remains of the TURN's own budget, never past it
    # -- `GENERATE_WAIT_TIMEOUT_SECONDS` alone could ask for hours longer
    # than a turn has left. `wait_for` returns the job AS-IS at whichever
    # deadline arrives first; a non-terminal job is a real, reportable
    # state below, not a failure to crash on.
    remaining = ctx.budget.deadline_monotonic - time.monotonic()
    timeout = max(1.0, min(GENERATE_WAIT_TIMEOUT_SECONDS, remaining))

    def on_poll(polled, elapsed: float) -> None:
        ctx.job.report_progress(int(elapsed), total=None, unit="seconds", label="generating")

    job = services.wait_for(job, timeout=timeout, on_poll=on_poll)

    payload = services.job_json(job)
    artifacts = tuple(f"output:{output['id']}" for output in payload.get("outputs", ()))

    lines = []
    if dropped:
        noun = "argument" if len(dropped) == 1 else "arguments"
        lines.append(
            f"Ignored {noun} not used by {operation_key!r}: {', '.join(dropped)}."
        )
    status = payload.get("status", "")
    if job.is_terminal:
        # Fix 2 (image-model-trace.md, Follow-up 3): a failed job's own
        # `error` already names the actionable thing (Fix 3's refusal
        # sentence, an engine rejection, a lost job) -- it sat one dict
        # key away in `payload` and was never read. Speaking it here is
        # what turns "finished as failed." into a recoverable answer.
        sentence = f"Generation {payload['id']} finished as {status}"
        if status == "failed" and payload.get("error"):
            sentence += f": {payload['error']}"
        lines.append(sentence + ".")
    else:
        lines.append(f"Generation {payload['id']} is still running (status: {status}).")

    # 2026-09-03 fix batch, item C: the refs land in `.artifacts` above,
    # but the MODEL never sees that field in a replayed turn -- only
    # `.text` -- so a follow-up edit step had no in-turn name for the
    # image it just made. Named here too, in PROSE, EXACT `output:<id>`
    # spelling. Deliberately NOT a bracketed `[artifacts: ...]` block:
    # the chat runtime already appends its own such line to a replayed
    # tool turn (agents/runtime, the peer's half of this fix), and a
    # second bracketed block here would read as duplicate noise. Only
    # emitted when there IS an output yet (a non-terminal or failed job
    # has none) -- redundant with `.artifacts` on purpose, per the
    # brief: cross-turn replay is the other half, this is ours.
    if artifacts:
        pronoun = "this" if len(artifacts) == 1 else "these"
        lines.append(f"Outputs: {', '.join(artifacts)} — reference {pronoun} to edit.")

    # READS, never produces (RULED CORRECTION, superseding this module's
    # earlier "no describer" note): `payload["description"]` is written
    # by `tools.vision.jobs.run_generate` -- the QUEUED `vision.generate`
    # job kind's own handler, admitted with `rag.extract` alongside the
    # image model (`jobs.plan_generate`) -- STRICTLY AFTER a generation
    # finishes, never by this runner. A generation THIS runner submits
    # (`services.submit_job`/`wait_for`, directly, synchronously, inside
    # the turn already running this tool call) never passes through that
    # job kind at all, so `payload["description"]` is `""` for every
    # generation reached this way today -- this line costs nothing now
    # and asks nothing new of the turn's own model admission, and it
    # starts reading a value the moment anything ever writes one for a
    # generation reached through THIS path. Appended verbatim, no
    # further templating: `services.describe_output` already writes a
    # complete, clearly-labelled sentence (or the honest failure
    # sentence) onto the stored field.
    if payload.get("description"):
        lines.append(payload["description"])
    text = " ".join(lines)

    return ToolResult(text=text, data=payload, artifacts=artifacts)
