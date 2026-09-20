# Image Generation (Vision Track) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/vision/` page where the operator types a prompt and gets an image, driven through the existing model-management grammar — a new ComfyUI engine adapter, a new `image-generation` capability, and a new `vision.generate` role bound in the `/inference/` console.

**Architecture:** A platform-level *operation* registry (`core/inference/operations.py`) defines `txt2img` and its parameter schema; the ComfyUI adapter (`core/inference/engines/comfyui.py` + `comfyui_workflows/`) maps that operation onto a ComfyUI API-format graph and submits it over HTTP; `modules/vision` owns the job records, the managed output store under `data/generated/`, the service layer (`preflight`/`submit_job`/`refresh_job`/`wait_for`/`delete_job`), and the poll-driven page. Nothing above the engine line knows a ComfyUI node exists.

**Tech Stack:** Django 5.2, Postgres, httpx, pytest + pytest-django, Docker Compose, ComfyUI running natively on the host.

**Spec:** `docs/superpowers/specs/2026-08-22-image-generation-design.md` (§9 "Deferred" is explicitly out of scope for this plan)

**Execution order: 1–13, 16, 17, 14, 15.** Tasks 16 and 17 (the universal in-app **Setup page**, an owner requirement added 2026-08-23) were appended after the numbering was already in flight; they execute before the documentation and live-verification tasks so those cover them. Every other task number is unchanged.

## Global Constraints

- **Tests and docs are core deliverables of every task, not afterthoughts** (ADR 0008): each task's steps include its unit tests and the doc edits it implies. No task is done until its tests pass and its docs are updated.
- **Test runner:** `<repo>/.venv/bin/pytest -q`, run **natively** — never `docker exec`. Postgres must be up for `django_db` tests: `docker compose up -d db`.
- **Mock at the HTTP layer, never the engine's own methods away.** Engine tests patch `core.inference.engines.comfyui.httpx.get` / `.post` so the adapter's real parsing is exercised.
- **NO `conftest.py` anywhere** (the repo forbids it). Shared test code lives in plain importable `_helpers.py` modules; autouse fixtures are *defined* per test module and delegate their bodies to those helpers. `@pytest.mark.django_db` goes at **class** level.
- **No baked model defaults:** no default model/checkpoint name anywhere in code, settings, or `.env.example`. `COMFYUI_BASE_URL` default `http://localhost:8188` is the one allowed default (a *location*, exactly like `OLLAMA_BASE_URL`).
- **The platform never downloads models or assets.** Operators place checkpoint files; the platform lists and uses them.
- **Content-agnostic:** no prompt or output filtering code paths, no model inspection.
- **Layering:** `core/` never imports `console/` or `modules/` at module scope. Engines are stateless — `endpoint` is data passed per call, never read from settings. ComfyUI graph vocabulary (node class names, node ids, `/prompt` payload shape) never leaves `core/inference/engines/comfyui*`.
- **Offline UI:** all CSS/JS inline, no external assets, no CDN. Every page works with JS disabled (polling is the only enhancement).
- **Track coordination:** files under `console/inference/*`, `core/inference/bindings.py`, `config/settings.py`, and `docs/DEV.md` belong to the other active worktree (`worktree-model-management-framework`). **Tasks 1 and 2 are the only commits that touch them** (spec §4.6) — land and announce them first so the other track can rebase. No later task edits those files again, except **additive new sections** in `docs/DEV.md` in Task 14.
- **Commit messages:** conventional style (`feat(inference): …`, `feat(vision): …`, `docs(vision): …`), and every commit message ends with exactly these two trailer lines:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  ```
  Use the `git commit -F - <<'EOF'` form shown in every commit step so the trailers land verbatim.
- **Pinned names — use exactly these:** capability `"image-generation"`; role constant `VISION_GENERATE_ROLE = "vision.generate"`; engine `name = "comfyui"`, `well_known_ports = (8188,)`; settings `COMFYUI_BASE_URL`, `INFERENCE_DEFAULT_ENDPOINTS`, `FARABUNKER_FEATURES`, `GENERATED_DIR`, `VISION_STALE_AFTER`; migration `0003_modelconnection_config`; modules `core/inference/operations.py`, `core/inference/engines/comfyui.py`, `core/inference/engines/comfyui_workflows/{__init__,txt2img}.py`; base additions `Asset`, `JobStatus`, `ImageGenerator`, `GenerationRejected`; `gateway.get_image_generator`; app `modules/vision` with models `GenerationJob` (UUID pk), `JobInput`, `GeneratedOutput`; services `preflight`/`submit_job`/`refresh_job`/`wait_for`/`delete_job`; exception `VisionUnavailable`; URL names `vision-create`, `vision-generate`, `vision-job-status`, `vision-job-delete`, `vision-gallery`, `vision-output-file`.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `config/settings.py` | `COMFYUI_BASE_URL`, `INFERENCE_DEFAULT_ENDPOINTS`, `FARABUNKER_FEATURES`, `GENERATED_DIR`, `VISION_STALE_AFTER`, `INSTALLED_APPS` | 1 |
| `config/urls.py` | feature-gated `/vision/` mount | 1 |
| `core/inference/roles.py` | `"image-generation"` capability + `VISION_GENERATE_ROLE` | 1 |
| `console/inference/discovery.py` | per-engine discovery endpoints | 2 |
| `console/inference/views.py` | `_engine_endpoints`, per-row endpoint, capability phrase | 2 |
| `console/inference/models.py` + `migrations/0003_modelconnection_config.py` | nullable `config` JSONField | 2 |
| `console/inference/bindings.py` | pass `config` through to `ResolvedModel` | 2 |
| `core/inference/operations.py` | `Param`, `Operation`, registry, `validate_params`, `GenerationRequest`, `TXT2IMG` | 3 |
| `core/inference/engines/base.py` | `Asset`, `JobStatus`, `GenerationRejected`, `ImageGenerator`, optional `InferenceEngine` members | 4 |
| `core/inference/engines/comfyui.py` | health, listing, assets, choices, operations, generator factory | 5, 7 |
| `core/inference/engines/comfyui_workflows/` | graph templates (`txt2img`) | 6 |
| `core/inference/gateway.py` | `get_image_generator` | 8 |
| `modules/vision/{apps,models,store}.py` + `migrations/0001_initial.py` | app registration, job records, managed store | 1, 9 |
| `modules/vision/context_processors.py` | `farabunker_features` in every template's context | 1 |
| `templates/_shell.html` | feature-gated **Generate** entry in the shared nav | 11 |
| `modules/vision/services.py` | the chatbot-ready service surface | 10 |
| `modules/vision/{forms,views,urls}.py` + `templates/vision/` | the page | 11, 12, 13 |
| `core/inference/engines/ollama.py` | Ollama's `setup_guide` + `serves_capabilities` | 16 |
| `console/setup/` (`apps.py`, `urls.py`, `views.py`, `templates/setup/index.html`, `tests/`, `README.md`) | the universal `/setup/` page, one section per registered engine | 1 (skeleton), 17 |
| `docs/adr/0012-image-generation-engine-adapter.md`, `docs/DEV.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `modules/vision/README.md` | documentation | 14 |

---

### Task 1: Platform config, feature flag, and the `modules/vision` app skeleton

**Suggested implementer tier:** opus — cross-cutting settings/URL/app-registry change in another track's files; a mistake here breaks every test in the repo.

