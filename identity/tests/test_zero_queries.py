"""An open box never runs a permission query.

A TESTABLE CLAIM, not a promise. In `open` posture no request executes a
query against any of the TWELVE forbidden tables below. IA-2 extends the
forbidden set from the one IA-1 name (`identity_user`) to the full set
spec section 3.4 names: the two entitlement-grant tables `identity/`
owns, plus the five per-column entitlement/share tables IA-2 T1-T17 add
(`rag_documententitlement`, `agents_toolentitlement`, `agents_share`,
`agents_agententitlement`, `agents_flowentitlement`), plus the three
model-set tables (T14). Workstreams WS-1 T13 adds the twelfth --
`agents_workstreamscopeentitlement`, the stream's own wall table -- as
the first task to wire a view against it. Ruling A (spec §23.A) is what
keeps the open-box claim true of it: `agents.workstreams.wall_ids`
returns the empty set without ever querying the table when
`accounts_on()` is False, so the wall stays dormant, never dropped,
across a posture switch.

The one primary-key read of `identity_identitysettings` is NOT a
permission query -- it is the query that answers WHICH POSTURE, and the
box must ask it before it can skip anything else.
"""
from __future__ import annotations

import pytest
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from identity.contracts.postures import POSTURE_OPEN
from identity.tests._helpers import posture

pytestmark = pytest.mark.django_db

