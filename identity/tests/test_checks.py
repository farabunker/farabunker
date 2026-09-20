"""The six system checks, and their one shared silence.

Each check is exercised in isolation: the two conditions a case is not
testing are satisfied by the fixture, so a passing assertion cannot be a
second condition firing. `TestTheSharedSilence` below parametrizes over
only five of the six -- `check_allowed_hosts_is_not_a_wildcard` reads
no database and has no silence to share.
"""
from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError

from identity import checks
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL
from identity.tests._helpers import make_admin, posture

pytestmark = pytest.mark.django_db

REAL_KEY = "a-real-key-for-this-test-only"


@pytest.fixture(autouse=True)
def _clean(settings):
    settings.DEBUG = False
    settings.SECRET_KEY = REAL_KEY
    settings.SECURE_COOKIES = True


def _ids(messages):
    return [m.id for m in messages]


class TestE001:
    def test_it_fires_for_debug_plus_accounts(self, settings):
        settings.DEBUG = True
        with posture(POSTURE_ENTERPRISE):
            assert _ids(checks.check_debug_is_off(None)) == ["identity.E001"]

    def test_it_is_silent_in_the_open_posture(self, settings):
        """`DEBUG=1` on an open box is the ordinary development
        configuration and is not a violation of anything."""
        settings.DEBUG = True
        with posture(POSTURE_OPEN):
            assert checks.check_debug_is_off(None) == []


class TestE002:
    def test_it_fires_for_the_shipped_key_plus_accounts(self, settings):
        from config.settings import DEV_SECRET_KEY
        settings.SECRET_KEY = DEV_SECRET_KEY
        with posture(POSTURE_ENTERPRISE):
            assert _ids(checks.check_secret_key_is_not_the_default(None)) == [
                "identity.E002"]

    def test_a_real_key_passes(self):
        with posture(POSTURE_ENTERPRISE):
            assert checks.check_secret_key_is_not_the_default(None) == []


class TestW001:
    def test_it_warns_not_errors(self, settings):
        settings.SECURE_COOKIES = False
        with posture(POSTURE_ENTERPRISE):
            messages = checks.check_secure_cookies(None)
        assert _ids(messages) == ["identity.W001"]
        assert messages[0].level < 40      # WARNING, not ERROR


class TestW002:
    """`identity.W002` -- posture is open but at least one account
    already exists, which is what a lost or restored `IdentitySettings`
    row looks like from the outside."""

    def test_it_is_silent_on_a_genuinely_open_box(self):
        """No users at all: this IS the shipped default and must not
        warn."""
        with posture(POSTURE_OPEN):
            assert checks.check_open_box_with_existing_users(None) == []

    def test_it_warns_when_open_with_an_existing_account(self):
        make_admin()
        with posture(POSTURE_OPEN):
            messages = checks.check_open_box_with_existing_users(None)
        assert _ids(messages) == ["identity.W002"]
        assert messages[0].level < 40   # WARNING, not ERROR

    def test_it_is_silent_once_the_box_requires_accounts(self):
        """The same accounts, but the posture now matches them --
        exactly what fixing the lost row looks like."""
        make_admin()
        with posture(POSTURE_ENTERPRISE):
            assert checks.check_open_box_with_existing_users(None) == []