**Files:**
- Modify: `config/settings.py` (new settings, `INSTALLED_APPS`, `TEMPLATES` context processor)
- Modify: `config/urls.py`
- Modify: `core/inference/roles.py` (`CAPABILITIES`, `VISION_GENERATE_ROLE`)
- Create: `modules/vision/__init__.py`, `modules/vision/apps.py`, `modules/vision/urls.py`, `modules/vision/context_processors.py`, `modules/vision/migrations/__init__.py`, `modules/vision/tests/__init__.py`
- Create: `console/setup/__init__.py`, `console/setup/apps.py`, `console/setup/urls.py`, `console/setup/views.py`, `console/setup/templates/setup/index.html`, `console/setup/tests/__init__.py` (a NEW app — `console/setup/*` is not `console/inference/*`, so it is not the other track's territory)
- Modify: `.env.example`, `compose.yaml`, `compose.preview.yaml`, `scripts/preview`
- Test: `modules/vision/tests/test_config.py` (create), `console/setup/tests/test_views.py` (create), `scripts/tests/test_preview.py` (extend)

**Interfaces:**
- Produces:
  - `settings.COMFYUI_BASE_URL: str` (default `"http://localhost:8188"`)
  - `settings.INFERENCE_DEFAULT_ENDPOINTS: dict[str, str]` — `{"ollama": OLLAMA_BASE_URL, "comfyui": COMFYUI_BASE_URL}`
  - `settings.FARABUNKER_FEATURES: frozenset[str]` — parsed from a comma-separated env var, default `"vision"` (an operator opts *out*)
  - `settings.GENERATED_DIR: Path` — `DATA_DIR / "generated"`
  - `settings.VISION_STALE_AFTER: timedelta` — default 10 minutes
  - `core.inference.roles.CAPABILITIES` now contains `"image-generation"`; `core.inference.roles.VISION_GENERATE_ROLE == "vision.generate"`
  - Django app `modules.vision` (label `vision`) with an empty `urlpatterns`, mounted at `/vision/` only when the feature is enabled.
  - `modules.vision.context_processors.features(request) -> {"farabunker_features": settings.FARABUNKER_FEATURES}`, registered in `TEMPLATES[0]["OPTIONS"]["context_processors"]` so **every** rendered page (not just `/vision/`) can gate the shared shell's nav link on the flag. The context key is `farabunker_features` everywhere.
  - Django app `console.setup` (label `setup`) mounted at `/setup/`, URL name `setup-index`, answering 200 from a minimal template. Task 17 grows it into the real page; the skeleton exists now so `config/settings.py` and `config/urls.py` are touched exactly once, in this commit.

- [ ] **Step 1: Write the failing tests**

Create `modules/vision/tests/__init__.py` (empty file) and `modules/vision/tests/test_config.py`:

```python
"""Unit tests for the vision feature's platform configuration (spec §4.6).

Covers the settings this track adds, the feature-flag parser, the
feature-gated `/vision/` URL mount, and the new platform capability. No DB,
no HTTP -- pure settings/registry facts.
"""
from __future__ import annotations

import importlib

import pytest
from django.conf import settings
from django.test import Client, RequestFactory, override_settings
from django.urls import clear_url_caches, reverse

from config.settings import _feature_set
from core.inference.roles import CAPABILITIES, VISION_GENERATE_ROLE, RoleSpec
from modules.vision.context_processors import features


class TestFeatureSet:
    def test_unset_falls_back_to_the_default_list(self, monkeypatch):
        monkeypatch.delenv("FARABUNKER_FEATURES", raising=False)
        assert _feature_set("FARABUNKER_FEATURES", "vision") == frozenset({"vision"})

    def test_comma_separated_values_are_split_and_stripped(self, monkeypatch):
        monkeypatch.setenv("FARABUNKER_FEATURES", " vision , chat ")
        assert _feature_set("FARABUNKER_FEATURES", "vision") == frozenset({"vision", "chat"})

    def test_blank_value_disables_everything(self, monkeypatch):
        """An operator opts OUT by setting the variable to empty -- that is a
        deliberate choice, not 'unset', so it must not fall back."""
        monkeypatch.setenv("FARABUNKER_FEATURES", "")
        assert _feature_set("FARABUNKER_FEATURES", "vision") == frozenset()


class TestVisionSettings:
    def test_comfyui_base_url_defaults_to_the_local_server_location(self):
        assert settings.COMFYUI_BASE_URL

    def test_default_endpoints_map_covers_both_engines(self):
        assert settings.INFERENCE_DEFAULT_ENDPOINTS["ollama"] == settings.OLLAMA_BASE_URL
        assert settings.INFERENCE_DEFAULT_ENDPOINTS["comfyui"] == settings.COMFYUI_BASE_URL

    def test_generated_dir_sits_under_the_durable_data_dir(self):
        assert settings.GENERATED_DIR == settings.DATA_DIR / "generated"

    def test_vision_stale_after_is_ten_minutes_by_default(self):
        assert settings.VISION_STALE_AFTER.total_seconds() == 600

    def test_a_zero_or_malformed_window_falls_back_to_the_default(self, monkeypatch):
        """`_optional_int` reads zero/negative/malformed as "not set" -- a
        zero-minute window would mark every job stale the moment it was
        created. Pin that inherited behaviour rather than leaving it
        accidental."""
        from datetime import timedelta

        for raw in ("0", "-5", "soon", ""):
            monkeypatch.setenv("VISION_STALE_AFTER_MINUTES", raw)
            from config.settings import _optional_int

            assert timedelta(minutes=_optional_int("VISION_STALE_AFTER_MINUTES") or 10) == timedelta(
                minutes=10
            )

    def test_vision_app_is_installed(self):
        assert "modules.vision" in settings.INSTALLED_APPS


class TestImageGenerationCapability:
    def test_capability_is_part_of_the_platform_vocabulary(self):
        assert "image-generation" in CAPABILITIES

    def test_role_key_constant(self):
        assert VISION_GENERATE_ROLE == "vision.generate"

    def test_a_role_can_declare_the_capability(self):
        spec = RoleSpec(VISION_GENERATE_ROLE, "Image generation", "image-generation")
        assert spec.capability == "image-generation"


def _mounted_prefixes() -> list[str]:
    from config import urls as config_urls

    return [str(pattern.pattern) for pattern in config_urls.urlpatterns]


@pytest.mark.django_db
class TestFeatureContextProcessor:
    """The shared shell's nav link to /vision/ is feature-gated, so EVERY
    page -- not just this module's -- needs the flag in its context."""

    def test_the_processor_returns_the_feature_set(self):
        request = RequestFactory().get("/")
        assert features(request) == {"farabunker_features": settings.FARABUNKER_FEATURES}

    def test_it_is_registered_so_rendered_pages_see_the_key(self):
        response = Client().get(reverse("rag-ask-page"))
        assert response.context["farabunker_features"] == settings.FARABUNKER_FEATURES


class TestVisionUrlMount:
    def test_mounted_while_the_feature_is_enabled(self):
        assert ("vision/" in _mounted_prefixes()) == ("vision" in settings.FARABUNKER_FEATURES)

    def test_not_mounted_when_the_feature_is_off(self):
        """The flag has exactly one job (D9): gate role registration and the
        URL mount. Reloading the URLConf under an overridden setting is the
        only honest way to prove the gate -- restored in `finally` so the
        rest of the suite keeps the real URLConf."""
        from config import urls as config_urls

        with override_settings(FARABUNKER_FEATURES=frozenset()):
            importlib.reload(config_urls)
            clear_url_caches()
            try:
                assert "vision/" not in _mounted_prefixes()
            finally:
                importlib.reload(config_urls)
                clear_url_caches()
```

Create `console/setup/tests/__init__.py` (empty file) and `console/setup/tests/test_views.py`:

```python
"""Unit tests for the universal setup page (console/setup/views.py).

Task 1 only proves the page exists and answers; Task 17 fills in the engine
sections, the role table, and the nav entry, and extends this file.
"""
from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
class TestSetupPageSkeleton:
    def test_get_returns_200(self):
        response = Client().get(reverse("setup-index"))
        assert response.status_code == 200

    def test_it_is_mounted_at_setup(self):
        assert reverse("setup-index") == "/setup/"
```

Add to `scripts/tests/test_preview.py`, inside the dry-run test that already asserts the data dirs (the block asserting `postgres`/`documents`/`inbox`):

```python
        assert (data_dir / "generated").is_dir()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest modules/vision/tests/test_config.py console/setup/tests/test_views.py scripts/tests/test_preview.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.vision'` (and `ImportError: cannot import name '_feature_set'`, `NoReverseMatch: 'setup-index'`).

- [ ] **Step 3: Add the settings**

In `config/settings.py`, add `from datetime import timedelta` to the imports at the top, then add after the `OLLAMA_BASE_URL` block (keeping its "server *location*" comment intact):

```python
# The image-generation engine's location. Same exception as OLLAMA_BASE_URL
# under the no-baked-defaults rule (ADR 0010 third amendment): a server
# *location* is deploy convention, not a model choice. There is still NO
# default checkpoint anywhere -- the operator places .safetensors files and
# binds one in the console.
COMFYUI_BASE_URL = os.environ.get("COMFYUI_BASE_URL", "http://localhost:8188")

# Where each engine adapter is polled for installed models when nothing has
# been registered yet (spec §4.6 / D11). `console.inference.discovery.discover`
# takes a per-engine endpoint map; this supplies its defaults, unioned with
# each engine's registered-connection endpoints. Keyed by the adapter's own
# `.name` -- an engine missing from this map is simply never polled by
# default (its registered connections still are).
INFERENCE_DEFAULT_ENDPOINTS = {
    "ollama": OLLAMA_BASE_URL,
    "comfyui": COMFYUI_BASE_URL,
}
```

Add below `_optional_int` (it uses the same "explicit blank means the operator meant it" rule):

```python
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


# Feature toggles (D9). A toggle has exactly ONE job: gate a feature app's
# role registration (modules/vision/apps.py) and its URL mount
# (config/urls.py). Nothing else branches on it.
FARABUNKER_FEATURES = _feature_set("FARABUNKER_FEATURES", "vision")
```

Add after `INGEST_INBOX_DIR`:

```python
# Managed generated-media store (spec §5): one directory per generation job,
# `<FARABUNKER_DATA_DIR>/generated/<job-uuid>/`, holding that job's inputs and
# the outputs copied back from the engine. Host-mounted under DATA_DIR
# (ADR 0006) exactly like the document store -- the engine's own output
# folder is never relied on afterwards (it may be on another machine).
GENERATED_DIR = DATA_DIR / "generated"

# How long a job may sit queued before the page marks its card stale with a
# "check the image engine" hint (spec §6). Still polled -- stale is a hint,
# not a terminal state. A duration, not a model: a default is fine here.
#
# Semantics inherited from `_optional_int`: unset, blank, malformed, zero, or
# negative all read as "not set" and land on the 10-minute default. A zero
# staleness window would mark every job stale the instant it was created,
# which is noise, not information -- so it is deliberately NOT honoured.
VISION_STALE_AFTER = timedelta(minutes=_optional_int("VISION_STALE_AFTER_MINUTES") or 10)
```

Add `"modules.vision",` to `INSTALLED_APPS`, right after `"modules.rag",`, and `"console.setup",` right after `"console.inference",`.

Register the feature context processor in `TEMPLATES[0]["OPTIONS"]["context_processors"]`, after the messages processor:

```python
                "django.contrib.messages.context_processors.messages",
                # Feature toggles in every template's context, so the shared
                # shell (templates/_shell.html) can gate its nav link to a
                # feature that may not be mounted at all (D9). Registering it
                # here rather than in the vision app keeps the shell's one
                # nav partial working on every page, including /rag/ and
                # /inference/, which know nothing about this feature.
                "modules.vision.context_processors.features",
```

- [ ] **Step 4: Add the capability and role key**

In `core/inference/roles.py`:

```python
CAPABILITIES = {"chat", "embeddings", "vision", "image-generation"}
```

and below `RAG_EMBED_ROLE`:

```python
# The first non-RAG role (ADR 0010's own validation case for the framework),
# registered by `modules.vision` when the "vision" feature is enabled. Lives
# here beside the RAG role keys for the same reason they do: `core/` is the
# one place both core and `modules/` can reach a single definition.
VISION_GENERATE_ROLE = "vision.generate"
```

- [ ] **Step 5: Create the app skeleton**

`modules/vision/__init__.py` — empty file.

`modules/vision/migrations/__init__.py` — empty file.

`modules/vision/apps.py`:

```python
"""The image-generation module (spec §4.7; see modules/vision/README.md)."""
from __future__ import annotations

from django.apps import AppConfig
from django.conf import settings

FEATURE = "vision"


class VisionConfig(AppConfig):
    """Registers the `vision.generate` role and the operations this app
    serves -- but ONLY while the "vision" feature is enabled (D9), so an
    operator who opted out sees no role in the console and no `/vision/`
    page. Role and operation registration land in a later task; this
    skeleton exists so `INSTALLED_APPS` and the URL mount have something
    real to point at."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.vision"
    label = "vision"

    def ready(self) -> None:
        if FEATURE not in settings.FARABUNKER_FEATURES:
            return
```

`modules/vision/urls.py`:

```python
"""URL routes for the vision module, mounted at /vision/ (config/urls.py)."""
from django.urls import path

urlpatterns: list[path] = []
```

`modules/vision/context_processors.py`:

```python
"""Template context processors for the vision feature.

Registered in `TEMPLATES[0]["OPTIONS"]["context_processors"]` (config/settings.py)
so the SHARED shell (templates/_shell.html) can decide whether to render its
nav link to `/vision/`: with the feature off there is no route at all, and a
nav entry pointing at a URL that does not exist would 404. A context
processor is the one mechanism that reaches every rendered page -- including
/rag/ and /inference/, which must not import this module for anything else.

No DB, no HTTP: it hands back the already-parsed setting, nothing more.
"""
from __future__ import annotations

from django.conf import settings


def features(request) -> dict:
    """`{"farabunker_features": settings.FARABUNKER_FEATURES}` -- the
    frozenset of enabled feature names, under the one key every template
    reads it by."""
    return {"farabunker_features": settings.FARABUNKER_FEATURES}
```

Also create the `console/setup` skeleton — the universal setup page's app, so `config/` is edited exactly once:

`console/setup/__init__.py` and `console/setup/tests/__init__.py` — empty files.

`console/setup/apps.py`:

```python
"""The universal setup page — see console/setup/README.md."""
from __future__ import annotations

from django.apps import AppConfig


class SetupConfig(AppConfig):
    """Explains how to install every registered model engine and how a model
    reaches a role. Holds no models and registers no roles: it is a pure
    reading surface over `core.inference.engines` and the role registry."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "console.setup"
    label = "setup"
```

`console/setup/views.py` (minimal for now — Task 17 fills it in):

```python
"""The universal setup page (/setup/)."""
from __future__ import annotations

from django.views.generic import TemplateView


class SetupView(TemplateView):
    """GET /setup/ -- how to install an engine, how a model reaches a role."""

    template_name = "setup/index.html"
```

`console/setup/urls.py`:

```python
"""URL routes for the setup page, mounted at /setup/ (config/urls.py)."""
from django.urls import path

from console.setup.views import SetupView

urlpatterns = [
    path("", SetupView.as_view(), name="setup-index"),
]
```

`console/setup/templates/setup/index.html`:

```html
{% extends "_shell.html" %}
{% comment %}
The universal setup page. Task 17 renders one section per registered engine
from each adapter's own `setup_guide`; this skeleton exists so `config/urls.py`
and `config/settings.py` are touched exactly once, in the coordination commit.
{% endcomment %}
{% block title %}Setup — farabunker{% endblock %}
{% block content %}
<h1>Setup</h1>
<p>How to install a model engine and connect it to farabunker.</p>
{% endblock %}
```

- [ ] **Step 6: Mount the feature-gated URLs**

`config/urls.py`:

```python
"""Farabunker URL configuration. Modules mount their own UI/API surface here."""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("rag/", include("modules.rag.urls")),
    path("inference/", include("console.inference.urls")),
    # The universal setup page: how to install every registered engine. Not
    # feature-gated -- it explains the engines themselves, which exist
    # regardless of which features are enabled.
    path("setup/", include("console.setup.urls")),
]

# Feature-gated mount (D9): with the "vision" feature off, the page simply
# does not exist -- no route, no role, no half-wired UI.
if "vision" in settings.FARABUNKER_FEATURES:
    urlpatterns.append(path("vision/", include("modules.vision.urls")))
```

- [ ] **Step 7: Wire the environment, compose files, and preview script**

`.env.example` — append:

```bash
# Image-generation engine (ComfyUI) location. Runs NATIVELY on the host so it
# can reach the GPU (Metal/CUDA/ROCm); the web container reaches it via
# host.docker.internal, same contract as Ollama. Start ComfyUI with
# `--listen 0.0.0.0` or it will only accept connections from the host itself.
# There is NO default checkpoint: place .safetensors files in
# ComfyUI/models/checkpoints/ and bind one to the Image generation role at
# /inference/.
COMFYUI_BASE_URL=http://localhost:8188

# Feature toggles: comma-separated, default "vision" (enabled). Set it to an
# empty value to opt OUT -- that removes the vision.generate role and the
# /vision/ page entirely.
# FARABUNKER_FEATURES=vision

# How long a queued generation job may sit before its card is marked stale
# (minutes, default 10).
# VISION_STALE_AFTER_MINUTES=10
```

`compose.yaml` — in the durable-data header comment, add under `./data/inbox`:

```yaml
#   ./data/generated  — the managed generated-media store (one dir per job),
#                       reached inside the web container at /app/data/generated
```

and add to **both** the `web` and `watcher` services' `environment:` blocks, right after `OLLAMA_BASE_URL`:

```yaml
      COMFYUI_BASE_URL: http://host.docker.internal:8188
```

`compose.preview.yaml` — add the same `COMFYUI_BASE_URL: http://host.docker.internal:8188` line to both services' `environment:` blocks, and extend the header's Ollama paragraph with:

```yaml
# ComfyUI is not isolated either, for the same reason: it runs natively on the
# host and every preview reaches the SAME instance at
# http://host.docker.internal:8188. The platform never downloads checkpoints.
```

`scripts/preview` — in `create_data_dirs`, extend the `mkdir -p` line:

```bash
  mkdir -p "$data_dir/postgres" "$data_dir/documents" "$data_dir/inbox" "$data_dir/generated"
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/pytest modules/vision/tests/test_config.py console/setup/tests/test_views.py scripts/tests/test_preview.py -q`
Expected: PASS.

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all PASS — nothing else reads the new settings yet, and the new capability only widens `CAPABILITIES`.

- [ ] **Step 10: Commit**

```bash
git add config/settings.py config/urls.py core/inference/roles.py modules/vision console/setup \
        .env.example compose.yaml compose.preview.yaml scripts/preview scripts/tests/test_preview.py
git commit -F - <<'EOF'
feat(inference): image-generation capability, vision feature flag, setup page mount

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 2: Per-engine discovery endpoints + `ModelConnection.config`

**Suggested implementer tier:** opus — a signature change to shared console code with ~25 existing call sites and endpoint-scoping logic that must keep behaving identically for Ollama.

**Files:**
- Modify: `console/inference/discovery.py` (`DiscoveryRow`, `discover`)
- Modify: `console/inference/views.py` (`_engine_endpoints`, `_build_context`, `_installed_views`, `_CAPABILITY_NEED_PHRASES`)
- Modify: `console/inference/models.py` (`ModelConnection.config`)
- Create: `console/inference/migrations/0003_modelconnection_config.py`
- Modify: `console/inference/bindings.py` (`db_provider` passes `config`)
- Modify: `console/inference/templates/inference/_installed_row.html` (per-row endpoint on the register form)
- Modify: `console/inference/README.md`
- Test: `console/inference/tests/test_discovery.py`, `console/inference/tests/test_db_bindings.py`, `console/inference/tests/test_views.py`

**Interfaces:**
- Consumes: `settings.INFERENCE_DEFAULT_ENDPOINTS` (Task 1).
- Produces:
  - `discover(endpoints: dict[str, list[str]], connections: list[ModelConnection]) -> list[DiscoveryRow]` — per-engine endpoint lists; an engine absent from the map is not polled.
  - `DiscoveryRow.endpoint: str` — the endpoint an installed row was seen at (`""` for catalog/connection-only rows).
  - `console.inference.views._engine_endpoints(connections, endpoint) -> dict[str, list[str]]`.
  - `ModelConnection.config: dict | None` (JSONField, `null=True, blank=True`), surfacing as `ResolvedModel.config` (`{}` when null).

> **Why `_default_endpoint()` survives:** the spec writes `_default_endpoint()` → `_engine_endpoints(connections)`. `_default_endpoint()` is *also* the console's health-check target, its `?endpoint=` override baseline, its scan baseline, and the endpoint every row/connection comparison is scoped to. Deleting it would reshape the whole console page — exactly what §10 asks this track not to do. So `_engine_endpoints` is **added** for discovery's benefit and takes the current page endpoint as its `endpoint` argument, so every engine is still polled at the address the operator is looking at (today's behaviour, as a superset).

- [ ] **Step 1: Write the failing tests**

Append to `console/inference/tests/test_discovery.py` (the `_stub_engine` helper and `ENDPOINT` constant already exist at the top of that file):

```python
# --- discover(): per-engine endpoints (spec §4.6 / D11) -------------------


@pytest.mark.django_db
class TestDiscoverPerEngineEndpoints:
    """`discover()` takes a per-engine endpoint map: each engine is polled
    only at ITS OWN addresses. Before this, every engine was polled at the
    single Ollama endpoint, so a ComfyUI checkpoint could never appear."""

    def test_each_engine_is_polled_only_at_its_own_endpoints(self):
        alpha = _stub_engine(name="alpha", models=[InstalledModel(model_id="a-model")])
        beta = _stub_engine(name="beta", models=[InstalledModel(model_id="b-model")])
        with patch.dict(engines.ENGINES, {"alpha": alpha, "beta": beta}, clear=True):
            rows = discover(
                {"alpha": ["http://alpha:1111"], "beta": ["http://beta:2222"]},
                list(ModelConnection.objects.all()),
            )

        alpha.list_installed.assert_called_once_with("http://alpha:1111")
        beta.list_installed.assert_called_once_with("http://beta:2222")
        by_id = {(row.engine, row.model_id): row for row in rows}
        assert by_id[("alpha", "a-model")].endpoint == "http://alpha:1111"
        assert by_id[("beta", "b-model")].endpoint == "http://beta:2222"

    def test_an_engine_absent_from_the_map_is_not_polled(self):
        alpha = _stub_engine(name="alpha", models=[InstalledModel(model_id="a-model")])
        with patch.dict(engines.ENGINES, {"alpha": alpha}, clear=True):
            rows = discover({}, list(ModelConnection.objects.all()))

        alpha.list_installed.assert_not_called()
        assert [row for row in rows if row.installed] == []

    def test_one_unreachable_endpoint_does_not_hide_the_others(self):
        alpha = _stub_engine(name="alpha")
        alpha.list_installed.side_effect = [
            httpx.ConnectError("down"),
            [InstalledModel(model_id="a-model")],
        ]
        with patch.dict(engines.ENGINES, {"alpha": alpha}, clear=True):
            rows = discover(
                {"alpha": ["http://dead:1111", "http://alive:1111"]},
                list(ModelConnection.objects.all()),
            )

        installed = [row for row in rows if row.installed]
        assert [row.model_id for row in installed] == ["a-model"]
        assert installed[0].endpoint == "http://alive:1111"

    def test_duplicate_endpoints_are_polled_once(self):
        alpha = _stub_engine(name="alpha", models=[InstalledModel(model_id="a-model")])
        with patch.dict(engines.ENGINES, {"alpha": alpha}, clear=True):
            discover(
                {"alpha": ["http://alpha:1111", "http://alpha:1111/"]},
                list(ModelConnection.objects.all()),
            )

        assert alpha.list_installed.call_count == 1
```

Add `InstalledModel` to that file's imports: `from core.inference.engines.base import InstalledModel`.

Append to `console/inference/tests/test_db_bindings.py`:

```python
@pytest.mark.django_db
class TestConnectionConfigRoundTrip:
    """D8: `ResolvedModel.config` already flowed into engine builders, but
    the DB row could not carry it. A multi-file family (Flux/SD3: UNet +
    CLIPs + VAE) needs it without a later schema change."""

    def test_config_reaches_the_resolved_model(self):
        connection = ModelConnection.objects.create(
            name="comfy sdxl", engine="comfyui", endpoint="http://localhost:8188",
            model_id="sdxl.safetensors", capabilities=["image-generation"],
            config={"vae": "sdxl_vae.safetensors"},
        )
        RoleBinding.objects.create(role_key="vision.generate", connection=connection)

        resolved = db_provider("vision.generate")

        assert resolved.config == {"vae": "sdxl_vae.safetensors"}

    def test_null_config_resolves_to_an_empty_dict(self):
        connection = ModelConnection.objects.create(
            name="plain", engine="ollama", endpoint="http://localhost:11434",
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        RoleBinding.objects.create(role_key="rag.answer", connection=connection)

        assert db_provider("rag.answer").config == {}
```

Append to `console/inference/tests/test_views.py`:

```python
@pytest.mark.django_db
class TestEngineEndpointsMap:
    """`_engine_endpoints` builds `discover()`'s per-engine map: each
    engine's default endpoint, plus its registered connections' endpoints,
    plus the endpoint the page is currently viewing (so an operator's
    `?endpoint=` override is still scanned for every engine)."""

    def test_defaults_connections_and_the_viewed_endpoint_are_unioned(self):
        ModelConnection.objects.create(
            name="remote comfy", engine="comfyui", endpoint="http://box:8188",
            model_id="sdxl.safetensors", capabilities=["image-generation"],
        )
        engine_map = views._engine_endpoints(
            list(ModelConnection.objects.all()), "http://elsewhere:11434"
        )

        assert settings.INFERENCE_DEFAULT_ENDPOINTS["comfyui"] in engine_map["comfyui"]
        assert "http://box:8188" in engine_map["comfyui"]
        assert "http://elsewhere:11434" in engine_map["ollama"]

    def test_endpoints_are_deduplicated_by_normalized_form(self):
        engine_map = views._engine_endpoints([], settings.OLLAMA_BASE_URL + "/")
        assert len(engine_map["ollama"]) == 1


class TestImageGenerationNeedPhrase:
    def test_checklist_names_the_image_generation_need(self):
        role = RoleSpec("vision.generate", "Image generation", "image-generation")
        checklist = views._needs_checklist([role], [])
        assert checklist[0]["requirement"] == "an image-generation model"
        assert checklist[0]["used_by"] == "Image generation"
        assert checklist[0]["satisfied"] is False
```

Ensure `from console.inference import views` and `from django.conf import settings` are imported in `test_views.py` (add whichever is missing).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest console/inference/tests -q -k "PerEngineEndpoints or ConfigRoundTrip or EngineEndpointsMap or ImageGenerationNeedPhrase"`
Expected: FAIL — `discover()` takes a string, `DiscoveryRow` has no `endpoint`, `_engine_endpoints` does not exist, `ModelConnection` has no `config`.

- [ ] **Step 3: Change `discover()` to a per-engine endpoint map**

In `console/inference/discovery.py`, add `endpoint` to `DiscoveryRow` (after `capability_source`):

```python
    # The endpoint this model was seen INSTALLED at (spec §4.6 / D11).
    # Empty for catalog-only or connection-only rows: nothing was polled to
    # produce them. The console's "Add to registered" button reads this so a
    # ComfyUI checkpoint is registered against the ComfyUI endpoint, not
    # whichever address the page happens to be viewing.
    endpoint: str = ""
```

Add above `discover()`:

```python
def _unique_endpoints(raw) -> list[str]:
    """De-duplicate an engine's endpoint list by `norm_endpoint`, keeping
    first-seen order -- the default endpoint and a registered connection
    frequently name the same address with/without a trailing slash, and
    polling it twice would double the page's HTTP cost for nothing."""
    seen: set[str] = set()
    ordered: list[str] = []
    for endpoint in raw or ():
        key = norm_endpoint(endpoint)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(endpoint)
    return ordered
```

Replace the installed-scan block and the `discover` signature/docstring:

```python
def discover(endpoints: dict[str, list[str]], connections: list[ModelConnection]) -> list[DiscoveryRow]:
    """Merge catalog + live installed models + DB connections into rows.

    Args:
        endpoints: Per-engine endpoint lists (spec §4.6 / D11) -- each
            registered engine is polled at ITS OWN addresses, built by
            `console.inference.views._engine_endpoints` from
            `settings.INFERENCE_DEFAULT_ENDPOINTS` plus that engine's
            registered-connection endpoints plus the endpoint currently
            being viewed. An engine absent from the map is not polled at
            all (its catalog/connection rows still appear). Before this,
            every engine was polled at the single Ollama endpoint, so a
            ComfyUI checkpoint could never show up.
        connections: The registered `ModelConnection` rows to merge in --
            fetched once by the caller (Task 7) rather than queried here.

    Returns:
        One `DiscoveryRow` per distinct (engine, normalized model_id):
        catalog entries first (in catalog order), then installed-only or
        connection-only models in first-seen order. Rows are still keyed by
        (engine, model_id) and NOT by endpoint -- the same model id at two
        endpoints of one engine merges into one row carrying the first
        endpoint it was seen at. See the module docstring for the
        unreachable-engine failure behavior, which now isolates per
        (engine, endpoint) pair.
    """
```

and the scan itself:

```python
    installed_by_engine: list[tuple[str, str, InstalledModel]] = []
    for engine in ENGINES.values():
        for endpoint in _unique_endpoints(endpoints.get(engine.name)):
            try:
                for installed in engine.list_installed(endpoint):
                    installed_by_engine.append((engine.name, endpoint, installed))
            except Exception:  # noqa: BLE001 -- one dead (engine, endpoint) must not blank the page
                continue

    for engine_name, engine_endpoint, installed in installed_by_engine:
        key = _key(engine_name, installed.model_id)
        row = rows[key]
```

and add `endpoint=engine_endpoint,` to the `replace(row, ...)` call in that loop (alongside `installed=True`).

- [ ] **Step 4: Build the map in the console view**

In `console/inference/views.py`, add after `_default_endpoint()`:

```python
def _engine_endpoints(connections: list[ModelConnection], endpoint: str) -> dict[str, list[str]]:
    """`discover()`'s per-engine endpoint map (spec §4.6 / D11).

    For each REGISTERED engine, the union of: its own default endpoint from
    `settings.INFERENCE_DEFAULT_ENDPOINTS`, every registered connection's
    endpoint for that engine, and `endpoint` -- the address this page is
    currently viewing (the resolved default, or an operator's `?endpoint=`
    override). Including `endpoint` for every engine preserves exactly the
    pre-D11 behaviour (every engine polled at the one viewed address) as a
    superset, so an override still scans for every engine.

    De-anchored by construction: no engine name, port, or path is written
    here -- keys come off each adapter's own `.name`.
    """
    engine_map: dict[str, list[str]] = {}

    def _add(engine_name: str, candidate: str) -> None:
        if not candidate:
            return
        bucket = engine_map.setdefault(engine_name, [])
        if all(norm_endpoint(candidate) != norm_endpoint(seen) for seen in bucket):
            bucket.append(candidate)

    for engine in ENGINES.values():
        _add(engine.name, settings.INFERENCE_DEFAULT_ENDPOINTS.get(engine.name, ""))
    for connection in connections:
        _add(connection.engine, connection.endpoint)
    for engine in ENGINES.values():
        _add(engine.name, endpoint)

    return engine_map
```

In `_build_context`, change the discovery call:

```python
    try:
        discovered = discover(_engine_endpoints(connections, endpoint), connections)
    except Exception:  # noqa: BLE001 -- discovery failure degrades to empty sections
        discovered = []
```

Add the capability phrase to `_CAPABILITY_NEED_PHRASES`:

```python
    "image-generation": "an image-generation model",
```

Scope `_installed_views` per row rather than per page:

```python
    local_connections: dict[tuple[str, str, str], ModelConnection] = {}
    for connection in connections:
        local_connections.setdefault(
            (connection.engine, norm_tag(connection.model_id), _norm_endpoint(connection.endpoint)),
            connection,
        )

    rows = []
    for row in discovered:
        if not row.installed:
            continue
        # A row now knows the endpoint it was actually seen at (D11); fall
        # back to the page's endpoint for any row a stub/older adapter
        # produced without one.
        row_endpoint = row.endpoint or endpoint
        rows.append(
            {
                "row": row,
                "endpoint": row_endpoint,
                "size_display": _human_size(row.size),
                "loaded_size_display": _human_size(getattr(row, "loaded_size", None)),
                "in_use": (row.engine, norm_tag(row.model_id)) in in_use_keys,
                "connection": local_connections.get(
                    (row.engine, norm_tag(row.model_id), _norm_endpoint(row_endpoint))
                ),
                "source_label": _SOURCE_LABELS.get(row.capability_source),
            }
        )
    return rows
```

In `console/inference/templates/inference/_installed_row.html`, change the register form's hidden endpoint field (line ~112) to use the row's own endpoint:

```html
        <input type="hidden" name="endpoint" value="{{ item.endpoint|default:endpoint }}">
```

- [ ] **Step 5: Add the `config` column and pass it through**

In `console/inference/models.py`, add to `ModelConnection` after `embed_dim`:

```python
    # Engine-specific extras for THIS connection, handed to the engine's
    # builder as `**ResolvedModel.config` (D8). Null for the common
    # single-file case; a multi-file family (Flux/SD3: UNet + CLIPs + VAE)
    # names its companion files here without a schema change. Never a model
    # default -- only what an operator or a later UI puts in it.
    config = models.JSONField(null=True, blank=True)
```

Create `console/inference/migrations/0003_modelconnection_config.py` with `makemigrations`:

```bash
.venv/bin/python manage.py makemigrations inference --name modelconnection_config
```

Expected content:

```python
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inference", "0002_seed_from_env")]

    operations = [
        migrations.AddField(
            model_name="modelconnection",
            name="config",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
```

In `console/inference/bindings.py`, add to the returned `ResolvedModel`:

```python
        config=connection.config or {},
```

with the docstring note: `config` is `{}` when the column is null, so engine builders always receive a mapping.

- [ ] **Step 6: Update the existing discovery call sites**

Add near the top of `console/inference/tests/test_discovery.py`, below `ENDPOINT`:

```python
def _at(endpoint: str = ENDPOINT) -> dict[str, list[str]]:
    """Every CURRENTLY registered engine polled at one endpoint -- the
    pre-D11 single-endpoint behaviour expressed as `discover()`'s new map.
    Evaluated per call, so a stub engine a test registers is included
    automatically."""
    return {engine.name: [endpoint] for engine in engines.ENGINES.values()}
```

Then rewrite the existing call sites mechanically:

```bash
sed -i '' 's/discover(ENDPOINT, /discover(_at(), /g' console/inference/tests/test_discovery.py
```

In `console/inference/tests/test_views.py`, update the one call-argument assertion (in `TestConsoleViewEndpointOverride`):

```python
        assert "http://elsewhere:11434" in mock_discover.call_args[0][0]["ollama"]
```

- [ ] **Step 7: Run the console suite**

Run: `.venv/bin/pytest console -q`
Expected: PASS — Ollama behaviour is unchanged (its default endpoint is still polled when nothing is registered, and the viewed endpoint is still polled for every engine).

- [ ] **Step 8: Update `console/inference/README.md`**

Add a short subsection under the discovery description:

- `discover()` takes a **per-engine endpoint map**, not one endpoint: each registered adapter is polled at its own default (`settings.INFERENCE_DEFAULT_ENDPOINTS`), at every registered connection's endpoint for that engine, and at whatever endpoint the page is currently viewing. A `DiscoveryRow` carries the `endpoint` it was seen at, and "Add to registered" registers against that address.
- `ModelConnection.config` (nullable JSON) rides through `db_provider` into `ResolvedModel.config` and is splatted into the engine's builder — the seam multi-file model families will use.

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add console/inference config/settings.py
git commit -F - <<'EOF'
feat(console): per-engine discovery endpoints and a connection config column

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

> **Announce after this commit:** the model-management track can now rebase — `console/inference/*`, `config/settings.py`, and `config/urls.py` are done being touched by this plan.

---

### Task 3: `core/inference/operations.py` — the platform operation vocabulary

**Suggested implementer tier:** sonnet — new pure-Python module with a schema validator; no framework surface, but real design detail in the validation rules.

**Files:**
- Create: `core/inference/operations.py`
- Test: `modules/vision/tests/test_operations.py`

**Interfaces:**
- Consumes: `core.inference.roles.CAPABILITIES` (Task 1).
- Produces:
  - `Param(key, kind, label, default=None, min=None, max=None, step=None, choices=(), asset_kind=None, accept=None, required=False, multiple=False)` — frozen dataclass.
  - `Operation(key, label, capability, params, output_media)` — frozen dataclass; `.param(key) -> Param | None`.
  - `register_operation(op) -> None`, `all_operations() -> list[Operation]`, `get_operation(key) -> Operation | None`, `operations_for(capability) -> list[Operation]`.
  - `validate_params(operation: Operation, raw: dict) -> dict` — coerced, range-checked, seed-resolved; raises `ParamError` (a `ValueError` subclass carrying `.errors: dict[str, str]`).
  - `GenerationRequest(operation: str, model_id: str, params: dict, inputs: dict[str, Path], client_ref: str)` — frozen.
  - `TXT2IMG: Operation` (key `"txt2img"`, capability `"image-generation"`, `output_media="image/png"`), **defined here but registered by `modules/vision/apps.py`** (Task 9).

> **Test placement:** `pytest.ini`'s `testpaths` are `modules console scripts` — `core/` is not collected, and `console/inference/tests/` is the other track's territory after Task 2. Every test for the new `core/` code therefore lives under `modules/vision/tests/`.

- [ ] **Step 1: Write the failing tests**

Create `modules/vision/tests/test_operations.py`:

```python
"""Unit tests for core/inference/operations.py (spec §4.1).

Pure data + validation: no DB, no HTTP, no Django settings.
"""
from __future__ import annotations

import pytest

from core.inference.operations import (
    TXT2IMG,
    Operation,
    Param,
    ParamError,
    all_operations,
    get_operation,
    operations_for,
    register_operation,
    validate_params,
)


def _clean(**overrides) -> dict:
    raw = {
        "prompt": "a lighthouse",
        "negative_prompt": "",
        "width": "1024",
        "height": "1024",
        "steps": "25",
        "cfg_scale": "7.0",
        "seed": "",
        "sampler": "euler",
        "scheduler": "normal",
        "batch_size": "1",
    }
    raw.update(overrides)
    return raw


class TestOperationShape:
    def test_txt2img_declares_the_image_generation_capability(self):
        assert TXT2IMG.key == "txt2img"
        assert TXT2IMG.label == "Text to image"
        assert TXT2IMG.capability == "image-generation"
        assert TXT2IMG.output_media == "image/png"

    def test_txt2img_carries_every_spec_param(self):
        assert [param.key for param in TXT2IMG.params] == [
            "prompt", "negative_prompt", "width", "height", "steps",
            "cfg_scale", "seed", "sampler", "scheduler", "batch_size",
        ]

    def test_param_lookup(self):
        assert TXT2IMG.param("steps").default == 25
        assert TXT2IMG.param("nope") is None

    def test_unknown_capability_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown capability"):
            Operation("x", "X", "telepathy", (), "image/png")

    def test_unknown_param_kind_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown param kind"):
            Operation("x", "X", "image-generation", (Param("p", "runes", "P"),), "image/png")


class TestRegistry:
    def test_register_and_look_up(self):
        op = Operation("t-op", "Test op", "image-generation", (), "image/png")
        register_operation(op)
        try:
            assert get_operation("t-op") is op
            assert op in all_operations()
            assert op in operations_for("image-generation")
            assert op not in operations_for("chat")
        finally:
            from core.inference import operations

            operations._OPERATIONS.pop("t-op", None)

    def test_unknown_key_is_none(self):
        assert get_operation("no-such-op") is None


class TestValidateParams:
    def test_coerces_numbers_and_keeps_text(self):
        clean = validate_params(TXT2IMG, _clean())
        assert clean["prompt"] == "a lighthouse"
        assert clean["width"] == 1024 and isinstance(clean["width"], int)
        assert clean["cfg_scale"] == 7.0 and isinstance(clean["cfg_scale"], float)
        assert clean["batch_size"] == 1

    def test_missing_optional_params_take_their_defaults(self):
        clean = validate_params(TXT2IMG, {"prompt": "x", "sampler": "euler", "scheduler": "normal"})
        assert clean["steps"] == 25
        assert clean["width"] == 1024
        assert clean["negative_prompt"] == ""

    def test_missing_required_param_is_an_error(self):
        with pytest.raises(ParamError) as excinfo:
            validate_params(TXT2IMG, _clean(prompt="   "))
        assert "prompt" in excinfo.value.errors

    def test_out_of_range_value_is_an_error(self):
        with pytest.raises(ParamError) as excinfo:
            validate_params(TXT2IMG, _clean(steps="500"))
        assert "steps" in excinfo.value.errors

    def test_non_numeric_value_is_an_error(self):
        with pytest.raises(ParamError) as excinfo:
            validate_params(TXT2IMG, _clean(width="wide"))
        assert "width" in excinfo.value.errors

    def test_unknown_keys_are_rejected(self):
        with pytest.raises(ParamError) as excinfo:
            validate_params(TXT2IMG, _clean(sneaky="1"))
        assert "sneaky" in excinfo.value.errors

    def test_blank_seed_is_resolved_to_a_random_integer(self):
        first = validate_params(TXT2IMG, _clean())["seed"]
        second = validate_params(TXT2IMG, _clean())["seed"]
        assert isinstance(first, int) and 0 <= first < 2 ** 32
        assert first != second  # 1-in-4-billion flake, acceptable

    def test_explicit_seed_is_kept(self):
        assert validate_params(TXT2IMG, _clean(seed="42"))["seed"] == 42

    def test_choice_is_free_when_the_schema_has_no_list(self):
        """Sampler/scheduler lists come from the ENGINE at render time
        (`list_choices`), so the schema cannot enumerate them; the form adds
        the live list and the engine rejects anything else as a failed job."""
        assert validate_params(TXT2IMG, _clean(sampler="dpmpp_3m_sde"))["sampler"] == "dpmpp_3m_sde"

    def test_required_choice_cannot_be_blank(self):
        with pytest.raises(ParamError) as excinfo:
            validate_params(TXT2IMG, _clean(sampler=""))
        assert "sampler" in excinfo.value.errors
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest modules/vision/tests/test_operations.py -q`
Expected: FAIL — `No module named 'core.inference.operations'`.

- [ ] **Step 3: Write `core/inference/operations.py`**

```python
"""
Operation registry -- the platform's generation vocabulary (D5).

An "operation" is a named generation mode with a parameter SCHEMA: txt2img
today; img2img, inpaint, controlnet, upscale later (spec §9). The schema is
what the page renders its form from and what an engine adapter maps onto its
own template, so adding a mode is one `Operation` definition plus one engine
template -- never a reshape of the page or the service layer.

Definitions live here in `core/` (pure data, no Django); REGISTRATION is done
by the feature app that actually serves them (`modules/vision/apps.py`), so
`all_operations()` only ever lists what an enabled feature can really run.

Kept dependency-free apart from `CAPABILITIES` (also pure), matching
`core.inference.roles` / `core.inference.catalog`.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.inference.roles import CAPABILITIES

# Every parameter kind the form layer knows how to render and this module
# knows how to validate. A kind outside this set is a programming error in an
# Operation definition, caught at definition time rather than at render time.
PARAM_KINDS = {"text", "int", "float", "choice", "seed", "file", "asset"}

# Asset kinds an engine may be asked to list (`InferenceEngine.list_assets`).
ASSET_KINDS = {"lora", "vae", "controlnet", "upscale_model", "embedding"}

# Upper bound for a randomly resolved seed. Deliberately modest (32-bit): it
# is small enough to type, paste, and compare by eye when reproducing a
# generation, and every engine we target accepts it.
SEED_MAX = 2 ** 32


@dataclass(frozen=True)
class Param:
    """One parameter of an operation, as both a validation rule and a
    rendering instruction.

    `choices` is for a `"choice"` param whose options are FIXED. Options an
    engine reports live (samplers, schedulers) are left empty here and
    supplied by the form layer from `InferenceEngine.list_choices` --
    `validate_params` therefore accepts any non-blank string for such a
    param, and an option the engine does not know surfaces as a failed job
    rather than a lie about what this platform supports.
    """

    key: str
    kind: str
    label: str
    default: Any = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: tuple[str, ...] = ()
    asset_kind: str | None = None
    accept: str | None = None
    required: bool = False
    multiple: bool = False

    def __post_init__(self) -> None:
        if self.kind not in PARAM_KINDS:
            raise ValueError(f"Unknown param kind {self.kind!r}; must be one of {sorted(PARAM_KINDS)}")
        if self.asset_kind is not None and self.asset_kind not in ASSET_KINDS:
            raise ValueError(f"Unknown asset kind {self.asset_kind!r}; must be one of {sorted(ASSET_KINDS)}")


@dataclass(frozen=True)
class Operation:
    """A generation mode plus its parameter schema."""

    key: str
    label: str
    capability: str
    params: tuple[Param, ...]
    output_media: str

    def __post_init__(self) -> None:
        if self.capability not in CAPABILITIES:
            raise ValueError(
                f"Unknown capability {self.capability!r}; must be one of {sorted(CAPABILITIES)}"
            )

    def param(self, key: str) -> Param | None:
        """The parameter named `key`, or None."""
        for param in self.params:
            if param.key == key:
                return param
        return None


_OPERATIONS: dict[str, Operation] = {}


def register_operation(operation: Operation) -> None:
    """Register `operation` under its `.key`, replacing any existing entry.
    Idempotent, like `register_role` -- re-importing a registering module is
    safe."""
    _OPERATIONS[operation.key] = operation


def all_operations() -> list[Operation]:
    """Every registered operation, in registration order."""
    return list(_OPERATIONS.values())


def get_operation(key: str) -> Operation | None:
    """The registered operation for `key`, or None."""
    return _OPERATIONS.get(key)


def operations_for(capability: str) -> list[Operation]:
    """Every registered operation answering `capability`."""
    return [op for op in _OPERATIONS.values() if op.capability == capability]


class ParamError(ValueError):
    """Raised by `validate_params` when the submitted values don't fit the
    schema. `.errors` maps a param key (or an unknown key) to a
    human-readable reason, so a view can re-render a form with per-field
    messages instead of one opaque string."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{key}: {message}" for key, message in errors.items()))
        self.errors = errors


def _coerce_number(param: Param, raw: Any, errors: dict[str, str]) -> Any:
    caster = int if param.kind == "int" else float
    try:
        value = caster(str(raw).strip())
    except (TypeError, ValueError):
        errors[param.key] = f"{param.label} must be a number."
        return None
    if param.min is not None and value < param.min:
        errors[param.key] = f"{param.label} must be at least {caster(param.min)}."
        return None
    if param.max is not None and value > param.max:
        errors[param.key] = f"{param.label} must be at most {caster(param.max)}."
        return None
    return value


def validate_params(operation: Operation, raw: dict) -> dict:
    """Coerce and range-check `raw` against `operation`'s schema.

    This is the ONLY validation the service layer does, and it is the floor
    every caller shares -- the page's form and (later) the chatbot tool both
    land here. It:

    - rejects keys the operation does not declare (a typo must not be
      silently swallowed into a graph);
    - fills absent optional params from their defaults;
    - coerces `int`/`float` and enforces `min`/`max`;
    - resolves a blank `seed` to a random integer and returns it, so the
      job row can record exactly what was run (D6 reproducibility);
    - requires a non-blank value for any `required` param and for any
      `choice` param.

    `step` is a UI hint only, not a rule: ComfyUI rejects a size that isn't
    divisible by 8 with a clear error, and surfacing THAT is more honest
    than this module inventing an engine's constraint.

    Raises `ParamError` with every problem found (not just the first), so a
    form can show all of them at once.
    """
    errors: dict[str, str] = {}
    known = {param.key for param in operation.params}
    for key in raw:
        if key not in known:
            errors[key] = "Unknown parameter for this operation."

    clean: dict[str, Any] = {}
    for param in operation.params:
        supplied = raw.get(param.key)
        blank = supplied is None or (isinstance(supplied, str) and not supplied.strip())

        if param.kind == "seed":
            if blank:
                clean[param.key] = secrets.randbelow(SEED_MAX)
                continue
            value = _coerce_number(Param(param.key, "int", param.label, min=0), supplied, errors)
            if value is not None:
                clean[param.key] = value
            continue

        if blank:
            if param.required:
                errors[param.key] = f"{param.label} is required."
                continue
            if param.kind == "choice":
                errors[param.key] = f"{param.label} must be chosen."
                continue
            clean[param.key] = param.default
            continue

        if param.kind in ("int", "float"):
            value = _coerce_number(param, supplied, errors)
            if value is not None:
                clean[param.key] = value
            continue

        if param.kind == "choice":
            value = str(supplied).strip()
            if param.choices and value not in param.choices:
                errors[param.key] = f"{param.label} must be one of {', '.join(param.choices)}."
                continue
            clean[param.key] = value
            continue

        clean[param.key] = str(supplied).strip() if isinstance(supplied, str) else supplied

    if errors:
        raise ParamError(errors)
    return clean


@dataclass(frozen=True)
class GenerationRequest:
    """One generation, as the engine seam receives it.

    `params` is always `validate_params` output (never raw form data);
    `inputs` maps a file param's key to the path it was stored at inside the
    job directory; `client_ref` is the job's UUID, used as the engine-side
    output filename prefix so a result can always be traced back to its job.
    """

    operation: str
    model_id: str
    params: dict
    inputs: dict[str, Path] = field(default_factory=dict)
    client_ref: str = ""


# --- The operations this platform ships ----------------------------------
# Defined here; REGISTERED by the feature app that serves them
# (modules/vision/apps.py), gated on its feature flag.

TXT2IMG = Operation(
    key="txt2img",
    label="Text to image",
    capability="image-generation",
    output_media="image/png",
    params=(
        Param("prompt", "text", "Prompt", default="", required=True),
        Param("negative_prompt", "text", "Negative prompt", default=""),
        Param("width", "int", "Width", default=1024, min=64, max=4096, step=8),
        Param("height", "int", "Height", default=1024, min=64, max=4096, step=8),
        Param("steps", "int", "Steps", default=25, min=1, max=150),
        Param("cfg_scale", "float", "CFG scale", default=7.0, min=0, max=30, step=0.5),
        Param("seed", "seed", "Seed", default=None),
        # Choices are engine-reported (`list_choices`) -- see `Param.choices`.
        Param("sampler", "choice", "Sampler"),
        Param("scheduler", "choice", "Scheduler"),
        Param("batch_size", "int", "Batch size", default=1, min=1, max=8),
    ),
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest modules/vision/tests/test_operations.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/inference/operations.py modules/vision/tests/test_operations.py
git commit -F - <<'EOF'
feat(inference): operation registry with the txt2img parameter schema

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 4: `engines/base.py` — `Asset`, `JobStatus`, `GenerationRejected`, `ImageGenerator`, `SetupGuide`

**Suggested implementer tier:** haiku — purely additive dataclasses and Protocol members transcribed from the spec; no behaviour.

**Files:**
- Modify: `core/inference/engines/base.py`
- Test: `modules/vision/tests/test_engine_base.py`

**Interfaces:**
- Consumes: `core.inference.operations.GenerationRequest` (Task 3).
- Produces:
  - `Asset(kind: str, asset_id: str, size: int | None = None)` — frozen.
  - `JobStatus(state: str, error: str | None = None, progress: float | None = None)` — frozen; `JOB_STATES = ("queued", "running", "done", "failed", "lost")`.
  - `GenerationRejected(RuntimeError)` — the engine refused a submission outright.
  - `ImageGenerator` Protocol: `submit(request) -> tuple[str, dict]`, `status(engine_ref) -> JobStatus`, `fetch_outputs(engine_ref) -> list[tuple[str, bytes, str]]`.
  - `SetupStep(title: str, body: str, command: str | None = None)` and `SetupGuide(summary: str, platforms: dict[str, tuple[SetupStep, ...]], network_note: str, models_note: str, verify_url_path: str)` — frozen; `SETUP_PLATFORMS = ("macos", "windows", "linux")` and `SETUP_PLATFORM_LABELS = {"macos": "macOS", "windows": "Windows", "linux": "Linux"}`.
  - `InferenceEngine` optional members: `supported_operations`, `list_assets`, `list_choices`, `build_image_generator`, `setup_guide: SetupGuide | None`, `serves_capabilities: tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

Create `modules/vision/tests/test_engine_base.py`:

```python
"""Unit tests for the additive engine-contract types (spec §4.2)."""
from __future__ import annotations

import pytest

from core.inference.engines.base import (
    JOB_STATES,
    SETUP_PLATFORM_LABELS,
    SETUP_PLATFORMS,
    Asset,
    GenerationRejected,
    ImageGenerator,
    InferenceEngine,
    JobStatus,
    SetupGuide,
    SetupStep,
)
from core.inference.engines.ollama import OllamaEngine


class TestAsset:
    def test_size_is_optional(self):
        asset = Asset(kind="lora", asset_id="sub\\detail.safetensors")
        assert asset.size is None
        assert asset.asset_id == "sub\\detail.safetensors"

    def test_is_frozen(self):
        with pytest.raises(Exception):
            Asset(kind="vae", asset_id="v.safetensors").kind = "lora"


class TestJobStatus:
    def test_defaults(self):
        state = JobStatus(state="queued")
        assert state.error is None
        assert state.progress is None

    def test_every_state_the_platform_understands(self):
        assert JOB_STATES == ("queued", "running", "done", "failed", "lost")


class TestOptionalMembersDegradeGracefully:
    """Downstream reads every optional member via `getattr(engine, name,
    None)`, so an adapter that predates them (Ollama) is untouched."""

    def test_ollama_has_no_image_generation_members(self):
        engine = OllamaEngine()
        assert getattr(engine, "build_image_generator", None) is None
        assert getattr(engine, "supported_operations", None) is None
        assert getattr(engine, "list_assets", None) is None
        assert getattr(engine, "list_choices", None) is None


class TestSetupTypes:
    """An engine declares its OWN install guide (the /setup/ page renders
    whatever it is handed and names no engine of its own)."""

    def test_a_step_may_carry_no_command(self):
        step = SetupStep(title="Download the portable build", body="Unzip it anywhere.")
        assert step.command is None

    def test_the_three_platform_keys_and_their_labels(self):
        assert SETUP_PLATFORMS == ("macos", "windows", "linux")
        assert SETUP_PLATFORM_LABELS == {"macos": "macOS", "windows": "Windows", "linux": "Linux"}

    def test_a_guide_carries_everything_the_page_renders(self):
        guide = SetupGuide(
            summary="s",
            platforms={"macos": (SetupStep("t", "b", "c"),)},
            network_note="n",
            models_note="m",
            verify_url_path="/health",
        )
        assert guide.platforms["macos"][0].command == "c"
        assert guide.verify_url_path == "/health"


class TestSetupMembersAreOptional:
    """Read via `getattr` like every other optional member, so an adapter
    that declares neither still renders a minimal card on /setup/."""

    def test_an_engine_without_them_degrades_to_empty(self):
        class _Bare:
            name = "bare"

        engine = _Bare()
        assert getattr(engine, "setup_guide", None) is None
        assert getattr(engine, "serves_capabilities", ()) == ()


class TestProtocolsAreImportable:
    def test_image_generator_declares_the_three_methods(self):
        for name in ("submit", "status", "fetch_outputs"):
            assert hasattr(ImageGenerator, name)

    def test_inference_engine_declares_the_optional_members(self):
        for name in (
            "supported_operations", "list_assets", "list_choices",
            "build_image_generator", "setup_guide", "serves_capabilities",
        ):
            assert hasattr(InferenceEngine, name)

    def test_generation_rejected_is_a_runtime_error(self):
        assert issubclass(GenerationRejected, RuntimeError)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest modules/vision/tests/test_engine_base.py -q`
Expected: FAIL — `ImportError: cannot import name 'Asset'`.

- [ ] **Step 3: Extend `core/inference/engines/base.py`**

Add the import and the new types (append after `InstalledModel`, before `InferenceEngine`):

```python
from core.inference.operations import GenerationRequest

# Every job state the platform understands, in lifecycle order. "lost" is
# not a job status a record can hold -- it means the ENGINE no longer knows
# this job (restarted, queue cleared), which the service layer turns into a
# `failed` record with an explaining message.
JOB_STATES = ("queued", "running", "done", "failed", "lost")


@dataclass(frozen=True)
class Asset:
    """A file an engine can adorn a generation with -- a LoRA, VAE,
    ControlNet, upscaler, or embedding (D7).

    A second axis from role-bound MODELS: assets don't answer a role, they
    decorate a job. `asset_id` is opaque and engine-shaped -- on Windows
    hosts it may contain a backslash-separated subfolder -- so it is never
    parsed, only passed back.
    """

    kind: str
    asset_id: str
    size: int | None = None


@dataclass(frozen=True)
class JobStatus:
    """What an engine says about one submitted job right now.

    `state` is one of `JOB_STATES`. `error` carries the engine's own words
    for a failure (never a rewritten guess); `progress` is 0..1 only when
    the engine actually reports it, `None` otherwise -- a fabricated
    progress bar is worse than none.
    """

    state: str
    error: str | None = None
    progress: float | None = None


class GenerationRejected(RuntimeError):
    """The engine refused a submission outright (unknown checkpoint, invalid
    graph). Distinct from a transport failure: the request reached the
    engine and came back rejected, so the job is immediately `failed` with
    the engine's own message rather than retried."""


# The platforms the setup page renders a guide for, in display order, and
# their human labels. Owned here (not in the page's template) so an adapter
# and the page agree on the keys without either hardcoding prose.
SETUP_PLATFORMS = ("macos", "windows", "linux")
SETUP_PLATFORM_LABELS = {"macos": "macOS", "windows": "Windows", "linux": "Linux"}


@dataclass(frozen=True)
class SetupStep:
    """One instruction on the setup page: a heading, a sentence of context,
    and at most ONE command to copy. A step with no command (download a zip,
    edit a .bat) carries `command=None` rather than a fake shell line."""

    title: str
    body: str
    command: str | None = None


@dataclass(frozen=True)
class SetupGuide:
    """Everything the `/setup/` page needs to explain ONE engine.

    Declared by the ADAPTER, because only the adapter knows how its server is
    installed, how it must listen to be reachable from a container, and where
    its model files go. The page renders whatever it is handed and names no
    engine of its own -- a newly registered adapter appears there with no
    change to the page.

    `platforms` is keyed by `SETUP_PLATFORMS` entries; a platform an engine
    does not support is simply absent. `verify_url_path` is the path the page
    appends to the engine's default endpoint for its "Verify" line (e.g.
    "/api/tags", "/system_stats") -- the same path the adapter's own
    `is_healthy` uses, so what the page tells an operator to open is exactly
    what the platform checks.
    """

    summary: str
    platforms: dict[str, tuple[SetupStep, ...]]
    network_note: str
    models_note: str
    verify_url_path: str


class ImageGenerator(Protocol):
    """The seam a generating engine hands back for one bound model.

    Stateless above the endpoint + model it was built with: the platform
    holds no engine connection between requests, so every method is safe to
    call from any request or a later worker.
    """

    def submit(self, request: GenerationRequest) -> tuple[str, dict]:
        """Submit `request` and return `(engine_ref, payload)`.

        `engine_ref` is the engine's own job handle; `payload` is the EXACT
        submission, stored verbatim on the job row (D6) so a result is
        reproducible and exportable. Raises `GenerationRejected` when the
        engine refuses the submission.
        """
        ...

    def status(self, engine_ref: str) -> JobStatus:
        """Current `JobStatus` for `engine_ref` (see `JOB_STATES`)."""
        ...

    def fetch_outputs(self, engine_ref: str) -> list[tuple[str, bytes, str]]:
        """Every finished output as `(filename, content, media_type)`."""
        ...
```

Then append the optional members to the `InferenceEngine` Protocol (with the existing convention noted):

```python
    # --- Optional members (spec §4.2) ------------------------------------
    # Read downstream via `getattr(engine, name, None)`, exactly like
    # `library_url` / `install_cmd_template`, so an adapter that implements
    # none of them (OllamaEngine) is untouched and never raises.

    def supported_operations(self, model_id: str, endpoint: str) -> tuple[str, ...]:
        """Operation keys this engine can run for `model_id`."""
        ...

    def list_assets(self, endpoint: str, kind: str) -> list[Asset]:
        """Assets of `kind` installed at `endpoint`; `[]` for a kind this
        engine doesn't have."""
        ...

    def list_choices(self, endpoint: str, param_key: str) -> tuple[str, ...]:
        """Live option list for a `"choice"` param (samplers, schedulers);
        `()` for a param key this engine doesn't report."""
        ...

    def build_image_generator(self, model_id: str, endpoint: str, **cfg) -> ImageGenerator:
        """Build an `ImageGenerator` bound to `model_id` at `endpoint`."""
        ...

    # How to install and reach this engine, rendered verbatim by the
    # `/setup/` page. `None` (or absent) means the page falls back to the
    # facts every adapter has: `name`, `api_description`, `well_known_ports`,
    # `install_cmd_template`.
    setup_guide: SetupGuide | None = None

    # Which platform capabilities this engine can serve at all -- the
    # "Served by" column of the setup page's "What each feature needs"
    # table. Declarative and engine-owned, like `well_known_ports`; absent
    # means "unknown", rendered as no engine claiming that capability.
    serves_capabilities: tuple[str, ...] = ()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest modules/vision/tests/test_engine_base.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite (import-graph check)**

Run: `.venv/bin/pytest -q`
Expected: all PASS — `base.py` now imports `core.inference.operations`, which imports only `core.inference.roles`; no cycle.

- [ ] **Step 6: Commit**

```bash
git add core/inference/engines/base.py modules/vision/tests/test_engine_base.py
git commit -F - <<'EOF'
feat(inference): add Asset, JobStatus and the ImageGenerator engine seam

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 5: ComfyUI adapter — health, checkpoints, assets, choices, setup guide

**Suggested implementer tier:** sonnet — a new engine adapter mirroring `ollama.py`, plus the shared HTTP double every later ComfyUI test uses.

**Files:**
- Create: `core/inference/engines/comfyui.py`
- Modify: `core/inference/engines/__init__.py` (register)
- Create: `modules/vision/tests/_helpers.py`
- Test: `modules/vision/tests/test_comfyui_engine.py`

**Interfaces:**
- Consumes: `InstalledModel`, `Asset` (`core.inference.engines.base`).
- Produces:
  - `ComfyUIEngine` with `name = "comfyui"`, `api_description = "the ComfyUI HTTP API"`, `library_url = "https://github.com/comfyanonymous/ComfyUI"`, `install_cmd_template = "copy <model>.safetensors into ComfyUI/models/checkpoints/"`, `well_known_ports = (8188,)`.
  - `is_healthy(endpoint, timeout=None) -> bool`, `list_installed(endpoint) -> list[InstalledModel]`, `list_assets(endpoint, kind) -> list[Asset]`, `list_choices(endpoint, param_key) -> tuple[str, ...]`, `supported_operations(model_id, endpoint) -> tuple[str, ...]`.
  - `build_llm` / `build_embedder` raise `NotImplementedError("comfyui serves image generation only")`.
  - `ComfyUIEngine.setup_guide: SetupGuide` (macOS / Windows / Linux install steps, the `--listen 0.0.0.0` network note, the "place your own checkpoints" models note, `verify_url_path = "/system_stats"`) and `ComfyUIEngine.serves_capabilities = ("image-generation",)` — read by the `/setup/` page (Task 17), which hardcodes nothing about this engine.
  - `modules.vision.tests._helpers.FakeComfyUI` — the HTTP-layer double (`.get` / `.post` side effects) and `clear_bindings()`.

- [ ] **Step 1: Write the shared test double**

Create `modules/vision/tests/_helpers.py`:

```python
"""Shared test helpers for modules/vision/tests.

A plain importable module -- **not** a `conftest.py` (the repo forbids
those anywhere). Each test module imports what it needs; autouse fixtures
stay defined per module and delegate their bodies here, per repo
convention.

`FakeComfyUI` is an HTTP-LAYER double: tests patch
`core.inference.engines.comfyui.httpx.get` / `.post` with its methods, so
the adapter's real URL building, JSON parsing, and error handling run. The
engine's own methods are never mocked away.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from console.inference.models import ModelConnection, RoleBinding


def clear_bindings() -> None:
    """Body of the `_clear_bindings` autouse fixture used by every vision
    test that touches roles: migration 0002 may seed a connection/binding
    pair from an operator's environment, and these tests assume they own
    the registry."""
    RoleBinding.objects.all().delete()
    ModelConnection.objects.all().delete()


@dataclass
class _Response:
    """Just enough of `httpx.Response` for the adapter's call sites."""

    status_code: int = 200
    payload: Any = None
    content: bytes = b""

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://comfyui.test/")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )


def object_info(node: str, inputs: dict[str, Any]) -> dict:
    """A `/object_info/<node>` body in ComfyUI's real shape: every required
    input maps to `[<spec>, <options dict>]`, where a combo input's spec is
    the LIST of allowed values."""
    return {node: {"input": {"required": inputs}}}


@dataclass
class FakeComfyUI:
    """A ComfyUI server, as seen through `httpx`.

    Every field is the fact the corresponding endpoint should report:

    - `healthy` -> `GET /system_stats` (200 vs a raised `ConnectError`)
    - `checkpoints`/`loras`/`vaes`/`controlnets`/`upscalers` ->
      `GET /object_info/<loader node>`
    - `samplers`/`schedulers` -> `GET /object_info/KSampler`
    - `prompt_id`, `prompt_status`, `prompt_body` -> `POST /prompt`
    - `history` -> `GET /history/<id>` (keyed by prompt id)
    - `queue_running`/`queue_pending` -> `GET /queue`
    - `images` -> `GET /view` (keyed by filename)
    """

    healthy: bool = True
    checkpoints: tuple[str, ...] = ()
    loras: tuple[str, ...] = ()
    vaes: tuple[str, ...] = ()
    controlnets: tuple[str, ...] = ()
    upscalers: tuple[str, ...] = ()
    samplers: tuple[str, ...] = ("euler", "dpmpp_2m")
    schedulers: tuple[str, ...] = ("normal", "karras")
    prompt_id: str = "1a2b3c"
    prompt_status: int = 200
    prompt_body: dict | None = None
    history: dict = field(default_factory=dict)
    queue_running: list = field(default_factory=list)
    queue_pending: list = field(default_factory=list)
    images: dict = field(default_factory=dict)
    submitted: list = field(default_factory=list)
    view_calls: list = field(default_factory=list)

    _NODE_INPUTS = {
        "CheckpointLoaderSimple": ("checkpoints", "ckpt_name"),
        "LoraLoader": ("loras", "lora_name"),
        "VAELoader": ("vaes", "vae_name"),
        "ControlNetLoader": ("controlnets", "control_net_name"),
        "UpscaleModelLoader": ("upscalers", "model_name"),
    }

    def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> _Response:
        path = url.split("://", 1)[-1].split("/", 1)[-1]

        if path == "system_stats":
            if not self.healthy:
                raise httpx.ConnectError("connection refused")
            return _Response(payload={"system": {"comfyui_version": "0.3.0"}})

        if path.startswith("object_info/"):
            node = path.split("/", 1)[1]
            if node == "KSampler":
                return _Response(
                    payload=object_info(
                        "KSampler",
                        {
                            "seed": ["INT", {"default": 0}],
                            "sampler_name": [list(self.samplers), {"default": self.samplers[0]}],
                            "scheduler": [list(self.schedulers), {"default": self.schedulers[0]}],
                        },
                    )
                )
            if node in self._NODE_INPUTS:
                attribute, input_key = self._NODE_INPUTS[node]
                values = list(getattr(self, attribute))
                return _Response(payload=object_info(node, {input_key: [values, {"tooltip": "x"}]}))
            return _Response(status_code=404, payload={})

        if path.startswith("history/"):
            ref = path.split("/", 1)[1]
            entry = self.history.get(ref)
            return _Response(payload={ref: entry} if entry is not None else {})

        if path == "queue":
            return _Response(
                payload={"queue_running": self.queue_running, "queue_pending": self.queue_pending}
            )

        if path.startswith("view"):
            self.view_calls.append(params or {})
            filename = (params or {}).get("filename", "")
            if filename not in self.images:
                return _Response(status_code=404)
            return _Response(content=self.images[filename])

        raise AssertionError(f"unexpected GET url: {url}")

    def post(self, url: str, json: dict | None = None, timeout: float | None = None) -> _Response:
        if not url.endswith("/prompt"):
            raise AssertionError(f"unexpected POST url: {url}")
        self.submitted.append(json)
        if self.prompt_status >= 400:
            return _Response(
                status_code=self.prompt_status,
                payload=self.prompt_body
                or {
                    "error": {
                        "type": "prompt_outputs_failed_validation",
                        "message": "Prompt outputs failed validation",
                    },
                    "node_errors": {
                        "1": {
                            "errors": [
                                {
                                    "message": "Value not in list",
                                    "details": "ckpt_name: 'nope.safetensors' not in ['sdxl.safetensors']",
                                }
                            ]
                        }
                    },
                },
            )
        return _Response(payload={"prompt_id": self.prompt_id, "number": 0, "node_errors": {}})


def history_success(prompt_id: str, filenames: tuple[str, ...] = ("job_00001_.png",)) -> dict:
    """A finished `/history/<id>` entry, in ComfyUI's real shape."""
    return {
        prompt_id: {
            "status": {"status_str": "success", "completed": True, "messages": []},
            "outputs": {
                "7": {
                    "images": [
                        {"filename": name, "subfolder": "", "type": "output"} for name in filenames
                    ]
                }
            },
        }
    }


def history_error(prompt_id: str, message: str = "Allocation on device") -> dict:
    """A failed `/history/<id>` entry: ComfyUI records the failure in
    `status.messages` as an `execution_error` tuple."""
    return {
        prompt_id: {
            "status": {
                "status_str": "error",
                "completed": False,
                "messages": [
                    ["execution_start", {"prompt_id": prompt_id}],
                    ["execution_error", {"prompt_id": prompt_id, "exception_message": message}],
                ],
            },
            "outputs": {},
        }
    }
```

- [ ] **Step 2: Write the failing engine tests**

Create `modules/vision/tests/test_comfyui_engine.py`:

```python
"""Unit tests for core/inference/engines/comfyui.py (spec §4.3).

HTTP is mocked at the `httpx` layer (never the engine's own methods), so
the adapter's real `/object_info` parsing runs. No DB.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.inference.engines import ENGINES, get_engine
from core.inference.engines.base import SETUP_PLATFORMS
from core.inference.engines.comfyui import ComfyUIEngine
from modules.vision.tests._helpers import FakeComfyUI

ENDPOINT = "http://comfy.local:8188"


class TestRegistrationAndMetadata:
    def test_registered_under_its_own_name(self):
        assert isinstance(get_engine("comfyui"), ComfyUIEngine)
        assert "comfyui" in ENGINES

    def test_console_facing_metadata(self):
        engine = ComfyUIEngine()
        assert engine.name == "comfyui"
        assert engine.api_description == "the ComfyUI HTTP API"
        assert engine.library_url == "https://github.com/comfyanonymous/ComfyUI"
        assert engine.install_cmd_template == "copy <model>.safetensors into ComfyUI/models/checkpoints/"
        assert engine.well_known_ports == (8188,)


class TestIsHealthy:
    def test_true_when_system_stats_answers(self):
        fake = FakeComfyUI()
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().is_healthy(ENDPOINT) is True

    def test_false_when_unreachable(self):
        fake = FakeComfyUI(healthy=False)
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().is_healthy(ENDPOINT) is False

    def test_caller_supplied_timeout_is_used(self):
        seen = {}

        def _get(url, params=None, timeout=None):
            seen["timeout"] = timeout
            return FakeComfyUI().get(url, params, timeout)

        with patch("core.inference.engines.comfyui.httpx.get", _get):
            ComfyUIEngine().is_healthy(ENDPOINT, timeout=0.5)
        assert seen["timeout"] == 0.5


class TestListInstalled:
    def test_every_checkpoint_becomes_an_image_generation_model(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors", "sd15.ckpt"))
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)

        assert [model.model_id for model in installed] == ["sdxl.safetensors", "sd15.ckpt"]
        assert all(model.capabilities == ("image-generation",) for model in installed)
        assert all(model.size is None and model.loaded is False for model in installed)

    def test_windows_subfolder_ids_are_passed_through_verbatim(self):
        """A Windows host reports `subdir\\file.safetensors`; the id is
        opaque and must never be split, normalized, or re-cased -- it goes
        straight back to ComfyUI as `ckpt_name`."""
        fake = FakeComfyUI(checkpoints=("sdxl\\turbo.safetensors",))
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)

        assert installed[0].model_id == "sdxl\\turbo.safetensors"

    def test_no_checkpoints_is_an_empty_list(self):
        fake = FakeComfyUI(checkpoints=())
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().list_installed(ENDPOINT) == []

    def test_unreachable_endpoint_raises(self):
        """Adapters stay raw: retry/catch policy is the caller's
        (`discover()` isolates per engine+endpoint)."""
        import httpx

        with patch("core.inference.engines.comfyui.httpx.get", side_effect=httpx.ConnectError("x")):
            with pytest.raises(httpx.HTTPError):
                ComfyUIEngine().list_installed(ENDPOINT)


class TestListAssets:
    def test_each_kind_reads_its_own_loader_node(self):
        fake = FakeComfyUI(
            loras=("detail.safetensors",), vaes=("sdxl_vae.safetensors",),
            controlnets=("canny.pth",), upscalers=("4x_ultrasharp.pth",),
        )
        engine = ComfyUIEngine()
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert [a.asset_id for a in engine.list_assets(ENDPOINT, "lora")] == ["detail.safetensors"]
            assert [a.asset_id for a in engine.list_assets(ENDPOINT, "vae")] == ["sdxl_vae.safetensors"]
            assert [a.asset_id for a in engine.list_assets(ENDPOINT, "controlnet")] == ["canny.pth"]
            assert [a.asset_id for a in engine.list_assets(ENDPOINT, "upscale_model")] == ["4x_ultrasharp.pth"]
            assert all(a.kind == "lora" for a in engine.list_assets(ENDPOINT, "lora"))

    def test_unknown_kind_is_empty_without_any_http_call(self):
        called = []

        def _get(url, params=None, timeout=None):
            called.append(url)
            raise AssertionError("should not be called")

        with patch("core.inference.engines.comfyui.httpx.get", _get):
            assert ComfyUIEngine().list_assets(ENDPOINT, "embedding") == []
        assert called == []


class TestListChoices:
    def test_samplers_and_schedulers_come_from_ksampler(self):
        fake = FakeComfyUI(samplers=("euler", "heun"), schedulers=("karras", "sgm_uniform"))
        engine = ComfyUIEngine()
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert engine.list_choices(ENDPOINT, "sampler") == ("euler", "heun")
            assert engine.list_choices(ENDPOINT, "scheduler") == ("karras", "sgm_uniform")

    def test_unknown_param_key_is_empty(self):
        fake = FakeComfyUI()
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().list_choices(ENDPOINT, "guidance") == ()


class TestSupportedOperations:
    def test_txt2img_only_in_this_cut(self):
        assert ComfyUIEngine().supported_operations("sdxl.safetensors", ENDPOINT) == ("txt2img",)


class TestSetupGuide:
    """The adapter owns its own install guide; the /setup/ page just renders
    it (spec §8 / owner requirement 2026-08-23)."""

    def test_covers_every_platform_the_page_renders(self):
        guide = ComfyUIEngine.setup_guide
        assert set(guide.platforms) == set(SETUP_PLATFORMS)
        assert all(guide.platforms[key] for key in SETUP_PLATFORMS)

    def test_every_platform_says_how_to_start_it_listening(self):
        guide = ComfyUIEngine.setup_guide
        for key in ("macos", "linux"):
            commands = " ".join(step.command or "" for step in guide.platforms[key])
            assert "--listen 0.0.0.0" in commands
        windows = " ".join(step.body for step in guide.platforms["windows"])
        assert "--listen 0.0.0.0" in windows

    def test_network_note_explains_the_container_hop(self):
        assert "host.docker.internal" in ComfyUIEngine.setup_guide.network_note

    def test_models_note_names_the_checkpoint_folder_and_who_downloads(self):
        note = ComfyUIEngine.setup_guide.models_note
        assert "models/checkpoints" in note
        assert "models/loras" in note
        assert "models/vae" in note
        assert "never downloads" in note

    def test_verify_path_matches_the_health_check(self):
        assert ComfyUIEngine.setup_guide.verify_url_path == "/system_stats"

    def test_it_declares_the_capability_it_serves(self):
        assert ComfyUIEngine.serves_capabilities == ("image-generation",)


class TestUnsupportedRoles:
    def test_build_llm_refuses(self):
        with pytest.raises(NotImplementedError, match="image generation only"):
            ComfyUIEngine().build_llm("sdxl.safetensors", ENDPOINT)

    def test_build_embedder_refuses(self):
        with pytest.raises(NotImplementedError, match="image generation only"):
            ComfyUIEngine().build_embedder("sdxl.safetensors", ENDPOINT)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest modules/vision/tests/test_comfyui_engine.py -q`
Expected: FAIL — `No module named 'core.inference.engines.comfyui'`.

- [ ] **Step 4: Write `core/inference/engines/comfyui.py`**

```python
"""
ComfyUI inference engine adapter (D1, D2).

Implements the `InferenceEngine` seam (core/inference/engines/base.py) for
ComfyUI's HTTP API, and its `ImageGenerator` half in `ComfyUIGenerator`.
ComfyUI's graph vocabulary -- node class names, node ids, the `/prompt`
payload shape -- lives ONLY in this module and `comfyui_workflows/`; above
this line the platform speaks `Operation`, `GenerationRequest`,
`JobStatus`, `InstalledModel`, `Asset`.

Nothing here downloads anything. `list_installed` reports what the operator
already placed in `ComfyUI/models/checkpoints/`, and `install_cmd_template`
tells the console to say exactly that.

HTTP is plain `httpx` with per-call timeouts and NO retries: retry policy is
a caller concern (the service layer decides), the same way `ollama.py`
leaves its errors raw for `discover()` to isolate.
"""
from __future__ import annotations

import httpx

from core.inference.engines.base import Asset, InstalledModel, SetupGuide, SetupStep

# A generation can legitimately run for minutes; submission and output
# fetching ride the same generous ceiling as Ollama's generation timeout.
DEFAULT_REQUEST_TIMEOUT = 300.0

# Health/listing calls are polled by pages and must fail fast instead.
DISCOVERY_TIMEOUT = 5.0

# Which ComfyUI loader node reports each platform asset kind, and which of
# its required inputs holds the file list. Engine-specific by nature, so the
# mapping lives here rather than in the platform layer. "embedding" is
# deliberately absent: ComfyUI has no embedding LOADER node (embeddings are
# referenced from prompt text), so that kind honestly reports nothing.
_ASSET_NODES: dict[str, tuple[str, str]] = {
    "lora": ("LoraLoader", "lora_name"),
    "vae": ("VAELoader", "vae_name"),
    "controlnet": ("ControlNetLoader", "control_net_name"),
    "upscale_model": ("UpscaleModelLoader", "model_name"),
}

# Which node input backs each platform `"choice"` param.
_CHOICE_INPUTS: dict[str, tuple[str, str]] = {
    "sampler": ("KSampler", "sampler_name"),
    "scheduler": ("KSampler", "scheduler"),
}

# The node whose `ckpt_name` combo IS the installed-checkpoint list.
_CHECKPOINT_NODE = ("CheckpointLoaderSimple", "ckpt_name")


def _combo_values(endpoint: str, node: str, input_key: str, timeout: float | None = None) -> tuple[str, ...]:
    """The allowed values of one node input, from `/object_info/<node>`.

    ComfyUI answers `{"<Node>": {"input": {"required": {"<key>": [[...values...],
    {...options...}]}}}}` -- a combo input's spec is the list of values in
    element 0. Anything else (a typed input like `["INT", {...}]`, a node
    this build doesn't have) yields `()` rather than raising: an absent
    optional node is not an error, an unreachable server is (and propagates).
    """
    response = httpx.get(
        f"{endpoint}/object_info/{node}",
        timeout=DISCOVERY_TIMEOUT if timeout is None else timeout,
    )
    response.raise_for_status()
    spec = (
        (response.json() or {})
        .get(node, {})
        .get("input", {})
        .get("required", {})
        .get(input_key)
    )
    if not spec or not isinstance(spec, list):
        return ()
    values = spec[0]
    if not isinstance(values, list):
        return ()
    return tuple(str(value) for value in values)


class ComfyUIEngine:
    """`InferenceEngine` adapter for ComfyUI."""

    name = "comfyui"

    # The console's "Supported APIs" disclosure prints this verbatim.
    api_description = "the ComfyUI HTTP API"

    # Where an operator browses the engine itself. Printed as plain text,
    # never fetched -- this platform makes no outbound calls on an
    # operator's behalf. ComfyUI has no model library of its own (checkpoints
    # come from wherever the operator chooses), so this points at the engine.
    library_url = "https://github.com/comfyanonymous/ComfyUI"

    # The SHAPE of getting a model here: a file copy, not a download command.
    # The platform never fetches a checkpoint (ADR 0010 §5).
    install_cmd_template = "copy <model>.safetensors into ComfyUI/models/checkpoints/"

    # ComfyUI's default listen port -- the only address the console's server
    # scan knows about this engine, declared here, never in the scan loop.
    well_known_ports = (8188,)

    # Which platform capability this engine can answer at all -- the setup
    # page's "Served by" column reads this; nothing else branches on it.
    serves_capabilities = ("image-generation",)

    # How an operator installs and reaches this engine. Declared HERE
    # because only this adapter knows it; the /setup/ page renders it
    # verbatim and hardcodes nothing about ComfyUI.
    setup_guide = SetupGuide(
        summary=(
            "ComfyUI runs natively on the host so it can use the GPU (Metal on macOS, "
            "CUDA or ROCm on Linux, CUDA on Windows). farabunker talks to it over HTTP "
            "and never downloads a model for you."
        ),
        platforms={
            "macos": (
                SetupStep(
                    "Clone ComfyUI",
                    "Anywhere outside the farabunker repo.",
                    "git clone https://github.com/comfyanonymous/ComfyUI",
                ),
                SetupStep(
                    "Create a virtual environment",
                    "Keeps ComfyUI's dependencies off your system Python.",
                    "python3 -m venv venv && source venv/bin/activate",
                ),
                SetupStep(
                    "Install PyTorch",
                    "The default wheels carry Apple Silicon (Metal) support.",
                    "pip install torch torchvision torchaudio",
                ),
                SetupStep(
                    "Install ComfyUI's requirements",
                    "Built-in nodes only — farabunker needs no custom nodes and no Manager.",
                    "pip install -r requirements.txt",
                ),
                SetupStep(
                    "Start it",
                    "Listen on all interfaces, or the farabunker container cannot reach it.",
                    "python main.py --listen 0.0.0.0 --port 8188",
                ),
            ),
            "windows": (
                SetupStep(
                    "Download the portable build",
                    "Unzip the release archive anywhere; it carries its own Python and PyTorch.",
                ),
                SetupStep(
                    "Add the listen flag",
                    "Edit run_nvidia_gpu.bat (or run_cpu.bat) and append --listen 0.0.0.0 "
                    "to the python line it runs.",
                ),
                SetupStep("Start it", "Double-click the .bat file you just edited."),
                SetupStep(
                    "Or install from source instead",
                    "Same route as macOS/Linux if you prefer a checkout you control.",
                    "git clone https://github.com/comfyanonymous/ComfyUI",
                ),
            ),
            "linux": (
                SetupStep(
                    "Clone ComfyUI",
                    "Anywhere outside the farabunker repo.",
                    "git clone https://github.com/comfyanonymous/ComfyUI",
                ),
                SetupStep(
                    "Create a virtual environment",
                    "",
                    "python3 -m venv venv && source venv/bin/activate",
                ),
                SetupStep(
                    "Install PyTorch for your accelerator",
                    "Pick the CUDA or ROCm wheel index matching your GPU driver.",
                    "pip install torch torchvision torchaudio "
                    "--index-url https://download.pytorch.org/whl/cu124",
                ),
                SetupStep("Install ComfyUI's requirements", "", "pip install -r requirements.txt"),
                SetupStep(
                    "Start it",
                    "Listen on all interfaces, or the farabunker container cannot reach it.",
                    "python main.py --listen 0.0.0.0 --port 8188",
                ),
            ),
        },
        network_note=(
            "Start ComfyUI with --listen 0.0.0.0. It binds to localhost by default, and the "
            "farabunker web container reaches your machine as host.docker.internal — a "
            "localhost-only server is invisible to it."
        ),
        models_note=(
            "Place model files yourself: checkpoints (.safetensors) in "
            "ComfyUI/models/checkpoints/, LoRAs in models/loras/, VAEs in models/vae/. "
            "farabunker never downloads a model — community checkpoints come from Civitai "
            "or Hugging Face, and which one to run is your decision, not the platform's."
        ),
        verify_url_path="/system_stats",
    )

    def is_healthy(self, endpoint: str, timeout: float | None = None) -> bool:
        """True if `endpoint` answers `/system_stats` with 200."""
        try:
            response = httpx.get(
                f"{endpoint}/system_stats",
                timeout=DISCOVERY_TIMEOUT if timeout is None else timeout,
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def list_installed(self, endpoint: str) -> list[InstalledModel]:
        """Every checkpoint ComfyUI can load at `endpoint`.

        `model_id` is ComfyUI's exact string and is OPAQUE -- a Windows host
        reports `subdir\\file.safetensors`, which must go back unchanged as
        `ckpt_name`. ComfyUI reports no size and no loaded state for
        checkpoints, so those stay `None`/`False` rather than being guessed.
        """
        node, input_key = _CHECKPOINT_NODE
        return [
            InstalledModel(model_id=name, capabilities=("image-generation",))
            for name in _combo_values(endpoint, node, input_key)
        ]

    def list_assets(self, endpoint: str, kind: str) -> list[Asset]:
        """Assets of `kind` installed at `endpoint`; `[]` for a kind ComfyUI
        has no loader node for (e.g. "embedding")."""
        mapping = _ASSET_NODES.get(kind)
        if mapping is None:
            return []
        node, input_key = mapping
        return [Asset(kind=kind, asset_id=name) for name in _combo_values(endpoint, node, input_key)]

    def list_choices(self, endpoint: str, param_key: str) -> tuple[str, ...]:
        """Live options for a `"choice"` param (sampler, scheduler); `()`
        for a key this engine doesn't report."""
        mapping = _CHOICE_INPUTS.get(param_key)
        if mapping is None:
            return ()
        node, input_key = mapping
        return _combo_values(endpoint, node, input_key)

    def supported_operations(self, model_id: str, endpoint: str) -> tuple[str, ...]:
        """Operations this adapter has a graph template for. One in this cut;
        adding img2img is one template plus one entry here (spec §9)."""
        return ("txt2img",)

    def build_llm(self, model_id: str, endpoint: str, **cfg):
        raise NotImplementedError("comfyui serves image generation only")

    def build_embedder(self, model_id: str, endpoint: str, **cfg):
        raise NotImplementedError("comfyui serves image generation only")
```

- [ ] **Step 5: Register the adapter**

In `core/inference/engines/__init__.py`, add the import and registration:

```python
from core.inference.engines.comfyui import ComfyUIEngine
```

and, after `register(OllamaEngine())`:

```python
register(ComfyUIEngine())
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest modules/vision/tests/test_comfyui_engine.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all PASS. Two engines are now registered, so the console's server scan probes port 8188 too and `discover()` polls ComfyUI at `INFERENCE_DEFAULT_ENDPOINTS["comfyui"]` — both already covered by Task 2's per-engine isolation.

- [ ] **Step 8: Commit**

```bash
git add core/inference/engines/comfyui.py core/inference/engines/__init__.py \
        modules/vision/tests/_helpers.py modules/vision/tests/test_comfyui_engine.py
git commit -F - <<'EOF'
feat(inference): ComfyUI engine adapter — health, listing, assets, choices, setup guide

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 6: `comfyui_workflows/` — the txt2img graph template

**Suggested implementer tier:** sonnet — small module, but every parameter must land on exactly the right node input.

**Files:**
- Create: `core/inference/engines/comfyui_workflows/__init__.py`
- Create: `core/inference/engines/comfyui_workflows/txt2img.py`
- Test: `modules/vision/tests/test_comfyui_workflows.py`

**Interfaces:**
- Consumes: `GenerationRequest` (Task 3).
- Produces:
  - `get_template(operation_key: str) -> Callable[[GenerationRequest, str, dict], dict]`; raises `ValueError` for an unknown key.
  - `txt2img.build(request, model_id, config) -> dict` — a ComfyUI API-format graph, nodes `"1"`–`"7"`.

- [ ] **Step 1: Write the failing tests**

Create `modules/vision/tests/test_comfyui_workflows.py`:

```python
"""Unit tests for the ComfyUI graph templates (spec §4.4)."""
from __future__ import annotations

import pytest

from core.inference.engines.comfyui_workflows import get_template
from core.inference.operations import GenerationRequest

PARAMS = {
    "prompt": "a lighthouse at dusk",
    "negative_prompt": "blurry",
    "width": 832,
    "height": 1216,
    "steps": 30,
    "cfg_scale": 6.5,
    "seed": 123456,
    "sampler": "dpmpp_2m",
    "scheduler": "karras",
    "batch_size": 2,
}


def _graph(config: dict | None = None) -> dict:
    request = GenerationRequest(
        operation="txt2img",
        model_id="sdxl\\turbo.safetensors",
        params=dict(PARAMS),
        client_ref="8f14e45f-ceea-467a-9575-1b0e5a1d1f1e",
    )
    return get_template("txt2img")(request, request.model_id, config or {})


class TestGetTemplate:
    def test_unknown_operation_is_a_clear_error(self):
        with pytest.raises(ValueError, match="No ComfyUI template"):
            get_template("inpaint")


class TestTxt2ImgGraph:
    def test_checkpoint_node_carries_the_opaque_model_id(self):
        node = _graph()["1"]
        assert node["class_type"] == "CheckpointLoaderSimple"
        assert node["inputs"]["ckpt_name"] == "sdxl\\turbo.safetensors"

    def test_positive_and_negative_prompts_land_on_their_own_encoders(self):
        graph = _graph()
        assert graph["2"]["class_type"] == "CLIPTextEncode"
        assert graph["2"]["inputs"]["text"] == "a lighthouse at dusk"
        assert graph["2"]["inputs"]["clip"] == ["1", 1]
        assert graph["3"]["inputs"]["text"] == "blurry"
        assert graph["3"]["inputs"]["clip"] == ["1", 1]

    def test_latent_carries_size_and_batch(self):
        inputs = _graph()["4"]["inputs"]
        assert inputs == {"width": 832, "height": 1216, "batch_size": 2}

    def test_sampler_node_carries_every_sampling_param_and_its_wiring(self):
        inputs = _graph()["5"]["inputs"]
        assert inputs["seed"] == 123456
        assert inputs["steps"] == 30
        assert inputs["cfg"] == 6.5
        assert inputs["sampler_name"] == "dpmpp_2m"
        assert inputs["scheduler"] == "karras"
        assert inputs["denoise"] == 1.0
        assert inputs["model"] == ["1", 0]
        assert inputs["positive"] == ["2", 0]
        assert inputs["negative"] == ["3", 0]
        assert inputs["latent_image"] == ["4", 0]

    def test_decode_and_save_wiring(self):
        graph = _graph()
        assert graph["6"]["class_type"] == "VAEDecode"
        assert graph["6"]["inputs"] == {"samples": ["5", 0], "vae": ["1", 2]}
        assert graph["7"]["class_type"] == "SaveImage"
        assert graph["7"]["inputs"]["images"] == ["6", 0]

    def test_filename_prefix_is_the_client_ref(self):
        """The job UUID prefixes every engine-side output file, so a result
        found on the engine can always be traced back to its job."""
        assert _graph()["7"]["inputs"]["filename_prefix"] == "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"

    def test_connection_config_is_ignored_by_this_template(self):
        """`cfg` is reserved for multi-file families (a Flux template will
        read `cfg["unet"]`/`cfg["clip"]`/`cfg["vae"]`); a single-file SD
        checkpoint needs none of it, and an unexpected key must never leak
        into the graph."""
        assert _graph({"unet": "flux.safetensors"}) == _graph({})

    def test_missing_negative_prompt_becomes_an_empty_encoder(self):
        request = GenerationRequest(
            operation="txt2img",
            model_id="sd15.ckpt",
            params={k: v for k, v in PARAMS.items() if k != "negative_prompt"},
            client_ref="ref",
        )
        graph = get_template("txt2img")(request, request.model_id, {})
        assert graph["3"]["inputs"]["text"] == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest modules/vision/tests/test_comfyui_workflows.py -q`
Expected: FAIL — `No module named 'core.inference.engines.comfyui_workflows'`.

- [ ] **Step 3: Write the templates**

`core/inference/engines/comfyui_workflows/__init__.py`:

```python
"""
ComfyUI graph templates -- one per platform operation (spec §4.4).

Each template turns a `GenerationRequest` into a ComfyUI API-format graph:
`{"<node id>": {"class_type": "<Node>", "inputs": {...}}}`, where a link is
`[<source node id>, <output index>]`. This package and `comfyui.py` are the
ONLY places that vocabulary exists.

Adding a mode (img2img, inpaint, ControlNet, upscale) is one module here
plus one entry in `_TEMPLATES` plus one `Operation` definition -- never a
change to the page, the service layer, or the engine adapter itself.
"""
from __future__ import annotations

from typing import Callable

from core.inference.engines.comfyui_workflows import txt2img
from core.inference.operations import GenerationRequest

# operation key -> (request, model_id, connection config) -> graph
Template = Callable[[GenerationRequest, str, dict], dict]

_TEMPLATES: dict[str, Template] = {
    "txt2img": txt2img.build,
}


def get_template(operation_key: str) -> Template:
    """The graph builder for `operation_key`.

    Raises `ValueError` for an operation this engine has no template for --
    the console never offers such a pairing (role options are filtered by
    capability and `supported_operations`), so reaching this is a bug, and
    it says so plainly.
    """
    try:
        return _TEMPLATES[operation_key]
    except KeyError:
        raise ValueError(
            f"No ComfyUI template for operation {operation_key!r}; "
            f"this engine implements {sorted(_TEMPLATES)}"
        ) from None
```

`core/inference/engines/comfyui_workflows/txt2img.py`:

```python
"""Text-to-image graph: checkpoint -> two CLIP encodes -> KSampler -> VAE
decode -> SaveImage. Built from ComfyUI's BUILT-IN nodes only, so vanilla
ComfyUI with no custom nodes and no Manager can run it (D1)."""
from __future__ import annotations

from core.inference.operations import GenerationRequest


def build(request: GenerationRequest, model_id: str, config: dict) -> dict:
    """The API-format graph for one txt2img request.

    `model_id` is ComfyUI's own opaque checkpoint string (possibly with a
    Windows subfolder separator) and is passed through untouched.

    `config` is the bound connection's `ModelConnection.config` (D8),
    reserved for multi-file families -- a Flux/SD3 template will read
    `config["unet"]`, `config["clip"]`, `config["vae"]`. A single-file SD
    checkpoint needs none of it, so this template deliberately ignores it
    rather than leaking unknown keys into the graph.
    """
    params = request.params
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": model_id},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": params.get("prompt", ""), "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": params.get("negative_prompt") or "", "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": params["width"],
                "height": params["height"],
                "batch_size": params["batch_size"],
            },
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "seed": params["seed"],
                "steps": params["steps"],
                "cfg": params["cfg_scale"],
                "sampler_name": params["sampler"],
                "scheduler": params["scheduler"],
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            # The job UUID prefixes every engine-side file, so an output
            # found on the engine can be traced back to its job.
            "inputs": {"filename_prefix": request.client_ref, "images": ["6", 0]},
        },
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest modules/vision/tests/test_comfyui_workflows.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/inference/engines/comfyui_workflows modules/vision/tests/test_comfyui_workflows.py
git commit -F - <<'EOF'
feat(inference): ComfyUI txt2img graph template

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 7: `ComfyUIGenerator` — submit, status, fetch outputs

**Suggested implementer tier:** sonnet — the state-mapping logic (history vs queue vs lost) is the heart of the adapter and needs careful, exhaustive tests.

**Files:**
- Modify: `core/inference/engines/comfyui.py` (add `ComfyUIGenerator`, `build_image_generator`)
- Test: `modules/vision/tests/test_comfyui_generator.py`

**Interfaces:**
- Consumes: `get_template` (Task 6), `GenerationRejected`/`JobStatus` (Task 4), `GenerationRequest` (Task 3).
- Produces:
  - `ComfyUIEngine.build_image_generator(model_id, endpoint, **cfg) -> ComfyUIGenerator`.
  - `ComfyUIGenerator(endpoint, model_id, config=None, timeout=DEFAULT_REQUEST_TIMEOUT)` implementing `ImageGenerator`:
    - `submit(request) -> (prompt_id, {"prompt": graph, "client_id": request.client_ref})`
    - `status(engine_ref) -> JobStatus` with `state` in `queued|running|done|failed|lost`
    - `fetch_outputs(engine_ref) -> list[(filename, bytes, media_type)]`

- [ ] **Step 1: Write the failing tests**

Create `modules/vision/tests/test_comfyui_generator.py`:

```python
"""Unit tests for ComfyUIGenerator (spec §4.3, §6).

HTTP mocked at the `httpx` layer; the generator's real payload building,
state mapping, and `/view` fetching all run.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.inference.engines.base import GenerationRejected
from core.inference.engines.comfyui import ComfyUIEngine
from core.inference.operations import GenerationRequest
from modules.vision.tests._helpers import FakeComfyUI, history_error, history_success

ENDPOINT = "http://comfy.local:8188"
REF = "1a2b3c"

PARAMS = {
    "prompt": "a lighthouse", "negative_prompt": "", "width": 1024, "height": 1024,
    "steps": 25, "cfg_scale": 7.0, "seed": 99, "sampler": "euler",
    "scheduler": "normal", "batch_size": 1,
}


def _request(client_ref: str = "job-uuid") -> GenerationRequest:
    return GenerationRequest(
        operation="txt2img", model_id="sdxl.safetensors", params=dict(PARAMS), client_ref=client_ref
    )


def _generator(config: dict | None = None):
    return ComfyUIEngine().build_image_generator("sdxl.safetensors", ENDPOINT, **(config or {}))


class TestSubmit:
    def test_posts_the_graph_and_returns_the_prompt_id_with_the_verbatim_payload(self):
        fake = FakeComfyUI(prompt_id="p-42")
        with patch("core.inference.engines.comfyui.httpx.post", fake.post):
            engine_ref, payload = _generator().submit(_request())

        assert engine_ref == "p-42"
        assert payload["client_id"] == "job-uuid"
        assert payload["prompt"]["1"]["inputs"]["ckpt_name"] == "sdxl.safetensors"
        assert payload["prompt"]["5"]["inputs"]["seed"] == 99
        assert fake.submitted == [payload]

    def test_rejection_carries_comfyui_own_words(self):
        fake = FakeComfyUI(prompt_status=400)
        with patch("core.inference.engines.comfyui.httpx.post", fake.post):
            with pytest.raises(GenerationRejected) as excinfo:
                _generator().submit(_request())

        message = str(excinfo.value)
        assert "Prompt outputs failed validation" in message
        assert "not in ['sdxl.safetensors']" in message

    def test_missing_prompt_id_is_a_rejection_not_a_silent_success(self):
        fake = FakeComfyUI()
        fake.prompt_id = ""
        with patch("core.inference.engines.comfyui.httpx.post", fake.post):
            with pytest.raises(GenerationRejected, match="no prompt_id"):
                _generator().submit(_request())


class TestStatus:
    def test_running_when_the_ref_is_in_the_running_queue(self):
        fake = FakeComfyUI(queue_running=[[0, REF, {}, {}, []]])
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert _generator().status(REF).state == "running"

    def test_queued_when_the_ref_is_pending(self):
        fake = FakeComfyUI(queue_pending=[[1, REF, {}, {}, []]])
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert _generator().status(REF).state == "queued"

    def test_done_when_history_has_outputs(self):
        fake = FakeComfyUI(history=history_success(REF))
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert _generator().status(REF).state == "done"

    def test_failed_carries_the_first_execution_error_message(self):
        fake = FakeComfyUI(history=history_error(REF, "Allocation on device"))
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            state = _generator().status(REF)

        assert state.state == "failed"
        assert "Allocation on device" in state.error

    def test_lost_when_neither_history_nor_queue_knows_the_ref(self):
        fake = FakeComfyUI()
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert _generator().status(REF).state == "lost"

    def test_success_with_no_outputs_is_a_failure_not_a_silent_done(self):
        fake = FakeComfyUI(
            history={REF: {"status": {"status_str": "success", "completed": True, "messages": []}, "outputs": {}}}
        )
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            state = _generator().status(REF)

        assert state.state == "failed"
        assert "no output images" in state.error

    def test_history_entry_without_outputs_falls_back_to_the_queue(self):
        fake = FakeComfyUI(
            history={REF: {"status": {"status_str": "success", "completed": False, "messages": []}}},
            queue_running=[[0, REF, {}, {}, []]],
        )
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert _generator().status(REF).state == "running"

    def test_a_malformed_queue_entry_never_raises(self):
        fake = FakeComfyUI(queue_pending=[None, [], [1]])
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert _generator().status(REF).state == "lost"


class TestFetchOutputs:
    def test_every_output_image_is_downloaded_with_its_media_type(self):
        fake = FakeComfyUI(
            history=history_success(REF, ("job_00001_.png", "job_00002_.png")),
            images={"job_00001_.png": b"PNG-ONE", "job_00002_.png": b"PNG-TWO"},
        )
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            outputs = _generator().fetch_outputs(REF)

        assert outputs == [
            ("job_00001_.png", b"PNG-ONE", "image/png"),
            ("job_00002_.png", b"PNG-TWO", "image/png"),
        ]
        assert fake.view_calls[0] == {"filename": "job_00001_.png", "subfolder": "", "type": "output"}

    def test_temp_previews_are_skipped(self):
        history = history_success(REF)
        history[REF]["outputs"]["7"]["images"].append(
            {"filename": "preview.png", "subfolder": "", "type": "temp"}
        )
        fake = FakeComfyUI(history=history, images={"job_00001_.png": b"PNG"})
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            outputs = _generator().fetch_outputs(REF)

        assert [name for name, _, _ in outputs] == ["job_00001_.png"]

    def test_no_history_yields_nothing(self):
        fake = FakeComfyUI()
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert _generator().fetch_outputs(REF) == []

    def test_outputs_are_ordered_by_node_id(self):
        fake = FakeComfyUI(
            history={
                REF: {
                    "status": {"status_str": "success", "completed": True, "messages": []},
                    "outputs": {
                        "9": {"images": [{"filename": "b.png", "subfolder": "", "type": "output"}]},
                        "7": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]},
                    },
                }
            },
            images={"a.png": b"A", "b.png": b"B"},
        )
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert [name for name, _, _ in _generator().fetch_outputs(REF)] == ["a.png", "b.png"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest modules/vision/tests/test_comfyui_generator.py -q`
Expected: FAIL — `AttributeError: 'ComfyUIEngine' object has no attribute 'build_image_generator'`.

- [ ] **Step 3: Implement the generator**

In `core/inference/engines/comfyui.py`, extend the imports:

```python
import mimetypes

from core.inference.engines.base import Asset, GenerationRejected, InstalledModel, JobStatus
from core.inference.engines.comfyui_workflows import get_template
from core.inference.operations import GenerationRequest
```

Add `build_image_generator` to `ComfyUIEngine` (above `build_llm`):

```python
    def build_image_generator(self, model_id: str, endpoint: str, **cfg) -> "ComfyUIGenerator":
        """An `ImageGenerator` bound to `model_id` at `endpoint`.

        `cfg` is the bound connection's `config` (D8) and is handed to the
        graph template unchanged -- this adapter never interprets it.
        """
        return ComfyUIGenerator(endpoint=endpoint, model_id=model_id, config=cfg)
```

Append the generator to the module:

```python
def _media_type(filename: str) -> str:
    """Media type from the filename extension; a generic binary type when
    unrecognised. ComfyUI names its outputs, so the extension is the only
    honest signal available without sniffing bytes."""
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _rejection_message(payload: object) -> str:
    """Flatten ComfyUI's `/prompt` rejection body into one operator-readable
    line: its own `error.message` plus every `node_errors` detail. ComfyUI's
    words, never a rewritten guess."""
    if not isinstance(payload, dict):
        return "ComfyUI rejected the request."
    parts: list[str] = []
    error = payload.get("error")
    if isinstance(error, dict):
        parts.append(str(error.get("message") or error.get("type") or "").strip())
    for node_id, node_error in (payload.get("node_errors") or {}).items():
        for entry in (node_error or {}).get("errors", []):
            detail = str(entry.get("details") or entry.get("message") or "").strip()
            if detail:
                parts.append(f"node {node_id}: {detail}")
    message = " — ".join(part for part in parts if part)
    return message or "ComfyUI rejected the request."


def _execution_error(status_block: dict) -> str:
    """The first `execution_error` message ComfyUI recorded for a job.

    History `status.messages` is a list of `[event_name, payload]` pairs;
    the failure detail lives in the `execution_error` payload's
    `exception_message`.
    """
    for message in status_block.get("messages") or []:
        if isinstance(message, (list, tuple)) and len(message) == 2 and message[0] == "execution_error":
            payload = message[1] or {}
            detail = payload.get("exception_message") or payload.get("exception_type")
            if detail:
                return str(detail)
    return ""


def _queue_ref(entry: object) -> str | None:
    """The prompt id inside one `/queue` entry.

    ComfyUI's queue entries are positional lists,
    `[number, prompt_id, prompt, extra_data, outputs_to_execute]`; anything
    that doesn't fit that shape is skipped rather than crashing a poll."""
    if isinstance(entry, (list, tuple)) and len(entry) > 1:
        return str(entry[1])
    return None


class ComfyUIGenerator:
    """`ImageGenerator` for one checkpoint at one ComfyUI endpoint.

    Stateless between calls: it holds only the endpoint, model id, and the
    connection config it was built with. No retries -- the service layer
    decides what a transient failure means (spec §6: an unreachable engine
    during a poll leaves the job untouched).
    """

    def __init__(
        self,
        endpoint: str,
        model_id: str,
        config: dict | None = None,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model_id = model_id
        self.config = dict(config or {})
        self.timeout = timeout

    def submit(self, request: GenerationRequest) -> tuple[str, dict]:
        """Build the graph for `request.operation` and queue it.

        Returns `(prompt_id, payload)` where `payload` is the EXACT body
        posted, stored verbatim on the job row (D6). Raises
        `GenerationRejected` when ComfyUI refuses the graph (unknown
        checkpoint, invalid wiring) -- the caller turns that into an
        immediately-failed job carrying ComfyUI's own words.
        """
        graph = get_template(request.operation)(request, self.model_id, self.config)
        payload = {"prompt": graph, "client_id": request.client_ref}
        response = httpx.post(f"{self.endpoint}/prompt", json=payload, timeout=self.timeout)
        if response.status_code >= 400:
            raise GenerationRejected(_rejection_message(response.json()))
        prompt_id = (response.json() or {}).get("prompt_id")
        if not prompt_id:
            raise GenerationRejected("ComfyUI accepted the request but returned no prompt_id.")
        return str(prompt_id), payload

    def status(self, engine_ref: str) -> JobStatus:
        """Map ComfyUI's history + queue onto a platform `JobStatus`.

        History first (it is authoritative for a finished job), then the
        queue (running vs pending). Known to neither means ComfyUI no
        longer has this job at all -- `lost`, which the service layer
        explains to the operator. A history entry reporting success with no
        outputs is reported as a failure rather than a silent `done`: there
        is nothing to show, and saying "done" would be a lie.
        """
        history = self._history(engine_ref)
        if history is None:
            return self._queue_state(engine_ref)

        status_block = history.get("status") or {}
        if status_block.get("status_str") == "error":
            return JobStatus(
                state="failed",
                error=_execution_error(status_block) or "ComfyUI reported an execution error.",
            )
        if history.get("outputs"):
            return JobStatus(state="done")
        if status_block.get("completed"):
            return JobStatus(
                state="failed", error="ComfyUI finished the job but produced no output images."
            )
        return self._queue_state(engine_ref)

    def fetch_outputs(self, engine_ref: str) -> list[tuple[str, bytes, str]]:
        """Download every saved output image for `engine_ref`.

        Only `type == "output"` images are taken -- `temp` entries are
        ComfyUI's live previews, not results. Node ids are visited in sorted
        order so a multi-output graph always yields a stable sequence.
        """
        history = self._history(engine_ref) or {}
        outputs: list[tuple[str, bytes, str]] = []
        for _node_id, node_output in sorted((history.get("outputs") or {}).items()):
            for image in (node_output or {}).get("images") or []:
                if image.get("type") != "output":
                    continue
                params = {
                    "filename": image.get("filename", ""),
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                }
                response = httpx.get(f"{self.endpoint}/view", params=params, timeout=self.timeout)
                response.raise_for_status()
                filename = params["filename"]
                outputs.append((filename, response.content, _media_type(filename)))
        return outputs

    def _history(self, engine_ref: str) -> dict | None:
        """This job's `/history/<id>` entry, or None while ComfyUI has none."""
        response = httpx.get(f"{self.endpoint}/history/{engine_ref}", timeout=DISCOVERY_TIMEOUT)
        response.raise_for_status()
        entry = (response.json() or {}).get(engine_ref)
        return entry or None

    def _queue_state(self, engine_ref: str) -> JobStatus:
        """`running` / `queued` / `lost`, from `/queue`."""
        response = httpx.get(f"{self.endpoint}/queue", timeout=DISCOVERY_TIMEOUT)
        response.raise_for_status()
        queue = response.json() or {}
        for entry in queue.get("queue_running") or []:
            if _queue_ref(entry) == engine_ref:
                return JobStatus(state="running")
        for entry in queue.get("queue_pending") or []:
            if _queue_ref(entry) == engine_ref:
                return JobStatus(state="queued")
        return JobStatus(state="lost")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest modules/vision/tests/test_comfyui_generator.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/inference/engines/comfyui.py modules/vision/tests/test_comfyui_generator.py
git commit -F - <<'EOF'
feat(inference): ComfyUI generator — submit, status mapping, output fetching

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 8: `gateway.get_image_generator`

**Suggested implementer tier:** haiku — one function following `get_llm`'s existing shape exactly.

**Files:**
- Modify: `core/inference/gateway.py`
- Test: `modules/vision/tests/test_gateway.py`

**Interfaces:**
- Consumes: `resolve` (`core.inference.bindings`), `get_engine`, `VISION_GENERATE_ROLE`, `ComfyUIGenerator`.
- Produces: `get_image_generator(role: str = VISION_GENERATE_ROLE) -> ImageGenerator`; raises `ValueError` when the bound engine cannot generate images.

- [ ] **Step 1: Write the failing tests**

Create `modules/vision/tests/test_gateway.py`:

```python
"""Unit tests for gateway.get_image_generator (spec §4.5).

Uses real DB rows (a registered connection + role binding) so the whole
resolve -> get_engine -> build path runs; no HTTP happens until the
generator is actually used.
"""
from __future__ import annotations

import pytest

from console.inference.models import ModelConnection, RoleBinding
from core.inference.engines.comfyui import ComfyUIGenerator
from core.inference.gateway import get_image_generator
from core.inference.roles import VISION_GENERATE_ROLE
from modules.vision.tests._helpers import clear_bindings


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


def _bind(engine: str, model_id: str, endpoint: str, config=None) -> ModelConnection:
    connection = ModelConnection.objects.create(
        name=f"{engine} {model_id}", engine=engine, endpoint=endpoint,
        model_id=model_id, capabilities=["image-generation"], config=config,
    )
    RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)
    return connection


@pytest.mark.django_db
class TestGetImageGenerator:
    def test_builds_the_bound_engine_generator(self):
        _bind("comfyui", "sdxl.safetensors", "http://comfy.local:8188")

        generator = get_image_generator()

        assert isinstance(generator, ComfyUIGenerator)
        assert generator.model_id == "sdxl.safetensors"
        assert generator.endpoint == "http://comfy.local:8188"

    def test_connection_config_reaches_the_generator(self):
        _bind("comfyui", "flux.safetensors", "http://comfy.local:8188", config={"vae": "ae.safetensors"})

        assert get_image_generator().config == {"vae": "ae.safetensors"}

    def test_unbound_role_raises_the_resolver_error(self):
        with pytest.raises(ValueError, match="No inference binding resolved"):
            get_image_generator()

    def test_engine_that_cannot_generate_images_says_so(self):
        _bind("ollama", "llama3.1:8b", "http://localhost:11434")

        with pytest.raises(ValueError, match="cannot generate images"):
            get_image_generator()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest modules/vision/tests/test_gateway.py -q`
Expected: FAIL — `ImportError: cannot import name 'get_image_generator'`.

- [ ] **Step 3: Add the gateway function**

In `core/inference/gateway.py`, extend the roles import to include `VISION_GENERATE_ROLE` and append:

```python
def get_image_generator(role: str = VISION_GENERATE_ROLE):
    """Return an `ImageGenerator` for `role` (spec §4.5).

    Same resolve-then-build path as `get_llm()`: the role resolves to an
    engine + model, and THAT engine builds the generator. The gateway
    constructs nothing itself, so a second image engine is a new adapter
    and nothing else.

    Raises `ValueError` if the bound engine has no `build_image_generator`
    -- reachable only if an operator bound an image-generation role to a
    non-generating engine, since role options are filtered by capability.
    """
    resolved = resolve(role)
    engine = get_engine(resolved.engine)
    builder = getattr(engine, "build_image_generator", None)
    if builder is None:
        raise ValueError(
            f"Engine {resolved.engine!r} cannot generate images "
            f"(no build_image_generator); bind {role!r} to an image-generation engine."
        )
    return builder(resolved.model_id, resolved.endpoint, **resolved.config)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest modules/vision/tests/test_gateway.py -q` (needs `docker compose up -d db`)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/inference/gateway.py modules/vision/tests/test_gateway.py
git commit -F - <<'EOF'
feat(inference): gateway.get_image_generator resolves the vision.generate role

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 9: `modules/vision` — job records, managed store, role + operation registration

**Suggested implementer tier:** sonnet — standard Django models/migration plus a store mirroring `modules/rag/store.py`.

**Files:**
- Create: `modules/vision/models.py`, `modules/vision/store.py`, `modules/vision/migrations/0001_initial.py`
- Modify: `modules/vision/apps.py` (register role + operation)
- Test: `modules/vision/tests/test_models.py`, `modules/vision/tests/test_store.py`, `modules/vision/tests/test_apps.py`

**Interfaces:**
- Consumes: `settings.GENERATED_DIR`, `settings.VISION_STALE_AFTER`, `settings.FARABUNKER_FEATURES` (Task 1); `TXT2IMG`, `register_operation` (Task 3); `VISION_GENERATE_ROLE`, `RoleSpec`, `register_role`.
- Produces:
  - `GenerationJob` (UUID pk) with `operation`, `params`, `seed`, `engine`, `model_id`, `model_fingerprint`, `model_config`, `engine_ref`, `engine_payload`, `status`, `error`, `created_at`, `started_at`, `finished_at`; `Status` text choices `queued|running|done|failed`; properties `is_terminal`, `is_stale`.
  - `JobInput(job, param_key, path, media_type)` (`related_name="inputs"`), `GeneratedOutput(job, index, path, media_type, width, height, created_at)` (`related_name="outputs"`).
  - `store.job_dir(job_id) -> Path`, `store.store_input(job_id, param_key, uploaded) -> str`, `store.store_output(job_id, index, filename, content) -> str`, `store.remove_job_files(job_id) -> None`, `store.png_dimensions(content) -> tuple[int | None, int | None]`.
  - `VisionConfig.ready()` registering `RoleSpec(VISION_GENERATE_ROLE, "Image generation", "image-generation")` and `TXT2IMG` when the feature is on.

- [ ] **Step 1: Write the failing tests**

Create `modules/vision/tests/test_models.py`:

```python
"""Unit tests for modules/vision/models.py (spec §4.7)."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from modules.vision.models import GeneratedOutput, GenerationJob, JobInput


def _job(**overrides) -> GenerationJob:
    fields = {
        "operation": "txt2img",
        "params": {"prompt": "a lighthouse", "seed": 7},
        "seed": 7,
        "engine": "comfyui",
        "model_id": "sdxl.safetensors",
        "model_fingerprint": "comfyui:sdxl.safetensors:None",
        "model_config": {},
    }
    fields.update(overrides)
    return GenerationJob.objects.create(**fields)


@pytest.mark.django_db
class TestGenerationJob:
    def test_primary_key_is_a_uuid_and_defaults_to_queued(self):
        job = _job()
        assert isinstance(job.id, uuid.UUID)
        assert job.status == GenerationJob.Status.QUEUED
        assert job.error == ""
        assert job.engine_ref == ""
        assert job.engine_payload == {}

    def test_terminal_states(self):
        assert _job(status=GenerationJob.Status.DONE).is_terminal is True
        assert _job(status=GenerationJob.Status.FAILED).is_terminal is True
        assert _job(status=GenerationJob.Status.RUNNING).is_terminal is False
        assert _job(status=GenerationJob.Status.QUEUED).is_terminal is False

    @override_settings(VISION_STALE_AFTER=timedelta(minutes=10))
    def test_a_long_queued_job_reads_as_stale(self):
        job = _job()
        GenerationJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - timedelta(minutes=11)
        )
        job.refresh_from_db()
        assert job.is_stale is True

    @override_settings(VISION_STALE_AFTER=timedelta(minutes=10))
    def test_a_finished_job_is_never_stale(self):
        job = _job(status=GenerationJob.Status.DONE)
        GenerationJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        job.refresh_from_db()
        assert job.is_stale is False

    def test_newest_first_ordering(self):
        first = _job()
        second = _job()
        assert list(GenerationJob.objects.all()) == [second, first]


@pytest.mark.django_db
class TestJobChildren:
    def test_inputs_and_outputs_cascade_with_the_job(self):
        job = _job()
        JobInput.objects.create(job=job, param_key="init_image", path="/tmp/a.png", media_type="image/png")
        GeneratedOutput.objects.create(
            job=job, index=0, path="/tmp/out.png", media_type="image/png", width=1024, height=1024
        )

        assert job.inputs.count() == 1
        assert job.outputs.count() == 1

        job.delete()
        assert GeneratedOutput.objects.count() == 0
        assert JobInput.objects.count() == 0

    def test_outputs_are_ordered_by_index(self):
        job = _job()
        for index in (2, 0, 1):
            GeneratedOutput.objects.create(
                job=job, index=index, path=f"/tmp/{index}.png", media_type="image/png"
            )
        assert [output.index for output in job.outputs.all()] == [0, 1, 2]
```

Create `modules/vision/tests/test_store.py`:

```python
"""Unit tests for modules/vision/store.py (spec §5)."""
from __future__ import annotations

import struct
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from modules.vision import store


def _png(width: int, height: int) -> bytes:
    """A minimal but REAL PNG header: signature + IHDR length/type/size."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"
    )


class TestJobDir:
    def test_lives_under_the_generated_dir(self, tmp_path):
        job_id = uuid.uuid4()
        with override_settings(GENERATED_DIR=tmp_path):
            assert store.job_dir(job_id) == tmp_path / str(job_id)

    def test_does_not_create_the_directory(self, tmp_path):
        job_id = uuid.uuid4()
        with override_settings(GENERATED_DIR=tmp_path):
            store.job_dir(job_id)
            assert not (tmp_path / str(job_id)).exists()


class TestStoreOutput:
    def test_writes_the_bytes_and_returns_the_path(self, tmp_path):
        job_id = uuid.uuid4()
        with override_settings(GENERATED_DIR=tmp_path):
            path = store.store_output(job_id, 0, "job_00001_.png", b"PNG-BYTES")

        assert (tmp_path / str(job_id) / "0-job_00001_.png").read_bytes() == b"PNG-BYTES"
        assert path.endswith("0-job_00001_.png")

    def test_engine_supplied_paths_cannot_escape_the_job_directory(self, tmp_path):
        """The filename comes from the ENGINE, so it is treated as untrusted:
        only its basename is ever used."""
        job_id = uuid.uuid4()
        with override_settings(GENERATED_DIR=tmp_path):
            path = store.store_output(job_id, 0, "../../etc/passwd", b"x")

        assert str(tmp_path / str(job_id)) in path
        assert not (tmp_path / "etc").exists()


class TestStoreInput:
    def test_writes_an_uploaded_file_under_the_job_inputs_dir(self, tmp_path):
        job_id = uuid.uuid4()
        upload = SimpleUploadedFile("seed.png", b"IMG", content_type="image/png")
        with override_settings(GENERATED_DIR=tmp_path):
            path = store.store_input(job_id, "init_image", upload)

        assert (tmp_path / str(job_id) / "inputs" / "init_image-seed.png").read_bytes() == b"IMG"
        assert path.endswith("init_image-seed.png")


class TestRemoveJobFiles:
    def test_deletes_the_whole_job_tree(self, tmp_path):
        job_id = uuid.uuid4()
        with override_settings(GENERATED_DIR=tmp_path):
            store.store_output(job_id, 0, "a.png", b"x")
            store.remove_job_files(job_id)

        assert not (tmp_path / str(job_id)).exists()

    def test_missing_directory_is_not_an_error(self, tmp_path):
        with override_settings(GENERATED_DIR=tmp_path):
            store.remove_job_files(uuid.uuid4())


class TestPngDimensions:
    def test_reads_width_and_height_from_the_header(self):
        assert store.png_dimensions(_png(832, 1216)) == (832, 1216)

    def test_non_png_content_is_unknown_not_an_error(self):
        assert store.png_dimensions(b"not a png at all") == (None, None)

    def test_truncated_png_is_unknown(self):
        assert store.png_dimensions(b"\x89PNG\r\n\x1a\n") == (None, None)
```

Create `modules/vision/tests/test_apps.py`:

```python
"""Unit tests for modules/vision/apps.py -- feature-gated registration (D9)."""
from __future__ import annotations

from django.apps import apps as django_apps
from django.test import override_settings

from core.inference import operations as operations_module
from core.inference import roles as roles_module
from core.inference.operations import TXT2IMG, get_operation
from core.inference.roles import VISION_GENERATE_ROLE, get_role


def _run_ready_with(features):
    """Call `VisionConfig.ready()` under a feature setting, with both global
    registries saved and restored -- app startup already registered the real
    entries and the rest of the suite depends on them."""
    saved_roles = dict(roles_module._ROLES)
    saved_operations = dict(operations_module._OPERATIONS)
    roles_module._ROLES.pop(VISION_GENERATE_ROLE, None)
    operations_module._OPERATIONS.pop(TXT2IMG.key, None)
    try:
        with override_settings(FARABUNKER_FEATURES=features):
            django_apps.get_app_config("vision").ready()
        return get_role(VISION_GENERATE_ROLE), get_operation(TXT2IMG.key)
    finally:
        roles_module._ROLES.clear()
        roles_module._ROLES.update(saved_roles)
        operations_module._OPERATIONS.clear()
        operations_module._OPERATIONS.update(saved_operations)


class TestVisionRegistration:
    def test_role_and_operation_registered_when_the_feature_is_on(self):
        role, operation = _run_ready_with(frozenset({"vision"}))

        assert role is not None
        assert role.key == "vision.generate"
        assert role.label == "Image generation"
        assert role.capability == "image-generation"
        assert role.rematerialize is None
        assert operation is TXT2IMG

    def test_nothing_is_registered_when_the_feature_is_off(self):
        role, operation = _run_ready_with(frozenset())

        assert role is None
        assert operation is None

    def test_the_real_startup_registered_them(self):
        """The running app registers at import time -- proof the wiring is
        live, not just callable."""
        assert get_role(VISION_GENERATE_ROLE) is not None
        assert get_operation("txt2img") is TXT2IMG
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest modules/vision/tests/test_models.py modules/vision/tests/test_store.py modules/vision/tests/test_apps.py -q`
Expected: FAIL — `No module named 'modules.vision.models'`.

- [ ] **Step 3: Write `modules/vision/models.py`**

```python
"""
Generation job records (spec §4.7, D6).

Deliberately MEDIA-GENERIC: a job records what was asked, which model
answered, the exact payload the engine received, and where the results
landed. Video or audio later are new `media_type` values on the same
tables, not new tables.

Every result is reproducible: `params` (validated, seed resolved),
`model_fingerprint` + `model_config` (the binding as it was AT SUBMIT
TIME, not as it is now), and `engine_payload` (the verbatim submission).
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class GenerationJob(models.Model):
    """One generation request and its lifecycle.

    The primary key is a UUID because it is also the engine-side filename
    prefix (`GenerationRequest.client_ref`) and the job directory name under
    `GENERATED_DIR` -- a sequential integer would leak ordering into the
    engine's own output folder and collide across machines.

    There is no "lost" status: an engine that no longer knows a job leaves
    the record `failed` with an explaining message, because from the
    operator's side the job did not produce anything.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    TERMINAL_STATUSES = (Status.DONE, Status.FAILED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.CharField(max_length=64)
    # `validate_params` output -- coerced, range-checked, seed resolved.
    params = models.JSONField(default=dict)
    # The resolved seed, duplicated out of `params` because it is the one
    # value an operator reuses by hand ("same seed, one more step").
    seed = models.BigIntegerField()
    engine = models.CharField(max_length=64)
    model_id = models.CharField(max_length=255)
    # `ResolvedModel.fingerprint` at submit time -- rebinding the role later
    # must not rewrite history.
    model_fingerprint = models.CharField(max_length=255)
    model_config = models.JSONField(default=dict, blank=True)
    # The engine's own job handle (ComfyUI: prompt_id). Blank until accepted.
    engine_ref = models.CharField(max_length=255, blank=True)
    # The EXACT body the engine received (D6): reproducible and exportable.
    engine_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.operation} {self.id}"

    @property
    def is_terminal(self) -> bool:
        """True once the job can no longer change without a resubmit."""
        return self.status in self.TERMINAL_STATUSES

    @property
    def is_stale(self) -> bool:
        """True when a non-terminal job has been waiting longer than
        `settings.VISION_STALE_AFTER` -- a HINT for the card ("check
        ComfyUI"), never a state change: it is still polled."""
        if self.is_terminal or self.created_at is None:
            return False
        return timezone.now() - self.created_at > settings.VISION_STALE_AFTER


class JobInput(models.Model):
    """A file parameter's stored input (img2img's init image, an inpaint
    mask). Empty for txt2img; the table exists so file params are a data
    addition later, not a schema change."""

    job = models.ForeignKey(GenerationJob, on_delete=models.CASCADE, related_name="inputs")
    param_key = models.CharField(max_length=64)
    path = models.CharField(max_length=1024)
    media_type = models.CharField(max_length=128, blank=True)

    class Meta:
        ordering = ["param_key"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.param_key} of {self.job_id}"


class GeneratedOutput(models.Model):
    """One produced file, copied into the managed store (spec §5).

    `width`/`height` are read from the file header when it is a format we
    can measure without a decoder dependency, and stay `None` otherwise --
    an unknown size is shown as unknown, never guessed.
    """

    job = models.ForeignKey(GenerationJob, on_delete=models.CASCADE, related_name="outputs")
    index = models.PositiveIntegerField(default=0)
    path = models.CharField(max_length=1024)
    media_type = models.CharField(max_length=128, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["index"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"output {self.index} of {self.job_id}"
```

- [ ] **Step 4: Write `modules/vision/store.py`**

```python
"""
Managed generated-media store (spec §5).

Single source of truth for where a job's files live: one directory per job,
`<GENERATED_DIR>/<job-uuid>/`, mirroring the document store's
`<DOCUMENTS_DIR>/<doc-id>/` shape (ADR 0009). Outputs are COPIED out of the
engine and never read from the engine's own output folder afterwards -- the
engine may be on another machine, or wiped.

Filenames coming back from an engine are untrusted input: only their
basename is ever used, so an engine (or a tampered response) can never write
outside a job's directory.
"""
from __future__ import annotations

import shutil
import struct
from pathlib import Path

from django.conf import settings

# PNG magic + the fixed offsets of the IHDR width/height fields. Reading two
# integers out of a header is cheaper and far lighter than adding an image
# decoding dependency, and PNG is what our one operation produces.
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IHDR_SIZE_OFFSET = 16
_IHDR_SIZE_END = 24


def job_dir(job_id) -> Path:
    """This job's managed directory, `<GENERATED_DIR>/<job_id>/`. Does not
    create it -- the store functions below do that when they write."""
    return settings.GENERATED_DIR / str(job_id)


def store_input(job_id, param_key: str, uploaded) -> str:
    """Write an uploaded file for `param_key` into the job's `inputs/`
    subdirectory and return its absolute path as a string."""
    dest_dir = job_dir(job_id) / "inputs"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{param_key}-{Path(uploaded.name).name}"
    with open(dest_path, "wb") as handle:
        for chunk in uploaded.chunks():
            handle.write(chunk)
    return str(dest_path)


def store_output(job_id, index: int, filename: str, content: bytes) -> str:
    """Write one engine output into the job's directory and return its
    absolute path as a string.

    The stored name is `<index>-<basename>`: the index keeps a batch's files
    ordered and collision-free, and taking only the basename means an
    engine-supplied path can never escape the job directory.
    """
    dest_dir = job_dir(job_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{index}-{Path(filename).name}"
    dest_path.write_bytes(content)
    return str(dest_path)


def remove_job_files(job_id) -> None:
    """Delete a job's entire directory tree, if present. Safe to call when
    it never existed (a job that failed before producing anything)."""
    dest_dir = job_dir(job_id)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)


def png_dimensions(content: bytes) -> tuple[int | None, int | None]:
    """`(width, height)` from a PNG header, or `(None, None)`.

    Anything that isn't a PNG long enough to carry an IHDR reads as unknown
    rather than raising: a size we cannot measure is displayed as unknown,
    never guessed, and a future non-PNG output must not break job refresh.
    """
    if not content.startswith(_PNG_SIGNATURE) or len(content) < _IHDR_SIZE_END:
        return (None, None)
    width, height = struct.unpack(">II", content[_IHDR_SIZE_OFFSET:_IHDR_SIZE_END])
    return (width, height)
```

- [ ] **Step 5: Register the role and the operation**

Replace `VisionConfig.ready()` in `modules/vision/apps.py`:

```python
    def ready(self) -> None:
        """Register the `vision.generate` role and the operations this app
        serves -- only while the feature is enabled (D9).

        Imports are local so app import stays light (no DB, no HTTP at
        startup), matching `modules/rag/apps.py`. The operation is DEFINED
        in `core` and merely registered here, so `all_operations()` lists
        only what an enabled feature can actually run.
        """
        if FEATURE not in settings.FARABUNKER_FEATURES:
            return

        from core.inference.operations import TXT2IMG, register_operation
        from core.inference.roles import VISION_GENERATE_ROLE, RoleSpec, register_role

        register_role(RoleSpec(VISION_GENERATE_ROLE, "Image generation", "image-generation"))
        register_operation(TXT2IMG)
```

- [ ] **Step 6: Generate the migration**

```bash
.venv/bin/python manage.py makemigrations vision
```

Expected `modules/vision/migrations/0001_initial.py`:

```python
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="GenerationJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("operation", models.CharField(max_length=64)),
                ("params", models.JSONField(default=dict)),
                ("seed", models.BigIntegerField()),
                ("engine", models.CharField(max_length=64)),
                ("model_id", models.CharField(max_length=255)),
                ("model_fingerprint", models.CharField(max_length=255)),
                ("model_config", models.JSONField(blank=True, default=dict)),
                ("engine_ref", models.CharField(blank=True, max_length=255)),
                ("engine_payload", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"), ("running", "Running"),
                            ("done", "Done"), ("failed", "Failed"),
                        ],
                        default="queued",
                        max_length=16,
                    ),
                ),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="JobInput",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("param_key", models.CharField(max_length=64)),
                ("path", models.CharField(max_length=1024)),
                ("media_type", models.CharField(blank=True, max_length=128)),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="inputs", to="vision.generationjob")),
            ],
            options={"ordering": ["param_key"]},
        ),
        migrations.CreateModel(
            name="GeneratedOutput",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("index", models.PositiveIntegerField(default=0)),
                ("path", models.CharField(max_length=1024)),
                ("media_type", models.CharField(blank=True, max_length=128)),
                ("width", models.PositiveIntegerField(blank=True, null=True)),
                ("height", models.PositiveIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="outputs", to="vision.generationjob")),
            ],
            options={"ordering": ["index"]},
        ),
    ]
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest modules/vision/tests -q`
Expected: PASS — the migration applies to the (fresh) test database, which is also the check that a preview stack's empty DB will migrate cleanly.

- [ ] **Step 8: Commit**

```bash
git add modules/vision/models.py modules/vision/store.py modules/vision/apps.py \
        modules/vision/migrations modules/vision/tests/test_models.py \
        modules/vision/tests/test_store.py modules/vision/tests/test_apps.py
git commit -F - <<'EOF'
feat(vision): generation job records, managed output store, role registration

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 10: `modules/vision/services.py` — the service layer

**Suggested implementer tier:** sonnet — the state machine every caller (page today, chatbot tool later) goes through; needs disciplined, exhaustive tests.

**Files:**
- Create: `modules/vision/services.py`
- Modify: `modules/vision/tests/_helpers.py` (add the stub engine/generator)
- Test: `modules/vision/tests/test_services.py`

**Interfaces:**
- Consumes: `preflight` inputs (`resolve`, `get_engine`), `get_image_generator` (Task 8), `validate_params`/`get_operation`/`GenerationRequest` (Task 3), models + store (Task 9).
- Produces:
  - `PreflightResult(state: str, resolved: ResolvedModel | None, message: str)` — `state` in `ready|unbound|unreachable`.
  - `VisionUnavailable(state, message)` — `RuntimeError` with `.state` / `.message`.
  - `preflight() -> PreflightResult`
  - `submit_job(operation_key: str, raw_params: dict, files: dict | None = None) -> GenerationJob`
  - `refresh_job(job: GenerationJob) -> GenerationJob` (sets a transient `job.unreachable` attribute)
  - `wait_for(job, timeout: float, interval: float = 1.0) -> GenerationJob`
  - `delete_job(job) -> None`
  - `LOST_MESSAGE: str`

- [ ] **Step 1: Extend the test helpers**

Append to `modules/vision/tests/_helpers.py`:

```python
class StubGenerator:
    """A scripted `ImageGenerator` for service-layer tests.

    The service layer's contract is with the SEAM, not with ComfyUI, so its
    tests drive a stub generator rather than an HTTP double -- the ComfyUI
    adapter has its own HTTP-layer tests (test_comfyui_*.py).
    """

    def __init__(self, *, engine_ref="ref-1", states=None, outputs=(), submit_error=None):
        self.engine_ref = engine_ref
        self.states = list(states or [])
        self.outputs = list(outputs)
        self.submit_error = submit_error
        self.submitted = []
        self.status_calls = 0
        self.fetch_calls = 0

    def submit(self, request):
        self.submitted.append(request)
        if self.submit_error is not None:
            raise self.submit_error
        return self.engine_ref, {"prompt": {"stub": True}, "client_id": request.client_ref}

    def status(self, engine_ref):
        self.status_calls += 1
        if not self.states:
            raise AssertionError("StubGenerator ran out of scripted states")
        state = self.states[0] if len(self.states) == 1 else self.states.pop(0)
        if isinstance(state, Exception):
            raise state
        return state

    def fetch_outputs(self, engine_ref):
        self.fetch_calls += 1
        return list(self.outputs)


class StubEngine:
    """A registered engine that hands back a `StubGenerator` (and can be
    unhealthy on demand), so `resolve -> get_engine -> build_image_generator`
    runs for real."""

    name = "stubengine"
    api_description = "a stub API"
    well_known_ports = (9999,)

    def __init__(self, generator=None, healthy=True):
        self.generator = generator or StubGenerator()
        self.healthy = healthy

    def is_healthy(self, endpoint, timeout=None):
        if isinstance(self.healthy, Exception):
            raise self.healthy
        return self.healthy

    def list_installed(self, endpoint):
        return []

    def build_image_generator(self, model_id, endpoint, **cfg):
        return self.generator
```

- [ ] **Step 2: Write the failing tests**

Create `modules/vision/tests/test_services.py`:

```python
"""Unit tests for modules/vision/services.py (spec §4.7, §6).

Drives a stub engine registered in the real `ENGINES` registry, so the whole
resolve -> get_engine -> build_image_generator path runs. No HTTP.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import override_settings

from console.inference.models import ModelConnection, RoleBinding
from core.inference.engines import ENGINES
from core.inference.engines.base import GenerationRejected, JobStatus
from core.inference.operations import ParamError
from core.inference.roles import VISION_GENERATE_ROLE
from modules.vision import services
from modules.vision.models import GeneratedOutput, GenerationJob
from modules.vision.tests._helpers import StubEngine, StubGenerator, clear_bindings

RAW = {
    "prompt": "a lighthouse", "negative_prompt": "", "width": "1024", "height": "1024",
    "steps": "25", "cfg_scale": "7.0", "seed": "42", "sampler": "euler",
    "scheduler": "normal", "batch_size": "1",
}

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR" + b"\x00\x00\x04\x00" + b"\x00\x00\x04\x00" + b"\x08\x06\x00\x00\x00"


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


def _bind(engine_name="stubengine"):
    connection = ModelConnection.objects.create(
        name="stub image model", engine=engine_name, endpoint="http://stub:9999",
        model_id="stub.safetensors", capabilities=["image-generation"],
    )
    RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)
    return connection


def _registered(engine):
    return patch.dict(ENGINES, {engine.name: engine})


@pytest.mark.django_db
class TestPreflight:
    def test_unbound_role(self):
        result = services.preflight()
        assert result.state == "unbound"
        assert result.resolved is None
        assert "/inference/" in result.message

    def test_unreachable_engine_names_the_endpoint(self):
        _bind()
        with _registered(StubEngine(healthy=False)):
            result = services.preflight()

        assert result.state == "unreachable"
        assert "http://stub:9999" in result.message

    def test_health_check_blowing_up_reads_as_unreachable(self):
        _bind()
        with _registered(StubEngine(healthy=RuntimeError("boom"))):
            assert services.preflight().state == "unreachable"

    def test_ready(self):
        _bind()
        with _registered(StubEngine()):
            result = services.preflight()

        assert result.state == "ready"
        assert result.resolved.model_id == "stub.safetensors"
        assert result.message == ""


@pytest.mark.django_db
class TestSubmitJob:
    def test_creates_a_queued_job_carrying_the_binding_snapshot(self):
        _bind()
        generator = StubGenerator(engine_ref="p-7")
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW))

        assert job.status == GenerationJob.Status.QUEUED
        assert job.engine_ref == "p-7"
        assert job.engine_payload["client_id"] == str(job.id)
        assert job.engine == "stubengine"
        assert job.model_id == "stub.safetensors"
        assert job.model_fingerprint == "stubengine:stub.safetensors:None"
        assert job.seed == 42
        assert job.params["width"] == 1024

    def test_the_request_carries_the_job_uuid_as_client_ref(self):
        _bind()
        generator = StubGenerator()
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW))

        assert generator.submitted[0].client_ref == str(job.id)
        assert generator.submitted[0].operation == "txt2img"

    def test_invalid_params_never_create_a_job(self):
        _bind()
        with _registered(StubEngine()):
            with pytest.raises(ParamError):
                services.submit_job("txt2img", dict(RAW, steps="9000"))

        assert GenerationJob.objects.count() == 0

    def test_unbound_role_raises_vision_unavailable(self):
        with pytest.raises(services.VisionUnavailable) as excinfo:
            services.submit_job("txt2img", dict(RAW))

        assert excinfo.value.state == "unbound"
        assert GenerationJob.objects.count() == 0

    def test_unreachable_engine_raises_vision_unavailable(self):
        _bind()
        with _registered(StubEngine(healthy=False)):
            with pytest.raises(services.VisionUnavailable) as excinfo:
                services.submit_job("txt2img", dict(RAW))

        assert excinfo.value.state == "unreachable"

    def test_engine_rejection_leaves_a_failed_job_with_the_engine_words(self):
        _bind()
        generator = StubGenerator(submit_error=GenerationRejected("ckpt_name: 'x' not in [...]"))
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW))

        assert job.status == GenerationJob.Status.FAILED
        assert "not in" in job.error
        assert job.finished_at is not None

    def test_unknown_operation_is_a_value_error(self):
        _bind()
        with _registered(StubEngine()):
            with pytest.raises(ValueError, match="Unknown operation"):
                services.submit_job("inpaint", dict(RAW))


@pytest.mark.django_db
class TestRefreshJob:
    def _submit(self, generator):
        _bind()
        with _registered(StubEngine(generator)):
            return services.submit_job("txt2img", dict(RAW))

    def test_queued_stays_queued(self):
        generator = StubGenerator(states=[JobStatus(state="queued")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            refreshed = services.refresh_job(job)

        assert refreshed.status == GenerationJob.Status.QUEUED
        assert refreshed.started_at is None

    def test_running_records_started_at_once(self):
        generator = StubGenerator(states=[JobStatus(state="running")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            first = services.refresh_job(job)
            started = first.started_at
            second = services.refresh_job(first)

        assert first.status == GenerationJob.Status.RUNNING
        assert started is not None
        assert second.started_at == started

    def test_done_stores_every_output_with_its_dimensions(self, tmp_path):
        generator = StubGenerator(
            states=[JobStatus(state="done")],
            outputs=[("a.png", PNG, "image/png"), ("b.png", PNG, "image/png")],
        )
        job = self._submit(generator)
        with _registered(StubEngine(generator)), override_settings(GENERATED_DIR=tmp_path):
            refreshed = services.refresh_job(job)

        assert refreshed.status == GenerationJob.Status.DONE
        assert refreshed.finished_at is not None
        outputs = list(refreshed.outputs.all())
        assert [output.index for output in outputs] == [0, 1]
        assert outputs[0].width == 1024 and outputs[0].height == 1024
        assert outputs[0].media_type == "image/png"

    def test_refresh_is_idempotent_on_a_terminal_job(self, tmp_path):
        generator = StubGenerator(states=[JobStatus(state="done")], outputs=[("a.png", PNG, "image/png")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)), override_settings(GENERATED_DIR=tmp_path):
            services.refresh_job(job)
            calls = generator.status_calls
            services.refresh_job(job)

        assert generator.status_calls == calls
        assert GeneratedOutput.objects.count() == 1

    def test_failed_records_the_engine_error(self):
        generator = StubGenerator(states=[JobStatus(state="failed", error="Allocation on device")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            refreshed = services.refresh_job(job)

        assert refreshed.status == GenerationJob.Status.FAILED
        assert refreshed.error == "Allocation on device"

    def test_lost_explains_what_to_do(self):
        generator = StubGenerator(states=[JobStatus(state="lost")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            refreshed = services.refresh_job(job)

        assert refreshed.status == GenerationJob.Status.FAILED
        assert refreshed.error == services.LOST_MESSAGE
        assert "resubmit" in services.LOST_MESSAGE

    def test_unreachable_during_poll_leaves_the_job_untouched(self):
        generator = StubGenerator(states=[ConnectionError("down")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            refreshed = services.refresh_job(job)

        refreshed.refresh_from_db()
        assert refreshed.status == GenerationJob.Status.QUEUED
        assert refreshed.error == ""
        assert getattr(refreshed, "unreachable", False) is True

    def test_a_job_that_never_reached_the_engine_is_not_polled(self):
        _bind()
        generator = StubGenerator(submit_error=GenerationRejected("nope"))
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW))
            services.refresh_job(job)

        assert generator.status_calls == 0


@pytest.mark.django_db
class TestWaitFor:
    def test_returns_as_soon_as_the_job_is_terminal(self, tmp_path):
        _bind()
        generator = StubGenerator(
            states=[JobStatus(state="running"), JobStatus(state="done")],
            outputs=[("a.png", PNG, "image/png")],
        )
        with _registered(StubEngine(generator)), override_settings(GENERATED_DIR=tmp_path):
            job = services.submit_job("txt2img", dict(RAW))
            finished = services.wait_for(job, timeout=5, interval=0)

        assert finished.status == GenerationJob.Status.DONE

    def test_gives_up_at_the_timeout_without_touching_the_job(self):
        _bind()
        generator = StubGenerator(states=[JobStatus(state="queued")])
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW))
            waited = services.wait_for(job, timeout=0, interval=0)

        assert waited.status == GenerationJob.Status.QUEUED


@pytest.mark.django_db
class TestDeleteJob:
    def test_removes_rows_and_files(self, tmp_path):
        _bind()
        generator = StubGenerator(states=[JobStatus(state="done")], outputs=[("a.png", PNG, "image/png")])
        with _registered(StubEngine(generator)), override_settings(GENERATED_DIR=tmp_path):
            job = services.submit_job("txt2img", dict(RAW))
            services.refresh_job(job)
            job_directory = tmp_path / str(job.id)
            assert job_directory.exists()

            services.delete_job(job)

        assert not job_directory.exists()
        assert GenerationJob.objects.count() == 0
        assert GeneratedOutput.objects.count() == 0
```

> **Note:** every test that lets `refresh_job` reach the `done` branch must run under `override_settings(GENERATED_DIR=tmp_path)` (or the `settings` fixture) — outputs are written to disk there, and they must never land in the repo's `data/`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest modules/vision/tests/test_services.py -q`
Expected: FAIL — `No module named 'modules.vision.services'`.

- [ ] **Step 4: Write `modules/vision/services.py`**

```python
"""
Vision service layer (spec §4.7) -- the surface the page uses today and the
chatbot tool will use tomorrow, unchanged.

Everything the operator's action means lives here: preflight honesty,
submission, the refresh state machine, and deletion. Views hold no
generation logic, so a second caller gets identical behaviour for free.

Idempotent by design: `refresh_job` may be called as often as a page polls,
and a terminal job is never re-fetched or re-stored.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.inference.bindings import ResolvedModel, resolve
from core.inference.engines import get_engine
from core.inference.engines.base import GenerationRejected
from core.inference.gateway import get_image_generator
from core.inference.operations import GenerationRequest, get_operation, validate_params
from core.inference.roles import VISION_GENERATE_ROLE
from modules.vision import store
from modules.vision.models import GeneratedOutput, GenerationJob, JobInput

logger = logging.getLogger(__name__)

# What the operator is told when the engine has forgotten a job. Written
# here, not in the engine adapter: the adapter reports the FACT ("lost"),
# the platform decides how to explain it.
LOST_MESSAGE = (
    "The image engine no longer has this job — it was probably restarted; resubmit to try again."
)


@dataclass(frozen=True)
class PreflightResult:
    """Three honest states, mirroring `AskView._precheck_models`: the role
    is `ready`, `unbound` (nothing assigned), or `unreachable` (assigned but
    the engine isn't answering). Never a fourth, vaguer state."""

    state: str
    resolved: ResolvedModel | None
    message: str

    @property
    def ready(self) -> bool:
        return self.state == "ready"


class VisionUnavailable(RuntimeError):
    """Raised by `submit_job` when preflight is not `ready`. Carries the
    machine-readable `state` (for the caller's status code) and the
    operator-facing `message` (already written for display)."""

    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state = state
        self.message = message


def preflight() -> PreflightResult:
    """Resolve the image-generation role and health-check its engine.

    Engine-agnostic on purpose: the message names the bound engine and its
    endpoint from the resolved binding rather than hardcoding a product
    name, so a second adapter needs no new copy here.
    """
    try:
        resolved = resolve(VISION_GENERATE_ROLE)
    except ValueError:
        logger.debug("No inference binding resolved for %r", VISION_GENERATE_ROLE, exc_info=True)
        return PreflightResult(
            "unbound",
            None,
            f"No model assigned for Image generation — assign one at {reverse('inference-console')}",
        )
    except Exception:  # noqa: BLE001 -- log detail, then degrade to the honest banner
        logger.exception("Unexpected error resolving %r", VISION_GENERATE_ROLE)
        return PreflightResult(
            "unbound",
            None,
            f"No model assigned for Image generation — assign one at {reverse('inference-console')}",
        )

    try:
        healthy = get_engine(resolved.engine).is_healthy(resolved.endpoint)
    except Exception:  # noqa: BLE001 -- an engine that blows up is unreachable, not a 500
        logger.exception("Health check failed for %s at %r", resolved.engine, resolved.endpoint)
        healthy = False

    if not healthy:
        return PreflightResult(
            "unreachable",
            resolved,
            f"The image engine ({resolved.engine}) at {resolved.endpoint} is not reachable.",
        )
    return PreflightResult("ready", resolved, "")


def submit_job(operation_key: str, raw_params: dict, files: dict | None = None) -> GenerationJob:
    """Validate, record, and queue one generation.

    Order matters and is deliberate: preflight BEFORE any row is written (no
    orphan jobs for an unbound role), validation before the job row (invalid
    input never becomes a record), then the row, then the engine call -- so
    the job's UUID already exists to be used as the engine-side filename
    prefix.

    An engine REJECTION (or any submission failure) does not raise: the job
    exists, it simply failed immediately, carrying the engine's own words.
    Raises `VisionUnavailable` when preflight fails and `ParamError` when the
    parameters don't fit the schema.
    """
    operation = get_operation(operation_key)
    if operation is None:
        raise ValueError(f"Unknown operation {operation_key!r}")

    check = preflight()
    if not check.ready:
        raise VisionUnavailable(check.state, check.message)

    params = validate_params(operation, raw_params)
    resolved = check.resolved

    job = GenerationJob.objects.create(
        operation=operation.key,
        params=params,
        seed=params["seed"],
        engine=resolved.engine,
        model_id=resolved.model_id,
        model_fingerprint=resolved.fingerprint,
        model_config=dict(resolved.config or {}),
        status=GenerationJob.Status.QUEUED,
    )

    inputs: dict[str, Path] = {}
    for param_key, uploaded in (files or {}).items():
        path = store.store_input(job.id, param_key, uploaded)
        JobInput.objects.create(
            job=job,
            param_key=param_key,
            path=path,
            media_type=getattr(uploaded, "content_type", "") or "",
        )
        inputs[param_key] = Path(path)

    request = GenerationRequest(
        operation=operation.key,
        model_id=resolved.model_id,
        params=params,
        inputs=inputs,
        client_ref=str(job.id),
    )

    try:
        engine_ref, payload = get_image_generator().submit(request)
    except GenerationRejected as exc:
        return _fail(job, str(exc))
    except Exception as exc:  # noqa: BLE001 -- the job exists; report why it never started
        logger.exception("Submitting job %s to %s failed", job.id, resolved.engine)
        return _fail(job, f"The image engine did not accept the job: {exc}")

    job.engine_ref = engine_ref
    job.engine_payload = payload
    job.save(update_fields=["engine_ref", "engine_payload"])
    return job


def refresh_job(job: GenerationJob) -> GenerationJob:
    """Bring `job` up to date with the engine and return it.

    Sets a TRANSIENT `job.unreachable` attribute (never a DB column) when the
    engine could not be reached: the card shows "engine unreachable, still
    checking" and the job itself is left exactly as it was -- a network
    hiccup must never turn a running job into a failed one.

    Idempotent: a terminal job returns immediately without an engine call,
    so polling costs nothing once a job has finished.
    """
    job.unreachable = False
    if job.is_terminal or not job.engine_ref:
        return job

    try:
        generator = get_image_generator()
        state = generator.status(job.engine_ref)
    except Exception:  # noqa: BLE001 -- transient: leave the job alone and say so
        logger.debug("Refreshing job %s failed", job.id, exc_info=True)
        job.unreachable = True
        return job

    if state.state == "queued":
        return job

    if state.state == "running":
        if job.status != GenerationJob.Status.RUNNING:
            job.status = GenerationJob.Status.RUNNING
            job.started_at = job.started_at or timezone.now()
            job.save(update_fields=["status", "started_at"])
        return job

    if state.state == "failed":
        return _fail(job, state.error or "The image engine reported a failure.")

    if state.state == "lost":
        return _fail(job, LOST_MESSAGE)

    try:
        outputs = generator.fetch_outputs(job.engine_ref)
    except Exception:  # noqa: BLE001 -- finished but not fetchable yet: try again next poll
        logger.debug("Fetching outputs for job %s failed", job.id, exc_info=True)
        job.unreachable = True
        return job

    with transaction.atomic():
        job.outputs.all().delete()
        for index, (filename, content, media_type) in enumerate(outputs):
            path = store.store_output(job.id, index, filename, content)
            width, height = store.png_dimensions(content)
            GeneratedOutput.objects.create(
                job=job, index=index, path=path, media_type=media_type, width=width, height=height
            )
        job.status = GenerationJob.Status.DONE
        job.started_at = job.started_at or job.created_at
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "started_at", "finished_at"])
    return job


def wait_for(job: GenerationJob, timeout: float, interval: float = 1.0) -> GenerationJob:
    """Poll `refresh_job` until the job is terminal or `timeout` seconds
    pass, then return it as-is.

    For SYNCHRONOUS callers (tests, a future chatbot tool, a management
    command). The page never uses this -- it polls from the browser instead,
    so a request thread is never held open by a generation.
    """
    deadline = time.monotonic() + timeout
    while True:
        job = refresh_job(job)
        if job.is_terminal or time.monotonic() >= deadline:
            return job
        time.sleep(interval)


def delete_job(job: GenerationJob) -> None:
    """Delete a job, its child rows (cascade), and its whole directory."""
    job_id = job.id
    job.delete()
    store.remove_job_files(job_id)


def _fail(job: GenerationJob, error: str) -> GenerationJob:
    """Mark `job` failed with `error`, stamping the finish time."""
    job.status = GenerationJob.Status.FAILED
    job.error = error
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error", "finished_at"])
    return job
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest modules/vision/tests/test_services.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add modules/vision/services.py modules/vision/tests/_helpers.py modules/vision/tests/test_services.py
git commit -F - <<'EOF'
feat(vision): service layer — preflight, submit, refresh, wait, delete

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 11: The create page — schema-driven form, preflight banner, route

**Suggested implementer tier:** sonnet — form generation from the schema plus a page that must be honest in three states.

**Files:**
- Create: `modules/vision/forms.py`, `modules/vision/views.py`, `modules/vision/templates/vision/base.html`, `modules/vision/templates/vision/create.html`
- Modify: `modules/vision/urls.py`
- Modify: `templates/_shell.html` (feature-gated nav link — a project-level template, NOT one of the other track's files, so it may be edited outside the coordination commit)
- Test: `modules/vision/tests/test_forms.py`, `modules/vision/tests/test_views_create.py`

**Interfaces:**
- Consumes: `TXT2IMG`, `Param` (Task 3), `preflight` (Task 10), `ComfyUIEngine.list_choices` via the resolved engine.
- Produces:
  - `forms.build_form(operation, engine_choices: dict[str, tuple[str, ...]], data=None, initial=None) -> django.forms.Form`
  - `views.CreatePageView` (`GET /vision/`), URL name `vision-create`
  - `views.live_choices(operation, resolved) -> dict[str, tuple[str, ...]]` — engine-reported options, one call per choice param, `{}` when the engine can't be asked.
  - `templates/vision/base.html` (module shell, marking the shared nav's Generate entry current), `templates/vision/create.html`
  - `templates/_shell.html` gains a fourth nav entry, **Generate**, rendered only while `"vision" in farabunker_features` (the context key from Task 1).

- [ ] **Step 1: Write the failing tests**

Create `modules/vision/tests/test_forms.py`:

```python
"""Unit tests for modules/vision/forms.py -- schema-driven form building."""
from __future__ import annotations

import pytest
from django import forms as django_forms

from core.inference.operations import TXT2IMG, Operation, Param
from modules.vision.forms import build_form

CHOICES = {"sampler": ("euler", "dpmpp_2m"), "scheduler": ("normal", "karras")}


class TestBuildForm:
    def test_every_param_becomes_a_field_of_the_right_type(self):
        form = build_form(TXT2IMG, CHOICES)
        assert isinstance(form.fields["prompt"], django_forms.CharField)
        assert isinstance(form.fields["width"], django_forms.IntegerField)
        assert isinstance(form.fields["cfg_scale"], django_forms.FloatField)
        assert isinstance(form.fields["sampler"], django_forms.ChoiceField)
        assert isinstance(form.fields["seed"], django_forms.CharField)

    def test_ranges_and_widget_hints_come_from_the_schema(self):
        form = build_form(TXT2IMG, CHOICES)
        width = form.fields["width"]
        assert width.min_value == 64
        assert width.max_value == 4096
        assert width.widget.attrs["step"] == 8
        assert form.fields["prompt"].required is True
        assert form.fields["negative_prompt"].required is False

    def test_engine_choices_populate_choice_fields_with_the_first_as_default(self):
        form = build_form(TXT2IMG, CHOICES)
        assert form.fields["sampler"].choices == [("euler", "euler"), ("dpmpp_2m", "dpmpp_2m")]
        assert form.fields["sampler"].initial == "euler"

    def test_a_choice_param_with_no_live_options_renders_a_text_input(self):
        """The engine could not be asked (unreachable). The operator can still
        type a sampler name rather than facing an empty dropdown -- and the
        engine rejects an unknown one as a failed job, honestly."""
        form = build_form(TXT2IMG, {})
        assert isinstance(form.fields["sampler"], django_forms.CharField)

    def test_initial_values_prefill_the_form(self):
        form = build_form(TXT2IMG, CHOICES, initial={"prompt": "reuse me", "steps": 40})
        assert form.fields["prompt"].initial == "reuse me"
        assert form.fields["steps"].initial == 40

    def test_bound_form_validates_against_the_live_choices(self):
        form = build_form(TXT2IMG, CHOICES, data={"prompt": "x", "sampler": "nope", "scheduler": "normal"})
        assert form.is_valid() is False
        assert "sampler" in form.errors

    def test_a_valid_bound_form_cleans_to_schema_shaped_values(self):
        form = build_form(
            TXT2IMG, CHOICES,
            data={"prompt": "x", "sampler": "euler", "scheduler": "normal",
                  "width": "512", "height": "512", "steps": "20", "cfg_scale": "5",
                  "batch_size": "1", "seed": "", "negative_prompt": ""},
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["width"] == 512

    def test_a_file_param_becomes_a_file_field(self):
        operation = Operation(
            "img2img", "Image to image", "image-generation",
            (Param("init_image", "file", "Init image", accept="image/*", required=True),),
            "image/png",
        )
        form = build_form(operation, {})
        assert isinstance(form.fields["init_image"], django_forms.FileField)
        assert form.fields["init_image"].widget.attrs["accept"] == "image/*"

    def test_an_asset_param_is_refused_until_its_ui_ships(self):
        operation = Operation(
            "lora-run", "LoRA", "image-generation",
            (Param("lora", "asset", "LoRA", asset_kind="lora"),), "image/png",
        )
        with pytest.raises(ValueError, match="asset"):
            build_form(operation, {})
```

Create `modules/vision/tests/test_views_create.py`:

```python
"""Unit tests for the /vision/ create page and the shared nav entry (spec §4.7)."""
from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest
from django.test import Client, override_settings
from django.urls import clear_url_caches, reverse

from console.inference.models import ModelConnection, RoleBinding
from core.inference.engines import ENGINES
from core.inference.roles import VISION_GENERATE_ROLE
from modules.vision.models import GenerationJob
from modules.vision.tests._helpers import StubEngine, clear_bindings


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


def _bind():
    connection = ModelConnection.objects.create(
        name="stub image model", engine="stubengine", endpoint="http://stub:9999",
        model_id="stub.safetensors", capabilities=["image-generation"],
    )
    RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)


def _engine(healthy=True, choices=None):
    engine = StubEngine(healthy=healthy)
    engine.list_choices = lambda endpoint, key: (choices or {}).get(key, ())
    return patch.dict(ENGINES, {"stubengine": engine})


@pytest.mark.django_db
class TestCreatePage:
    def test_unbound_role_shows_the_banner_and_a_link_to_the_console(self, client):
        response = client.get(reverse("vision-create"))
        body = response.content.decode()

        assert response.status_code == 200
        assert response.context["preflight"].state == "unbound"
        assert "No model assigned for Image generation" in body
        assert reverse("inference-console") in body

    def test_unreachable_engine_says_so_without_hiding_the_form(self, client):
        _bind()
        with _engine(healthy=False):
            response = client.get(reverse("vision-create"))

        body = response.content.decode()
        assert response.context["preflight"].state == "unreachable"
        assert "not reachable" in body
        assert 'name="prompt"' in body

    def test_ready_state_renders_the_schema_form_with_live_sampler_options(self, client):
        _bind()
        with _engine(choices={"sampler": ("euler", "heun"), "scheduler": ("karras",)}):
            response = client.get(reverse("vision-create"))

        body = response.content.decode()
        assert response.context["preflight"].state == "ready"
        assert '<option value="euler"' in body
        assert '<option value="karras"' in body
        assert 'name="negative_prompt"' in body
        assert 'name="steps"' in body

    def test_recent_jobs_are_listed_newest_first(self, client):
        _bind()
        older = GenerationJob.objects.create(
            operation="txt2img", params={"prompt": "one"}, seed=1, engine="stubengine",
            model_id="stub.safetensors", model_fingerprint="f",
        )
        newer = GenerationJob.objects.create(
            operation="txt2img", params={"prompt": "two"}, seed=2, engine="stubengine",
            model_id="stub.safetensors", model_fingerprint="f",
        )
        with _engine():
            response = client.get(reverse("vision-create"))

        assert [job.id for job in response.context["jobs"]] == [newer.id, older.id]

    def test_the_page_never_500s_when_the_engine_blows_up(self, client):
        _bind()
        engine = StubEngine()
        engine.list_choices = lambda endpoint, key: (_ for _ in ()).throw(RuntimeError("boom"))
        with patch.dict(ENGINES, {"stubengine": engine}):
            response = client.get(reverse("vision-create"))

        assert response.status_code == 200


@pytest.mark.django_db
class TestSharedNavEntry:
    """The shared shell (templates/_shell.html) links to /vision/ as
    "Generate", gated on the vision feature so it never points at a route
    that isn't mounted."""

    def test_the_link_appears_on_every_page_while_the_feature_is_on(self, client):
        body = client.get(reverse("rag-ask-page")).content.decode()
        assert f'href="{reverse("vision-create")}"' in body
        assert ">Generate</a>" in body

    def test_the_vision_page_marks_that_entry_current(self, client):
        body = client.get(reverse("vision-create")).content.decode()
        assert f'<a href="{reverse("vision-create")}" class="current">Generate</a>' in body

    def test_the_link_is_gone_when_the_feature_is_off(self, client):
        """With the feature off there is no /vision/ route at all, so the
        nav must not render the entry -- and the `{% url %}` inside the
        `{% if %}` must never be evaluated (an unmounted route would raise
        NoReverseMatch)."""
        from config import urls as config_urls

        with override_settings(FARABUNKER_FEATURES=frozenset()):
            importlib.reload(config_urls)
            clear_url_caches()
            try:
                body = client.get(reverse("rag-ask-page")).content.decode()
                assert ">Generate</a>" not in body
            finally:
                importlib.reload(config_urls)
                clear_url_caches()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest modules/vision/tests/test_forms.py modules/vision/tests/test_views_create.py -q`
Expected: FAIL — `No module named 'modules.vision.forms'` / `NoReverseMatch: 'vision-create'`.

- [ ] **Step 3: Write `modules/vision/forms.py`**

```python
"""
Schema-driven form building (spec §4.7).

The page never hand-writes a field: `build_form` renders an `Operation`'s
`Param` tuple into a Django form, so adding a parameter -- or a whole
operation -- changes no template and no view. Live option lists (samplers,
schedulers) come from the ENGINE at render time and are injected here,
because only the engine knows what it actually supports.
"""
from __future__ import annotations

from django import forms

from core.inference.operations import Operation, Param


def _widget_attrs(param: Param) -> dict:
    attrs: dict[str, object] = {}
    if param.step is not None:
        attrs["step"] = param.step
    if param.accept:
        attrs["accept"] = param.accept
    return attrs


def _field_for(param: Param, engine_choices: dict[str, tuple[str, ...]]) -> forms.Field:
    """One `Param` as a Django field.

    A `"choice"` param whose live options are unavailable (the engine could
    not be asked) degrades to a free-text field rather than an empty
    dropdown: the operator can still type a value, and an unknown one comes
    back as an honestly failed job instead of a page that offers nothing.
    """
    if param.kind == "text":
        return forms.CharField(
            label=param.label,
            required=param.required,
            initial=param.default,
            widget=forms.Textarea(attrs={"rows": 3}),
        )
    if param.kind == "int":
        return forms.IntegerField(
            label=param.label, required=False, initial=param.default,
            min_value=None if param.min is None else int(param.min),
            max_value=None if param.max is None else int(param.max),
            widget=forms.NumberInput(attrs=_widget_attrs(param)),
        )
    if param.kind == "float":
        return forms.FloatField(
            label=param.label, required=False, initial=param.default,
            min_value=param.min, max_value=param.max,
            widget=forms.NumberInput(attrs=_widget_attrs(param)),
        )
    if param.kind == "seed":
        return forms.CharField(
            label=param.label, required=False,
            help_text="Leave blank for a random seed.",
            widget=forms.TextInput(attrs={"inputmode": "numeric"}),
        )
    if param.kind == "choice":
        options = param.choices or engine_choices.get(param.key, ())
        if not options:
            return forms.CharField(label=param.label, required=True)
        return forms.ChoiceField(
            label=param.label,
            choices=[(value, value) for value in options],
            initial=options[0],
        )
    if param.kind == "file":
        return forms.FileField(
            label=param.label, required=param.required,
            widget=forms.ClearableFileInput(attrs=_widget_attrs(param)),
        )
    raise ValueError(
        f"Param kind {param.kind!r} ({param.key!r}) has no form field yet — "
        "asset parameters ship with the first operation that uses them."
    )


def build_form(
    operation: Operation,
    engine_choices: dict[str, tuple[str, ...]],
    data=None,
    files=None,
    initial: dict | None = None,
) -> forms.Form:
    """A bound or unbound form for `operation`.

    `engine_choices` maps a choice param's key to the engine's live options
    (`InferenceEngine.list_choices`). `initial` prefills fields -- the
    gallery's "reuse settings" path passes a previous job's params.

    Form validation is the *live* layer (real option lists, widget bounds);
    `core.inference.operations.validate_params` remains the schema floor
    every caller shares, including the future chatbot tool that never builds
    a form at all.
    """
    fields = {param.key: _field_for(param, engine_choices) for param in operation.params}
    if initial:
        for key, value in initial.items():
            if key in fields:
                fields[key].initial = value
    form_class = type("GenerationForm", (forms.Form,), fields)
    return form_class(data=data, files=files)
```

- [ ] **Step 4: Write `modules/vision/views.py` (create page only)**

```python
"""
Vision module HTTP surface (spec §4.7) -- the `/vision/` page.

The page holds NO generation logic: every action goes through
`modules.vision.services`, so the chatbot tool that calls the same functions
later behaves identically. It also never learns which engine is bound --
only the platform types (`Operation`, `PreflightResult`, `GenerationJob`)
cross this line.
"""
from __future__ import annotations

import logging

from django.views.generic import TemplateView

from core.inference.engines import get_engine
from core.inference.operations import TXT2IMG, Operation, get_operation
from modules.vision import services
from modules.vision.models import GenerationJob

logger = logging.getLogger(__name__)

# The operation this page serves. One in this cut; a page-level chooser is
# the natural addition when the second operation ships (spec §9).
PAGE_OPERATION = TXT2IMG.key

# How many recent jobs the create page shows as cards.
RECENT_JOBS = 8


def live_choices(operation: Operation, resolved) -> dict[str, tuple[str, ...]]:
    """Engine-reported options for every `"choice"` param of `operation`.

    One `list_choices` call per choice param, once per request. Returns `{}`
    when the role is unbound or the engine can't answer -- the form then
    degrades to free-text choice fields instead of empty dropdowns, and the
    page still renders.
    """
    if resolved is None:
        return {}
    lister = getattr(get_engine(resolved.engine), "list_choices", None)
    if lister is None:
        return {}
    choices: dict[str, tuple[str, ...]] = {}
    for param in operation.params:
        if param.kind != "choice" or param.choices:
            continue
        try:
            choices[param.key] = lister(resolved.endpoint, param.key)
        except Exception:  # noqa: BLE001 -- never 500 over an option list
            logger.debug("list_choices(%r) failed", param.key, exc_info=True)
            choices[param.key] = ()
    return choices


class CreatePageView(TemplateView):
    """GET /vision/ -- the prompt form, the preflight banner, recent jobs."""

    template_name = "vision/create.html"

    def get_context_data(self, **kwargs):
        from modules.vision.forms import build_form

        context = super().get_context_data(**kwargs)
        operation = get_operation(PAGE_OPERATION)
        check = services.preflight()

        context["operation"] = operation
        context["preflight"] = check
        context["form"] = build_form(operation, live_choices(operation, check.resolved))
        context["jobs"] = list(
            GenerationJob.objects.prefetch_related("outputs")[:RECENT_JOBS]
        )
        return context
```

- [ ] **Step 5: Write the templates**

`modules/vision/templates/vision/base.html`:

```html
{% extends "_shell.html" %}
{% comment %}
Module-local base for the /vision/ pages. Extends the shared project shell
(templates/_shell.html) so the design tokens, dark-mode overrides, and the
global nav live in ONE place -- exactly as modules/rag/templates/rag/base.html
does. (The three module base templates are a known consolidation candidate;
out of scope here.)

Template inheritance creates no Python import, so core/ purity and the module
contract are unaffected.

Marking the shared nav's "Generate" entry current is done exactly the way
ask.html / documents.html / console.html do it -- by overriding the matching
`nav_current_*` block with `current`. It sits on this BASE (not on each leaf
page) because every page under /vision/ belongs to that one nav destination;
the page-level distinction is the sub-nav below.
{% endcomment %}
{% block nav_current_vision %}current{% endblock %}
{% block extra_style %}
  :root { --page-max-width: 1040px; }
  .vision-wrap { max-width: var(--page-max-width); margin: 0 auto; }
  .vision-subnav { display: flex; gap: 1rem; font-size: 0.9rem; margin-bottom: 1rem; }
  .vision-subnav a { color: var(--accent); text-decoration: none; }
  .vision-subnav a.current { color: var(--text); font-weight: 600; pointer-events: none; }
  .banner {
    border: 1px solid var(--border); border-radius: 8px; background: var(--panel);
    padding: 0.75rem 1rem; margin-bottom: 1rem;
  }
  .banner.warn { border-color: var(--accent); }
  .banner a { color: var(--accent); }
  .card {
    border: 1px solid var(--border); border-radius: 8px; background: var(--panel);
    padding: 1rem; margin-bottom: 1rem;
  }
  .muted { color: var(--muted); font-size: 0.85rem; }
  {% block vision_style %}{% endblock %}
{% endblock %}
{% block content %}
<div class="vision-wrap">
  <div class="vision-subnav">
    <a href="{% url 'vision-create' %}" class="{% block subnav_current_create %}{% endblock %}">Generate</a>
  </div>
  {% block vision_content %}{% endblock %}
</div>
{% endblock %}
```

`modules/vision/templates/vision/create.html`:

```html
{% extends "vision/base.html" %}
{% block title %}Generate — farabunker{% endblock %}
{% block subnav_current_create %}current{% endblock %}
{% block vision_style %}
  .gen-form { display: grid; gap: 0.75rem; }
  .gen-form .row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0.75rem; }
  .gen-form label { display: block; font-size: 0.85rem; color: var(--muted); }
  .gen-form input, .gen-form select, .gen-form textarea {
    width: 100%; padding: 0.45rem 0.55rem; border: 1px solid var(--border);
    border-radius: 6px; background: var(--bg); color: var(--text); font: inherit;
  }
  .errorlist { color: #b3261e; font-size: 0.85rem; margin: 0.2rem 0 0; padding-left: 1rem; }
{% endblock %}
{% block vision_content %}
<h1>Generate an image</h1>

{% if preflight.state != "ready" %}
<div class="banner warn">
  {{ preflight.message }}
  {% if preflight.state == "unbound" %}
  <a href="{% url 'inference-console' %}">Assign a model</a>
  {% else %}
  <a href="{% url 'inference-console' %}">Check the model setup</a>
  {% endif %}
</div>
{% endif %}

<form class="card gen-form" method="post" action="{% url 'vision-generate' %}" id="generate-form">
  {% csrf_token %}
  {% for field in form %}
    {% if field.name == "prompt" or field.name == "negative_prompt" %}
      <div>
        <label for="{{ field.id_for_label }}">{{ field.label }}</label>
        {{ field }}
        {{ field.errors }}
      </div>
    {% endif %}
  {% endfor %}
  <div class="row">
    {% for field in form %}
      {% if field.name != "prompt" and field.name != "negative_prompt" %}
      <div>
        <label for="{{ field.id_for_label }}">{{ field.label }}</label>
        {{ field }}
        {% if field.help_text %}<span class="muted">{{ field.help_text }}</span>{% endif %}
        {{ field.errors }}
      </div>
      {% endif %}
    {% endfor %}
  </div>
  <div><button type="submit">Generate</button></div>
</form>

<h2>Recent</h2>
<div id="jobs">
  {% for job in jobs %}{% include "vision/_job_card.html" %}{% empty %}
  <p class="muted">Nothing generated yet.</p>
  {% endfor %}
</div>
{% endblock %}
```

> The `vision-generate` URL and `_job_card.html` land in Task 12. Write this template now but add its `{% url 'vision-generate' %}` action and the `{% include %}` **in Task 12** — until then the form posts to `{% url 'vision-create' %}` and the loop body renders `<p class="muted">{{ job.params.prompt }} — {{ job.status }}</p>`, so this task's page renders and its tests pass on their own.

- [ ] **Step 6: Add the feature-gated nav entry to the shared shell**

In `templates/_shell.html`, add a fourth link inside the nav partial, after the "Model setup" line:

```html
  <a href="{% url 'inference-console' %}" class="{% block nav_current_console %}{% endblock %}">Model setup</a>
  {% if "vision" in farabunker_features %}<a href="{% url 'vision-create' %}" class="{% block nav_current_vision %}{% endblock %}">Generate</a>{% endif %}
```

and extend the file's comment block, which currently says the nav links to "all three destinations":

```
Also owns the ONE global nav partial (the {% block nav %} below) so every
page links to every mounted destination -- Ask, Document library, Model
setup, and Generate -- with the current page marked via the `.current` rule.
A page marks itself current by overriding the matching `nav_current_*` block
(see ask.html / documents.html / console.html / vision/base.html) with
`current`; this needs no context processor beyond the feature flags and no
JS, just plain block overrides.

The Generate entry is FEATURE-GATED (`farabunker_features`, supplied by
modules.vision.context_processors.features): with the vision feature off
there is no /vision/ route at all, so the link must not render. Django only
evaluates the `{% url %}` tag inside a taken `{% if %}` branch, so the
absent route raises nothing when the feature is off.
```

- [ ] **Step 7: Add the route**

`modules/vision/urls.py`:

```python
"""URL routes for the vision module, mounted at /vision/ (config/urls.py)."""
from django.urls import path

from modules.vision.views import CreatePageView

urlpatterns = [
    path("", CreatePageView.as_view(), name="vision-create"),
]
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/pytest modules/vision/tests/test_forms.py modules/vision/tests/test_views_create.py -q`
Expected: PASS.

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all PASS — the shell change touches every page, so the RAG and console page tests are the real check that the nav still renders (and that the feature-off branch never reverses a missing URL).

- [ ] **Step 10: Commit**

```bash
git add modules/vision/forms.py modules/vision/views.py modules/vision/urls.py \
        modules/vision/templates templates/_shell.html \
        modules/vision/tests/test_forms.py modules/vision/tests/test_views_create.py
git commit -F - <<'EOF'
feat(vision): create page, feature-gated Generate nav entry, preflight banner

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 12: Generate, poll, delete, and serve — job cards and the polling script

**Suggested implementer tier:** sonnet — four endpoints, an HTML fragment contract, and the one piece of JavaScript in the feature.

**Files:**
- Modify: `modules/vision/views.py` (`generate`, `job_status`, `job_delete`, `output_file`, `_job_json`)
- Modify: `modules/vision/urls.py`
- Create: `modules/vision/templates/vision/_job_card.html`
- Modify: `modules/vision/templates/vision/create.html` (real form action, card include, polling script)
- Test: `modules/vision/tests/test_views_generate.py`

**Interfaces:**
- Consumes: `services.submit_job`, `services.refresh_job`, `services.delete_job`, `VisionUnavailable`, `ParamError`.
- Produces:
  - `POST /vision/generate/` → `vision-generate`: 200 job-card fragment for an XHR, 302 to `vision-create` otherwise; 400 on invalid params; 503 with the honest message on `VisionUnavailable`.
  - `GET /vision/jobs/<uuid>/` → `vision-job-status`: refreshed `_job_card.html` fragment, or the job as JSON with `?format=json`.
  - `POST /vision/jobs/<uuid>/delete/` → `vision-job-delete`: 302 back to `vision-create` (204 for an XHR).
  - `GET /vision/outputs/<int:output_id>/file/` → `vision-output-file`: streams the stored file inline (`?download=1` forces an attachment); 404 when the file is gone.
  - `_job_json(job) -> dict` — the shape the future chatbot tool and the tests read.

- [ ] **Step 1: Write the failing tests**

Create `modules/vision/tests/test_views_generate.py`:

```python
"""Unit tests for the generate / poll / delete / file endpoints (spec §4.7, §6)."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from console.inference.models import ModelConnection, RoleBinding
from core.inference.engines import ENGINES
from core.inference.engines.base import JobStatus
from core.inference.roles import VISION_GENERATE_ROLE
from modules.vision.models import GenerationJob
from modules.vision.tests._helpers import StubEngine, StubGenerator, clear_bindings

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR" + b"\x00\x00\x02\x00" + b"\x00\x00\x02\x00" + b"\x08\x06\x00\x00\x00"

FORM = {
    "prompt": "a lighthouse", "negative_prompt": "", "width": "512", "height": "512",
    "steps": "20", "cfg_scale": "7", "seed": "42", "sampler": "euler",
    "scheduler": "normal", "batch_size": "1",
}

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


def _bind():
    connection = ModelConnection.objects.create(
        name="stub image model", engine="stubengine", endpoint="http://stub:9999",
        model_id="stub.safetensors", capabilities=["image-generation"],
    )
    RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)


def _engine(generator=None, healthy=True, choices=("euler",)):
    engine = StubEngine(generator or StubGenerator(), healthy=healthy)
    engine.list_choices = lambda endpoint, key: choices
    return patch.dict(ENGINES, {"stubengine": engine})


@pytest.mark.django_db
class TestGenerate:
    def test_valid_post_queues_a_job_and_redirects_without_js(self, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM)

        assert response.status_code == 302
        assert response["Location"] == reverse("vision-create")
        assert GenerationJob.objects.count() == 1

    def test_xhr_post_returns_the_job_card_fragment(self, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM, **XHR)

        body = response.content.decode()
        job = GenerationJob.objects.get()
        assert response.status_code == 200
        assert str(job.id) in body
        assert "data-job-poll" in body

    def test_invalid_params_are_a_400_that_re_renders_the_form(self, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), dict(FORM, prompt=""))

        assert response.status_code == 400
        assert GenerationJob.objects.count() == 0
        assert "This field is required" in response.content.decode()

    def test_unbound_role_is_a_503_with_the_honest_message(self, client):
        response = client.post(reverse("vision-generate"), FORM)

        assert response.status_code == 503
        assert "No model assigned for Image generation" in response.content.decode()

    def test_unreachable_engine_is_a_503_naming_the_endpoint(self, client):
        _bind()
        with _engine(healthy=False):
            response = client.post(reverse("vision-generate"), FORM)

        assert response.status_code == 503
        assert "http://stub:9999" in response.content.decode()

    def test_get_is_not_allowed(self, client):
        assert client.get(reverse("vision-generate")).status_code == 405


@pytest.mark.django_db
class TestJobStatus:
    def _job(self, generator):
        _bind()
        with _engine(generator):
            client = Client()
            client.post(reverse("vision-generate"), FORM)
        return GenerationJob.objects.get()

    def test_refreshes_and_renders_the_card(self, client):
        generator = StubGenerator(states=[JobStatus(state="running")])
        job = self._job(generator)
        with _engine(generator):
            response = client.get(reverse("vision-job-status", args=[job.id]))

        job.refresh_from_db()
        assert response.status_code == 200
        assert job.status == GenerationJob.Status.RUNNING
        assert "data-job-poll" in response.content.decode()

    def test_a_finished_card_stops_polling(self, client, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path
        generator = StubGenerator(states=[JobStatus(state="done")], outputs=[("a.png", PNG, "image/png")])
        job = self._job(generator)
        with _engine(generator):
            response = client.get(reverse("vision-job-status", args=[job.id]))

        body = response.content.decode()
        assert "data-job-poll" not in body
        assert reverse("vision-output-file", args=[job.outputs.get().id]) in body

    def test_json_format_returns_the_job_dict(self, client, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path
        generator = StubGenerator(states=[JobStatus(state="done")], outputs=[("a.png", PNG, "image/png")])
        job = self._job(generator)
        with _engine(generator):
            response = client.get(reverse("vision-job-status", args=[job.id]), {"format": "json"})

        payload = json.loads(response.content)
        assert payload["status"] == "done"
        assert payload["seed"] == 42
        assert payload["outputs"][0]["width"] == 512
        assert payload["unreachable"] is False

    def test_unreachable_engine_shows_the_still_checking_note(self, client):
        generator = StubGenerator(states=[ConnectionError("down")])
        job = self._job(generator)
        with _engine(generator):
            response = client.get(reverse("vision-job-status", args=[job.id]))

        assert "still checking" in response.content.decode()
        job.refresh_from_db()
        assert job.status == GenerationJob.Status.QUEUED

    def test_unknown_job_is_a_404(self, client):
        response = client.get(
            reverse("vision-job-status", args=["8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"])
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestOutputFile:
    def _finished_job(self, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path
        _bind()
        generator = StubGenerator(states=[JobStatus(state="done")], outputs=[("a.png", PNG, "image/png")])
        with _engine(generator):
            Client().post(reverse("vision-generate"), FORM)
            job = GenerationJob.objects.get()
            from modules.vision import services

            services.refresh_job(job)
        return job

    def test_streams_the_stored_bytes_inline(self, client, tmp_path, settings):
        job = self._finished_job(tmp_path, settings)
        output = job.outputs.get()

        response = client.get(reverse("vision-output-file", args=[output.id]))

        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        assert b"".join(response.streaming_content) == PNG

    def test_download_flag_sets_an_attachment_disposition(self, client, tmp_path, settings):
        job = self._finished_job(tmp_path, settings)
        output = job.outputs.get()

        response = client.get(reverse("vision-output-file", args=[output.id]), {"download": "1"})

        assert "attachment" in response["Content-Disposition"]

    def test_missing_file_on_disk_is_a_404_not_a_500(self, client, tmp_path, settings):
        job = self._finished_job(tmp_path, settings)
        output = job.outputs.get()
        os.remove(output.path)

        assert client.get(reverse("vision-output-file", args=[output.id])).status_code == 404


@pytest.mark.django_db
class TestJobDelete:
    def test_deletes_and_redirects(self, client, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path
        _bind()
        generator = StubGenerator()
        with _engine(generator):
            client.post(reverse("vision-generate"), FORM)
        job = GenerationJob.objects.get()

        response = client.post(reverse("vision-job-delete", args=[job.id]))

        assert response.status_code == 302
        assert GenerationJob.objects.count() == 0

    def test_get_is_not_allowed(self, client):
        _bind()
        generator = StubGenerator()
        with _engine(generator):
            Client().post(reverse("vision-generate"), FORM)
        job = GenerationJob.objects.get()

        assert client.get(reverse("vision-job-delete", args=[job.id])).status_code == 405
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest modules/vision/tests/test_views_generate.py -q`
Expected: FAIL — `NoReverseMatch: 'vision-generate'`.

- [ ] **Step 3: Add the endpoints to `modules/vision/views.py`**

Extend the imports:

```python
import os

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.inference.operations import ParamError
from modules.vision.models import GeneratedOutput, GenerationJob
```

and append:

```python
def _is_xhr(request) -> bool:
    """True for the page's own fetch() calls. Everything works without JS;
    this only decides whether to answer with a fragment or a redirect."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _job_json(job: GenerationJob) -> dict:
    """The job as plain data -- the shape `?format=json` returns and the
    future chatbot tool reads. Paths are never exposed; outputs are named by
    their serving URL."""
    return {
        "id": str(job.id),
        "operation": job.operation,
        "status": job.status,
        "error": job.error,
        "seed": job.seed,
        "params": job.params,
        "engine": job.engine,
        "model_id": job.model_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "stale": job.is_stale,
        "unreachable": bool(getattr(job, "unreachable", False)),
        "outputs": [
            {
                "id": output.id,
                "url": reverse("vision-output-file", args=[output.id]),
                "media_type": output.media_type,
                "width": output.width,
                "height": output.height,
            }
            for output in job.outputs.all()
        ],
    }


def _render_card(request, job: GenerationJob) -> HttpResponse:
    return render(request, "vision/_job_card.html", {"job": job})


@require_POST
def generate(request):
    """POST /vision/generate/ -- validate and queue one generation.

    503 with the preflight message when the role is unbound or the engine is
    unreachable; 400 with the re-rendered form when the parameters don't fit
    the schema; otherwise the new job's card (XHR) or a redirect back to the
    page (no JS).
    """
    operation = get_operation(PAGE_OPERATION)
    check = services.preflight()
    form = build_form_for(request, operation, check)

    if not form.is_valid():
        return _create_page_response(request, operation, check, form, status=400)

    try:
        job = services.submit_job(operation.key, dict(form.cleaned_data), files=request.FILES or None)
    except services.VisionUnavailable as exc:
        return render(
            request, "vision/_unavailable.html", {"message": exc.message}, status=503
        )
    except ParamError as exc:
        for key, message in exc.errors.items():
            form.add_error(key if key in form.fields else None, message)
        return _create_page_response(request, operation, check, form, status=400)

    if _is_xhr(request):
        return _render_card(request, job)
    return redirect("vision-create")


def job_status(request, job_id):
    """GET /vision/jobs/<uuid>/ -- refresh the job, then render its card.

    `?format=json` returns `_job_json` instead: the same facts, for the
    future chatbot tool and for tests that shouldn't parse HTML.
    """
    job = get_object_or_404(GenerationJob, pk=job_id)
    job = services.refresh_job(job)
    if request.GET.get("format") == "json":
        return JsonResponse(_job_json(job))
    return _render_card(request, job)


@require_POST
def job_delete(request, job_id):
    """POST /vision/jobs/<uuid>/delete/ -- remove the job and its files."""
    job = get_object_or_404(GenerationJob, pk=job_id)
    services.delete_job(job)
    if _is_xhr(request):
        return HttpResponse(status=204)
    return redirect("vision-create")


def output_file(request, output_id: int):
    """GET /vision/outputs/<id>/file/ -- stream one generated file.

    Serves strictly by `GeneratedOutput` primary key, mirroring
    `rag-document-file`: the request never supplies a filesystem path, so
    there is no path-traversal surface. `?download=1` forces an attachment;
    otherwise it streams inline so the browser renders the image.

    Unauthenticated in this Phase-1 skeleton, the same known gap
    `/inference/` and `/rag/`'s file view carry (spec §9).
    """
    output = get_object_or_404(GeneratedOutput, pk=output_id)
    if not output.path or not os.path.isfile(output.path):
        raise Http404(
            f"The file for output {output_id} is no longer on disk "
            "(the job's directory may have been deleted)."
        )
    download = request.GET.get("download", "").lower() in ("1", "true")
    return FileResponse(
        open(output.path, "rb"),
        as_attachment=download,
        filename=os.path.basename(output.path),
        content_type=output.media_type or None,
    )


def build_form_for(request, operation: Operation, check):
    """The bound form for a submission, with the engine's live choices."""
    from modules.vision.forms import build_form

    return build_form(
        operation,
        live_choices(operation, check.resolved),
        data=request.POST,
        files=request.FILES or None,
    )


def _create_page_response(request, operation: Operation, check, form, status: int):
    """Re-render the create page around an invalid form."""
    return render(
        request,
        "vision/create.html",
        {
            "operation": operation,
            "preflight": check,
            "form": form,
            "jobs": list(GenerationJob.objects.prefetch_related("outputs")[:RECENT_JOBS]),
        },
        status=status,
    )
```

Refactor `CreatePageView.get_context_data` to reuse the same context keys (it already does) — no further change.

- [ ] **Step 4: Write the fragment templates**

`modules/vision/templates/vision/_job_card.html`:

```html
{% comment %}
One job card. Rendered inline on the create page and returned on its own by
`job_status` -- the SAME fragment either way, so polling swaps like for like.

`data-job-poll` is present only while the job can still change: the script in
create.html keeps polling exactly as long as that attribute exists, so a
finished or failed job costs no further requests, and a page with JS disabled
simply shows the "refresh to update" note instead.
{% endcomment %}
<div class="card job-card" id="job-{{ job.id }}"
     {% if not job.is_terminal %}data-job-poll="{% url 'vision-job-status' job.id %}"{% endif %}>
  <div class="job-head">
    <strong>{{ job.params.prompt|default:"(no prompt)"|truncatechars:120 }}</strong>
    <span class="muted">{{ job.get_status_display }}</span>
  </div>

  {% if job.outputs.all %}
  <div class="job-images">
    {% for output in job.outputs.all %}
    <a href="{% url 'vision-output-file' output.id %}" target="_blank" rel="noopener">
      <img src="{% url 'vision-output-file' output.id %}" alt="Generated image {{ forloop.counter }}">
    </a>
    {% endfor %}
  </div>
  {% endif %}

  {% if job.error %}<p class="job-error">{{ job.error }}</p>{% endif %}
  {% if job.unreachable %}<p class="muted">Engine unreachable, still checking.</p>{% endif %}
  {% if job.is_stale %}<p class="muted">Still queued — check that the image engine is running.</p>{% endif %}
  {% if not job.is_terminal %}<p class="muted no-js-note">Refresh to update.</p>{% endif %}

  <p class="muted">
    seed {{ job.seed }} · {{ job.params.width }}×{{ job.params.height }} ·
    {{ job.params.steps }} steps · cfg {{ job.params.cfg_scale }} ·
    {{ job.params.sampler }}/{{ job.params.scheduler }} · {{ job.model_id }}
  </p>

  <form method="post" action="{% url 'vision-job-delete' job.id %}"
        onsubmit="return confirm('Delete this generation and its files?');">
    {% csrf_token %}
    <button type="submit" class="secondary">Delete</button>
  </form>
</div>
```

`modules/vision/templates/vision/_unavailable.html`:

```html
{% comment %}
The 503 body for a generate attempt made while the role is unbound or the
engine is unreachable -- the same honest message the page banner shows,
usable as a fragment (XHR) or a whole small page (no JS).
{% endcomment %}
<div class="banner warn">
  {{ message }}
  <a href="{% url 'inference-console' %}">Model setup</a>
</div>
```

- [ ] **Step 5: Wire the create page to the real endpoints**

In `modules/vision/templates/vision/create.html`: point the form at `{% url 'vision-generate' %}`, replace the recent-jobs loop body with `{% include "vision/_job_card.html" %}`, add the card styles to `{% block vision_style %}`:

```css
  .job-card img { max-width: 100%; border-radius: 6px; display: block; }
  .job-images { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.5rem 0; }
  .job-images img { max-height: 320px; width: auto; }
  .job-head { display: flex; justify-content: space-between; gap: 1rem; align-items: baseline; }
  .job-error { color: #b3261e; }
  button.secondary { background: transparent; color: var(--accent); padding: 0.3rem 0; font-weight: 500; }
```

and add the polling script at the end of the template:

```html
{% block scripts %}
<script>
// Poll every non-terminal card every 2s and swap in the fresh fragment; a
// card without data-job-poll is finished, so polling stops by itself. No
// external assets, and the page works fully without this script.
(function () {
  function poll(card) {
    var url = card.getAttribute('data-job-poll');
    if (!url) { return; }
    fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (response) { return response.text(); })
      .then(function (html) {
        var holder = document.createElement('div');
        holder.innerHTML = html.trim();
        var fresh = holder.firstElementChild;
        card.replaceWith(fresh);
        if (fresh.getAttribute('data-job-poll')) { setTimeout(function () { poll(fresh); }, 2000); }
      })
      .catch(function () { setTimeout(function () { poll(card); }, 5000); });
  }
  function watch(card) { setTimeout(function () { poll(card); }, 2000); }
  Array.prototype.forEach.call(document.querySelectorAll('[data-job-poll]'), watch);

  var form = document.getElementById('generate-form');
  if (form) {
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
      }).then(function (response) {
        return response.text().then(function (html) {
          var holder = document.createElement('div');
          holder.innerHTML = html.trim();
          var card = holder.firstElementChild;
          var jobs = document.getElementById('jobs');
          if (!response.ok || !card.id) { jobs.insertAdjacentHTML('afterbegin', html); return; }
          jobs.insertAdjacentElement('afterbegin', card);
          watch(card);
        });
      });
    });
  }
  Array.prototype.forEach.call(document.querySelectorAll('.no-js-note'), function (note) {
    note.textContent = 'Waiting for the engine…';
  });
})();
</script>
{% endblock %}
```

- [ ] **Step 6: Add the routes**

`modules/vision/urls.py`:

```python
"""URL routes for the vision module, mounted at /vision/ (config/urls.py)."""
from django.urls import path

