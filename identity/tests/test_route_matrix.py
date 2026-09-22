"""Every route × every principal × every posture × both settings of the
administrator-content toggle.

THE ROUTE LIST IS DERIVED, NOT TYPED, exactly as
`agents/chat/tests/test_never_500.py` derives its own: a route added to
any `urls.py` without a `ROUTE_RULES` entry fails here immediately,
naming itself, and a route with no driver raises a `KeyError` naming
itself. That `KeyError` -- not a silently shorter loop -- is what
"swept automatically" means.

KEEPS "vision" IN FARABUNKER_FEATURES: it calls `reverse()` throughout,
and `config/urls.py` mounts `/vision/` only with that flag.

FOUR PRINCIPALS, THREE COLUMNS HERE (spec §18.1 done-when 3). This
module sweeps anonymous, member and admin. The fourth -- the OPEN
principal -- is covered by two dedicated modules instead, because for
it the interesting assertion is not a status code:
`identity/tests/test_zero_queries.py` drives a representative GET of
every mount as the open principal and asserts no identity table is
queried at all, which is the claim that actually matters for it, and
`identity/tests/test_middleware.py::TestOpenPosture` (Task 7) asserts
every route answers exactly as it does today. Adding `open` as a fourth
`who` column here would parametrise ~340 further cells to re-assert
`_ADMITTED` everywhere, which is what "the box has one principal and it
is an administrator" already guarantees by construction.

(The spec's fourth principal is the entitlement owner, not the open
principal; in IA-1 there are no entitlements to own, so that column is
IA-2's -- noted here so the two readings of "four" do not get
conflated. Either way, three columns is the honest count for this
phase and the two modules above carry the rest.)
"""
from __future__ import annotations

import itertools
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import pytest
from django.conf import settings
from django.test import Client
from django.urls import NoReverseMatch, Resolver404, resolve, reverse

from agents.defaults import DEFAULT_AGENTS
from foundation.settings_area import with_assistant_flag
from foundation.settings_help import card_routes
from agents.models import Share, WorkstreamTaint
from agents.tests._helpers import _workstream
from identity.access import owner_fields
from identity.contracts.postures import (
    POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL,
)
from identity.routes import ROUTE_RULES
from identity.tests._helpers import (
    grant, make_admin, make_agent, make_category, make_connection, make_conversation,
    make_document, make_entitlement, make_generation, make_group, make_job_input,
    make_model_set, make_output, make_queue_job, make_turn, make_user, make_workstream,
    posture, sign_in, user_principal,
)
from models.contracts.roles import CHAT_CONVERSE_ROLE, RAG_EMBED_ROLE

pytestmark = pytest.mark.django_db

# Fresh usernames, category names and connection names per call, so two
# matrix cells in one transaction never collide on a CI-unique
# constraint.
_counter = itertools.count()

# The chat sweep's set, plus the two statuses the gate introduces.
_NEVER_500_STATUSES = frozenset({200, 202, 302, 400, 401, 403, 404, 405, 409, 503})

