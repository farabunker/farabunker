"""
Shared, pure "no usable model" message formatting -- `model_unavailable_
message` -- read by THREE callers today (W6 review NIT: this docstring used
to say "two entry points onto `answer_question`", which stopped being true
the moment `SearchView` (W6) started reusing the same function without ever
calling `answer_question` at all):

- the synchronous `AskView` pre-check (`tools/rag/views.py`), checking
  BOTH `rag.answer`/`rag.embed` together before calling `answer_question`;
- the queued `rag.ask` job's pre-run re-check (`tools/rag/jobs.py::
  run_ask`, T5), the SAME two-role check, immediately before it too calls
  `answer_question`;
- `tools.rag.views.SearchView`'s own `_precheck_embed_role` (W6), checking
  `rag.embed` ALONE -- this page never synthesizes an answer, so it never
  calls `answer_question`, but it still needs the exact same "unbound" /
  "unreachable" sentence for the one role it does depend on, so it reuses
  this module's `_ROLE_LABELS`/`UNBOUND`/`UNREACHABLE`/`model_unavailable_
  message` rather than growing a fourth copy of that vocabulary.

All three need to build the EXACT same operator-facing sentence from the
same (role -> cause) map, so it lives here once rather than drifting into
multiple copies.

The same three callers also share `unreachable_endpoints` (C-15): the
dedup-by-normalized-endpoint health-check probe that used to be written out
once per caller. It returns roles, not sentences -- each caller still
renders its own copy (a `Response`, an inline template string, or a
`RuntimeError`) from the roles it names as unreachable.

Kept out of `views.py` on purpose: that module is the module's HTTP surface
(imports `django.views.generic`, `django.http`, ...), not a shared seam.
`jobs.py` runs worker-side, inside a job handler -- it should not have to
drag in a view module's whole import graph merely to build the same string
the web 503 shows. This module has no Django REST/HTTP dependency at all:
`views.py` wraps `model_unavailable_message`'s return value in a
`JsonResponse` (`AskView`) or renders it inline in template context
(`SearchView`); `jobs.py` wraps it in a `RuntimeError` that becomes an
`InferenceJob.error` column. Same string, multiple transports.
"""
from __future__ import annotations

import logging

from models.contracts.bindings import ResolvedModel
from models.contracts.engines import get_engine
from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE, RAG_EXTRACT_ROLE, RAG_TRANSCRIBE_ROLE

logger = logging.getLogger(__name__)

# Roles `answer_question()` actually depends on -- both the AskView
# pre-check and a queued `rag.ask` job's pre-run re-check walk exactly this
# pair, in this order (the "mixed causes" sentence below joins clauses in
# this order too, so the message is deterministic regardless of dict
# iteration order). Deliberately does NOT include RAG_TRANSCRIBE_ROLE (T7):
# that role is checked ALONE (`tools.rag.media._resolve_role_or_fail`), one
# role at a time, never paired with `rag.answer`/`rag.embed` in a single
# check -- extending this tuple would change the two-role "both unbound"/
# "same endpoint unreachable" branches below, which only make sense for a
# check that always resolves exactly the SAME two roles together.
PRECHECK_ROLES = (RAG_ANSWER_ROLE, RAG_EMBED_ROLE)
# `_ROLE_LABELS` is intentionally wider than `PRECHECK_ROLES`: it's the
# vocabulary `model_unavailable_message`'s single-role branch
# (`len(causes) == 1`) reads from, and a solo transcription/extraction check
# (`tools.rag.media._resolve_role_or_fail`, one role at a time) always
# produces a `causes` dict of length <= 1 -- it can never trip the two-role
# branches above (those require `set(causes) == set(PRECHECK_ROLES)`, i.e.
# every PRECHECK_ROLES key present -- a cardinality check by SET, not
# `len()`, so it stays correct even if `PRECHECK_ROLES` ever grew a
# duplicate entry or a caller ever built `causes` some way other than
# iterating `PRECHECK_ROLES` itself), so adding each of these two labels
# (T7's `RAG_TRANSCRIBE_ROLE`, T8's `RAG_EXTRACT_ROLE`) is a minimal,
# additive extension of this module's existing single-role path, not a new
# code path of its own.
_ROLE_LABELS = {
    RAG_ANSWER_ROLE: "chat",
    RAG_EMBED_ROLE: "embedding",
    RAG_TRANSCRIBE_ROLE: "transcription",
    RAG_EXTRACT_ROLE: "image text extraction",
}