class TestW003TheOpenBoxWarning:
    """`identity.W003` -- the state report F observed on the live box:
    open posture, DEBUG on, no accounts, nothing said about it."""

    def test_an_open_box_with_debug_on_warns_even_with_no_accounts(self, settings):
        settings.DEBUG = True
        with posture(POSTURE_OPEN):
            ids = _ids(checks.check_open_box_is_anonymous_administrator(None))
        assert ids == ["identity.W003"]

    def test_the_warning_names_the_state_and_points_at_the_runbook(self, settings):
        settings.DEBUG = True
        with posture(POSTURE_OPEN):
            warning = checks.check_open_box_is_anonymous_administrator(None)[0]
        assert "administrator" in str(warning.msg)
        assert "docs/OPERATIONS.md" in str(warning.hint)

    def test_a_closed_posture_is_silent(self, settings):
        settings.DEBUG = True
        with posture(POSTURE_PERSONAL):
            assert checks.check_open_box_is_anonymous_administrator(None) == []

    def test_an_open_box_with_debug_off_is_silent(self, settings):
        settings.DEBUG = False
        with posture(POSTURE_OPEN):
            assert checks.check_open_box_is_anonymous_administrator(None) == []

    def test_it_is_a_warning_so_the_boot_aggregator_does_not_refuse_on_it(self, settings):
        """H12's `serious_boot_problems` collects Errors. An open box is
        not a misconfiguration -- it is a posture -- so this must never
        stop a genuinely fresh box from booting."""
        settings.DEBUG = True
        with posture(POSTURE_OPEN):
            assert checks.serious_boot_problems() == []

    # H22 review round 1 MINOR: a direct-mock silence test used to live
    # here too, duplicating `TestTheSharedSilence`'s parametrized case
    # for this same check (which patches `identity.access.accounts_on`
    # to raise `DatabaseError`, the realistic "row cannot be read" path)
    # -- one test of that contract per check, not two.

    def test_the_check_is_registered(self):
        from django.core.checks.registry import registry
        names = {c.__name__ for c in registry.get_checks()}
        assert "check_open_box_is_anonymous_administrator" in names


class TestAllowedHostsCheck:
    """`identity.E003` (S1): a wildcard `ALLOWED_HOSTS` outside DEBUG is a
    refusal, because it is what makes DNS rebinding a same-origin bypass
    of every other control on this box."""

    def test_a_wildcard_outside_debug_is_an_error(self, settings):
        settings.DEBUG = False
        settings.ALLOWED_HOSTS = ["*"]
        problems = checks.check_allowed_hosts_is_not_a_wildcard(None)
        assert [p.id for p in problems] == ["identity.E003"]

    def test_a_wildcard_inside_a_longer_list_is_still_an_error(self, settings):
        settings.DEBUG = False
        settings.ALLOWED_HOSTS = ["localhost", "*"]
        assert [p.id for p in checks.check_allowed_hosts_is_not_a_wildcard(None)] == ["identity.E003"]

    def test_a_wildcard_under_debug_is_not_an_error(self, settings):
        settings.DEBUG = True
        settings.ALLOWED_HOSTS = ["*"]
        assert checks.check_allowed_hosts_is_not_a_wildcard(None) == []

    def test_named_hosts_are_not_an_error(self, settings):
        settings.DEBUG = False
        settings.ALLOWED_HOSTS = ["localhost", "127.0.0.1", "box.lan"]
        assert checks.check_allowed_hosts_is_not_a_wildcard(None) == []

    def test_the_shipped_default_is_not_a_wildcard(self):
        """The finding itself, pinned -- asserted on the module-level
        DEFAULT, not on `ALLOWED_HOSTS`. Reloading the settings module
        re-runs `load_dotenv`, and `scripts/preview` copies a real `.env`
        into every worktree (S15); an ALLOWED_HOSTS set there would fail
        this for reasons that have nothing to do with the change. The
        default is computed from `socket.gethostname()` alone and cannot
        be perturbed."""
        import importlib
        import config.settings as shipped
        importlib.reload(shipped)
        assert "*" not in shipped._DEFAULT_ALLOWED_HOSTS
        assert shipped._DEFAULT_ALLOWED_HOSTS.startswith("localhost,127.0.0.1,[::1]")

    def test_the_check_is_registered(self):
        from django.core.checks.registry import registry
        names = {c.__name__ for c in registry.get_checks()}
        assert "check_allowed_hosts_is_not_a_wildcard" in names

    def test_an_empty_allowed_hosts_outside_debug_is_an_error(self, settings):
        """The symmetric silent lockout: `ALLOWED_HOSTS=` written blank in
        `.env` locks out every visitor -- including the operator -- just
        as surely as a wildcard hands the box away, and just as
        silently: nothing about `manage.py check` said so before this
        branch existed."""
        settings.DEBUG = False
        settings.ALLOWED_HOSTS = []
        problems = checks.check_allowed_hosts_is_not_a_wildcard(None)
        assert [p.id for p in problems] == ["identity.E004"]

    def test_an_empty_allowed_hosts_under_debug_is_not_an_error(self, settings):
        settings.DEBUG = True
        settings.ALLOWED_HOSTS = []
        assert checks.check_allowed_hosts_is_not_a_wildcard(None) == []


