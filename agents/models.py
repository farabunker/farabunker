"""The agent column's data model (spec section 7, deviations D1/D2).

FOUR LIFETIMES, grouped by what a row is FOR rather than by a count
(this docstring said "Nine tables" for several phases after it had
stopped being nine -- workstreams, taints and now `ChatSettings` all
landed without it; a structural claim is the one a reader can still
trust, the same correction `agents/chat/views/__init__.py`'s own
docstring already made for the identical reason):

- `Agent` / `Conversation` / `Turn` / `Flow` are the CONVERSATION side.
  `Agent` and `Flow` are both declarations -- a resident actor and a
  named recipe, respectively, each a ROW rather than a code declaration
  for the same reason (`AppConfig.ready()` may not touch the database).
  `Conversation` and `Turn` are what runs against one: a `Turn` is
  replayed into a prompt, so `Turn.tool_call` is exactly what the model
  was told and nothing else may be smuggled into it.
- `ToolInvocation` is the AUDIT TRAIL. It is never replayed into
  anything. It records WHO called (a principal, not an agent -- an
  external MCP `tools/call` has no conversation at all), what ran, how it
  ended, and how long it took. The 2026-08-27 addendum's consequence 3
  is why it is its own row rather than columns on `Turn`.
- `ToolEntitlement`, `AgentEntitlement`, `FlowEntitlement` and `Share`
  (IA-2) are the ACCESS side -- none of them replayed into a prompt nor
  an audit row. `ToolEntitlement` labels a code-registered tool key with
  the entitlement that gates it; `AgentEntitlement`/`FlowEntitlement`
  (spec section 6.11, the 2026-08-30 second directive) do the same for
  an `Agent`/`Flow` ROW, direct labels rather than sets because an agent
  is a handful of long-lived rows an operator names once, not a fleet
  that churns (spec section 22.33); `Share` extends a row someone owns
  to somebody else.
- `ChatSettings` (round 21) is OPERATOR POLICY -- one singleton row,
  never replayed, never audited, holding what an operator decided about
  how a conversation's prompt is built. Its own docstring has the
  reasoning for its being a row rather than an environment variable,
  and for its being here rather than a column on somebody else's
  settings table.

No import of `models.queue` anywhere in this file: `Turn.queue_job_id`
is a plain `BigIntegerField`, exactly as `tools/vision/models.py:135-143`
records for `GenerationJob.queue_job_id`, because import-law rule 2
forbids an `agents/*` app reaching into the queue's storage layer.
"""
from __future__ import annotations

import logging
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from agents.limits import MAX_STEPS_DEFAULT
from models.contracts.roles import CHAT_CONVERSE_ROLE

logger = logging.getLogger(__name__)


def _refuse_slug_change(instance, noun: str, resolvers: str) -> None:
    """Raise `ValueError` if `instance.slug` differs from what is stored
    under its pk.

    Shared by `Agent.save()` and `Flow.save()` (CQ-10): both carried the
    identical eleven-line block, differing only in the noun and the two
    resolvers named in the message. `noun` is the possessive opener
    ("An agent's", "A flow's"); `resolvers` is everything from what
    resolves the slug through the "create a new ... instead" close, so
    each caller's exact wording survives verbatim. The slug stays
    immutable on both because it is a KEY, not a label: something else
    always resolves it by that string.
    """
    if not instance.pk:
        return
    stored_slug = (
        type(instance).objects.filter(pk=instance.pk)
        .values_list("slug", flat=True).first()
    )
    if stored_slug is not None and stored_slug != instance.slug:
        raise ValueError(
            f"{noun} slug is immutable: {stored_slug!r} cannot become "
            f"{instance.slug!r}. {resolvers}"
        )