# Two distinct failure causes a role can have -- kept apart because they call
# for different operator action and different wording: UNBOUND means nothing
# is configured for the role (resolve() raised, or raised unexpectedly --
# either way there's no endpoint to call unreachable), UNREACHABLE means a
# binding exists but its engine failed (or errored on) the health check.
UNBOUND = "unbound"
UNREACHABLE = "unreachable"

_NO_MODEL_MESSAGE = "No language model is connected yet — set one up"
_ENDPOINT_UNREACHABLE_MESSAGE = (
    "The model endpoint is unreachable — check that your model server is running."
)


def score_floor_message(floor: float) -> str:
    """W4 (ADR 0014 §14): the honest no-answer sentence `tools.rag.
    retrieval.answer_question` returns when EVERY retrieved chunk scored
    below `RagSettings.retrieval_score_floor` for a question -- truthful
    ("nothing scored high enough"), not an error (the retrieval itself
    worked fine; nothing in the library was a good enough match), and never
    a model name. `floor` renders to two decimal places (`retrieval_score_
    floor`'s own operator-facing grammar, matching the History settings
    card's own display -- see `HistoryView`) so the sentence always names
    the exact threshold that was in effect, not a truncated/rounded
    approximation of it."""
    return f"Nothing in the library scored above the retrieval floor ({floor:.2f}) for this question."


def search_score_floor_message(floor: float) -> str:
    """W6 (ADR 0014 §18): the search page's (`tools.rag.views.SearchView`)
    own honest empty-state sentence when something was actually retrieved
    AND every retrieved chunk scored below `RagSettings.retrieval_score_
    floor` -- the search-page sibling of `score_floor_message` above, kept
    as its own function rather than reused verbatim because that one says
    "...for this question", which doesn't fit a page that runs a plain
    keyword/semantic query, not a natural-language question. Same `.2f`
    grammar, same truthful "retrieval worked, nothing cleared the bar"
    framing, never a model name.

    Callers must only reach for this copy when something was retrieved and
    a floor is set (`SearchView`'s own `nodes and floor > 0.0 and not
    hybrid` guard, mirroring `answer_question`'s identical guard in
    `tools.rag.retrieval`) -- an empty/no-match library or category with
    a floor configured still retrieves NOTHING, and gets `SEARCH_NO_
    RESULTS_MESSAGE` instead (W6 review MAJOR 1): naming a floor that
    wasn't even the reason for the empty result would be misleading."""
    return f"Nothing scored above the retrieval floor ({floor:.2f})."


# W6 (ADR 0014 §18): the search page's honest empty state when NOTHING was
# retrieved at all (the floor is off, or nothing in the library matched the
# query even before a floor could apply) -- kept apart from `search_score_
# floor_message` above since naming a floor that wasn't even the reason for
# the empty result would be misleading.
SEARCH_NO_RESULTS_MESSAGE = "Nothing in the library matched this search."

# W6: the search page runs retrieval synchronously in the request (see
# `tools.rag.views.SearchView`'s own docstring for why) -- an embedding
# call failure surfacing THERE, after the model precheck already passed
# (e.g. the engine went away in between, or a request the health check
# doesn't reproduce), must degrade to this copy rather than a 500.
SEARCH_FAILED_MESSAGE = "Something went wrong running this search — try again."


