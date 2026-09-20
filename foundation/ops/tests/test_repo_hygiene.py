"""Invariants about the REPOSITORY ITSELF -- what may enter a `docker
build .` context.

A sibling of `test_import_law.py` and `test_css_ownership.py`: those walk
the tree to enforce a rule about the code, this one walks it to enforce a
rule about the artefacts. `foundation/ops/` is the column with no
dependents, so a repo-wide walk cannot become a cyclic import. Later
hardening tasks extend this module with adjacent invariants about the
tree -- see task H2 of the 2026-09-10 hardening plan.
"""
from __future__ import annotations

import re

import pytest

from foundation.ops.tests._helpers import REPO_ROOT

# Every pattern a `docker build .` context must not carry, at minimum
# (task H2, security audit S3). NOT a copy of `.gitignore`: the two files
# answer different questions -- `.gitignore` asks "may this be
# committed", `.dockerignore` asks "may this enter an image layer" -- and
# only their intersection looks the same. `.env` is the clearest
# divergence: it is gitignored AND the single most damaging thing to bake
# into a layer, because `compose.yaml` makes it mandatory for `web` to
# start, so it exists on every working install.
_REQUIRED_DOCKERIGNORE_PATTERNS = (
    ".env",
    "data/",
    ".git/",
    ".claude/",
    ".superpowers/",
)


def _dockerignore_lines() -> list[str]:
    path = REPO_ROOT / ".dockerignore"
    assert path.exists(), (
        ".dockerignore is missing; `COPY . .` would bake .env and data/ "
        "into every image layer"
    )
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_the_docker_build_context_excludes_every_secret_and_data_path():
    """S3. Docker's build context is the FILESYSTEM, not `git ls-files`,
    so `.gitignore` provides no protection here at all -- parsed
    directly, never by shelling out to `docker`."""
    lines = _dockerignore_lines()
    missing = [pattern for pattern in _REQUIRED_DOCKERIGNORE_PATTERNS if pattern not in lines]
    assert missing == [], missing


def test_the_image_does_not_run_as_root():
    """S12. Root inside the container plus a read-write bind mount of the
    source is write access to code the next restart executes. `0` and
    `0:0` are root exactly as much as the name `root` is -- a
    `USER 0:0` line would satisfy a name-only check while still running
    as uid 0, so this checks the uid half of a possible `uid:gid` pair,
    not the literal string."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    user_lines = [line for line in dockerfile.splitlines() if line.startswith("USER ")]
    assert user_lines, "Dockerfile has no USER directive"
    last_user = user_lines[-1].split()[1]
    uid = last_user.split(":")[0]
    assert uid not in ("root", "0"), last_user


def test_the_production_requirements_carry_no_test_toolchain():
    """IMAGE-7 (dependency audit). The Dockerfile installs requirements.txt
    into the image the web, watcher and worker services all share --
    `pytest`/`pytest-django` (and the test-only `identity/testing.py` they
    make importable) have no reason to be in an appliance image."""
    runtime = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for package in ("pytest", "pytest-django"):
        assert package not in runtime, package


def test_the_dev_requirements_include_the_runtime_ones():
    """So `pip install -r requirements-dev.txt` is the ONE command a
    developer runs -- a split that made people run two is a split people
    get wrong."""
    dev = (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" in dev
    assert "pytest" in dev


def test_the_dockerfile_installs_only_the_runtime_requirements():
    """IMAGE-7. The dev/test toolchain file must never appear on the
    Dockerfile's `pip install` line."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements-dev.txt" not in dockerfile