class Agent(models.Model):
    """One agent: a system prompt, an LLM role, and granted tool keys.

    `tool_keys` validation is ruling R1, and the asymmetry IS the
    ruling: a key whose REGISTERED spec is `mutates=True` is REJECTED by
    name (ADR 0010:266-276 -- a settings-mutating tool is registered but
    not grantable before Identity & Auth, so a row granting one must
    never save); a key that is not registered AT ALL is ACCEPTED and
    logged. The second case is the one a strict check gets wrong: the
    `general` default grants the vision tools, which are not registered
    when the `vision` feature flag is off, and rejecting them would make
    installing that shipped default (`agents.defaults.install_default`)
    fail on a perfectly legal install. An unregistered key is a not-yet
    or a not-here, never a privilege escalation -- there is nothing to
    escalate TO, because `granted_tools` drops it again before a prompt
    is ever built.

    Three conditions gate a tool call, checked in three different
    places, and all three must hold: the row names it (here), it is
    registered and non-mutating (`granted_tools`,
    `agents/contracts/tools.py`), and its declared roles resolve
    (`plan_turn`, `agents/runtime/jobs.py`).

    RULING 3 (2026-08-28): `resident=True` is an ORIGIN MARKER, not a
    lock. It records that this row started life as a shipped default
    (`agents/resident.py`) -- the operator owns it from the moment it
    exists, and `manage.py install_defaults --reset <slug>` is how the
    shipped text comes back, a deliberate act with that text in front of
    them. P2's edit-lock and its `_from_resident_sync` bypass are gone;
    only `slug` stays immutable (see `save()`).
    """

    slug = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    system_prompt = models.TextField(blank=True, default="")
    # Which RoleSpec backs this agent's LLM. A per-agent override is
    # ROADMAP:242-245's "per-role model split as a first-class outcome",
    # realized: a lean model can back one agent and a conversational one
    # another, with no framework change.
    llm_role = models.CharField(max_length=255, default=CHAT_CONVERSE_ROLE)
    tool_keys = models.JSONField(default=list, blank=True)
    max_steps = models.PositiveIntegerField(default=MAX_STEPS_DEFAULT)
    # True for a row that STARTED life as a code-declared default
    # (`agents/resident.py`). RULING 3: an origin marker, not a lock --
    # see the class docstring.
    resident = models.BooleanField(default=False)
    # THE AUDIENCE, distinct from `resident` the ORIGIN MARKER above.
    # True means "everybody on this box may use this row"; False means
    # "only the people its ownership and its labels reach".
    #
    # A SEPARATE COLUMN RATHER THAN AN OVERLOAD OF `resident` (spec
    # decision 6, owner ruling flag 2). `resident` has a SECOND READER
    # -- `agents.visibility.resident_agent_tool_keys`, which warns the
    # tool-label page that labelling a tool a SHIPPED agent declares
    # makes the shell path silently weaker -- and overloading it would
    # make that warning fire for rows that were never shipped defaults,
    # while making the agent form's own audience control lie about where
    # a row came from.
    #
    # `visible_agents` AND-s the entitlement label clause onto its
    # ownership OR, so `box_wide=True` is not a bypass: a box-wide row
    # narrowed to an entitlement reaches everybody on this box WHO HOLDS
    # IT, which is the useful fourth row of §4.3.1's truth table.
    box_wide = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    # RULING 4b (2026-08-27 addendum / 2026-08-28 ruling): the principal
    # that owns this row, in the same shape as `Conversation`'s identical
    # pair below. Blank until Identity & Auth introduces the user
    # principal kind; "my agents" is then a filter, not a migration.
    owner_kind = models.CharField(max_length=32, blank=True, default="")
    owner_key = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]
        constraints = [models.UniqueConstraint(Lower("slug"), name="uniq_agent_slug_ci")]
        indexes = [
            models.Index(fields=["owner_kind", "owner_key"], name="agents_agent_owner"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.slug

    def save(self, *args, **kwargs) -> None:
        """Validate `tool_keys` (ruling R1), then refuse a slug change.

        RULING 3 (2026-08-28) removed P2's resident edit-lock and its
        `_from_resident_sync` bypass. `resident=True` is now an ORIGIN
        MARKER, not a lock: it records that this row started life as a
        shipped default, and the operator owns it from that moment.
        `manage.py install_defaults --reset <slug>` restores the
        shipped text; nothing else ever rewrites a row behind the
        operator's back.

        The slug stays immutable because it is a KEY, not a label:
        `flow.run` resolves one, an `agent.<slug>` grant names one, and
        `--reset` matches on one.
        """
        self._validate_tool_keys()
        _refuse_slug_change(
            self, "An agent's",
            "Grants, tool keys, and `install_defaults --reset` all resolve it. "
            "Create a new agent instead.",
        )
        super().save(*args, **kwargs)

    def _validate_tool_keys(self) -> None:
        from agents.contracts.tools import all_tools

        if not isinstance(self.tool_keys, list) or not all(
            isinstance(key, str) for key in self.tool_keys
        ):
            raise ValueError(
                f"Agent {self.slug!r}: tool_keys must be a list of strings, "
                f"got {self.tool_keys!r}"
            )
        registered = {spec.key: spec for spec in all_tools()}
        mutating = sorted(
            key for key in self.tool_keys if key in registered and registered[key].mutates
        )
        if mutating:
            raise ValueError(
                f"Agent {self.slug!r} may not be granted {', '.join(mutating)}: a tool "
                f"that changes state which already exists is registered but not "
                f"grantable until Identity & Auth lands (ADR 0010)."
            )
        for key in self.tool_keys:
            if key not in registered:
                logger.info(
                    "agents: %r grants %r, which is not registered on this install "
                    "(a feature-gated or not-yet-shipped tool). Written verbatim; "
                    "dropped again when the prompt is built.",
                    self.slug, key,
                )


class Flow(models.Model):
    """One flow: a named, multi-step recipe an agent can run as a single
    tool call (ruling 1, 2026-08-28).

    A row, not a code declaration -- the same shape `Agent` already
    takes, and for the same reason: `AppConfig.ready()` may not touch
    the database, so N flows cannot become N registered `ToolSpec`s.
    Ruling 1's answer is ONE registered tool, `flow.run`
    (`agents/runtime/flowtool.py`), whose `flow` param's
    choices are filled per turn from the enabled rows a principal may
    see, and whose runner loads the chosen row by slug at call time.

    `resident=True` is the same ORIGIN MARKER `Agent.resident` is
    (ruling 3): "this row started life as a shipped default." It locks
    nothing; the operator owns the row from the moment it exists, and
    `manage.py install_defaults --reset <slug>` is how the shipped
    steps come back.
    """

    slug = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    # [{"key": ..., "kind": ..., "label": ...}, ...] -- one entry per
    # value the flow's caller supplies, in `Param`'s own vocabulary
    # (`models/contracts/operations.py`). Validated by
    # `agents.defaults.validate_flow_json`, the SAME rule the
    # shipped catalogue is checked by.
    inputs = models.JSONField(default=list, blank=True)
    # [{"tool": ..., "args": {...}}, ...] -- one entry per step, each
    # `args` value either a literal or a `$`-prefixed reference resolved
    # against the steps that precede it. Validated by the same function.
    steps = models.JSONField(default=list, blank=True)
    # True for a row that STARTED life as a code-declared default
    # (`agents/defaults.py`). RULING 3: an origin marker, not a
    # lock -- see the class docstring.
    resident = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    # RULING 4b: the principal that owns this row, in the same shape as
    # `Agent`'s and `Conversation`'s identical pair. Blank until Identity
    # & Auth introduces the user principal kind.
    owner_kind = models.CharField(max_length=32, blank=True, default="")
    owner_key = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]
        constraints = [models.UniqueConstraint(Lower("slug"), name="uniq_flow_slug_ci")]
        indexes = [
            models.Index(fields=["owner_kind", "owner_key"], name="agents_flow_owner"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.slug

    def save(self, *args, **kwargs) -> None:
        """Validate `inputs`/`steps` against the SAME rules the shipped
        catalogue is checked by, then refuse a slug change.

        `agents.defaults.validate_flow_json(inputs, steps)` is the one
        validator. A row and a shipped default that were
        checked by two different rules would be two different things
        wearing one name, and the row is the thing that actually runs.

        It checks SHAPE only -- every `$` reference syntactically
        resolvable against the steps that PRECEDE it, no step naming an
        `agent.*` or a `flow.*` key. Whether a step's tool is REGISTERED
        is an install fact checked at run time (ruling R1's tolerance:
        a vision step on a box with the vision flag off is a not-here,
        not a fault).

        The slug stays immutable for the same reason `Agent.slug` does:
        it is what `flow.run` resolves and what `--reset` matches.
        """
        from agents.defaults import validate_flow_json

        validate_flow_json(self.inputs, self.steps)
        _refuse_slug_change(
            self, "A flow's",
            "`flow.run` and `install_defaults --reset` both resolve it. "
            "Create a new flow instead.",
        )
        super().save(*args, **kwargs)


class Conversation(models.Model):
    """A thread of turns with one agent.

    UUID pk, matching `GenerationJob` (`tools/vision/models.py:101`) and
    the `ChatSession` this replaces. `PROTECT` on `agent` so an agent
    can never be deleted out from under a conversation. `title` is set
    from the first ~60 characters of the first user turn and is NEVER
    generated by a model -- that would be a second, invisible model call
    per conversation.

    `owner_kind` / `owner_key` are the principal that owns this
    conversation; blank until Identity & Auth introduces the user
    principal kind -- "my conversations" is then a filter, not a
    migration.

    `archived_at` is A TIMESTAMP, NOT A BOOLEAN (UI-3b). `null` means
    active and a value means "archived, and this is when" -- a boolean
    would answer the first question and throw the second away, and
    "when did this leave my list" is the one thing an operator asks
    about an archived row. Archiving is reversible and destroys
    nothing: it is exactly a list filter, which is why the column
    lives here rather than the rows moving anywhere.

    `pinned_at` (ROUND 20, owner feedback: "can we add the ability t[o]
    pin chats so it's always shown in its own se[c]tion... it should
    only appear if we have somethign that is pinned") is the IDENTICAL
    shape as `archived_at`, for the identical reason -- `null` means not
    pinned, a value is "pinned, and this is when", and the sidebar's own
    PINNED section (`agents/chat/sidebar.py`) orders by it, most recent
    first, which a boolean could not answer either. PINNING IS THE
    OWNER'S OWN BOOKMARK, not a per-viewer one (owner decision, this
    round): the column is on the conversation row itself, exactly like
    `archived_at`, so a share recipient sees whatever the owner pinned,
    never their own independent set -- a per-viewer pin table is a
    genuinely different feature (spec has no request for it yet) and is
    left as a follow-up, the same way `archived_at`'s own docstring
    scopes archiving to "this list" rather than claiming a feature it
    does not build.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name="conversations")
    title = models.CharField(max_length=255, blank=True, default="")
    owner_kind = models.CharField(max_length=32, blank=True, default="")
    owner_key = models.CharField(max_length=200, blank=True, default="")
    archived_at = models.DateTimeField(null=True, blank=True, default=None)
    pinned_at = models.DateTimeField(null=True, blank=True, default=None)
    # NEW. Null = loose (today's behaviour, in every respect). Set = born
    # in that stream, FOR LIFE: no route writes this column after
    # creation, and `agents/visibility.py` exposes no setter (owner
    # decision 2). PROTECT, not CASCADE -- deleting a stream that still
    # holds conversations is refused and named (spec §8.1).
    workstream = models.ForeignKey("agents.Workstream", null=True, blank=True,
                                   on_delete=models.PROTECT,
                                   related_name="conversations")
    # NEW (WS-2). The `Turn.index` this conversation was last consolidated
    # THROUGH, and when. Null = never. Two columns rather than a
    # `Consolidation` row per run: only the LATEST matters -- a
    # re-consolidation overwrites the note (owner decision 6) -- and a
    # history table with one meaningful row is a history table nobody
    # reads.
    consolidated_through_index = models.PositiveIntegerField(null=True, blank=True)
    consolidated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["owner_kind", "owner_key"], name="agents_conv_owner"),
            # The scoped sidebar's exact ordering (spec §15.2), so a
            # stream's conversation list is one index scan rather than a
            # filter over the owner index.
            models.Index(fields=["workstream", "-updated_at"], name="agents_conv_ws"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.title or str(self.id)


class Workstream(models.Model):
    """A named scoped work area.

    OWNER COLUMNS, NOT A USER FK, matching every other owned row in this
    codebase (`Agent`, `Flow`, `Conversation`, `GenerationJob`,
    `AskRecord`): the open posture's single principal is not a `User`
    row, and `owned_rows_q` is the one predicate that already knows how
    to read these two columns in every posture.

    NO SLUG. A stream is addressed by integer pk in a URL and by name on
    a page. `Agent` and `Flow` carry slugs because a slug is how a
    declaration in `agents/defaults.py` and a tool key (`agent.<slug>`)
    name a row IN CODE; nothing names a stream in code, and an immutable
    slug on a user-renamed thing would be a second identity nobody edits.

    NO `enabled`. `Agent`/`Flow` carry one because a disabled agent must
    refuse a turn while staying visible to its operator; a stream has no
    such state -- `archived_at` covers "put it away".
    """

    class UploadPlacement(models.TextChoices):
        UNIVERSAL = "universal", "The universal library"
        CONTAINED = "contained", "This workstream only"

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    # Appended to the agent's system prompt for every turn in this
    # stream, as a clearly-labelled block (spec §11). TextField, not a
    # capped CharField: it is a prompt, and a cap is a truncation nobody
    # asked for.
    instructions = models.TextField(blank=True, default="")
    # "" = ASK EVERY TIME (owner decision 5). Not `null=True`: a blank
    # CharField with choices is the shape `owner_kind`/`media_type`
    # already use for "not set", and a nullable choice column would give
    # one column two spellings of empty.
    default_upload_placement = models.CharField(
        max_length=16, choices=UploadPlacement.choices, blank=True, default="")
    # ROUND 17 (owner, verbatim: "we should have a bool in settings to
    # use all rag documents ... in settings to toggle on/off the
    # inclusion of the rag documents in the system and default it on").
    # DEFAULT TRUE so every existing stream's corpus is BYTE-IDENTICAL
    # to what it was before this column existed (`tools.rag.workstreams.
    # stream_documents`'s own docstring, and `tools.rag.retrieval.
    # _visibility_filters`, are the two readers -- both treat False as
    # "drop the (universal ∩ wall) leg from the corpus entirely", never
    # merely "narrow it further"). Pinned + contained + the round-12
    # conversation leg (chat attachments) are UNCHANGED by this column
    # either way -- the owner's own round-13 ruling on guaranteed
    # chat-scoped availability outranks a library-wide convenience
    # toggle, and pinning/containing are deliberate per-document acts
    # this toggle was never asked to undo.
    include_universal = models.BooleanField(default=True)
    owner_kind = models.CharField(max_length=32, blank=True, default="")
    owner_key = models.CharField(max_length=200, blank=True, default="")
    archived_at = models.DateTimeField(null=True, blank=True, default=None)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            # CASE-INSENSITIVELY UNIQUE PER OWNER, not globally: two people
            # on one box may each have a stream called "Taxes", and a
            # global unique would make the second person's stream
            # unnameable for a reason no page could explain. `Lower(...)`
            # matches `uniq_agent_slug_ci` and `uniq_category_name_ci`.
            models.UniqueConstraint(Lower("name"), "owner_kind", "owner_key",
                                    name="uniq_workstream_name_ci_per_owner"),
        ]
        indexes = [
            models.Index(fields=["owner_kind", "owner_key"], name="agents_ws_owner"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class WorkstreamScopeEntitlement(models.Model):
    """One entitlement in a stream's WALL (owner decision 3a).

    NO ROWS = NO NARROWING, which is what makes an unwalled stream cost
    exactly what a loose conversation costs, and what makes every
    existing behaviour the default. Present rows narrow, and they narrow
    by INTERSECTION with the reader's own grants -- never by union
    (spec §6.1).

    The same table shape as `DocumentEntitlement`, `ToolEntitlement`,
    `AgentEntitlement`, `FlowEntitlement` and `ModelSetEntitlement`
    before it, deliberately: a sixth spelling of "this row carries
    entitlement ids" would be a sixth thing to keep in agreement.

    THE REVERSE ACCESSOR IS `scope_entitlements`, NOT `entitlement_labels`
    (author decision 4). The four existing label tables all name theirs
    `entitlement_labels` precisely so `agents.visibility.
    label_permitted_q` can be one function for two models -- and a wall
    is not a label. A label says "holders of this may reach this row"; a
    wall says "narrow this row's reach to this". Reusing the accessor
    would let a future `label_permitted_q` call compile against a stream
    and answer the wrong question silently.
    """

    workstream = models.ForeignKey(Workstream, on_delete=models.CASCADE,
                                   related_name="scope_entitlements")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="workstream_scopes")
    # PROVENANCE, written and not read by anything this phase ships --
    # deliberately, and exactly as `DocumentEntitlement.labelled_by` and
    # `Share.shared_by` already are. "Who narrowed this stream, and when"
    # is the question an operator asks of a wall they did not set, and a
    # column that is cheap to write now cannot be back-filled later.
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="+")
    set_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workstream", "entitlement"],
                                               name="uniq_workstream_scope")]
        indexes = [models.Index(fields=["entitlement"], name="agents_wsscope_ent")]


class ConversationTaint(models.Model):
    """One entitlement whose labelled material retrieval has actually
    returned into this conversation (owner decision 3b).

    ADDITIVE ONLY in v1: rows are created, never deleted, except by the
    conversation's own CASCADE and by the entitlement cascade (spec
    §8.4). Removal -- "this conversation no longer contains E" -- is a
    deferred item with a real design question behind it (spec §22).

    `first_turn` is THE CAUSING TURN, kept as a plain integer, not a
    ForeignKey: it is the evidence a person needs when they ask why a
    share went dormant, and a real FK would make deleting a turn delete
    the record of what that turn brought in.

    IT HANGS OFF THE CONVERSATION, NOT THE STREAM (author decision 9), so
    a loose conversation accumulates tags exactly the same way. Nothing
    reads them in v1 -- a loose conversation's share is IA-2's and this
    phase does not change it -- and the rows are what makes
    conversation-level share gating a later change with no back-fill.
    """

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE,
                                     related_name="taint_tags")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="conversation_taints")
    first_turn = models.BigIntegerField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conversation", "entitlement"],
                                               name="uniq_conversation_taint")]
        indexes = [models.Index(fields=["entitlement"], name="agents_convtaint_ent")]


class WorkstreamTaint(models.Model):
    """The union of its conversations' tags, MATERIALISED (owner decision
    3b, author decision 26).

    Materialised, not derived, and that is THE DECISION rather than an
    optimisation. The read-time share gate (spec §12.2) runs on every
    non-owner view of a shared stream; deriving the union would mean a
    join across every conversation in the stream on every one of those
    reads and -- worse -- the read-time gate would be answering from a
    DIFFERENT QUERY than the share-time gate did, which is how two gates
    come to disagree about one fact. ONE WRITER (spec §7.3), in the same
    transaction as the conversation row it follows.

    TWO TABLES, NOT ONE WITH A NULLABLE PAIR OF PARENTS. They answer two
    questions with different readers: the conversation's tags are what a
    note document inherits and what the per-conversation display shows;
    the stream's tags are what both share gates read. One table with
    `conversation` XOR `workstream` would need the XOR check constraint,
    two partial uniques and a branch at every read, to save one migration
    operation.
    """

    workstream = models.ForeignKey(Workstream, on_delete=models.CASCADE,
                                   related_name="taint_tags")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="workstream_taints")
    first_conversation = models.UUIDField(null=True, blank=True)
    first_turn = models.BigIntegerField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workstream", "entitlement"],
                                               name="uniq_workstream_taint")]
        indexes = [models.Index(fields=["entitlement"], name="agents_wstaint_ent")]


class ToolInvocation(models.Model):
    """One tool call, as an audit record (2026-08-27 addendum,
    consequence 3).

    Its own table, referenced BY `Turn`, never a set of columns ON it:
    an external MCP `tools/call` has no conversation and no turn, and
    must land in this same table unchanged. `principal_kind` /
    `principal_key` are `identity.contracts.principals.Principal`'s two
    fields, stored flat -- a principal is two strings, and joining a
    table to read them would buy nothing.

    `outcome` is a CLASS, not a message: five values, closed, and the
    one thing every later report (a per-principal rate limit, a failure
    dashboard, an MCP error mapping) can be built on without parsing
    prose.

    TWO FIELDS DECIDE HOW A ROW READS, AND `outcome` IS ONLY ONE OF
    THEM. This row is created BEFORE its runner runs, with
    `outcome=ERROR` as a placeholder `invoke_tool._finish` overwrites.
    `finished_at IS NULL` therefore means "still running", whatever
    `outcome` currently says, and every reader goes through
    `agents.runtime.audit.invocation_state` rather than reading
    `outcome` directly. `text` is likewise blank on every failure path
    (`_finish` writes it only when a `ToolResult` came back), so the
    words live in `error`.
    """

    class Outcome(models.TextChoices):
        OK = "ok", "OK"
        REFUSED = "refused", "Refused"
        PARAM_ERROR = "param_error", "Bad arguments"
        ERROR = "error", "Error"
        DEGRADED = "degraded", "Degraded"

    principal_kind = models.CharField(max_length=32)
    principal_key = models.CharField(max_length=255)
    # WHOSE TOOL DECLARATION WAS IN FORCE, as distinct from
    # `principal_kind`/`principal_key`, which is WHO THIS WAS DONE FOR.
    # Written by both `agents.runtime.invoke.invoke_tool` (a real call)
    # and `::invoke_unknown_tool` (a hallucinated wire name -- the
    # agent's own declaration was still in force even though the tool
    # it named does not exist), both from `ToolContext.agent_slug`.
    # Blank for a call with no agent at all -- the future MCP edge --
    # and for every row written before IA-2.
    agent_slug = models.CharField(max_length=64, blank=True, default="")
    tool_key = models.CharField(max_length=255, db_index=True)
    args = models.JSONField(default=dict, blank=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    # What the runner handed back as the tool message -- the only part a
    # model ever saw. Blank on a refusal or an error.
    text = models.TextField(blank=True, default="")
    # `str(exc)`, never a traceback.
    error = models.TextField(blank=True, default="")
    # The queue job this invocation's own tool call enqueued, if any.
    # Declared here because this phase has ONE migration for the whole
    # `agents` app; a later part of the runtime is what actually writes
    # to and reads this field. NOT a ForeignKey, for
    # the same reason `Turn.queue_job_id` is not one: `agents/` may not
    # import `models.queue` (import-law rule 2), exactly as
    # `tools/vision/models.py:135-143` records for
    # `GenerationJob.queue_job_id`.
    queue_job_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["principal_kind", "principal_key"],
                         name="agents_inv_principal"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.tool_key} -> {self.outcome}"

    @property
    def duration_ms(self) -> int | None:
        """Milliseconds from start to finish, or `None` while the call is
        still running. A property, not a column: it is derivable, and a
        stored copy is one more thing that can disagree with its own
        inputs."""
        if self.finished_at is None or self.started_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds() * 1000)


class Turn(models.Model):
    """One entry in a conversation.

    Only an ASSISTANT turn ever holds a non-`done` `state`; USER and
    TOOL turns are written after the fact and are always `done`.
    """

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        TOOL = "tool", "Tool"
        SYSTEM = "system", "System"

    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE,
                                     related_name="turns")
    index = models.PositiveIntegerField()
    role = models.CharField(max_length=16, choices=Role.choices)
    text = models.TextField(blank=True, default="")
    # On a TOOL turn; null otherwise. FIVE keys, all JSON-safe, all
    # always present (spec section 7.3):
    #   "tool"      -- the ToolSpec KEY that ran ("rag.search"), dotted,
    #                  never the wire name. The wire name is derivable;
    #                  the key is what every other layer indexes by.
    #   "args"      -- the VALIDATED args dict (`validate_tool_args`
    #                  output) on an ok/refused/error/degraded outcome;
    #                  the RAW dict the model emitted on a param_error,
    #                  because validation is precisely what did not
    #                  happen there and there is no validated form to
    #                  record. Replayed into a later prompt either way,
    #                  which is why it must be what actually ran.
    #   "agent"     -- the Agent.slug whose loop issued the call. Differs
    #                  from the conversation's agent on a delegated turn.
    #   "id"        -- the model-supplied tool_call_id, or "" when the
    #                  engine supplied none. RESERVED, not load-bearing
    #                  today: the installed Ollama integration supplies
    #                  none at all (see `agents/runtime/prompt.py`).
    #   "discarded" -- [{"tool": ..., "args": ...}, ...] for every call in
    #                  the same response that was NOT run. An empty list
    #                  in the ordinary single-call case, NEVER omitted: a
    #                  missing key and an empty list must not both mean
    #                  "nothing was discarded".
    tool_call = models.JSONField(null=True, blank=True)
    # `ToolResult.data` on a TOOL turn (RAG citations live in
    # data["citations"]). On an ASSISTANT turn (H5 review round 2,
    # finding 1): `{"appended_tool_result": [...]}`, one entry per raw
    # tool result `agents.runtime.loop._honest_ending`/`_two_failures_
    # text` baked into this turn's own `text` -- `"tool"` (the dotted
    # tool key), `"text"` (the exact raw substring), `"offset"`/`"length"`
    # (H5 review round 3, finding 2: that substring's own start index and
    # length within `text`, computed once by `loop.py` at the moment it
    # composes `text`, never re-found by searching a copy of it later),
    # and, only when the entry's own tool key is `flow.run`, `"steps"`
    # (that flow's own `data["steps"]`, needed to classify it by its last
    # step). `agents.runtime.prompt`'s history replay uses the recorded
    # offset/length to fence exactly that portion later -- after checking
    # `text[offset:offset + length]` still equals that entry's own
    # recorded `"text"`, falling back to leaving that one entry's portion
    # alone otherwise -- without `text` itself ever carrying fence
    # scaffolding a human reader would see. Null on every USER/SYSTEM
    # turn, and on an ASSISTANT turn that never baked a raw tool result
    # into its own text at all.
    data = models.JSONField(null=True, blank=True)
    artifacts = models.JSONField(default=list, blank=True)   # ["output:12", "document:7"]
    depth = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=16, choices=State.choices, default=State.DONE)
    error = models.TextField(blank=True, default="")
    # The audit row for this turn's tool call (2026-08-27 addendum,
    # consequence 3). SET_NULL, not CASCADE: losing an audit row must
    # never delete the conversation turn that referenced it.
    invocation = models.ForeignKey(ToolInvocation, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="turns")
    # The `agent.turn` job that produced (or is producing) this turn. NOT
    # a ForeignKey: `agents/` may not import `models.queue` (import-law
    # rule 2), exactly the reasoning `tools/vision/models.py:135-143`
    # records for `GenerationJob.queue_job_id`.
    queue_job_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    # C-1: WHO WROTE THIS. A use-level share recipient and a workstream
    # share recipient may both post (`agents.visibility.may_post_to`), and
    # before this column the thread could not answer "whose text is this"
    # for the page, for the prompt, or for an audit after the fact.
    # NULLABLE: every row that predates the column has no answer, and an
    # open-posture box has no user to record -- `None` means "not
    # attributable", never "the platform".
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="authored_turns")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["index"]
        constraints = [
            models.UniqueConstraint(fields=["conversation", "index"], name="uniq_turn_index"),
        ]
        indexes = [
            models.Index(fields=["conversation", "index"], name="agents_turn_thread"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.conversation_id}#{self.index} {self.role}"

    @classmethod
    def next_index(cls, conversation) -> int:
        """The next free index in `conversation`.

        Read-then-write, and that is safe here rather than lucky: a
        conversation's turns are written by exactly one `agent.turn` job
        at a time (a turn is enqueued only after the previous one
        finished), and the `uniq_turn_index` constraint above turns a
        real race into an honest `IntegrityError` rather than a silently
        reordered conversation.
        """
        last = cls.objects.filter(conversation=conversation).order_by("-index").first()
        return 0 if last is None else last.index + 1


class ToolEntitlement(models.Model):
    """A tool key, labelled with an entitlement.

    UNLABELLED MEANS CALLABLE. A tool with no row here is callable by
    every signed-in principal; a labelled one is callable by a principal
    holding ANY of its entitlements. That is the same OR-match documents
    use, and for the same reason: "must hold both" is a named non-goal,
    and the answer to it is a more specific entitlement.

    `tool_key` IS A STRING, NOT A FOREIGN KEY: tools are code-registered
    (`agents.contracts.tools.register_tool`) and there is no tool table
    to point at -- the same reason `Turn.queue_job_id` is a plain
    integer. A row naming a key that is not registered on THIS install
    (a feature-gated tool with its flag off) is tolerated exactly as
    `Agent.tool_keys` tolerates one.
    """

    tool_key = models.CharField(max_length=255)
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="tool_labels")
    labelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    labelled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tool_key", "entitlement"],
                                    name="uniq_tool_entitlement"),
        ]
        indexes = [models.Index(fields=["tool_key"], name="agents_toollabel_key")]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.tool_key}@{self.entitlement_id}"


class AgentEntitlement(models.Model):
    """An agent's label -- beside the thing it protects.

    UNLABELLED = every signed-in principal, which is today's behaviour and
    which is what keeps a box that never labels an agent unchanged.
    LABELLED = holders of any of its entitlements, OR-matched.
    """

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE,
                              related_name="entitlement_labels")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="agent_labels")
    labelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    labelled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["agent", "entitlement"],
                                               name="uniq_agent_entitlement")]
        indexes = [models.Index(fields=["entitlement"], name="agents_agentlabel_ent")]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.agent_id}@{self.entitlement_id}"


class FlowEntitlement(models.Model):
    """The same, for a flow.

    A SEPARATE TABLE rather than one polymorphic label row: `Agent` and
    `Flow` are two models with two primary keys, and a `target_type`/
    `target_key` pair here would be `Share`'s shape solving a problem
    `Share` has and this does not -- these two labels are read by two
    functions that each already know which model they are filtering.
    """

    flow = models.ForeignKey(Flow, on_delete=models.CASCADE,
                             related_name="entitlement_labels")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="flow_labels")
    labelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    labelled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["flow", "entitlement"],
                                               name="uniq_flow_entitlement")]
        indexes = [models.Index(fields=["entitlement"], name="agents_flowlabel_ent")]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.flow_id}@{self.entitlement_id}"


def _uuid_or_none(raw: str):
    """A UUID, or None. NEVER raises -- see `Share`'s docstring."""
    import uuid
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def _int_or_none(raw: str):
    """An int, or None. NEVER raises -- see `Share`'s docstring."""
    try:
        return int(str(raw))
    except (ValueError, TypeError):
        return None


class Share(models.Model):
    """One row this principal's owner has extended to somebody else.

    GENERIC TARGET, ONE TABLE, so adding the second sharing UI later is a
    form and a template rather than a second table and a second set of
    visibility functions. IA-2 ships exactly one UI -- conversations
    (spec section 10.3) -- and `vision_output` has no writer at all, so
    it cannot orphan.

    `target_key` IS THE TARGET'S PRIMARY KEY AS TEXT. The four targets
    live in three apps and have three different key types (UUID, int,
    int); a real foreign key would be a cross-column import (import-law
    rule 2) and a `GenericForeignKey` would make `contenttypes` a second
    identity for a row this platform already identifies by pk.

    THE KEY IS PARSED ON THE WAY IN AND AGAIN ON THE WAY OUT.
    `save()` refuses a `target_key` the target's own parser rejects;
    `agents/shares.py::shared_keys` drops one on the way out. Two halves
    of one rule, because a row can also arrive from a shell or from an
    older schema, and an unparseable key reaching `Q(pk__in=[...])`
    raises inside a listing queryset -- a 500 on a never-500 surface,
    reachable by one bad row.

    ORPHANS ARE INERT AND NO SIGNAL SWEEPS THEM. This repository uses no
    Django signals anywhere; every reader resolves the target first, and
    a target that does not resolve contributes nothing. The one shipped
    delete surface, `agents.visibility.delete_conversation`, removes a
    conversation's shares in the same transaction.
    """

    class Target(models.TextChoices):
        CONVERSATION = "conversation", "Conversation"
        AGENT = "agent", "Agent"
        FLOW = "flow", "Flow"
        VISION_OUTPUT = "vision_output", "Generated image"
        WORKSTREAM = "workstream", "Workstream"     # NEW

    class Level(models.TextChoices):
        VIEW = "view", "Can view"
        USE = "use", "Can use"

    target_type = models.CharField(max_length=16, choices=Target.choices)
    target_key = models.CharField(max_length=64)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.CASCADE, related_name="shares_received")
    group = models.ForeignKey("auth.Group", null=True, blank=True,
                              on_delete=models.CASCADE, related_name="shares_received")
    level = models.CharField(max_length=8, choices=Level.choices, default=Level.VIEW)
    shared_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="shares_made")
    shared_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(user__isnull=False, group__isnull=True)
                           | Q(user__isnull=True, group__isnull=False)),
                name="share_user_xor_group"),
            models.UniqueConstraint(fields=["target_type", "target_key", "user"],
                                    condition=Q(user__isnull=False),
                                    name="uniq_share_target_user"),
            models.UniqueConstraint(fields=["target_type", "target_key", "group"],
                                    condition=Q(group__isnull=False),
                                    name="uniq_share_target_group"),
        ]
        indexes = [
            models.Index(fields=["target_type", "target_key"], name="agents_share_target"),
            models.Index(fields=["user"], name="agents_share_user"),
            models.Index(fields=["group"], name="agents_share_group"),
        ]

    def save(self, *args, **kwargs):
        """Validate `target_key` against the TARGET'S OWN key shape.

        `ValueError`, not a `ValidationError`: a share row whose key
        cannot name a row is a programming error at the call site, not a
        form the operator can correct.
        """
        parser = TARGET_KEY_PARSERS.get(self.target_type)
        if parser is None:
            raise ValueError(f"{self.target_type!r} is not a share target on this platform.")
        if parser(self.target_key) is None:
            raise ValueError(
                f"{self.target_key!r} is not a valid key for a {self.target_type} share."
            )
        return super().save(*args, **kwargs)


