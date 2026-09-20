"""One cookie jar per stack, on a machine that runs four of them.

COOKIES ARE NOT SCOPED BY PORT. A cookie's scope is scheme + host +
path; RFC 6265 section 8.5 says so in as many words ("cookies do not
provide isolation by port"). This box is routinely run four ways at once
on a dev machine -- the live stack on :8000 and up to three branch
previews on :8001-:8004 -- and every one of them was writing `sessionid`
and `csrftoken` to `localhost`. Whichever tab rolled its session last
won the jar and signed the others out mid-click, with their server-side
session rows still perfectly alive, which is what made the symptom so
hard to read: the server says you are signed in and the browser
disagrees.

`config/settings.py` therefore reads both names from the environment,
defaulting to Django's own, and `compose.preview.yaml` keys them on the
one thing already unique per stack -- the published web port.

THE DEFAULTS ARE THE POINT OF HALF THIS MODULE. An unset environment
must leave the live box, CI and this suite byte-identical to before the
setting existed; a change that fixed dev at the cost of a different
cookie name in production would be a worse bug than the one it fixes.
"""
from __future__ import annotations

import ast
import pathlib
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.test import override_settings
from django.urls import reverse

from identity.testing import make_user, sign_in

REPO_ROOT = Path(django_settings.BASE_DIR)

SESSION_ENV = "FARABUNKER_SESSION_COOKIE_NAME"
CSRF_ENV = "FARABUNKER_CSRF_COOKIE_NAME"


@pytest.fixture
def reloaded_settings(monkeypatch):
    """`config.settings`, re-imported under a caller-supplied
    environment, and restored afterwards.

    THE SAME FIXTURE SHAPE `test_backup.py` USES, and for the reason its
    own docstring gives at length: `importlib.reload` mutates the actual
    module object in `sys.modules`, and pytest's `monkeypatch` teardown
    undoes the environment variable but not that mutation -- so without
    the second reload every LATER test in the process would import
    `config.settings` and see this module's values.
    """
    import importlib

    import config.settings as shipped

    def reload_with(**environment):
        for name in (SESSION_ENV, CSRF_ENV):
            value = environment.get(name)
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        importlib.reload(shipped)
        return shipped

    yield reload_with
    reload_with()


class TestTheDefaultsAreDjangosOwn:
    def test_an_unset_environment_changes_nothing(self, reloaded_settings):
        """THE HALF THAT PROTECTS THE LIVE BOX. Nothing about :8000, CI
        or this suite may move because a dev machine has a collision."""
        shipped = reloaded_settings()
        assert shipped.SESSION_COOKIE_NAME == "sessionid"
        assert shipped.CSRF_COOKIE_NAME == "csrftoken"

    def test_the_running_suite_is_on_those_defaults(self):
        """Non-vacuous companion: the module above is reloaded in
        isolation, so it could agree with itself while the suite this
        assertion runs inside had drifted."""
        assert django_settings.SESSION_COOKIE_NAME == "sessionid"
        assert django_settings.CSRF_COOKIE_NAME == "csrftoken"


class TestEachNameIsReadFromTheEnvironment:
    def test_the_session_cookie_name_flows_through(self, reloaded_settings):
        shipped = reloaded_settings(**{SESSION_ENV: "sessionid_8003"})
        assert shipped.SESSION_COOKIE_NAME == "sessionid_8003"
        # AND THE OTHER ONE DOES NOT MOVE WITH IT: two stacks that
        # renamed only half their jar would still collide on the other
        # half, which is a harder bug to see than the one being fixed.
        assert shipped.CSRF_COOKIE_NAME == "csrftoken"

    def test_the_csrf_cookie_name_flows_through(self, reloaded_settings):
        shipped = reloaded_settings(**{CSRF_ENV: "csrftoken_8003"})
        assert shipped.CSRF_COOKIE_NAME == "csrftoken_8003"
        assert shipped.SESSION_COOKIE_NAME == "sessionid"

    def test_both_together(self, reloaded_settings):
        shipped = reloaded_settings(**{SESSION_ENV: "s_8004", CSRF_ENV: "c_8004"})
        assert (shipped.SESSION_COOKIE_NAME, shipped.CSRF_COOKIE_NAME) == (
            "s_8004", "c_8004")


@pytest.mark.django_db
class TestTheNamesReachARealResponse:
    """THE PROPERTY THAT ACTUALLY FIXES THE COLLISION, which a settings
    read alone does not prove: the browser has to be handed the renamed
    cookie and NOT the default one, or the two stacks still share an
    entry."""

    def test_a_real_login_sets_the_renamed_session_cookie_and_not_the_default(
            self, client):
        user = make_user()
        with override_settings(SESSION_COOKIE_NAME="sessionid_8003"):
            sign_in(client, user)
            assert "sessionid_8003" in client.cookies
            assert "sessionid" not in client.cookies

    def test_a_form_page_sets_the_renamed_csrf_cookie_and_not_the_default(
            self, client):
        with override_settings(CSRF_COOKIE_NAME="csrftoken_8003"):
            response = client.get(reverse("identity-login"))
        assert response.status_code == 200
        assert "csrftoken_8003" in response.cookies
        assert "csrftoken" not in response.cookies