# One driver per route: it seeds whatever rows the route needs, and
# returns `(method, url, data)`. A route with no driver raises KeyError.
# ONE DRIVER PER ROUTE. Each is `(world) -> (method, url, data)`, where
# `world` is the seeded fixture below. A name with no driver raises
# `KeyError` naming itself -- the same "swept automatically" mechanism
# `agents/chat/tests/test_never_500.py` already uses, and the reason
# `test_every_route_has_a_driver` below can be a one-line assertion.
#
# EVERY ROW-ADDRESSED ROUTE POINTS AT A ROW OWNED BY `world.other` --
# a third account that appears in no test as a caller. That is what
# gives the "an admin with the setting off gets the member's answer"
# column something to be about: a route pointed at the CALLER's own row
# would answer 200 for everybody and prove nothing.
#
# Bodies are the real field names each view reads, not invented ones:
# `agents/chat/views/{conversations,turns,defaults}.py`,
# `tools/rag/views.py` (`_numeric_setting`'s `raw_key` per endpoint),
# `models/queue/views.py:340-402`, `models/registry/views.py:1312,
# 2105-2107,2273`. A body that named a field the view does not read
# would exercise the view's "you sent nothing" branch and the matrix
# would still pass -- which is a matrix testing the wrong thing.
_DRIVERS: dict[str, Callable[["World"], tuple[str, str, dict]]] = {
    # --- / -----------------------------------------------------------
    "landing": lambda w: ("get", reverse("landing"), {}),

    # --- /chat/ ------------------------------------------------------
    "chat-index": lambda w: ("get", reverse("chat-index"), {}),
    # ROUND 14, Part 2: class A, same "own list, no row addressed"
    # shape as `chat-index` just above -- `visible_conversations`
    # itself is the entire admission rule, so no override set is
    # needed (identical to `chat-index`'s own absence from every
    # override dict below).
    "chat-all": lambda w: ("get", reverse("chat-all"), {}),
    # `w.default_slug`, NOT `w.agent.slug`: `chat-start` is class A --
    # "no row rule" (`identity/routes.py`) -- but `_startable_agent`
    # resolves its `agent` field through `visible_agents(principal)`, so
    # naming `w.agent` (owned by `other`) would make an ADMIN's answer
    # depend on `admin_sees_content`: with the toggle OFF, `other`'s
    # agent is invisible, so `_startable_agent` returns `None` -> 400;
    # with it ON, the same agent becomes visible and is enabled, so the
    # request proceeds to `preflight_turn` -- which refuses because no
    # `chat.converse` role is bound in this fresh world -> 503. Both
    # land in `_ADMITTED`, so the matrix's own admission test never
    # caught it, but it is a real, deterministic dependency this driver
    # introduced by accident, not a class-O property of this route.
    # `w.default_slug` names no row this fresh world ever creates, so
    # `_startable_agent` returns `None` and every caller gets the SAME
    # 400, in both settings, matching the class's own "no row rule" --
    # exactly `chat-default-install`'s own driver convention, just
    # below. (Confirmed directly: with `w.default_slug` the answer is
    # 400/400, never 503 -- there is no row for it to become visible or
    # invisible to.)
    "chat-start": lambda w: ("post", reverse("chat-start"),
                             {"agent": w.default_slug, "text": "hello"}),
    "chat-default-install": lambda w: ("post", reverse("chat-default-install"),
                                       {"kind": "agent", "slug": w.default_slug}),
    "chat-conversation": lambda w: (
        "get", reverse("chat-conversation", args=[w.conversation.id]), {}),
    "chat-turn": lambda w: (
        "post", reverse("chat-turn", args=[w.conversation.id]), {"text": "hello"}),
    # CHAT CLUSTER, FEATURE C: row-addressed by TWO ids (conversation,
    # turn) -- `w.turn` is the world's own USER turn on `w.conversation`,
    # written DONE by `Turn.state`'s own default, which is what makes it
    # editable at all. Class O and gated by
    # `agents.visibility.may_edit_turn`, so no override set applies: the
    # base O mapping already says member -> 404, admin -> 404 or
    # admitted with the content toggle, which is exactly
    # `may_manage_conversation`'s answer -- the same predicate
    # `chat-conversation-duplicate` above is gated by. The admitted leg
    # lands on 302 (this fresh world binds no chat role, so `start_turn`
    # refuses and the view redirects with a banner) rather than 200, and
    # 302 is in `_ADMITTED`.
    "chat-turn-edit": lambda w: (
        "post", reverse("chat-turn-edit", args=[w.conversation.id, w.turn.pk]),
        {"text": "edited from the matrix"}),
    "chat-conversation-delete": lambda w: (
        "post", reverse("chat-conversation-delete", args=[w.conversation.id]), {}),
    # ROUND 13 (message-bound attachments): row-addressed by TWO ids
    # (conversation, document) -- `w.document` is the row `_build_world`
    # attaches to `w.conversation`/`w.turn` above, for exactly this
    # driver.
    "chat-attachment-detach": lambda w: (
        "post", reverse("chat-attachment-detach", args=[w.conversation.id, w.document.id]), {}),
    # UI-3b, the sidebar menu's four actions. Class O and ROW-ADDRESSED,
    # so no override set applies: the base O mapping already says
    # member -> 404, admin -> 404 or admitted with the content toggle,
    # which is exactly `may_manage_conversation`'s own answer -- the
    # same predicate `chat-conversation-delete` just above is gated by.
    # Each body names the field its view really reads (the rename's
    # `title`; the other three read none), never an invented one: a
    # driver that named a field the view ignores would exercise its "you
    # sent nothing" branch and this matrix would pass while testing the
    # wrong thing.
    "chat-conversation-rename": lambda w: (
        "post", reverse("chat-conversation-rename", args=[w.conversation.id]),
        {"title": "Renamed"}),
    "chat-conversation-duplicate": lambda w: (
        "post", reverse("chat-conversation-duplicate", args=[w.conversation.id]), {}),
    "chat-conversation-archive": lambda w: (
        "post", reverse("chat-conversation-archive", args=[w.conversation.id]), {}),
    "chat-conversation-unarchive": lambda w: (
        "post", reverse("chat-conversation-unarchive", args=[w.conversation.id]), {}),
    # ROUND 20: the SAME shape archive/unarchive already take.
    "chat-conversation-pin": lambda w: (
        "post", reverse("chat-conversation-pin", args=[w.conversation.id]), {}),
    "chat-conversation-unpin": lambda w: (
        "post", reverse("chat-conversation-unpin", args=[w.conversation.id]), {}),
    "chat-turn-status": lambda w: (
        "get", reverse("chat-turn-status", args=[w.turn.pk]), {}),
    # ROUND 21: the chat settings page. Class S, no row rule -- a plain
    # GET, the same shape `chat-tool-entitlements` just below takes.
    "chat-settings": lambda w: ("get", reverse("chat-settings"), {}),
    "chat-tool-entitlements": lambda w: ("get", reverse("chat-tool-entitlements"), {}),
    # Class S, no row rule -- same shape as `chat-tool-entitlements`
    # just above. `w.agent` (owned by `other`) is on the rendered page
    # for an admin with the content setting ON, but the DRIVER itself
    # names no row: this is a plain GET, exactly like its sibling.
    "chat-agent-entitlements": lambda w: ("get", reverse("chat-agent-entitlements"), {}),
    "chat-conversation-share": lambda w: (
        "post", reverse("chat-conversation-share", args=[w.conversation.id]),
        {"action": "share", "subject": f"user:{w.other.pk}", "level": "view"}),

    # --- /rag/ -------------------------------------------------------
    "rag-ask-page": lambda w: ("get", reverse("rag-ask-page"), {}),
    "rag-ask": lambda w: ("post", reverse("rag-ask"),
                          {"question": "what does the library say?"}),
    "rag-ask-status": lambda w: (
        "get", reverse("rag-ask-status", args=[w.queue_job.pk]), {}),
    "rag-search": lambda w: ("get", reverse("rag-search") + "?q=anything", {}),
    "rag-documents": lambda w: ("get", reverse("rag-documents"), {}),
    # No file part: the view's own "you selected nothing" branch is a
    # 302 with a message, which is an honest non-500 and keeps this
    # driver from depending on a fixture file on disk.
    "rag-document-upload": lambda w: ("post", reverse("rag-document-upload"),
                                      {"category": ""}),
    "rag-document-file": lambda w: (
        "get", reverse("rag-document-file", args=[w.document.pk]), {}),
    "rag-document-transcript": lambda w: (
        "get", reverse("rag-document-transcript", args=[w.document.pk]), {}),
    "rag-document-delete": lambda w: (
        "post", reverse("rag-document-delete", args=[w.document.pk]), {}),
    "rag-document-reingest": lambda w: (
        "post", reverse("rag-document-reingest", args=[w.document.pk]), {}),
    # No row in its own URL -- an empty `entitlements` list takes the
    # view's own early "choose at least one" refusal (302) for every
    # caller alike, the base R mapping's own admission, exactly like the
    # two LISTING R routes below.
    "rag-document-labels-bulk": lambda w: (
        "post", reverse("rag-document-labels-bulk"),
        {"documents": [], "entitlements": [], "action": "apply"}),
    "rag-category-rename": lambda w: (
        "post", reverse("rag-category-rename", args=[w.category.pk]),
        {"name": "a renamed category"}),
    "rag-category-delete": lambda w: (
        "post", reverse("rag-category-delete", args=[w.category.pk]), {}),
    "rag-history": lambda w: ("get", reverse("rag-history"), {}),
    "rag-settings": lambda w: ("get", reverse("rag-settings"), {}),
    # S2 (Coherence Wave C): ONE driver, where seven per-field ones used
    # to sit. NOT VACUOUS -- it carries a real `field` value and that
    # field's own real payload, so an admin cell exercises an actual
    # save rather than the dispatcher's own refusal branch, which would
    # answer 302 for every caller alike and pin nothing.
    "rag-settings-update": lambda w: (
        "post", reverse("rag-settings-update"),
        {"field": "history_limit", "history_limit": "50"}),
    # `w.workstream` is owned by `other` (`chat-workstream-edit`'s own
    # comment gives the reasoning); `w.document` carries no owner column
    # and no `workstream_id`, so it is the universal row this driver
    # pins. Class O through the same `may_manage_workstream` predicate
    # `chat-workstream-edit` already exercises -- not in
    # `_OWNER_WIDENED`, for the same reason that route is not: an
    # entitlement owner has no standing over a stream they do not own.
    "rag-workstream-pin": lambda w: (
        "post", reverse("rag-workstream-pin", args=[w.workstream.pk]),
        {"action": "pin", "document": str(w.document.pk)}),

    # --- /vision/ (only reachable with the flag on; see the skip) -----
    "vision-create": lambda w: ("get", reverse("vision-create"), {}),
    "vision-create-operation": lambda w: (
        "get", reverse("vision-create-operation", args=["txt2img"]), {}),
    "vision-gallery": lambda w: ("get", reverse("vision-gallery"), {}),
    "vision-jobs-strip": lambda w: ("get", reverse("vision-jobs-strip"), {}),
    "vision-operations": lambda w: ("get", reverse("vision-operations"), {}),
    "vision-generate": lambda w: ("post", reverse("vision-generate"),
                                  {"operation": "txt2img", "prompt": "a picture"}),
    "vision-job-status": lambda w: (
        "get", reverse("vision-job-status", args=[w.generation.pk]), {}),
    "vision-job-delete": lambda w: (
        "post", reverse("vision-job-delete", args=[w.generation.pk]), {}),
    # No row in its own URL -- an empty `jobs` list takes the view's own
    # early "nothing selected" refusal (302) for every caller alike, the
    # same admission `rag-document-labels-bulk` above relies on. It
    # never 404s even with a real (foreign) id -- `_FILTERING_O`, below,
    # narrows this class O route's own generic cell answers to match;
    # `TestVisionJobsDeleteSelectedFilters` pins what a real id actually
    # does per caller, directly against the database.
    "vision-jobs-delete-selected": lambda w: (
        "post", reverse("vision-jobs-delete-selected"), {"jobs": []}),
    "vision-output-file": lambda w: (
        "get", reverse("vision-output-file", args=[w.output.pk]), {}),
    "vision-input-file": lambda w: (
        "get", reverse("vision-input-file", args=[w.job_input.pk]), {}),
    "vision-queue-status": lambda w: (
        "get", reverse("vision-queue-status", args=[w.queue_job.pk]), {}),

    # --- /vision/engine-files/ (2026-09-02) ---------------------------
    "vision-engine-files": lambda w: ("get", reverse("vision-engine-files"), {}),
    # A NAME THAT NEVER RESOLVES, deliberately: this world seeds no
    # engine-folder bind mount at all, so every cell's admin answer is
    # the SAME 404 whether `admin_sees_content` is on or off (see
    # `_ROW_ADDRESSED_S`, below, and `tools/vision/maintenance.py::
    # engine_file_thumbnail`'s own content gate) -- the thumbnail route
    # is the one place in this matrix where a class-S route's answer is
    # allowed to be 404 rather than the generic S default.
    "vision-engine-file-thumbnail": lambda w: (
        "get", reverse("vision-engine-file-thumbnail", args=["output", "missing.png"]), {}),
    "vision-engine-files-delete": lambda w: (
        "post", reverse("vision-engine-files-delete"), {"files": []}),

    # --- /inference/ -------------------------------------------------
    "inference-console": lambda w: ("get", reverse("inference-console"), {}),
    "inference-connection-add": lambda w: (
        "post", reverse("inference-connection-add"),
        {"name": "a connection", "engine": "ollama",
         "endpoint": "http://localhost:1", "model_id": "an-identifier"}),
    "inference-connection-remove": lambda w: (
        "post", reverse("inference-connection-remove"),
        {"connection_id": str(w.connection.pk), "confirm": "yes"}),
    "inference-machine-add": lambda w: (
        "post", reverse("inference-machine-add"),
        {"engine": "ollama", "endpoint": "http://localhost:1",
         "model_id": "an-identifier"}),
    "inference-role-assign": lambda w: (
        "post", reverse("inference-role-assign"),
        {"role_key": CHAT_CONVERSE_ROLE, "choice": f"conn:{w.connection.pk}",
         "confirm": "yes"}),
    "inference-role-reencode": lambda w: (
        "post", reverse("inference-role-reencode"), {"role_key": RAG_EMBED_ROLE}),
    # The scan forms post BARE -- their parameters arrive on the query
    # string (`models/registry/views.py`'s own docstring says so).
    "inference-server-scan": lambda w: ("post", reverse("inference-server-scan"), {}),
    # --- /inference/ (IA-2 T14: model sets) ---------------------------
    "inference-connection-sets": lambda w: (
        "post", reverse("inference-connection-sets", args=[w.connection.pk]),
        {"sets": []}),
    "inference-model-sets": lambda w: ("get", reverse("inference-model-sets"), {}),
    "inference-model-set-edit": lambda w: (
        "post", reverse("inference-model-set-edit", args=[w.model_set.pk]),
        {"action": "rename", "name": "renamed"}),

    # --- /queue/ -----------------------------------------------------
    "jobs-queue": lambda w: ("get", reverse("jobs-queue"), {}),
    # F1 (Coherence Wave C): "Job execution", the settings page -- a GET,
    # like every other settings PAGE in this table; the POST endpoint its
    # forms submit to is the row below, unchanged.
    "jobs-settings": lambda w: ("get", reverse("jobs-settings"), {}),
    "jobs-settings-update": lambda w: (
        "post", reverse("jobs-settings-update"),
        {"form": "budget", "budget_gb": "8", "max_concurrent_jobs": "1"}),
    "jobs-queue-cancel": lambda w: (
        "post", reverse("jobs-queue-cancel", args=[w.queue_job.pk]), {}),

    # --- /setup/ -----------------------------------------------------
    "setup-index": lambda w: ("get", reverse("setup-index"), {}),

    # --- /settings/ --------------------------------------------------
    "settings-index": lambda w: ("get", reverse("settings-index"), {}),

    # --- /settings/assistant/ -----------------------------------------
    # NO ROW IS ADDRESSED by any of the three, so no `world.other` row is
    # needed: the ask resolves the CALLER's own conversation (creating one
    # if there is none), reset archives the caller's own, and the panel
    # reads the caller's own. `text` is the field the ask view actually
    # reads -- a body naming a field the view ignores would exercise its
    # "you sent nothing" branch and the matrix would still pass.
    "settings-assistant-ask": lambda w: (
        "post", reverse("settings-assistant-ask"), {"text": "hello"}),
    "settings-assistant-reset": lambda w: (
        "post", reverse("settings-assistant-reset"), {}),
    "settings-assistant-panel": lambda w: (
        "get", reverse("settings-assistant-panel"), {}),
    # The agent library. Class S, no row rule -- a plain GET, the same
    # shape `chat-agent-entitlements` takes, and a GET is the whole
    # vocabulary this route has (`require_safe`): every write it offers
    # is a link to `chat-agent-edit`, which has its own driver above.
    # `w.agent` (owned by `other`) is on the rendered page for an
    # administrator, but the driver names no row.
    "settings-agents": lambda w: ("get", reverse("settings-agents"), {}),

    # --- /identity/ --------------------------------------------------
    "identity-login": lambda w: ("get", reverse("identity-login"), {}),
    "identity-logout": lambda w: ("post", reverse("identity-logout"), {}),
    "identity-password-change": lambda w: (
        "get", reverse("identity-password-change"), {}),
    "identity-password-change-done": lambda w: (
        "get", reverse("identity-password-change-done"), {}),
    "identity-users": lambda w: ("get", reverse("identity-users"), {}),
    "identity-user-create": lambda w: (
        "post", reverse("identity-user-create"),
        {"username": f"created-{next(_counter)}", "password": "a-real-enough-value"}),
    # `demote` on a NON-admin is a no-op the service returns early from,
    # so this driver never trips the last-admin guard and never changes
    # the world between matrix cells.
    "identity-user-edit": lambda w: (
        "post", reverse("identity-user-edit", args=[w.other.pk]), {"action": "demote"}),
    "identity-settings": lambda w: ("get", reverse("identity-settings"), {}),

    # --- /identity/ (new in IA-2) ------------------------------------
    "identity-groups": lambda w: ("get", reverse("identity-groups"), {}),
    "identity-group-edit": lambda w: (
        "post", reverse("identity-group-edit", args=[w.group.pk]),
        {"action": "add_member", "user": w.other.pk}),
    "identity-entitlements": lambda w: ("get", reverse("identity-entitlements"), {}),
    # POINTED AT AN ENTITLEMENT `world.other` OWNS AND NOBODY ELSE DOES,
    # for the same reason every other row-addressed driver points at a
    # row `other` owns: a route pointed at the CALLER's own row would
    # answer 200 for everybody and prove nothing. A member and an admin
    # with the content setting off therefore get this route's real R
    # answers -- 404 and 200 respectively.
    "identity-entitlement-edit": lambda w: (
        "get", reverse("identity-entitlement-edit", args=[w.entitlement.pk]), {}),

    # --- /chat/agents/ (chat cluster, feature B) ----------------------
    "chat-agents": lambda w: ("get", reverse("chat-agents"), {}),
    # A GET probe even though this route also accepts a POST, the SAME
    # choice `chat-workstream-new`'s own driver makes just below and for
    # the identical reason: this cell runs once per (principal,
    # content_on) combination, and a POST driver would create a row as a
    # side effect of merely proving the class boundary.
    "chat-agent-new": lambda w: ("get", reverse("chat-agent-new"), {}),
    # Row-addressed and owned by `other`, exactly like the conversation
    # menu's own four drivers. The body names the fields the view really
    # reads; a driver naming fields it ignores would exercise the "you
    # sent nothing" branch and pass while testing the wrong thing.
    # `_ADMIN_ALWAYS_ADMITTED_O` (below) carries this route's one
    # departure from the base O mapping.
    "chat-agent-edit": lambda w: (
        "post", reverse("chat-agent-edit", args=[w.agent.pk]),
        {"action": "fields", "name": "Driven", "description": "",
         "system_prompt": "", "max_steps": "2", "enabled": "on"}),

    # --- /chat/w/ (new in Workstreams WS-1, T13) ----------------------
    "chat-workstreams": lambda w: ("get", reverse("chat-workstreams"), {}),
    # SETUP SCREEN (owner feedback round 7): class A, the identical shape
    # `chat-workstreams` itself carries. A GET probe, the SAME choice
    # `chat-workstreams`' own driver above makes even though its route
    # also accepts a POST -- this matrix cell runs once per (principal,
    # content_on) combination, and a POST driver would create a row (or,
    # on a name collision across two cells sharing a principal, 400
    # instead of the class's own expected admission) as a side effect of
    # merely proving the class boundary, which GET proves just as well
    # without mutating anything.
    "chat-workstream-new": lambda w: ("get", reverse("chat-workstream-new"), {}),
    "chat-workstream": lambda w: (
        "get", reverse("chat-workstream", args=[w.workstream.pk]), {}),
    # SETTINGS-PAGE SPLIT: class O, but deliberately NOT `chat-workstream`'s
    # own 403 exception -- `workstream_settings` has no `stream_access`
    # branch at all, so this cell's O answer is the plain house 404 rule,
    # proven separately by `test_chat_workstream_settings_answers_404_
    # never_403_for_a_live_share_holder` below.
    "chat-workstream-settings": lambda w: (
        "get", reverse("chat-workstream-settings", args=[w.workstream.pk]), {}),
    # `action`/`name` are the two fields `workstream_edit`'s "rename"
    # branch really reads, for the same reason `chat-conversation-rename`
    # names `title` above rather than an invented field.
    "chat-workstream-edit": lambda w: (
        "post", reverse("chat-workstream-edit", args=[w.workstream.pk]),
        {"action": "rename", "name": "Renamed"}),
    # An EMPTY `entitlements` list, so this cell never depends on which
    # entitlements the calling principal happens to hold -- exactly the
    # reason `rag-document-labels-bulk`'s own empty-selection driver takes
    # the base R mapping's admission rather than exercising the write
    # path's own refusal.
    "chat-workstream-scope": lambda w: (
        "post", reverse("chat-workstream-scope", args=[w.workstream.pk]),
        {"entitlements": []}),
    # An EMPTY body, for the identical reason the scope driver just above
    # takes one: the owner's own answer is a 400 ("pick an account or a
    # group") that is in `_ADMITTED` regardless of which accounts exist
    # in this world, so this cell never depends on `share_subjects`
    # happening to offer a particular row.
    "chat-workstream-share": lambda w: (
        "post", reverse("chat-workstream-share", args=[w.workstream.pk]), {}),
    # POINTED AT `w.conversation`, which is now built INSIDE `w.workstream`
    # (Task 18) for exactly this route: `workstream_consolidate` resolves
    # its `conversation` field through `visible_conversations(principal).
    # filter(workstream_id=stream.pk)`, so a conversation outside the
    # world's workstream would 404 for every caller alike and this cell
    # would prove nothing about the route's real O answer.
    "chat-workstream-consolidate": lambda w: (
        "post", reverse("chat-workstream-consolidate", args=[w.workstream.pk]),
        {"conversation": str(w.conversation.id)}),
}


