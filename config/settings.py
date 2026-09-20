"""
Django settings for Farabunker.

Config is read entirely from the environment (via `.env` in dev; real env vars
in containers) so the same codebase runs unmodified across posture profiles
and deployment targets — see docs/ARCHITECTURE.md §2-3 and
docs/adr/0006-containerization-and-isolation.md.
"""
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv
import os
import socket

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env for local/dev use. In containers, real env vars take precedence
# and this is a no-op if no .env file is present.
load_dotenv(BASE_DIR / ".env")

# --- Core / security ---------------------------------------------------

# The shipped development key, named as a CONSTANT so
# `identity/checks.py` and `identity.services.set_posture` can compare
# against it without either of them carrying a second copy of the
# literal -- two copies of "the insecure default" is exactly how one of
# them comes to be wrong.
DEV_SECRET_KEY = "dev-insecure-change-me"
SECRET_KEY = os.environ.get("SECRET_KEY", DEV_SECRET_KEY)
DEBUG = os.environ.get("DEBUG", "1") == "1"

# S1 (2026-09-10 security audit): this defaulted to `"*"` and was set
# NOWHERE -- not in `.env.example`, not in any compose file, not in the
# Dockerfile, not in `docs/DEV.md`'s environment table. A wildcard here
# is not a lax setting, it is the one setting that makes every other
# control on this box conditional: a LAN browser pointed at an attacker
# DNS name that re-resolves to this box sends `Host: evil.example`,
# Django serves the request instead of raising `DisallowedHost`, and the
# BROWSER then treats the response as same-origin with the attacker's
# page -- reading every body and the CSRF cookie. CSRF is not worked
# around there, it is bypassed.
#
# THE DEFAULT IS THE BOX'S OWN NAMES, NOT LOOPBACK ALONE. A LAN
# appliance is browsed at `http://<box>:8000` from another machine, so a
# loopback-only default would turn every ordinary visit into a 400 the
# day this landed -- a behaviour regression wearing a security hat.
# `socket.gethostname()` is a local syscall (no DNS, no network, no
# failure mode worth guarding) and returns exactly that name.
#
# An operator with a different name (a CNAME, an mDNS `.local` alias, a
# reverse proxy) sets `ALLOWED_HOSTS` in `.env` -- documented in
# `docs/DEV.md` §3 beside `SECURE_COOKIES`. `identity.E003` refuses a
# `"*"` written there while `DEBUG` is off; `identity.E004` refuses the
# opposite mistake, an `ALLOWED_HOSTS=` left blank, which locks out every
# visitor -- including the operator -- just as silently.
_BOX_HOSTNAME = socket.gethostname()
_DEFAULT_ALLOWED_HOSTS = ",".join(
    dict.fromkeys(h for h in ("localhost", "127.0.0.1", "[::1]", _BOX_HOSTNAME) if h)
)
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", _DEFAULT_ALLOWED_HOSTS).split(",")
    if host.strip()
]

# Declared BESIDE `ALLOWED_HOSTS` rather than left to Django's default,
# and DELIBERATELY EMPTY (S1's companion; the security audit's own
# "not-findings" section records why an empty list is CORRECT here): on a
# non-secure request Django compares `Origin` against
# `"http://" + request.get_host()`, which same-origin traffic on a direct
# plain-HTTP box already satisfies. This setting exists so the day this
# box sits behind a TLS-terminating proxy, the name an operator has to
# set is already here with its reasoning attached, rather than being
# discovered as a 403 nobody can explain.
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# The platform's user model. SET IN THE SAME COMMIT AS
# `identity/migrations/0001_initial.py`, or Django refuses to start.
# It cannot be changed once rows reference it (spec section 6.1), which
# is why `identity.User` exists now, empty, rather than later, full.
AUTH_USER_MODEL = "identity.User"