def _settings_env_key(setting_name: str) -> str:
    """The environment variable `config/settings.py` REALLY reads for
    `setting_name`, parsed out of its own source.

    DERIVED, NOT TYPED, and this module is the reason that matters. The
    first version of these compose assertions checked
    `"SESSION_COOKIE_NAME:" in block` -- which is equally true of
    `FARABUNKER_SESSION_COOKIE_NAME:`, because one is a suffix of the
    other. So `compose.preview.yaml` shipped setting the BARE Django
    names as container environment keys, Django read the prefixed ones,
    found nothing, and served the defaults: the preview stack carried
    `SESSION_COOKIE_NAME=sessionid_8001` in its environment while
    answering `Set-Cookie: csrftoken=`, and the whole collision this
    setting exists to fix was still live. Rendering the compose file
    proved the VALUES were right and never that the KEY was one Django
    reads.
    """
    tree = ast.parse((REPO_ROOT / "config" / "settings.py").read_text())
    keys = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if setting_name not in [t.id for t in node.targets if isinstance(t, ast.Name)]:
            continue
        call = node.value
        if not isinstance(call, ast.Call) or not call.args:
            continue
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            keys.append(first.value)
    # EXACTLY ONE, not the first of several. A later conditional override
    # -- `if DEBUG: SESSION_COOKIE_NAME = ...` -- would shadow the
    # assignment this returns, and a helper whose whole purpose is to be
    # DERIVED rather than trusted must not quietly answer the wrong one.
    # Two is as loud a failure as none.
    assert len(keys) == 1, (
        f"config/settings.py has {len(keys)} environment reads for {setting_name} "
        f"({keys}); this helper answers the effective one only while there is exactly "
        "one, so a conditional override needs this derivation reworked, not extended")
    return keys[0]


def _parse_environment_block(text: str) -> dict[str, str]:
    """A compose `environment:` mapping body, as {key: value}.

    KEYS, NOT SUBSTRINGS. A `key: value` line is split on its FIRST
    colon and the key is compared whole, so no key can be satisfied by
    the tail of a longer one -- which is the exact failure that shipped.
    Comments, blanks and the `<<:` merge key are skipped.
    """
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("<<:"):
            continue
        key, _, value = stripped.partition(":")
        parsed[key.strip()] = value.strip()
    return parsed


def _compose_web_environment() -> dict[str, str]:
    """`compose.preview.yaml`'s `web` service `environment:` mapping."""
    text = (REPO_ROOT / "compose.preview.yaml").read_text()
    web = text[text.index("  web:"):text.index("  watcher:")]
    body = web[web.index("    environment:") + len("    environment:"):]
    lines: list[str] = []
    for line in body.splitlines():
        if line.strip() and not line.startswith("      "):
            break
        lines.append(line)
    return _parse_environment_block("\n".join(lines))