@dataclass
class World:
    """Every row the drivers address, all owned by `other`.

    Rebuilt per matrix cell (the fixture is function-scoped): several
    drivers MUTATE -- `chat-conversation-delete`, `rag-category-delete`,
    `jobs-queue-cancel` -- and a shared world would make the answer to
    cell N depend on which cells ran before it, which is exactly the
    collection-order leakage `docs/DEV.md`'s reversed-order run exists
    to catch.
    """

    other: object
    agent: object
    default_slug: str
    conversation: object
    turn: object
    document: object
    category: object
    queue_job: object
    connection: object
    group: object
    entitlement: object
    model_set: object
    workstream: object
    generation: object = None
    output: object = None
    job_input: object = None


def _seed_document_files(document) -> None:
    """Give `document` REAL bytes on disk (`rag-document-file` streams
    `source_path`) and a REAL transcript sidecar (`rag-document-transcript`
    reads `extract.json` off `settings.DOCUMENTS_DIR`) -- both routes
    answer 404 for MISSING CONTENT regardless of who is asking, and that
    is a content fact, not a permission one. Without this, `world.document`
    would 404 every signed-in caller on both L routes for a reason that has
    nothing to do with the matrix this module exists to check.

    Laid out the same way `tools.rag.store.store_file` lays out a real
    ingest (`<DOCUMENTS_DIR>/<doc_id>/...`), under `DOCUMENTS_DIR` as
    redirected by `_redirect_documents_dir` below -- never the real
    `data/documents/`, so ~1,000 `_build_world()` calls across the matrix
    never leave a trace in the working tree.
    """
    from django.conf import settings as django_settings

    doc_dir = django_settings.DOCUMENTS_DIR / str(document.pk)
    doc_dir.mkdir(parents=True, exist_ok=True)
    source_path = doc_dir / "source.txt"
    source_path.write_text("a document body, for the matrix's own document.")
    (doc_dir / "extract.json").write_text(
        json.dumps({"segments": [{"start": 0.0, "text": "a transcribed line"}]})
    )
    document.source_path = str(source_path)
    document.save(update_fields=["source_path"])


def _seed_vision_files(output, job_input) -> None:
    """Real bytes for `output.path`/`job_input.path`, the exact same
    reason `_seed_document_files` gives `document.source_path` real
    bytes: `tools.vision.views._serve_stored_file` 404s on a path that
    isn't a REGULAR file (the builders' own default, `/dev/null`, is a
    character device, not one) -- a content fact that would make
    `vision-output-file`/`vision-input-file` 404 for EVERY caller,
    including an administrator with the toggle on, which is exactly the
    cell `TestTheContentToggleMovesExactlyOneClassInIA1` catches: an O
    route whose admin answer must FLIP off of 404 once the setting is on,
    and cannot if the row's bytes were never real to begin with. Written
    under the same redirected `DOCUMENTS_DIR` as `_seed_document_files`
    (a plain scratch directory here, not a real vision concept) purely to
    reuse the one already-isolated, already-cleaned-up tmp path."""
    from django.conf import settings as django_settings

    output_path = django_settings.DOCUMENTS_DIR / "vision-output.png"
    output_path.write_bytes(b"not a real png, but a real file")
    output.path = str(output_path)
    output.save(update_fields=["path"])

    input_path = django_settings.DOCUMENTS_DIR / "vision-input.png"
    input_path.write_bytes(b"not a real png, but a real file")
    job_input.path = str(input_path)
    job_input.save(update_fields=["path"])


def _build_world() -> World:
    """Seed one world. `other` is a plain member who owns everything and
    is never the caller.

    A PLAIN FUNCTION with a thin fixture over it, not a fixture alone:
    the two cross-cell classes below (`TestThePosturesAgree`,
    `TestTheContentToggleMovesExactlyTwoClasses`) each need a FRESH
    world inside a loop, and a fixture can only be requested once per
    test."""
    other = make_user(username=f"other-{next(_counter)}")
    principal = user_principal(other)
    agent = make_agent(slug=f"agent-{next(_counter)}", **owner_fields(principal))
    # OWNED BY `other`, the same convention every other row-addressed
    # driver's row follows: a route pointed at the CALLER's own row would
    # answer 200 for everybody and prove nothing. Built BEFORE
    # `conversation` (moved up from its earlier spot below `document`) so
    # `chat-workstream-consolidate` (Task 18) has a conversation that is
    # actually IN this world's workstream to address -- the row that
    # route's own `visible_conversations(...).filter(workstream_id=...)`
    # resolution needs.
    workstream = make_workstream(name=f"workstream-{next(_counter)}",
                                 **owner_fields(principal))
    conversation = make_conversation(agent=agent, workstream=workstream,
                                     **owner_fields(principal))
    turn = make_turn(conversation=conversation)
    document = make_document()                # documents carry no owner column
    _seed_document_files(document)
    category = make_category(name=f"category-{next(_counter)}")
    queue_job = make_queue_job(
        payload={"question": "a private question",
                 "actor_kind": "user", "actor_key": str(other.pk)})
    connection = make_connection(name=f"connection-{next(_counter)}")
    group = make_group(name=f"group-{next(_counter)}")
    entitlement = make_entitlement(name=f"entitlement-{next(_counter)}")
    grant(entitlement, user=other, role="owner")
    # `world.document` is labelled with `world.entitlement`, which
    # `world.other` also owns -- the pairing the `owner` principal's
    # column (T18) needs: a member and an admin with the setting off get
    # this route's real R/L answers about a row they have no standing
    # over, and the `owner` column is admitted because it holds the same
    # entitlement `world.other` does. Resolved through `apps.get_model`,
    # never imported, for the same reason every other row builder in
    # `identity/tests/_helpers.py` is: this module is scaffolding for a
    # column that may not import `tools.rag.models`.
    from django.apps import apps
    apps.get_model("rag.DocumentEntitlement").objects.create(
        document=document, entitlement=entitlement)
    # NO `DocumentAttachment` FOR `chat-attachment-detach` HERE,
    # DELIBERATELY -- see `_ADMIN_NEVER_ADMITTED_O`'s own comment: that
    # route's real 403 branch (`may_detach_attachment`, uploader-only)
    # is the platform's SECOND row-addressed 403 exception, exercised
    # by its own dedicated test near `test_chat_workstream_answers_403_
    # for_a_live_share_holder_and_404_without_the_row`, never by this
    # generic sweep -- the identical "the sweep never builds the row
    # that triggers the exceptional branch" shape that route's own
    # world-building already established.
    model_set = make_model_set(name=f"set-{next(_counter)}")
    world = World(
        other=other, agent=agent, default_slug=DEFAULT_AGENTS[0].slug,
        conversation=conversation, turn=turn, document=document, category=category,
        queue_job=queue_job, connection=connection, group=group, entitlement=entitlement,
        model_set=model_set, workstream=workstream,
    )
    if "vision" in settings.FARABUNKER_FEATURES:
        world.generation = make_generation(**owner_fields(principal))
        world.output = make_output(job=world.generation)
        world.job_input = make_job_input(job=world.generation)
        _seed_vision_files(world.output, world.job_input)
    return world