def test_the_dockerfile_installs_against_the_constraints_file():
    """PIN-11. `requirements.txt` stays the human file (floors, ceilings
    and the reasons for them); `constraints.txt` is the generated, exact
    resolved set, so two builds of the same commit install the same
    versions -- see `docs/DEV.md` "Bumping a dependency".

    Anchored to the actual `RUN pip install` line via a multiline regex,
    not a bare substring search -- a comment that merely *mentions* the
    flag (as this Dockerfile's own comment used to) would satisfy a plain
    `"-c constraints.txt" in dockerfile` check with the real install line
    changed to anything at all. Only the real invocation can pass this."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^RUN pip install .*-c constraints\.txt", dockerfile, re.M), dockerfile
    assert (REPO_ROOT / "constraints.txt").exists()


@pytest.mark.xfail(
    strict=True,
    reason="llama-index-embeddings-ollama requires pytest-asyncio unconditionally; "
    "follow-up H15b removes the test toolchain from the image",
)
def test_the_constraints_file_carries_no_test_toolchain():
    """PIN-11 / IMAGE-7's other half. `requirements.txt` itself names no
    test toolchain (`test_the_production_requirements_carry_no_test_
    toolchain` above) -- but `constraints.txt` is the RESOLVED set, and
    `llama-index-embeddings-ollama` depends on `pytest-asyncio` (which
    pulls in `pytest`) unconditionally, not as an optional extra. Both
    therefore land in the production image regardless of how cleanly
    `requirements.txt`/`requirements-dev.txt` are split.

    `strict=True` so this stays LOUD: it fails every run (visible, not
    silently accepted) and would fail differently -- an unexpected pass
    -- the day the dependency is actually removed, rather than this test
    quietly going stale. See `docs/DEV.md`'s "Bumping a dependency"
    known-gap note."""
    constraints = (REPO_ROOT / "constraints.txt").read_text(encoding="utf-8").lower()
    leaked = [pkg for pkg in ("pytest==", "pytest-asyncio==") if pkg in constraints]
    assert leaked == [], leaked


def test_the_python_base_image_is_tag_pinned_with_an_owner_digest_todo():
    """IMAGE-8, this session's ruling. `python:3.12-slim` tracks whatever
    patch and Debian base it currently resolves to, and pinning it by
    digest properly (dependency audit IMAGE-8) needs a registry pull
    (`docker buildx imagetools inspect python:3.12-slim`) that this
    session's rules do not allow -- a guessed digest would not pull at
    all. The base stays tag-pinned with an explicit TODO(owner) comment
    naming the exact command, and `docs/OPERATIONS.md` records closing it
    as an owner step. `pgvector/pgvector:pg16` (compose.yaml/
    compose.preview.yaml's `db` service) IS pinned by digest already --
    see `foundation/ops/tests/test_compose_topology.py`.

    Whole-branch review, final wave: this test now accepts EITHER form --
    the bare `FROM python:3.12-slim` this session shipped, OR
    `FROM python:3.12-slim@sha256:<64 hex>` once an owner actually runs
    the named `docker buildx imagetools inspect` command and pins it.
    The TODO(owner) comment is required only in the unpinned case -- an
    owner who does the pin has closed the TODO and this test must not
    then demand a stale comment naming a command already run.
    `docs/OPERATIONS.md`'s owner step names this test as the check that
    accepts the pinned form."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(
        r"^FROM python:3\.12-slim(?:@sha256:[0-9a-f]{64})?\s*$", dockerfile, re.M,
    )
    assert match, dockerfile
    if "@sha256:" not in match.group(0):
        assert "digest: TODO(owner) pin with docker buildx imagetools inspect" in dockerfile


def test_the_dockerfile_declares_a_healthcheck():
    """IMAGE-10. `web`/`watcher`/`worker` all `depends_on: db:
    service_healthy`, so they wait for Postgres -- but nothing checked
    whether THEY came up, beyond `restart: unless-stopped` retrying a
    crash loop forever. The image carries one HTTP-liveness probe;
    compose then turns it off for the two services that serve no HTTP
    (`test_compose_topology.py`)."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "HEALTHCHECK" in dockerfile


def test_the_python_version_is_stated_once():
    """PIN-12. It was stated nowhere: no pyproject, no runtime.txt, no
    `.python-version`, and no line in `docs/DEV.md`. `3.12` is what
    actually ships (matching the Dockerfile's base) even though the
    repository's own dev `.venv` is currently on a different minor --
    `docs/DEV.md` "Which Python" records that gap and the one command to
    close it."""
    declared = (REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    from_line = next(line for line in dockerfile.splitlines() if line.startswith("FROM "))
    assert f"FROM python:{declared}" in from_line, (declared, from_line)
