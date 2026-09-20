"""The regroup's DB-free pin (spec section 3.5).

Renaming a package changes `AppConfig.name`; it must change NOTHING
else. Django derives a label from the module path ONLY when the
subclass has not set one (`django/apps/config.py:34-35`), and every
AppConfig in this project sets `label` explicitly -- so table names
(`f"{label}_{model}"`, no `Meta.db_table` anywhere in the repo),
`django_content_type` rows, and `django_migrations` rows (keyed
`(app_label, name)`) are all untouched by a package move. This module
is the thing that actually enforces that, rather than trusting the
comment next to each `label = "..."` line.

`EXPECTED_TABLES` below is a LITERAL pin: every string in it was read off
the pre-move tree once and typed in by hand, not computed from
`app_label`/`model_name` at test time. A version that recomputed
`f"{app_label}_{model_name}"` on both sides of its own assertion would be
comparing a value to itself -- true by construction, and blind to a label
rename, because both sides shift together (proven by the red run in this
task's report). Comparing live output against strings written down in
advance is what actually proves `inference_modelconnection` does not
silently become `registry_modelconnection` when `models/registry`
moves.

No `conftest.py` (the repo forbids them anywhere) and no
`FARABUNKER_FEATURES` override: the app registry is populated at
startup from whatever flags the run was launched with, and this file
asserts against the vision-enabled set both supported gate states share.
"""
from __future__ import annotations

from io import StringIO

import pytest
from django.apps import apps
from django.core.management import call_command

# Every label this project owns, frozen. A move that changes one of
# these renames a database table; that is a migration, and P0 has none.
# "agents" joined in P2 Task 1 (agents/apps.py) -- a genuinely NEW app,
# not a rename of an existing one, so it is an addition to this set
# rather than evidence the pin's own mechanism failed. It owned no model
# at first; P2 Task 3 (agents/models.py) added its four tables, and P3
# Task 1 (agents/migrations/0002) added a fifth, "agents.flow" -- flows
# are rows (ruling 1). IA-2 Task 4 (agents/migrations/0003_
# toolentitlement_and_share.py) adds two more, "agents.toolentitlement"
# and "agents.share" -- which is why EXPECTED_TABLES below now carries
# seven "agents.*" entries.
# IA-2 Task 15 (agents/migrations/0004_agent_flow_entitlements.py)
# adds two more, "agents.agententitlement" and "agents.flowentitlement"
# -- which is why EXPECTED_TABLES below now carries nine
# "agents.*" entries.
# "chat" joined in P3 Task 3 (agents/chat/apps.py) -- like "agents"
# before it, a genuinely NEW app rather than a rename, so it is an
# addition to this set rather than evidence the pin's mechanism
# failed. `agents.chat` itself owns no model, so it contributes no
# entry to EXPECTED_TABLES -- but P3 Task 1 adds `agents.flow` to that
# dict for the `agents` app, so do not read this line as "P3 changes
# no tables".
# "identity" joined in IA-1 Task 2 (identity/apps.py) -- another
# genuinely NEW app, not a rename, so it is an addition here too. Its
# three tables (`identity.user`, `identity.identitysettings`,
# `identity.auditevent`) are new entries in EXPECTED_TABLES below,
# alongside the two auto-created many-to-many join tables
# `AbstractUser` brings with it (`identity_user_groups`,
# `identity_user_user_permissions`) -- those two have no model of
# their own, so they contribute no `EXPECTED_TABLES` key, exactly like
# any other Django-generated M2M table in this project. IA-2 Task 1
# (identity/migrations/0003_entitlement_and_grant.py) adds two more:
# `identity.entitlement` and `identity.entitlementgrant`. IA-2 Task 9
# (tools/rag/migrations/0015_documententitlement.py) adds one more:
# `rag.documententitlement` -- the library's own label table, joined to
# `identity.Entitlement` by string FK.
# "landing" joined in UI-1 (foundation/landing/apps.py) -- the front door
# at `/`. Like "agents", "chat" and "identity" before it, a genuinely NEW
# app rather than a rename, so it is an addition to this set rather than
# evidence the pin's own mechanism failed. It owns no model, so it
# contributes no EXPECTED_TABLES entry at all.
# Workstreams v1 Task 2 (agents/migrations/0006_workstream.py) adds two
# more: `agents.workstream` and `agents.workstreamscopeentitlement` --
# the wall table's own name deliberately unrelated to the four existing
# `*entitlement_labels`/`*Entitlement` tables it is not a sixth of (see
# agents/models.py::WorkstreamScopeEntitlement).
# Workstreams v1 Task 16 (agents/migrations/0007_workstream_taint.py)
# adds two more: `agents.conversationtaint` and `agents.workstreamtaint`
# -- the taint tags, one per level (see agents/models.py::
# ConversationTaint / WorkstreamTaint). The same migration also adds
# `Conversation.consolidated_through_index`/`consolidated_at`, two
# columns on an EXISTING table, so they contribute no new
# EXPECTED_TABLES key.
# Round 21 (agents/migrations/0010_chat_settings.py) adds ONE more:
# `agents.chatsettings`, the chat column's operator-policy singleton
# (see agents/models.py::ChatSettings). It lives under the `agents`
# label, not `chat`: `agents.chat` owns no model at all, which is the
# same reason that app contributes no key of its own here.
EXPECTED_LABELS = frozenset({
    "agents", "chat", "rag", "vision", "inference", "jobs", "setup", "ops",
    "identity", "landing",
})