# --- Inference Gateway config (models/contracts/gateway.py reads these) --
# The single place these are declared; the gateway is the only code that
# should read them directly, so engine/model stay swappable by config.
#
# NO MODEL DEFAULTS (owner ruling, ADR 0010 third amendment): the platform
# never presumes a model. `LLM_MODEL` / `EMBED_MODEL` / `EMBED_DIM` are
# OPTIONAL explicit overrides an operator may set deliberately; unset they
# are `None`, and a role with no override and no registered connection is
# honestly UNASSIGNED (the console says so, the Ask page's 503 matrix says
# so) rather than silently pointed at a model nobody chose. A fresh install
# therefore starts empty and the cold-start onboarding flow in the model
# console -- register a model, then assign it to a role -- is the real
# first-run path.
#
# `OLLAMA_BASE_URL` deliberately KEEPS its default: a server *location* is
# deploy convention (ADR 0006 -- host Ollama on macOS dev, the Ollama
# container on the appliance), not a model choice.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# The image-generation engine's location. Same exception as OLLAMA_BASE_URL
# under the no-baked-defaults rule (ADR 0010 third amendment): a server
# *location* is deploy convention, not a model choice. There is still NO
# default checkpoint anywhere -- the operator places .safetensors files and
# binds one in the console.
COMFYUI_BASE_URL = os.environ.get("COMFYUI_BASE_URL", "http://localhost:8188")

# The transcription engine's location (media-into-RAG plan T6). Same
# exception as OLLAMA_BASE_URL/COMFYUI_BASE_URL above under the
# no-baked-defaults rule (ADR 0010 third amendment): a server *location* is
# deploy convention, not a model choice. There is still NO default model
# file anywhere -- whisper-server's own -m flag is the model choice, and it
# is the operator's to make (see models.contracts.engines.whisper.WhisperEngine
# .setup_guide).
WHISPER_BASE_URL = os.environ.get("WHISPER_BASE_URL", "http://localhost:8080")

# Where each engine adapter is polled for installed models when nothing has
# been registered yet (spec §4.6 / D11). `models.registry.discovery.discover`
# takes a per-engine endpoint map; this supplies its defaults, unioned with
# each engine's registered-connection endpoints. Keyed by the adapter's own
# `.name` -- an engine missing from this map is simply never polled by
# default (its registered connections still are).
INFERENCE_DEFAULT_ENDPOINTS = {
    "ollama": OLLAMA_BASE_URL,
    "comfyui": COMFYUI_BASE_URL,
    "whisper": WHISPER_BASE_URL,
}