def _role_cause_clause(role: str, cause: str) -> str:
    """A lowercase clause naming `role`'s problem, for splicing into a sentence."""
    label = _ROLE_LABELS[role]
    if cause == UNBOUND:
        return f"the {label} model isn't set up yet"
    return f"the {label} model is unreachable"


def unreachable_endpoints(resolved: dict, *, log_prefix: str) -> set:
    """The roles in `resolved` whose endpoint does not answer (C-15).

    ONE PROBE PER NORMALIZED ENDPOINT, not one per role: two roles bound
    to the same model server are one question. The normalization is
    `endpoint.rstrip("/")`, matching `models/registry/views.py`'s own
    `norm_endpoint`.

    AN ENGINE THAT RAISES IS UNREACHABLE, not a 500 -- the exception is
    logged with `log_prefix` (the caller's own name for itself, because
    an operator reading "rag.ask re-check" and one reading "AskView" are
    looking at different surfaces) and the endpoint is recorded as down.

    RETURNS ROLES, NOT MESSAGES. Every caller renders its own copy: the
    ask page returns a `Response`, the search page returns a single-role
    message, and the job path returns a 3-tuple and deliberately says
    something different about a deleted connection pk (`tools/rag/
    jobs.py`'s own docstring records that divergence, which this
    extraction does not touch).
    """
    endpoint_health: dict[str, bool] = {}
    unreachable = set()
    for role, model in resolved.items():
        endpoint_key = model.endpoint.rstrip("/")
        if endpoint_key not in endpoint_health:
            try:
                endpoint_health[endpoint_key] = get_engine(model.engine).is_healthy(model.endpoint)
            except Exception:  # noqa: BLE001 -- log detail, then degrade to the caller's friendly copy
                logger.exception(
                    "%s: unexpected error health-checking %s endpoint %r (role %r)",
                    log_prefix, model.engine, model.endpoint, role,
                )
                endpoint_health[endpoint_key] = False
        if not endpoint_health[endpoint_key]:
            unreachable.add(role)
    return unreachable


def model_unavailable_message(causes: dict[str, str], resolved: dict[str, ResolvedModel]) -> str:
    """Build the friendly "model unavailable" sentence for a pre-check/
    re-check failure.

    `causes` maps each bad role (a subset of `PRECHECK_ROLES`) to why it's
    bad (`UNBOUND` or `UNREACHABLE`); `resolved` holds every role that did
    resolve, used here only to check whether two bad roles share one literal
    endpoint. The message must never claim more or less breakage than is
    actually true, so each combination gets its own wording rather than a
    single generic-plus-role-name template:

    - both roles unbound -> the original generic "no model connected" copy.
    - both roles resolved to the *same* endpoint and it's unreachable ->
      name the endpoint, not an arbitrary role (they're equally affected).
    - exactly one role bad -> name that role and its specific cause (setup
      vs. network -- these call for different operator action).
    - anything else (mixed causes, or the same cause at different endpoints)
      -> name every bad role with its own cause in one sentence.
    """
    if all(cause == UNBOUND for cause in causes.values()) and set(causes) == set(PRECHECK_ROLES):
        return _NO_MODEL_MESSAGE

    if (
        set(causes) == set(PRECHECK_ROLES)
        and all(cause == UNREACHABLE for cause in causes.values())
        and len({resolved[role].endpoint.rstrip("/") for role in causes}) == 1
    ):
        return _ENDPOINT_UNREACHABLE_MESSAGE

    if len(causes) == 1:
        ((role, cause),) = causes.items()
        label = _ROLE_LABELS[role]
        if cause == UNBOUND:
            return f"The {label} model isn't set up yet — assign one in the model console."
        return f"The {label} model is unreachable."

    clauses = [_role_cause_clause(role, causes[role]) for role in PRECHECK_ROLES if role in causes]
    sentence = "; ".join(clauses)
    return sentence[0].upper() + sentence[1:] + "."