# Every concrete model this project owns, frozen as `"{app_label}.{model_name}"
# -> literal db_table string`. Read off `apps.get_models()` ONCE on the
# pre-move tree and typed in by hand -- NOT computed from app_label/model_name
# here, or this would just be the derivation formula compared to itself.
EXPECTED_TABLES = {
    "agents.agent": "agents_agent",
    "agents.agententitlement": "agents_agententitlement",
    "agents.chatsettings": "agents_chatsettings",
    "agents.conversation": "agents_conversation",
    "agents.conversationtaint": "agents_conversationtaint",
    "agents.flow": "agents_flow",
    "agents.flowentitlement": "agents_flowentitlement",
    "agents.share": "agents_share",
    "agents.toolentitlement": "agents_toolentitlement",
    "agents.toolinvocation": "agents_toolinvocation",
    "agents.turn": "agents_turn",
    "agents.workstream": "agents_workstream",
    "agents.workstreamscopeentitlement": "agents_workstreamscopeentitlement",
    "agents.workstreamtaint": "agents_workstreamtaint",
    "inference.materialization": "inference_materialization",
    "inference.modelconnection": "inference_modelconnection",
    "inference.modelset": "inference_modelset",
    "inference.modelsetentitlement": "inference_modelsetentitlement",
    "inference.modelsetmember": "inference_modelsetmember",
    "inference.rolebinding": "inference_rolebinding",
    "jobs.inferencejob": "jobs_inferencejob",
    "jobs.jobsettings": "jobs_jobsettings",
    "rag.askrecord": "rag_askrecord",
    "rag.category": "rag_category",
    "rag.document": "rag_document",
    "rag.documentattachment": "rag_documentattachment",
    "rag.documententitlement": "rag_documententitlement",
    "rag.documentrow": "rag_documentrow",
    "rag.ragsettings": "rag_ragsettings",
    "rag.workstreampin": "rag_workstreampin",
    "vision.generatedoutput": "vision_generatedoutput",
    "vision.generationjob": "vision_generationjob",
    "vision.jobinput": "vision_jobinput",
    "identity.user": "identity_user",
    "identity.user_groups": "identity_user_groups",
    "identity.user_user_permissions": "identity_user_user_permissions",
    "identity.identitysettings": "identity_identitysettings",
    "identity.auditevent": "identity_auditevent",
    "identity.entitlement": "identity_entitlement",
    "identity.entitlementgrant": "identity_entitlementgrant",
}


def _project_app_configs():
    """Every installed AppConfig this repository authored -- Django's
    own contrib apps excluded by name, since their labels are not ours
    to pin. There is no third-party app to exclude any more: C-55
    removed `rest_framework`, the only one this box ever installed."""
    return [cfg for cfg in apps.get_app_configs() if not cfg.name.startswith("django.")]


def test_every_project_app_label_is_exactly_what_it_was_before_the_regroup():
    assert {cfg.label for cfg in _project_app_configs()} == EXPECTED_LABELS


def test_every_app_config_sets_its_label_explicitly_on_the_subclass():
    """The mechanism, not just the outcome: if a subclass stopped setting
    `label`, Django would start deriving it from `name` -- and the very
    next package move would silently rename a table."""
    for cfg in _project_app_configs():
        assert "label" in type(cfg).__dict__, (
            f"{cfg.name} does not set `label` on its AppConfig subclass; "
            f"Django would derive it from the module path."
        )


def test_every_table_name_matches_its_pinned_literal():
    """Literal-string pin, deliberately NOT a re-derivation: see
    `EXPECTED_TABLES` above for why computing `f"{app_label}_{model_name}"`
    on both sides here would make this assertion untestable (it stayed
    green under a live app-label rename in this task's own red run,
    because a renamed label changes `meta.app_label` and the recomputed
    expectation together). Every value below was typed from the pre-move
    tree, so a package move that quietly renames a table is a literal
    string mismatch, not a tautology.

    `include_auto_created=True`: `identity.User` (`AUTH_USER_MODEL`,
    IA-1 Task 2) inherits `AbstractUser`'s `groups`/`user_permissions`
    many-to-many fields, and Django auto-creates a through table+model
    for each (`identity_user_groups`, `identity_user_user_permissions`)
    -- tables an app-label rename would silently rename exactly like
    any other, so this pin covers them too rather than exempting the
    one model in the tree that happens to have M2M fields."""
    actual = {
        f"{model._meta.app_label}.{model._meta.model_name}": model._meta.db_table
        for model in apps.get_models(include_auto_created=True)
        if model._meta.app_label in EXPECTED_LABELS
    }
    # Same set of models mapped -- catches an added/removed/renamed model
    # even in the freak case its table name happened to collide with another.
    assert set(actual) == set(EXPECTED_TABLES)
    # Same set of table names -- the literal pin.
    assert set(actual.values()) == set(EXPECTED_TABLES.values())
    # Per-model: which model, not just which set, moved.
    for key, expected_table in EXPECTED_TABLES.items():
        assert actual[key] == expected_table, (
            f"{key}: expected table {expected_table!r}, got {actual[key]!r}"
        )


@pytest.mark.django_db
def test_makemigrations_check_reports_no_missing_migrations():
    """P0 contains zero migrations. `--check` exits non-zero (SystemExit)
    when Django wants one, so an un-raised call IS the assertion."""
    out = StringIO()
    try:
        call_command("makemigrations", "--check", "--dry-run", stdout=out, stderr=out)
    except SystemExit:
        pytest.fail("makemigrations --check found unmade migrations:\n" + out.getvalue())


@pytest.mark.django_db
def test_showmigrations_reports_every_migration_applied():
    """`django_migrations` needs no new rows: every migration the test
    database was built with is applied, and a package move adds none."""
    out = StringIO()
    call_command("showmigrations", "--list", stdout=out)
    unapplied = [
        line.strip()
        for line in out.getvalue().splitlines()
        if line.strip().startswith("[ ]")
    ]
    assert unapplied == []
