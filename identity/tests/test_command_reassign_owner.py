"""`manage.py reassign_owner` -- the documented follow-up to
deactivating somebody who owned rows (spec section 10.5).

`_conversation`/`_ask_record` build the minimum row each test needs,
local to this module, matching `test_command_adopt_open_rows.py`'s own
helpers.
"""
from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from identity.contracts import actions
from identity.models import AuditEvent
from identity.tests._helpers import make_user

pytestmark = pytest.mark.django_db


def _agent():
    from agents.models import Agent

    return Agent.objects.get_or_create(
        slug="reassign-owner-agent",
        defaults=dict(name="Reassign owner agent", tool_keys=[]),
    )[0]


def _conversation(**overrides):
    from agents.models import Conversation

    fields = dict(agent=_agent())
    fields.update(overrides)
    return Conversation.objects.create(**fields)


def _ask_record(**overrides):
    from tools.rag.models import AskRecord

    fields = dict(question="q", connection_name="conn", model_id="model", answer="a")
    fields.update(overrides)
    return AskRecord.objects.create(**fields)


class TestRefusals:
    def test_an_unknown_from_username_is_refused(self):
        make_user(username="ann")
        with pytest.raises(CommandError) as exc:
            call_command("reassign_owner", "--from", "nobody", "--to", "ann")
        assert "nobody" in str(exc.value)

    def test_an_unknown_to_username_is_refused(self):
        make_user(username="bob")
        with pytest.raises(CommandError) as exc:
            call_command("reassign_owner", "--from", "bob", "--to", "nobody")
        assert "nobody" in str(exc.value)

    def test_an_inactive_to_user_is_refused(self):
        make_user(username="bob")
        make_user(username="ann", is_active=False)
        with pytest.raises(CommandError):
            call_command("reassign_owner", "--from", "bob", "--to", "ann")

    def test_an_unknown_kind_names_the_valid_keys(self):
        make_user(username="bob")
        make_user(username="ann")
        with pytest.raises(CommandError) as exc:
            call_command("reassign_owner", "--from", "bob", "--to", "ann",
                         "--kind", "nonsense.table")
        message = str(exc.value)
        assert "nonsense.table" in message
        assert "agents.conversation" in message


class TestReassignment:
    def test_it_moves_a_username_owned_row_and_audits_it(self):
        bob = make_user(username="bob")
        ann = make_user(username="ann")
        row = _conversation(owner_kind="user", owner_key=str(bob.pk))
        call_command("reassign_owner", "--from", "bob", "--to", "ann")
        row.refresh_from_db()
        assert (row.owner_kind, row.owner_key) == ("user", str(ann.pk))
        audit_row = AuditEvent.objects.get(action=actions.OWNER_REASSIGNED)
        assert audit_row.detail["from"] == "bob"
        assert audit_row.detail["to"] == "ann"
        assert audit_row.detail["kind"] is None
        assert audit_row.detail["counts"]["Conversations"] == 1

    def test_it_moves_open_owned_rows_when_from_is_the_literal_open(self):
        ann = make_user(username="ann")
        row = _conversation(owner_kind="open", owner_key="box")
        call_command("reassign_owner", "--from", "open", "--to", "ann")
        row.refresh_from_db()
        assert (row.owner_kind, row.owner_key) == ("user", str(ann.pk))

    def test_from_service_moves_exactly_the_shell_made_rows_and_nothing_else(self):
        """Not reachable from `adopt_open_rows`, which deliberately
        skips these rows -- an operator who wants one handed to a
        person says so explicitly, here, once (spec section 10.5)."""
        ann = make_user(username="ann")
        shell_made = _conversation(owner_kind="service", owner_key="local")
        open_owned = _conversation(owner_kind="open", owner_key="box")
        call_command("reassign_owner", "--from", "service", "--to", "ann")
        shell_made.refresh_from_db()
        open_owned.refresh_from_db()
        assert (shell_made.owner_kind, shell_made.owner_key) == ("user", str(ann.pk))
        assert (open_owned.owner_kind, open_owned.owner_key) == ("open", "box")

    def test_it_writes_exactly_one_audit_event(self):
        """One row, not one per moved row -- the same shape
        `adopt_open_rows` writes."""
        bob = make_user(username="bob")
        ann = make_user(username="ann")
        for _ in range(3):
            _conversation(owner_kind="user", owner_key=str(bob.pk))
        call_command("reassign_owner", "--from", "bob", "--to", "ann")
        rows = AuditEvent.objects.filter(action=actions.OWNER_REASSIGNED)
        assert rows.count() == 1
        assert rows.first().detail["counts"]["Conversations"] == 3

    def test_it_is_idempotent(self):
        bob = make_user(username="bob")
        ann = make_user(username="ann")
        _conversation(owner_kind="user", owner_key=str(bob.pk))
        call_command("reassign_owner", "--from", "bob", "--to", "ann")
        before = AuditEvent.objects.count()
        call_command("reassign_owner", "--from", "bob", "--to", "ann")
        row = AuditEvent.objects.filter(action=actions.OWNER_REASSIGNED).first()
        assert AuditEvent.objects.count() == before + 1
        assert sum(row.detail["counts"].values()) == 0

    def test_a_failing_audit_write_rolls_back_the_ownership_change(self, monkeypatch):
        """The row update and the audit write share one transaction: a
        database that disagrees with its own audit trail is worse than
        a command that raises and changes nothing."""
        bob = make_user(username="bob")
        ann = make_user(username="ann")
        row = _conversation(owner_kind="user", owner_key=str(bob.pk))

        def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("identity.audit.record", _boom)
        with pytest.raises(RuntimeError):
            call_command("reassign_owner", "--from", "bob", "--to", "ann")
        row.refresh_from_db()
        assert (row.owner_kind, row.owner_key) == ("user", str(bob.pk))
        assert AuditEvent.objects.filter(action=actions.OWNER_REASSIGNED).count() == 0

    def test_from_and_to_the_same_account_is_a_no_op_and_writes_no_audit_row(self):
        bob = make_user(username="bob")
        row = _conversation(owner_kind="user", owner_key=str(bob.pk))
        call_command("reassign_owner", "--from", "bob", "--to", "bob")
        row.refresh_from_db()
        assert (row.owner_kind, row.owner_key) == ("user", str(bob.pk))
        assert AuditEvent.objects.filter(action=actions.OWNER_REASSIGNED).count() == 0


class TestKind:
    def test_kind_narrows_to_one_table_and_touches_no_other(self):
        bob = make_user(username="bob")
        ann = make_user(username="ann")
        conv = _conversation(owner_kind="user", owner_key=str(bob.pk))
        ask = _ask_record(owner_kind="user", owner_key=str(bob.pk))
        call_command("reassign_owner", "--from", "bob", "--to", "ann",
                     "--kind", "agents.conversation")
        conv.refresh_from_db()
        ask.refresh_from_db()
        assert (conv.owner_kind, conv.owner_key) == ("user", str(ann.pk))
        assert (ask.owner_kind, ask.owner_key) == ("user", str(bob.pk))
