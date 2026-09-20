"""`identity/contracts/` imports no Django. Pinned, not hoped for.

Same shape and same reasoning as `agents/contracts/tests/test_purity.py`:
a subprocess with no DJANGO_SETTINGS_MODULE set at all, so an accidental
`from django.conf import settings` fails here with an
ImproperlyConfigured (or an unexpected `django` in `sys.modules`) rather
than silently making a rule-1 pure leaf depend on a configured Django.

The live tripwire is not hypothetical: `identity/contracts/ownership.py`
names its models as `app_label.ModelName` strings PRECISELY so it never
has to `from django.apps import apps` at module scope. Doing so would
turn this test red immediately.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from django.conf import settings

REPO_ROOT = Path(settings.BASE_DIR)

_PROBE = textwrap.dedent("""
    import sys
    import identity.contracts.principals
    import identity.contracts.postures
    import identity.contracts.actions
    import identity.contracts.ownership
    import identity.contracts.cascades
    import identity.contracts.axes
    leaked = sorted(m for m in sys.modules if m == "django" or m.startswith("django."))
    print("|".join(leaked))
""")


def _run_probe(script: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
    )


def test_the_whole_package_imports_with_no_django_configured():
    result = _run_probe(_PROBE)
    assert result.returncode == 0, result.stderr


def test_importing_it_pulls_in_no_django_module_at_all():
    result = _run_probe(_PROBE)
    leaked = [m for m in result.stdout.strip().split("|") if m]
    assert leaked == [], leaked


def test_the_probe_would_actually_notice_a_django_import():
    """Anti-vacuous pin: a probe that cannot fail proves nothing."""
    result = _run_probe(_PROBE.replace(
        "import identity.contracts.principals",
        "import identity.contracts.principals\nimport django.utils.timezone",
    ))
    leaked = [m for m in result.stdout.strip().split("|") if m]
    assert "django" in leaked or "django.utils.timezone" in leaked