class TestTheSharedSilence:
    @pytest.mark.parametrize("check", [
        checks.check_debug_is_off,
        checks.check_secret_key_is_not_the_default,
        checks.check_secure_cookies,
        checks.check_open_box_with_existing_users,
        checks.check_open_box_is_anonymous_administrator,
    ])
    def test_every_check_is_silent_when_the_row_cannot_be_read(self, monkeypatch, check):
        """A box mid-migration has no settings row and is not in
        violation. The swallow is documented in each check's own
        docstring so it is not mistaken for a hole."""
        def boom():
            raise DatabaseError("relation \"identity_identitysettings\" does not exist")
        monkeypatch.setattr("identity.access.accounts_on", boom)
        assert check(None) == []

    def test_every_check_is_also_silent_on_an_interface_error(self, monkeypatch):
        """`InterfaceError` (e.g. a closed or broken connection) is a
        SIBLING of `DatabaseError` under `django.db.Error`, not a
        subclass of it -- catching only `DatabaseError` would let this
        one through as an unhandled exception at boot. A box that
        cannot even reach its connection is no more in violation than
        one whose table doesn't exist yet."""
        from django.db import InterfaceError

        def boom():
            raise InterfaceError("connection already closed")
        monkeypatch.setattr("identity.access.accounts_on", boom)
        assert checks.check_debug_is_off(None) == []


