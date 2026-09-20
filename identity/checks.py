"""Six system checks, run once per boot by `manage.py check`.

The five DB-reading checks swallow `django.db.Error` AND RETURN NOTHING,
and every one of their docstrings says so rather than leaving a reader
to mistake it for a hole: a box mid-migration, or one running
`manage.py migrate` against an empty database, has no settings row and
is not in violation of anything. `Error`, not the narrower
`DatabaseError` -- `InterfaceError` (a closed or broken connection) is a
sibling of `DatabaseError` under `Error`, not a subclass of it, and a
box that cannot even reach its connection is no more in violation than
one whose table doesn't exist yet. `check_allowed_hosts_is_not_a_wildcard`
reads no database at all and needs no such branch.

A check that runs only at startup is not enough on its own -- flipping
the posture on a RUNNING box would change nothing until the next restart
-- so `identity.services.set_posture` enforces the same three
posture-coupled conditions (no active superuser, `DEBUG`, the default
`SECRET_KEY`) at run time. These two are halves of one rule, not two
rules. `set_posture` does not enforce `ALLOWED_HOSTS`; that one is
boot-only.
"""
from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Warning as CheckWarning
from django.core.exceptions import ImproperlyConfigured
from django.db import Error as DBError


def _accounts_on() -> bool | None:
    """Whether accounts are on, or None if the box cannot be asked."""
    try:
        from identity.access import accounts_on
        return accounts_on()
    except DBError:
        return None


def check_debug_is_off(app_configs, **kwargs):
    """`identity.E001` -- DEBUG is on while the posture is not open.

    `DEBUG=True` renders tracebacks with settings and environment to any
    visitor, which is a disclosure a posture with accounts must not
    permit. Silent when the settings row cannot be read.
    """
    if _accounts_on() is not True or not settings.DEBUG:
        return []
    return [Error(
        "DEBUG is on while this box requires accounts.",
        hint="Set DEBUG=0. A box with accounts must not render tracebacks -- "
             "with its settings and environment in them -- to any visitor.",
        id="identity.E001",
    )]


def check_secret_key_is_not_the_default(app_configs, **kwargs):
    """`identity.E002` -- a default SECRET_KEY while the posture is not
    open.

    That key signs every session cookie. A box whose signing key is
    published in a public repository has accounts in name only: anybody
    who can read the repository can mint a session for any account on
    it. This check is the reason accounts and a default key cannot
    coexist. Silent when the settings row cannot be read.
    """
    if _accounts_on() is not True or settings.SECRET_KEY != settings.DEV_SECRET_KEY:
        return []
    return [Error(
        "This box requires accounts but is still signing sessions with the "
        "shipped development key.",
        hint="Set a real SECRET_KEY in the environment and restart.",
        id="identity.E002",
    )]


def check_secure_cookies(app_configs, **kwargs):
    """`identity.W001` -- accounts are on and SECURE_COOKIES is off.

    A WARNING, not an error, because HTTP on a local network is a
    supported posture and forcing Secure cookies on would break login
    there. Silent when the settings row cannot be read.
    """
    if _accounts_on() is not True or settings.SECURE_COOKIES:
        return []
    return [CheckWarning(
        "Accounts are on but session and CSRF cookies are not marked Secure.",
        hint="Set SECURE_COOKIES=1 if this box is served over TLS. Leave it off "
             "for a plain-HTTP local-network box, which is supported.",
        id="identity.W001",
    )]