# A conversation's primary key is a UUID; an agent's, a flow's and a
# generated image's are integers. Each parser RETURNS None rather than
# raising, so both halves of the rule -- `Share.save()` on the way in and
# `agents.shares.shared_keys` on the way out -- can ask the same question
# without either of them needing a try/except of its own.
TARGET_KEY_PARSERS = {
    Share.Target.CONVERSATION: _uuid_or_none,
    Share.Target.AGENT: _int_or_none,
    Share.Target.FLOW: _int_or_none,
    Share.Target.VISION_OUTPUT: _int_or_none,
    Share.Target.WORKSTREAM: _int_or_none,          # NEW
}


class ChatSettings(models.Model):
    """The chat column's operator-policy singleton -- one row, holding
    the settings that govern how a conversation's prompt is built.

    A DATABASE ROW, not an environment variable, for the identical
    reason `identity.models.IdentitySettings`' own docstring gives: the
    process that BUILDS a prompt is the `worker`, not `web`, and
    `compose.yaml` supplies those two environments independently -- so a
    variable an operator set on the page they were looking at could be
    unset in the process that actually reads it. The database is the one
    thing every process provably shares.

    ONE COLUMN TODAY, and the model exists rather than the column being
    hung on `IdentitySettings` (posture, session window, admin content
    access) or on `tools.rag.models.RagSettings` (retention, upload
    caps, retrieval): neither of those is about how a conversation's
    prompt is assembled, and a settings row is read by the code that
    owns the subject. `agents/` owns the prompt.
    """

    # OWNER, ROUND 21, verbatim: "can we inject the date/time into the
    # prompt so that its time aware ... This should also be configurable
    # in the settings but defaulted to on."
    #
    # ONE BOOLEAN GOVERNS THE WHOLE ENHANCEMENT, both halves of it: the
    # current-date line on the carrying turn's system message AND the
    # per-turn `[YYYY-MM-DD HH:MM]` prefix replayed history carries
    # (`agents.runtime.prompt`). Two switches for one feature would let
    # an operator land in a state the owner never asked for -- timestamps
    # on history with no statement of what "now" is, which is precisely
    # the confusion the toggle exists to remove.
    time_aware = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_solo(cls) -> "ChatSettings":
        """The one row (`pk=1`), created with defaults on first use.

        The SAME `get_or_create(pk=1)` shape `RagSettings.get_solo` and
        `IdentitySettings.get_solo` already use, and the same reason
        there is no seed migration with it: a box restored from a backup
        taken before this column existed behaves exactly like a fresh
        install -- time awareness ON, which is the owner's own default.
        """
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"ChatSettings(time_aware={self.time_aware})"