def _optional_str(name: str) -> str | None:
    """An optional string env var: `None` when unset or blank.

    A blank value ("LLM_MODEL=" left in a .env) is treated as unset rather
    than as the empty model id -- the operator has not chosen anything.
    """
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _optional_int(name: str) -> int | None:
    """An optional POSITIVE integer env var: `None` when unset, blank, or
    malformed.

    A malformed value is NOT quietly replaced with a made-up number -- there
    is no safe embedding dimension to guess (a store built at a guessed
    width is corrupt by construction, see tools/rag/index.py). It reads as
    unset here and fails loudly at the point of use, with the same
    "no recorded embedding dimension" error a DB connection missing one gets.

    Zero and negatives are malformed for the same reason, not merely odd:
    the one consumer of this (`EMBED_DIM`) is a vector width, and only
    `None` triggers the fail-fast in `tools/rag/index.py` -- a `0` would
    sail past it into `PGVectorStore.from_params(embed_dim=0)` and surface
    as an opaque low-level error instead of the clean, actionable one.
    """
    raw = _optional_str(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _feature_set(name: str, default: str) -> frozenset[str]:
    """Parse a comma-separated feature-toggle env var into a set.

    An UNSET variable falls back to `default` (features ship enabled -- an
    operator opts *out*). An explicitly BLANK variable is a deliberate
    "nothing enabled", not an absence, so it does NOT fall back. Whitespace
    around names is stripped and empty entries are dropped, so
    "vision, , chat" reads as {"vision", "chat"}.
    """
    raw = os.environ.get(name)
    if raw is None:
        raw = default
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


LLM_MODEL = _optional_str("LLM_MODEL")
EMBED_MODEL = _optional_str("EMBED_MODEL")
EMBED_DIM = _optional_int("EMBED_DIM")

# Dotted path to a `BindingProvider` (models/contracts/bindings.py) that
# resolves a role to a model binding ahead of the env-only fallback. Defaults
# to the `models/` column's DB-backed provider (models/registry/bindings.py);
# the env var always wins, and an explicit empty-string override falls back
# to pure `env_provider` (e.g. for a deployment with no DB-backed registry
# provider in play).
INFERENCE_BINDING_PROVIDER = os.environ.get(
    "INFERENCE_BINDING_PROVIDER", "models.registry.bindings.db_provider"
)

# Dotted path to a module (`models/contracts/queue.py`'s seam) exposing
# `enqueue`/`get_job` that backs the execution queue (ADR 0013).
# Defaults to the `models/` column's queue implementation
# (`models/queue/backend.py`); the env var is there for tests/alternate
# topologies, same rationale as
# INFERENCE_BINDING_PROVIDER above.
INFERENCE_QUEUE_BACKEND = os.environ.get(
    "INFERENCE_QUEUE_BACKEND", "models.queue.backend"
)

# --- Feature toggles -----------------------------------------------------
# A toggle's OWN job (D9) is gating a feature app's role registration
# (tools/vision/apps.py::VisionConfig.ready(), tools/rag/apps.py) and its
# URL mount (config/urls.py). It does not stop there, and the honest
# count is FOURTEEN sites, not "nothing else".
#
# ELEVEN of them BRANCH on the set directly: the "vision" token gates the
# feature app's role registration (tools/vision/apps.py:34), the /vision/
# URL mount (config/urls.py:45), the settings sidebar's Engine files
# entry (foundation/templates/_settings.html:207) and the Images nav
# entry's role check (models/registry/context_processors.py:86); the
# "media" token gates its own app hook (tools/rag/apps.py:44), which
# upload extensions the library accepts
# (tools/rag/ingest.py::supported_exts, :155) and FIVE branches
# inside `tools/rag/ingest.py` (:168,276,380,435,547) -- staging, the
# watcher's poll loop and the manual-upload path all run through those
# same functions, so each of the five checks governs every caller that
# reaches it rather than being repeated per caller.
#
# THREE MORE READ THE SET WITHOUT BRANCHING ON IT THEMSELVES, and a
# count that omits them is not honest either (final whole-delta review
# M5): foundation/settings_area.py:221 hands it to `first_entry`, whose
# `_may_see` (:159) is what decides where a bare /settings/ redirects;
# tools/vision/context_processors.py:21 is what PUTS the set in template
# context, so _settings.html's branch above has nothing to read without
# it; and foundation/ops/backup.py:621 records it in every backup
# manifest, which is how a restore can tell which features the box it
# came from had on.
# Content already ingested before a flag was turned off is deliberately
# NOT re-gated by any of this -- see `tools/rag/jobs.py`'s own comment
# on why disabling "media" mid-life must not strand existing content.
FARABUNKER_FEATURES = _feature_set("FARABUNKER_FEATURES", "vision")

# --- Managed document store (ADR 0009) -----------------------------------
# The box owns its files: documents are copied into a managed store under
# DATA_DIR rather than referenced by their original (possibly transient)
# upload/watch-folder path. DATA_DIR should point at a durable host-mounted
# volume in production (ADR 0006 "durable data lives outside the container
# lifecycle"); it defaults to a local `data/` dir for dev.

DATA_DIR = Path(os.environ.get("FARABUNKER_DATA_DIR", BASE_DIR / "data"))
DOCUMENTS_DIR = DATA_DIR / "documents"

# Watch-inbox for browser uploads: files land here (under a per-category
# subfolder) and the `watcher` service (manage.py ingest_watch) ingests them
# out-of-band. Host-mounted under DATA_DIR (ADR 0006), gitignored via data/.
INGEST_INBOX_DIR = DATA_DIR / "inbox"

# Consolidated workstream notes (spec §10.4). UNDER `DATA_DIR`,
# DELIBERATELY NOT UNDER `INGEST_INBOX_DIR`: the watcher polls the inbox,
# and a note written there would be staged TWICE -- once by the
# consolidation job and once by the watcher, as a UNIVERSAL document with
# a service principal for an actor. `stage_document` takes any path and
# `move=False` leaves the file in place, so a directory the watcher never
# looks at costs one constant and closes the whole question.
NOTES_DIR = DATA_DIR / "notes"

# Chat-scoped attachment staging (round 12 owner ruling; round-12 fix
# verify A-1/B-4). THE IDENTICAL REASON `NOTES_DIR` ABOVE IS NOT UNDER
# `INGEST_INBOX_DIR`, for a document instead of a note: `document_upload`
# stages a `placement="conversation"` ("This chat only") upload under a
# per-conversation subdirectory of THIS setting, never under the inbox --
# a first cut nested that subdirectory INSIDE the inbox instead
# (`INGEST_INBOX_DIR/chat-<uuid>/`), which sits squarely inside the
# watcher's own `recursive=True` observer tree. `watch_folder` derives
# each file's CATEGORY from the first path segment under the inbox
# (`category_from_subfolder`), so a watcher race that won against
# `document_upload`'s own `enqueue_ingest` call (the SAME race
# `tools.rag.views.document_upload`'s own docstring already documents
# for the ordinary case) re-ingested the file as an ordinary UNIVERSAL
# document, service-owned, under a junk `chat-<uuid>` category --
# silently discarding the owner's "This chat only" choice through the
# race branch specifically, reopening round 12 review I-1's own failure
# mode one layer down. A directory the watcher never looks at costs one
# constant and closes the whole question, exactly as it already did for
# `NOTES_DIR`.
CHAT_STAGING_DIR = DATA_DIR / "chat-attachments"

# Where Django buffers a large multipart upload's file objects while
# parsing the request, before `tools.rag.views.document_upload` ever sees
# them (Django's own `FILE_UPLOAD_MAX_MEMORY_SIZE` threshold spills bigger
# uploads to disk here). Defaults to the OS temp dir, which -- inside a
# container -- can be a small overlay/tmpfs mount, and a multi-GB document
# upload must not transit that writable layer at all (ADR 0006 §3: durable
# data lives outside the container lifecycle -- the same caution applies to
# scratch space, not just the final managed-store copy). Pointed at a
# DATA_DIR subdir instead, so it shares DATA_DIR's host-mounted volume.
#
# Unlike INGEST_INBOX_DIR (mkdir'd lazily by `manage.py ingest_watch`) or
# DOCUMENTS_DIR (mkdir'd lazily by `tools.rag.store.store_file`/
# `move_file` at first write), nothing in the request-handling path gets a
# chance to mkdir this one lazily before Django's own upload handler needs
# it to already exist -- so it's created here, eagerly, at settings import,
# the one point guaranteed to run before any request does.
def _ensure_file_upload_temp_dir(path: Path) -> None:
    """Create `path`, turning a bare `PermissionError` into a refusal
    that names the fix. S12 made the container a fixed non-root user
    (uid/gid 10001); a box whose `./data` still carries root ownership
    from before that change fails HERE, at settings import -- before
    Django, `manage.py`, or any request handler exists to produce a more
    legible error -- so this is the one place worth spending the extra
    words. `docs/OPERATIONS.md` §"Upgrading to the non-root container
    user" is the full sequence this message points at."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise ImproperlyConfigured(
            f"Cannot create FILE_UPLOAD_TEMP_DIR ({path}): {exc}. This is "
            "almost always ./data still being owned by root from before "
            "this box ran the container's non-root user (S12, uid/gid "
            "10001) -- see docs/OPERATIONS.md §\"Upgrading to the "
            "non-root container user\" for the chown sequence."
        ) from exc


FILE_UPLOAD_TEMP_DIR = DATA_DIR / "tmp"
_ensure_file_upload_temp_dir(FILE_UPLOAD_TEMP_DIR)

# S9: the ONE bound on how many bytes a single request may declare. 4 GiB
# -- twice the 2 GiB per-file default of `RagSettings.max_upload_bytes`,
# so a legitimate single large media upload plus multipart overhead fits
# and a multipart body carrying twenty of them does not. Lower it on a
# box with a small data volume; `docs/OPERATIONS.md` says how.
MAX_REQUEST_BODY_BYTES = int(
    os.environ.get("FARABUNKER_MAX_REQUEST_BYTES", str(4 * 1024**3))
)

# S9: `request.FILES.getlist("files")` was unbounded in COUNT -- N files
# each just under the per-file cap were all accepted. Django's own
# setting does this without a view-level counter, which is why there
# isn't one. (Django 5.2's own default is already 100; stated explicitly
# here, lower, so the value is a fact about this box rather than an
# inherited default nobody chose.)
DATA_UPLOAD_MAX_NUMBER_FILES = 50

# Stated explicitly rather than inherited (Django's default is 2.5 MB).
# NON-FILE POST data only -- it is not, and never was, an upload cap,
# which is exactly the misreading S9 records. A form on this box has no
# legitimate reason to post two megabytes of fields.
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

# Stated explicitly for the same reason, and deliberately UNCHANGED in
# effect: this is the spill-to-disk threshold, not a cap. Lowering it
# makes uploads hit disk sooner; it bounds nothing.
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

# Managed generated-media store (spec §5): one directory per generation job,
# `<FARABUNKER_DATA_DIR>/generated/<job-uuid>/`, holding that job's inputs and
# the outputs copied back from the engine. Host-mounted under DATA_DIR
# (ADR 0006) exactly like the document store -- the engine's own output
# folder is never relied on afterwards (it may be on another machine).
GENERATED_DIR = DATA_DIR / "generated"

# S14: where `manage.py backup` writes by default. Points INSIDE the
# durable data volume, not merely "outside the source tree" -- inside the
# container `/app` IS the checkout and `/app/data` is the host-mounted
# volume, so a default anywhere else would be container-local and
# EPHEMERAL, losing every backup on `docker compose down`. That is worse
# than the exposure it would be fixing.
#
# The real fix for the exposure is the file modes (0700 directories, 0600
# files, applied explicitly in `foundation/ops/backup.py`), and the real
# answer for confidentiality is an encrypted volume, which
# `docs/OPERATIONS.md` has always asked for and this variable now makes
# possible without editing anything: point it at the mount.
BACKUP_DIR = Path(os.environ.get("FARABUNKER_BACKUP_DIR", "") or (DATA_DIR / "backups"))

# How long a job may sit queued before the page marks its card stale with a
# "check the image engine" hint (spec §6). Still polled -- stale is a hint,
# not a terminal state. A duration, not a model: a default is fine here.
#
# Semantics inherited from `_optional_int`: unset, blank, malformed, zero, or
# negative all read as "not set" and land on the 10-minute default. A zero
# staleness window would mark every job stale the instant it was created,
# which is noise, not information -- so it is deliberately NOT honoured.
VISION_STALE_AFTER = timedelta(minutes=_optional_int("VISION_STALE_AFTER_MINUTES") or 10)

# How long an upload the page staged for a queued generation is kept
# before it is swept. It is consumed within seconds in the normal case
# (the worker's `submit_job` copies the bytes into the job's own
# directory); the window exists for a submission whose queue job never
# ran -- cancelled, or the worker was down. Not deleted on consumption,
# deliberately: the queue's orphan sweep can re-run a job, and a payload
# whose references had been deleted would fail a re-run that would
# otherwise have worked.
VISION_STAGED_UPLOAD_TTL = timedelta(
    hours=_optional_int("VISION_STAGED_UPLOAD_TTL_HOURS") or 24
)

# --- Applications --------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "tools.rag",
    "tools.vision",
    "models.registry",
    "models.queue",
    "agents",
    # The `/chat/` surface. Its own app for its own templates and tests;
    # it owns no model and no migration (agents/chat/apps.py says why).
    "agents.chat",
    "foundation.setup",
    # The front door at `/`. Its own app for its own template and tests;
    # it owns no model and no migration (foundation/landing/apps.py
    # says why).
    "foundation.landing",
    "foundation.ops",
    "identity",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # S9: refuses an oversize declared `Content-Length` before ANYTHING
    # reads the body -- placed here, immediately after SecurityMiddleware
    # and before SessionMiddleware/CsrfViewMiddleware, both of which read
    # POST data for a form-encoded request (and reading POST data is what
    # triggers Django's own upload handlers). Any later position would
    # let at least one of those already spill an oversize body to disk
    # before this ever ran.
    "foundation.uploads.RequestBodyLimitMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # AFTER AuthenticationMiddleware, because it needs `request.user` --
    # and therefore after CSRF, deliberately (see the module docstring
    # in identity/middleware.py: authorisation must not be evaluated
    # before forgery).
    "identity.middleware.IdentityGateMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# A literal PATH, not a resolver name: `identity.middleware.
# refuse_anonymous` redirects through `django.contrib.auth.views.
# redirect_to_login` with no explicit `login_url`, which resolves this
# setting without ever calling `reverse()` -- so it stays correct even
# if `/identity/` were ever unmounted for a boot (`reverse
# ("identity-login")` would need the route to exist; this literal does
# not).
LOGIN_URL = "/identity/login/"
LOGIN_REDIRECT_URL = "chat-index"
# A RESOLVER NAME, unlike `LOGIN_URL` above -- safe here because Django
# only reverses `LOGOUT_REDIRECT_URL` from inside the logout VIEW
# (`identity/views.py`'s logout view, mounted at `identity-logout`),
# which never runs before that route exists.
LOGOUT_REDIRECT_URL = "identity-login"

# --- Sessions and cookies (Identity & Auth) ----------------------------
#
# A ROLLING idle window, applied per request by
# `identity.middleware.IdentityGateMiddleware` from
# `IdentitySettings.session_idle_minutes`. This setting is what makes
# the window actually roll: without it Django writes the session only
# when it changes, and the expiry the middleware set would never move.
SESSION_SAVE_EVERY_REQUEST = True

# Stated explicitly rather than left to Django's defaults, because a
# default that is right is still a default somebody can change without
# noticing.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# COOKIES IGNORE PORTS, WHICH IS A DEV PROBLEM AND NOT A PRODUCTION ONE.
# A cookie's scope is scheme + host + path -- the port is NOT part of it
# (RFC 6265 section 8.5, "cookies do not provide isolation by port") --
# so every stack a developer runs on `localhost` shares ONE jar. This
# box is routinely run four ways at once on a dev machine: the live
# stack on :8000 and up to three branch previews on :8001-:8004, all
# writing `sessionid` and `csrftoken` to `localhost`. Whichever tab
# rolls its session last wins the jar, and the others are silently
# signed out mid-click -- their server-side rows are still alive, which
# is what makes the symptom so confusing to read.
#
# NAMING THEM APART PER STACK is the whole fix. Defaults are Django's
# own, so an unset environment leaves the live box, CI and the test
# suite byte-identical; `compose.preview.yaml` sets both to
# `<name>_<PREVIEW_WEB_PORT>` so a preview never shares a jar entry with
# its siblings or with :8000, and docs/DEV.md names the two variables
# for a stack started by hand.
SESSION_COOKIE_NAME = os.environ.get("FARABUNKER_SESSION_COOKIE_NAME", "sessionid")
CSRF_COOKIE_NAME = os.environ.get("FARABUNKER_CSRF_COOKIE_NAME", "csrftoken")

# Whether this box is behind TLS is a DEPLOYMENT fact, exactly as a
# server location is -- the same exception the engine base-URL settings
# above carry under ADR 0010's no-baked-defaults rule. Forcing Secure
# cookies on would break login on a plain-HTTP LAN box, which is a
# supported posture (docs/ARCHITECTURE.md's `isolated-lan`), so this
# defaults OFF and `identity.W001` warns when accounts are on without
# it.
SECURE_COOKIES = os.environ.get("SECURE_COOKIES", "0") == "1"
SESSION_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_SECURE = SECURE_COOKIES

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "foundation" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Feature toggles in every template's context, so the shared
                # shell (foundation/templates/_shell.html) can gate its nav link to a
                # feature that may not be mounted at all (D9). Registering it
                # here rather than in the vision app keeps the shell's one
                # nav partial working on every page, including /rag/ and
                # /inference/, which know nothing about this feature.
                "tools.vision.context_processors.features",
                # `identity_posture` / `identity_is_admin` / `identity_accounts_on`
                # for the shared shell's Accounts/sign-out nav entries (IA-1 T9) --
                # registered here for the same reason the vision entry above is.
                "identity.context_processors.identity",
                # `bound_roles` / `surface_available` (UI-1): which
                # model-consuming surfaces have a model bound, so the
                # shared shell and the landing page render an entry only
                # for a destination that can actually do something.
                # Registered here, not in the app, for the same reason
                # the two above are: the shell renders on every page.
                "models.registry.context_processors.availability",
                # The settings assistant panel's context (spec §6.2).
                # Registered here for the same reason the three above are
                # -- the composition root is the one place that already
                # names every column, and `identity/`, which owns four of
                # the settings pages, may not import `agents/` at
                # all. It returns `{}` on every page that is not a
                # settings page, at ZERO queries, and `{}` again for a
                # principal the panel will not render for.
                "agents.chat.context_processors.settings_assistant",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database ------------------------------------------------------------
# Single Postgres+pgvector datastore for vectors, metadata, tabular rows,
# and chat memory (ADR 0004). Configurable via DATABASE_URL (ADR 0006).

DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get(
            "DATABASE_URL",
            "postgres://farabunker:farabunker@localhost:5432/farabunker",
        ),
        conn_max_age=600,
        # A long-lived process (the execution queue's `worker`, T4;
        # equally the `watcher`) holding a `CONN_MAX_AGE=600` connection
        # open can otherwise silently keep trying to use it even after the
        # DB itself restarts underneath it -- Postgres closes the socket,
        # but Django doesn't notice until the next query fails outright.
        # `CONN_HEALTH_CHECKS` makes Django ping a reused connection
        # (`SELECT 1`) before handing it back out and transparently
        # reconnect if that ping fails, rather than surfacing a
        # `django.db.OperationalError` mid-request. Benefits `web` too,
        # not just the long-lived processes -- added here rather than
        # per-process because there is only one `DATABASES` config.
        conn_health_checks=True,
    )
}

# --- Auth ------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- i18n ------------------------------------------------------------

LANGUAGE_CODE = "en-us"
# THE BOX'S OWN WALL CLOCK, and it is operator-facing now rather than a
# storage detail (round 21): every date/time a conversation's prompt
# states -- the clock line and each replayed message's stamp
# (`agents/runtime/prompt.py`) -- is rendered in THIS zone, and so is
# every timestamp the settings and queue pages show. Left at "UTC" the
# box is internally consistent and honestly labelled, but an operator
# at UTC-7 reads a "current time" seven hours ahead of their wall clock
# and, for seven hours of every day, tomorrow's DATE -- which is the
# same class of confusion the time-aware prompt exists to remove.
#
# DEFAULT UNCHANGED: with `FARABUNKER_TIME_ZONE` unset this is exactly
# the "UTC" it has always been, so no existing box moves. Storage is
# unaffected either way -- `USE_TZ = True` means every datetime is
# stored in UTC regardless; this governs only how one is RENDERED.
# Any name the system's own tz database knows works ("America/Denver",
# "Europe/Madrid"); an unknown one is Django's own startup error, not
# a silent fallback, which is the honest failure for a value nobody
# can guess a sensible substitute for.
TIME_ZONE = os.environ.get("FARABUNKER_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

# --- Static files ------------------------------------------------------------

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