from modules.vision.views import CreatePageView, generate, job_delete, job_status

urlpatterns = [
    path("", CreatePageView.as_view(), name="vision-create"),
    path("generate/", generate, name="vision-generate"),
    path("jobs/<uuid:job_id>/", job_status, name="vision-job-status"),
    path("jobs/<uuid:job_id>/delete/", job_delete, name="vision-job-delete"),
    path("outputs/<int:output_id>/file/", output_file, name="vision-output-file"),
]
```

(with `output_file` added to the `from modules.vision.views import ...` line). The card and `_job_json` both link to `vision-output-file`, so the file-serving route belongs to this task; Task 13 adds only the gallery.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest modules/vision/tests -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add modules/vision/views.py modules/vision/urls.py modules/vision/templates \
        modules/vision/tests/test_views_generate.py
git commit -F - <<'EOF'
feat(vision): generate, poll, delete and file-serving endpoints with job cards

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 13: Gallery with "reuse settings"

**Suggested implementer tier:** sonnet — a paginated listing plus the prefill round-trip back into the create form.

**Files:**
- Modify: `modules/vision/views.py` (`gallery`, `CreatePageView` prefill)
- Modify: `modules/vision/urls.py`
- Modify: `modules/vision/templates/vision/base.html` (gallery sub-nav link)
- Create: `modules/vision/templates/vision/gallery.html`
- Test: `modules/vision/tests/test_views_gallery.py`

**Interfaces:**
- Consumes: `GeneratedOutput`, `GenerationJob` (Task 9), `build_form` `initial` (Task 11), `vision-output-file` (Task 12).
- Produces:
  - `GET /vision/gallery/` → `vision-gallery`: paginated outputs with prompt, seed, params, model, a link to the file, and a "reuse settings" link.
  - `GET /vision/?reuse=<job-uuid>` prefills the create form from that job's params.
  - `views.GALLERY_PAGE_SIZE`.

- [ ] **Step 1: Write the failing tests**

Create `modules/vision/tests/test_views_gallery.py`:

```python
"""Unit tests for the /vision/gallery/ page and the reuse-settings prefill."""
from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from modules.vision.models import GeneratedOutput, GenerationJob
from modules.vision.tests._helpers import clear_bindings


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


