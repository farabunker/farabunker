# farabunker `web` service — Django/ASGI core + module UI/API.
# See docs/adr/0006-containerization-and-isolation.md: this image is
# ephemeral; all durable state lives on host-mounted volumes, never here.
#
# PINNED BY DIGEST is the goal (dependency audit IMAGE-8): `python:3.12-slim`
# tracks whatever patch release and Debian base it currently resolves to, so
# two rebuilds of the same commit are not provably the same image. Resolving
# the current digest needs a registry pull (`docker buildx imagetools inspect
# python:3.12-slim`), which this session's rules do not allow, so the base
# stays tag-pinned with the owner step recorded below and in
# docs/OPERATIONS.md ("Pinning the Python base image by digest").
#
# digest: TODO(owner) pin with docker buildx imagetools inspect
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# libpq is needed at runtime by psycopg (even with the [binary] wheel extra,
# having the client lib present avoids surprises across base image updates).
# ffmpeg is codec tooling, not a model -- no weights, no runtime downloads,
# same category as libpq5 above: needed by the watcher (duration probe,
# tools/rag/transcode.py::probe_duration) and the worker (audio
# extraction, extract_audio/slice_audio) -- both share this image (ADR 0006
# §6, "same same-image-different-command pattern"), so it's installed once
# here rather than per-service. Image growth measured 2026-08-24 and
# recorded in ADR 0014 §12 (roughly +410-420 MB, almost all of it the codec
# dependency closure rather than the ffmpeg binary itself).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Runtime requirements ONLY (dependency audit IMAGE-7): the separate
# dev/test requirements file (pytest, pytest-django, and by extension
# identity/testing.py's direct posture-row writes) is deliberately never
# installed into this image -- see foundation/ops/tests/test_repo_hygiene.py.
#
# PIN-11 (dependency audit): requirements.txt stays the human file --
# floors, ceilings and the reasons for them -- while constraints.txt is
# the generated, exact resolved set that was verified when it was last
# regenerated, so two builds of the same commit install the same
# versions. The pip invocation below constrains its resolution against
# that generated file. See docs/DEV.md "Bumping a dependency" for how to
# regenerate it.
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

# S12: a non-root runtime user. A compromise of the Django process --
# a deserialisation bug in one of the document parsers, say; Pillow,
# pypdf, pypdfium2 and openpyxl all parse attacker-supplied uploads --
# otherwise yields root INSIDE the container, and with compose's
# read-write `.:/app` bind mount that is write access to the host's
# application source, which the next restart executes.
#
# A FIXED UID/GID (10001), not a distro-assigned one: the bind mount
# carries the HOST's ownership on Linux, so the numbers have to be
# something an operator can match with `chown` rather than something
# that changes between base-image builds. The group is created
# explicitly with the same fixed GID for the same reason -- relying on
# `useradd`'s own group-creation default ties the GID to distro
# defaults this Dockerfile does not control. `docs/OPERATIONS.md` says
# how to reconcile an existing box's data ownership.
RUN groupadd --gid 10001 farabunker \
    && useradd --create-home --uid 10001 --gid 10001 --shell /usr/sbin/nologin farabunker

COPY --chown=farabunker:farabunker . .

USER farabunker

EXPOSE 8000

# IMAGE-10: web/watcher/worker all wait on the db's healthcheck, but
# nothing checked whether THEY came up -- a failed migrate or a
# crash-looping uvicorn was invisible behind `restart: unless-stopped`.
#
# THE ROOT URL, not a new /healthz route: it is served by the landing
# view, which issues no query of its own (it reaches the database only
# through the shell's context processors -- so this is a liveness probe
# for Postgres too, which is what you want), and adding an endpoint whose
# only purpose is to be polled is a route that must then be classified in
# the identity route matrix for no user-visible gain.
#
# ANY ANSWER UNDER 500 IS ALIVE, INCLUDING A 4xx, and that takes real
# code rather than a one-liner: `urlopen` RAISES `HTTPError` for every
# 4xx/5xx, so a bare `... .status < 500` would be unreachable and a
# DisallowedHost 400 or a gate 403 -- both meaning "the app answered" --
# would exit non-zero with a traceback. 3xx is followed by urlopen
# itself.
#
# `python -c` rather than curl: curl is not in python:3.12-slim and
# installing it to answer this question would grow the image for a probe.
# The watcher and worker serve no HTTP; compose switches their healthcheck
# off with `disable: true`.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import urllib.error,urllib.request,sys\ntry:\n    code = urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).status\nexcept urllib.error.HTTPError as exc:\n    code = exc.code\nexcept Exception:\n    sys.exit(1)\nsys.exit(0 if code < 500 else 1)"]

CMD ["sh", "-c", "python manage.py migrate --noinput && uvicorn config.asgi:application --host 0.0.0.0 --port 8000"]