def check_open_box_with_existing_users(app_configs, **kwargs):
    """`identity.W002` -- the posture is `open` but at least one
    `identity_user` row already exists.

    A box in the open posture has no signed-in caller and every request
    runs as `OPEN_PRINCIPAL` -- that is correct on a genuinely fresh
    box, where `identity_user` is empty. It is a DIFFERENT fact, and one
    worth a warning, when accounts exist AND the box is nonetheless
    open: that combination is what a lost or restored `IdentitySettings`
    row looks like from the outside -- the accounts an operator created
    are still in the database, but the posture that used to require
    signing in to reach them has silently reverted to open, and every
    caller can now act as every one of those accounts without a
    password.

    A WARNING, not an error: an open box with users is not itself a
    broken configuration (`set_posture` can put a box back into `open`
    on purpose, users and all, and this check must not block that), it
    is a fact worth an operator's attention. Silent when either read
    (the settings row, the `identity_user` count) cannot be made --
    `_accounts_on`'s own docstring explains why that is "not in
    violation" rather than a hole.
    """
    accounts_required = _accounts_on()
    if accounts_required is None:
        return []
    if accounts_required:
        return []
    try:
        from identity.models import User
        any_users = User.objects.exists()
    except DBError:
        return []
    if not any_users:
        return []
    return [CheckWarning(
        "This box is in the open posture but at least one account already "
        "exists.",
        hint="This usually means the identity settings row was lost or "
             "restored separately from the accounts table. If accounts "
             "should be required, set the posture back to personal or "
             "enterprise (see docs/OPERATIONS.md).",
        id="identity.W002",
    )]


def check_open_box_is_anonymous_administrator(app_configs, **kwargs):
    """`identity.W003` -- the posture is `open` AND `DEBUG` is on.

    THE COMBINATION IS THE FINDING, not either half. `identity.E001`
    refuses `DEBUG` on a box that requires accounts and is silent here by
    construction; `identity.W002` warns about an open box that already has
    accounts and is silent on a fresh one. A fresh open box with `DEBUG`
    on therefore said nothing at all -- and it is the state in which every
    administrator surface answers an unauthenticated caller, the technical
    404 enumerates every URL pattern, and a 500 renders settings and
    environment to whoever triggered it.

    A WARNING, not an error, for the same reason `W002` is one: an open
    box is a posture an operator may choose, and a check that refused to
    boot it would make the fresh-install path impossible. The point is
    that the state is SAID, once per boot, next to the one document that
    says how to leave it. Silent when the settings row cannot be read,
    same contract as its four DB-reading neighbours.
    """
    if _accounts_on() is not False or not settings.DEBUG:
        return []
    return [CheckWarning(
        "This box is in the open posture with DEBUG on: every caller on the "
        "network is an administrator, and any error renders this box's settings "
        "and environment to them.",
        hint="Set DEBUG=0, a real SECRET_KEY and this box's ALLOWED_HOSTS "
             "together, recreate the containers, create the first "
             "administrator, then switch the posture -- the order matters "
             "and is written out in docs/OPERATIONS.md under "
             "“Leaving the open posture”.",
        id="identity.W003",
    )]


def check_allowed_hosts_is_not_a_wildcard(app_configs, **kwargs):
    """`identity.E003`/`identity.E004` -- `ALLOWED_HOSTS` contains `"*"`,
    or is empty, while `DEBUG` is off. Two branches of one function
    because they are the same mistake in opposite directions: a wildcard
    hands the box to any DNS name; an empty list (`ALLOWED_HOSTS=`
    written blank) refuses every `Host:` header, including the
    operator's own, and locks the box out just as silently.

    UNLIKE ITS FIVE NEIGHBOURS, THIS ONE NEVER READS THE DATABASE, and
    therefore never returns nothing for a box mid-migration: both
    misconfigurations are wrong in every posture, `open` included. An
    `open` box has no accounts to compromise, but it still holds the
    operator's whole document library, and DNS rebinding hands that to
    any page a LAN browser visits (S1) -- and a locked-out box serves
    nobody, operator included.

    `DEBUG` IS THE ONE EXEMPTION, because Django itself already treats
    `DEBUG=True` as implying `["localhost", "127.0.0.1", "[::1]"]` and a
    developer running `runserver` behind a tunnel has a legitimate,
    short-lived reason to widen it (or, mid-edit, to leave it briefly
    empty). `identity.E001` already refuses `DEBUG` on a box with
    accounts, so the two checks compose: a box with accounts can reach
    neither branch of this exemption.
    """
    if settings.DEBUG:
        return []
    if "*" in settings.ALLOWED_HOSTS:
        return [Error(
            "ALLOWED_HOSTS contains \"*\" while DEBUG is off.",
            hint="Name this box's real hostnames in ALLOWED_HOSTS (see "
                 "docs/DEV.md section 3). A wildcard lets any DNS name resolve "
                 "to this box and be served, which makes an attacker's page "
                 "same-origin with it.",
            id="identity.E003",
        )]
    if not settings.ALLOWED_HOSTS:
        return [Error(
            "ALLOWED_HOSTS is empty while DEBUG is off.",
            hint="Name this box's real hostnames in ALLOWED_HOSTS (see "
                 "docs/DEV.md section 3), or unset it to use the default. An "
                 "empty list refuses every Host: header Django sees, "
                 "including the operator's own, so this box answers nothing "
                 "until it is fixed.",
            id="identity.E004",
        )]
    return []


