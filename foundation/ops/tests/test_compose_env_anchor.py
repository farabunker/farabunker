"""C-33: the four engine addresses (`DATABASE_URL`, `OLLAMA_BASE_URL`,
`COMFYUI_BASE_URL`, `WHISPER_BASE_URL`) are written once per compose file,
as a YAML anchor, and merged into each of `app`, `watcher` and `worker`
rather than repeated three times.

A pure file-text assertion, same pattern as test_docs_sync.py -- no Django
models, no docker. `scripts/tests/test_preview.py` shells out to
scripts/preview against a stub compose.preview.yaml it writes itself and
never reads the real file's `environment:` block, so nothing there pins
this; this module is the pin.

Two files, not one: `compose.yaml` and `compose.preview.yaml` stay
separate by design (ADR 0011, branch preview stacks) and a YAML anchor
cannot cross a file boundary, so each file gets its own anchor.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings

REPO_ROOT = Path(settings.BASE_DIR)


@pytest.mark.parametrize("compose_file", ["compose.yaml", "compose.preview.yaml"])
def test_the_engine_env_block_is_written_once_per_compose_file(compose_file):
    """C-33. Four engine addresses were written out three times per file,
    six times in all. An anchor per file collapses each set to one; the
    two files stay separate by design (ADR 0011) and an anchor cannot
    cross them."""
    text = (REPO_ROOT / compose_file).read_text()
    assert "&engine-env" in text
    assert text.count("<<: *engine-env") == 3
    assert text.count("COMFYUI_BASE_URL") == 1