@pytest.fixture(autouse=True)
def _fast_password_hashing(settings):
    """TEST-ONLY speed pin, this module alone: `_sign_in_as` creates and
    signs in a fresh `member`/`admin` account for nearly every one of
    this module's ~1,000 cells, and each sign-in both HASHES a password
    (`make_user`/`make_admin`) and RE-VERIFIES it (`client.login` calling
    `authenticate`). Django's default `PASSWORD_HASHERS[0]` (PBKDF2, a
    six-figure iteration count) is deliberately slow -- correct for
    production, and the dominant cost of this module's own runtime. This
    module asserts nothing ABOUT hashing strength, so it swaps in the
    fast, intentionally-insecure hasher Django ships expressly for
    tests. Nothing outside this module is affected: the setting is
    restored after each test the same way `_redirect_documents_dir`'s
    `DOCUMENTS_DIR` override is."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture(autouse=True)
def _redirect_documents_dir(settings, tmp_path):
    """Every test in this module gets its OWN `DOCUMENTS_DIR`, never the
    real `data/documents/` (`config/settings.py` points that at the
    working tree unless overridden) -- `_seed_document_files` above
    writes into it once per `_build_world()` call, and this module builds
    a fresh world for most of its ~1,000 cells. `settings` (pytest-django)
    restores the real value after the test; `tmp_path` is unique per test
    and pytest cleans it up."""
    settings.DOCUMENTS_DIR = tmp_path


@pytest.fixture
def world():
    """One world per matrix cell."""
    return _build_world()


@pytest.fixture
def client_with_csrf():
    """A client built with `enforce_csrf_checks=True` -- Django's test
    client disables CSRF enforcement by default, which would make an
    anonymous-POST assertion vacuous. The same `Client(enforce_csrf_
    checks=True)` shape `TestTheAnonymousPostColumnIsNotVacuous` already
    builds ad hoc, below, lifted to a fixture for
    `test_an_anonymous_post_to_each_new_route_is_refused`."""
    return Client(enforce_csrf_checks=True)


def _assert_never_500(response):
    assert response.status_code in _NEVER_500_STATUSES, (
        response.status_code, getattr(response, "content", b"")[:2000])
    # A STREAMING response (`FileResponse`/`StreamingHttpResponse` --
    # `rag-document-file`, `vision-output-file`, `vision-input-file` all
    # admit into one on a real hit) has no `.content`: consuming
    # `.streaming_content` here would exhaust the very iterator the test
    # client still needs to finish the response, so the traceback check
    # is skipped for it -- a 500 body is never streaming in this codebase
    # (every error path renders a plain template/JSON response instead).
    if not getattr(response, "streaming", False):
        assert "Traceback" not in response.content.decode(errors="replace")
    return response


class TestTheTableIsComplete:
    def test_every_route_has_a_driver(self):
        missing = sorted(set(ROUTE_RULES) - set(_DRIVERS))
        assert missing == [], missing


# WHAT EACH CLASS MUST ANSWER, as a table rather than as a chain of
# ifs -- the design's section 11.1, transcribed. Keys are
# `(class, who, content_on)`; values are the acceptable statuses for
# that cell. Several cells accept a SET, and every one of those sets is
# narrow and deliberate:
#
#   * a GET route cannot answer 405 and a POST route cannot answer 200,
#     so `_expected` intersects the cell with the method's own
#     vocabulary (`_METHOD_STATUSES`) before asserting;
#   * a POST that succeeds redirects (302), and one that refuses a
#     malformed body answers 400 -- both are "you were allowed in", and
#     the matrix is asserting ADMISSION, not the view's own grammar;
#   * 503 is "allowed in, but the queue or the engine is not up", which
#     is an honest answer on this box and never an authorisation fact.
#
# What the table is strict about is the REFUSALS, because those are the
# assertions with security meaning: 302/401 for anonymous, 403 for a
# member on S, and 404 -- never 403 -- for a row a principal may not
# see.
_ADMITTED = frozenset({200, 202, 302, 400, 409, 503})
_REFUSED_ANON = frozenset({302, 401})
_REFUSED_ADMIN_SURFACE = frozenset({403})
_REFUSED_ROW = frozenset({404})

# THE NARROWING IA-1 STARTED AND IA-2 WIDENS TO A THIRD NAME, rather
# than folding it into the table.
#
# `rag-document-delete` and `rag-document-reingest` are class R, and in
# IA-1 their view rule was `is_admin` ALONE (Task 13 Step 6b) -- document
# labels did not exist yet, so the entitlement-owner half of the spec's
# rule could not be written. IA-2 (Task 12) widens both to "`is_admin`,
# OR an owner of one of the document's entitlements". `world.document` IS
# labelled with `world.entitlement` in this matrix (T18), but a PLAIN
# member holds no grant on it -- only the `owner` column does
# (`_OWNER_WIDENED`, below) -- so a plain member still holds nothing to
# delegate from on either route, and still gets 403, not the R column's
# ordinary admission. 403 and not 404 because BOTH views resolve their
# row through the UNSCOPED `Document.objects` manager, checking
# `is_admin(principal) or owned_entitlement_ids(principal)` before ever
# looking the row up (`tools/rag/views.py::document_delete`,
# `::document_reingest`) -- a caller with no standing over ANY
# entitlement is refused before a row is resolved at all, so a label on
# THIS PARTICULAR document changes nothing about their answer.
#
# `rag-document-labels` USED TO BE ARGUED ABOUT HERE and is gone (C-36):
# the per-document SET route had no live caller, and its bulk twin
# (`rag-document-labels-bulk`) takes no row in its own URL, so it has no
# 403-vs-404 question to answer at all. What survives from that argument
# is the rule the two names below still obey: these two resolve their row
# through the UNSCOPED manager after checking standing, so a label on
# THIS PARTICULAR document changes nothing about their answer.
_LIBRARY_MUTATIONS = frozenset({"rag-document-delete", "rag-document-reingest"})

# THE OTHER NARROWING: the R routes addressed BY ID at a row a non-owner
# member has no standing over -- refused the ROW itself (404), never the
# ambiguous "either the listing's 200 or the row's 404" union the base R
# mapping below used to carry for every R name alike. Three from IA-1
# point at a job; `identity-entitlement-edit` (IA-2) points at an
# entitlement `world.other` owns, for the same reason. The two LISTING R
# routes (`rag-documents`, `jobs-queue`) take no id at all and are never
# in this set -- they stay on the base `_ADMITTED` mapping, because there
# is no row to be refused.
_ROW_ADDRESSED_R = frozenset({"jobs-queue-cancel", "rag-ask-status", "vision-queue-status",
                              "identity-entitlement-edit"})

# THE ONE FILTERING O. `vision-jobs-delete-selected` is class O -- a
# generated image is CONTENT (`tools/vision/visibility.py`), so the row
# rule is `sees_all_content`, never `is_admin` -- but it carries no row
# in its own URL (`identity/routes.py`'s own comment on the entry): the
# gallery's select-mode bulk delete takes a LIST of ids and silently
# DROPS whichever ones `visible_jobs(principal)` refuses, rather than
# 404ing the one row a row-addressed O route would. The base O mapping's
# strict `_REFUSED_ROW` for "member" and "admin without the content
# setting" describes a route that CANNOT be reached without a row it may
# refuse; this one is reached (302, "Nothing selected."/"Deleted N
# generations.") no matter what the caller submitted -- `_ADMITTED`
# either way. `TestVisionJobsDeleteSelectedFilters`, further down, pins
# what actually happens to a REAL foreign id per caller directly against
# the database -- the fact this override intentionally does not ask the
# HTTP status code to carry.
_FILTERING_O = frozenset({"vision-jobs-delete-selected"})

# THE ONE CLASS-S ROUTE WHOSE ADMIN ANSWER IS 404, NOT THE GENERIC S
# DEFAULT. `vision-engine-file-thumbnail` gates its PIXELS on
# `sees_all_content` on top of the route's own `is_admin` class
# (`tools/vision/maintenance.py`'s own module docstring), and its driver
# names a file that never resolves in this matrix's world (no engine
# folder is bind-mounted here) -- so an administrator's answer is 404
# whether the content setting is on or off, which is exactly why
# `TestTheContentToggleMovesExactlyOneClassInIA1` (below) can still
# assert the two answers are IDENTICAL for this route without this
# override: only the STATUS this generic matrix accepts for "S" (never
# 404, see `_EXPECTED`) needs the exception.
_ROW_ADDRESSED_S = frozenset({"vision-engine-file-thumbnail"})

# THE OWNER WIDENINGS. Each of these routes is pointed by its driver at a
# row labelled with `world.entitlement` -- the entitlement the `owner`
# principal owns -- so an owner is ADMITTED where a member is refused.
# `rag-document-labels-bulk`, `rag-document-delete` and `-reingest` are
# the three capabilities spec section 7.4 gives an entitlement owner over
# a document; `identity-entitlement-edit` is the fourth, over the grant
# itself.
# `rag-document-labels-bulk`'s own driver posts an empty `documents` list,
# so its cell never actually exercises the widening (the base R mapping
# already admits everyone alike for an empty selection); the entry
# documents the intent, and the real per-document widening is asserted
# directly in `tools/rag/tests/test_document_label_page.py`.
# `rag-document-file` and `-transcript` join here in Step 3: an owner
# grant IS a grant, so an owner reads the content of a document under
# their entitlement with no extra branch anywhere.
_OWNER_WIDENED = frozenset({
    "rag-document-delete", "rag-document-reingest",
    "identity-entitlement-edit", "rag-document-labels-bulk",
    "rag-document-file", "rag-document-transcript",
})

# THE ONE O ROUTE WHOSE ADMIN ANSWER NEVER FLIPS TO ADMITTED, IN
# EITHER CONTENT SETTING. `chat-attachment-detach` (round 13) gates its
# ACTION on `tools.rag.access.may_detach_attachment` -- UPLOADER-ONLY,
# by design (that predicate's own docstring: an admin who needs to
# remove someone else's attachment already has the library's own
# Delete, `may_administer_document`, a DIFFERENT, wider predicate this
# route deliberately does not use). `_build_world()` creates no
# `DocumentAttachment` for this route on purpose (its own comment there
# has the full reasoning, mirroring `chat-workstream`'s own precedent
# below): with nothing actually attached, `agents.attachments.
# detach_attachment` answers 404 ("not attached to this conversation")
# for EVERY caller who gets far enough to ask, including an admin with
# the content setting on -- never the base O mapping's `_ADMITTED`,
# which describes a route where the content toggle alone decides
# admission. The REAL uploader-vs-stranger 403 this predicate can
# produce is pinned by its own dedicated test, `test_chat_attachment_
# detach_answers_403_for_a_non_uploader_and_404_without_the_row`, below
# -- the identical "generic sweep never builds the row that triggers
# the exceptional branch" shape `chat-workstream`'s own exception
# already established, so the platform-wide "O is never 403" invariant
# this module's own `TestTheMatrix` asserts stays true for THIS sweep.
_ADMIN_NEVER_ADMITTED_O = frozenset({"chat-attachment-detach"})

# THE MIRROR IMAGE, AND THE ONE O ROUTE WHOSE ADMIN ANSWER IS ADMITTED
# IN **BOTH** CONTENT SETTINGS. `chat-agent-edit` (chat cluster, feature
# B) gates on `agents.visibility.may_manage_agent`, which SHORT-CIRCUITS
# on `is_admin` alone and never reads `admin_sees_content` -- an agent is
# box INVENTORY, the same call `labellable_agents` already records for
# `/chat/access/`, not a conversation or a document. The content toggle
# governs READING somebody else's content, and administering the box's
# own agents is not reading it: an administrator who could not open the
# row could not turn a runaway agent off.
#
# It is still class O rather than S, and that is the whole feature (see
# `identity/routes.py`'s own entry): a MEMBER is answered by the base O
# mapping's `_REFUSED_ROW` for `world.agent`, which `other` owns -- so
# this override widens exactly one cell of the three, and the member
# column still proves the house 404.
_ADMIN_ALWAYS_ADMITTED_O = frozenset({"chat-agent-edit"})

_METHOD_STATUSES = {"get": frozenset({200, 302, 400, 401, 403, 404, 503}),
                    "post": frozenset({200, 202, 302, 400, 401, 403, 404, 409, 503})}

# THE ONE GET ROUTE WHOSE SUCCESS IS A REDIRECT (UI-2). `settings-index`
# (`/settings/`) renders nothing: it is the app bar's `Settings` entry,
# and it forwards to the first settings section the caller may open, so
# the entry never lands on a page that would refuse them. That makes it
# the single exception to the rule stated on `_METHOD_STATUSES_SIGNED_IN`
# below -- a named set of one rather than 302 being readmitted to the
# whole GET vocabulary, which would silently re-open the "the gate
# bounced a signed-in caller to login" hole for every other route. The
# leniency is paid for: the cell test asserts this route really does
# redirect, and specifically NOT to `identity-login`, in every column
# including anonymous. WHICH section each principal lands on is
# `foundation/tests/test_settings_area.py`'s claim, not this module's.
_REDIRECTS_INSTEAD_OF_RENDERING = frozenset({"settings-index"})

# The SIGNED-IN vocabulary: 302 dropped from GET. A GET route's own
# successful admission never legitimately redirects anywhere in this
# codebase -- every admitted GET cell in this module renders a page or a
# JSON body inline -- so a 302 landing on a MEMBER or ADMIN's GET request
# can only mean the gate silently treated a signed-in caller as
# anonymous and bounced them to login: a real bug, not a third admitted
# shape. POST keeps 302 (a successful mutation redirects). `_METHOD_STATUSES`
# above (not this one) stays the vocabulary for the ANONYMOUS column,
# where a GET login-redirect is the ordinary refusal -- checked directly
# against `identity-login` in the merged test below, not merely accepted
# as "some 302".
_METHOD_STATUSES_SIGNED_IN = {
    "get": _METHOD_STATUSES["get"] - {302},
    "post": _METHOD_STATUSES["post"],
}

_EXPECTED: dict[tuple[str, str, bool], frozenset] = {}
for _klass in ("P", "A", "O", "L", "R", "S"):
    for _content in (False, True):
        # Anonymous: P is open to everybody; everything else redirects
        # or answers 401 to a poll.
        _EXPECTED[(_klass, "anonymous", _content)] = (
            _ADMITTED if _klass == "P" else _REFUSED_ANON)
        _EXPECTED[(_klass, "member", _content)] = {
            "P": _ADMITTED,
            "A": _ADMITTED,
            # L IS NOW REFUSED for a member holding none of the
            # document's labels. `world.document` is labelled with
            # `world.entitlement`, which a plain member does not hold,
            # so `readable_documents` excludes it and
            # `rag-document-file`/`-transcript` answer 404. The `owner`
            # column above holds that entitlement and is therefore
            # admitted -- which is the assertion that proves the label is
            # doing the work rather than the route being broken for
            # everybody.
            "L": _REFUSED_ROW,
            # R is `is_admin` AT THE QUEUE SEAM: a member is admitted
            # to the LISTING (`rag-documents`, `jobs-queue`) and 404'd
            # on another's ROW. The base mapping here is the listing's
            # own answer; `_ROW_ADDRESSED_R` above overrides the
            # id-addressed names to the strict `_REFUSED_ROW` answer in
            # `_expected_for`, rather than this cell carrying an
            # ambiguous union that would pass for either reality.
            "R": _ADMITTED,
            "O": _REFUSED_ROW,
            "S": _REFUSED_ADMIN_SURFACE,
        }[_klass]
        # An administrator: admitted on P, A, R and S in BOTH settings --
        # administering needs rows, and rows are never gated by the
        # content toggle. On O and L they are answered like a member
        # while the toggle is off, and admitted once it is on. L joins O
        # in IA-2, now that `readable_documents` has a body to gate on.
        _EXPECTED[(_klass, "admin", _content)] = {
            "P": _ADMITTED, "A": _ADMITTED,
            "R": _ADMITTED, "S": _ADMITTED,
            "O": _ADMITTED if _content else _REFUSED_ROW,
            "L": _ADMITTED if _content else _REFUSED_ROW,
        }[_klass]
        # AN ENTITLEMENT OWNER IS A MEMBER EVERYWHERE EXCEPT INSIDE THEIR
        # OWN ENTITLEMENT. So the base mapping is the member's, and the
        # three routes where owning changes the answer are named in
        # `_OWNER_WIDENED` below rather than folded in here -- a cell that
        # said "admitted" for every R route would pass whether or not the
        # owner rule was implemented at all.
        _EXPECTED[(_klass, "owner", _content)] = _EXPECTED[(_klass, "member", _content)]


def _expected_for(name, who, content_on):
    """The cell, with the IA-1 narrowings and the IA-2 owner widenings
    applied."""
    if who == "owner" and name in _OWNER_WIDENED:
        return _ADMITTED
    # The three `inference-*-set*` routes and `chat-agent-entitlements`
    # are class S: a model connection and an agent are box inventory, and
    # an entitlement owner has no standing over either, so the owner
    # column answers exactly as the member column does (403). No entry in
    # `_OWNER_WIDENED` for any of them, deliberately -- and this comment
    # is why, so a later reader does not "fix" the omission.
    if name in _LIBRARY_MUTATIONS and who in ("member", "owner"):
        return _REFUSED_ADMIN_SURFACE
    if name in _ROW_ADDRESSED_R and who in ("member", "owner"):
        return _REFUSED_ROW
    # `_ROW_ADDRESSED_S`: admin only, since member/owner are already
    # refused at the middleware (403, the generic S default) before the
    # view's own content gate ever runs.
    if name in _ROW_ADDRESSED_S and who == "admin":
        return _REFUSED_ROW
    # `_FILTERING_O`: admitted for every signed-in caller in every
    # content setting -- it filters instead of refusing, so there is no
    # cell where this route's own HTTP status is a 404.
    if name in _FILTERING_O and who != "anonymous":
        return _ADMITTED
    if name in _ADMIN_NEVER_ADMITTED_O and who == "admin":
        return _REFUSED_ROW
    # `_ADMIN_ALWAYS_ADMITTED_O`: the mirror image, admin only -- a
    # member and an owner still take the base O mapping's `_REFUSED_ROW`.
    if name in _ADMIN_ALWAYS_ADMITTED_O and who == "admin":
        return _ADMITTED
    return _EXPECTED[(ROUTE_RULES[name], who, content_on)]


def _sign_in_as(client, who, world):
    """Returns the caller, or None for anonymous. Every non-anonymous
    `who` is a FRESH account per cell, so nothing one cell does to an
    account can reach the next.

    `owner` is a plain member who holds ONE `role=owner` grant -- on
    `world.entitlement`, which is also the entitlement `world.document`
    is labelled with. That pairing is the whole point of the column: an
    entitlement owner is a MEMBER everywhere except inside their own
    entitlement, and the matrix has to show both halves.

    `world` IS AN ARGUMENT, not a module-level global the fixture writes
    into. The cell test already receives `world` before it signs anybody
    in, so threading it through costs nothing -- while coupling two
    fixtures through a module-level list would make a wrong ordering an
    `IndexError` rather than a readable failure, and would be exactly the
    hidden shared state this module's per-cell fresh-world discipline
    exists to avoid.
    """
    if who == "anonymous":
        return None
    user = make_admin() if who == "admin" else make_user()
    if who == "owner":
        grant(world.entitlement, user=user, role="owner")
    sign_in(client, user)
    return user


# PINNED TO ONE POSTURE, deliberately. Sweeping both would double
# ~1,000 cells -- each of which seeds a fresh world -- to prove a fact
# `TestThePosturesAgree` below already asserts directly and far more
# cheaply: that the two non-open postures answer IDENTICALLY for every
# route, principal and setting. Proving it a second time by exhaustion,
# six times per merge gate, buys nothing -- and the posture sweep
# (`FARABUNKER_TEST_POSTURE=enterprise`) runs this whole module against
# the other posture anyway.
_MATRIX_POSTURE = POSTURE_PERSONAL


@pytest.mark.parametrize("content_on", [False, True])
@pytest.mark.parametrize("who", ["anonymous", "member", "admin", "owner"])
@pytest.mark.parametrize("name", sorted(ROUTE_RULES))
class TestTheMatrix:
    def test_the_cell_is_correct(
        self, client, world, name, who, content_on,
    ):
        """ONE request per cell, then three checks against it, in order.

        Collapsed from three separate per-cell tests that each used to
        issue the SAME request independently against a SEPARATE `world`
        build (the fixture is function-scoped, so three test methods
        meant three world builds and three HTTP round trips per cell,
        tripling this module's own runtime for no extra coverage).

        1. Never a 500, never a traceback -- the never-500 obligation,
           extended to every route, principal and content setting in
           `_MATRIX_POSTURE`. The OTHER non-open posture is covered by
           `TestThePosturesAgree` (which compares status codes across
           both) and by the `FARABUNKER_TEST_POSTURE` sweep, which runs
           this whole module against it. Checked FIRST so a class whose
           expected set is later argued about still cannot regress into
           a 500 while that argument is being had.

        2. The status is the one the route's class requires -- the
           whole of the design's section 11.1, as an assertion. With
           the content setting OFF -- the default -- an administrator
           is answered LIKE A MEMBER on every **O** and **L** route
           and LIKE AN ADMINISTRATOR on every R and S route: they run
           the box without reading anybody's words. Turning it on
           collapses the two columns, and changes nothing on R or S.
           L WAS admitted in both settings in IA-1 (spec section 18.1 --
           no entitlements, no labels); IA-2 gives `readable_documents`
           a body and L now flips with O. Run in ONE posture,
           `_MATRIX_POSTURE`, for the reason stated above it.

           An ANONYMOUS 302 is checked FURTHER: `_REFUSED_ANON` and
           `_ADMITTED` share the status code 302 (a redirect can be
           either "go log in" or "your mutation succeeded"), so a
           request that FAILED OPEN and redirected somewhere else
           entirely would otherwise pass this assertion for the wrong
           reason. It must be a redirect to `identity-login`
           specifically. A SIGNED-IN caller (member/admin) never gets
           this leniency at all for a GET request --
           `_METHOD_STATUSES_SIGNED_IN` drops 302 from the GET
           vocabulary entirely, because a GET route's own successful
           admission never legitimately redirects anywhere in this
           codebase, so a 302 there can only mean the gate silently
           treated a signed-in caller as anonymous.

        3. 404, NOT 403, on the row-addressed classes (O and L) ONLY. A
           403 on a row-addressed URL confirms the row exists, which is
           exactly the enumeration `chat-turn-status`'s sequential
           integer already exposes. A 403 on an ADMIN SURFACE is fine
           and is used, because the existence of a model console is not
           a secret. Applied as a plain `if`, never a `pytest.skip` --
           this module has ZERO skips other than the vision-flag guard
           just below, so a class outside O/L simply has nothing
           asserted here rather than a skip recorded for it. **R is
           excluded from this check on purpose**: `rag-document-delete`
           and `rag-document-reingest` are class R and answer a member
           **403** in IA-1 (Task 13 Step 6b), because a member can
           already see the document's title on the library page --
           hiding its existence at the mutation would be a secrecy the
           surrounding page does not keep. Their sibling R routes 404
           through the queue seam (`_ROW_ADDRESSED_R`, above), and Task
           13's own tests pin that side.

           **`chat-workstream` (class O) has its own named exception**,
           spec §12.3: a caller who holds a live `Share` row on the
           stream but whose grants have since lapsed gets 403, not 404
           (WS-2's read-time share gate, `agents.workstreams.
           stream_access`) -- the one route on this platform that
           answers 403 on a row-addressed URL, because only a holder of
           a REAL Share reaches that branch at all; a stranger is
           excluded earlier by `visible_workstreams` and still gets the
           house 404. This sweep never exercises that branch and stays
           green regardless: `_build_world()` creates no `Share` row for
           any world it builds, so no cell in this matrix is ever a
           Share holder in the first place. `test_chat_workstream_
           answers_403_for_a_live_share_holder_and_404_without_the_row`,
           below, builds that world on purpose and pins the exception
           this sweep cannot reach.
        """
        if name.startswith("vision-") and "vision" not in settings.FARABUNKER_FEATURES:
            pytest.skip("the vision tree is not mounted without its flag")
        with posture(_MATRIX_POSTURE, admin_sees_content=content_on):
            _sign_in_as(client, who, world)
            method, url, data = _DRIVERS[name](world)
            response = getattr(client, method)(url, data)

        _assert_never_500(response)

        method_statuses = (
            _METHOD_STATUSES if who == "anonymous" else _METHOD_STATUSES_SIGNED_IN
        )
        allowed = _expected_for(name, who, content_on) & method_statuses[method]
        if name in _REDIRECTS_INSTEAD_OF_RENDERING:
            allowed = allowed | {302}
        # `getattr(..., "content", b"")`, not `response.content`, directly:
        # a STREAMING admission (`rag-document-file`, the two vision file
        # routes) has no `.content`, and this message must not itself
        # raise `AttributeError` and mask a real assertion failure on one
        # of those routes.
        assert response.status_code in allowed, (
            name, who, content_on, response.status_code,
            getattr(response, "content", b"")[:500],
        )
        if name in _REDIRECTS_INSTEAD_OF_RENDERING:
            # The leniency above, paid for in EVERY column: this route's
            # 302 must be the forward it exists to be, never the gate
            # bouncing the caller to sign in.
            assert response.status_code == 302, (name, who, response.status_code)
            assert not response["Location"].startswith(reverse("identity-login")), (
                name, who, content_on, response["Location"],
            )
        elif who == "anonymous" and response.status_code == 302:
            assert response["Location"].startswith(reverse("identity-login")), (
                name, who, content_on, response["Location"],
            )

        if ROUTE_RULES[name] in ("O", "L"):
            assert response.status_code != 403, (name, who, content_on)


class TestThePosturesAgree:
    @pytest.mark.parametrize("content_on", [False, True])
    @pytest.mark.parametrize("who", ["member", "admin", "owner"])
    def test_the_two_non_open_postures_give_identical_answers(
        self, client, who, content_on,
    ):
        """THE ASSERTION THAT FAILS IF A POSTURE BRANCH IS EVER
        REINTRODUCED into a visibility function. `personal` and
        `enterprise` differ only in WHICH PAGES EXIST; for the same
        principal and the same setting they answer identically
        everywhere.

        The world and the caller are rebuilt per posture, so the two
        runs differ by the posture column and nothing else -- a shared
        world would let the first run's `jobs-queue-cancel` change the
        second run's answer."""
        for name in sorted(ROUTE_RULES):
            if name.startswith("vision-") and "vision" not in settings.FARABUNKER_FEATURES:
                continue
            answers = []
            for posture_name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
                fresh = Client()
                with posture(posture_name, admin_sees_content=content_on):
                    world = _build_world()
                    _sign_in_as(fresh, who, world)
                    method, url, data = _DRIVERS[name](world)
                    answers.append(getattr(fresh, method)(url, data).status_code)
            assert answers[0] == answers[1], (name, who, content_on, answers)


class TestTheContentToggleMovesExactlyOneClassInIA1:
    @pytest.mark.parametrize("name", sorted(ROUTE_RULES))
    def test_turning_it_on_changes_O_and_L_and_nothing_else(self, name):
        """The toggle's blast radius, asserted route by route. An
        administrator's answer must change on **O** and **L** and be
        IDENTICAL everywhere else -- because the setting governs READING,
        and administering is not reading.

        L WAS IN THE "NOTHING ELSE" HALF IN IA-1, and that was spec
        section 18.1 rather than an oversight: with no entitlements and
        no labels, a document was readable by everyone signed in in both
        settings, so the toggle had nothing to move. IA-2 gives
        `readable_documents` a body -- `world.document` now carries a
        label a plain member does not hold -- so L joins O in the flip
        branch here, and this test is the one the old docstring predicted
        would need renaming when that happened."""
        if name.startswith("vision-") and "vision" not in settings.FARABUNKER_FEATURES:
            pytest.skip("the vision tree is not mounted without its flag")
        answers = []
        for content_on in (False, True):
            fresh = Client()
            with posture(POSTURE_ENTERPRISE, admin_sees_content=content_on):
                world = _build_world()
                _sign_in_as(fresh, "admin", world)
                method, url, data = _DRIVERS[name](world)
                answers.append(getattr(fresh, method)(url, data).status_code)
        if name in _FILTERING_O:
            # The one O route with no row in its own URL: the toggle
            # changes what a real foreign id inside `jobs` DOES (dropped
            # vs. deleted -- `TestVisionJobsDeleteSelectedFilters` pins
            # that against the database), never the STATUS CODE this
            # empty-selection driver gets back, which is 302 either way.
            assert answers[0] == answers[1], (name, answers)
        elif name in _ADMIN_NEVER_ADMITTED_O:
            # `chat-attachment-detach`'s own gate (`may_detach_
            # attachment`, uploader-only) does not read `admin_sees_
            # content` at all -- the toggle governs DOCUMENT/CONVERSATION
            # visibility, and this route's own generic-sweep world has
            # no real attachment to become visible either way (`_build_
            # world`'s own comment), so an admin's answer is the SAME
            # 404 in both settings, never the flip every other O/L route
            # makes.
            assert answers[0] == answers[1] == 404, (name, answers)
        elif name in _ADMIN_ALWAYS_ADMITTED_O:
            # `may_manage_agent` short-circuits on `is_admin` and never
            # reads `admin_sees_content` at all (see that set's own
            # comment): an agent is box inventory, not content, so an
            # administrator's answer is the SAME admission in both
            # settings -- never the flip every other O/L route makes,
            # and never `_ADMIN_NEVER_ADMITTED_O`'s doubled 404 either.
            assert answers[0] == answers[1], (name, answers)
            assert answers[0] != 404, (name, answers)
        elif ROUTE_RULES[name] in ("O", "L"):
            assert answers[0] == 404 and answers[1] != 404, (name, answers)
        else:
            assert answers[0] == answers[1], (name, answers)


def _vision_generation_model():
    """Resolved through `apps.get_model`, never imported at module level
    -- the same reason every other row builder in `identity/tests/
    _helpers.py` is: this module is scaffolding for the route matrix,
    which spans every column and may not import `tools.vision.models`
    directly."""
    from django.apps import apps

    return apps.get_model("vision.GenerationJob")


class TestVisionJobsDeleteSelectedFilters:
    """`_FILTERING_O` (above) pins the HTTP shape of the gallery's
    select-mode bulk delete -- admitted (302) for every signed-in caller,
    every content setting, because it filters rather than refuses. That
    shape is silent about the fact the filtering is even happening at
    all: an empty `jobs` list and a `jobs` list full of ids nobody may
    touch both come back 302 too. This class is the other half --
    submitting a REAL id and checking what actually happened to the ROW,
    the four cells `tools/vision/visibility.py`'s `sees_all_content`
    rule distinguishes, pinned the same way `identity/tests/
    test_visibility.py`-style modules pin theirs: directly against the
    database, not the status code.
    """

    @staticmethod
    def _post_for(client, *jobs) -> None:
        client.post(
            reverse("vision-jobs-delete-selected"),
            {"jobs": [str(job.pk) for job in jobs]},
        )

    def test_a_member_deletes_their_own_selection(self, client):
        if "vision" not in settings.FARABUNKER_FEATURES:
            pytest.skip("the vision tree is not mounted without its flag")
        generation_model = _vision_generation_model()
        member = make_user()
        with posture(POSTURE_PERSONAL):
            mine = make_generation(**owner_fields(user_principal(member)))
            sign_in(client, member)
            self._post_for(client, mine)
        assert not generation_model.objects.filter(pk=mine.pk).exists()

    def test_a_members_foreign_ids_are_dropped_the_row_survives(self, client):
        if "vision" not in settings.FARABUNKER_FEATURES:
            pytest.skip("the vision tree is not mounted without its flag")
        generation_model = _vision_generation_model()
        owner, member = make_user(), make_user()
        with posture(POSTURE_PERSONAL):
            theirs = make_generation(**owner_fields(user_principal(owner)))
            sign_in(client, member)
            self._post_for(client, theirs)
        assert generation_model.objects.filter(pk=theirs.pk).exists()

    def test_an_admin_without_the_content_setting_drops_foreign_ids_too(self, client):
        """The whole reason this route is O, not R (`identity/routes.py`'s
        own comment on the entry): `is_admin` alone must NOT be enough --
        an administrator with the content setting off has no more
        standing over somebody's pictures here than a plain member does,
        exactly as `visibility.py`'s module docstring states for every
        other vision route."""
        if "vision" not in settings.FARABUNKER_FEATURES:
            pytest.skip("the vision tree is not mounted without its flag")
        generation_model = _vision_generation_model()
        owner, admin = make_user(), make_admin()
        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            theirs = make_generation(**owner_fields(user_principal(owner)))
            sign_in(client, admin)
            self._post_for(client, theirs)
        assert generation_model.objects.filter(pk=theirs.pk).exists()

    def test_an_admin_with_the_content_setting_on_deletes(self, client):
        if "vision" not in settings.FARABUNKER_FEATURES:
            pytest.skip("the vision tree is not mounted without its flag")
        generation_model = _vision_generation_model()
        owner, admin = make_user(), make_admin()
        with posture(POSTURE_PERSONAL, admin_sees_content=True):
            theirs = make_generation(**owner_fields(user_principal(owner)))
            sign_in(client, admin)
            self._post_for(client, theirs)
        assert not generation_model.objects.filter(pk=theirs.pk).exists()


class TestTheAnonymousPostColumnIsNotVacuous:
    # One POST per class, every one of them a plain Django view:
    # `chat-start` (A), `chat-turn` (O), `rag-document-delete` (R),
    # `rag-category-delete` (S) -- plus `rag-ask`, a SECOND A, added at
    # C-55. It is here not for class coverage (chat-start already covers
    # A) but because it is the one route this class used to argue ITSELF
    # out of, on grounds that stopped being true when it stopped being a
    # DRF APIView.
    #
    # `rag-ask` JOINS THIS CLASS AT C-55. It used to be excluded because
    # DRF enforced CSRF only for session-authenticated callers, so an
    # anonymous cross-site POST carried no token requirement at all -- a
    # CSRF assertion aimed at it would have passed whether or not a token
    # was ever sent, which is the vacuity this class exists to prevent.
    # It is a plain Django view now, so `CsrfViewMiddleware` enforces it
    # for every POST and the assertion has teeth.
    @pytest.mark.parametrize("name", ["chat-start", "chat-turn", "rag-document-delete",
                                      "rag-category-delete", "rag-ask"])
    def test_an_anonymous_post_with_no_csrf_cookie_is_403(self, world, name):
        """Django's test client disables CSRF enforcement by default,
        which would make the anonymous-POST column of the matrix
        vacuous. This runs one POST per class through a client built
        with `enforce_csrf_checks=True`.

        The 403 comes from CSRF, not from the gate, and that is CORRECT
        AND DELIBERATE: `CsrfViewMiddleware` runs before
        `AuthenticationMiddleware`, and therefore before
        `IdentityGateMiddleware`, so an unauthenticated cross-site POST
        is evaluated for FORGERY before it is evaluated for
        AUTHORISATION. Moving the gate ahead of CSRF would be the wrong
        order to fail in."""
        strict = Client(enforce_csrf_checks=True)
        with posture(POSTURE_PERSONAL):
            method, url, data = _DRIVERS[name](world)
            assert method == "post", name
            response = strict.post(url, data)
        assert response.status_code == 403, (name, response.status_code)
        _assert_never_500(response)

    def test_an_anonymous_post_WITH_a_csrf_cookie_reaches_the_gate(self, world):
        """The other half of the same rule, and the half that proves
        the gate is doing anything at all: a POST that satisfies CSRF
        but carries no session gets 302/401 from
        `IdentityGateMiddleware`, not 403 from CSRF.

        `chat-start`, because its driver posts a form the gate can answer
        plainly. `rag-ask` is a plain view too since C-55 and is now in the
        no-token half of this pair, just above."""
        strict = Client(enforce_csrf_checks=True)
        with posture(POSTURE_PERSONAL):
            strict.get(reverse("identity-login"))     # sets the CSRF cookie
            token = strict.cookies["csrftoken"].value
            method, url, data = _DRIVERS["chat-start"](world)
            response = strict.post(url, {**data, "csrfmiddlewaretoken": token})
        assert response.status_code in (302, 401), response.status_code


class TestALabelledDirectSurface:
    """Spec section 16.4: the route matrix carries the two direct-surface
    routes in BOTH label states. The unlabelled state is the matrix
    proper; this is the labelled one, kept out of the cross-product
    because it concerns exactly two routes."""

    @pytest.mark.parametrize("name,tool_key",
                             [("vision-generate", "vision.generate"),
                              ("rag-document-upload", "rag.ingest")])
    def test_it_is_403_for_a_non_holder_and_admitted_for_a_holder(
            self, client, world, name, tool_key):
        from django.apps import apps
        apps.get_model("agents.ToolEntitlement").objects.create(
            tool_key=tool_key, entitlement=world.entitlement)
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_user())
            _method, url, data = _DRIVERS[name](world)
            assert client.post(url, data).status_code == 403
            holder_client = client.__class__()
            holder = make_user()
            grant(world.entitlement, user=holder)
            sign_in(holder_client, holder)
            assert holder_client.post(url, data).status_code in _ADMITTED


def _reverse_for_sweep(url_name, *, workstream):
    """A URL for `url_name` with placeholder arguments, or
    `NoReverseMatch` for a name whose signature this cannot fill.

    THE STREAM'S OWN PK for every integer argument, so the one route that
    is allowed to name an entitlement is actually REACHED by the sweep --
    a placeholder `1` would 404 on `chat-workstream` and the test would
    pass by never rendering the page it exists to check. A fresh `uuid4()`
    for every UUID argument, which no row has, so a conversation-addressed
    route answers 404 rather than leaking a different row's content into
    the sweep.

    Tried widest-first: no arguments, then one, then two. `NoReverseMatch`
    propagates for anything none of the three fits, and the caller skips
    that name -- a route this helper cannot address is a route this
    property cannot be checked on, which is honest and is why the skip is
    the caller's decision rather than a silent `return None`.
    """
    import uuid

    for args in ((), (workstream.pk,), (workstream.pk, workstream.pk)):
        try:
            return reverse(url_name, args=args)
        except NoReverseMatch:
            continue
    return reverse(url_name, args=(uuid.uuid4(),))


def test_no_route_other_than_the_dormant_share_page_names_an_entitlement_to_a_non_holder(
        client):
    """§12.3's property, pinned so this page STAYS the only one.

    Today, the name of an entitlement you neither own nor hold is
    disclosed to you by NO route: `identity-entitlements` is class S, the
    accounts page rendering `effective_entitlements` is class S, and the
    only entitlement names a non-admin sees anywhere come from
    `labelling_entitlements`, which filters to `owned_entitlement_ids`
    for a non-admin. The dormant-share 403 is the FIRST, and owner
    decision 8 asks for it deliberately, trading a name for
    actionability (spec §24 concern 7).
    """
    # Walk every non-S url_name with a signed-in non-admin who neither
    # owns nor holds `secret`, and assert `secret.name` appears in no
    # response body except `chat-workstream`'s 403.
    owner, reader = make_user(), make_user()
    secret = make_entitlement(name="ZzUnholdableSecret")
    stream = _workstream(**owner_fields(user_principal(owner)))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=secret)
    Share.objects.create(target_type=Share.Target.WORKSTREAM,
                         target_key=str(stream.pk), user=reader, level=Share.Level.USE)

    named_by = []
    with posture("enterprise"):
        sign_in(client, reader)
        for url_name, klass in ROUTE_RULES.items():
            if klass == "S":
                continue          # the middleware refuses a non-admin first
            try:
                url = _reverse_for_sweep(url_name, workstream=stream)
            except NoReverseMatch:
                continue
            body = client.get(url).content.decode(errors="ignore")
            if secret.name in body:
                named_by.append(url_name)

    # EXACTLY ONE, and it is the fenced 403 (spec §12.3, §24 concern 7).
    assert named_by == ["chat-workstream"]