# One representative GET per mount. `vision-gallery`/`vision-engine-files`
# only when the flag is on -- `config/urls.py` mounts `/vision/` only
# with it, and `reverse(...)` would raise `NoReverseMatch` (a collection-
# time error, not a skip) otherwise.
#
# `identity-groups` (IA-2, T18) IS a representative GET of the four new
# `/identity/` pages (`identity/routes.py`'s "new in IA-2" block): with
# no `Group` rows in a fresh test database, `Group.objects
# .prefetch_related("user_set", "entitlement_grants__entitlement")`
# issues its base query against `auth_group` and Django's own prefetch
# machinery never issues the `entitlement_grants` follow-up query at all
# for an empty base queryset -- so this GET genuinely touches none of the
# eleven.
#
# THE OTHER THREE NEW `/identity/` PAGES, AND `/chat/tools/`,
# `/chat/access/` AND `/inference/sets/`, ARE DELIBERATELY NOT HERE.
# Each is an admin LISTING page whose entire purpose is to enumerate rows
# from one of the eleven tables, and each issues that listing query
# UNCONDITIONALLY -- regardless of how many rows exist, and regardless of
# posture:
#   * `identity-entitlements` (`identity/views.py::entitlements`) reads
#     `Entitlement.objects.annotate(grant_count=Count("grants"))` --
#     `identity_entitlementgrant` is named in the JOIN clause of that ONE
#     query's SQL text even when zero entitlements exist -- and, since
#     the manage-all round, ONE AGGREGATE PER REGISTERED AXIS as well
#     (`identity.axes.reach_by_entitlement`), which names
#     `agents_toolentitlement`, `agents_agententitlement`,
#     `agents_flowentitlement`, `inference_modelsetentitlement` and
#     `rag_documententitlement` unconditionally. That is MORE of the same
#     thing, not a different thing: the page's whole job is to report
#     what each entitlement reaches, so it reads every table an
#     entitlement can reach INTO, on an empty database as on a full one,
#     and never to decide whether the open principal may see somebody
#     else's row;
#   * `chat-tool-entitlements` (`agents/chat/views/tools.py`) calls
#     `tool_entitlement_ids()`, an unconditional bare
#     `SELECT ... FROM agents_toolentitlement`;
#   * `chat-agent-entitlements` (`agents/chat/views/access.py`) reads
#     both `agents_agententitlement` and `agents_flowentitlement` the
#     same way;
#   * `inference-model-sets` (`models/registry/views.py::model_sets`)
#     reads `ModelSet.objects.prefetch_related(...).all()` -- the base
#     query alone names `inference_modelset`.
# None of these four is a permission CHECK -- none of them decides
# whether the open principal may see somebody ELSE's row, which is the
# claim this module exists to pin (spec section 3.4). Each is the page
# whose entire job IS to list its own table's rows, for an operator
# administering that very inventory, exactly as `identity-users`
# (IA-1, already excluded from this sweep for the same reason) lists
# `identity_user`. Adding any of the four here would not catch a
# regression in access control -- it would only prove that an admin
# listing page lists what it is for, and would break on every future
# entitlement/tool-label/model-set this box ever gains. Confirmed
# directly, not asserted: each of the four was GET-swept against the full
# eleven-table set on an empty database while writing this test, and each
# genuinely names its own table in a captured query -- `offenders` for
# `identity-entitlements` reads
# `['SELECT ... COUNT("identity_entitlementgrant"."id") ...']`, and the
# other three name their own table the same way.
#
# `inference-console` STAYS, unchanged from IA-1: its own IA-2 addition
# (`_registered_connection.html`'s per-connection membership control,
# `models/registry/views.py:_build_context`) reads `ModelSetMember`
# through `connection__in=connections`, and reads `ModelSet.objects.all()`
# for `all_model_sets` -- but ONLY on the WARM-STATE branch, reached when
# at least one `ModelConnection` is registered. On the empty database this
# sweep runs against, `_build_context` takes the cold-start branch instead
# and never reaches either read -- confirmed directly, the same way as
# the four exclusions above: the captured queries for `inference-console`
# name `inference_modelconnection`, `inference_rolebinding` and
# `inference_materialization`, never `inference_modelset` or
# `inference_modelsetmember`.
#
# `identity-settings` (IA-1, T9) IS HERE TOO (IA-2 walkthrough fix W-1):
# it reads only the one allowed `identity_identitysettings` row -- the
# posture that decides everything else -- and, since W-1, renders a
# STATIC sign-in sentence in the open posture with no query of its own.
# Confirmed directly, the same way as every exclusion/inclusion above:
# GET-swept against the full eleven-table set on an empty database, and
# it names none of them.
# `landing` (UI-1) is a new top-level mount -- `config/urls.py`'s `""` --
# and belongs in a sweep whose rule is one representative GET per mount.
# It genuinely pins something: the view issues no query at all, and its
# render path reads only `identity_identitysettings` (the allowed posture
# read) plus `inference_rolebinding`, which is not one of the eleven.
# `settings-index` (UI-2) is the tenth mount -- `config/urls.py`'s
# `settings/` -- and it belongs here for the reason this sweep exists:
# it is the app bar's one door to the operator surfaces, it decides
# WHERE to send the viewer, and "decides who you are" is exactly the
# shape of thing that accidentally grows a permission query. It reads
# the posture row the gate middleware already stashed and, in the open
# posture, `principal_for_request` short-circuits to the open principal
# without touching `identity_user` at all -- which is the claim being
# pinned rather than assumed.
# `chat-workstreams` and `chat-workstream` (Workstreams WS-1 T13) join
# the sweep here. `chat-workstreams` is class A -- no row, and
# `visible_workstreams` returns everything under `sees_all_content`
# without a second query. `chat-workstream` is class O, resolved through
# the same `visible_workstreams`, and its one registered panel
# (`tools.rag.workstreams.panel`) resolves documents through
# `readable_documents`, whose `unrestricted` branch (`document_
# visibility`'s own docstring: "THE OPEN BRANCH IS FIRST") never joins
# `rag_documententitlement` at all -- and the wall it would otherwise
# read (`agents.workstreams.wall_ids`) short-circuits on `accounts_on()`
# before ever touching `agents_workstreamscopeentitlement`, the twelfth
# table this task adds. Confirmed directly, the same way as every
# inclusion this module documents: GET-swept against the full
# twelve-table set on an empty database, and both name none of them.
# `chat-workstream-settings` (settings-page split) joins them here too:
# also class O through `visible_workstreams`, and reachable at 200 on an
# open box the same way `chat-workstream` is (`may_manage_workstream`
# short-circuits to `True` under `sees_all_content` without a query of
# its own). Its own builder, `_settings_context`, reads no eleven-table
# name in the open posture either -- `accounts_on()` gates Scope/Tags/
# Shares off before any of `agents_workstreamscopeentitlement`/`agents_
# share` is ever touched, the identical short-circuit `chat-workstream`'s
# own wall read already takes. Confirmed directly: GET-swept against the
# full twelve-table set on an empty database, and it names none of them.
# `chat-workstream-new` (owner feedback round 7's setup screen) joins
# them too: class A, no row to resolve at all, so `_url_for_mount` needs
# no special case for it the way the two row-addressed names above do.
# `_setup_context` gates its own Scope section on `accounts_on()` alone
# (ruling A) -- False on an open box, so `held_entitlement_ids`/
# `entitlement_names` are never even called, the identical short-circuit
# every other Scope-shaped section on this sweep already takes. Confirmed
# directly: GET-swept against the full twelve-table set on an empty
# database, and it names none of them either.
_MOUNTS = ["landing", "chat-index", "rag-ask-page", "rag-documents", "jobs-queue",
           # "Job execution" (F1, Coherence Wave C; added here at N5 of
           # that wave's review). Class S and reachable at 200 on an open
           # box for the same reason `inference-console` above is. Its
           # own read is ONE `JobSettings.get_solo()` and nothing else --
           # it deliberately takes no queue snapshot -- so it names
           # `queue_jobsettings` and none of the forbidden tables;
           # confirmed by the sweep below rather than asserted here.
           "jobs-settings",
           "inference-console", "setup-index", "settings-index", "identity-groups",
           "identity-settings", "chat-workstreams", "chat-workstream-new",
           "chat-workstream", "chat-workstream-settings"]
