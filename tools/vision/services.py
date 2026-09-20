"""
Vision service layer (spec §4.7) -- the surface the page uses today and the
chatbot tool will use tomorrow, unchanged.

Everything the operator's action means lives here: preflight honesty,
submission, the refresh state machine, and deletion. Views hold no
generation logic, so a second caller gets identical behaviour for free.

Idempotent by design: `refresh_job` may be called as often as a page polls,
and a terminal job is never re-fetched or re-stored.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from models.contracts import bindings
from models.contracts.bindings import ResolvedModel, config_family
from models.contracts.engines import get_engine
from models.contracts.engines.base import GenerationRejected
from models.contracts.gateway import get_image_generator, get_image_generator_for
from models.contracts.operations import (
    GenerationRequest,
    Operation,
    Param,
    describe,
    get_operation,
    operations_for,
    validate_params,
)
from models.contracts.roles import IMAGE_GENERATION_CAPABILITY, VISION_GENERATE_ROLE
from agents.contracts.artifacts import file_resolver_for, parse_artifact
from models.contracts.jobkinds import resolve_dotted_path
from identity.access import is_admin, owner_fields
from tools.vision import probe_cache, store
from tools.vision.models import GeneratedOutput, GenerationJob, JobInput
from tools.vision.visibility import visible_jobs

logger = logging.getLogger(__name__)

# What the operator is told when the engine has forgotten a job. Written
# here, not in the engine adapter: the adapter reports the FACT ("lost"),
# the platform decides how to explain it.
LOST_MESSAGE = (
    "The image engine no longer has this job — it was probably restarted; resubmit to try again."
)

# The picked pk named no usable image-generation connection -- deleted, or
# its capability removed, since it was last read. ONE copy (final-review
# finding 4 -- this used to be `tools.vision.views._UNREGISTERED_
# CONNECTION_MESSAGE`, duplicated by nothing but read only there): the
# page's picker refusal (`views._picker_failure_response`), the schema
# endpoint's 404 (`views.vision_operations`), and `jobs.run_generate`'s
# claim-time re-check of a payload's `connection` all say the same
# sentence, worded the way `tools/rag/views.py` words the same failure
# for the Ask picker.
UNREGISTERED_CONNECTION_MESSAGE = (
    "That model is no longer registered for image generation — reload the page to see "
    "the current list, then pick again. Nothing was queued."
)


@dataclass(frozen=True)
class PreflightResult:
    """Three honest states, mirroring `tools.rag.views._precheck_models`: the role
    is `ready`, `unbound` (nothing assigned), or `unreachable` (assigned but
    the engine isn't answering). Never a fourth, vaguer state."""

    state: str
    resolved: ResolvedModel | None
    message: str

    @property
    def ready(self) -> bool:
        return self.state == "ready"


# `PreflightResult.state` -> the failure kind a caller reads off
# `VisionUnavailable`. The fourth pre-row kind needs no constant of its
# own: `ParamError` means `GenerationJob.FailureKind.PARAMS_INVALID`, said
# once in that enum's docstring and once in ADR 0012, and an alias here
# would be a second name for a value nothing in this repo reads yet.
# `"connection_unavailable"` (final-review finding 4) is never a
# `PreflightResult.state` -- `preflight()` never resolves an arbitrary pk
# that could turn out unusable, only a pre-resolved `ResolvedModel` or the
# role -- it exists in this map only so `VisionUnavailable("connection_
# unavailable", ...)` (raised directly by `jobs.run_generate`) reads its
# `failure_kind` off the SAME map every other state does.
_UNAVAILABLE_FAILURE_KINDS = {
    "unbound": GenerationJob.FailureKind.ROLE_UNBOUND,
    "unreachable": GenerationJob.FailureKind.ENGINE_UNREACHABLE,
    "connection_unavailable": GenerationJob.FailureKind.CONNECTION_UNAVAILABLE,
}


class VisionUnavailable(RuntimeError):
    """Raised by `submit_job` when preflight is not `ready`. Carries the
    machine-readable `state` (for the caller's status code), the
    operator-facing `message` (already written for display), and
    `failure_kind` (the same vocabulary a FAILED row carries)."""

    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state = state
        self.message = message
        # The machine-readable kind, from the same vocabulary a FAILED
        # row carries (`GenerationJob.FailureKind`) -- one vocabulary, two
        # carriers, because these two failures happen before any row
        # exists. `.get`, not `[...]`: a KeyError raised from inside an
        # exception's constructor would turn an honest 503 into a 500.
        self.failure_kind = _UNAVAILABLE_FAILURE_KINDS.get(state, "")


def role_unbound_message() -> str:
    """The operator-facing sentence for "nothing assigned" -- built once so
    `preflight()`'s own two branches and `jobs.run_generate`'s claim-time
    re-check (which raises `VisionUnavailable` directly for a `ValueError`
    out of `jobs._resolve_model`, T9 fix-round Q1) can never drift apart on
    the wording."""
    return f"No model assigned for Image generation — assign one at {reverse('inference-console')}"


def _health_check(resolved: ResolvedModel) -> PreflightResult:
    """Health-check an ALREADY-resolved binding.

    The half of `preflight` that is identical whether the model came from
    the role or from a caller's own pick, written once so a picked model can
    never be checked by a different rule than a bound one. Engine-agnostic:
    the message names the engine and endpoint from the binding rather than
    hardcoding a product name.

    C-07 half B: the actual round trip (`is_healthy`) is cached for
    `probe_cache.CACHE_TTL_SECONDS` seconds, keyed on
    `f"{engine}|{endpoint}"` -- `tools/vision/probe_cache.py`'s module
    docstring says what invalidates it and why it is this column's own
    cache rather than the registry's.
    """

    def _probe(_key: str) -> bool:
        try:
            return get_engine(resolved.engine).is_healthy(resolved.endpoint)
        except Exception:  # noqa: BLE001 -- an engine that blows up is unreachable, not a 500
            logger.exception(
                "Health check failed for %s at %r", resolved.engine, resolved.endpoint
            )
            return False

    healthy = probe_cache.cached(f"{resolved.engine}|{resolved.endpoint}", _probe)

    if not healthy:
        return PreflightResult(
            "unreachable",
            resolved,
            f"The image engine ({resolved.engine}) at {resolved.endpoint} is not reachable.",
        )
    return PreflightResult("ready", resolved, "")


def preflight(resolved: ResolvedModel | None = None) -> PreflightResult:
    """Resolve the image-generation role and health-check its engine.

    `resolved` is a binding the CALLER already picked -- the per-generation
    model picker's choice, resolved through
    `models.registry.bindings.resolve_connection` before it gets here. It
    stands in for the role binding for this one call and never changes it,
    exactly as the Ask-time picker's override does for `rag.answer`. When it
    is given, the role is not consulted at all: a caller who brought its
    own model is not blocked by an unbound role.

    Three honest states either way, mirroring `tools.rag.views._precheck_models`:
    `ready`, `unbound` (nothing assigned -- only reachable on the role
    path), or `unreachable` (assigned but the engine isn't answering).
    """
    if resolved is not None:
        # C-07 half B: an operator's own per-generation pick deserves a
        # live answer, not a chat turn's 30-second-old cached one -- see
        # `probe_cache`'s module docstring.
        probe_cache.invalidate()
        return _health_check(resolved)

    try:
        resolved = bindings.resolve(VISION_GENERATE_ROLE)
    except ValueError:
        logger.debug("No inference binding resolved for %r", VISION_GENERATE_ROLE, exc_info=True)
        return PreflightResult("unbound", None, role_unbound_message())
    except Exception:  # noqa: BLE001 -- log detail, then degrade to the honest banner
        logger.exception("Unexpected error resolving %r", VISION_GENERATE_ROLE)
        return PreflightResult("unbound", None, role_unbound_message())

    return _health_check(resolved)


def live_options(operation: Operation, resolved) -> dict[str, tuple[str, ...]]:
    """Engine-reported options for every param of `operation` whose options
    the ENGINE owns: `"choice"` params with no fixed `choices` (samplers,
    schedulers) and every `"asset"` param (LoRAs, upscalers, VAEs).

    Returns `{}` when the role is unbound or the engine cannot be reached;
    a single param that fails reports `()` and the form degrades for that
    field alone. Never raises -- an option list is not worth a 500, and
    every optional protocol member is read with `getattr` so an adapter
    that predates assets simply reports nothing.
    """
    if resolved is None:
        return {}
    try:
        engine = get_engine(resolved.engine)
    except Exception:  # noqa: BLE001 -- an engine name that isn't registered is not a 500
        logger.debug("get_engine(%r) failed", resolved.engine, exc_info=True)
        return {}

    list_choices = getattr(engine, "list_choices", None)
    list_assets = getattr(engine, "list_assets", None)

    def safe(call, key: str) -> tuple[str, ...]:
        try:
            return tuple(call())
        except Exception:  # noqa: BLE001 -- never 500 over an option list
            logger.debug("Listing options for %r failed", key, exc_info=True)
            return ()

    options: dict[str, tuple[str, ...]] = {}
    for param in operation.params:
        if param.kind == "choice" and not param.choices and list_choices is not None:
            options[param.key] = safe(
                lambda p=param: list_choices(resolved.endpoint, p.key), param.key
            )
        elif param.kind == "asset" and list_assets is not None:
            options[param.key] = safe(
                lambda p=param: [asset.asset_id for asset in list_assets(resolved.endpoint, p.asset_kind)],
                param.key,
            )
    return options


def live_defaults(operation: Operation, resolved) -> dict:
    """Where `operation`'s form should START for the SELECTED model.

    The exact twin of `live_options` above, and for the same reason: the
    schema declares what a param IS, and only the engine knows what THIS
    model's graph wants it to be -- a distilled build samples in a handful
    of steps and fixes its guidance, and a form that opened at the schema's
    numbers would cost the operator a wrong, slow generation to discover it.

    Reported keys the operation does not declare are DROPPED: an engine may
    describe a param, never invent one. Returns `{}` for an unbound role, an
    unregistered engine name, an adapter with no such member, or one that
    raises -- an opening value is never worth a 500, and the schema's own
    default is always a truthful fallback.
    """
    if resolved is None:
        return {}
    try:
        engine = get_engine(resolved.engine)
    except Exception:  # noqa: BLE001 -- an unregistered engine name is not a 500
        logger.debug("get_engine(%r) failed", resolved.engine, exc_info=True)
        return {}
    reader = getattr(engine, "param_defaults", None)
    if reader is None:
        return {}
    try:
        reported = dict(reader(operation.key, dict(resolved.config or {})))
    except Exception:  # noqa: BLE001 -- never 500 over a default
        logger.debug("param_defaults failed for %r", operation.key, exc_info=True)
        return {}
    declared = {param.key for param in operation.params}
    return {key: value for key, value in reported.items() if key in declared}


def live_ignored(operation: Operation, resolved) -> dict[str, str]:
    """Which of `operation`'s params the SELECTED model's graph cannot
    honour at all, mapped to the operator-facing reason.

    The negative twin of `live_defaults` above, and read the same way:
    `{}` for an unbound role, an engine name that is not registered, an
    adapter with no such member, or one that raises -- a disabled field
    is never worth a 500, and rendering the field ENABLED is the honest
    degradation (nothing was learned, so nothing is claimed).

    Reported keys the operation does not declare are DROPPED, exactly as
    `live_defaults` drops them: an engine may annotate a param, never
    invent one. This is what lets the page disable a field and say why
    (ADR 0012 D-EDIT-13) without ever importing a graph template.
    """
    if resolved is None:
        return {}
    try:
        engine = get_engine(resolved.engine)
    except Exception:  # noqa: BLE001 -- an unregistered engine name is not a 500
        logger.debug("get_engine(%r) failed", resolved.engine, exc_info=True)
        return {}
    reader = getattr(engine, "ignored_params", None)
    if reader is None:
        return {}
    try:
        reported = dict(reader(operation.key, dict(resolved.config or {})))
    except Exception:  # noqa: BLE001 -- never 500 over a reason string
        logger.debug("ignored_params failed for %r", operation.key, exc_info=True)
        return {}
    declared = {param.key for param in operation.params}
    return {key: str(value) for key, value in reported.items() if key in declared}


def fill_engine_blanks(
    operation: Operation, params: dict, resolved, keys: set[str] | None = None
) -> dict:
    """`params`, with every blank ENGINE-OWNED `"choice"` param in `keys`
    filled from what the selected model reports.

    `keys` defaults to `live_ignored(operation, resolved)` -- the page's
    rule. A field the console DISABLED (this model's graph cannot honour
    it) submits nothing, and `validate_params` refuses a blank `"choice"`
    regardless of `required` (operations.py) -- so without this, a flux2
    txt2img submission would 400 on a control the operator was never
    allowed to touch. A caller with a different question passes its own
    set: `tools.vision.tools._fill_engine_params` fills every blank
    engine-owned choice, because a TOOL caller may simply have omitted
    one.

    The value is `live_defaults`'s, else the first entry `live_options`
    reports, else the param is left exactly as it came -- the same order
    `build_form` opens its own fields in.

    Only `"choice"` params are ever filled. `negative_prompt` is a
    `"text"` param and stays blank on purpose: writing a value into the
    job record for text the graph never read would be a lie about what
    ran, and nothing requires it. A `required` param that is also ignored
    is a TEMPLATE bug, not a case to paper over -- nothing fills it, and
    `validate_params` refuses it loudly.

    Never mutates `params`; the returned dict is always a copy, so a
    caller's `{**cleaned_data, **files}` (upload objects included) is
    carried through untouched.
    """
    if resolved is None:
        return dict(params)
    if keys is None:
        keys = set(live_ignored(operation, resolved))
    fillable = {
        param.key for param in operation.params
        if param.kind == "choice" and param.key in keys
    }
    blank = {
        key for key in fillable
        if not str(params.get(key) or "").strip()
    }
    if not blank:
        return dict(params)

    defaults = live_defaults(operation, resolved)
    options = live_options(operation, resolved)
    filled = dict(params)
    for key in blank:
        if defaults.get(key):
            filled[key] = defaults[key]
        elif options.get(key):
            filled[key] = options[key][0]
    return filled


def operations_for_model(resolved: ResolvedModel | None) -> list[Operation]:
    """Every registered image-generation operation the SELECTED model can
    actually run, in registration order.

    The platform's operation registry says what modes EXIST; only the engine
    can say which of them it has a graph for THIS model -- and for a
    multi-file family that answer depends on the family the operator
    declared on the connection (`config["family"]`, ADR 0012 D-EDIT-2). A
    checkpoint connection declares none and gets the checkpoint modes.

    Silence is never read as refusal. `None` (nothing selected and nothing
    bound), an engine name that is not registered, an adapter with no
    `supported_operations` member at all, or one that raises -- each returns
    the FULL registered list, because "this engine has no opinion" and "this
    engine supports nothing" are different facts and only the second should
    ever empty the page.
    """
    available = operations_for(IMAGE_GENERATION_CAPABILITY)
    if resolved is None:
        return available
    try:
        engine = get_engine(resolved.engine)
    except Exception:  # noqa: BLE001 -- an unregistered engine name is not a 500
        logger.debug("get_engine(%r) failed", resolved.engine, exc_info=True)
        return available

    reader = getattr(engine, "supported_operations", None)
    if reader is None:
        return available
    try:
        supported = set(
            reader(
                resolved.model_id,
                resolved.endpoint,
                # `config_family` (final-review finding 9): the ONE
                # normalization of `config["family"]` -- a JSON `null`
                # reads as `""`, exactly like an absent key -- shared with
                # `models.contracts.engines.comfyui.ComfyUIGenerator.submit`.
                family=config_family(resolved.config),
            )
        )
    except Exception:  # noqa: BLE001 -- never 500 over an operation list
        logger.debug("supported_operations failed for %r", resolved.model_id, exc_info=True)
        return available
    return [operation for operation in available if operation.key in supported]


def union_params(operations: Sequence[Operation]) -> tuple[Param, ...]:
    """Every param `operations` declare, de-duplicated by key, FIRST
    declaration wins, in the order the operations were registered.

    The SET and the ORDER the console's one constant form renders (ADR
    0012 D-EDIT-13): the operator sees the same 20 fields whichever mode
    is picked, so switching modes never makes a control vanish. The
    first-declaration rule is what makes the order stable -- `steps` sits
    where `txt2img` put it even on an `edit` page, though `edit`'s own
    `Param` (default 20, not 25) is what renders there.

    Pure and engine-free: this is a fact about the REGISTRY, so a caller
    with no binding gets the same answer as one with a model picked.
    """
    seen: set[str] = set()
    union: list[Param] = []
    for operation in operations:
        for param in operation.params:
            if param.key in seen:
                continue
            seen.add(param.key)
            union.append(param)
    return tuple(union)


@dataclass(frozen=True)
class OperationState:
    """One registered operation, and whether the SELECTED model can run
    it -- with the sentence to show when it cannot."""

    operation: Operation
    supported: bool
    reason: str  # "" when supported


def operation_states(resolved: ResolvedModel | None) -> tuple[OperationState, ...]:
    """Every REGISTERED image-generation operation, each marked supported
    or not for `resolved`, in registration order.

    `operations_for_model` answers "what can this model run"; this answers
    the question the operator actually has in front of a chooser -- "what
    exists, and why can't I pick that one". One `operations_for_model`
    call answers for the whole list; a health check is not repeated per
    operation, the same dedup `operation_catalog` already applies.

    The wording depends on what the CONNECTION declared, because that is
    what the answer depends on (ADR 0012 D-EDIT-2): a connection with a
    family names it, one without says only that the engine has no such
    graph. Nothing bound and nothing picked is "no opinion" --
    `operations_for_model` returns the full registry, so every operation
    is supported and no reason is invented.
    """
    supported_keys = {operation.key for operation in operations_for_model(resolved)}
    family = config_family(resolved.config) if resolved is not None else ""
    states = []
    for operation in operations_for(IMAGE_GENERATION_CAPABILITY):
        if operation.key in supported_keys:
            states.append(OperationState(operation, True, ""))
            continue
        reason = (
            f"No {operation.label.lower()} graph for the {family} family."
            if family
            else f"This model's engine has no {operation.label.lower()} graph."
        )
        states.append(OperationState(operation, False, reason))
    return tuple(states)


def operation_catalog(resolved: ResolvedModel | None = None) -> list[dict]:
    """Every registered operation, as data, marked supported or not for the
    selected model, with the BOUND engine's live option lists filled in.

    `models.contracts.operations.describe` answers "what does the schema
    say"; only an engine can answer "what does this install actually
    have" (`sampler`, `scheduler`, the LoRAs and upscalers on disk). This
    is those two answers in one object -- the thing a tool reads before it
    submits, and the thing a `?format=json` schema endpoint would return.
    `resolved` is the model the caller picked; with none, the role binding
    answers, exactly as before. The catalog lists EVERY registered
    operation, each marked `"supported"` and carrying an
    `"unsupported_reason"` when it is not (`operation_states`). This
    reverses the earlier rule ("never tell a tool about a mode it cannot
    perform", ADR 0012 D-EDIT-13): a caller that reads the array as "modes
    I can run" must now filter on `"supported"`, and in exchange it can say
    WHY a mode is unavailable instead of pretending it does not exist.
    Each param also carries `"ignored"` -- the reason this model's graph
    cannot honour it, or `None` -- from `live_ignored`.

    `preflight()` is called ONCE for the whole catalog, not once per
    operation: a health check is a blocking HTTP round trip (up to seconds
    when an engine is down), and every operation would resolve the same
    role and check the same endpoint -- the same dedup reasoning
    `tools.rag.views._precheck_models` applies to its two roles.

    Readiness is deliberately NOT part of the return value: `preflight()`
    is the function that answers "is the engine there", and a caller that
    needs to know asks it. With nothing bound, every shape here is still
    true -- only the engine's lists are missing, and a param whose options
    the engine owns simply carries none.
    """
    check = preflight(resolved)
    catalog = []
    for state in operation_states(check.resolved):
        operation = state.operation
        entry = describe(operation)
        entry["supported"] = state.supported
        entry["unsupported_reason"] = state.reason
        options = live_options(operation, check.resolved)
        defaults = live_defaults(operation, check.resolved)
        ignored = live_ignored(operation, check.resolved)
        for param in entry["params"]:
            param["ignored"] = ignored.get(param["key"])
            if param["key"] in options:
                param["options"] = list(options[param["key"]])
            if param["key"] in defaults:
                param["default"] = defaults[param["key"]]
        catalog.append(entry)
    return catalog


def _generator_for_job(job: GenerationJob):
    """An `ImageGenerator` for the binding recorded ON `job`.

    Polling must ask the engine that actually HAS the job. Re-resolving the
    role instead would, after a rebind, ask a different queue about a
    `prompt_id` it never saw -- which answers `lost`, and the operator is
    told the engine forgot a job that is running fine.

    A row from before the `endpoint` column existed carries a blank one and
    has no address to poll; those fall back to the role binding, which is
    the same behaviour they had when they were written.
    """
    if not job.endpoint:
        return get_image_generator()
    return get_image_generator_for(
        ResolvedModel(
            engine=job.engine,
            model_id=job.model_id,
            endpoint=job.endpoint,
            config=dict(job.model_config or {}),
        )
    )


# What a stored-file reference looks like, and the two things it can name.
# A REFERENCE, not a path: it is JSON-safe (so a queue payload or a future
# tool call can carry it), it names a row this module owns, and resolving
# it re-reads the platform's own copy of the bytes -- nothing about the
# filesystem crosses a caller's hands.
INPUT_REFERENCE_KINDS = ("output", "input", "document")
_REFERENCE_SHAPE = (
    "a reference looks like output:<id>, input:<id> or document:<id>"
)


def parse_input_reference(reference: str) -> tuple[str, int]:
    """Split `"output:12"` into `("output", 12)`.

    DELEGATES to `agents.contracts.artifacts.parse_artifact` (chat image
    artifacts, 2026-09-16) rather than re-splitting the string here. Two
    reasons, and the second is the load-bearing one:

    - `document` references may carry a percent-encoded display TITLE
      behind a SECOND colon (`mint_artifact`'s own R5 suffix -- the
      attachments provider mints with one), so the string a model is
      handed is not always `kind:id`. That peeling rule already exists,
      in exactly one place, and copying it here is how two parsers start
      disagreeing.
    - `agents/contracts/tests/test_artifacts.py::TestParseArtifact
      Agreement` asserts the two answer identically on every kind. With
      the delegation in place that is true by construction instead of by
      vigilance.

    Raises `ValueError` with operator-facing text for anything else --
    THE SAME SENTENCE THIS FUNCTION HAS ALWAYS RAISED, only with a third
    kind named in the shape clause: this value arrives from a queue
    payload, a link, or a tool call, so it is untrusted input and a clear
    refusal beats a confusing failure later.
    """
    try:
        kind, pk = parse_artifact(reference)
    except ValueError:
        raise ValueError(
            f"{reference!r} is not a stored-image reference — {_REFERENCE_SHAPE}."
        ) from None
    if kind not in INPUT_REFERENCE_KINDS:
        # Vacuous today (the two tuples match), kept because they are two
        # different vocabularies that happen to coincide: an artifact kind
        # that is never a generation INPUT would be admitted silently.
        raise ValueError(
            f"{reference!r} is not a stored-image reference — {_REFERENCE_SHAPE}."
        )
    return kind, pk


def stored_input(reference: str, principal) -> store.StoredFile:
    """The file `reference` names, as an upload-shaped object -- IF
    `principal` may see it.

    The point of the indirection: `submit_job(files=...)` takes one kind of
    thing, and a browser upload and a gallery image both become that thing
    here -- so a queued job, a "use this image" link, and a form post all
    travel the SAME submission path.

    Raises `ValueError` for a malformed reference, a row that does not
    exist, a row whose file has been deleted (a job's directory is removed
    with the job), OR a row `principal` may not see -- IA-1 SECURITY FIX:
    an invisible reference behaves EXACTLY like a nonexistent one, so a
    member cannot derive from another principal's generated image or job
    input by guessing its id (`_visible_referenced_row` is the one place
    this is decided).

    THE `document` KIND (chat image artifacts, 2026-09-16) resolves
    through `_stored_document_input` below -- a REGISTERED DOTTED PATH,
    never an import: `tools/vision` may not import the column that owns
    documents, and vice versa. THE `output`/`input` PATH BELOW IS
    UNCHANGED, BYTE FOR BYTE -- same row resolution, same two sentences.
    Adding a kind must never alter what an existing one answers.

    PARSED ONCE, here, and the pair is handed down. The dispatch and the
    branch both need `(kind, pk)`, and a second `parse_input_reference`
    call inside the branch would be a second chance for the two to
    disagree about what the same string says.
    """
    kind, pk = parse_input_reference(reference)
    if kind == "document":
        return _stored_document_input(kind, pk, principal)
    row = _visible_referenced_row(reference, principal)
    if row is None:
        raise ValueError(f"{reference} does not name a stored image.")
    if not row.path or not os.path.isfile(row.path):
        raise ValueError(f"The file for {reference} is no longer on disk.")
    return store.StoredFile(row.path, content_type=row.media_type or "")


def _stored_document_input(kind: str, pk: int, principal) -> store.StoredFile:
    """`stored_input`'s `document` branch (chat image artifacts,
    2026-09-16) -- the owner's third outcome, "those images should ... be
    able to be referenced in other tasks through the use of other tools".

    RESOLVED THROUGH THE REGISTRY, NEVER AN IMPORT: `agents.contracts.
    artifacts.file_resolver_for("document")` answers with a dotted path
    the column that owns the kind registered at app start, and
    `resolve_dotted_path` turns it into the callable HERE, at call time.
    `tools/vision` may not import `tools/rag` (peer columns; `foundation/
    ops/tests/test_import_law.py` pins it) and this is how a file input
    reaches a row it does not own.

    NO REGISTERED RESOLVER == A DEAD REFERENCE, on purpose: the owning
    column is not installed on this box, so the reference cannot be live,
    and from here that is indistinguishable from a row that is gone.

    THE BARE `<kind>:<id>` FORM IS WHAT EVERY SENTENCE BELOW ECHOES,
    never the caller's own string. A `document` reference MAY carry a
    percent-encoded display title (`mint_artifact`'s R5 suffix), which is
    somebody's uploaded FILENAME -- and an operator-facing refusal built
    out of an uploaded filename is how a filename becomes prose somebody
    reads. The sentence SHAPES are the `output:`/`input:` ones verbatim;
    only the reference inside them is normalized.

    TAKES `(kind, pk)`, ALREADY PARSED, from its one caller -- never the
    raw string to re-parse (review round 1, finding 14).
    """
    bare = f"{kind}:{pk}"

    resolver = file_resolver_for(kind)
    if resolver is None:
        raise ValueError(f"{bare} does not name a stored image.")
    try:
        artifact = resolve_dotted_path(resolver)(pk, principal)
    except LookupError:
        raise ValueError(f"{bare} does not name a stored image.") from None

    media_type = artifact.media_type or ""
    if not media_type.startswith("image/"):
        # BYTE-STABLE, and asserted with `==` in `tools/vision/tests/
        # test_document_references.py`: a model reads this to decide what
        # to try next. Two sentences, because "a PDF" and "we have no
        # idea what this is" are different facts and neither should be
        # dressed up as the other.
        what = media_type if media_type else "a file of unknown type"
        raise ValueError(f"{bare} is {what}, not an image.")
    if not artifact.path or not os.path.isfile(artifact.path):
        # The resolver's own contract already refuses a file-less row;
        # this is the RACE behind it (deleted between the lookup and
        # here), answered with the sentence that condition already has.
        raise ValueError(f"The file for {bare} is no longer on disk.")
    return store.StoredFile(
        artifact.path, name=artifact.name, content_type=media_type,
    )


def _referenced_row(reference: str):
    """The `GeneratedOutput` or `JobInput` row `reference` names, or None
    -- UNGATED, no visibility check.

    Used ONLY by `discard_staged_inputs`, which cleans up references the
    SAME request just staged moments earlier in the SAME submission --
    there is no second principal to be private from, and gating it would
    only risk leaving an orphaned upload behind on a failed enqueue.
    Every other caller resolves through `_visible_referenced_row` instead.

    VISION'S OWN TWO KINDS ONLY (chat image artifacts, 2026-09-16).
    `INPUT_REFERENCE_KINDS` grew a third, and the `else JobInput` below
    would otherwise read `document:42` as `JobInput` #42 -- a row with
    nothing to do with document 42. For `discard_staged_inputs`
    specifically that is DESTRUCTIVE: a failed enqueue carrying
    `input_x=document:42` would delete a staged upload, with its file,
    belonging to somebody else. A kind this function does not own
    answers `None`, which every caller already handles as "not a row of
    mine", exactly as a malformed string already does.
    """
    kind, pk = parse_input_reference(reference)
    if kind not in ("output", "input"):
        return None
    model = GeneratedOutput if kind == "output" else JobInput
    return model.objects.filter(pk=pk).first()


def _visible_referenced_row(reference: str, principal):
    """The `GeneratedOutput` or `JobInput` row `reference` names, IF
    `principal` may see it -- or None otherwise, exactly as if the
    reference named nothing at all (IA-1).

    A generated output's visibility is `tools.vision.visibility.
    visible_jobs` -- the SAME predicate `job_status`/`gallery` use, so a
    reference cannot show a row those routes would 404 on.

    A `JobInput` with a job (an ordinary generation's own input) follows
    that job's visibility too. A STAGED `JobInput` (`job_id is None`) has
    no owner column to check against -- `tools.vision.models.JobInput`
    carries none, and adding one is a fifth migration out of IA-1's scope
    -- so it resolves for an administrator only, the same ruling
    `views.input_file` applies to the identical row shape. On an open box
    `is_admin` is always True, so this changes nothing there.

    VISION'S OWN TWO KINDS ONLY (chat image artifacts, 2026-09-16), for
    the reason `_referenced_row` above states in full: a third kind
    falling through to `JobInput` would make `stored_input_exists`
    confirm a `document:` reference off a foreign job input, and the
    create page render `/vision/inputs/<that pk>/file/` as its preview.
    `document:` has exactly ONE door in this module,
    `_stored_document_input`, and it is reached from `stored_input`
    before this function is ever called.
    """
    kind, pk = parse_input_reference(reference)
    if kind not in ("output", "input"):
        return None
    if kind == "output":
        return GeneratedOutput.objects.filter(pk=pk, job__in=visible_jobs(principal)).first()
    job_ok = Q(job__in=visible_jobs(principal))
    if is_admin(principal):
        job_ok |= Q(job__isnull=True)
    return JobInput.objects.filter(pk=pk).filter(job_ok).first()


def stored_input_exists(reference: str, principal) -> bool:
    """True when `reference` still names a file on disk that `principal`
    may see.

    What a caller that only wants to DISPLAY a carried reference needs --
    the create page checking whether the thumbnail it is about to render
    still exists. `stored_input` would answer the same question by
    building a `StoredFile` and throwing it away; this asks it directly and
    treats a malformed reference, OR one `principal` may not see, as "no"
    rather than raising -- a stale OR foreign link leaves a normal empty
    form behind, not an error page and not an existence oracle.
    """
    try:
        row = _visible_referenced_row(reference, principal)
    except ValueError:
        return False
    return row is not None and bool(row.path) and os.path.isfile(row.path)


class InputReferenceError(ValueError):
    """A stored-image reference that cannot be used, and the param it
    answered.

    A `ValueError` still, so a caller with no form (a queued job, a tool)
    catches it exactly as it caught `stored_input`'s refusals before. The
    `param_key` exists for the caller that DOES have a form: the create
    page attaches the message to the field the operator is looking at,
    which is the one thing a single shared resolver would otherwise cost.
    """

    def __init__(self, param_key: str, message: str):
        super().__init__(message)
        self.param_key = param_key


def resolve_inputs(
    operation, references: dict[str, str], principal
) -> dict[str, store.StoredFile]:
    """Every `{param key: reference}` pair as upload-shaped files that
    `principal` may see.

    THE one resolver: the page's carried "use this image" references and a
    queue payload's `"inputs"` both land here, so a reference that the
    page accepts is a reference the queue accepts, and a reference either
    refuses is refused with the same sentence.

    A reference naming a param the operation does not declare as a file
    param is REFUSED, never dropped: running the generation without it
    would run a generation nobody asked for. That is the stricter of the
    two rules this replaced, and it is the correct one.

    `principal` is REQUIRED (IA-1 security fix): a member must not be
    able to derive from another principal's generated image or job input
    by naming its id in `input_<param>=output:<id>` -- an invisible
    reference is refused exactly like a malformed or dead one, through
    `stored_input`'s own `_visible_referenced_row` check.

    Returns lazy handles: `store.StoredFile` opens nothing until its
    `chunks()` is iterated, so a caller resolving purely to prove a
    reference is live (the create page, before it enqueues) pays a
    database read and no file read at all.
    """
    if not references:
        return {}
    declared = operation.file_param_keys()
    undeclared = sorted(set(references) - declared)
    if undeclared:
        raise InputReferenceError(
            undeclared[0],
            f"operation {operation.key!r} declares no file parameter named "
            f"{', '.join(undeclared)} — it takes {sorted(declared)}.",
        )
    resolved: dict[str, store.StoredFile] = {}
    for param_key, reference in references.items():
        try:
            resolved[param_key] = stored_input(reference, principal)
        except ValueError as exc:
            raise InputReferenceError(param_key, str(exc)) from exc
    return resolved


def stage_upload(param_key: str, uploaded) -> str:
    """Record one browser upload as a stored input that has no job yet,
    and return the `input:<id>` reference that names it.

    THE seam that lets the page enqueue. A queue payload is JSON and a
    browser file is not, so the bytes go into the managed store here and
    the payload carries a reference to the row that owns them -- the same
    reference kind a gallery image or another job's input uses, resolved
    by the same `resolve_inputs`, and handed to `submit_job` as the same
    `store.StoredFile`. The queued job's `submit_job` then copies those
    bytes into ITS OWN directory and writes its own `JobInput`, exactly as
    it does for a file posted directly, so a job still owns its inputs and
    deleting a job still deletes them.
    """
    return "input:%d" % JobInput.objects.create(
        job=None,
        param_key=param_key,
        path=store.stage_input(uploaded),
        media_type=store.media_type_for_upload(uploaded),
    ).id


def prune_staged_inputs(older_than=None) -> int:
    """Delete staged uploads older than `settings.VISION_STAGED_UPLOAD_TTL`
    (or `older_than`), with their files, and return how many went.

    Prune-on-write, called by the page on EVERY submission (staging
    something or not -- an unconditional call is one rule instead of two,
    and the query is indexed and usually empty) -- the same grammar `models.queue.backend._prune_finished_jobs` and
    `tools.rag.services._prune_ask_records` use, because this codebase
    has no cron and will not grow one for this.

    A staged upload is NOT deleted when it is consumed: the queue's orphan
    sweep can re-run a job from scratch, and a payload whose references had
    already been deleted would fail a re-run that would otherwise have
    worked. Age is the honest rule instead.

    Only rows with NO job are touched -- a job's own input belongs to the
    job for as long as the job exists, however old it is -- and a row
    written before `created_at` existed reads NULL, never matches the
    cutoff, and is therefore never swept (every one of them is attached).
    """
    cutoff = timezone.now() - (older_than or settings.VISION_STAGED_UPLOAD_TTL)
    stale = list(JobInput.objects.filter(job__isnull=True, created_at__lt=cutoff))
    for row in stale:
        store.remove_staged_input(row.path)
        row.delete()
    return len(stale)


def discard_staged_inputs(references) -> None:
    """Delete the staged uploads `references` names, with their files.

    For the one case where waiting for the sweep would be pointless: the
    enqueue that was going to consume them failed, so nothing ever will.
    A reference that does not name a staged upload -- a gallery output, a
    job's own input, a row that is gone, a malformed string -- is skipped
    in silence: this is cleanup, and cleanup that raises leaves the caller
    reporting the wrong failure.
    """
    for reference in references:
        try:
            row = _referenced_row(reference)
        except ValueError:
            continue
        if row is None or not isinstance(row, JobInput) or row.job_id is not None:
            continue
        store.remove_staged_input(row.path)
        row.delete()


def submit_job(
    operation_key: str,
    raw_params: dict,
    files: dict | None = None,
    resolved: ResolvedModel | None = None,
    *,
    actor,
) -> GenerationJob:
    """Validate, record, and queue one generation.

    Order matters and is deliberate: schema validation FIRST (an invalid
    submission is a 400 about the parameters, never a 503 about the
    engine, and never costs a health check), then preflight before any row
    is written (no orphan jobs for an unbound role), then the row, then the
    engine call -- so the job's UUID already exists to be used as the
    engine-side filename prefix.

    `resolved` is the per-generation model the caller picked, already
    resolved (`models.registry.bindings.resolve_connection`). Passing it
    substitutes that binding for the role's, for this one job -- and the row
    written below records THAT binding, which is why recording the pick
    needs no new column: `engine`, `model_id`, `endpoint`,
    `model_fingerprint`, and `model_config` already document exactly what
    ran (D6).

    `actor` -- the `identity.contracts.principals.Principal` this job is
    stamped as (`identity.access.owner_fields`). REQUIRED, not defaulted:
    a default would be a fail-open default, silently writing a row no
    filter can reason about the moment a caller forgot it. Every caller
    already holds a principal -- `tools.vision.jobs.run_generate` reads
    it off the payload (`identity.contracts.principals.
    principal_from_payload`), `tools.vision.tools.run_generate` reads it
    off `ToolContext.principal`.

    The generator is built from the binding PREFLIGHT resolved, not from a
    second `resolve()` call: the row four lines up already documents that
    binding, and a rebind between the two resolutions would leave the row
    describing a model that did not run the job.

    An engine REJECTION (or any submission failure) does not raise: the job
    exists, it simply failed immediately, carrying the engine's own words.
    Raises `VisionUnavailable` when preflight fails and `ParamError` when the
    parameters don't fit the schema.
    """
    # C-07 half B: this is this column's own "run one generation" entry
    # point (`tools.vision.jobs.run_generate` and `tools.vision.tools.
    # run_generate` both funnel here) -- actually submitting a job must
    # never ride on a chat turn's cached reachability answer, even one
    # still inside its 30-second window. See `probe_cache`'s docstring.
    probe_cache.invalidate()

    operation = get_operation(operation_key)
    if operation is None:
        raise ValueError(f"Unknown operation {operation_key!r}")

    # Only files answering a `"file"` param the operation actually declares
    # count: `request.FILES` carries whatever was posted, and an undeclared
    # upload has no meaning, no `JobInput` row, and no business in the
    # managed store.
    file_params = operation.file_param_keys()
    supplied_files = {key: value for key, value in (files or {}).items() if key in file_params}

    # A file IS the value of its param. Merging here means a caller names
    # it once (`files=`) and validation still sees it -- `_file_reference`
    # reduces it to a JSON-safe basename for the row, and the bytes go to
    # the store below.
    #
    # Validation first: a submission that does not fit the schema is
    # malformed whether or not an engine is answering, so it must report
    # the 400 that is true rather than a 503 about the engine -- and a
    # rejected submission must not pay a health round trip to find out.
    params = validate_params(operation, {**raw_params, **supplied_files})

    check = preflight(resolved)
    if not check.ready:
        raise VisionUnavailable(check.state, check.message)

    resolved = check.resolved

    job = GenerationJob.objects.create(
        operation=operation.key,
        params=params,
        seed=params.get("seed"),
        engine=resolved.engine,
        model_id=resolved.model_id,
        endpoint=resolved.endpoint,
        model_fingerprint=resolved.fingerprint,
        model_config=dict(resolved.config or {}),
        status=GenerationJob.Status.QUEUED,
        **owner_fields(actor),
    )

    inputs: dict[str, Path] = {}
    for param_key, uploaded in supplied_files.items():
        path = store.store_input(job.id, param_key, uploaded)
        JobInput.objects.create(
            job=job,
            param_key=param_key,
            path=path,
            media_type=store.media_type_for_upload(uploaded),
        )
        inputs[param_key] = Path(path)

    request = GenerationRequest(
        operation=operation.key,
        model_id=resolved.model_id,
        params=params,
        inputs=inputs,
        client_ref=str(job.id),
    )

    # HOISTED above the submission try/except (review round 1, finding
    # 1): `get_image_generator_for` can itself raise a `ValueError` --
    # `models.contracts.gateway`'s own "no `build_image_generator`"
    # case, reachable only if an operator bound the image-generation
    # role to a non-generating engine. That is a real MISCONFIGURATION
    # fault, not a template miss, and must never be caught by the
    # narrower `ValueError` -> `ENGINE_REJECTED` clause below, which
    # exists for exactly one thing (see that clause's own comment). A
    # separate try/except here keeps it classified `ENGINE_FAILED`,
    # with a traceback, like every other real submission fault.
    try:
        generator = get_image_generator_for(resolved)
    except Exception as exc:  # noqa: BLE001 -- the job exists; report why it never started
        logger.exception("Building an image generator for %s failed", resolved.engine)
        return _fail(
            job,
            f"The image engine did not accept the job: {exc}",
            GenerationJob.FailureKind.ENGINE_FAILED,
        )

    try:
        engine_ref, payload = generator.submit(request)
    except GenerationRejected as exc:
        return _fail(job, str(exc), GenerationJob.FailureKind.ENGINE_REJECTED)
    except json.JSONDecodeError as exc:
        # MUST precede the bare `ValueError` clause below --
        # `json.JSONDecodeError` IS a `ValueError` subclass, and Python
        # tries `except` clauses in order, so a narrower subclass caught
        # AFTER a broader one is never reached. `ComfyUIGenerator.
        # submit`'s own `response.json()` on a >=400 response
        # (`models.contracts.engines.comfyui.py`) raises this when the
        # body is not JSON at all -- a 502 gateway's HTML page, say.
        # That is a real transport/engine fault, not a routine template
        # miss, and must keep the SAME classification and traceback log
        # every other submission fault gets below, not the honest-miss
        # classification the next clause exists for.
        logger.exception("Submitting job %s to %s failed", job.id, resolved.engine)
        return _fail(
            job,
            f"The image engine did not accept the job: {exc}",
            GenerationJob.FailureKind.ENGINE_FAILED,
        )
    except ValueError as exc:
        # Fix 5 (image-model-trace.md, Follow-up 3): a template MISS
        # (`models.contracts.engines.comfyui_workflows.get_template`'s
        # own `ValueError`) is a routine, ex-ante-knowable condition --
        # Fix 3 (`tools/vision/tools.py`) already refuses it before a
        # row exists on the tool path, so reaching this is the
        # queued-job path or a caller that bypassed that check, not a
        # fault. This is the ONE `ValueError` left reaching this clause
        # now that `get_image_generator_for`'s own (hoisted above, its
        # own try/except) and `json.JSONDecodeError` (caught above) are
        # excluded from it. Same classification as `GenerationRejected`
        # for the same reason: neither is an engine FAULT, and this one
        # should not log a traceback for a condition the platform
        # already has a name for. Message construction unchanged
        # (cosmetic reclassification only, per the fix plan) -- only
        # `failure_kind` moves.
        return _fail(
            job,
            f"The image engine did not accept the job: {exc}",
            GenerationJob.FailureKind.ENGINE_REJECTED,
        )
    except Exception as exc:  # noqa: BLE001 -- the job exists; report why it never started
        logger.exception("Submitting job %s to %s failed", job.id, resolved.engine)
        return _fail(
            job,
            f"The image engine did not accept the job: {exc}",
            GenerationJob.FailureKind.ENGINE_FAILED,
        )

    job.engine_ref = engine_ref
    job.engine_payload = payload
    job.save(update_fields=["engine_ref", "engine_payload"])
    return job


def correlate_queue_job(job: GenerationJob, queue_job_id: int) -> None:
    """Stamp `queue_job_id` onto `job` -- the correlation
    `tools.vision.views.queue_job_status` reads to find the generation a
    still-running queue job has produced.

    LIVES HERE, not in `jobs.py` -- `GenerationJob.objects` is this
    module's and `tools/vision/visibility.py`'s alone under
    `foundation/ops/tests/test_column_boundaries.py`'s IA-1 gate. A bare
    `.update()` is not a create, but it is still a manager access, and
    `jobs.py` reaching it directly would be a second, unguarded door
    into the same table this task closes every other one of.
    """
    GenerationJob.objects.filter(pk=job.pk).update(queue_job_id=queue_job_id)
    job.queue_job_id = queue_job_id


def refresh_job(job: GenerationJob) -> GenerationJob:
    """Bring `job` up to date with the engine and return it.

    Sets a TRANSIENT `job.unreachable` attribute (never a DB column) when the
    engine could not be reached: the card shows "engine unreachable, still
    checking" and the job itself is left exactly as it was -- a network
    hiccup must never turn a running job into a failed one. Also sets a
    TRANSIENT `job.engine_position`, alongside `unreachable`. Both are
    stamped onto whichever object this actually returns (`job` or a
    freshly-read `current`, below) -- a caller can trust `getattr(result,
    "unreachable", False)` regardless of which internal branch answered.

    Idempotent: a terminal job returns immediately without an engine call,
    so polling costs nothing once a job has finished.

    SAFE UNDER CONCURRENT CALLERS on the SAME row (2026-09-03 fix batch,
    item D; widened in review): the web poll (`job_status`/
    `queue_job_status`) and the queued worker's own `wait_for` loop
    (`tools.vision.jobs.run_generate`) both call this directly, each on
    its own fetched `GenerationJob` object, and can be mid-flight on the
    same row at once. GUARANTEED: every branch below that WRITES the row
    (`running`, `failed`, `lost`, `done`) re-reads and LOCKS it
    (`select_for_update()`) immediately before writing, and returns the
    row AS-IS the instant that fresh read is already terminal -- so a
    stale caller, still holding an in-memory copy that looks
    `queued`/`running`, can never overwrite an already-committed
    `done`/`failed` row in EITHER direction: not by re-finalizing `done`
    a second time under a new `GeneratedOutput` id (item D's original
    defect -- the id is always one BELOW the surviving row's, because
    the burned pk is never reused), and not by stomping a committed
    `done` row with `running` or `failed` because THIS caller's own
    stale engine answer disagreed with what already happened. NOT
    covered, because nothing durable is at stake there: the `queued`
    branch and the two engine-unreachable branches only ever set the
    TRANSIENT attributes above, never a column, so there is nothing for
    a race to corrupt.
    """
    job.unreachable = False
    # TRANSIENT, exactly like `unreachable` above: the card renders it, no
    # column stores it, and a job read straight from the database simply
    # does not have it (Django templates resolve a missing attribute to the
    # empty string, so the card's `{% if %}` is false and nothing renders).
    job.engine_position = None
    if job.is_terminal or not job.engine_ref:
        return job

    try:
        generator = _generator_for_job(job)
        state = generator.status(job.engine_ref)
    except Exception:  # noqa: BLE001 -- transient: leave the job alone and say so
        logger.debug("Refreshing job %s failed", job.id, exc_info=True)
        job.unreachable = True
        return job

    if state.state == "queued":
        job.engine_position = state.queue_position
        return job

    if state.state in ("running", "failed", "lost"):
        with transaction.atomic():
            # RE-READ AND LOCK before writing anything (see this
            # function's own docstring): `job` above may be a STALE
            # copy, and this state came from the SAME engine call
            # regardless -- no further engine round trip is needed for
            # any of these three, so the lock is taken immediately.
            current = GenerationJob.objects.select_for_update().get(pk=job.pk)
            current.unreachable = False
            current.engine_position = None
            if current.is_terminal:
                # Someone else already finalized this job (`done` or
                # `failed`) while this call was talking to the engine.
                # This caller's own answer is now moot -- return the
                # DURABLY COMMITTED row exactly as it stands, never
                # overwrite it with a stale `running`/`failed`.
                return current

            if state.state == "running":
                if current.status != GenerationJob.Status.RUNNING:
                    current.status = GenerationJob.Status.RUNNING
                    current.started_at = current.started_at or timezone.now()
                    current.save(update_fields=["status", "started_at"])
                return current

            if state.state == "failed":
                return _fail(
                    current,
                    state.error or "The image engine reported a failure.",
                    GenerationJob.FailureKind.ENGINE_FAILED,
                )

            return _fail(current, LOST_MESSAGE, GenerationJob.FailureKind.LOST)

    try:
        outputs = generator.fetch_outputs(job.engine_ref)
    except Exception:  # noqa: BLE001 -- finished but not fetchable yet: try again next poll
        logger.debug("Fetching outputs for job %s failed", job.id, exc_info=True)
        job.unreachable = True
        return job

    with transaction.atomic():
        # RE-READ AND LOCK the row before finalizing (2026-09-03 fix
        # batch, item D -- the "wrong number initially, refresh fixes
        # it" defect). `job` above may be STALE: the web poll
        # (`job_status`/`queue_job_status`) and the queued worker's own
        # `wait_for` loop (`tools.vision.jobs.run_generate`) each call
        # `refresh_job` on a `GenerationJob` object THEY fetched, and
        # both can be mid-flight on the SAME row at once, each still
        # holding a pre-finish `job.status`. `job.is_terminal` at the
        # top of this function only ever checks THAT caller's own
        # object, never the row's true current state -- so a second,
        # concurrent call already past that check would re-enter this
        # block after the first had committed, delete the first call's
        # just-committed `GeneratedOutput` row, and recreate it under a
        # NEW auto-incrementing id. Whatever had already rendered the
        # first id (a card, a `?format=json` poll response mid-flight
        # to a browser) then names a row that no longer exists -- and
        # the id is always one BELOW the surviving row's, because the
        # burned pk is never reused.
        #
        # `select_for_update()` closes this: a second, concurrent
        # transaction blocks here until the first commits, then reads
        # ITS true, post-commit state. Already terminal means someone
        # else finalized this job while this call was talking to the
        # engine -- return that DURABLY COMMITTED row as-is and never
        # touch `outputs` a second time. Sequential callers (as in the
        # regression test, no real thread needed) hit the exact same
        # branch: staleness is the whole bug, not wall-clock timing.
        current = GenerationJob.objects.select_for_update().get(pk=job.pk)
        current.unreachable = False
        current.engine_position = None
        if current.is_terminal:
            return current
        current.outputs.all().delete()
        for index, (filename, content, media_type) in enumerate(outputs):
            path = store.store_output(current.id, index, filename, content)
            width, height = store.png_dimensions(content)
            GeneratedOutput.objects.create(
                job=current, index=index, path=path, media_type=media_type, width=width, height=height
            )
        current.status = GenerationJob.Status.DONE
        current.started_at = current.started_at or current.created_at
        current.finished_at = timezone.now()
        current.save(update_fields=["status", "started_at", "finished_at"])
    return current


def job_json(job: GenerationJob) -> dict:
    """One job as plain data -- the canonical representation every caller
    reads: the page's `?format=json`, the queue job's result
    (`tools.vision.jobs.run_generate`), and the chatbot tool.

    Paths are never exposed; a file is named by the URL that serves it, so
    a caller holds a handle to the platform's copy and never a location on
    a disk it cannot see.

    `unreachable` is the TRANSIENT attribute `refresh_job` sets, read with
    a default rather than assumed: `refresh_job` sets it on every call
    before anything else, so a caller that polled has a real value, and a
    row read straight out of the database honestly reports `False` --
    nothing has tried to reach the engine for it yet. It is not a column
    and must not become one: a network hiccup is not part of a job's
    durable record (see `refresh_job`).
    """
    return {
        "id": str(job.id),
        "operation": job.operation,
        "status": job.status,
        "error": job.error,
        "failure_kind": job.failure_kind,
        "queue_job_id": job.queue_job_id,
        "seed": job.seed,
        "params": job.params,
        "engine": job.engine,
        "model_id": job.model_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        # Seconds, derived (`GenerationJob.durations`) -- an agent reading
        # this must not have to subtract ISO strings, and a caller that
        # polls a RUNNING job gets a live number.
        "durations": job.durations,
        "stale": job.is_stale,
        "unreachable": bool(getattr(job, "unreachable", False)),
        "outputs": [
            {
                "id": output.id,
                "url": reverse("vision-output-file", args=[output.id]),
                "media_type": output.media_type,
                "width": output.width,
                "height": output.height,
            }
            for output in job.outputs.all()
        ],
        "inputs": [
            {
                "id": job_input.id,
                "param_key": job_input.param_key,
                "url": reverse("vision-input-file", args=[job_input.id]),
                "media_type": job_input.media_type,
            }
            for job_input in job.inputs.all()
        ],
    }


def wait_for(
    job: GenerationJob,
    timeout: float,
    interval: float = 1.0,
    on_poll: Callable[[GenerationJob, float], None] | None = None,
) -> GenerationJob:
    """Poll `refresh_job` until the job is terminal or `timeout` seconds
    pass, then return it as-is.

    For SYNCHRONOUS callers (tests, a future chatbot tool, a management
    command). The page never uses this -- it polls from the browser instead,
    so a request thread is never held open by a generation.

    `on_poll` (optional) is called after EVERY refresh with
    `(job, elapsed_seconds)`. This loop is the only place that knows a poll
    happened, which is why the seam is here rather than inside
    `refresh_job` (which a page also calls, once, per request, and which
    has no notion of elapsed time). `tools.vision.jobs.run_generate`
    passes the callback that turns each tick into a
    `JobContext.report_progress` call; every other caller passes nothing
    and this loop behaves exactly as it did. An exception raised by
    `on_poll` is deliberately NOT swallowed: a reporter is the caller's own
    code, and a silent except here would hide a bug in it behind a
    generation that looks fine.
    """
    started = time.monotonic()
    deadline = started + timeout
    while True:
        job = refresh_job(job)
        if on_poll is not None:
            on_poll(job, time.monotonic() - started)
        if job.is_terminal or time.monotonic() >= deadline:
            return job
        time.sleep(interval)


def delete_job(job: GenerationJob) -> None:
    """Delete a job, its child rows (cascade), its whole managed
    directory, AND best-effort-sweep the engine's own output/input
    folders for files this job left behind there (Engine files feature,
    2026-09-02: `store.remove_engine_files` never raises, so a missing
    bind mount or a permission error here can never turn a job delete
    into a failure). The gallery's bulk delete (`views.
    jobs_delete_selected`) already calls this same function per job, so
    it gets the sweep for free."""
    job_id = job.id
    job.delete()
    store.remove_job_files(job_id)
    store.remove_engine_files(job_id)


def _fail(job: GenerationJob, error: str, failure_kind: str) -> GenerationJob:
    """Mark `job` failed with `error` and `failure_kind`, stamping the
    finish time.

    `failure_kind` is REQUIRED, not defaulted: a failure that cannot say
    which kind it is, is exactly the thing this argument exists to
    abolish, and a default would let a new failure site quietly skip it.
    """
    job.status = GenerationJob.Status.FAILED
    job.error = error
    job.failure_kind = failure_kind
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error", "failure_kind", "finished_at"])
    return job