def test_chat_workstream_answers_403_for_a_live_share_holder_and_404_without_the_row(client):
    """The pair, asserted TOGETHER, because the exception is only safe if
    the negative case holds. This is the only 403 on a row-addressed URL
    on this platform and spec §12.3 fences it three ways."""
    owner, reader = make_user(), make_user()
    ent = make_entitlement(name="Finance")
    stream = _workstream(**owner_fields(user_principal(owner)))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    url = reverse("chat-workstream", args=[stream.pk])

    for name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
        share = Share.objects.create(
            target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
            user=reader, level=Share.Level.USE)
        with posture(name):
            sign_in(client, reader)
            assert client.get(url).status_code == 403, name
            share.delete()
            # THE PAIR: without the row, `visible_workstreams` excludes
            # the stream and the view raises `Http404` BEFORE
            # `stream_access` is called -- so no input a stranger can
            # supply reaches the 403 branch.
            assert client.get(url).status_code == 404, name


def test_chat_workstream_settings_answers_404_never_403_for_a_live_share_holder(client):
    """The settings-page split's own contrast to the pair just above:
    `chat-workstream-settings` deliberately does NOT extend `chat-
    workstream`'s own 403 exception. The IDENTICAL live-share-holder
    scenario that route answers 403 for gets the plain house 404 rule
    here instead -- `workstream_settings` has no `stream_access` call at
    all, so there is no branch left that could answer 403."""
    owner, reader = make_user(), make_user()
    ent = make_entitlement(name="Finance")
    stream = _workstream(**owner_fields(user_principal(owner)))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    url = reverse("chat-workstream-settings", args=[stream.pk])

    for name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
        share = Share.objects.create(
            target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
            user=reader, level=Share.Level.USE)
        with posture(name):
            sign_in(client, reader)
            # THE SHARE ROW IS LIVE (just created, not yet deleted) and
            # the reader lacks `Finance` -- the exact scenario that turns
            # `chat-workstream` into a 403 -- and this route still
            # answers 404, because it never calls `stream_access` at all.
            assert client.get(url).status_code == 404, name
            share.delete()
            assert client.get(url).status_code == 404, name