def _job_with_output(prompt="a lighthouse", seed=42, index=0):
    job = GenerationJob.objects.create(
        operation="txt2img",
        params={
            "prompt": prompt, "negative_prompt": "", "width": 512, "height": 512,
            "steps": 20, "cfg_scale": 7.0, "seed": seed, "sampler": "euler",
            "scheduler": "normal", "batch_size": 1,
        },
        seed=seed, engine="stubengine", model_id="stub.safetensors",
        model_fingerprint="stubengine:stub.safetensors:None",
        status=GenerationJob.Status.DONE,
    )
    output = GeneratedOutput.objects.create(
        job=job, index=index, path="/tmp/a.png", media_type="image/png", width=512, height=512
    )
    return job, output


@pytest.mark.django_db
class TestGallery:
    def test_lists_outputs_with_their_facts(self, client):
        job, output = _job_with_output()

        response = client.get(reverse("vision-gallery"))
        body = response.content.decode()

        assert response.status_code == 200
        assert "a lighthouse" in body
        assert "42" in body
        assert "stub.safetensors" in body
        assert reverse("vision-output-file", args=[output.id]) in body

    def test_empty_gallery_says_so(self, client):
        response = client.get(reverse("vision-gallery"))
        assert "Nothing generated yet" in response.content.decode()

    def test_is_paginated_newest_first(self, client):
        from modules.vision import views

        for index in range(views.GALLERY_PAGE_SIZE + 1):
            _job_with_output(prompt=f"prompt {index}")

        page_one = client.get(reverse("vision-gallery"))
        page_two = client.get(reverse("vision-gallery"), {"page": 2})

        assert len(page_one.context["page_obj"].object_list) == views.GALLERY_PAGE_SIZE
        assert len(page_two.context["page_obj"].object_list) == 1
        assert page_one.context["page_obj"].object_list[0].job.params["prompt"] == (
            f"prompt {views.GALLERY_PAGE_SIZE}"
        )

    def test_reuse_link_points_at_the_create_page_with_the_job_id(self, client):
        job, _ = _job_with_output()

        body = client.get(reverse("vision-gallery")).content.decode()

        assert f"{reverse('vision-create')}?reuse={job.id}" in body