if "vision" in settings.FARABUNKER_FEATURES:
    _MOUNTS.append("vision-gallery")
    # Engine files (2026-09-02): class S, like `inference-console` above
    # -- and reachable at 200 on an open box for the same reason that one
    # is, since `IdentityGateMiddleware` returns before it ever looks at
    # a route's tier in open posture. Its own reads
    # (`tools.vision.visibility.known_job_uuids`, the staged-upload query
    # `tools.vision.maintenance._known_staged_uuids`) name
    # `vision_generationjob`/`vision_jobinput`, neither of which is one
    # of the eleven; `sees_all_content` short-circuits to `True` in open
    # posture (`accounts_on()` false) without touching `identity_user`.
    # Confirmed directly, the same way as every inclusion/exclusion this
    # module already documents: GET-swept against the full eleven-table
    # set on an empty database, and it names none of them.
    _MOUNTS.append("vision-engine-files")

# THE ONE MOUNT WHOSE SUCCESSFUL ANSWER IS A REDIRECT. Every other entry
# in `_MOUNTS` renders a page, and the 200 assertion below is this
# sweep's anti-vacuity guard -- a request that got bounced would run no
# forbidden-table query either and would pass for the wrong reason.
# `settings-index` renders nothing by design, so it gets a named
# carve-out of one rather than the guard being loosened for everybody,
# and it pays for the carve-out the same way `identity/tests/
# test_route_matrix.py` makes its own: the 302 has to be the forward
# this route exists to be, never the gate bouncing the caller to login.
_REDIRECTS_INSTEAD_OF_RENDERING = frozenset({"settings-index"})