def test_chat_attachment_detach_answers_403_for_a_non_uploader_and_404_without_the_row(
    client,
):
    """The platform's SECOND row-addressed 403 exception (the first is
    `chat-workstream`'s own live-share-holder branch, just above): a
    real attachment answers 403 for a principal who is not its own
    uploader but CAN otherwise see the conversation it is attached to
    (`tools.rag.access.may_detach_attachment`, uploader-only, no admin
    widening) -- and 404, never 403, for a `doc_id` with no real claim
    on this conversation at all, the plain house rule. The generic
    sweep above never builds this row at all (`_build_world`'s own
    comment), which is what keeps its own "O is never 403" invariant
    honest for this route -- this is the dedicated test that pins the
    exception directly, instead."""
    from tools.rag.models import Document, DocumentAttachment

    uploader, admin = make_user(), make_admin()
    agent = make_agent()
    conversation = make_conversation(agent=agent, **owner_fields(user_principal(uploader)))
    turn = make_turn(conversation=conversation)
    doc = make_document(scope=Document.Scope.CONVERSATION,
                        **owner_fields(user_principal(uploader)))
    DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id,
                                      turn_id=turn.pk)
    url = reverse("chat-attachment-detach", args=[conversation.id, doc.id])

    with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
        sign_in(client, admin)
        assert client.post(url, {}).status_code == 403
        # No real claim on THIS conversation at all (a fresh doc_id) --
        # the SAME admin gets the house 404, never 403.
        assert client.post(
            reverse("chat-attachment-detach", args=[conversation.id, 999999]), {},
        ).status_code == 404

    with posture(POSTURE_ENTERPRISE):
        sign_in(client, uploader)
        assert client.post(url, {}).status_code == 302