def serious_boot_problems() -> list[str]:
    """The messages of every refusal-level problem with this box's
    configuration; empty when there are none.

    S13: the three `Error` checks above are enforced today only because
    `migrate` runs system checks and `migrate` happens to be in the
    container's CMD. That is real protection and it is also an ACCIDENT
    OF THE COMMAND CHAIN: a deployment that migrates separately -- a CI
    step, an init container, `docker compose run web migrate` then a
    plain `uvicorn` -- loses it silently, and an enterprise-posture box
    then serves Django's technical-500 page, with its settings and frame
    locals, to any visitor.

    This function is what `refuse_if_boot_problems` (below) asks, and
    through it what BOTH `config/asgi.py` and `config/wsgi.py` ask, so
    the refusal travels with the APPLICATION at either entry point --
    including `compose.override.yaml`'s dev `runserver` path, which
    imports `config.wsgi`, not `config.asgi`. Deliberately NOT
    `django.core.checks.run_checks()`: that runs every registered check,
    including third-party ones, and would turn any unrelated Error
    anywhere into a boot failure -- far wider than the finding.

    Same "silent when the box cannot be asked" contract as the checks it
    calls: a box mid-migration is not in violation of anything.

    H12 review round 1: EACH LINE CARRIES ITS CHECK'S `hint`, not only
    its `msg`. The bare `msg` alone (e.g. E002's "This box requires
    accounts but is still signing sessions with the shipped development
    key.") names the problem but not the fix; the operator reading the
    `ImproperlyConfigured` `refuse_if_boot_problems` raises from this
    needs the environment variable to change, not just the diagnosis.
    """
    problems = []
    for check in (check_debug_is_off,
                  check_secret_key_is_not_the_default,
                  check_allowed_hosts_is_not_a_wildcard):
        problems.extend(f"{message.msg} {message.hint}" for message in check(None))
    return problems


def refuse_if_boot_problems() -> None:
    """Raise `ImproperlyConfigured`, naming every problem
    `serious_boot_problems()` finds, or do nothing when there are none.

    Whole-branch review, final wave: this used to be inlined in
    `config/asgi.py` alone, which meant `config/wsgi.py` -- and, through
    it, `compose.override.yaml`'s dev `runserver` path -- booted a
    misconfigured box with none of ASGI's refusal. One function, called
    by both entry points, so the refusal is IDENTICAL at either one
    rather than two hand-kept copies of the same raise drifting apart.
    Calling `serious_boot_problems()` as a plain module-level name
    (rather than capturing it in a default argument or similar) matters
    for testability: a test that does
    `monkeypatch.setattr(checks, "serious_boot_problems", ...)` and then
    reloads `config.asgi`/`config.wsgi` relies on this function's own
    global lookup picking up the patched attribute at call time.
    """
    problems = serious_boot_problems()
    if problems:
        raise ImproperlyConfigured(
            "This box refuses to serve with its current configuration:\n  - "
            + "\n  - ".join(problems)
        )
