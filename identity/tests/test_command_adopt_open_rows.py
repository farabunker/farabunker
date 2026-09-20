"""`manage.py adopt_open_rows` -- an open box's first admin claims the
rows the open principal owns.

`_conversation`/`_ask_record` build the minimum row each test needs,
local to this module -- the same "no shared factory layer" convention
`tools/rag/tests/test_access.py`'s own `_ask_record` already follows.
`_conversation` shares one `Agent` row across calls (`get_or_create`)
because `Agent.slug` is unique and a test may create several
conversations.
"""
from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from identity.contracts import actions
from identity.models import AuditEvent
from identity.tests._helpers import make_admin, make_user

pytestmark = pytest.mark.django_db


def _agent():
    from agents.models import Agent

    return Agent.objects.get_or_create(
        slug="adopt-open-rows-agent",
        defaults=dict(name="Adopt open rows agent", tool_keys=[]),
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
    def test_an_unknown_username_is_refused(self):
        with pytest.raises(CommandError) as exc:
            call_command("adopt_open_rows", "--user", "nobody")
        assert "nobody" in str(exc.value)

    def test_a_member_is_refused(self):
        """Adoption gives one account every row on the box. That is an
        administrator's decision, and the refusal names why."""
        make_user(username="ann")
        with pytest.raises(CommandError) as exc:
            call_command("adopt_open_rows", "--user", "ann")
        assert "superuser" in str(exc.value).lower()

    def test_an_inactive_superuser_is_refused(self):
        make_admin(username="ann", is_active=False)
        with pytest.raises(CommandError):
            call_command("adopt_open_rows", "--user", "ann")


class TestClaiming:
    def test_it_claims_open_owned_and_blank_owned_rows(self, capsys):
        """BOTH. `("open", "box")` is what the agents column stamped;
        `("", "")` is what `vision` and `rag` rows written before IA-1
        carry, because their columns did not exist. A command that
        claimed only one would leave half the box unowned with no
        message saying so."""
        admin = make_admin(username="ann")
        open_owned = _conversation(owner_kind="open", owner_key="box")
        blank_owned = _ask_record(owner_kind="", owner_key="")
        call_command("adopt_open_rows", "--user", "ann")
        open_owned.refresh_from_db()
        blank_owned.refresh_from_db()
        assert (open_owned.owner_kind, open_owned.owner_key) == ("user", str(admin.pk))
        assert (blank_owned.owner_kind, blank_owned.owner_key) == ("user", str(admin.pk))

    def test_it_never_claims_a_row_a_shell_path_made(self):
        """Spec section 10.4. Attributing `manage.py agent_turn`'s
        conversation to the first administrator would put a person's
        name on an automated action, and nothing downstream could tell
        it had happened."""
        make_admin(username="ann")
        shell_made = _conversation(owner_kind="service", owner_key="local")
        call_command("adopt_open_rows", "--user", "ann")
        shell_made.refresh_from_db()
        assert (shell_made.owner_kind, shell_made.owner_key) == ("service", "local")

    def test_it_leaves_a_row_already_owned_by_somebody_alone(self):
        bob = make_user()
        make_admin(username="ann")
        theirs = _conversation(owner_kind="user", owner_key=str(bob.pk))
        call_command("adopt_open_rows", "--user", "ann")
        theirs.refresh_from_db()
        assert theirs.owner_key == str(bob.pk)

    def test_it_is_idempotent(self):
        make_admin(username="ann")
        _conversation(owner_kind="open", owner_key="box")
        call_command("adopt_open_rows", "--user", "ann")
        before = AuditEvent.objects.count()
        call_command("adopt_open_rows", "--user", "ann")
        row = AuditEvent.objects.filter(action=actions.ADOPTED).first()
        assert AuditEvent.objects.count() == before + 1
        assert sum(row.detail["counts"].values()) == 0

    def test_it_writes_exactly_one_audit_event_with_the_counts(self):
        """One row, not one per claimed row: the interesting fact is the
        EVENT and the counts, not five thousand line items."""
        make_admin(username="ann")
        for _ in range(3):
            _conversation(owner_kind="open", owner_key="box")
        call_command("adopt_open_rows", "--user", "ann")
        rows = AuditEvent.objects.filter(action=actions.ADOPTED)
        assert rows.count() == 1
        assert rows.first().detail["counts"]["Conversations"] == 3

    def test_a_failing_audit_write_rolls_back_the_ownership_change(self, monkeypatch):
        """The row update and the audit write share one transaction: a
        database that disagrees with its own audit trail is worse than
        a command that raises and changes nothing."""
        make_admin(username="ann")
        row = _conversation(owner_kind="open", owner_key="box")

        def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("identity.audit.record", _boom)
        with pytest.raises(RuntimeError):
            call_command("adopt_open_rows", "--user", "ann")
        row.refresh_from_db()
        assert (row.owner_kind, row.owner_key) == ("open", "box")
        assert AuditEvent.objects.filter(action=actions.ADOPTED).count() == 0


class TestDryRun:
    def test_it_prints_the_counts_and_writes_nothing(self, capsys):
        make_admin(username="ann")
        row = _conversation(owner_kind="open", owner_key="box")
        call_command("adopt_open_rows", "--user", "ann", "--dry-run")
        row.refresh_from_db()
        assert row.owner_kind == "open"
        assert AuditEvent.objects.filter(action=actions.ADOPTED).count() == 0
        assert "Conversations" in capsys.readouterr().out


class TestOrdering:
    def test_it_prints_the_ordering_guidance(self, capsys):
        """Create the superuser, adopt, THEN switch the posture.
        Switching first leaves the new admin looking at their own empty
        box until they adopt -- which is confusing, not dangerous, and
        the command says so rather than letting somebody discover it."""
        make_admin(username="ann")
        call_command("adopt_open_rows", "--user", "ann")
        assert "posture" in capsys.readouterr().out.lower()

    def test_there_is_no_undo_flag(self):
        """Reversibility is the POSTURE SWITCH, not an undo: switching
        back to open makes every visibility function return everything,
        so the reassigned owner stops mattering. Rewriting owners back
        to the open principal would be a second, lossier operation that
        also erased any ownership recorded after adoption."""
        from identity.management.commands import adopt_open_rows
        source = adopt_open_rows.__file__
        assert "--undo" not in open(source).read()