@pytest.mark.django_db
class TestReusePrefill:
    def test_prefills_the_form_from_a_previous_job(self, client):
        job, _ = _job_with_output(prompt="reuse me", seed=7)

        response = client.get(reverse("vision-create"), {"reuse": str(job.id)})
        form = response.context["form"]

        assert form.fields["prompt"].initial == "reuse me"
        assert form.fields["steps"].initial == 20
        assert form.fields["seed"].initial == 7

    def test_an_unknown_job_id_is_ignored_not_an_error(self, client):
        response = client.get(
            reverse("vision-create"), {"reuse": "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"}
        )
        assert response.status_code == 200

    def test_a_malformed_job_id_is_ignored(self, client):
        assert client.get(reverse("vision-create"), {"reuse": "not-a-uuid"}).status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest modules/vision/tests/test_views_gallery.py -q`
Expected: FAIL — `NoReverseMatch: 'vision-gallery'`.

- [ ] **Step 3: Add the gallery view and the prefill**

In `modules/vision/views.py`, add `from django.core.paginator import Paginator` and:

```python
# Outputs shown per gallery page.
GALLERY_PAGE_SIZE = 24


def _reuse_initial(request) -> dict:
    """Form initial values from a previous job (`?reuse=<uuid>`).

    A missing, malformed, or unknown id is silently ignored -- a stale link
    should land the operator on a normal empty form, never on an error page.
    Only keys the operation still declares survive, so a removed parameter in
    an older job can't poison the form.
    """
    raw = request.GET.get("reuse", "").strip()
    if not raw:
        return {}
    try:
        job = GenerationJob.objects.filter(pk=raw).first()
    except (ValueError, ValidationError):
        return {}
    return dict(job.params) if job else {}