class TestSeriousBootProblems:
    """`posture(...)` is a CONTEXT MANAGER re-exported from
    `identity/testing.py`, not a fixture -- the shape every other test in
    this module already uses. `REAL_KEY` and the autouse `_clean` fixture
    are already defined at the top of this file; reuse them, do not
    redefine them."""

    def test_a_correctly_configured_box_has_none(self, settings):
        settings.DEBUG = False
        settings.SECRET_KEY = REAL_KEY
        settings.ALLOWED_HOSTS = ["localhost"]
        with posture(POSTURE_ENTERPRISE):
            assert checks.serious_boot_problems() == []

    def test_debug_on_with_accounts_is_one(self, settings):
        settings.DEBUG = True
        settings.SECRET_KEY = REAL_KEY
        settings.ALLOWED_HOSTS = ["localhost"]
        with posture(POSTURE_ENTERPRISE):
            problems = checks.serious_boot_problems()
        assert len(problems) == 1
        # H12 review round 1: the hint travels too, so the message names
        # the fix ("Set DEBUG=0"), not only the diagnosis.
        assert "DEBUG=0" in problems[0]

    def test_the_shipped_secret_key_with_accounts_is_one(self, settings):
        settings.DEBUG = False
        settings.SECRET_KEY = settings.DEV_SECRET_KEY
        settings.ALLOWED_HOSTS = ["localhost"]
        with posture(POSTURE_ENTERPRISE):
            problems = checks.serious_boot_problems()
        assert len(problems) == 1
        # H12 review round 1: without the hint this message named no
        # environment variable at all.
        assert "SECRET_KEY" in problems[0]

    def test_a_wildcard_host_is_one_in_every_posture(self, settings):
        """Unlike its two neighbours this one does not depend on the
        posture: an open box has no accounts to compromise but still
        holds the whole document library."""
        settings.DEBUG = False
        settings.ALLOWED_HOSTS = ["*"]
        with posture(POSTURE_OPEN):
            problems = checks.serious_boot_problems()
        assert len(problems) == 1
        assert "ALLOWED_HOSTS" in problems[0]

    def test_a_box_that_cannot_reach_its_database_is_not_in_violation(self, settings, monkeypatch):
        """Same contract every check in this module already has: a box
        mid-migration is not misconfigured."""
        monkeypatch.setattr(checks, "_accounts_on", lambda: None)
        settings.DEBUG = True
        settings.ALLOWED_HOSTS = ["localhost"]
        assert checks.serious_boot_problems() == []

    def test_the_live_box_state_is_not_in_violation(self, settings, monkeypatch):
        """H12 addendum: the live box today runs `DEBUG=1` in the `open`
        posture with a non-default `SECRET_KEY` -- that exact state must
        keep booting. `open` means `_accounts_on()` is False, so E001 and
        E002 are silent regardless of `DEBUG`/`SECRET_KEY`, and E003/E004
        are exempted outright while `DEBUG` is on.

        H12 review round 1 (orchestrator ruling, Minor 6): `ALLOWED_HOSTS`
        is exercised at its GENUINE default -- what `config.settings`
        computes when the environment variable is unset -- rather than
        pinned to an arbitrary test value like `["localhost"]`, which
        would only prove the exemption holds for one hand-picked list,
        not for the box's actual shipped default.
        """
        import importlib

        import config.settings as settings_module
        monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
        importlib.reload(settings_module)
        settings.DEBUG = True
        settings.SECRET_KEY = REAL_KEY
        settings.ALLOWED_HOSTS = settings_module.ALLOWED_HOSTS
        with posture(POSTURE_OPEN):
            assert checks.serious_boot_problems() == []

    @pytest.mark.parametrize("entrypoint", ["config.asgi", "config.wsgi"])
    def test_the_entrypoint_refuses_a_box_with_a_problem(self, monkeypatch, entrypoint):
        """The finding: the refusal must travel with the APPLICATION, not
        only with `migrate` -- and it must travel with BOTH entry
        points, not only ASGI. `compose.override.yaml`'s dev `web`
        command runs `python manage.py runserver`, which imports
        `config.wsgi`, not `config.asgi` -- before the whole-branch
        review fix, that path booted a misconfigured box with none of
        ASGI's refusal.

        Both modules call `identity.checks.refuse_if_boot_problems`,
        which calls `serious_boot_problems()` as a plain global lookup
        inside `identity/checks.py` -- so patching the module attribute
        here is what the reload of EITHER entrypoint picks up.

        H12 review round 1: `match=` pins the operator-facing string
        each entrypoint actually raises, not merely that SOME
        `ImproperlyConfigured` was raised.
        """
        import importlib
        monkeypatch.setattr(checks, "serious_boot_problems", lambda: ["nope"])
        with pytest.raises(
            ImproperlyConfigured,
            match=r"This box refuses to serve with its current configuration:",
        ):
            importlib.reload(importlib.import_module(entrypoint))

    @pytest.mark.parametrize("entrypoint", ["config.asgi", "config.wsgi"])
    def test_the_entrypoint_lists_every_problem_as_its_own_bullet(self, monkeypatch, entrypoint):
        """H12 review round 1: two problems must render as two separate
        `  - ` bullets, not get run together into one line an operator
        could misread as a single condition -- true at both entry
        points, since both go through the one shared
        `refuse_if_boot_problems` helper."""
        import importlib
        monkeypatch.setattr(
            checks, "serious_boot_problems",
            lambda: ["first problem here", "second problem here"],
        )
        with pytest.raises(ImproperlyConfigured) as exc_info:
            importlib.reload(importlib.import_module(entrypoint))
        message = str(exc_info.value)
        assert "  - first problem here" in message
        assert "  - second problem here" in message

    @pytest.mark.parametrize("entrypoint", ["config.asgi", "config.wsgi"])
    def test_the_entrypoint_boots_the_live_box_state(self, settings, monkeypatch, entrypoint):
        """Companion to the refusal test above, unpatched: reloading
        EITHER entrypoint under the exact live-box state (H12 addendum)
        must NOT raise. Same Minor 6 fix as
        `test_the_live_box_state_is_not_in_violation` above: `ALLOWED_HOSTS`
        is the genuine, unset default, not a pinned test value."""
        import importlib

        import config.settings as settings_module
        monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
        importlib.reload(settings_module)
        settings.DEBUG = True
        settings.SECRET_KEY = REAL_KEY
        settings.ALLOWED_HOSTS = settings_module.ALLOWED_HOSTS
        with posture(POSTURE_OPEN):
            importlib.reload(importlib.import_module(entrypoint))
