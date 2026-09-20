from __future__ import annotations

from django.apps import AppConfig


class InferenceConfig(AppConfig):
    """The operator-facing model registry -- see models/registry/README.md.

    A `models/` column app, not a tool: it implements the §4 Config &
    Secrets model registry as a DB-backed binding provider for
    models/contracts/bindings.py, plus the ACCESS console UI for managing it.
    Owns no ROLES of its own (roles are a tool concern), but DOES own
    one job KIND -- `rag.reencode` (T8) -- registered below: see
    `models/registry/jobs.py`'s module docstring for why a job whose
    work is fundamentally registry-owned (it stamps
    `models.registry.models.Materialization`) is registered from here
    rather than from the `tools/rag` app whose role it re-encodes.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "models.registry"
    label = "inference"

    def ready(self) -> None:
        """Register the `rag.reencode` job kind at app startup.

        Imports `jobkinds`/`models.registry.jobs` locally, matching
        `tools.rag.apps.RagConfig.ready()`'s own convention -- no DB, no
        heavy imports at startup; `planner`/`handler`/`summarizer` are
        resolved lazily (dotted-path strings), only when a job is actually
        enqueued/run, never at registration time.
        """
        from models.contracts.jobkinds import JobKind, register_job_kind

        register_job_kind(
            JobKind(
                key="rag.reencode",
                label="Re-encode embeddings",
                planner="models.registry.jobs.plan_reencode",
                handler="models.registry.jobs.run_reencode",
                summarizer="models.registry.jobs.summarize_reencode",
                # Background maintenance work queues behind interactive
                # asks (owner's per-kind priority ruling) -- `rag.ask`
                # registers no `default_priority` at all (falls through to
                # `JobSettings.default_priority`, 100 out of the box), so
                # this kind's higher number keeps it strictly lower
                # priority than an ask by default, without hard-coding a
                # dependency on that other kind's own number.
                default_priority=200,
            )
        )

        # Tool registration (spec section 4.3, ruling R2). Imports the
        # pure contracts plus this app's own `tools` module, which itself
        # imports nothing heavy at module scope -- so this method's
        # no-DB-no-heavy-imports promise (see the docstring above) holds.
        from agents.contracts.tools import register_tool
        from models.registry.tools import MODELS_STATUS

        register_tool(MODELS_STATUS)

        # This column's answer to "an entitlement is being deleted", as a
        # DOTTED-PATH STRING so `identity/` can run it without importing
        # `models/` (import-law rule 4). Registration imports nothing:
        # `identity/cascades.py` resolves the path at delete time, the
        # same way a `JobKind`'s handler is resolved at run time. It
        # detaches the entitlement from every model SET; the sets and
        # their memberships survive (spec sections 6.10/22.31/22.33, the
        # 2026-08-30 owner directives).
        from identity.contracts.cascades import (
            EntitlementCascade, register_entitlement_cascade,
        )

        register_entitlement_cascade(EntitlementCascade(
            key="inference.model_sets",
            label="Model set attachments",
            handler="models.registry.labels.model_set_cascade",
        ))

        # THE OTHER DIRECTION ON THE SAME TABLE. The cascade above
        # answers "this entitlement is going away"; this answers "which
        # sets carry it, and change that" -- the question an operator
        # asks from the entitlement's own page. Same dotted-path
        # mechanism, same import-law reason, same idempotent `ready()`;
        # `models/registry/axes.py` keeps every write on this side going
        # through `labels.attach`/`labels.detach`, which audit
        # themselves.
        from identity.contracts.axes import EntitlementAxis, register_entitlement_axis

        register_entitlement_axis(EntitlementAxis(
            key="inference.model_sets", label="Model sets", order=40,
            counts="models.registry.axes.model_set_counts",
            rows="models.registry.axes.model_set_rows",
            ids_for="models.registry.axes.model_set_ids_for",
            set_for="models.registry.axes.set_model_set_axis",
        ))

        # The availability cache's invalidation (UI-1). SENDERS AS
        # STRINGS, so this method still imports no model module -- Django
        # resolves `"inference.RoleBinding"` lazily, which keeps the
        # no-DB-no-heavy-imports promise the docstring above makes.
        #
        # `ModelConnection`'s `post_delete` is in the set for a reason
        # that is easy to miss: `RoleBinding.connection` is
        # `on_delete=SET_NULL`, and Django clears it with a bulk UPDATE
        # that emits no `post_save` at all -- see
        # `models.registry.availability.invalidate_after_commit`.
        #
        # `dispatch_uid` on each, so `ready()` running twice (it can, in
        # some management-command paths) connects one receiver, not two.
        from django.db.models.signals import post_delete, post_save

        from models.registry.availability import invalidate_after_commit

        post_save.connect(
            invalidate_after_commit, sender="inference.RoleBinding",
            dispatch_uid="inference.availability.rolebinding.saved",
        )
        post_delete.connect(
            invalidate_after_commit, sender="inference.RoleBinding",
            dispatch_uid="inference.availability.rolebinding.deleted",
        )
        post_delete.connect(
            invalidate_after_commit, sender="inference.ModelConnection",
            dispatch_uid="inference.availability.modelconnection.deleted",
        )

        # The probe cache's invalidation (C-07, half A) -- SAME strings-
        # as-senders convention as the availability wiring just above, and
        # the same reasoning, but FOUR receivers rather than three:
        # mirroring `availability`'s set exactly would leave
        # `ModelConnection`'s `post_save` unconnected, and
        # `models.registry.probe_cache.invalidate_after_commit`'s own
        # docstring explains why that one is required here even though
        # `availability` has no use for it (creating a connection binds no
        # role, but `connection_add`/`machine_model_add` DO create one and
        # redirect straight back into a fresh `_build_context`).
        from models.registry.probe_cache import (
            invalidate_after_commit as invalidate_probe_cache,
        )

        for signal, sender, uid in (
            (post_save, "inference.RoleBinding", "inference.probe_cache.rolebinding.saved"),
            (post_delete, "inference.RoleBinding", "inference.probe_cache.rolebinding.deleted"),
            (post_save, "inference.ModelConnection", "inference.probe_cache.modelconnection.saved"),
            (post_delete, "inference.ModelConnection", "inference.probe_cache.modelconnection.deleted"),
        ):
            signal.connect(invalidate_probe_cache, sender=sender, dispatch_uid=uid)
