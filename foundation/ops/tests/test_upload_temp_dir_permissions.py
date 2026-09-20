"""S12 follow-on: a fresh Linux install (or an existing box that hasn't
run the `chown` in `docs/OPERATIONS.md` yet) leaves `./data` owned by
root, and `config.settings.FILE_UPLOAD_TEMP_DIR` is created EAGERLY, at
settings-import time, before any request or management command runs.

`Path.mkdir` raising a bare `PermissionError` at import time is one of
the least legible failures a Django box can produce -- a traceback with
no mention of `chown`, no mention of the uid this box now runs as, deep
in Django's own settings-loading machinery. `config.settings` wraps that
one `mkdir` in a small, directly-testable helper
(`_ensure_file_upload_temp_dir`) so the fix is named in the error itself
rather than left for someone to look up.

This module lives in `foundation/ops/tests/`, not a `config/tests/`
package (none exists -- `config/` is the Django project's settings
module, not a column with its own test suite), for the same reason
`test_repo_hygiene.py` and `test_compose_topology.py` do: it is a
hardening-plan invariant about how this box behaves under the S12
non-root user, not app-specific behaviour.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings import _ensure_file_upload_temp_dir


class _RaisesOnMkdir(Path):
    """A Path stand-in whose `mkdir` raises `PermissionError`, the way a
    real `Path.mkdir` does against a directory this process's uid does
    not own -- without touching the filesystem at all."""

    def mkdir(self, *args, **kwargs):  # noqa: D102 - trivial
        raise PermissionError(13, "Permission denied")


def test_a_permission_error_becomes_an_actionable_refusal():
    bad_path = _RaisesOnMkdir("/nonexistent/data/tmp")
    with pytest.raises(ImproperlyConfigured) as excinfo:
        _ensure_file_upload_temp_dir(bad_path)
    message = str(excinfo.value)
    assert str(bad_path) in message
    assert "10001" in message
    assert "chown" in message


def test_a_normal_mkdir_is_left_alone(tmp_path):
    """The happy path -- an already-writable directory -- must still
    work exactly as `Path.mkdir(parents=True, exist_ok=True)` did before
    this wrapper existed."""
    target = tmp_path / "tmp"
    _ensure_file_upload_temp_dir(target)
    assert target.is_dir()
    # exist_ok=True: calling it again on the same, now-existing directory
    # must not raise.
    _ensure_file_upload_temp_dir(target)
