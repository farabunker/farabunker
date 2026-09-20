"""The five tables identity owns: who exists, what posture this box is
in, what has been done to it, the named permission everything in IA-2
is granted against, and who holds it.

`User`, `IdentitySettings` and `AuditEvent` are IA-1's: a complete,
shippable posture on its own (a household box with three accounts and
private conversations), not half of a feature. `Entitlement` and
`EntitlementGrant` are IA-2's addition -- the permission and the grant,
and nothing else. Document labels and tool/agent/flow shares still live
in their OWN columns' tables (`tools/rag`, `agents`), each pointing back
at `Entitlement` by string FK: identity answers "which entitlement ids",
never "which documents" or "which rows are shared".
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from identity.contracts.actions import AUDIT_ACTIONS, SOURCE_CHOICES, SOURCE_WEB
from identity.contracts.postures import (
    LIBRARY_CHOICES, LIBRARY_OPEN, POSTURE_CHOICES, POSTURE_OPEN,
    SESSION_IDLE_MINUTES_DEFAULT,
)


class User(AbstractUser):
    """The platform's user model.

    NO EXTRA FIELDS, deliberately. It exists NOW because
    `AUTH_USER_MODEL` cannot be changed once rows reference it, and the
    cost of adding a field later to a model we own is one migration --
    while the cost of swapping the model later is database surgery
    nobody wants to perform on a box holding a document library.

    Django's own `identity_user_groups` and `identity_user_user_
    permissions` join tables come with `AbstractUser`'s inherited M2M
    fields. The permissions one is NEVER written to: Django's
    `Permission` model is a named non-goal (owner decision 16), because
    it is model-level and would be a second grant mechanism beside the
    entitlements IA-2 adds.
    """


class IdentitySettings(models.Model):
    """The posture singleton -- the ONE answer to "what posture is this
    box in".

    A DATABASE ROW, not a setting (spec section 3.2). `compose.yaml`
    starts `web`, `worker` and `watcher` as three processes with
    independently supplied environments, so an environment variable
    governing whether permissions are enforced could be set on one and
    unset on another -- and the worker is where turns actually run
    tools. The database is the one thing all three provably share. It
    also makes the posture VALIDATABLE against database facts: switching
    away from `open` is refused unless an active superuser exists
    (`identity.services.set_posture`), and an environment variable
    cannot be refused.

    Read once per request through `get_solo()` -- one primary-key read
    on a connection Django already holds open (`CONN_MAX_AGE=600`). NO
    CACHE LAYER: a cache would be a second truth with a staleness
    window, and the staleness window of a security posture is exactly
    the interval in which the box is wrong.
    """

    posture = models.CharField(max_length=16, choices=POSTURE_CHOICES,
                               default=POSTURE_OPEN)
    # Whether documents carrying NO label are readable by everyone
    # signed in. STORED AND EDITABLE IN IA-1, CONSULTED BY NOTHING until
    # IA-2 gives labels a body -- the column lands now so the posture
    # page is written once, and `set_posture`'s reset-to-open rule (see
    # `identity.services`) needs no data migration later.
    library_posture = models.CharField(max_length=16, choices=LIBRARY_CHOICES,
                                       default=LIBRARY_OPEN)
    # Whether an ADMINISTRATOR may read other people's CONTENT -- their
    # conversations, Ask history, generated images, agent and flow
    # bodies, document bytes, and the payload/answer text of jobs they
    # did not start.
    #
    # DEFAULT FALSE, AND THAT DEFAULT IS THE DECISION. Administering is
    # not reading: an admin already sees every ROW they need to run the
    # box -- a job's kind, owner, state and progress; a document's
    # title, category and status -- without this. Turning it on is a
    # deliberate act on the posture page, audited exactly like the
    # posture, and read per request so it takes effect without a
    # restart.
    admin_sees_content = models.BooleanField(default=False)
    # A ROLLING idle timeout, applied per request by
    # `IdentityGateMiddleware` via `request.session.set_expiry(...)`.
    # Zero means "expire when the browser closes", which is why one
    # column covers both behaviours.
    session_idle_minutes = models.PositiveIntegerField(
        default=SESSION_IDLE_MINUTES_DEFAULT)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_solo(cls) -> "IdentitySettings":
        """The one row (`pk=1`), creating it with defaults on first use.

        Never raises `DoesNotExist` -- the same pattern
        `tools.rag.models.RagSettings.get_solo` established, and the
        reason there is no seed migration: a backup restored from before
        this phase behaves identically to a fresh install.
        """
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"IdentitySettings(posture={self.posture})"


class AuditEvent(models.Model):
    """One row per thing somebody did TO this box. Append-only.

    NO FOREIGN KEY TO `User`, deliberately: two strings and a label,
    exactly like `ToolInvocation`'s principal columns. An audit row a
    cascade could delete is not an audit row -- and it also means
    `identity/0001_initial.py` needs no `swappable_dependency` and can
    create this table alongside the user model in one migration.

    APPEND-ONLY IS A CODE-LEVEL PROPERTY, NOT A DATABASE ONE, and this
    docstring says so rather than letting `save()` imply otherwise. A
    queryset `.update()` or `.delete()` never calls `save()`, so the
    real guard is the AST sweep in `foundation/ops/tests/
    test_column_boundaries.py`: no module outside `identity/audit.py`
    may touch `AuditEvent.objects` at all. Making the table append-only
    in Postgres would need a rule or a trigger -- a second enforcement
    mechanism outside the application -- and is out of scope.
    """

    actor_kind = models.CharField(max_length=32)
    actor_key = models.CharField(max_length=255)
    # The actor's DISPLAY NAME at the time of the event. Denormalised on
    # purpose: an audit line must still read correctly after the account
    # is renamed or deactivated, and a join that resolves a deleted pk
    # to "unknown" is an audit trail that forgets.
    actor_label = models.CharField(max_length=255, blank=True, default="")
    action = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=64, blank=True, default="")
    target_key = models.CharField(max_length=255, blank=True, default="")
    target_label = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_WEB)
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-at"]
        indexes = [
            models.Index(fields=["actor_kind", "actor_key"], name="identity_audit_actor"),
            models.Index(fields=["target_type", "target_key"], name="identity_audit_target"),
        ]

    def save(self, *args, **kwargs):
        """Append-only, and a closed action vocabulary.

        `ValueError`, not a `ValidationError`: this is a programming
        error at the call site, not a form the operator can correct.
        """
        if self.pk is not None:
            raise ValueError(
                "AuditEvent rows are append-only; this row already exists "
                f"(pk={self.pk}, action={self.action!r})."
            )
        if self.action not in AUDIT_ACTIONS:
            raise ValueError(
                f"Unknown audit action {self.action!r}. Add it to "
                f"identity/contracts/actions.py::AUDIT_ACTIONS first."
            )
        return super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"AuditEvent({self.at:%Y-%m-%d %H:%M} · {self.action})"


class Entitlement(models.Model):
    """A named permission, granted to users and groups, spent on
    documents, tools and nothing else.

    ENTITLEMENTS PARTITION ONE LIBRARY AMONG THE PEOPLE WHO SHARE ONE
    MACHINE. They do not partition machines: nothing here is
    multi-tenant, and a second box is a second box.

    Case-insensitively unique by name, the same house convention
    `agents.models.Agent`'s `uniq_agent_slug_ci` and
    `models.registry.models.ModelConnection`'s `uniq_modelconnection_
    name_ci` already use -- "Finance" and "finance" are one entitlement,
    because two rows that read identically on a grant form are two rows
    somebody will grant the wrong one of.
    """

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    # SET_NULL, NOT CASCADE: deleting a user must never delete an
    # entitlement -- and with it every grant and label that hung off it.
    # Users are deactivated rather than deleted anyway (`identity.
    # services.deactivate_user`), so the null branch exists for the
    # hypothetical shell deletion, not as a normal path.
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="entitlements_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="uniq_entitlement_name_ci"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class EntitlementGrant(models.Model):
    """Who holds an entitlement, and in what role.

    A USER XOR A GROUP, never both and never neither -- a database
    constraint, not a convention, because a grant that named both would
    make "revoke this person's access" a question with two answers.

    `role` IS A COLUMN ON THIS ROW, NOT A SECOND ROW. Promoting a member
    to owner is an `UPDATE`, which is why the unique constraints below do
    not include it: two rows for one (entitlement, user) pair would make
    "does this person hold E" ambiguous, and the ambiguity would show up
    as a duplicate on every grant page.

    An OWNER of E is a member of E plus exactly three capabilities --
    grant/revoke E, label/unlabel with E, and see everything in E. The
    third needs no code at all: an owner grant is a grant, so it already
    appears in `identity.access.held_entitlement_ids`.
    """

    class Role(models.TextChoices):
        MEMBER = "member", "Member"
        OWNER = "owner", "Owner"

    class Source(models.TextChoices):
        MANUAL = "manual", "Granted here"
        # WRITTEN BY NOTHING TODAY. Present from day one because it is
        # the column an identity-provider reconciliation joins on:
        # "revoke every grant this provider used to assert and no longer
        # does" is answerable only if provider-asserted grants are
        # distinguishable from hand-made ones, and backfilling that
        # distinction later is impossible -- nobody can classify a row
        # after the fact.
        SSO = "sso", "From the identity provider"

    entitlement = models.ForeignKey(Entitlement, on_delete=models.CASCADE,
                                    related_name="grants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.CASCADE, related_name="entitlement_grants")
    group = models.ForeignKey("auth.Group", null=True, blank=True,
                              on_delete=models.CASCADE, related_name="entitlement_grants")
    role = models.CharField(max_length=8, choices=Role.choices, default=Role.MEMBER)
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.MANUAL)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="grants_made")
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["entitlement__name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(user__isnull=False, group__isnull=True)
                           | Q(user__isnull=True, group__isnull=False)),
                name="grant_user_xor_group",
            ),
            # PARTIAL uniques, not one plain unique across three columns:
            # NULLs do not collide in Postgres, so a plain
            # `UniqueConstraint(entitlement, user, group)` would happily
            # store the same group grant a thousand times.
            models.UniqueConstraint(fields=["entitlement", "user"],
                                    condition=Q(user__isnull=False),
                                    name="uniq_grant_entitlement_user"),
            models.UniqueConstraint(fields=["entitlement", "group"],
                                    condition=Q(group__isnull=False),
                                    name="uniq_grant_entitlement_group"),
        ]
        indexes = [
            models.Index(fields=["user"], name="identity_grant_user"),
            models.Index(fields=["group"], name="identity_grant_group"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        subject = self.user_id or f"group {self.group_id}"
        return f"{self.entitlement_id}->{subject} ({self.role})"