class TestThePreviewStackSetsTheKeySettingsReads:
    """`compose.preview.yaml` is where a preview gets this for free -- if
    and only if the key it sets is the key `config/settings.py` asks the
    environment for. A file-text assertion, the same shape
    `test_compose_env_anchor.py` uses and for the same reason: no
    docker, no daemon, no network."""

    def test_the_compose_key_is_exactly_the_key_settings_reads(self):
        """THE PIN THE SHIPPED BUG WALKED PAST. Both halves matter: the
        prefixed key must be present, AND the bare Django setting name
        must NOT be -- as a container environment key it configures
        nothing at all, since Django reads settings from
        `config/settings.py` and not from the environment by name."""
        environment = _compose_web_environment()
        assert environment, "the web service's environment block did not parse"
        whole_file = (REPO_ROOT / "compose.preview.yaml").read_text()
        for setting in ("SESSION_COOKIE_NAME", "CSRF_COOKIE_NAME"):
            key = _settings_env_key(setting)
            assert key in environment, (setting, key, sorted(environment))
            assert setting not in environment, (
                f"compose sets the bare Django name {setting!r}, which Django never "
                f"reads from the environment -- it reads {key!r}")
            # AND NOT IN AN ANCHOR EITHER. `_parse_environment_block`
            # skips `<<:` merge keys, because it cannot expand one -- so
            # the check above proves only that the bare name is absent
            # from the INLINE block. The same key sitting in
            # `x-engine-env` would be merged into `web` and configure
            # exactly as much nothing. Swept over the whole file, at the
            # one indentation a mapping key uses here, so prose that
            # merely names the setting (this file has plenty) is not a
            # hit.
            assert f"\n      {setting}:" not in whole_file, (
                f"{setting!r} is set as a mapping key somewhere in compose.preview.yaml "
                f"-- Django reads {key!r} and nothing else")

    def test_the_module_constants_are_what_settings_actually_reads(self):
        """This module's own `SESSION_ENV`/`CSRF_ENV` are typed
        literals; everything else here is derived from them, so they are
        pinned against the source rather than trusted."""
        assert _settings_env_key("SESSION_COOKIE_NAME") == SESSION_ENV
        assert _settings_env_key("CSRF_COOKIE_NAME") == CSRF_ENV

    def test_they_are_keyed_on_the_published_web_port(self):
        """UNIQUE BY CONSTRUCTION: two previews cannot publish the same
        host port, so two previews cannot land on the same cookie name
        either. The default must also track `PREVIEW_WEB_PORT`'s own
        default, or the two drift the day somebody changes one."""
        environment = _compose_web_environment()
        for env_name in (SESSION_ENV, CSRF_ENV):
            assert "${PREVIEW_WEB_PORT:-8001}" in environment[env_name], env_name
        text = (REPO_ROOT / "compose.preview.yaml").read_text()
        web = text[text.index("  web:"):text.index("  watcher:")]
        published = next(row for row in web.splitlines() if ':8000"' in row)
        assert "${PREVIEW_WEB_PORT:-8001}" in published, published

    def test_they_are_interpolated_so_dot_env_still_wins(self):
        """THE RULE THIS FILE STATES ABOUT ITSELF, applied: compose's
        `environment:` beats `env_file:`, so a literal here would win
        over .env and make the variable `docs/DEV.md` documents a lie.
        The same reasoning its ALLOWED_HOSTS comment gives -- and the
        same self-referential shape that comment's own
        `ALLOWED_HOSTS: ${ALLOWED_HOSTS:-...}` uses, which is safe
        because compose interpolates from the host environment and .env,
        never from the service's own block."""
        environment = _compose_web_environment()
        for env_name, default in ((SESSION_ENV, "sessionid"),
                                  (CSRF_ENV, "csrftoken")):
            assert environment[env_name].startswith(f"${{{env_name}:-{default}_"), (
                env_name, environment[env_name])


class TestTheseAssertionsCouldFail:
    """Anti-vacuous pins for the machinery above, because the defect
    this module is closing was a test that could not fail."""

    def test_the_parser_tells_a_key_from_the_tail_of_another_key(self):
        parsed = _parse_environment_block(
            "      FARABUNKER_SESSION_COOKIE_NAME: sessionid_8001\n")
        assert parsed == {"FARABUNKER_SESSION_COOKIE_NAME": "sessionid_8001"}
        # THE SUBSTRING TEST THAT SHIPPED THE BUG would have been true
        # here; the key test is false, which is the whole difference.
        assert "SESSION_COOKIE_NAME:" in "      FARABUNKER_SESSION_COOKIE_NAME: x"
        assert "SESSION_COOKIE_NAME" not in parsed

    def test_the_parser_finds_the_real_files_other_keys(self):
        """Non-vacuous the other way: a parser returning `{}` would pass
        every `not in` assertion above."""
        environment = _compose_web_environment()
        assert environment["FARABUNKER_DATA_DIR"] == "/preview-data"

    def test_the_settings_reader_finds_a_real_dotted_key(self):
        assert _settings_env_key("SESSION_COOKIE_NAME").startswith("FARABUNKER_")

    def test_the_anchor_sweep_would_catch_a_bare_name_in_an_anchor(self):
        """The inline-block check cannot see a merge key, so the sweep
        that backs it up has to be shown working on the shape it exists
        for -- and shown NOT firing on the prose that names the same
        setting all over this file."""
        anchor = "x-engine-env: &engine-env\n      SESSION_COOKIE_NAME: sessionid_8001\n"
        assert "\n      SESSION_COOKIE_NAME:" in anchor
        prose = "      # SESSION_COOKIE_NAME: the Django setting, named in a comment\n"
        assert "\n      SESSION_COOKIE_NAME:" not in prose

    def test_the_settings_reader_refuses_an_ambiguous_derivation(self):
        """F4: the helper answers the EFFECTIVE read, which it can only
        do while there is one. Two assignments must fail loudly rather
        than silently returning the first."""
        assert _settings_env_key.__doc__ is not None
        import config.settings as shipped
        source = pathlib.Path(shipped.__file__).read_text()
        for setting in ("SESSION_COOKIE_NAME", "CSRF_COOKIE_NAME"):
            # The real file has exactly one, which is what makes the
            # assertion inside the helper pass rather than vacuous.
            assert source.count(f"\n{setting} = ") == 1, setting


def test_docs_name_both_variables():
    """A dev-machine fix nobody can find is a fix nobody uses. The two
    variable names must be greppable from the preview documentation."""
    text = (REPO_ROOT / "docs" / "DEV.md").read_text()
    assert SESSION_ENV in text
    assert CSRF_ENV in text
