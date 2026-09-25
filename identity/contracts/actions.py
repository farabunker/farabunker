"""The closed vocabulary of things that get audited.

DECLARED IN FULL NOW, including the sixteen actions only IA-2 ever
writes (spec section 18.1: "actions IA-2 writes are declared now, so the
vocabulary is not amended twice"). A tuple, not a str: a typo'd action
name is a construction error in `AuditEvent.save()` rather than a
category that silently splits an audit report in two.

Page views, document reads, search queries and tool calls are
DELIBERATELY absent (spec section 13.3). The first three would produce a
log nobody reads, in which the interesting lines are invisible; tool
calls already have a better record in `agents.models.ToolInvocation`.
The two tables exist separately on purpose -- one answers "who changed
what about this box", the other "what did the machinery do" -- and
merging them would make retention policy impossible, because the first
must be kept and the second must be prunable.
"""
from __future__ import annotations

# -- session ------------------------------------------------------------
LOGIN = "identity.login"
LOGIN_FAILED = "identity.login_failed"
LOGOUT = "identity.logout"

# -- account ------------------------------------------------------------
USER_CREATED = "identity.user_created"
USER_DEACTIVATED = "identity.user_deactivated"
USER_REACTIVATED = "identity.user_reactivated"
PASSWORD_CHANGED = "identity.password_changed"
PASSWORD_RESET = "identity.password_reset"
SUPERUSER_GRANTED = "identity.superuser_granted"
SUPERUSER_REVOKED = "identity.superuser_revoked"

# -- posture and ownership ---------------------------------------------
POSTURE_CHANGED = "identity.posture_changed"
LIBRARY_POSTURE_CHANGED = "identity.library_posture_changed"
ADMIN_CONTENT_ACCESS_CHANGED = "identity.admin_content_access_changed"
ADOPTED = "identity.adopted"
OWNER_REASSIGNED = "identity.owner_reassigned"

# -- entitlements and groups (IA-2 writes these) ------------------------
ENTITLEMENT_CREATED = "entitlement.created"
ENTITLEMENT_RENAMED = "entitlement.renamed"
ENTITLEMENT_DELETED = "entitlement.deleted"
GRANT_ADDED = "grant.added"
GRANT_ROLE_CHANGED = "grant.role_changed"
GRANT_REVOKED = "grant.revoked"
GROUP_CREATED = "group.created"
GROUP_DELETED = "group.deleted"
GROUP_MEMBER_ADDED = "group.member_added"
GROUP_MEMBER_REMOVED = "group.member_removed"

# -- labels and shares (IA-2 writes these) ------------------------------
DOCUMENT_LABELLED = "library.document_labelled"
DOCUMENT_UNLABELLED = "library.document_unlabelled"
# `DOCUMENT_CONTAINED` (2026-09-03, WS-1): a document's `Document.
# workstream` set or cleared by `tools.rag.workstreams.
# set_document_workstream`. `library.`, not `workstream.`, because
# `DOCUMENT_LABELLED` already takes the `library.` namespace for the same
# subject row -- a containment change is a fact about the document, the
# same as a label change.
DOCUMENT_CONTAINED = "library.document_contained"
TOOL_LABELLED = "tool.labelled"
TOOL_UNLABELLED = "tool.unlabelled"
SHARE_ADDED = "share.added"
SHARE_REVOKED = "share.revoked"
# `LIBRARY_SETTINGS_UPDATED` (S3, Coherence Wave B backend audit): any of
# `tools.rag.models.RagSettings`'s seven fields -- history retention,
# upload/media/page caps, the two retrieval knobs, hybrid search --
# changed. `library.`, the SAME namespace `DOCUMENT_LABELLED`/
# `DOCUMENT_CONTAINED` already use for this page (its own title is
# "Library"), rather than a `ragsettings.` namespace that would name the
# model instead of the page. ONE action for all seven fields (`detail`
# carries `field=`/`to=`), matching this codebase's existing rule that a
# settings namespace names WHAT domain changed, not which literal column
# -- the same way `POSTURE_CHANGED`/`LIBRARY_POSTURE_CHANGED`/
# `ADMIN_CONTENT_ACCESS_CHANGED` are the three IdentitySettings fields
# that ARE split, because those three are semantically distinct security
# postures; RagSettings' seven are all the same kind of thing (an
# operator-tuned numeric cap or toggle), the way `session_idle_minutes`
# is on `IdentitySettings`. `docs/EXTENDING.md`'s "the audit decision"
# step states the house DEFAULT for a settings write with no rationale
# of its own is AUDITED -- the rule S3 (this finding) applies to close
# RagSettings/JobSettings/ModelConnection/RoleBinding's gap, the four
# surfaces S3 actually names. `session_idle_minutes` is NOT one of
# those four, and is not silently exempted by this default: its own
# omission is separately, pre-existingly argued at `identity/
# services.py::set_posture`'s own docstring ("an operational tuning
# knob, not a change to who can see what") -- see that write site
# (M5, Coherence Wave B review) for the recorded reason, not a gap this
# comment's "house default" framing accidentally implies.
LIBRARY_SETTINGS_UPDATED = "library.settings_updated"
# `QUEUE_SETTINGS_UPDATED` (S3): any of `models.queue.models.
# JobSettings`'s four fields -- memory budget, max concurrent jobs,
# retention limit, default priority -- changed. Same "one action per
# settings domain" reasoning as `LIBRARY_SETTINGS_UPDATED` above.
QUEUE_SETTINGS_UPDATED = "queue.settings_updated"
# `CONNECTION_*` (S3): a `models.registry.models.ModelConnection` --
# including its `footprint_override_bytes`/`context_window` overrides --
# registered, edited, or removed. `connection.`, naming the row this
# platform calls a "connection" everywhere else (`models/registry/
# README.md`, the console template, `ModelConnection` itself), not
# `model.` -- this box explicitly never audits WHICH model a connection
# names (no model names in committed text; the row's own `name` field is
# the operator's free-text label, not a vendor/model identifier).
CONNECTION_CREATED = "connection.created"
CONNECTION_UPDATED = "connection.updated"
CONNECTION_DELETED = "connection.deleted"
# `ROLE_*` (S3): a `models.registry.models.RoleBinding` bound to a
# connection, or cleared. Two actions, not one, mirroring `MODELSET_
# ATTACHED`/`MODELSET_DETACHED` below -- an assign and an unassign are
# opposite, equally significant facts about who a role currently answers
# to, not one "changed" event with the direction buried in `detail`.
ROLE_ASSIGNED = "role.assigned"
ROLE_UNASSIGNED = "role.unassigned"