@pytest.mark.parametrize("url_name", [
    "chat-workstreams", "chat-workstream-new", "chat-workstream-edit",
    "chat-workstream-scope", "chat-workstream-share", "chat-workstream-consolidate",
    "rag-workstream-pin",
])
def test_an_anonymous_post_to_each_new_route_is_refused(client_with_csrf, url_name):
    """The anti-vacuous pin the matrix already uses, through a client
    built with `enforce_csrf_checks=True`."""
    args = [] if url_name in ("chat-workstreams", "chat-workstream-new") else [1]
    with posture("enterprise"):
        response = client_with_csrf.post(reverse(url_name, args=args), {})
    # Redirected to sign-in, or refused outright -- never a write, and
    # never a 500.
    assert response.status_code in (302, 403, 404)
    assert "Traceback" not in response.content.decode(errors="ignore")


# --- The settings assistant panel's open flag, swept over this same table ---
#
# The endpoints the sweep below MUST find. Derived-not-typed is the rule
# this module lives by, so this list is an ANTI-VACUOUS FLOOR, not the
# subject: the sweep discovers what to check by asking each POST driver
# where its redirect actually lands, and a settings save that stopped
# preserving the flag fails there whether or not it is named here. What
# this names is the sweep having gone blind -- a derivation that quietly
# found nothing would otherwise pass.
_SETTINGS_SAVES = frozenset({
    "rag-settings-update",          # Library -- one dispatched endpoint, seven forms
    "jobs-settings-update",         # Job execution -- one endpoint, two forms
    "chat-settings",                # Chat -- GET/POST on the one url name
    "chat-tool-entitlements",       # Tool access
    "chat-agent-entitlements",      # Agent access
    "identity-settings",            # Identity & security
    "identity-user-create",         # Accounts
    "identity-user-edit",           # Accounts
    "identity-group-edit",          # Groups
    "inference-connection-add",     # Models -- via `_redirect_console`
    "inference-machine-add",        # Models -- via `_redirect_console`
    "inference-role-assign",        # Models -- via `_redirect_console`
    "inference-role-reencode",      # Models -- via `settings_redirect`
    "vision-engine-files-delete",   # Engine files
})

