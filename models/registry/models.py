"""
Django models for the console model registry (ARCHITECTURE.md §4 Config &
Secrets: "Posture profiles, model registry, a local secrets vault").

Three tables, one job each:

- `ModelConnection` -- a concrete, reachable engine+model an operator has
  registered (e.g. "ollama @ http://localhost:11434, a-chat-model:8b").
- `RoleBinding` -- which `ModelConnection` currently backs a given role key
  (e.g. "rag.answer"). Read by `models/registry/bindings.py::db_provider`,
  the first link in `models.contracts.bindings.resolve()`'s db -> env chain.
- `Materialization` -- the fingerprint a role was last *materialized*
  against (e.g. last re-embedded with), so drift between "what's bound now"
  and "what the data actually reflects" can be detected (see README.md's
  re-encode guard; the comparison itself lives in
  `models/registry/drift.py`).
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from foundation.format import bytes_to_gb
from django.db.models.functions import Lower


class ModelConnectionQuerySet(models.QuerySet):
    """The one ordering rule for every model picker, so a picker's sort
    order lives in exactly one place instead of being re-derived per view
    -- used by role-assignment dropdowns and the Ask-time picker alike,
    rather than a second copy of the rule.

    Ranked connections come first, lowest `rank` first; unranked
    connections (`rank` is null) sort after every ranked one. Ties -- same
    rank, or no rank at all -- break by `name`, case-insensitively.
    Duplicate ranks are allowed and never validated for uniqueness; this
    stable secondary sort is what keeps their relative order deterministic
    rather than arbitrary.
    """

    def picker_order(self) -> "ModelConnectionQuerySet":
        return self.order_by(models.F("rank").asc(nulls_last=True), Lower("name"))


class ModelConnection(models.Model):
    """A registered, reachable engine+model an operator can bind roles to.

    `name` is the operator-facing label (e.g. "workstation chat model"), kept
    case-insensitively unique the same way `tools.rag.models.Category`
    keeps category names unique -- so "Workstation" and "workstation" can't
    coexist as two different rows an operator would confuse for one.
    """

    objects = ModelConnectionQuerySet.as_manager()

    name = models.CharField(max_length=255)  # CI-unique -- see constraint below
    engine = models.CharField(max_length=64, default="ollama")
    endpoint = models.CharField(max_length=512)
    model_id = models.CharField(max_length=255)
    # List drawn from the capability vocabulary "chat" | "embeddings" |
    # "vision" (models.contracts.roles.CAPABILITIES), e.g. ["chat"] or
    # ["embeddings"]. A plain JSONField list rather than a through-table:
    # capability sets are small and read far more often than written.
    capabilities = models.JSONField(default=list, blank=True)
    # Positive-int-only is enforced at the view layer,
    # same as `context_window`/`rank` below -- a bad value
    # degrades to a clean form re-render rather than an IntegrityError. The
    # field type mirrors those two for the same reason: nothing about
    # "dimension" is ever meaningfully zero or negative.
    embed_dim = models.PositiveIntegerField(null=True, blank=True)
    # An operator's per-connection cap on the KV cache the engine is asked
    # to allocate (threaded to `models.contracts.engines.ollama.build_llm`'s
    # `context_window` cfg via `models.registry.bindings.db_provider`).
    # Null/blank means "unset" -- the adapter's own bounded default applies
    # (see that module's `DEFAULT_CONTEXT_WINDOW` for why one exists at
    # all). Positive-int-only is enforced at the view layer, not here, so a
    # bad value degrades to a clean form re-render rather than an IntegrityError.
    context_window = models.PositiveIntegerField(null=True, blank=True)
    # Engine-specific extras for THIS connection, handed to the engine's
    # builder as `**ResolvedModel.config` (D8). Null for the common
    # single-file case; a multi-file family (UNet + CLIPs + VAE) names its
    # companion files here without a schema change. Never a model
    # default -- only what an operator or a later UI puts in it.
    config = models.JSONField(null=True, blank=True)
    # Owner-directed, verbatim: "for us to self assign the
    # qualifiers of the models... define the describers for each of the
    # models and rank them so we can choose them". `descriptor` is a
    # single free-text line in the OPERATOR'S OWN WORDS -- the platform
    # never auto-fills, suggests, or derives it; there is no taxonomy here,
    # just whatever helps the operator recognize this connection at a
    # glance. `rank` is a plain positive integer an operator assigns by
    # hand; lower sorts first in every picker that orders connections (see
    # `ModelConnectionQuerySet.picker_order`, above). Both blank/null means
    # "no opinion" -- unranked connections still show up, just last, and an
    # undescribed connection just shows its bare name. Neither is ever set
    # by `machine_model_add` (detection-only registration; the operator
    # opts in to both, later, in the one edit home) or bound into
    # `models.contracts.bindings.ResolvedModel` -- they are console-only
    # metadata, never threaded into the engine adapters.
    descriptor = models.CharField(max_length=80, blank=True, default="")
    rank = models.PositiveIntegerField(null=True, blank=True)
    # The execution queue's per-connection memory facts (T2b): what this
    # connection's model actually costs to hold in memory, so the future
    # scheduler can decide what fits alongside what. Three nullable
    # columns, no data migration -- "unknown" is the honest starting state
    # for every existing row, not a guessed zero; the queue worker
    # self-heals it the first time it loads this connection's model.
    #
    # Last footprint the queue worker (`models/queue/worker.py`) actually
    # measured for this connection's model while it was loaded -- an
    # opportunistic observation, never written by an operator form. Null
    # means "never measured yet".
    measured_footprint_bytes = models.BigIntegerField(null=True, blank=True)
    # When `measured_footprint_bytes` was last written -- paired so a
    # measurement can be shown as dated rather than presented as current
    # forever. Null exactly when that field is.
    measured_footprint_at = models.DateTimeField(null=True, blank=True)
    # Operator-declared footprint (entered in GB on the edit form, stored
    # here in bytes -- see `connection_add`'s `round(gb * 1024**3)`, which
    # must match `_human_size`'s 1024 base so what the operator typed
    # renders back identically). Always wins over `measured_footprint_bytes`
    # (see `effective_footprint_bytes` below) -- an operator who knows
    # better than the last measurement has the final word. Null means "no
    # override": the measured value, if any, applies.
    footprint_override_bytes = models.BigIntegerField(null=True, blank=True)
    # THE THIRD RUNG (queue memory governance, 2026-09-21, ADR 0013's
    # dated amendment §4). The size an engine reports for a model it
    # says is LOADED, harvested from the residency snapshot the queue
    # worker is taking anyway (`models.queue.worker.Worker.
    # _residency_snapshot` reads `InstalledModel.loaded_size`) -- NEVER
    # a new HTTP call. It exists because those numbers were previously
    # read and thrown away, and because a model that has never completed
    # a run under this queue has no `measured_footprint_bytes` at all and
    # is therefore treated as unknown-footprint, i.e. exclusive, for ever.
    #
    # A SEPARATE COLUMN, not folded into `measured_footprint_bytes`: the
    # two are facts of different quality (ours-after-a-run vs the
    # engine's-while-loaded), the recorder rule compares a reading only
    # against its OWN standing value, and the console's label would be
    # untrue again the moment one column had to answer for both.
    engine_reported_footprint_bytes = models.BigIntegerField(null=True, blank=True)
    # When `engine_reported_footprint_bytes` was last written -- paired so
    # the console can date the reading, exactly as `measured_footprint_at`
    # dates its own.
    engine_reported_footprint_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def effective_footprint_bytes(self) -> int | None:
        """The footprint the scheduler should reserve for this
        connection's model, on a FOUR-rung ladder (spec §3.1):
        `footprint_override_bytes` (the operator's word, always wins),
        else `measured_footprint_bytes` (what we observed after a run
        this queue executed), else `engine_reported_footprint_bytes`
        (what the engine said a loaded copy occupied, harvested from a
        residency snapshot), else `None` -- "unknown", which
        `models.registry.bindings.footprint_for` documents as "this job
        runs alone".

        Walks on `is not None`, never on truthiness: a genuine zero is a
        value, and reading it as "unknown" would silently make a
        zero-cost model exclusive.
        """
        if self.footprint_override_bytes is not None:
            return self.footprint_override_bytes
        if self.measured_footprint_bytes is not None:
            return self.measured_footprint_bytes
        return self.engine_reported_footprint_bytes

    @property
    def footprint_source(self) -> str | None:
        """Which rung backs `effective_footprint_bytes` -- one of
        `"override"` / `"measured"` / `"engine_reported"` / `None`, keyed
        EXACTLY to `models.registry.views._FOOTPRINT_SOURCE_LABELS`.

        NOT keyed to `_SOURCE_LABELS`, which is and stays the label map
        for `DiscoveryRow.capability_source` (where "detected from the
        model server" is true of a capability probe and would be a lie
        about a post-run delta measurement). The two vocabularies are
        deliberately separate -- see that module and spec §3.1.
        """
        if self.footprint_override_bytes is not None:
            return "override"
        if self.measured_footprint_bytes is not None:
            return "measured"
        if self.engine_reported_footprint_bytes is not None:
            return "engine_reported"
        return None

    @property
    def footprint_override_gb(self) -> str:
        """`footprint_override_bytes` converted to GB, one decimal, for the
        edit form's round-trip prefill (`_connection_edit.html`'s
        `footprint_gb` field) -- empty string when unset, the same
        blank-when-unset idiom every other optional field's template value
        uses (`embed_dim|default_if_none:''` etc.). `foundation.format.bytes_to_gb`
        (T10) mirrors `_human_size`'s base exactly, so a value the operator
        typed (e.g. 8.5) renders back unchanged after `connection_add`
        stores `foundation.format.gb_to_bytes(gb)`.
        """
        if self.footprint_override_bytes is None:
            return ""
        return f"{bytes_to_gb(self.footprint_override_bytes):.1f}"

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="uniq_modelconnection_name_ci"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class RoleBinding(models.Model):
    """Which `ModelConnection` currently backs a role key (e.g. "rag.answer").

    `role_key` is CI-unique for the same reason `ModelConnection.name` is --
    one binding per role, regardless of casing. `connection` is nullable
    with `SET_NULL` so deleting a `ModelConnection` un-binds the role
    (falling back through `resolve()` to `env_provider`) rather than
    deleting the `RoleBinding` row or blocking the delete.
    """

    role_key = models.CharField(max_length=255)  # CI-unique -- see constraint below
    connection = models.ForeignKey(
        ModelConnection, null=True, blank=True, on_delete=models.SET_NULL, related_name="role_bindings"
    )

    class Meta:
        ordering = ["role_key"]
        constraints = [
            models.UniqueConstraint(Lower("role_key"), name="uniq_rolebinding_role_key_ci"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.role_key


class Materialization(models.Model):
    """The fingerprint a role was last materialized (e.g. re-embedded)
    against -- the re-encode guard's bookkeeping (see README.md).

    Compared against the role's *current* active fingerprint
    (`resolve(role).fingerprint`) to detect drift: a role whose binding has
    moved to a different model/dimension since the last materialization
    needs re-materializing before its data can be trusted. No row for a
    role means "never materialized", not "drifted" -- that judgment call
    lives in `models/registry/drift.py`, not here.
    """

    role_key = models.CharField(max_length=255, unique=True)
    fingerprint = models.CharField(max_length=255)
    materialized_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.role_key}@{self.fingerprint}"


class ModelSet(models.Model):
    """A named group of model connections -- the unit an entitlement
    attaches to.

    CI-UNIQUE BY NAME, like `Entitlement`, `Category` and
    `ModelConnection` before it: two sets that read identically on an
    attach form are two rows somebody will attach the wrong one of.

    A set is NOT an entitlement and NOT a capability. It answers "which
    models is this", once, so that "who may use them" can be answered
    somewhere else and changed without touching it.
    """

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="model_sets_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"),
                                               name="uniq_modelset_name_ci")]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class ModelSetMember(models.Model):
    """One connection's membership of one set. The edge that maps models
    into sets, and the one an operator edits when a new model arrives."""

    model_set = models.ForeignKey(ModelSet, on_delete=models.CASCADE,
                                  related_name="members")
    connection = models.ForeignKey(ModelConnection, on_delete=models.CASCADE,
                                   related_name="set_memberships")
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="+")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["model_set", "connection"],
                                               name="uniq_modelset_member")]
        indexes = [models.Index(fields=["connection"], name="inference_setmember_conn")]


class ModelSetEntitlement(models.Model):
    """One entitlement's attachment to one set -- the LABEL row, moved up
    a level from the connection to the set.

    The foreign key to the entitlement is a STRING, exactly as
    `DocumentEntitlement`'s and `ToolEntitlement`'s are, which is what
    keeps `identity/` importable-from rather than importing (rule 4).
    """

    model_set = models.ForeignKey(ModelSet, on_delete=models.CASCADE,
                                  related_name="entitlement_attachments")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="model_set_attachments")
    attached_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    attached_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["model_set", "entitlement"],
                                               name="uniq_modelset_entitlement")]
        indexes = [models.Index(fields=["entitlement"], name="inference_setent_ent")]
