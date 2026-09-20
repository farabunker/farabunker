"""What the compose files must and must not say.

Parsed as YAML rather than grepped, so an indentation change or a
reordered key cannot make an assertion pass by accident. `yaml` is
already installed -- it is a transitive dependency of the toolchain -- so
this adds no requirement.
"""
from __future__ import annotations

import yaml

from foundation.ops.tests._helpers import REPO_ROOT

_COMPOSE_FILES = ("compose.yaml", "compose.preview.yaml")


def _compose(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))


def _published_ports(service: dict) -> list[str]:
    return [str(entry) for entry in (service.get("ports") or [])]


def test_postgres_is_published_on_loopback_only():
    """S4. `"5432:5432"` with no host-IP prefix binds 0.0.0.0 -- every
    interface. Anyone on the LAN then runs `psql` with the published
    credential and reads identity_user (password hashes), django_session,
    identity_auditevent, every labelled document row and every
    entitlement grant, then writes their own superuser row: the whole
    identity/entitlement/posture system bypassed at the transport
    underneath it."""
    for name in _COMPOSE_FILES:
        db = _compose(name)["services"]["db"]
        published = _published_ports(db)
        assert published, f"{name}: db service has no published ports at all"
        for entry in published:
            assert entry.startswith("127.0.0.1:"), f"{name}: {entry}"


def test_the_database_password_is_not_a_literal_in_a_tracked_file():
    """S4's other half. A password in a tracked file is a password in a
    public repository the day this goes public -- and, because compose's
    `environment:` beats `env_file:`, an inline DATABASE_URL also made it
    NON-OVERRIDABLE from .env, so changing it meant editing a tracked
    file and no document anywhere said so.

    Only checks `web`'s DATABASE_URL, not `watcher`'s or `worker`'s --
    that is not an oversight, it is
    `test_every_service_that_talks_to_the_database_gets_the_same_url`'s
    job below, which pins all three to the same value so a check here
    against `web` alone cannot go vacuous by one service quietly
    drifting off the anchor."""
    for name in _COMPOSE_FILES:
        compose = _compose(name)
        environment = compose["services"]["db"]["environment"]
        assert "${POSTGRES_PASSWORD" in str(environment["POSTGRES_PASSWORD"]), name
        anchor_url = str(compose["services"]["web"]["environment"]["DATABASE_URL"])
        assert "${POSTGRES_PASSWORD" in anchor_url, name
        assert "farabunker:farabunker@" not in anchor_url, name


def test_every_service_that_talks_to_the_database_gets_the_same_url():
    """Anti-vacuous pin: the anchor is the mechanism, so a service that
    quietly stopped using it would read a stale URL and the test above
    would still pass by only looking at `web`."""
    for name in _COMPOSE_FILES:
        services = _compose(name)["services"]
        urls = {key: services[key]["environment"]["DATABASE_URL"]
                for key in ("web", "watcher", "worker")}
        assert len(set(map(str, urls.values()))) == 1, urls


def test_no_new_privileges_is_set_on_every_service():
    """S12. The Dockerfile's non-root `USER` stops a compromised process
    from writing the host's bind-mounted source; `no-new-privileges`
    closes the other half -- a setuid binary reached from inside the
    container can no longer be used to climb back to root even if some
    other bug hands the process a way to exec one. `db` gets this too:
    it manages its own in-container user, but nothing about that user
    needs the ability to raise privileges via a setuid binary either."""
    for name in _COMPOSE_FILES:
        services = _compose(name)["services"]
        for service, body in services.items():
            assert "no-new-privileges:true" in (body.get("security_opt") or []), f"{name}:{service}"


def test_the_db_image_is_pinned_by_digest():
    """IMAGE-8/9. `pgvector/pgvector:pg16` moves across Postgres point
    releases AND bundled extension versions, so two rebuilds of the same
    commit were not the same image. The Dockerfile's own base
    (`python:3.12-slim`) stays tag-only with an owner TODO in this
    session (see `foundation/ops/tests/test_repo_hygiene.py`); `db` here
    already has a digest from a locally cached pull, so it is pinned for
    real."""
    for name in _COMPOSE_FILES:
        image = str(_compose(name)["services"]["db"]["image"])
        assert "pgvector/pgvector:pg16@sha256:" in image, f"{name}: {image}"
        digest = image.split("@sha256:", 1)[1]
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), f"{name}: {digest}"


def test_only_the_http_service_carries_the_image_healthcheck():
    """IMAGE-10. `web`/`watcher`/`worker` all `depends_on: db:
    service_healthy` so they wait for Postgres -- but nothing checked
    whether THEY came up, beyond `restart: unless-stopped` retrying a
    crash loop forever.

    `web` is the only service that serves HTTP, so it is the only one the
    image's own `HEALTHCHECK` (Dockerfile) is right for -- it carries no
    `healthcheck:` key of its own, and inherits the image's. `watcher`
    and `worker` serve no HTTP at all and must switch the inherited probe
    OFF, or a perfectly healthy worker reads as unhealthy."""
    for name in _COMPOSE_FILES:
        services = _compose(name)["services"]
        assert "healthcheck" not in services["web"], f"{name}: web must inherit the image probe"
        for silent in ("watcher", "worker"):
            assert services[silent]["healthcheck"] == {"disable": True}, f"{name}:{silent}"
        assert services["db"]["healthcheck"]["test"], name


def test_no_service_sets_user_root_anywhere():
    """S12. The image's own `USER` already drops to a fixed non-root uid
    (10001) -- nothing in ANY compose file may override that back to
    root, including `compose.override.yaml`, which auto-merges into
    `compose.yaml` on every plain `docker compose up` with no `-f` at
    all, unlike `compose.preview.yaml`. `0` and `0:0` count as root the
    same way they do for the Dockerfile's own `USER` line above."""
    for name in (*_COMPOSE_FILES, "compose.override.yaml"):
        services = _compose(name)["services"]
        for service, body in services.items():
            user = body.get("user")
            if user is None:
                continue
            uid = str(user).split(":")[0]
            assert uid not in ("root", "0"), f"{name}:{service}={user!r}"