# The `modelset.*` seven, and the `agent.*`/`flow.*` four Task 15 adds,
# arrived with the 2026-08-30 owner directives (spec sections 22.31 and
# 22.33), AFTER the catalogue was closed in IA-1. That is the one
# amendment to "the vocabulary is not amended twice" this phase makes,
# and it is recorded rather than quietly absorbed: three label kinds that
# existed in no plan when the catalogue was written cannot have had
# actions reserved for them.
#
# THE SET VOCABULARY MIRRORS THE GROUP VOCABULARY on purpose -- a model
# set is a group of models, `auth.Group` is a group of people, and giving
# the two different verb shapes would make one audit report read two
# ways. `modelset.attached`/`detached` have no group twin only because a
# group needs none: a grant covers there what an attachment covers here.
MODELSET_CREATED = "modelset.created"
MODELSET_RENAMED = "modelset.renamed"
MODELSET_DELETED = "modelset.deleted"
MODELSET_MEMBER_ADDED = "modelset.member_added"
MODELSET_MEMBER_REMOVED = "modelset.member_removed"
MODELSET_ATTACHED = "modelset.attached"
MODELSET_DETACHED = "modelset.detached"

# Task 15 (spec sections 6.11, 9.6, 22.33): an agent or a flow labelled
# with an entitlement, or unlabelled. Direct labels, not sets -- see
# `agents.models.AgentEntitlement`/`FlowEntitlement`.
AGENT_LABELLED = "agent.labelled"
AGENT_UNLABELLED = "agent.unlabelled"
FLOW_LABELLED = "flow.labelled"
FLOW_UNLABELLED = "flow.unlabelled"

# The agent form (chat cluster, feature B): a row created or edited from
# `/chat/agents/` or `/settings/agents/`. `agent.edited` names WHICH
# FIELDS changed and never their contents -- a system prompt is the
# operator's own text.
AGENT_CREATED = "agent.created"
AGENT_EDITED = "agent.edited"

# Engine files (2026-09-02): an administrator bulk-deleting the image
# engine's own phantom output/input files, straight off disk
# (`tools/vision/maintenance.py::engine_files_delete`). ONE row per
# delete REQUEST, not per file -- see that view's own docstring.
ENGINE_FILE_DELETED = "vision.engine_file_deleted"

# Workstreams (2026-09-03, WS-1): a named scoped work area, its wall, and
# its upload default. Naming follows the existing convention exactly --
# dotted `namespace.verb_phrase`, past tense, constant name the
# SCREAMING_SNAKE of the value's tail.
#
# `SHARE_ADDED`/`SHARE_REVOKED` are REUSED, with `target_type=
# "workstream"`: they already carry the subject in `detail` and the
# target type in its own column, and a second pair of constants would
# split one question -- "who was this shared with" -- across two
# vocabularies.
WORKSTREAM_CREATED = "workstream.created"
WORKSTREAM_RENAMED = "workstream.renamed"
WORKSTREAM_DELETED = "workstream.deleted"
WORKSTREAM_ARCHIVED = "workstream.archived"
WORKSTREAM_UNARCHIVED = "workstream.unarchived"
WORKSTREAM_INSTRUCTIONS_SET = "workstream.instructions_set"
WORKSTREAM_SCOPE_ADDED = "workstream.scope_added"
WORKSTREAM_SCOPE_REMOVED = "workstream.scope_removed"
WORKSTREAM_UPLOAD_DEFAULT_SET = "workstream.upload_default_set"
# ROUND 17 (owner: "a bool in settings to use all rag documents ...
# default it on"): AUDITED, like upload-default/instructions and unlike
# description -- this toggle changes what the stream's own corpus
# admits at retrieval time, the "access consequence" §16.3's rule keys
# the trail on, not a label with none.
WORKSTREAM_INCLUDE_UNIVERSAL_SET = "workstream.include_universal_set"

