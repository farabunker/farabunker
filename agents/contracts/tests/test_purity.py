"""`agents/contracts/` imports no Django. Pinned, not hoped for.

"Pure" that is only asserted in a docstring is a hope. This imports every
module in the package in a SUBPROCESS with no DJANGO_SETTINGS_MODULE set
at all -- so an accidental `from django.conf import settings` fails here
with an ImproperlyConfigured (or an unexpected `django` in sys.modules)
rather than silently making a rule-1 pure leaf dependent on a configured
Django, which would break its universal importability.

The live tripwire this catches is not hypothetical: `models/contracts/
jobkinds.py:34` imports `django.utils.module_loading`, so importing
`JobContext` for real -- rather than under `if TYPE_CHECKING:` -- turns
this test red immediately.
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
    import agents.contracts.artifacts
    import agents.contracts.attachments
    import agents.contracts.tools
    import agents.contracts.toolschema
    import agents.contracts.workstreams
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
        "import agents.contracts.artifacts",
        "import agents.contracts.artifacts\nimport django.utils.timezone",
    ))
    leaked = [m for m in result.stdout.strip().split("|") if m]
    assert "django" in leaked or "django.utils.timezone" in leaked


def test_the_moved_principal_type_keeps_this_package_pure():
    """`agents/contracts/tools.py` now imports `identity.contracts.
    principals`. That import is inside this probe's reach, so a future
    Django import added THERE fails HERE as well as in identity's own
    probe -- which is the property that makes the move safe.

    The replacement targets the line with a trailing newline, not a bare
    substring: `"import agents.contracts.tools"` is also a PREFIX of
    `"import agents.contracts.toolschema"` two lines down, so a bare
    substring replace would corrupt that line too."""
    result = _run_probe(_PROBE.replace(
        "import agents.contracts.tools\n",
        "import agents.contracts.tools\nimport identity.contracts.principals\n",
    ))
    assert result.returncode == 0, result.stderr
    assert [m for m in result.stdout.strip().split("|") if m] == []