def gallery(request):
    """GET /vision/gallery/ -- every generated output, newest first."""
    outputs = GeneratedOutput.objects.select_related("job").order_by("-job__created_at", "index")
    page = Paginator(outputs, GALLERY_PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "vision/gallery.html", {"page_obj": page})
```

Add `from django.core.exceptions import ValidationError` to the imports, and use the prefill in `CreatePageView.get_context_data`:

```python
        context["form"] = build_form(
            operation,
            live_choices(operation, check.resolved),
            initial=_reuse_initial(self.request),
        )
```

- [ ] **Step 4: Write `modules/vision/templates/vision/gallery.html`**

```html
{% extends "vision/base.html" %}
{% block title %}Gallery — farabunker{% endblock %}
{% block subnav_current_gallery %}current{% endblock %}
{% block vision_style %}
  .gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 1rem; }
  .gallery figure { margin: 0; }
  .gallery img { width: 100%; height: auto; border-radius: 6px; display: block; }
  .gallery figcaption { font-size: 0.85rem; color: var(--muted); margin-top: 0.35rem; }
  .pager { display: flex; gap: 1rem; margin-top: 1.5rem; }
{% endblock %}
{% block vision_content %}
<h1>Gallery</h1>

{% if page_obj.object_list %}
<div class="gallery">
  {% for output in page_obj.object_list %}
  <figure class="card">
    <a href="{% url 'vision-output-file' output.id %}" target="_blank" rel="noopener">
      <img src="{% url 'vision-output-file' output.id %}" alt="{{ output.job.params.prompt|default:'Generated image' }}">
    </a>
    <figcaption>
      {{ output.job.params.prompt|default:"(no prompt)"|truncatechars:100 }}<br>
      seed {{ output.job.seed }} ·
      {{ output.width|default:"?" }}×{{ output.height|default:"?" }} ·
      {{ output.job.params.steps }} steps · cfg {{ output.job.params.cfg_scale }} ·
      {{ output.job.params.sampler }}/{{ output.job.params.scheduler }}<br>
      {{ output.job.model_id }} ({{ output.job.engine }})<br>
      <a href="{% url 'vision-create' %}?reuse={{ output.job.id }}">Reuse settings</a> ·
      <a href="{% url 'vision-output-file' output.id %}?download=1">Download</a>
    </figcaption>
  </figure>
  {% endfor %}