# Pinning (2026-09-03, WS-1): a universal document associated into a
# stream's working set, or removed from it. `target_type` is
# "workstream" for each -- the pin is a change to the stream's working
# set, and `for_target("workstream", pk)` is the question an operator
# asks.
DOCUMENT_PINNED = "workstream.document_pinned"
DOCUMENT_UNPINNED = "workstream.document_unpinned"

# Taint (2026-09-03, WS-2, Task 16): an entitlement whose labelled
# material retrieval has actually returned into a conversation or a
# stream, or the removal of that fact when the entitlement itself is
# deleted (`agents.workstreams.workstream_entitlement_cascade`) -- the one
# path a tag is removed in v1.
WORKSTREAM_TAINTED = "workstream.tainted"
CONVERSATION_TAINTED = "conversation.tainted"
WORKSTREAM_UNTAINTED = "workstream.untainted"
CONVERSATION_UNTAINTED = "conversation.untainted"

# Consolidation (2026-09-03, WS-2, Task 18): a stream conversation was
# distilled into its contained note document (spec §10.4). `target_type=
# "workstream"`: consolidation is a stream mutation, beside re-sharing,
# wall edits and pin changes.
WORKSTREAM_CONSOLIDATED = "workstream.consolidated"

AUDIT_ACTIONS = (
    LOGIN, LOGIN_FAILED, LOGOUT,
    USER_CREATED, USER_DEACTIVATED, USER_REACTIVATED, PASSWORD_CHANGED,
    PASSWORD_RESET, SUPERUSER_GRANTED, SUPERUSER_REVOKED,
    POSTURE_CHANGED, LIBRARY_POSTURE_CHANGED, ADMIN_CONTENT_ACCESS_CHANGED,
    ADOPTED, OWNER_REASSIGNED,
    ENTITLEMENT_CREATED, ENTITLEMENT_RENAMED, ENTITLEMENT_DELETED,
    GRANT_ADDED, GRANT_ROLE_CHANGED, GRANT_REVOKED,
    GROUP_CREATED, GROUP_DELETED, GROUP_MEMBER_ADDED, GROUP_MEMBER_REMOVED,
    DOCUMENT_LABELLED, DOCUMENT_UNLABELLED, DOCUMENT_CONTAINED, TOOL_LABELLED, TOOL_UNLABELLED,
    SHARE_ADDED, SHARE_REVOKED,
    LIBRARY_SETTINGS_UPDATED, QUEUE_SETTINGS_UPDATED,
    CONNECTION_CREATED, CONNECTION_UPDATED, CONNECTION_DELETED,
    ROLE_ASSIGNED, ROLE_UNASSIGNED,
    MODELSET_CREATED, MODELSET_RENAMED, MODELSET_DELETED,
    MODELSET_MEMBER_ADDED, MODELSET_MEMBER_REMOVED,
    MODELSET_ATTACHED, MODELSET_DETACHED,
    AGENT_LABELLED, AGENT_UNLABELLED, FLOW_LABELLED, FLOW_UNLABELLED, AGENT_CREATED, AGENT_EDITED,
    ENGINE_FILE_DELETED,
    WORKSTREAM_CREATED, WORKSTREAM_RENAMED, WORKSTREAM_DELETED,
    WORKSTREAM_ARCHIVED, WORKSTREAM_UNARCHIVED, WORKSTREAM_INSTRUCTIONS_SET,
    WORKSTREAM_SCOPE_ADDED, WORKSTREAM_SCOPE_REMOVED, WORKSTREAM_UPLOAD_DEFAULT_SET,
    WORKSTREAM_INCLUDE_UNIVERSAL_SET,
    DOCUMENT_PINNED, DOCUMENT_UNPINNED,
    WORKSTREAM_TAINTED, CONVERSATION_TAINTED, WORKSTREAM_UNTAINTED, CONVERSATION_UNTAINTED,
    WORKSTREAM_CONSOLIDATED,
)

# Where an audited action was performed. `web` is a page, `cli` a
# management command, `admin` Django's break-glass surface.
SOURCE_WEB = "web"
SOURCE_CLI = "cli"
SOURCE_ADMIN = "admin"
SOURCE_CHOICES = (
    (SOURCE_WEB, "Web page"),
    (SOURCE_CLI, "Command line"),
    (SOURCE_ADMIN, "Django admin"),
)