# `identity_user_groups` (the `User.groups` M2M through table) is NOT its
# own entry: every query this codebase could run against it joins through
# `identity_user` in the same SQL text (there is no code path here that
# queries group membership without first naming the user table), so
# checking for `identity_user` alone already catches it -- a second,
# subsumed entry would just be a second name for the same forbidden fact.
#
# THE TWELVE (IA-2 T18, +1 Workstreams WS-1 T13): `identity_user`
# (IA-1), the two `identity.Entitlement`/`EntitlementGrant` tables, the
# four per-column entitlement/share tables `rag`/`agents` add, the three
# model-set tables (T14), and the stream wall table T13 adds
# (`agents_workstreamscopeentitlement`). Table names come from
# `Model._meta.db_table` in `test_the_forbidden_table_names_are_the_
# real_ones` below, rather than being typed twice, so a rename cannot
# silently make this set (or that test) drift from the real schema.
_FORBIDDEN_TABLES = frozenset({
    "identity_user",
    "identity_entitlement",
    "identity_entitlementgrant",
    "rag_documententitlement",
    "agents_toolentitlement",
    "agents_share",
    "agents_agententitlement",
    "agents_flowentitlement",
    "inference_modelset",
    "inference_modelsetmember",
    "inference_modelsetentitlement",
    "agents_workstreamscopeentitlement",
})


def test_the_forbidden_table_names_are_the_real_ones():
    """Anti-vacuous pin: a table name typed by hand that no longer
    matches the model would make this whole gate assert nothing."""
    from django.apps import apps
    real = {
        apps.get_model(label)._meta.db_table
        for label in ("identity.User", "identity.Entitlement", "identity.EntitlementGrant",
                      "rag.DocumentEntitlement", "agents.ToolEntitlement", "agents.Share",
                      "agents.AgentEntitlement", "agents.FlowEntitlement",
                      "inference.ModelSet", "inference.ModelSetMember",
                      "inference.ModelSetEntitlement", "agents.WorkstreamScopeEntitlement")
    }
    assert real == _FORBIDDEN_TABLES


def _url_for_mount(name):
    """`reverse(name)`, except for a ROW-ADDRESSED mount, which needs a
    real pk to resolve to this mount's own 200 rather than a route
    mismatch. `chat-workstream` and `chat-workstream-settings` are the two
    such names in `_MOUNTS`; each row is built through `identity.tests.
    _helpers.make_workstream` (`apps.get_model`, never a direct `agents`
    import -- this module's own convention) BEFORE the query capture
    starts, so the row's own creation (against `agents_workstream`, not
    one of the twelve) is never mistaken for the thing under test."""
    if name in ("chat-workstream", "chat-workstream-settings"):
        from identity.tests._helpers import make_workstream

        return reverse(name, args=[make_workstream().pk])
    return reverse(name)


@pytest.mark.parametrize("name", _MOUNTS)
def test_no_identity_table_is_queried_on_an_open_box(client, name):
    url = _url_for_mount(name)
    with posture(POSTURE_OPEN):
        with CaptureQueriesContext(connection) as captured:
            response = client.get(url)
    # ANTI-VACUOUS, the other half of it: a request that got bounced
    # (a redirect, a 403, a 404) would ALSO run no forbidden-table
    # query, and would make this pin pass for the wrong reason -- it
    # must actually reach the mount's own 200, in open posture, for the
    # "no permission query" claim to mean anything. The one mount that
    # answers a redirect on purpose gets the same guard in its own
    # shape (`_REDIRECTS_INSTEAD_OF_RENDERING`, above).
    if name in _REDIRECTS_INSTEAD_OF_RENDERING:
        assert response.status_code == 302, response.status_code
        assert not response["Location"].startswith(reverse("identity-login")), (
            name, response["Location"])
    else:
        assert response.status_code == 200, response.status_code
    offenders = [q["sql"] for q in captured.captured_queries
                 if any(table in q["sql"] for table in _FORBIDDEN_TABLES)]
    assert offenders == [], offenders


@pytest.mark.parametrize("name", _MOUNTS)
def test_the_settings_row_is_read_and_that_is_the_allowed_one(client, name):
    """Anti-vacuous pin: a page that queried NOTHING would pass the test
    above by doing nothing, so this asserts the posture really was
    asked."""
    url = _url_for_mount(name)
    with posture(POSTURE_OPEN):
        with CaptureQueriesContext(connection) as captured:
            client.get(url)
    assert any("identity_identitysettings" in q["sql"] for q in captured.captured_queries)