</div>

<div class="pager">
  {% if page_obj.has_previous %}<a href="?page={{ page_obj.previous_page_number }}">← Newer</a>{% endif %}
  <span class="muted">Page {{ page_obj.number }} of {{ page_obj.paginator.num_pages }}</span>
  {% if page_obj.has_next %}<a href="?page={{ page_obj.next_page_number }}">Older →</a>{% endif %}
</div>
{% else %}
<p class="muted">Nothing generated yet.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Add the route and the sub-nav link**

`modules/vision/urls.py` — add `gallery` to the import and:

```python
    path("gallery/", gallery, name="vision-gallery"),
```

`modules/vision/templates/vision/base.html` — extend the sub-nav:

```html
    <a href="{% url 'vision-gallery' %}" class="{% block subnav_current_gallery %}{% endblock %}">Gallery</a>
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest modules/vision/tests -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add modules/vision/views.py modules/vision/urls.py modules/vision/templates \
        modules/vision/tests/test_views_gallery.py
git commit -F - <<'EOF'
feat(vision): gallery with per-output facts and reuse-settings prefill

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 16: Ollama's setup guide + `serves_capabilities`

> **Execution order:** run this after Task 13 and before Task 14 (see the header note). The number is 16 because it was added on 2026-08-23, after the earlier numbering was in flight.

**Suggested implementer tier:** haiku — declarative data added to an existing adapter, mirroring the ComfyUI guide already written in Task 5.

**Files:**
- Modify: `core/inference/engines/ollama.py` (adapter file — **not** one of the other track's files)
- Test: `modules/vision/tests/test_setup_guides.py`

**Interfaces:**
- Consumes: `SetupGuide`, `SetupStep`, `SETUP_PLATFORMS` (Task 4).
- Produces: `OllamaEngine.setup_guide: SetupGuide` (`verify_url_path = "/api/tags"`) and `OllamaEngine.serves_capabilities = ("chat", "embeddings", "vision")`.

- [ ] **Step 1: Write the failing tests**

Create `modules/vision/tests/test_setup_guides.py`:

```python
"""Unit tests for the engine-declared setup guides (owner requirement 2026-08-23).

Two layers: Ollama's own guide, and a UNIVERSAL shape check every registered
adapter that declares a guide must pass -- so a future adapter cannot ship a
half-filled guide the /setup/ page would render as blanks.
"""
from __future__ import annotations

import pytest

from core.inference.engines import ENGINES
from core.inference.engines.base import SETUP_PLATFORMS, SetupGuide
from core.inference.engines.ollama import OllamaEngine


class TestOllamaSetupGuide:
    def test_covers_every_platform(self):
        guide = OllamaEngine.setup_guide
        assert set(guide.platforms) == set(SETUP_PLATFORMS)
        assert all(guide.platforms[key] for key in SETUP_PLATFORMS)

    def test_every_platform_says_how_to_bind_all_interfaces(self):
        guide = OllamaEngine.setup_guide
        for key in SETUP_PLATFORMS:
            text = " ".join(
                f"{step.title} {step.body} {step.command or ''}" for step in guide.platforms[key]
            )
            assert "OLLAMA_HOST=0.0.0.0" in text

    def test_install_commands_are_the_platform_native_ones(self):
        platforms = OllamaEngine.setup_guide.platforms
        assert any("brew install ollama" in (s.command or "") for s in platforms["macos"])
        assert any("winget install" in (s.command or "") for s in platforms["windows"])
        assert any("ollama.com/install.sh" in (s.command or "") for s in platforms["linux"])

    def test_network_note_explains_the_container_hop(self):
        assert "host.docker.internal" in OllamaEngine.setup_guide.network_note

    def test_models_note_carries_the_pull_shape_and_who_pulls(self):
        note = OllamaEngine.setup_guide.models_note
        assert "ollama pull <model-name>" in note
        assert "never" in note

    def test_verify_path_matches_the_health_check(self):
        assert OllamaEngine.setup_guide.verify_url_path == "/api/tags"

    def test_it_declares_the_capabilities_it_serves(self):
        assert OllamaEngine.serves_capabilities == ("chat", "embeddings", "vision")


