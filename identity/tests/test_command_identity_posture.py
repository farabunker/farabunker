"""`manage.py identity_posture` -- the break-glass path.

It reaches `identity.services.set_posture` exactly as the posture page
does, so every case here proves it is NOT a way around that function's
three refusals: printing takes no action, a genuine switch writes one
audit row, and each refusal surfaces as an operator-readable
`CommandError` rather than a stack trace or a silent no-op.
"""
from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from identity.contracts import actions
from identity.contracts.postures import POSTURE_OPEN, POSTURE_PERSONAL
from identity.models import AuditEvent, IdentitySettings
from identity.tests._helpers import make_admin, make_user, posture

pytestmark = pytest.mark.django_db

REAL_KEY = "a-real-key-for-this-test-only"


@pytest.fixture(autouse=True)
def _a_real_key_and_no_debug(settings):
    """Two of the three `set_posture` conditions satisfied by default,
    matching `identity/tests/test_services.py`'s own fixture."""
    settings.DEBUG = False
    settings.SECRET_KEY = REAL_KEY


def _run(*args):
    out = StringIO()
    call_command("identity_posture", *args, stdout=out)
    return out.getvalue()


class TestPrintingTakesNoAction:
    def test_printing_with_no_arguments_does_not_write(self):
        before = AuditEvent.objects.count()
        output = _run()
        assert "posture: open" in output
        assert AuditEvent.objects.count() == before
        assert IdentitySettings.get_solo().posture == POSTURE_OPEN


class TestSwitching:
    def test_switching_writes_one_audit_row(self):
        make_admin()
        output = _run(POSTURE_PERSONAL)
        assert "posture: personal" in output
        assert AuditEvent.objects.filter(action=actions.POSTURE_CHANGED).count() == 1
        assert IdentitySettings.get_solo().posture == POSTURE_PERSONAL


class TestTheThreeRefusalsAreNotBypassed:
    """Each refusal is exercised on its own -- see
    `test_services.py::TestSetPosture` for why the other two conditions
    must be satisfied in every case below."""

    def test_no_active_superuser_is_reported_by_name(self):
        make_user()                      # a member, not an admin
        with pytest.raises(CommandError) as exc:
            _run(POSTURE_PERSONAL)
        assert "superuser" in str(exc.value).lower()
        assert IdentitySettings.get_solo().posture == POSTURE_OPEN

    def test_debug_on_is_reported_by_name(self, settings):
        make_admin()
        settings.DEBUG = True
        with pytest.raises(CommandError) as exc:
            _run(POSTURE_PERSONAL)
        assert "debug" in str(exc.value).lower()
        assert IdentitySettings.get_solo().posture == POSTURE_OPEN

    def test_the_shipped_default_secret_key_is_reported_by_name(self, settings):
        from config.settings import DEV_SECRET_KEY
        make_admin()
        settings.SECRET_KEY = DEV_SECRET_KEY
        with pytest.raises(CommandError) as exc:
            _run(POSTURE_PERSONAL)
        assert "key" in str(exc.value).lower()
        assert IdentitySettings.get_solo().posture == POSTURE_OPEN


class TestSwitchingToOpenIsAlwaysAllowed:
    @pytest.mark.parametrize("broken", ["no_admin", "debug", "key"])
    def test_switching_to_open_succeeds_under_all_three(self, settings, broken):
        from config.settings import DEV_SECRET_KEY
        admin = make_admin()
        with posture(POSTURE_PERSONAL):
            if broken == "no_admin":
                admin.is_active = False
                admin.save()
            elif broken == "debug":
                settings.DEBUG = True
            else:
                settings.SECRET_KEY = DEV_SECRET_KEY
            output = _run(POSTURE_OPEN)
            assert "posture: open" in output
            assert IdentitySettings.get_solo().posture == POSTURE_OPEN


class TestAdminSeesContentFlag:
    def test_on_writes_true_and_one_audit_row(self):
        make_admin()
        _run("--admin-sees-content", "on")
        assert IdentitySettings.get_solo().admin_sees_content is True
        row = AuditEvent.objects.get(action=actions.ADMIN_CONTENT_ACCESS_CHANGED)
        assert row.detail == {"to": True}

    def test_off_writes_false_and_one_audit_row(self):
        make_admin()
        _run("--admin-sees-content", "on")
        _run("--admin-sees-content", "off")
        assert IdentitySettings.get_solo().admin_sees_content is False
        assert AuditEvent.objects.filter(
            action=actions.ADMIN_CONTENT_ACCESS_CHANGED).count() == 2
        last = AuditEvent.objects.filter(
            action=actions.ADMIN_CONTENT_ACCESS_CHANGED).order_by("-at").first()
        assert last.detail == {"to": False}

    def test_it_can_be_set_with_no_posture_argument_at_all(self):
        """`--admin-sees-content` alone, with no positional `posture`,
        is still a switch -- not the print-only path -- and it is
        refused by none of the three posture conditions, because it
        never touches `posture`."""
        admin = make_admin()
        output = _run("--admin-sees-content", "on")
        assert "posture: open" in output
        assert IdentitySettings.get_solo().admin_sees_content is True
        assert admin.is_superuser is True  # unaffected