# `inference-server-scan` IS DELIBERATELY ABSENT, and it is the one
# settings action the flag cannot ride (ADR 0018, decision 14's named
# gap): it does not redirect at all -- it RE-RENDERS the console from
# its own POST url, because scan results have nowhere stateless to
# survive a redirect (`models/registry/views.py::server_scan` states
# that reasoning in full). The panel's own gate keys on the route being
# a settings CARD route, `inference-server-scan` is not one, so that
# re-render carries no panel context and the panel collapses there --
# closing it needs either a card-registry change or a cross-column
# import the import law forbids, neither of which is this round's.




# --- The other half of the same rule: the form ACTIONS ---------------------
#
# A POST form an open settings page may render WITHOUT the flag, keyed by
# the route its action resolves to, each with the reason it is exempt.
# Everything else on a settings page must carry it, or the save lands on
# a redirect that has nothing to preserve and the panel shuts.
_UNFLAGGED_ACTIONS = {
    # The named gap (ADR 0018, G15): `server_scan` re-renders the console
    # from its own non-card POST route, where the panel has no context at
    # all, so the flag would be a parameter in the address bar of a
    # panel-less page.
    "inference-server-scan": "re-renders from a non-card route -- the named gap",
    # THE PANEL'S OWN THREE FORMS, which carry the flag in a `next` field
    # rather than on the action, and are pinned in `agents/chat/tests/
    # test_assistant_panel.py` where they belong: the install offer posts
    # `assistant.install_next` (idempotently flagged), and ask/reset go
    # back through `assistant.py::_back`, which preserves it.
    "chat-default-install": "posts `install_next`, which already carries the flag",
    "settings-assistant-ask": "`_back()` preserves the flag off the `next` field",
    "settings-assistant-reset": "`_back()` preserves the flag off the `next` field",
    # Signing out ends the session; there is no panel left to keep open.
    "identity-logout": "sign-out -- nothing to come back to",
}

# The extra query a page needs before it renders its forms at all.
_REVEALING_QUERY = {
    # Engine files renders its one delete form only in select mode.
    "vision-engine-files": "&select=1",
}

# The pages this sweep MUST find at least one flagged form on -- the
# anti-vacuity floor, for the same reason `_SETTINGS_SAVES` above is one:
# a page that stopped rendering forms, or a sweep that stopped seeing
# them, would otherwise pass in silence.
_PAGES_WITH_FORMS = frozenset({
    "chat-settings", "chat-tool-entitlements", "chat-agent-entitlements",
    "identity-settings", "identity-users", "identity-groups", "identity-entitlements",
    "inference-console", "jobs-settings", "rag-settings", "vision-engine-files",
})


class TestEverySettingsSaveKeepsTheAssistantPanelOpen:
    """PERSISTENCE ROUND (owner report, 2026-09-14): the panel's whole
    open/closed state is `?assistant=1` on the URL, so a settings form
    that posts and redirects back without it SHUTS the panel on every
    save.

    A SAVE HAS TWO HALVES AND THIS CLASS SWEEPS BOTH, because either one
    alone is a green suite over a broken feature (review round 1, I1 --
    the first version of this class pinned only the second half, and 35
    of the 36 shipped form actions had no guard at all):

      * the FORM gives the flag -- `{{ assistant.open_query }}` on every
        settings form's `action`, asserted off the rendered HTML of every
        page in `card_routes()`;
      * the VIEW gives it back -- `foundation.settings_area.
        preserve_assistant_flag` on every settings POST redirect.

    IT REUSES THIS MODULE'S DRIVER TABLE rather than typing a second
    one: each driver already carries a real payload for its route, and
    WHICH of them are settings saves is derived from where the redirect
    lands -- a 302 to a page in `card_routes()`, the same frozenset the
    panel's own context processor gates on. An endpoint added later
    needs no edit here: give it a driver (this module already fails
    without one) and it is swept.

    PLUS THE PAGES THAT POST TO THEMSELVES. Four settings pages take
    GET and POST on ONE url name (`chat-settings` and `identity-
    settings`' singleton shape, and the two entitlement pages), so the
    table's driver for them is a GET and the derivation above cannot
    see their write at all. The second leg probes every card route with
    an empty POST and keeps the ones that answer a redirect INTO the
    settings area -- which is exactly that shape, and nothing else: a
    page with no POST path answers 200 or 405 and is skipped.

    A FRESH ADMINISTRATOR PER REQUEST, for one specific reason:
    `identity-logout` is a POST driver too, and it sorts before most of
    the table -- a single sign-in would sweep the second half of the
    alphabet as an anonymous visitor and find nothing, silently.
    """

    def _post(self, client, url, data=None):
        """One POST as a fresh administrator. See the class docstring
        for why the sign-in is per request rather than per sweep."""
        sign_in(client, make_admin())
        return client.post(url, data or {})

    def _settled_in_the_settings_area(self, response):
        """The `Location` of a redirect that lands on a settings page,
        or `None` for anything else."""
        if response.status_code != 302:
            return None
        target = response.headers["Location"]
        try:
            match = resolve(urlsplit(target).path)
        except Resolver404:                           # nothing this box routes
            return None
        return target if match.url_name in card_routes() else None

    def _saves(self, client, settings, *, flagged: bool = False):
        """Every settings-save endpoint this box has, as `{name:
        Location}`. `flagged` posts to the flag-carrying URL, so the same
        derivation drives both halves of the claim.

        THROUGH `with_assistant_flag`, not `url + "?assistant=1"` (review
        round 1, N3): a `_DRIVERS` entry whose url already carries a
        query would otherwise be posted to `…?a=b?assistant=1`, and this
        sweep would be testing a malformed URL rather than the rule."""
        def _url(url: str) -> str:
            return with_assistant_flag(url) if flagged else url

        landing: dict[str, str] = {}
        for name in sorted(_DRIVERS):
            if name.startswith("vision-") and "vision" not in settings.FARABUNKER_FEATURES:
                continue
            method, url, data = _DRIVERS[name](_build_world())
            if method != "post":
                continue
            target = self._settled_in_the_settings_area(
                self._post(client, _url(url), data))
            if target is not None:
                landing[name] = target
        for name in sorted(card_routes()):
            try:
                url = reverse(name)
            except NoReverseMatch:                    # a feature-gated page, off
                continue
            target = self._settled_in_the_settings_area(self._post(client, _url(url)))
            if target is not None:
                landing[name] = target
        return landing

    def test_the_flag_rides_every_settings_save_back_and_only_those(self, client, settings):
        with posture(_MATRIX_POSTURE):
            plain = self._saves(client, settings)
            flagged = self._saves(client, settings, flagged=True)

        missing = _SETTINGS_SAVES - set(plain)
        assert not missing, (sorted(missing), sorted(plain))
        assert set(flagged) == set(plain), (sorted(flagged), sorted(plain))
        for name, target in plain.items():
            # The collapsed half: untouched, byte for byte.
            assert "assistant" not in target, (name, target)
            # And the open half: the same destination plus the flag --
            # never a different page, and never a second copy of it.
            assert flagged[name] == with_assistant_flag(target), (name, flagged[name])

    _POST_FORM = re.compile(r'<form\b([^>]*)>')
    _ACTION = re.compile(r'action="([^"]*)"')

    def _post_form_actions(self, body: str) -> list[str]:
        """Every `method="post"` form action on a rendered page."""
        actions = []
        for attributes in self._POST_FORM.findall(body):
            if 'method="post"' not in attributes:
                continue
            action = self._ACTION.search(attributes)
            assert action is not None, attributes     # every POST form names one
            actions.append(action.group(1))
        return actions

    def _open_settings_pages(self, client, settings):
        """`{route_name: [action, ...]}` for every settings page this box
        has, rendered with the panel OPEN as an administrator."""
        # ONE SEEDED WORLD FIRST: several settings pages render their
        # per-row forms only when there is a row (an agent to grant, a
        # group to edit, a connection to attach), so a sweep over an
        # empty box would quietly check fewer forms than the box has.
        _build_world()
        pages = {}
        for name in sorted(card_routes()):
            try:
                url = reverse(name)
            except NoReverseMatch:                    # a feature-gated page, off
                continue
            sign_in(client, make_admin())
            response = client.get(
                f"{url}?assistant=1{_REVEALING_QUERY.get(name, '')}")
            assert response.status_code == 200, (name, response.status_code)
            pages[name] = self._post_form_actions(response.content.decode())
        return pages

    def test_every_form_on_an_open_settings_page_posts_with_the_flag(self, client, settings):
        """THE TEMPLATE HALF (review round 1, I1). The sweep above proves
        the VIEWS preserve a flag they are given; this one proves the
        forms actually give them one. Without it, 35 of the 36 shipped
        `action`s had no guard at all and a new settings page could add
        `settings_redirect`, pass every test, and still shut the panel on
        every save -- the exact defect this round exists to fix.

        DERIVED, like everything else here: it renders every page in
        `card_routes()` as an open administrator and reads the actions
        off the HTML, so a form added to any settings page later is swept
        with no edit to this file. The exemptions are named, few, and
        each carries its reason in `_UNFLAGGED_ACTIONS`.
        """
        with posture(_MATRIX_POSTURE):
            pages = self._open_settings_pages(client, settings)

        flagged: dict[str, int] = {}
        for name, actions in pages.items():
            for action in actions:
                route = resolve(urlsplit(action).path).url_name
                if route in _UNFLAGGED_ACTIONS:
                    assert "assistant" not in action, (name, route, action)
                    continue
                assert action.endswith("?assistant=1"), (name, route, action)
                flagged[name] = flagged.get(name, 0) + 1

        missing = (_PAGES_WITH_FORMS & set(pages)) - set(flagged)
        assert not missing, (sorted(missing), {k: len(v) for k, v in pages.items()})

    def test_the_same_pages_render_bare_actions_with_the_panel_shut(self, client, settings):
        """The collapsed half, byte for byte: nothing on a settings page
        carries the parameter when the panel is not open -- not even the
        forms the test above requires to carry it."""
        with posture(_MATRIX_POSTURE):
            _build_world()
            for name in sorted(card_routes()):
                try:
                    url = reverse(name)
                except NoReverseMatch:
                    continue
                sign_in(client, make_admin())
                query = _REVEALING_QUERY.get(name, "").replace("&", "?", 1)
                body = client.get(url + query).content.decode()
                for action in self._post_form_actions(body):
                    assert "assistant" not in action, (name, action)