class TestEveryRegisteredGuideIsComplete:
    """Runs over whatever is registered, so a new adapter is covered the day
    it lands -- no per-engine test to remember to write."""

    @pytest.mark.parametrize("engine", list(ENGINES.values()), ids=lambda e: e.name)
    def test_a_declared_guide_is_fully_filled_in(self, engine):
        guide = getattr(engine, "setup_guide", None)
        if guide is None:
            pytest.skip(f"{engine.name} declares no setup guide")
        assert isinstance(guide, SetupGuide)
        assert guide.summary.strip()
        assert guide.network_note.strip()
        assert guide.models_note.strip()
        assert guide.verify_url_path.startswith("/")
        assert set(guide.platforms) <= set(SETUP_PLATFORMS)
        for steps in guide.platforms.values():
            assert steps
            for step in steps:
                assert step.title.strip()

    @pytest.mark.parametrize("engine", list(ENGINES.values()), ids=lambda e: e.name)
    def test_declared_capabilities_are_platform_vocabulary(self, engine):
        from core.inference.roles import CAPABILITIES

        for capability in getattr(engine, "serves_capabilities", ()):
            assert capability in CAPABILITIES
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest modules/vision/tests/test_setup_guides.py -q`
Expected: FAIL — `AttributeError: type object 'OllamaEngine' has no attribute 'setup_guide'`.

- [ ] **Step 3: Add the guide to `core/inference/engines/ollama.py`**

Extend the import:

```python
from core.inference.engines.base import InstalledModel, SetupGuide, SetupStep
```

and add to `OllamaEngine`, after `well_known_ports`:

```python
    # Which platform capabilities this engine can answer at all -- the setup
    # page's "Served by" column reads this; nothing else branches on it.
    serves_capabilities = ("chat", "embeddings", "vision")

    # How an operator installs and reaches Ollama. Declared HERE because only
    # this adapter knows it; the /setup/ page renders it verbatim.
    setup_guide = SetupGuide(
        summary=(
            "Ollama runs natively on the host and serves chat, embedding, and vision "
            "models over HTTP. farabunker never pulls a model for you — you pull the "
            "ones you want, then assign them to roles."
        ),
        platforms={
            "macos": (
                SetupStep(
                    "Install Ollama",
                    "Homebrew, or the .dmg from ollama.com.",
                    "brew install ollama",
                ),
                SetupStep(
                    "Start it so containers can reach it",
                    "OLLAMA_HOST=0.0.0.0 binds all interfaces; without it Ollama listens "
                    "on localhost only and the farabunker container cannot see it.",
                    "OLLAMA_HOST=0.0.0.0 ollama serve",
                ),
                SetupStep(
                    "Pull a model",
                    "Browse the library at https://ollama.com/library and pick your own.",
                    "ollama pull <model-name>",
                ),
            ),
            "windows": (
                SetupStep(
                    "Install Ollama",
                    "winget, or the installer from ollama.com.",
                    "winget install Ollama.Ollama",
                ),
                SetupStep(
                    "Let containers reach it",
                    "Set OLLAMA_HOST=0.0.0.0 in the system environment variables, then "
                    "restart Ollama from the tray.",
                ),
                SetupStep(
                    "Pull a model",
                    "Browse the library at https://ollama.com/library and pick your own.",
                    "ollama pull <model-name>",
                ),
            ),
            "linux": (
                SetupStep(
                    "Install Ollama",
                    "The official installer script sets up a systemd service.",
                    "curl -fsSL https://ollama.com/install.sh | sh",
                ),
                SetupStep(
                    "Let containers reach it",
                    'Bind all interfaces — for the systemd unit, add '
                    'Environment="OLLAMA_HOST=0.0.0.0" and reload; when running it by '
                    "hand, use the command shown.",
                    "OLLAMA_HOST=0.0.0.0 ollama serve",
                ),
                SetupStep(
                    "Pull a model",
                    "Browse the library at https://ollama.com/library and pick your own.",
                    "ollama pull <model-name>",
                ),
            ),
        },
        network_note=(
            "The farabunker web container reaches your machine as host.docker.internal, so "
            "Ollama must listen on all interfaces (OLLAMA_HOST=0.0.0.0). On the Linux "
            "appliance Ollama is its own container instead — same OLLAMA_BASE_URL contract, "
            "different value."
        ),
        models_note=(
            "Pull models yourself: ollama pull <model-name>. farabunker never pulls one for "
            "you and presumes no model — a role stays honestly unassigned until you assign "
            "it. The full library is at https://ollama.com/library."
        ),
        verify_url_path="/api/tags",
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest modules/vision/tests/test_setup_guides.py -q`
Expected: PASS — both engines now satisfy the universal shape check.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all PASS — the additions are declarative class attributes; nothing reads them yet except the new tests.

- [ ] **Step 6: Commit**

```bash
git add core/inference/engines/ollama.py modules/vision/tests/test_setup_guides.py
git commit -F - <<'EOF'
feat(inference): Ollama declares its own setup guide and served capabilities

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 17: The universal `/setup/` page

> **Execution order:** run this after Task 16 and before Task 14 (see the header note).

**Suggested implementer tier:** sonnet — a page assembled entirely from registries, plus the nav and banner wiring; the discipline is keeping every engine name out of the template.

**Files:**
- Modify: `console/setup/views.py` (replace the Task 1 skeleton)
- Modify: `console/setup/templates/setup/index.html` (replace the Task 1 skeleton)
- Modify: `templates/_shell.html` (add the always-on **Setup** nav entry)
- Modify: `modules/vision/templates/vision/create.html` (preflight banner links to the engine's setup section)
- Create: `console/setup/README.md`
- Test: `console/setup/tests/test_views.py` (extend), `modules/vision/tests/test_views_create.py` (extend)

**Interfaces:**
- Consumes: `ENGINES` and each adapter's `setup_guide` / `serves_capabilities` / `api_description` / `library_url` / `install_cmd_template` / `well_known_ports` / `is_healthy` (Tasks 4, 5, 16); `settings.INFERENCE_DEFAULT_ENDPOINTS` (Task 1); `all_roles()`, `resolve()`.
- Produces:
  - `GET /setup/` (`setup-index`) rendering three sections: **How models reach farabunker**, one section per registered engine at anchor `engine-<name>`, and **What each feature needs**.
  - `console.setup.views._engine_views() -> list[dict]` with keys `name`, `anchor`, `api_description`, `library_url`, `install_cmd_template`, `well_known_ports`, `endpoint`, `guide`, `platforms`, `verify_url`, `status` (`"reachable" | "unreachable" | "unknown"`).
  - `console.setup.views._role_views() -> list[dict]` with keys `label`, `capability`, `engines`, `bound`.
  - `templates/_shell.html` nav order: Ask · Document library · Model setup · **Setup** · Generate (the last one feature-gated, Task 11).

- [ ] **Step 1: Write the failing tests**

Replace the body of `console/setup/tests/test_views.py` with:

```python
"""Unit tests for the universal setup page (console/setup/views.py).

Engine-agnostic by construction: the assertions read the SAME registries the
page does, and one test registers a throwaway engine to prove a new adapter
appears with no change to the view or its template.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.urls import reverse

from console.setup import views
from core.inference.engines import ENGINES
from core.inference.engines.base import SetupGuide, SetupStep
from core.inference.roles import all_roles


def _guided_engine(name="stubsetup", healthy=True, capabilities=("chat",)):
    engine = MagicMock()
    engine.name = name
    engine.api_description = "a stub HTTP API"
    engine.library_url = "https://example.invalid/library"
    engine.install_cmd_template = "stub pull <model-name>"
    engine.well_known_ports = (9999,)
    engine.serves_capabilities = capabilities
    engine.is_healthy.return_value = healthy
    engine.setup_guide = SetupGuide(
        summary="A stub engine used only in tests.",
        platforms={"macos": (SetupStep("Install it", "Somehow.", "stub install"),)},
        network_note="Bind all interfaces.",
        models_note="Place your own model files.",
        verify_url_path="/stub-health",
    )
    return engine


def _bare_engine(name="bareengine"):
    engine = MagicMock(spec=["name", "api_description", "well_known_ports", "is_healthy", "install_cmd_template", "library_url"])
    engine.name = name
    engine.api_description = "a bare HTTP API"
    engine.install_cmd_template = "bare pull <model-name>"
    engine.library_url = ""
    engine.well_known_ports = (8888,)
    engine.is_healthy.return_value = False
    return engine


@pytest.mark.django_db
class TestSetupPageStructure:
    def test_it_answers_and_marks_itself_current_in_the_nav(self):
        body = Client().get(reverse("setup-index")).content.decode()
        assert f'<a href="{reverse("setup-index")}" class="current">Setup</a>' in body

    def test_it_explains_the_register_then_bind_flow_and_links_to_the_console(self):
        body = Client().get(reverse("setup-index")).content.decode()
        assert "How models reach farabunker" in body
        assert reverse("inference-console") in body

    def test_every_registered_engine_gets_its_own_anchored_section(self):
        body = Client().get(reverse("setup-index")).content.decode()
        for engine in ENGINES.values():
            assert f'id="engine-{engine.name}"' in body

    def test_every_registered_role_appears_in_the_needs_table(self):
        body = Client().get(reverse("setup-index")).content.decode()
        for role in all_roles():
            assert role.label in body
            assert role.capability in body


@pytest.mark.django_db
class TestEngineSections:
    def test_a_guided_engine_renders_its_own_steps_notes_and_verify_url(self):
        engine = _guided_engine()
        with patch.dict(ENGINES, {engine.name: engine}, clear=True), patch.dict(
            "django.conf.settings.INFERENCE_DEFAULT_ENDPOINTS",
            {"stubsetup": "http://stub:9999"},
            clear=True,
        ):
            body = Client().get(reverse("setup-index")).content.decode()

        assert "A stub engine used only in tests." in body
        assert "stub install" in body
        assert "Bind all interfaces." in body
        assert "Place your own model files." in body
        assert "http://stub:9999/stub-health" in body

    def test_health_is_reported_honestly(self):
        for healthy, expected in ((True, "Reachable"), (False, "Not reachable")):
            engine = _guided_engine(healthy=healthy)
            with patch.dict(ENGINES, {engine.name: engine}, clear=True), patch.dict(
                "django.conf.settings.INFERENCE_DEFAULT_ENDPOINTS",
                {"stubsetup": "http://stub:9999"},
                clear=True,
            ):
                body = Client().get(reverse("setup-index")).content.decode()
            assert expected in body

    def test_a_health_check_that_blows_up_reads_as_not_reachable(self):
        engine = _guided_engine()
        engine.is_healthy.side_effect = RuntimeError("boom")
        with patch.dict(ENGINES, {engine.name: engine}, clear=True), patch.dict(
            "django.conf.settings.INFERENCE_DEFAULT_ENDPOINTS",
            {"stubsetup": "http://stub:9999"},
            clear=True,
        ):
            response = Client().get(reverse("setup-index"))

        assert response.status_code == 200
        assert "Not reachable" in response.content.decode()

    def test_an_engine_with_no_default_endpoint_is_not_checked(self):
        engine = _guided_engine()
        with patch.dict(ENGINES, {engine.name: engine}, clear=True), patch.dict(
            "django.conf.settings.INFERENCE_DEFAULT_ENDPOINTS", {}, clear=True
        ):
            body = Client().get(reverse("setup-index")).content.decode()

        engine.is_healthy.assert_not_called()
        assert "not checked" in body

    def test_an_engine_without_a_guide_still_gets_a_minimal_card(self):
        engine = _bare_engine()
        with patch.dict(ENGINES, {engine.name: engine}, clear=True):
            body = Client().get(reverse("setup-index")).content.decode()

        assert 'id="engine-bareengine"' in body
        assert "a bare HTTP API" in body
        assert "bare pull &lt;model-name&gt;" in body
        assert "8888" in body

    def test_platform_sections_are_ordered_with_macos_open(self):
        engine = _guided_engine()
        engine.setup_guide = SetupGuide(
            summary="s",
            platforms={
                "linux": (SetupStep("L", "", "l"),),
                "macos": (SetupStep("M", "", "m"),),
                "windows": (SetupStep("W", "", "w"),),
            },
            network_note="n",
            models_note="mm",
            verify_url_path="/h",
        )
        with patch.dict(ENGINES, {engine.name: engine}, clear=True):
            platforms = views._platform_views(engine.setup_guide)

        assert [p["key"] for p in platforms] == ["macos", "windows", "linux"]
        assert [p["label"] for p in platforms] == ["macOS", "Windows", "Linux"]
        assert platforms[0]["open"] is True
        assert platforms[1]["open"] is False


@pytest.mark.django_db
class TestNeedsTable:
    def test_it_names_the_engines_that_can_serve_each_capability(self):
        engine = _guided_engine(capabilities=("image-generation",))
        with patch.dict(ENGINES, {engine.name: engine}, clear=True):
            rows = views._role_views()

        by_capability = {row["capability"]: row for row in rows}
        assert by_capability["image-generation"]["engines"] == ["stubsetup"]
        assert by_capability["chat"]["engines"] == []

    def test_an_unbound_role_reads_as_not_yet_assigned(self):
        from console.inference.models import ModelConnection, RoleBinding

        RoleBinding.objects.all().delete()
        ModelConnection.objects.all().delete()

        assert all(row["bound"] is False for row in views._role_views())

    def test_a_bound_role_reads_as_assigned(self):
        from console.inference.models import ModelConnection, RoleBinding

        RoleBinding.objects.all().delete()
        ModelConnection.objects.all().delete()
        connection = ModelConnection.objects.create(
            name="c", engine="comfyui", endpoint="http://x:8188",
            model_id="sdxl.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.create(role_key="vision.generate", connection=connection)

        rows = {row["capability"]: row for row in views._role_views()}
        assert rows["image-generation"]["bound"] is True
```

Append to `modules/vision/tests/test_views_create.py`:

```python
@pytest.mark.django_db
class TestBannerLinksToSetup:
    """The banner points at the engine's own section of /setup/, so an
    operator who has nothing bound is one click from the install steps."""

    def test_unbound_points_at_the_image_engine_section(self, client):
        body = client.get(reverse("vision-create")).content.decode()
        assert f'{reverse("setup-index")}#engine-comfyui' in body

    def test_bound_but_unreachable_points_at_the_bound_engine_section(self, client):
        _bind()
        with _engine(healthy=False):
            body = client.get(reverse("vision-create")).content.decode()

        assert f'{reverse("setup-index")}#engine-stubengine' in body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest console/setup/tests/test_views.py modules/vision/tests/test_views_create.py -q`
Expected: FAIL — `AttributeError: module 'console.setup.views' has no attribute '_platform_views'`, and the nav/banner assertions find nothing.

- [ ] **Step 3: Write `console/setup/views.py`**

```python
"""
The universal setup page (/setup/).

Answers one question for an operator with a fresh box: *how do I get a model
into this thing?* It explains the register-then-bind flow once, then renders
ONE section per registered engine — built entirely from what each adapter
declares about itself (`setup_guide`, and the facts every adapter has) — and
finishes with a table of what each registered feature needs and whether it
has it yet.

Engine-agnostic by construction: this module and its template contain NO
engine name, port, path, or install command. A newly registered adapter gets
its own anchored section, its own live health line, and its own row in the
"served by" column with no change here. An adapter that declares no
`setup_guide` still gets a minimal card from `api_description`,
`well_known_ports`, `install_cmd_template`, and `library_url`.

Read-only and never-500: every engine call is wrapped, because this is the
page an operator lands on precisely WHEN things are not working.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.views.generic import TemplateView

from core.inference.bindings import resolve
from core.inference.engines import ENGINES
from core.inference.engines.base import SETUP_PLATFORM_LABELS, SETUP_PLATFORMS, SetupGuide
from core.inference.roles import all_roles

logger = logging.getLogger(__name__)


def _platform_views(guide: SetupGuide | None) -> list[dict]:
    """The guide's platforms in display order (`SETUP_PLATFORMS`), each with
    its human label and whether it opens by default.

    Order comes from the platform tuple, not from the adapter's dict literal,
    so every engine's section reads the same way; the first platform is open
    so the page shows real steps without a click, and the others stay
    collapsed rather than burying the page in three OSes at once.
    """
    if guide is None:
        return []
    views = []
    for key in SETUP_PLATFORMS:
        steps = guide.platforms.get(key)
        if not steps:
            continue
        views.append(
            {
                "key": key,
                "label": SETUP_PLATFORM_LABELS[key],
                "steps": steps,
                "open": not views,
            }
        )
    return views


def _engine_views() -> list[dict]:
    """One view-model per registered engine.

    `status` is the honest three-way answer: "reachable", "unreachable", or
    "unknown" when there is no default endpoint to check at all. The health
    call is wrapped -- a broken adapter must not take down the page that
    explains how to fix it.
    """
    views = []
    for engine in ENGINES.values():
        endpoint = settings.INFERENCE_DEFAULT_ENDPOINTS.get(engine.name, "")
        guide = getattr(engine, "setup_guide", None)

        status = "unknown"
        if endpoint:
            try:
                status = "reachable" if engine.is_healthy(endpoint) else "unreachable"
            except Exception:  # noqa: BLE001 -- a broken engine is unreachable, never a 500
                logger.debug("Health check failed for %s at %r", engine.name, endpoint, exc_info=True)
                status = "unreachable"

        verify_path = guide.verify_url_path if guide else ""
        views.append(
            {
                "name": engine.name,
                "anchor": f"engine-{engine.name}",
                "api_description": getattr(engine, "api_description", ""),
                "library_url": getattr(engine, "library_url", ""),
                "install_cmd_template": getattr(engine, "install_cmd_template", ""),
                "well_known_ports": getattr(engine, "well_known_ports", ()),
                "endpoint": endpoint,
                "guide": guide,
                "platforms": _platform_views(guide),
                "verify_url": f"{endpoint}{verify_path}" if endpoint and verify_path else "",
                "status": status,
            }
        )
    return views


def _role_views() -> list[dict]:
    """One row per registered role for the "What each feature needs" table.

    `engines` lists the adapters that declare they can serve that capability
    (`serves_capabilities`) -- an honest "no registered engine serves this
    yet" when none do. `bound` answers "is it assigned right now?" through
    the real resolver, wrapped: an unassigned role is the normal state on a
    fresh box, not an error.
    """
    rows = []
    for role in all_roles():
        servers = [
            engine.name
            for engine in ENGINES.values()
            if role.capability in getattr(engine, "serves_capabilities", ())
        ]
        try:
            resolve(role.key)
            bound = True
        except Exception:  # noqa: BLE001 -- unassigned (or a provider hiccup) reads as not bound
            bound = False
        rows.append(
            {
                "label": role.label,
                "capability": role.capability,
                "engines": servers,
                "bound": bound,
            }
        )
    return rows


class SetupView(TemplateView):
    """GET /setup/ -- how to install an engine, how a model reaches a role."""

    template_name = "setup/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["engines"] = _engine_views()
        context["roles"] = _role_views()
        return context
```

- [ ] **Step 4: Write `console/setup/templates/setup/index.html`**

```html
{% extends "_shell.html" %}
{% comment %}
The universal setup page. Every engine section is rendered from the adapter's
own `setup_guide` (core/inference/engines/base.py) -- this template names no
engine, no port, no command, so a newly registered adapter appears here with
no template change. An adapter with no guide falls back to the facts every
adapter has.
{% endcomment %}
{% block title %}Setup — farabunker{% endblock %}
{% block nav_current_setup %}current{% endblock %}
{% block extra_style %}
  :root { --page-max-width: 900px; }
  .setup-wrap { max-width: var(--page-max-width); margin: 0 auto; }
  .card {
    border: 1px solid var(--border); border-radius: 8px; background: var(--panel);
    padding: 1rem 1.25rem; margin-bottom: 1.25rem;
  }
  .muted { color: var(--muted); font-size: 0.9rem; }
  h2 { font-size: 1.15rem; margin-top: 0; }
  ol.flow, details ol { padding-left: 1.2rem; }
  details ol > li { margin-bottom: 0.6rem; }
  pre {
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    padding: 0.55rem 0.7rem; overflow-x: auto; margin: 0.35rem 0 0;
  }
  pre, code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85rem; }
  details.platform { margin: 0.5rem 0; }
  details.platform summary { cursor: pointer; font-weight: 600; }
  .status { font-weight: 600; }
  .status.ok { color: #1b7f4f; }
  .status.down { color: #b3261e; }
  table.needs { border-collapse: collapse; width: 100%; }
  table.needs th, table.needs td {
    text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border);
    vertical-align: top; font-size: 0.9rem;
  }
{% endblock %}
{% block content %}
<div class="setup-wrap">
<h1>Setup</h1>

<section class="card" id="how-models-reach-farabunker">
  <h2>How models reach farabunker</h2>
  <p class="muted">
    farabunker runs no model itself and downloads nothing. You install an engine,
    you place the model files, and farabunker connects to what it finds.
  </p>
  <ol class="flow">
    <li><strong>Install an engine</strong> and put your model files where it can see them — one section per engine below.</li>
    <li><strong>Register the model</strong> on <a href="{% url 'inference-console' %}">Model setup</a>: it lists what each engine reports on this machine; press “Add to registered”.</li>
    <li><strong>Assign it to a role</strong> on that same page. Until you do, the role is honestly unassigned — nothing is chosen for you.</li>
  </ol>
</section>

{% for engine in engines %}
<section class="card" id="{{ engine.anchor }}">
  <h2>{{ engine.name }}</h2>
  {% if engine.guide %}
  <p>{{ engine.guide.summary }}</p>
  {% else %}
  <p>Speaks {{ engine.api_description|default:"its own HTTP API" }}.</p>
  {% endif %}

  {% for platform in engine.platforms %}
  <details class="platform"{% if platform.open %} open{% endif %}>
    <summary>{{ platform.label }}</summary>
    <ol>
      {% for step in platform.steps %}
      <li>
        <strong>{{ step.title }}</strong>
        {% if step.body %}<div class="muted">{{ step.body }}</div>{% endif %}
        {% if step.command %}<pre>{{ step.command }}</pre>{% endif %}
      </li>
      {% endfor %}
    </ol>
  </details>
  {% empty %}
  <p class="muted">
    No install guide ships for this engine yet.
    {% if engine.install_cmd_template %} Getting models looks like <code>{{ engine.install_cmd_template }}</code>.{% endif %}
    {% if engine.well_known_ports %} It is usually served on port {{ engine.well_known_ports|join:", " }}.{% endif %}
    {% if engine.library_url %} Project page: <a href="{{ engine.library_url }}">{{ engine.library_url }}</a>.{% endif %}
  </p>
  {% endfor %}

  {% if engine.guide %}
  <p><strong>Reaching it from farabunker.</strong> {{ engine.guide.network_note }}</p>
  <p><strong>Models.</strong> {{ engine.guide.models_note }}</p>
  {% endif %}

  <p>
    <strong>Verify:</strong>
    {% if engine.verify_url %}<code>{{ engine.verify_url }}</code>{% else %}<span class="muted">no default endpoint is configured for this engine</span>{% endif %}
    —
    {% if engine.status == "reachable" %}<span class="status ok">Reachable</span>
    {% elif engine.status == "unreachable" %}<span class="status down">Not reachable</span>
    {% else %}<span class="status muted">not checked</span>{% endif %}
  </p>
</section>
{% endfor %}

<section class="card" id="what-each-feature-needs">
  <h2>What each feature needs</h2>
  <table class="needs">
    <thead><tr><th>Feature</th><th>Needs</th><th>Served by</th><th>Assigned?</th></tr></thead>
    <tbody>
      {% for role in roles %}
      <tr>
        <td>{{ role.label }}</td>
        <td>{{ role.capability }}</td>
        <td>{% if role.engines %}{{ role.engines|join:", " }}{% else %}<span class="muted">no registered engine serves this yet</span>{% endif %}</td>
        <td>{% if role.bound %}yes{% else %}<a href="{% url 'inference-console' %}">not yet</a>{% endif %}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
</div>
{% endblock %}
```

- [ ] **Step 5: Add the always-on Setup entry to the shared nav**

In `templates/_shell.html`, the nav becomes (the Generate entry from Task 11 stays last, still feature-gated):

**Before**

```html
  <a href="{% url 'inference-console' %}" class="{% block nav_current_console %}{% endblock %}">Model setup</a>
  {% if "vision" in farabunker_features %}<a href="{% url 'vision-create' %}" class="{% block nav_current_vision %}{% endblock %}">Generate</a>{% endif %}
```

**After**

```html
  <a href="{% url 'inference-console' %}" class="{% block nav_current_console %}{% endblock %}">Model setup</a>
  <a href="{% url 'setup-index' %}" class="{% block nav_current_setup %}{% endblock %}">Setup</a>
  {% if "vision" in farabunker_features %}<a href="{% url 'vision-create' %}" class="{% block nav_current_vision %}{% endblock %}">Generate</a>{% endif %}
```

Extend the file's nav comment to name the new destination and why it is NOT gated: it explains the engines themselves, which exist regardless of which features are on.

- [ ] **Step 6: Point the vision banner at the engine's setup section**

In `modules/vision/templates/vision/create.html`:

**Before**

```html
{% if preflight.state != "ready" %}
<div class="banner warn">
  {{ preflight.message }}
  {% if preflight.state == "unbound" %}
  <a href="{% url 'inference-console' %}">Assign a model</a>
  {% else %}
  <a href="{% url 'inference-console' %}">Check the model setup</a>
  {% endif %}
</div>
{% endif %}
```

**After**

```html
{% if preflight.state != "ready" %}
<div class="banner warn">
  {{ preflight.message }}
  {% if preflight.state == "unbound" %}
  <a href="{% url 'inference-console' %}">Assign a model</a>
  {% else %}
  <a href="{% url 'inference-console' %}">Check the model setup</a>
  {% endif %}
  {% comment %}
  Deep-link to the bound engine's own section of /setup/. With nothing bound
  there is no engine to name, so this falls back to the image engine this cut
  ships an adapter for -- a documentation anchor only, never a behavioural
  default: no model, endpoint, or binding is presumed anywhere by it.
  {% endcomment %}
  <a href="{% url 'setup-index' %}#engine-{% if preflight.resolved %}{{ preflight.resolved.engine }}{% else %}comfyui{% endif %}">How to set this up</a>
</div>
{% endif %}
```

- [ ] **Step 7: Write `console/setup/README.md`**

Cover: what `/setup/` is for (the "how do I get a model into this thing" page); that it is assembled entirely from `ENGINES` and the role registry so it needs no maintenance when an adapter is added; how an adapter opts in by declaring `setup_guide` (`SetupGuide`/`SetupStep`, the three platform keys, the network/models notes, `verify_url_path`) and `serves_capabilities`; what an adapter without a guide gets; that the page is read-only, never-500, and unauthenticated like the rest of Phase 1; and how to run its tests.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/pytest console/setup/tests/test_views.py modules/vision/tests/test_views_create.py -q`
Expected: PASS.

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all PASS — the nav change touches every page, so the RAG, console, and vision page tests are the real check.

- [ ] **Step 10: Commit**

```bash
git add console/setup templates/_shell.html modules/vision/templates/vision/create.html \
        modules/vision/tests/test_views_create.py
git commit -F - <<'EOF'
feat(setup): universal /setup/ page built from each engine's own guide

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 14: Documentation — ADR 0012, DEV.md, ARCHITECTURE, READMEs, ROADMAP

**Suggested implementer tier:** sonnet — no code, but the ADR must argue the decisions faithfully and the install guide must be correct on three platforms.

**Files:**
- Create: `docs/adr/0012-image-generation-engine-adapter.md`, `modules/vision/README.md`
- Modify: `docs/DEV.md` (NEW sections only — the other track owns the existing ones), `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`
- Test: none (documentation), but the full suite must be green at the end.

**Interfaces:** none.

- [ ] **Step 1: Write ADR 0012**

Create `docs/adr/0012-image-generation-engine-adapter.md` following the house ADR shape (Status / Context / Decision / Consequences), covering:

- **Status:** Accepted, 2026-08-22. Builds on ADR 0010 (model-management framework) and its 2026-08-22 amendments, and ADR 0011 (branch preview stacks).
- **Context:** image generation is the first non-RAG capability; ADR 0010 predicted a second engine and a non-RAG role as its validation case.
- **Decision** — record D1–D12 verbatim in a table with the same reasons the spec gives: ComfyUI first (D1); an `ENGINES` adapter, not a special case (D2); a new `image-generation` capability distinct from the input-side `vision` (D3); one role `vision.generate`, so the *binding is the checkpoint* (D4); operation-driven, not mode-driven (D5); generic job/output records with the verbatim engine payload (D6); assets as a second axis (D7); `ModelConnection.config` (D8); the feature flag whose one job is gating role registration and the URL mount (D9); poll-driven completion, no worker (D10); per-engine discovery endpoints (D11); no default model, `COMFYUI_BASE_URL` as the one allowed location default (D12).
  Also record `GenerationRejected` (`core/inference/engines/base.py`) as a decision, not an implementation detail: it is the platform-level exception an engine raises when it refuses a submission outright, and it is what implements spec §6's "job created then immediately failed with the engine's own text" row — distinct from a transport failure, which leaves the job untouched and retried on the next poll.
- **Engine-declared setup guides (owner requirement, 2026-08-23):** every adapter owns a `SetupGuide` describing how its server is installed on macOS/Windows/Linux, how it must listen to be reachable from the `web` container, where its model files go, and the path to verify it — and declares `serves_capabilities`. The `/setup/` page (`console/setup/`) renders those declarations and the role registry; it contains no engine name, port, path, or install command, so a newly registered adapter documents itself. Record why the guide lives on the adapter rather than in a docs page: the install steps and the health path are engine knowledge, and a second copy in prose would rot the first time an adapter changed. Record too that the page is a reading surface only — it never registers, binds, installs, or downloads anything.
- **The content-agnostic stance, stated plainly:** any checkpoint the operator places is a first-class model; the platform performs no prompt or output filtering and no model inspection; what to run is the operator's decision. This is a design invariant, not an omission.
- **Deviations from the design spec** — record each, with its reason, so the next reader does not treat them as drift:
  - **`discover(endpoints: dict[str, list[str]], connections)`** takes a *list* per engine, not the spec §4.6's `set`: polling order must be deterministic (default endpoint, then registered connections, then the endpoint being viewed) so a model seen at two addresses always attributes to the same one, and so the console's output does not reshuffle between page loads. De-duplication is by `norm_endpoint`, which a set could not do anyway.
  - **Engine-agnostic error copy** replaces the spec's ComfyUI-named strings: `"The image engine ({resolved.engine}) at {resolved.endpoint} is not reachable."` and `"Still queued — check that the image engine is running."` (and the lost-job message likewise names no product). Spec §3 forbids the page from knowing which engine is bound, so the engine's own name comes from the resolved binding rather than from hardcoded copy — same information, no second adapter needing new strings.
  - **Validation has two layers.** `core.inference.operations.validate_params` is the schema *floor* every caller shares, including the future chatbot tool that builds no form; the Django form adds the engine's *live* option lists (samplers, schedulers, from `list_choices`) on top. The schema cannot enumerate engine-reported choices, so it accepts any non-blank value for such a param and an unknown one surfaces as an honestly failed job.
- **Consequences:** adding img2img/inpaint/ControlNet/upscale is one `Operation` + one template; a second engine is one adapter; a chatbot tool is a caller of `modules.vision.services`; deferred items (spec §9) listed explicitly, including that `/vision/`'s mutation endpoints are unauthenticated — the same Phase-1 gap `/inference/` carries.

- [ ] **Step 2: Add the ComfyUI install section to `docs/DEV.md`**

Append a NEW top-level section (do not edit existing sections — the other track owns them):

````markdown
## Install ComfyUI (image generation)

> The in-app version of this section lives at **`/setup/`** — it renders the
> same steps from the engine adapter's own `setup_guide`, plus a live
> reachability check for each engine. Prefer it when the box is running; this
> section is the copy you can read before it is.

ComfyUI runs **natively on the host** so it can use the GPU (Metal on macOS,
CUDA/ROCm on Linux, CUDA on Windows). Docker cannot reach the Metal GPU, so
the `web` container talks to it over `host.docker.internal`, exactly like
Ollama. Start it with `--listen 0.0.0.0` or it binds to localhost only and
the container cannot reach it.

**macOS (Apple Silicon)**

```bash
git clone https://github.com/comfyanonymous/ComfyUI
cd ComfyUI
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt          # installs torch with MPS support
python main.py --listen 0.0.0.0 --port 8188
```

**Windows** — download the portable build from the project's releases page,
unzip it, and edit `run_nvidia_gpu.bat` (or `run_cpu.bat`) to add
`--listen 0.0.0.0`. Checkpoint ids will come back with a backslash when they
sit in a subfolder (`sdxl\turbo.safetensors`); that is normal and farabunker
passes them through unchanged.

**Linux** — clone as on macOS, then install torch for your accelerator
(CUDA or ROCm wheel index), then `python main.py --listen 0.0.0.0 --port 8188`.

**Models are yours to place.** farabunker never downloads a checkpoint.
Copy `.safetensors` files into `ComfyUI/models/checkpoints/` — community
checkpoints come from Civitai or Hugging Face, downloaded by you, out of
band. Whatever is in that folder is what `/inference/` will list.

**"Offline" here** means: once the checkpoints are on disk, the whole path
(browser → farabunker → ComfyUI → image) runs with the network off. Verify
it by disabling Wi-Fi and generating.

**One GPU, two engines.** Ollama and ComfyUI both want VRAM. On a
single-GPU box, a large LLM held in memory can starve a generation (and
vice versa) — `ollama stop <model>` frees it, and ComfyUI releases its
weights between runs. Nothing in farabunker arbitrates this; it is a
hardware fact, not a policy.

## Generate images (`/vision/`)

1. Start ComfyUI (above) and make sure `docker compose ps` shows `web` up.
   Open **Setup** in the top nav to confirm the engine reads *Reachable*.
2. Open `/inference/`. The checkpoints ComfyUI reports appear under **On
   this machine**, tagged with the `comfyui` engine. Click **Add to
   registered** on the one you want — it registers against ComfyUI's own
   endpoint, not Ollama's.
3. In the **Image generation** role row, choose that connection and apply.
4. Click **Generate** in the top nav (it appears on every page while the
   vision feature is enabled), type a prompt, and press **Generate**. The
   card polls until the image appears; the **Gallery** tab lists everything
   generated, with "reuse settings" to send a previous job's parameters back
   into the form.

If the page says *No model assigned for Image generation*, step 3 has not
been done. If it says the engine is not reachable, check that ComfyUI is
running with `--listen 0.0.0.0` and that `COMFYUI_BASE_URL` matches (inside
the containers it must be `http://host.docker.internal:8188`).

To turn the whole feature off, set `FARABUNKER_FEATURES=` (empty) — the
role disappears from `/inference/`, the **Generate** entry disappears from
the top nav, and `/vision/` stops existing.
````

- [ ] **Step 3: Update `docs/ARCHITECTURE.md`**

- In §3 (plug-and-play) or §4 (core services), describe the three axes the gateway now has: **engines** (how to talk to a server), **operations** (what can be generated, with parameter schemas), and **assets** (files that adorn a job). Note that a role binds a model for a capability, and an operation names what to do with it.
- Add `modules/vision` to the module list beside `modules/rag`, with its mount (`/vision/`), its role (`vision.generate`), its capability (`image-generation`), and its data directory (`data/generated/<job-uuid>/`).
- Note the feature-flag pattern (`FARABUNKER_FEATURES`) as the way a module's roles, routes, and shared-nav entry are gated — the flag reaches templates through `modules.vision.context_processors.features` as `farabunker_features`.
- Add `console/setup` beside `console/inference` in the console layer: a read-only page at `/setup/` assembled from `ENGINES` and the role registry, where each adapter's own `SetupGuide` is the source of its install instructions — the "self-describing engine" counterpart to the module contract's self-describing UI surface.

- [ ] **Step 4: Write `modules/vision/README.md`**

Cover: what the module does; the role and capability it registers and the flag that gates them (including the `farabunker_features` context processor that feature-gates the shared nav's **Generate** entry); the operation registry and how to add an operation; the service surface (`preflight`/`submit_job`/`refresh_job`/`wait_for`/`delete_job`) as the seam the chatbot tool will call; the storage layout under `data/generated/<job-uuid>/`; the poll-driven page and its no-JS fallback; how to run its tests; and the deferred list (spec §9) so the next reader knows what is intentionally absent.

- [ ] **Step 5: Update `docs/ROADMAP.md`**

Tick image generation as delivered, naming the engine adapter (ComfyUI), the `image-generation` capability, the `vision.generate` role, and `/vision/`; keep the deferred items (img2img/inpaint/ControlNet/upscale, multi-file model families, per-job checkpoint override, background worker, retention, chatbot tool, second adapter) as follow-ups.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all PASS.

Then run it in the reversed order the repo also checks:

Run: `.venv/bin/pytest modules console scripts -q`
Expected: all PASS (registry-mutating tests must not leak into their neighbours).

- [ ] **Step 7: Commit**

```bash
git add docs/adr/0012-image-generation-engine-adapter.md docs/DEV.md \
        docs/ARCHITECTURE.md docs/ROADMAP.md modules/vision/README.md
git commit -F - <<'EOF'
docs(vision): ADR 0012, ComfyUI install guide, architecture and module docs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
```

---

### Task 15: Live verification on the preview stack (evidence, not code)

**Suggested implementer tier:** opus — diagnosing a live stack against a real ComfyUI needs judgment; there is no test to lean on.

**Files:** none. This task produces **screenshots**, not code. Nothing is reported complete before them (the verification doctrine).

**Interfaces:** none.

- [ ] **Step 1: Start ComfyUI natively with at least one checkpoint present**

```bash
# in the ComfyUI checkout, with a .safetensors already in models/checkpoints/
python main.py --listen 0.0.0.0 --port 8188
curl -s http://localhost:8188/system_stats | head -c 200
curl -s http://localhost:8188/object_info/CheckpointLoaderSimple | head -c 300
```
Expected: JSON from both; the second lists your checkpoint filename.

- [ ] **Step 2: Bring up the preview stack for this branch**

```bash
scripts/preview up vision-generation --port 8002 --db-port 5434
```
Expected: it prints `http://localhost:8002`; `data/preview/vision-generation/generated/` exists.

- [ ] **Step 3: Prove the container can reach ComfyUI**

```bash
docker compose -p farabunker-preview-vision-generation exec web \
  python -c "import httpx; print(httpx.get('http://host.docker.internal:8188/system_stats').status_code)"
```
Expected: `200`. A failure here is almost always a missing `--listen 0.0.0.0`.

- [ ] **Step 3b: Check the setup page at `http://localhost:8002/setup/`**

- The top nav shows **Setup** on every page (it is never feature-gated). **Screenshot.**
- The page renders a section for **both** engines — `ollama` and `comfyui` — each with its own platform steps, network note, models note, and verify URL. **Screenshot.**
- Each engine's status line is honest: ComfyUI reads *Reachable* now that it is running; the Ollama line reads whatever is true on this machine. **Screenshot.**
- The "What each feature needs" table lists every registered role with its capability, the engines that can serve it, and whether it is assigned yet. **Screenshot.**

- [ ] **Step 4: Register and bind, in the browser at `http://localhost:8002/inference/`**

- The checkpoint appears under **On this machine**, engine `comfyui`. **Screenshot.**
- Click **Add to registered**; confirm the new connection's endpoint reads `http://host.docker.internal:8188` (not the Ollama address). **Screenshot.**
- In the **Image generation** role row, choose it and apply; the row reads as bound. **Screenshot.**
- The "Getting models" checklist row *an image-generation model* now reads satisfied. **Screenshot.**

- [ ] **Step 5: Generate, from the top nav's Generate entry**

- The shared top nav shows **Generate** on `/inference/` and `/rag/` too, and it lands on `http://localhost:8002/vision/` with that entry marked current. **Screenshot.**
- With nothing bound (before Step 4, or after unbinding), the banner's **How to set this up** link lands on `/setup/#engine-comfyui` and scrolls to that engine's section; while bound-but-unreachable it points at the bound engine's own anchor. **Screenshot.**
- The form renders with live sampler/scheduler dropdowns from ComfyUI. **Screenshot.**
- Type a prompt, press **Generate**; the card appears as queued/running and updates without a manual reload. **Screenshot.**
- The finished card shows the image, seed, size, steps, cfg, sampler/scheduler, and model. **Screenshot.**

- [ ] **Step 6: Gallery, file, reuse, delete**

- `/vision/gallery/` lists the image with its facts. **Screenshot.**
- Click the image: the file opens from `/vision/outputs/<id>/file/`. **Screenshot.**
- Click **Reuse settings**: the create form is prefilled with that job's parameters. **Screenshot.**
- Delete the job from its card; confirm it disappears and that
  `data/preview/vision-generation/generated/<job-uuid>/` is gone:
  ```bash
  ls data/preview/vision-generation/generated/
  ```
  **Screenshot** of the emptied gallery.

- [ ] **Step 7: Prove it offline**

Disable Wi-Fi / pull the network, then generate again on `/vision/`.
Expected: it works end to end. **Screenshot** with the network indicator visible.

- [ ] **Step 8: Prove the honest failure states**

- Stop ComfyUI (`Ctrl-C`), reload `/vision/`: the banner says the engine is not reachable and the form is still there. **Screenshot.**
- Press **Generate** anyway: a 503 carrying that same message, not a traceback. **Screenshot.**
- Submit a job, then restart ComfyUI while it is queued: the card ends `failed` with the "no longer has this job … resubmit" message. **Screenshot.**
- Set `FARABUNKER_FEATURES=` in the preview's `.env`, restart the stack, and confirm `/vision/` 404s, the **Generate** entry is gone from the top nav on every page (no `NoReverseMatch`, no broken link), and the Image generation role is gone from `/inference/`. **Screenshot.** Restore the flag afterwards.

- [ ] **Step 9: Full suite, natively, one more time**

```bash
docker compose up -d db
.venv/bin/pytest -q
```
Expected: all PASS.

- [ ] **Step 10: Report**

No commit — this task produces evidence. Report: every screenshot, the ComfyUI version from `/system_stats`, the checkpoint used, and any deviation from the walk above. Tear the preview down when finished:

```bash
scripts/preview down vision-generation
```

---

## Self-Review

**1. Spec coverage**

| Spec | Task |
|---|---|
| §2 D1 ComfyUI first | 5, 6, 7 |
| §2 D2 registered `ENGINES` adapter | 5 |
| §2 D3 `image-generation` capability distinct from `vision` | 1 |
| §2 D4 one role `vision.generate`, binding *is* the checkpoint | 1, 9 |
| §2 D5 operation-driven | 3 |
| §2 D6 generic job/output records + verbatim payload | 9, 10 |
| §2 D7 assets as a second axis (interface now, UI later) | 4, 5 |
| §2 D8 `ModelConnection.config` | 2 |
| §2 D9 feature flag gates role registration + URL mount | 1, 9 |
| §2 D10 poll-driven, no worker | 10, 12 |
| §2 D11 per-engine discovery endpoints | 2 |
| §2 D12 no default model; `COMFYUI_BASE_URL` the one location default | 1 |
| §3 layering (core purity, stateless engines, graph vocabulary contained) | 3–8 (enforced by Global Constraints) |
| §4.1 `operations.py`, `TXT2IMG`, `validate_params`, `GenerationRequest` | 3 |
| §4.2 `Asset`, `JobStatus`, `ImageGenerator`, optional engine members | 4 |
| §4.3 ComfyUI adapter (health, listing, assets, choices, generator) | 5, 7 |
| §4.4 `comfyui_workflows/` txt2img template | 6 |
| §4.5 `CAPABILITIES`, `VISION_GENERATE_ROLE`, `get_image_generator` | 1, 8 |
| §4.6 coordination commit (settings, urls, discovery, views, models, bindings, env/compose/preview) | 1, 2 |
| §4.7 models, store, services, forms, views, urls, templates | 9–13 |
| Reachability: the shared shell links to `/vision/`, feature-gated | 1 (context processor), 11 (nav entry) |
| Owner requirement 2026-08-23: universal in-app Setup page, every engine, future engines automatically | 4 (`SetupGuide` types), 5 (ComfyUI guide), 16 (Ollama guide), 17 (the page, nav entry, vision banner link), 1 (app skeleton + mount), 14 (ADR/DEV/ARCHITECTURE), 15 (evidence) |
| §5 data & storage (`data/generated/<uuid>/`, copied outputs, delete removes files) | 1, 9, 10, 12 |
| §6 error handling matrix (unbound, unreachable, rejected, execution error, lost, poll failure, stale, invalid params) | 7, 10, 11, 12 |
| §7 testing (per-area table, `fake_comfyui` helper, migration on an empty DB, live acceptance walk) | every task; helper in 5; acceptance in 15 |
| §8 documentation (ADR 0012, DEV.md, ARCHITECTURE, READMEs, ROADMAP, env/compose comments) | 1 (env/compose), 2 (console README), 14 |
| §9 deferred | not implemented; recorded in ADR 0012 and `modules/vision/README.md` (Task 14) |
| §10 coordination | Tasks 1–2 only, announced after Task 2 |

**2. Placeholder scan:** no "TBD", "similar to Task N", or "add error handling" steps — every code step carries the actual code, every test step the actual tests. Two inline corrections were folded in during writing: `test_done_stores_every_output_with_its_dimensions` had a leftover conditional `with` expression (Task 10 flags the fix explicitly), and `output_file` moved from Task 13 into Task 12, where the job card first links to it.

**3. Type consistency:**
- `discover(endpoints: dict[str, list[str]], connections)` — the map is produced by `_engine_endpoints(connections, endpoint)` (Task 2) and consumed only there and in tests.
- `DiscoveryRow.endpoint` (Task 2) is read by `_installed_views` and `_installed_row.html` only.
- `validate_params(operation, raw) -> dict` raising `ParamError` (Task 3) is called by `services.submit_job` (Task 10) and caught in `views.generate` (Task 12); the form layer's own validation is separate and documented as such.
- `GenerationRequest(operation, model_id, params, inputs, client_ref)` (Task 3) is built in Task 10 and consumed in Tasks 6 and 7 with the same field names.
- `ImageGenerator.submit -> (engine_ref, payload)` / `.status -> JobStatus` / `.fetch_outputs -> [(filename, bytes, media_type)]` (Task 4) match `ComfyUIGenerator` (Task 7), `StubGenerator` (Task 10), and `services.refresh_job`'s use exactly.
- `JobStatus.state` values (`queued|running|done|failed|lost`) match `JOB_STATES` and the branches in `refresh_job`; `GenerationJob.Status` deliberately has no `lost` member (a lost job is `failed` with `LOST_MESSAGE`), and that asymmetry is documented in both places.
- `store.store_output(job_id, index, filename, content) -> str` / `store.png_dimensions(content) -> (w, h)` (Task 9) match their call sites in `refresh_job`.
- URL names (`vision-create`, `vision-generate`, `vision-job-status`, `vision-job-delete`, `vision-gallery`, `vision-output-file`) match between `urls.py`, the views, the templates, and every test.
- `preflight()` returns `PreflightResult(state, resolved, message)`; templates read `preflight.state` / `preflight.message`, and `VisionUnavailable` carries the same two fields.
- `SetupGuide`/`SetupStep` field names (`summary`, `platforms`, `network_note`, `models_note`, `verify_url_path`; `title`, `body`, `command`) are identical in Task 4's definition, Task 5's ComfyUI guide, Task 16's Ollama guide, Task 17's view and template, and every test. Platform keys are always `"macos" | "windows" | "linux"` from `SETUP_PLATFORMS`, labelled from `SETUP_PLATFORM_LABELS`; `serves_capabilities` is a tuple of `CAPABILITIES` members on both adapters; the engine anchor is `engine-<engine.name>` in `_engine_views`, the setup template, the vision banner, and the tests; the URL name is `setup-index` in `urls.py`, both templates, and every test; `_engine_views()["status"]` is exactly `"reachable" | "unreachable" | "unknown"` in the view, the template, and the tests.
- The template context key is `farabunker_features` in all four places: the processor's return value (Task 1), its registration in `TEMPLATES` (Task 1), its test (Task 1), and the `{% if "vision" in farabunker_features %}` guard in `templates/_shell.html` (Task 11). The nav block name is `nav_current_vision` in both `_shell.html` and `vision/base.html`.

**4. Known conditional:** Task 2's mechanical `sed` over `test_discovery.py` assumes every existing call is literally `discover(ENDPOINT, `. If a call site differs, convert it by hand to `discover(_at(), ` — the helper is defined once at the top of that file and evaluates the engine registry per call, so a test that registers a stub engine keeps working unchanged.
