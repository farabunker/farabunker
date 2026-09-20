# Vision Pre-Expansion Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the six bucket-A findings of the pre-expansion hygiene audit so that adding img2img / inpaint / ControlNet / upscale really is one `Operation` plus one graph template, exactly as ADR 0012 promises.

**Architecture:** Four seams get repaired and one document gets corrected. The *platform* layer stops putting non-JSON objects into a JSONField and stops re-resolving a role it already resolved; the *engine* layer takes ownership of moving a file input to wherever the engine actually lives; the *page* layer stops hardcoding one operation's parameter names into templates; the *template* layer stops copying the job card's CSS and delete control per page. Every change is additive to the existing grammar (`Operation`/`Param`, `GenerationRequest`, `ImageGenerator`, `ResolvedModel`) — no new abstraction is introduced.

**Tech Stack:** Python 3, Django (templates, forms, migrations), `httpx` (engine HTTP), pytest + `pytest-django`, ComfyUI HTTP API (`/prompt`, `/history`, `/queue`, `/view`, `/upload/image`, `/object_info`, `/system_stats`).

**Spec:** `docs/superpowers/specs/2026-08-22-image-generation-design.md` (binding design) — read with `docs/adr/0012-image-generation-engine-adapter.md` (the decisions as shipped). The audit this plan implements is the pre-expansion hygiene audit of `vision-generation` @ `4cf4881`, bucket A findings A1–A6.

**Worktree:** `<worktree>`, branch `vision-generation`, starting at `4cf4881`.
**Venv:** `<repo>/.venv`. Baseline before Task 1: **948 passed, 1 skipped** in BOTH orders.

The pass count each task expects is arithmetic from that baseline plus the tests that task adds. A different total is not a failure by itself — recount against the tests you actually wrote before assuming something broke. A total that differs **between the two orders** is always a real isolation bug.

## Global Constraints

These are project rules. Every task's requirements implicitly include all of them.

- Every task ships with unit tests **and** updated docs.
- No `conftest.py` anywhere — shared test helpers live in a plain importable module (`modules/vision/tests/_helpers.py`); autouse fixtures are defined per test module and delegate their bodies to it.
- Class-level `@pytest.mark.django_db` (on the test class, not per method).
- Mock at the `httpx` layer, never the engine class — patch `core.inference.engines.comfyui.httpx.get` / `.post`, so the adapter's real URL building, JSON parsing, and error handling run.
- No baked model defaults. `COMFYUI_BASE_URL` is a *location*, and the only default of its kind.
- Feature flag `FARABUNKER_FEATURES` semantics unchanged: it gates role registration, operation registration, and the URL mount — nothing else.
- The suite must stay green in **BOTH** orders at every commit:
  - `<repo>/.venv/bin/pytest -q`
  - `<repo>/.venv/bin/pytest modules console scripts -q`
- Never touch `console/inference/*` beyond what a task explicitly names — that is the model-management track's area. (`console/setup/*` **is** this track's area; Task 5 names one file there.)
- Every commit carries these two trailers, exactly:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  ```

---

## Audit coverage

Every bucket-A finding, and the task that closes it. Bucket B is Forward notes at the end of this document; bucket C was adjudicated as leave/stale and appears nowhere here.

| Finding | Defect | Task |
|---|---|---|
| A1 | A `file` param's `UploadedFile` lands in `GenerationJob.params`, a JSONField — a `TypeError` on the first img2img submit | Task 2 |
| A2 | `GenerationRequest.inputs` carries container-local paths to an engine on another machine; no transfer anywhere | Task 3 |
| A3 | The page is hardwired to one operation in five places; the facts lines are txt2img-shaped literals | Task 4 |
| A4 | Neither `submit_job` nor `refresh_job` uses the job's own recorded binding | Task 1 |
| A5 | ADR 0012 promises an additive expansion the code could not deliver | Task 6 |
| A6 | The job card's CSS and delete control are owned by a page and already duplicated; five raw `#b3261e` literals | Task 5 |

Task 7 is the closing verification: full suite in both orders, migration on an empty DB, docs sweep, live preview pixels.

---

## File Structure

Files this plan creates or modifies, and what each is responsible for afterwards.

**Created**

| Path | Responsibility |
|---|---|
| `modules/vision/migrations/0002_generationjob_endpoint.py` | Adds the endpoint column that completes the D6 binding snapshot. |
| `modules/vision/templates/vision/_job_facts.html` | The ONE facts line, rendered from an `Operation`'s `Param` schema. Included by the card and the gallery caption. |
| `modules/vision/templates/vision/_delete_control.html` | The ONE two-step delete confirm. Included by the card and the gallery figure. |

**Modified**

| Path | Change |
|---|---|
| `core/inference/gateway.py` | Adds `get_image_generator_for(resolved)`; `get_image_generator(role)` becomes a one-liner over it. |
| `core/inference/roles.py` | Adds `IMAGE_GENERATION_CAPABILITY`, beside the role keys. |
| `core/inference/operations.py` | Adds `Operation.file_param_keys()`; `validate_params` gains a `"file"` branch that records a JSON-safe basename, never the upload object; `TXT2IMG.label` carries the page heading. |
| `core/inference/engines/base.py` | `ImageGenerator.submit`'s contract states that the implementation owns input transfer. |
| `core/inference/engines/comfyui.py` | `ComfyUIGenerator` uploads every file input via `POST /upload/image` before building the graph; `supported_operations` reads the template registry. |
| `core/inference/engines/comfyui_workflows/__init__.py` | `Template` gains a fourth argument (the engine-side input references) and the package exports `template_keys()`. |
| `core/inference/engines/comfyui_workflows/txt2img.py` | `build` takes (and documents that it ignores) that fourth argument. |
| `modules/vision/models.py` | Adds `GenerationJob.endpoint` and the `GenerationJob.facts` property. |
| `modules/vision/services.py` | Records the endpoint; builds generators from the job's own binding; filters uploads to declared file params. |
| `modules/vision/forms.py` | `initial` never prefills a `"file"` field. |
| `modules/vision/views.py` | Operation comes from the URL/POST via `resolve_page_operation`; `operations` reaches the templates. |
| `modules/vision/urls.py` | Adds the `op/<slug:operation_key>/` route. |
| `modules/vision/templates/vision/base.html` | Owns every rule the shared job-card fragment needs. |
| `modules/vision/templates/vision/create.html` | Loses the shared CSS, gains `enctype`, the operation chooser, and the hidden `operation` field. |
| `modules/vision/templates/vision/gallery.html` | Loses the shared CSS and the copied delete markup; uses the shared partials. |
| `modules/vision/templates/vision/_job_card.html` | Uses both shared partials instead of literals. |
| `templates/_shell.html` | Promotes `--danger` / `--ok` into the token block and its dark override. |
| `console/setup/templates/setup/index.html` | Uses those tokens instead of two hex literals. |
| `modules/vision/tests/_helpers.py` | `FakeComfyUI` grows `/upload/image`; `StubEngine` records what it was asked to build. |
| `docs/adr/0012-image-generation-engine-adapter.md` | New "Input transfer belongs to `submit`" decision; corrected first Consequences bullet. |
| `modules/vision/README.md`, `docs/DEV.md`, `docs/ARCHITECTURE.md` | Doc ripple from the above. |

---

### Task 1: A job is polled through the binding it recorded, not the one bound now

**Files:**
- Modify: `core/inference/gateway.py:95-115` (`get_image_generator`)
- Modify: `modules/vision/models.py:50-55` (add `endpoint` beside `engine`/`model_id`)
- Create: `modules/vision/migrations/0002_generationjob_endpoint.py`
- Modify: `modules/vision/services.py:26` (import), `:132-141` (record endpoint), `:163` and `:192` (build from the job/binding)
- Modify: `modules/vision/tests/_helpers.py:268` (`StubEngine.build_image_generator` records its arguments)
- Test: `modules/vision/tests/test_gateway.py`, `modules/vision/tests/test_services.py`
- Docs: `modules/vision/README.md`

**Interfaces:**
- Produces: `core.inference.gateway.get_image_generator_for(resolved: ResolvedModel) -> ImageGenerator`; `modules.vision.services._generator_for_job(job: GenerationJob) -> ImageGenerator`; `GenerationJob.endpoint: str` (blank for rows written before this task).
- Consumes: `core.inference.bindings.ResolvedModel(engine, model_id, endpoint, embed_dim=None, config={})` and its `.fingerprint` property, both already shipped.

- [ ] **Step 1: Write the failing gateway test**

Append to `modules/vision/tests/test_gateway.py`:

```python
@pytest.mark.django_db
class TestGetImageGeneratorFor:
    """`get_image_generator_for` is the explicit-binding counterpart to
    `get_llm_for`: it builds from a `ResolvedModel` a caller already holds,
    never from the role registry."""

    def test_builds_from_an_explicit_binding_without_resolving_a_role(self):
        from unittest.mock import patch

        from core.inference.bindings import ResolvedModel
        from core.inference.engines import ENGINES
        from core.inference.gateway import get_image_generator_for
        from modules.vision.tests._helpers import StubEngine

        engine = StubEngine()
        resolved = ResolvedModel(
            engine="stubengine",
            model_id="recorded.safetensors",
            endpoint="http://recorded:9999",
            config={"unet": "flux.safetensors"},
        )
        with patch.dict(ENGINES, {"stubengine": engine}), patch(
            "core.inference.gateway.resolve"
        ) as never_resolved:
            generator = get_image_generator_for(resolved)

        assert never_resolved.call_count == 0
        assert generator is engine.generator
        assert engine.built == [("recorded.safetensors", "http://recorded:9999", {"unet": "flux.safetensors"})]

    def test_an_engine_that_cannot_generate_images_says_so(self):
        """`test_engine_that_cannot_generate_images_says_so` (already in this
        module) reaches this `ValueError` through the ROLE path. This reaches
        it through the explicit-binding path, which is where the check now
        lives and whose message this task reworded -- two entry points, one
        guarantee, and the wording is pinned at the entry point that owns it."""
        from unittest.mock import patch

        from core.inference.bindings import ResolvedModel
        from core.inference.engines import ENGINES
        from core.inference.gateway import get_image_generator_for

        class TextOnly:
            name = "textonly"

        resolved = ResolvedModel(engine="textonly", model_id="m", endpoint="http://x:1")
        with patch.dict(ENGINES, {"textonly": TextOnly()}):
            with pytest.raises(ValueError, match="cannot generate images"):
                get_image_generator_for(resolved)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_gateway.py -q`
Expected: FAIL — `ImportError: cannot import name 'get_image_generator_for'`, and `AttributeError: 'StubEngine' object has no attribute 'built'`.

- [ ] **Step 3: Teach `StubEngine` to record what it built**

In `modules/vision/tests/_helpers.py`, replace the `StubEngine.__init__` and `build_image_generator` bodies:

```python
    def __init__(self, generator=None, healthy=True):
        self.generator = generator or StubGenerator()
        self.healthy = healthy
        # Every (model_id, endpoint, config) this engine was asked to build a
        # generator for. Tests assert on it to prove WHICH binding a call
        # used -- the point of the job-recorded-binding fix.
        self.built: list[tuple[str, str, dict]] = []

    def build_image_generator(self, model_id, endpoint, **cfg):
        self.built.append((model_id, endpoint, dict(cfg)))
        return self.generator
```

- [ ] **Step 4: Split the gateway's resolve step from its build step**

In `core/inference/gateway.py`, replace `get_image_generator` (currently lines 95-115) with:

```python
def get_image_generator_for(resolved: ResolvedModel):
    """Return an `ImageGenerator` for an explicit `resolved` binding.

    The counterpart to `get_llm_for`: builds via `resolved`'s engine adapter
    without going through `resolve()` at all, for a caller that already
    holds its own `ResolvedModel`. `modules.vision.services` uses it twice
    -- once with the binding preflight just resolved (so the row and the
    submission can never document different models) and once with the
    binding RECORDED ON A JOB (so polling a job after the role was rebound
    still asks the engine that actually has it). That is D6's promise: a
    job documents the model that ran it, not the one bound now.

    Raises `ValueError` if the engine has no `build_image_generator` --
    reachable only if an operator bound an image-generation role to a
    non-generating engine, since role options are filtered by capability.
    """
    engine = get_engine(resolved.engine)
    builder = getattr(engine, "build_image_generator", None)
    if builder is None:
        raise ValueError(
            f"Engine {resolved.engine!r} cannot generate images "
            f"(no build_image_generator); bind image generation to an engine that can."
        )
    return builder(resolved.model_id, resolved.endpoint, **resolved.config)


def get_image_generator(role: str = VISION_GENERATE_ROLE):
    """Return an `ImageGenerator` for `role` (spec §4.5).

    Same resolve-then-build path as `get_llm()`: the role resolves to an
    engine + model, and THAT engine builds the generator, via
    `get_image_generator_for` -- the gateway constructs nothing itself, so a
    second image engine is a new adapter and nothing else.
    """
    return get_image_generator_for(resolve(role))
```

Also extend the module docstring (`core/inference/gateway.py:17-22`), after the `get_llm` / `get_llm_for` paragraph:

```
Image generation mirrors that pair exactly: `get_image_generator(role)`
answers "what's durably bound right now", `get_image_generator_for(resolved)`
takes a binding the caller already holds — a job's own recorded engine,
model, endpoint, and config.
```

- [ ] **Step 5: Run the gateway tests to verify they pass**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_gateway.py -q`
Expected: PASS (all tests in the file).

- [ ] **Step 6: Write the failing service tests**

Append to `modules/vision/tests/test_services.py`:

```python
@pytest.mark.django_db
class TestJobRecordedBinding:
    """A job records the binding it ran on and is polled through THAT --
    never through a second, independent resolution of the role."""

    def test_submit_records_the_endpoint_it_actually_used(self):
        _bind()
        with _registered(StubEngine()):
            job = services.submit_job("txt2img", dict(RAW))

        assert job.endpoint == "http://stub:9999"
        assert job.engine == "stubengine"
        assert job.model_id == "stub.safetensors"

    def test_submit_never_re_resolves_the_role_through_the_gateway(self):
        _bind()
        with _registered(StubEngine()), patch("core.inference.gateway.resolve") as gateway_resolve:
            job = services.submit_job("txt2img", dict(RAW))

        assert gateway_resolve.call_count == 0
        assert job.status == GenerationJob.Status.QUEUED

    def test_submit_builds_from_the_preflighted_binding(self):
        _bind()
        engine = StubEngine()
        with _registered(engine):
            services.submit_job("txt2img", dict(RAW))

        assert engine.built == [("stub.safetensors", "http://stub:9999", {})]

    def test_refresh_polls_the_binding_on_the_job_not_the_one_bound_now(self):
        _bind()
        submit_engine = StubEngine(StubGenerator(states=[JobStatus(state="running")]))
        with _registered(submit_engine):
            job = services.submit_job("txt2img", dict(RAW))

        # The operator rebinds the role to a different checkpoint at a
        # different address while the job is still in flight.
        RoleBinding.objects.all().delete()
        rebound = ModelConnection.objects.create(
            name="other image model", engine="stubengine", endpoint="http://other:9999",
            model_id="other.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=rebound)

        poll_engine = StubEngine(StubGenerator(states=[JobStatus(state="running")]))
        with _registered(poll_engine):
            refreshed = services.refresh_job(job)

        assert poll_engine.built == [("stub.safetensors", "http://stub:9999", {})]
        assert refreshed.status == GenerationJob.Status.RUNNING
        assert refreshed.unreachable is False

    def test_a_row_without_a_recorded_endpoint_falls_back_to_the_role(self):
        """Rows written before the endpoint column exists carry a blank one;
        they must still poll, through the role binding, rather than firing
        requests at an empty address."""
        _bind()
        job = GenerationJob.objects.create(
            operation="txt2img", params=dict(RAW), seed=42, engine="stubengine",
            model_id="stub.safetensors", model_fingerprint="stubengine:stub.safetensors:None",
            endpoint="", engine_ref="ref-1", status=GenerationJob.Status.QUEUED,
        )
        engine = StubEngine(StubGenerator(states=[JobStatus(state="running")]))
        with _registered(engine):
            refreshed = services.refresh_job(job)

        assert engine.built == [("stub.safetensors", "http://stub:9999", {})]
        assert refreshed.status == GenerationJob.Status.RUNNING
```

- [ ] **Step 7: Run them to verify they fail**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_services.py::TestJobRecordedBinding -q`
Expected: FAIL — `django.core.exceptions.FieldError` / `TypeError: 'endpoint' is an invalid keyword argument`, and `poll_engine.built` showing `http://other:9999`.

- [ ] **Step 8: Add the endpoint column**

In `modules/vision/models.py`, insert after `model_id` (line 51):

```python
    # The address the job was actually submitted to, recorded beside the
    # engine and model so the D6 snapshot is COMPLETE: `refresh_job` polls
    # this, not whatever the role points at now. Blank on rows written
    # before this column existed -- those fall back to the role binding.
    endpoint = models.CharField(max_length=255, blank=True)
```

Create `modules/vision/migrations/0002_generationjob_endpoint.py`:

```python
"""Record the endpoint a job was submitted to (D6's binding snapshot).

`engine`, `model_id`, `model_fingerprint`, and `model_config` already
described the binding; the ADDRESS was missing, which is what forced
`refresh_job` to re-resolve the role and risk polling a rebound engine's
queue for a job it never had.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("vision", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="generationjob",
            name="endpoint",
            field=models.CharField(blank=True, default="", max_length=255),
            preserve_default=False,
        ),
    ]
```

- [ ] **Step 9: Use the recorded binding in the service layer**

In `modules/vision/services.py`, change the gateway import (line 26) to:

```python
from core.inference.gateway import get_image_generator, get_image_generator_for
```

Add this helper directly above `submit_job`:

```python
def _generator_for_job(job: GenerationJob):
    """An `ImageGenerator` for the binding recorded ON `job`.

    Polling must ask the engine that actually HAS the job. Re-resolving the
    role instead would, after a rebind, ask a different queue about a
    `prompt_id` it never saw -- which answers `lost`, and the operator is
    told the engine forgot a job that is running fine.

    A row from before the `endpoint` column existed carries a blank one and
    has no address to poll; those fall back to the role binding, which is
    the same behaviour they had when they were written.
    """
    if not job.endpoint:
        return get_image_generator()
    return get_image_generator_for(
        ResolvedModel(
            engine=job.engine,
            model_id=job.model_id,
            endpoint=job.endpoint,
            config=dict(job.model_config or {}),
        )
    )
```

In `submit_job`, add `endpoint=resolved.endpoint,` to the `GenerationJob.objects.create(...)` call, directly after `model_id=resolved.model_id,`:

```python
    job = GenerationJob.objects.create(
        operation=operation.key,
        params=params,
        seed=params["seed"],
        engine=resolved.engine,
        model_id=resolved.model_id,
        endpoint=resolved.endpoint,
        model_fingerprint=resolved.fingerprint,
        model_config=dict(resolved.config or {}),
        status=GenerationJob.Status.QUEUED,
    )
```

Replace the submit call (line 163) so it builds from the binding preflight already resolved:

```python
        engine_ref, payload = get_image_generator_for(resolved).submit(request)
```

Replace the refresh call (line 192):

```python
        generator = _generator_for_job(job)
```

Finally, extend `submit_job`'s docstring after its "Order matters" paragraph:

```
    The generator is built from the binding PREFLIGHT resolved, not from a
    second `resolve()` call: the row four lines up already documents that
    binding, and a rebind between the two resolutions would leave the row
    describing a model that did not run the job.
```

- [ ] **Step 10: Run the service tests to verify they pass**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_services.py -q`
Expected: PASS.

- [ ] **Step 11: Update the module README**

In `modules/vision/README.md`, in the service-layer section, after the `submit_job` bullet (around line 85), add:

```markdown
- **The binding travels with the job.** `submit_job` records `engine`, `model_id`,
  `endpoint`, `model_fingerprint`, and `model_config` on the row and then submits through
  *that* binding (`core.inference.gateway.get_image_generator_for`), never through a second
  `resolve()`. `refresh_job` polls through the same recorded binding
  (`services._generator_for_job`), so rebinding `vision.generate` mid-flight never points a
  poll at another engine's queue — it would answer `lost` for a job that is running fine.
  Only `preflight()` reads the *current* binding, because that is the question it asks.
```

- [ ] **Step 12: Run the full suite in both orders**

Run: `<repo>/.venv/bin/pytest -q`
Expected: PASS, 955 passed, 1 skipped (948 + 7 new: 2 in `test_gateway.py`, 5 in `test_services.py`).

Run: `<repo>/.venv/bin/pytest modules console scripts -q`
Expected: PASS, same counts.

- [ ] **Step 13: Commit**

```bash
git add core/inference/gateway.py modules/vision/models.py modules/vision/migrations/0002_generationjob_endpoint.py modules/vision/services.py modules/vision/tests/_helpers.py modules/vision/tests/test_gateway.py modules/vision/tests/test_services.py modules/vision/README.md
git commit -m "fix(vision): submit and poll through the job's own recorded binding

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 2: A file parameter never enters the params JSONField

**Files:**
- Modify: `core/inference/operations.py:89-94` (add `Operation.file_param_keys` beside `Operation.param`), `:133-146` (add `_file_reference` beside `_coerce_number`), `:202-216` (the `file` branch)
- Modify: `modules/vision/forms.py:99-102` (`initial` skips file fields)
- Modify: `modules/vision/services.py:143-152` (only declared file params reach disk)
- Modify: `modules/vision/templates/vision/create.html:53` (`enctype`)
- Test: `modules/vision/tests/test_operations.py`, `modules/vision/tests/test_forms.py`, `modules/vision/tests/test_services.py`
- Docs: `modules/vision/README.md`

**Interfaces:**
- Consumes: `GenerationJob.endpoint` from Task 1 (`submit_job` writes it; nothing here changes that).
- Produces: `core.inference.operations.Operation.file_param_keys() -> frozenset[str]`; `core.inference.operations._file_reference(supplied) -> str` (module-private); the contract that `validate_params` output is always JSON-serialisable, which Task 3's engine layer and Task 4's facts line both rely on. A `"file"` param's value in `GenerationJob.params` is the uploaded file's **basename string**; its bytes live at the path on the matching `JobInput` row and in `GenerationRequest.inputs`.

- [ ] **Step 1: Write the failing schema tests**

Append to `modules/vision/tests/test_operations.py`:

```python
class _Upload:
    """The shape a Django `UploadedFile` presents to `validate_params`: a
    non-string object carrying a `.name`. Deliberately NOT JSON
    serialisable -- that is the whole bug this fixes."""

    def __init__(self, name: str):
        self.name = name


IMG2IMG = Operation(
    key="img2img",
    label="Image to image",
    capability="image-generation",
    output_media="image/png",
    params=(
        Param("prompt", "text", "Prompt", default="", required=True),
        Param("init_image", "file", "Init image", accept="image/*", required=True),
        Param("mask", "file", "Mask", accept="image/*"),
        Param("denoise", "float", "Denoise", default=0.6, min=0, max=1, step=0.05),
        Param("seed", "seed", "Seed", default=None),
    ),
)


class TestFileParams:
    """`GenerationJob.params` is a JSONField; a `"file"` param must resolve
    to something that can live in one."""

    def test_a_file_param_records_a_basename_not_the_upload(self):
        clean = validate_params(IMG2IMG, {"prompt": "a lighthouse", "init_image": _Upload("beach.png")})

        assert clean["init_image"] == "beach.png"
        json.dumps(clean)  # would raise TypeError on an UploadedFile

    def test_a_windows_style_upload_name_keeps_only_its_basename(self):
        clean = validate_params(
            IMG2IMG, {"prompt": "p", "init_image": _Upload(r"C:\Users\op\my photos\beach.png")}
        )

        assert clean["init_image"] == "beach.png"

    def test_a_plain_string_file_reference_survives_a_round_trip(self):
        """A caller re-submitting a job's stored params (the chatbot tool,
        `?reuse=`) hands back the basename string, not an upload."""
        clean = validate_params(IMG2IMG, {"prompt": "p", "init_image": "beach.png"})

        assert clean["init_image"] == "beach.png"

    def test_file_param_keys_names_every_file_param_and_nothing_else(self):
        """One definition of "which params are files", shared by the form
        layer (never prefill one) and the service layer (only these reach
        the store) -- so the two can never drift."""
        assert IMG2IMG.file_param_keys() == frozenset({"init_image", "mask"})
        assert TXT2IMG.file_param_keys() == frozenset()

    def test_a_required_file_param_that_is_absent_is_an_error(self):
        with pytest.raises(ParamError) as excinfo:
            validate_params(IMG2IMG, {"prompt": "p"})

        assert excinfo.value.errors["init_image"] == "Init image is required."

    def test_an_optional_file_param_that_is_absent_takes_its_default(self):
        clean = validate_params(IMG2IMG, {"prompt": "p", "init_image": _Upload("beach.png")})

        assert clean["mask"] is None
```

Add `import json` to that module's imports if it is not already there, and make sure `Operation`, `Param`, `ParamError`, `TXT2IMG`, and `validate_params` are imported (they already are, for the existing tests).

- [ ] **Step 2: Run them to verify they fail**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_operations.py::TestFileParams -q`
Expected: FAIL — `test_a_file_param_records_a_basename_not_the_upload` errors with `AssertionError` (`clean["init_image"]` is the `_Upload` object) and `TypeError: Object of type _Upload is not JSON serializable`; `test_file_param_keys_names_every_file_param_and_nothing_else` errors with `AttributeError: 'Operation' object has no attribute 'file_param_keys'`.

- [ ] **Step 3: Add the file branch to `validate_params`**

In `core/inference/operations.py`, add this method to `Operation`, directly below `param()` (after line 94):

```python
    def file_param_keys(self) -> frozenset[str]:
        """Every `"file"` param's key.

        One definition of "which of my params are files", because two layers
        need exactly that set for opposite reasons: the form layer must
        never prefill one (a browser cannot re-send a file from a name) and
        the service layer must let only these reach the managed store (an
        undeclared upload has no meaning). Written twice, they drift; written
        here beside `param()`, they cannot.
        """
        return frozenset(param.key for param in self.params if param.kind == "file")
```

Then add this function directly below `_coerce_number` (after line 146):

```python
def _file_reference(supplied: Any) -> str:
    """The JSON-safe value a `"file"` param records in a job's `params`: the
    uploaded file's BASENAME, never the upload object.

    `GenerationJob.params` is a JSONField, so a Django `UploadedFile` in
    there is a `TypeError` at save time -- and it would be the first line of
    code img2img executes. The bytes are not lost: `submit_job` writes them
    into the job's own `inputs/` directory, records a `JobInput` row that
    owns the path, and puts that path in `GenerationRequest.inputs`. What
    stays in `params` is a LABEL, so an operator reading a job card can see
    which file was run.

    Only the basename survives, and a backslash counts as a separator: a
    browser on Windows may report a full local path, and none of it is ours
    to keep.
    """
    name = getattr(supplied, "name", None)
    if name is None:
        name = supplied
    return str(name).replace("\\", "/").rsplit("/", 1)[-1]
```

Then, in `validate_params`, insert this branch immediately after the `"choice"` branch (after line 214, before the final fallthrough assignment):

```python
        if param.kind == "file":
            clean[param.key] = _file_reference(supplied)
            continue
```

And extend `validate_params`'s docstring bullet list, after the `seed` bullet:

```
    - reduces a `file` param to the upload's basename (`_file_reference`),
      so what lands in the JSONField is a label and never an upload object;
```

- [ ] **Step 4: Run the schema tests to verify they pass**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_operations.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing form test**

Append to `modules/vision/tests/test_forms.py`:

```python
class TestFileFieldsAreNeverPrefilled:
    def test_reuse_initial_prefills_text_but_not_a_file_field(self):
        """`?reuse=<job>` hands back a previous job's params, in which a
        file param is a basename STRING. Django would render that on a
        FileField as "Currently: beach.png" with a keep-it checkbox -- a
        file the browser cannot re-send and the engine never receives. The
        operator must pick the file again, so the field starts empty."""
        operation = Operation(
            "img2img", "Image to image", "image-generation",
            (
                Param("prompt", "text", "Prompt", default="", required=True),
                Param("init_image", "file", "Init image", accept="image/*", required=True),
            ),
            "image/png",
        )
        form = build_form(operation, {}, initial={"prompt": "a lighthouse", "init_image": "beach.png"})

        assert form.fields["prompt"].initial == "a lighthouse"
        assert form.fields["init_image"].initial is None
```

- [ ] **Step 6: Run it to verify it fails**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_forms.py::TestFileFieldsAreNeverPrefilled -q`
Expected: FAIL — `assert 'beach.png' is None`.

- [ ] **Step 7: Skip file fields when applying `initial`**

In `modules/vision/forms.py`, replace the `initial` loop inside `build_form` (lines 99-102) with:

```python
    if initial:
        # A `"file"` param's stored value is a basename string (see
        # `operations._file_reference`), and a browser cannot re-send a file
        # from a name. Prefilling one would render "Currently: beach.png"
        # for a file the engine will never receive, so file fields always
        # start empty and the operator picks again.
        file_keys = operation.file_param_keys()
        for key, value in initial.items():
            if key in fields and key not in file_keys:
                fields[key].initial = value
```

- [ ] **Step 8: Run the form tests to verify they pass**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_forms.py -q`
Expected: PASS.

- [ ] **Step 9: Write the failing service test for the whole upload path**

This is the `submit_job(files=...)` gap the audit records as untested (B13). Append to `modules/vision/tests/test_services.py`:

```python
@pytest.mark.django_db
class TestFileInputs:
    """The write half of the file-param path: uploads land in the job's own
    directory, `params` keeps only their names, and the engine seam receives
    the paths."""

    OPERATION = Operation(
        key="img2img",
        label="Image to image",
        capability="image-generation",
        output_media="image/png",
        params=(
            Param("prompt", "text", "Prompt", default="", required=True),
            Param("init_image", "file", "Init image", accept="image/*", required=True),
            Param("seed", "seed", "Seed", default=None),
        ),
    )

    def _registered_operation(self):
        return patch.dict(operations._OPERATIONS, {"img2img": self.OPERATION})

    def test_the_upload_is_stored_and_the_params_keep_only_its_name(self, tmp_path):
        _bind()
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        generator = StubGenerator()
        with self._registered_operation(), _registered(StubEngine(generator)), override_settings(
            GENERATED_DIR=tmp_path
        ):
            job = services.submit_job(
                "img2img",
                {"prompt": "a lighthouse", "init_image": upload, "seed": "42"},
                files={"init_image": upload},
            )

        assert job.params["init_image"] == "beach.png"
        json.dumps(job.params)

        stored = JobInput.objects.get(job=job)
        assert stored.param_key == "init_image"
        assert stored.media_type == "image/png"
        assert Path(stored.path).read_bytes() == PNG
        assert Path(stored.path).parent == tmp_path / str(job.id) / "inputs"

    def test_the_engine_seam_receives_the_stored_path(self, tmp_path):
        _bind()
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        generator = StubGenerator()
        with self._registered_operation(), _registered(StubEngine(generator)), override_settings(
            GENERATED_DIR=tmp_path
        ):
            services.submit_job(
                "img2img",
                {"prompt": "a lighthouse", "init_image": upload, "seed": "42"},
                files={"init_image": upload},
            )

        request = generator.submitted[0]
        assert set(request.inputs) == {"init_image"}
        assert Path(request.inputs["init_image"]).read_bytes() == PNG

    def test_an_upload_for_a_param_the_operation_does_not_declare_never_lands(self, tmp_path):
        """`request.FILES` is whatever was posted. Only files answering a
        declared `"file"` param may touch the managed store."""
        _bind()
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        rogue = SimpleUploadedFile("payload.bin", b"\x00\x01", content_type="application/octet-stream")
        generator = StubGenerator()
        with self._registered_operation(), _registered(StubEngine(generator)), override_settings(
            GENERATED_DIR=tmp_path
        ):
            job = services.submit_job(
                "img2img",
                {"prompt": "a lighthouse", "init_image": upload, "seed": "42"},
                files={"init_image": upload, "rogue": rogue},
            )

        assert [row.param_key for row in job.inputs.all()] == ["init_image"]
        assert sorted(p.name for p in (tmp_path / str(job.id) / "inputs").iterdir()) == [
            "init_image-beach.png"
        ]
```

Add to that module's imports:

```python
import json
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile

from core.inference import operations
from core.inference.operations import Operation, Param
from modules.vision.models import JobInput
```

(`GeneratedOutput`, `GenerationJob`, `override_settings`, and `patch` are already imported in this module.)

- [ ] **Step 10: Run them to verify they fail**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_services.py::TestFileInputs -q`
Expected: FAIL — `test_an_upload_for_a_param_the_operation_does_not_declare_never_lands` finds two files in `inputs/` and two `JobInput` rows. (The first two tests pass already, because Step 3 made `params` JSON-safe — they are the regression pins the audit found missing.)

- [ ] **Step 11: Filter uploads to declared file params**

In `modules/vision/services.py`, replace the input-storing loop in `submit_job` (lines 143-152) with:

```python
    # Only files answering a `"file"` param the operation actually declares
    # reach disk: `request.FILES` carries whatever was posted, and an
    # undeclared upload has no meaning, no `JobInput` row, and no business
    # in the managed store.
    file_params = operation.file_param_keys()
    inputs: dict[str, Path] = {}
    for param_key, uploaded in (files or {}).items():
        if param_key not in file_params:
            continue
        path = store.store_input(job.id, param_key, uploaded)
        JobInput.objects.create(
            job=job,
            param_key=param_key,
            path=path,
            media_type=getattr(uploaded, "content_type", "") or "",
        )
        inputs[param_key] = Path(path)
```

- [ ] **Step 12: Run the service tests to verify they pass**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_services.py -q`
Expected: PASS.

- [ ] **Step 13: Let the form actually send a file**

In `modules/vision/templates/vision/create.html`, line 53, add the encoding a file input needs:

```html
<form class="card gen-form" method="post" enctype="multipart/form-data" action="{% url 'vision-generate' %}" id="generate-form">
```

The page's own submit handler already posts `new FormData(form)`, which carries files unchanged, so the JS path needs no edit.

- [ ] **Step 14: Document the file-param contract**

In `modules/vision/README.md`, in "The operation registry, and how to add an operation", add a fifth numbered step:

```markdown
5. A `"file"` param needs nothing else from this module. `validate_params` reduces the
   upload to its basename so `GenerationJob.params` (a JSONField) stays JSON-safe;
   `submit_job` writes the bytes into `<GENERATED_DIR>/<job>/inputs/`, records a `JobInput`
   row, and puts the path in `GenerationRequest.inputs`; the engine adapter moves it from
   there (see `ImageGenerator.submit`). The create form already posts
   `enctype="multipart/form-data"`, and `?reuse=` never prefills a file field — a browser
   cannot re-send a file from a name, so the operator picks it again.
```

- [ ] **Step 15: Run the full suite in both orders**

Run: `<repo>/.venv/bin/pytest -q`
Expected: PASS, 965 passed, 1 skipped (955 + 10).

Run: `<repo>/.venv/bin/pytest modules console scripts -q`
Expected: PASS, same counts.

- [ ] **Step 16: Commit**

```bash
git add core/inference/operations.py modules/vision/forms.py modules/vision/services.py modules/vision/templates/vision/create.html modules/vision/tests/test_operations.py modules/vision/tests/test_forms.py modules/vision/tests/test_services.py modules/vision/README.md
git commit -m "fix(vision): a file param records a basename, never the upload object

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 3: The engine adapter owns input transfer

**The decision, argued from the spec's grammar.** `ImageGenerator.submit` transfers `request.inputs` itself; there is no platform-orchestrated `upload()` member. Three lines of justification:

1. The handle a transfer yields is **engine vocabulary** — ComfyUI answers `POST /upload/image` with `{"name", "subfolder", "type"}` and its nodes address the file as `"<subfolder>/<name>"` — and spec §3 forbids that vocabulary from crossing the adapter line; a platform-level `upload()` would make `modules/vision/services.py` hold and forward it.
2. D5 promises a mode is one `Operation` plus one template; a two-phase upload-then-submit puts a second engine call, its own error path, and its own retry semantics into the service layer for **every** file-taking operation.
3. The two failure modes ADR 0012 already rules on cover a transfer with no new grammar: a refusal is `GenerationRejected` (final, the engine's own words), a transport failure propagates and is retried — and the platform keeps the authoritative copy of every input (spec §5), so a resubmit re-transfers with nothing lost.

**What refresh does when the engine's copy of the upload is gone:** nothing special, by construction. `refresh_job` uses exactly one handle — `job.engine_ref`, the `prompt_id`. An engine that lost its uploaded input also lost its queue entry and its history, so `status()` reports `lost`, and the service layer's existing `LOST_MESSAGE` tells the operator to resubmit. A resubmit re-uploads from `<GENERATED_DIR>/<job>/inputs/`, which the platform never deleted.

**Files:**
- Modify: `core/inference/engines/base.py:132-140` (`ImageGenerator.submit` contract)
- Modify: `core/inference/engines/comfyui.py:19-27` (imports), `:36-56` (constants), `:249-252` (`supported_operations`), `:269-294` (add `_upload_rejection`), `:354-371` (`submit`, plus the new `_upload_inputs`)
- Modify: `core/inference/engines/comfyui_workflows/__init__.py:1-25` (docstring + `Template` type)
- Modify: `core/inference/engines/comfyui_workflows/txt2img.py:9-21` (signature + docstring)
- Modify: `modules/vision/tests/_helpers.py:32-50` (`_Response.text`), `:60-92` (upload fields), `:148-173` (`post`)
- Test: `modules/vision/tests/test_comfyui_generator.py`, `modules/vision/tests/test_comfyui_workflows.py:30` and `:96`
- Docs: `modules/vision/README.md`

**Interfaces:**
- Consumes: `GenerationRequest.inputs: dict[str, Path]` and `GenerationRejected` from `core.inference.engines.base`. `_upload_inputs` transfers every entry of `request.inputs` without re-checking it: Task 2 already filtered it through `Operation.file_param_keys()`, so an undeclared upload never reaches this layer. That is a real coupling — if the filter is ever removed, this method starts uploading anything posted.
- Produces: `Template = Callable[[GenerationRequest, str, dict, dict], dict]` — every graph template now takes `(request, model_id, config, inputs)` where `inputs` maps a file param key to the engine-side reference string. `ComfyUIGenerator._upload_inputs(request) -> dict[str, str]`.

- [ ] **Step 1: Write the failing generator tests**

Append to `modules/vision/tests/test_comfyui_generator.py`:

```python
class TestInputTransfer:
    """`submit` moves every file input to the engine before building the
    graph: a path inside the platform's container means nothing to a
    ComfyUI running natively on the host (ADR 0012 D1)."""

    @staticmethod
    def _template(request, model_id, config, inputs):
        """A stand-in img2img template: the only thing under test is that
        the engine-side reference reaches the graph."""
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": inputs["init_image"]}},
            "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": request.client_ref, "images": ["1", 0]}},
        }

    def _img2img_request(self, tmp_path, client_ref="job-uuid"):
        source = tmp_path / "beach.png"
        source.write_bytes(b"PNGBYTES")
        return GenerationRequest(
            operation="img2img",
            model_id="sdxl.safetensors",
            params={"prompt": "a lighthouse", "denoise": 0.6},
            inputs={"init_image": source},
            client_ref=client_ref,
        )

    def _registered_template(self):
        from core.inference.engines import comfyui_workflows

        return patch.dict(comfyui_workflows._TEMPLATES, {"img2img": self._template})

    def test_the_file_is_uploaded_and_its_engine_reference_reaches_the_graph(self, tmp_path):
        fake = FakeComfyUI()
        request = self._img2img_request(tmp_path)
        with self._registered_template(), patch(
            "core.inference.engines.comfyui.httpx.post", fake.post
        ):
            _generator().submit(request)

        assert len(fake.uploads) == 1
        upload = fake.uploads[0]
        assert upload["field"] == "image"
        assert upload["filename"] == "beach.png"
        assert upload["media_type"] == "image/png"
        assert upload["content"] == b"PNGBYTES"
        assert upload["data"] == {"subfolder": "job-uuid", "type": "input", "overwrite": "true"}
        assert fake.submitted[0]["prompt"]["1"]["inputs"]["image"] == "job-uuid/beach.png"

    def test_an_engine_reported_name_wins_over_the_local_one(self, tmp_path):
        """ComfyUI renames on collision. The reference must be ITS name."""
        fake = FakeComfyUI(upload_name="beach (1).png")
        with self._registered_template(), patch(
            "core.inference.engines.comfyui.httpx.post", fake.post
        ):
            _generator().submit(self._img2img_request(tmp_path))

        assert fake.submitted[0]["prompt"]["1"]["inputs"]["image"] == "job-uuid/beach (1).png"

    def test_an_engine_that_reports_no_subfolder_yields_a_bare_name(self, tmp_path):
        fake = FakeComfyUI(upload_subfolder="")
        with self._registered_template(), patch(
            "core.inference.engines.comfyui.httpx.post", fake.post
        ):
            _generator().submit(self._img2img_request(tmp_path))

        assert fake.submitted[0]["prompt"]["1"]["inputs"]["image"] == "beach.png"

    def test_a_refused_upload_is_a_rejection_carrying_comfyui_own_words(self, tmp_path):
        fake = FakeComfyUI(upload_status=400, upload_text="image file is required")
        with self._registered_template(), patch(
            "core.inference.engines.comfyui.httpx.post", fake.post
        ):
            with pytest.raises(GenerationRejected, match="image file is required"):
                _generator().submit(self._img2img_request(tmp_path))

        assert fake.submitted == []

    def test_a_transport_failure_during_upload_propagates_untouched(self, tmp_path):
        """Not a rejection: the service layer must leave the job alone and
        retry, per ADR 0012's two-failure-mode ruling."""

        def refuse(*args, **kwargs):
            raise httpx.ConnectError("connection refused")

        with self._registered_template(), patch(
            "core.inference.engines.comfyui.httpx.post", refuse
        ):
            with pytest.raises(httpx.ConnectError):
                _generator().submit(self._img2img_request(tmp_path))

    def test_txt2img_uploads_nothing(self):
        fake = FakeComfyUI()
        with patch("core.inference.engines.comfyui.httpx.post", fake.post):
            _generator().submit(_request())

        assert fake.uploads == []
        assert len(fake.submitted) == 1
```

Add to that module's imports:

```python
import httpx

from core.inference.engines.base import GenerationRejected
```

(`patch`, `pytest`, `FakeComfyUI`, `GenerationRequest`, `_generator`, and `_request` are already there; `GenerationRejected` may already be imported for the existing rejection tests — do not import it twice.)

- [ ] **Step 2: Run them to verify they fail**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_comfyui_generator.py::TestInputTransfer -q`
Expected: FAIL — `TypeError: FakeComfyUI.__init__() got an unexpected keyword argument 'upload_name'`, and once that is fixed, `TypeError: _template() missing 1 required positional argument: 'inputs'`.

- [ ] **Step 3: Teach the HTTP double about `/upload/image`**

In `modules/vision/tests/_helpers.py`, add a `text` field to `_Response` (after `content`):

```python
    text: str = ""
```

Add these fields to `FakeComfyUI`, after `submitted` / `view_calls`:

```python
    upload_status: int = 200
    upload_text: str = ""
    upload_name: str | None = None
    upload_subfolder: str | None = None
    uploads: list = field(default_factory=list)
```

Extend the `FakeComfyUI` docstring's endpoint list with:

```
    - `upload_status`/`upload_text`/`upload_name`/`upload_subfolder` ->
      `POST /upload/image` (recorded in `uploads`)
```

Replace `FakeComfyUI.post` with:

```python
    def post(
        self,
        url: str,
        json: dict | None = None,
        files: dict | None = None,
        data: dict | None = None,
        timeout: float | None = None,
    ) -> _Response:
        if url.endswith("/upload/image"):
            field_name, (filename, handle, media_type) = next(iter((files or {}).items()))
            self.uploads.append(
                {
                    "field": field_name,
                    "filename": filename,
                    "media_type": media_type,
                    "content": handle.read(),
                    "data": dict(data or {}),
                }
            )
            if self.upload_status >= 400:
                return _Response(status_code=self.upload_status, text=self.upload_text)
            subfolder = (
                (data or {}).get("subfolder", "")
                if self.upload_subfolder is None
                else self.upload_subfolder
            )
            return _Response(
                payload={
                    "name": self.upload_name or filename,
                    "subfolder": subfolder,
                    "type": "input",
                }
            )

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
```

- [ ] **Step 4: State the transfer contract on the protocol**

In `core/inference/engines/base.py`, replace `ImageGenerator.submit`'s docstring (lines 133-139) with:

```python
        """Submit `request` and return `(engine_ref, payload)`.

        `engine_ref` is the engine's own job handle; `payload` is the EXACT
        submission, stored verbatim on the job row (D6) so a result is
        reproducible and exportable. Raises `GenerationRejected` when the
        engine refuses the submission.

        **The implementation owns input TRANSFER.** `request.inputs` maps a
        `"file"` param's key to a path in the PLATFORM's managed store
        (`<GENERATED_DIR>/<job>/inputs/`) -- a path on the platform's
        filesystem, which the engine may not share at all: ADR 0012 D1 puts
        the engine on the host while the platform runs in the `web`
        container. Making those bytes reachable is engine knowledge (an
        upload endpoint, an inline encoding, a shared volume), so it happens
        HERE, inside `submit`, and never as a separate platform-orchestrated
        step -- the handle a transfer yields is engine vocabulary that must
        not cross this line (spec §3).

        A transfer the engine REFUSES (unsupported type, too large) raises
        `GenerationRejected` like any other refusal. A transfer that fails
        in TRANSPORT raises whatever the HTTP layer raised, which the
        service layer treats as transient. The platform keeps the
        authoritative copy of every input, so a resubmit re-transfers with
        nothing lost.
        """
```

- [ ] **Step 5: Widen the template signature**

In `core/inference/engines/comfyui_workflows/__init__.py`, replace the `Template` alias and its comment (lines 20-21) with:

```python
# operation key -> (request, model_id, connection config, engine-side input
# references) -> graph.
#
# The fourth argument is what `ComfyUIGenerator._upload_inputs` got back for
# each of `request.inputs`: `{param key: the string a ComfyUI node's image
# input takes}`. It is empty for an operation with no file params.
Template = Callable[[GenerationRequest, str, dict, dict], dict]
```

Add an exported accessor to the same file, beside `get_template`:

```python
def template_keys() -> tuple[str, ...]:
    """Every operation key this engine has a template for, in registration
    order. `ComfyUIEngine.supported_operations` returns this rather than a
    hand-maintained tuple, so adding a template really is the only edit --
    which is what this package's docstring has always claimed."""
    return tuple(_TEMPLATES)
```

And rewrite the module docstring's "Adding a mode" paragraph as:

```
Adding a mode (img2img, inpaint, ControlNet, upscale) is one module here
plus one entry in `_TEMPLATES` plus one `Operation` definition -- never a
change to the page or the service layer, and none to the engine adapter
either: `ComfyUIEngine.supported_operations` reads `template_keys()`.

A template that takes a file input reads its engine-side reference out of
the fourth argument (e.g. `inputs["init_image"]`) and never touches a
filesystem path -- the file has already been transferred by the time a
template runs.
```

(The previous wording claimed "never a change to ... the engine adapter itself" while `supported_operations` was a hardcoded `("txt2img",)`. Deriving it makes the claim true instead of striking it.)

In `core/inference/engines/comfyui.py`, add `template_keys` to the existing workflow import (line 26) and replace `supported_operations` (lines 249-252) with:

```python
    def supported_operations(self, model_id: str, endpoint: str) -> tuple[str, ...]:
        """Operations this adapter has a graph template for, read straight
        from the template registry -- so adding img2img is one template and
        nothing else (spec §9)."""
        return template_keys()
```

Append this test to `modules/vision/tests/test_comfyui_engine.py` (the existing
`assert ComfyUIEngine().supported_operations(...) == ("txt2img",)` at line 134 keeps passing unchanged):

```python
    def test_supported_operations_follows_the_template_registry(self):
        """A registered template is reported with no edit to the adapter."""
        from core.inference.engines import comfyui_workflows

        def _stub(request, model_id, config, inputs):
            return {}

        with patch.dict(comfyui_workflows._TEMPLATES, {"img2img": _stub}):
            assert "img2img" in ComfyUIEngine().supported_operations("sdxl.safetensors", ENDPOINT)
```

In `core/inference/engines/comfyui_workflows/txt2img.py`, change the signature and docstring:

```python
def build(request: GenerationRequest, model_id: str, config: dict, inputs: dict) -> dict:
    """The API-format graph for one txt2img request.

    `model_id` is ComfyUI's own opaque checkpoint string (possibly with a
    Windows subfolder separator) and is passed through untouched.

    `config` is the bound connection's `ModelConnection.config` (D8),
    reserved for multi-file families -- a Flux/SD3 template will read
    `config["unet"]`, `config["clip"]`, `config["vae"]`. A single-file SD
    checkpoint needs none of it, so this template deliberately ignores it
    rather than leaking unknown keys into the graph.

    `inputs` maps a file param's key to the reference ComfyUI gave the
    uploaded file (what a `LoadImage` node's `image` input takes). txt2img
    has no file params, so this template ignores it too -- an img2img
    template will read `inputs["init_image"]`.
    """
```

The body is unchanged.

Update both call sites in `modules/vision/tests/test_comfyui_workflows.py`:

- line 30: `return get_template("txt2img")(request, request.model_id, config or {}, {})`
- line 96: `graph = get_template("txt2img")(request, request.model_id, {}, {})`

No new test accompanies the signature widening: an assertion that a template produces the same graph whether or not it is handed an argument it never reads can never fail, and `TestInputTransfer::test_txt2img_uploads_nothing` (Step 1) already pins the behaviour that matters.

- [ ] **Step 6: Upload before building the graph**

In `core/inference/engines/comfyui.py`, add `from pathlib import Path` to the imports (after `import mimetypes`).

Add these constants after `_CHECKPOINT_NODE` (line 55):

```python
# ComfyUI's own upload endpoint and the multipart field it reads. An
# uploaded file lands in ComfyUI's `input/` folder and is addressed
# afterwards by the name ComfyUI reports back -- the ONLY handle a
# LoadImage-style node accepts. This is exactly the engine vocabulary that
# must not leave this module (spec §3), which is why the upload happens
# inside `submit` rather than being orchestrated by the platform.
_UPLOAD_PATH = "/upload/image"
_UPLOAD_FIELD = "image"
```

Add this function next to `_rejection_message` (after line 293):

```python
def _upload_rejection(response) -> str:
    """ComfyUI's own words for a refused upload.

    `/upload/image` answers a rejection with a plain-text body far more
    often than the JSON `/prompt` uses, so this reads `.text` and falls back
    to the bare status code -- never a rewritten guess."""
    text = (getattr(response, "text", "") or "").strip()
    return text or f"HTTP {response.status_code}"
```

Add this method to `ComfyUIGenerator`, directly above `submit`:

```python
    def _upload_inputs(self, request: GenerationRequest) -> dict[str, str]:
        """Transfer every file input to ComfyUI; return `{param key: engine
        reference}`.

        ComfyUI answers `POST /upload/image` with
        `{"name": ..., "subfolder": ..., "type": "input"}`, and a node's
        image input takes `"<subfolder>/<name>"` (or just `"<name>"` when
        there is no subfolder), so that string IS the reference.

        Each job uploads into a subfolder named after its own `client_ref`,
        with `overwrite=true`: two jobs sending files of the same name can
        never collide, while resubmitting the SAME job is idempotent.

        A refusal is a `GenerationRejected` carrying ComfyUI's own words; a
        transport failure propagates untouched, so the service layer retries
        instead of failing a job the engine never saw.
        """
        references: dict[str, str] = {}
        for param_key, path in (request.inputs or {}).items():
            source = Path(path)
            with open(source, "rb") as handle:
                response = httpx.post(
                    f"{self.endpoint}{_UPLOAD_PATH}",
                    files={_UPLOAD_FIELD: (source.name, handle, _media_type(source.name))},
                    data={"subfolder": request.client_ref, "type": "input", "overwrite": "true"},
                    timeout=self.timeout,
                )
            if response.status_code >= 400:
                raise GenerationRejected(
                    f"ComfyUI refused the upload for {param_key!r}: {_upload_rejection(response)}"
                )
            body = response.json() or {}
            name = str(body.get("name") or source.name)
            subfolder = str(body.get("subfolder") or "")
            references[param_key] = f"{subfolder}/{name}" if subfolder else name
        return references
```

Replace the first two lines of `submit`'s body (lines 363-364) with these three:

```python
        inputs = self._upload_inputs(request)
        graph = get_template(request.operation)(request, self.model_id, self.config, inputs)
        payload = {"prompt": graph, "client_id": request.client_ref}
```

And extend `submit`'s docstring with a closing paragraph:

```
        Every file input is transferred FIRST (`_upload_inputs`) so the graph
        can reference ComfyUI's own name for it. The stored `payload` stays
        the EXACT `/prompt` body and gains no upload bookkeeping: the
        references are already inside the graph, which is what makes the
        submission reproducible.
```

- [ ] **Step 7: Run the engine tests to verify they pass**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_comfyui_generator.py modules/vision/tests/test_comfyui_workflows.py -q`
Expected: PASS.

- [ ] **Step 8: Document the seam**

In `modules/vision/README.md`, append to the "operation registry" section's step 2:

```markdown
   A template that takes a file input receives the engine-side reference as its fourth
   argument (`inputs["init_image"]`), never a filesystem path: `ImageGenerator.submit` has
   already transferred the file — for ComfyUI, `POST /upload/image` into a subfolder named
   after the job. The platform keeps the authoritative copy in
   `<GENERATED_DIR>/<job>/inputs/`, so an engine that was restarted and lost its copy
   simply reports the job `lost` and a resubmit re-transfers it.
```

- [ ] **Step 9: Run the full suite in both orders**

Run: `<repo>/.venv/bin/pytest -q`
Expected: PASS, 972 passed, 1 skipped (965 + 7).

Run: `<repo>/.venv/bin/pytest modules console scripts -q`
Expected: PASS, same counts.

- [ ] **Step 10: Commit**

```bash
git add core/inference/engines/base.py core/inference/engines/comfyui.py core/inference/engines/comfyui_workflows/__init__.py core/inference/engines/comfyui_workflows/txt2img.py modules/vision/tests/_helpers.py modules/vision/tests/test_comfyui_generator.py modules/vision/tests/test_comfyui_engine.py modules/vision/tests/test_comfyui_workflows.py modules/vision/README.md
git commit -m "feat(vision): ImageGenerator.submit owns input transfer; ComfyUI uploads file inputs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 4: The page serves the operation registry, not one hardcoded operation

**Files:**
- Modify: `modules/vision/models.py` (add the `GenerationJob.facts` property)
- Create: `modules/vision/templates/vision/_job_facts.html`
- Modify: `modules/vision/templates/vision/_job_card.html:32-36`
- Modify: `modules/vision/templates/vision/gallery.html:28-37`
- Modify: `core/inference/roles.py:37` (the capability constant)
- Modify: `modules/vision/views.py:25` (imports), `:31-33` (drop `PAGE_OPERATION`), `:94-111` (`CreatePageView`), `:161-199` (`generate`), `:276-288` (`_create_page_response`)
- Modify: `modules/vision/urls.py:6-13`
- Modify: `modules/vision/templates/vision/create.html:26-27`, `:53-54`
- Modify: `core/inference/operations.py:246` (`TXT2IMG.label`), `modules/vision/tests/test_operations.py:42` (the assertion that pins it)
- Test: `modules/vision/tests/test_models.py`, `modules/vision/tests/test_views_create.py`, `modules/vision/tests/test_views_generate.py`, `modules/vision/tests/test_views_gallery.py`
- Docs: `modules/vision/README.md`, `docs/DEV.md`

**Interfaces:**
- Consumes: `core.inference.operations.operations_for(capability)`, `Operation.params`, `Param.label`, `Param.kind` (all shipped); `GenerationJob.params` being JSON-safe (Task 2).
- Produces:
  - `modules.vision.models.GenerationJob.facts -> list[tuple[str, str]]` (a property, so templates need no tag library).
  - `core.inference.roles.IMAGE_GENERATION_CAPABILITY = "image-generation"`; `modules.vision.views.page_operations() -> list[Operation]`; `resolve_page_operation(key: str | None) -> Operation` (raises `Http404` for an unregistered key).
  - URL name `vision-create-operation`, pattern `op/<slug:operation_key>/`.
  - Template context keys `operation` (the current `Operation`) and `operations` (all servable ones); the create form posts a hidden `operation` field.
  - `modules/vision/templates/vision/_job_facts.html`, included with `facts=`.

**Deliberate change to what the facts line shows:** it now lists every non-`seed`, non-`text` param the operation declares, by its `Param.label` — so `Width 1024 · Height 1024 · Steps 25 · CFG scale 7.0 · Sampler euler · Scheduler normal · Batch size 1 · Model <id>` instead of the txt2img-shaped literal. `text` params are skipped because the prompt is already the card's heading and the gallery caption's headline; `seed` leads because it is the one value an operator reuses by hand. An img2img job gets `Denoise 0.6 · Init image beach.png` with no template edit — which is the whole point.

- [ ] **Step 1: Write the failing facts test**

Append to `modules/vision/tests/test_models.py`:

```python
@pytest.mark.django_db
class TestJobFacts:
    """The card and the gallery read one schema-driven line, so an
    operation the templates have never seen describes itself."""

    def _job(self, operation="txt2img", params=None, model_id="sdxl.safetensors"):
        return GenerationJob.objects.create(
            operation=operation,
            params=params
            if params is not None
            else {
                "prompt": "a lighthouse", "negative_prompt": "blurry", "width": 1024,
                "height": 1024, "steps": 25, "cfg_scale": 7.0, "seed": 42,
                "sampler": "euler", "scheduler": "normal", "batch_size": 1,
            },
            seed=42, engine="comfyui", model_id=model_id, endpoint="http://x:8188",
            model_fingerprint="comfyui:sdxl.safetensors:None",
        )

    def test_txt2img_facts_are_labelled_from_the_schema(self):
        assert self._job().facts == [
            ("Seed", "42"),
            ("Width", "1024"),
            ("Height", "1024"),
            ("Steps", "25"),
            ("CFG scale", "7.0"),
            ("Sampler", "euler"),
            ("Scheduler", "normal"),
            ("Batch size", "1"),
            ("Model", "sdxl.safetensors"),
        ]

    def test_an_unseen_operation_describes_itself_with_no_template_change(self):
        operation = Operation(
            key="img2img", label="Image to image", capability="image-generation",
            output_media="image/png",
            params=(
                Param("prompt", "text", "Prompt", default="", required=True),
                Param("init_image", "file", "Init image", accept="image/*", required=True),
                Param("denoise", "float", "Denoise", default=0.6, min=0, max=1),
                Param("seed", "seed", "Seed", default=None),
            ),
        )
        job = self._job(
            operation="img2img",
            params={"prompt": "a lighthouse", "init_image": "beach.png", "denoise": 0.6, "seed": 42},
        )
        with patch.dict(operations._OPERATIONS, {"img2img": operation}):
            assert job.facts == [
                ("Seed", "42"),
                ("Init image", "beach.png"),
                ("Denoise", "0.6"),
                ("Model", "sdxl.safetensors"),
            ]

    def test_a_param_the_job_never_carried_is_skipped_not_rendered_blank(self):
        job = self._job(params={"prompt": "a lighthouse", "seed": 42, "steps": 25})

        assert job.facts == [("Seed", "42"), ("Steps", "25"), ("Model", "sdxl.safetensors")]

    def test_a_job_whose_operation_is_no_longer_registered_shows_its_raw_keys(self):
        """The feature was turned off, or the operation was renamed. Showing
        what was actually recorded beats showing nothing."""
        job = self._job(operation="retired", params={"steps": 25, "prompt": "a lighthouse"})

        assert job.facts == [
            ("Seed", "42"),
            ("prompt", "a lighthouse"),
            ("steps", "25"),
            ("Model", "sdxl.safetensors"),
        ]
```

Add to that module's imports:

```python
from unittest.mock import patch

from core.inference import operations
from core.inference.operations import Operation, Param
```

- [ ] **Step 2: Run it to verify it fails**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_models.py::TestJobFacts -q`
Expected: FAIL — `AttributeError: 'GenerationJob' object has no attribute 'facts'`.

- [ ] **Step 3: Render facts from the schema**

In `modules/vision/models.py`, add to the imports:

```python
from core.inference.operations import get_operation
```

Add this property to `GenerationJob`, after `is_stale`:

```python
    @property
    def facts(self) -> list[tuple[str, str]]:
        """This job's parameters as `(label, value)` pairs, read from its
        OPERATION's schema rather than from a txt2img-shaped literal.

        The job card and the gallery caption both render this, so an
        operation the templates have never seen (img2img's `denoise`, its
        `init_image`) describes itself with no template edit -- which is
        exactly what ADR 0012's Consequences promise, and the surface that
        did not yet keep it.

        `seed` leads because it is the one value an operator reuses by hand;
        the model trails because it answers "what produced this". `text`
        params are skipped: the prompt is already the card's heading and the
        gallery's caption headline, and repeating it here is noise, not
        information. A param the job never carried is skipped rather than
        rendered blank.

        A job whose operation is no longer registered (its feature was
        switched off, or the operation was renamed) falls back to its raw
        stored keys: showing the operator exactly what was recorded beats
        showing nothing.

        A property, not a free function and not a template tag: every
        surface that renders a job (the inline card, the standalone fragment
        `job_status` returns, the gallery caption) reaches it the same way,
        with no tag library and no per-view plumbing.
        """
        params = self.params or {}
        facts: list[tuple[str, str]] = [("Seed", str(self.seed))]
        operation = get_operation(self.operation)
        if operation is None:
            facts += [
                (key, str(value))
                for key, value in sorted(params.items())
                if key != "seed" and value is not None and value != ""
            ]
        else:
            for param in operation.params:
                if param.kind in ("seed", "text"):
                    continue
                value = params.get(param.key)
                if value is None or value == "":
                    continue
                facts.append((param.label, str(value)))
        facts.append(("Model", self.model_id))
        return facts
```

- [ ] **Step 4: Run it to verify it passes**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_models.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing view tests**

Append to `modules/vision/tests/test_views_create.py`:

```python
IMG2IMG = Operation(
    key="img2img", label="Image to image", capability="image-generation",
    output_media="image/png",
    params=(
        Param("prompt", "text", "Prompt", default="", required=True),
        Param("denoise", "float", "Denoise", default=0.6, min=0, max=1),
        Param("seed", "seed", "Seed", default=None),
    ),
)


@pytest.mark.django_db
class TestOperationSurface:
    """The page serves the operation REGISTRY. With one operation it looks
    exactly as it did; the second one needs no view, url, or template edit."""

    def test_the_chooser_is_hidden_while_only_one_operation_is_registered(self, client):
        body = client.get(reverse("vision-create")).content.decode()

        assert 'class="op-chooser"' not in body
        assert '<input type="hidden" name="operation" value="txt2img">' in body

    def test_a_second_operation_makes_the_chooser_appear_with_no_template_edit(self, client):
        with patch.dict(operations._OPERATIONS, {"img2img": IMG2IMG}):
            body = client.get(reverse("vision-create")).content.decode()

        assert 'class="op-chooser"' in body
        assert f'href="{reverse("vision-create-operation", args=["img2img"])}"' in body
        assert ">Image to image</a>" in body

    def test_an_operation_url_renders_that_operations_schema(self, client):
        with patch.dict(operations._OPERATIONS, {"img2img": IMG2IMG}):
            body = client.get(reverse("vision-create-operation", args=["img2img"])).content.decode()

        assert '<input type="hidden" name="operation" value="img2img">' in body
        assert 'name="denoise"' in body
        assert 'name="width"' not in body

    def test_an_unknown_operation_is_a_plain_404(self, client):
        assert client.get("/vision/op/inpaint/").status_code == 404

    def test_the_default_url_still_serves_the_first_registered_operation(self, client):
        """The heading comes from the operation's label, and txt2img's label
        carries the page's established copy -- so the visible page is
        unchanged while `/vision/op/img2img/` can say something else."""
        body = client.get(reverse("vision-create")).content.decode()

        assert "<h1>Generate an image</h1>" in body
        assert 'name="width"' in body

    def test_an_operation_url_headlines_that_operations_label(self, client):
        with patch.dict(operations._OPERATIONS, {"img2img": IMG2IMG}):
            body = client.get(reverse("vision-create-operation", args=["img2img"])).content.decode()

        assert "<h1>Image to image</h1>" in body
```

Append to `modules/vision/tests/test_views_generate.py`:

```python
@pytest.mark.django_db
class TestOperationFromThePost:
    def test_the_posted_operation_decides_which_schema_validates(self, client):
        operation = Operation(
            key="img2img", label="Image to image", capability="image-generation",
            output_media="image/png",
            params=(
                Param("prompt", "text", "Prompt", default="", required=True),
                Param("denoise", "float", "Denoise", default=0.6, min=0, max=1),
                Param("seed", "seed", "Seed", default=None),
            ),
        )
        _bind()
        with patch.dict(operations._OPERATIONS, {"img2img": operation}), _engine():
            response = client.post(
                reverse("vision-generate"),
                {"operation": "img2img", "prompt": "a lighthouse", "denoise": "0.4", "seed": "42"},
                **XHR,
            )

        assert response.status_code == 200
        job = GenerationJob.objects.get()
        assert job.operation == "img2img"
        assert job.params == {"prompt": "a lighthouse", "denoise": 0.4, "seed": 42}

    def test_a_post_naming_an_unregistered_operation_is_a_404(self, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), {"operation": "inpaint", **FORM}, **XHR)

        assert response.status_code == 404
        assert GenerationJob.objects.count() == 0

    def test_a_post_with_no_operation_field_uses_the_default(self, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM, **XHR)

        assert response.status_code == 200
        assert GenerationJob.objects.get().operation == "txt2img"
```

Append a facts-line assertion to each of the two page test modules.

`modules/vision/tests/test_views_gallery.py` already has the `_job_with_output(prompt="a lighthouse", seed=42, index=0)` factory (module level, returns `(job, output)`); reuse it. Add this method to `TestGallery`:

```python
    def test_the_caption_facts_come_from_the_schema(self, client):
        """The gallery caption renders `_job_facts.html`, so a second
        operation's parameters appear here with no template edit. The
        OUTPUT's own pixel size leads, because that measures the file
        rather than the request."""
        _job_with_output()

        body = client.get(reverse("vision-gallery")).content.decode()

        assert "Size 512×512" in body
        assert "Seed 42" in body
        assert "Sampler euler" in body
        assert "Model stub.safetensors" in body
```

`modules/vision/tests/test_views_create.py` has no job factory yet. Add one at module level, next to `_bind()`:

```python
def _done_job(prompt="a lighthouse", seed=42):
    """A finished job, for the surfaces that render a card. No files on
    disk: the card links outputs by URL and never reads one."""
    return GenerationJob.objects.create(
        operation="txt2img",
        params={
            "prompt": prompt, "negative_prompt": "", "width": 512, "height": 512,
            "steps": 20, "cfg_scale": 7.0, "seed": seed, "sampler": "euler",
            "scheduler": "normal", "batch_size": 1,
        },
        seed=seed, engine="stubengine", model_id="stub.safetensors",
        endpoint="http://stub:9999",
        model_fingerprint="stubengine:stub.safetensors:None",
        status=GenerationJob.Status.DONE,
    )
```

and this method to `TestOperationSurface`:

```python
    def test_the_card_facts_come_from_the_schema(self, client):
        _done_job()

        body = client.get(reverse("vision-create")).content.decode()

        assert "Seed 42" in body
        assert "Sampler euler" in body
        assert "Batch size 1" in body
        assert "Model stub.safetensors" in body
```

Add to `modules/vision/tests/test_views_create.py`'s imports:

```python
from core.inference import operations
from core.inference.operations import Operation, Param
```

(`test_views_generate.py` needs the same two lines; `test_views_gallery.py` needs neither.)

The existing gallery tests survive unchanged: `test_lists_outputs_with_their_facts` asserts `"42"` and `"stub.safetensors"` are in the body, and both still are.

- [ ] **Step 6: Run them to verify they fail**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_views_create.py modules/vision/tests/test_views_generate.py modules/vision/tests/test_views_gallery.py -q`
Expected: FAIL — `NoReverseMatch: Reverse for 'vision-create-operation' not found`, and the hidden-`operation`-field assertions failing.

- [ ] **Step 7: Add the shared facts partial**

Create `modules/vision/templates/vision/_job_facts.html`:

```html
{% comment %}
The ONE facts line, rendered from an Operation's `Param` schema
(`modules/vision/models.py::GenerationJob.facts`) instead of from txt2img-shaped
literals. Included by the job card and by the gallery caption, so a second
operation's parameters appear in both with no template edit -- which is the
promise ADR 0012's Consequences make.

Facts only. A caller with something the schema cannot know -- the gallery's
OUTPUT pixel size, which measures the produced file rather than the request
-- renders it inline next to the include, which is one line and needs no
slot here.
{% endcomment %}
{% for label, value in facts %}{{ label }} {{ value }}{% if not forloop.last %} · {% endif %}{% endfor %}
```

In `modules/vision/templates/vision/_job_card.html`, replace the facts paragraph (lines 32-36) with:

```html
  <p class="muted">{% include "vision/_job_facts.html" with facts=job.facts %}</p>
```

In `modules/vision/templates/vision/gallery.html`, replace the `<figcaption>` body (lines 28-37) with:

```html
    <figcaption>
      {{ output.job.params.prompt|default:"(no prompt)"|truncatechars:100 }}<br>
      Size {{ output.width|default:"?" }}×{{ output.height|default:"?" }} ·
      {% include "vision/_job_facts.html" with facts=output.job.facts %}<br>
      {{ output.job.engine }}<br>
      <a href="{% url 'vision-create' %}?reuse={{ output.job.id }}">Reuse settings</a> ·
      <a href="{% url 'vision-output-file' output.id %}?download=1">Download</a>
    </figcaption>
```

`GeneratedOutput` gets no new member. The size is a measurement of the FILE, not a parameter of the request, so it does not belong in the schema-driven line — and one inline template expression says it without a property, a list wrapper, and a loop slot to consume them. `?` when the header could not be read, exactly as before (`store.png_dimensions` degrades honestly rather than guessing).

- [ ] **Step 8: Serve the operation registry from the views**

In `modules/vision/views.py`, replace the operations import (line 25) with:

```python
from core.inference.operations import Operation, ParamError, operations_for
from core.inference.roles import IMAGE_GENERATION_CAPABILITY
```

First give the capability a single definition in `core/`. In `core/inference/roles.py`, add directly below `VISION_GENERATE_ROLE` (line 37):

```python
# The capability `vision.generate` answers and every image `Operation`
# declares. Shared here for the same reason the role keys above are: `core/`
# is the one place both core and `modules/` can reach a single definition,
# and hand-typing the string a seventh time is exactly what that comment
# exists to prevent. (The five existing literals in `operations.py`,
# `comfyui.py`, and `apps.py` are left alone -- retiring them is not this
# plan's business.)
IMAGE_GENERATION_CAPABILITY = "image-generation"
```

Then, in `modules/vision/views.py`, replace `PAGE_OPERATION` and its comment (lines 31-33) with:

```python
def page_operations() -> list[Operation]:
    """Every registered operation this page can serve, in registration order.
    The first is the default the bare `/vision/` URL lands on.

    The page reads the REGISTRY -- it names no operation of its own, so
    registering img2img in `VisionConfig.ready()` is the whole of making it
    appear here."""
    return operations_for(IMAGE_GENERATION_CAPABILITY)


def resolve_page_operation(key: str | None) -> Operation:
    """The `Operation` a request names, or the default when it names none.

    A key that is not a registered image-generation operation is a plain
    `Http404`: a stale link or a hand-typed URL must land on a 404, never on
    a form built from a schema this page cannot run.

    No branch for "nothing registered at all": operation registration and
    the `/vision/` URL mount are gated by the SAME `FARABUNKER_FEATURES`
    flag (`modules/vision/apps.py`, `config/urls.py`), so with no operations
    there is no route that reaches this function. A defensive 404 for an
    unreachable state is a branch no test can honestly pin.
    """
    available = page_operations()
    if not key:
        return available[0]
    for operation in available:
        if operation.key == key:
            return operation
    raise Http404(f"Unknown image-generation operation {key!r}.")
```

In `CreatePageView.get_context_data`, replace lines 97-102 with (the last replaced line is `context["preflight"] = check`, line 102 — stopping at 101 leaves a duplicate):

```python
        context = super().get_context_data(**kwargs)
        operation = resolve_page_operation(kwargs.get("operation_key"))
        check = services.preflight()

        context["operation"] = operation
        context["operations"] = page_operations()
        context["preflight"] = check
```

In `generate`, replace line 177 with:

```python
    operation = resolve_page_operation(request.POST.get("operation"))
```

In `_create_page_response`, add `"operations": page_operations(),` to the context dict, after `"operation": operation,`.

`gallery` is not touched. Task 5's shared delete control needs the gallery's own URL as a redirect target, and Django resolves that in the template (`{% url 'vision-gallery' as gallery_url %}`) — routing it through a view context key would put a Task-5 concern inside this task's commit for no design reason.

- [ ] **Step 9: Add the operation route**

In `modules/vision/urls.py`, insert after the bare create route:

```python
    # The default `/vision/` lands on the first registered operation; this
    # names one explicitly. The page's chooser links here, and `generate`
    # reads the same key out of its POST -- so an operation is identified
    # the same way whether the operator navigated or submitted.
    path("op/<slug:operation_key>/", CreatePageView.as_view(), name="vision-create-operation"),
```

(`slug` matches letters, digits, hyphens, and underscores, which covers `txt2img`, `img2img`, `upscale`, and `controlnet_depth`.)

- [ ] **Step 10: Add the chooser and the hidden operation field**

**The ruling on the page heading.** The `<h1>` renders from the operation's own schema label, so `/vision/op/<key>/` is honest on every URL rather than announcing "Generate an image" above an inpainting form. The txt2img page keeps its current visible copy by making that operation's label carry it. Consequences to apply deliberately, not to discover:

- `core/inference/operations.py:246` — `TXT2IMG`'s `label` becomes `"Generate an image"` (from `"Text to image"`).
- `modules/vision/tests/test_operations.py:42` — the existing `assert TXT2IMG.label == "Text to image"` becomes `assert TXT2IMG.label == "Generate an image"`. It is an existing test being corrected, so it does not change the pass count.
- The chooser renders the same label, so once img2img lands the modes read "Generate an image" / "Image to image". That is a wording job for the plan that adds the second mode; it is recorded in Forward notes rather than pre-solved here.
- `docs/superpowers/specs/2026-08-22-image-generation-design.md:99` carries `"Text to image"` in an illustrative code block, and `docs/superpowers/plans/2026-08-22-image-generation.md` records what that plan built — neither is corrected; Task 7's docs sweep confirms the design spec's comment is illustrative, not a claim about the shipped label.

In `modules/vision/templates/vision/create.html`, replace `<h1>Generate an image</h1>` (line 27) with `<h1>{{ operation.label }}</h1>`, then insert after it:

```html
{% comment %}
Hidden while one operation is registered, so the page looks exactly as it
did; the moment a second registers it appears, driven entirely by the
registry (`views.page_operations`). No operation is named here.
{% endcomment %}
{% if operations|length > 1 %}
<nav class="op-chooser" aria-label="Generation modes">
  {% for choice in operations %}
    {% if choice.key == operation.key %}<span class="current">{{ choice.label }}</span>
    {% else %}<a href="{% url 'vision-create-operation' choice.key %}">{{ choice.label }}</a>{% endif %}
  {% endfor %}
</nav>
{% endif %}
```

Inside the form, immediately after `{% csrf_token %}` (line 54), insert:

```html
  {% comment %}
  Which schema this submission is validated against. `generate` reads it and
  404s on a key that is not registered -- the URL and the POST identify an
  operation the same way.
  {% endcomment %}
  <input type="hidden" name="operation" value="{{ operation.key }}">
```

Add the chooser's styling to `create.html`'s `{% block vision_style %}` (it is page-specific, so it stays here even after Task 5 moves the shared rules out):

```css
  .op-chooser { display: flex; gap: 1rem; font-size: 0.95rem; margin-bottom: 1rem; }
  .op-chooser a { color: var(--accent); text-decoration: none; }
  .op-chooser .current { font-weight: 600; }
```

- [ ] **Step 11: Run the view tests to verify they pass**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_views_create.py modules/vision/tests/test_views_generate.py modules/vision/tests/test_views_gallery.py -q`
Expected: PASS.

- [ ] **Step 12: Update the docs**

In `modules/vision/README.md`, replace step 4 of "how to add an operation" with:

```markdown
4. Nothing under `modules/vision/` changes. `forms.build_form` renders any `Param` kind it
   knows a field for; the job card and the gallery caption render their facts from the
   schema (`GenerationJob.facts`); the page's operation chooser is driven by
   `operations_for("image-generation")` and appears by itself once a second operation is
   registered; `/vision/op/<key>/` serves that operation's form and the create form posts
   the same key back so `generate` validates against the right schema.
```

In `docs/DEV.md`, in the "Generate images (`/vision/`)" section, extend step 4:

```markdown
   While `txt2img` is the only registered operation the page shows it directly; when a
   second one is registered a mode chooser appears above the form and each mode has its
   own `/vision/op/<key>/` address.
```

- [ ] **Step 13: Run the full suite in both orders**

Run: `<repo>/.venv/bin/pytest -q`
Expected: PASS, 987 passed, 1 skipped (948 + 7 + 10 + 7 + 15).

Run: `<repo>/.venv/bin/pytest modules console scripts -q`
Expected: PASS, same counts.

- [ ] **Step 14: Commit**

```bash
git add core/inference/roles.py core/inference/operations.py modules/vision/tests/test_operations.py modules/vision/models.py modules/vision/views.py modules/vision/urls.py modules/vision/templates/vision/_job_facts.html modules/vision/templates/vision/_job_card.html modules/vision/templates/vision/gallery.html modules/vision/templates/vision/create.html modules/vision/tests/test_models.py modules/vision/tests/test_views_create.py modules/vision/tests/test_views_generate.py modules/vision/tests/test_views_gallery.py modules/vision/README.md docs/DEV.md
git commit -m "feat(vision): serve the operation registry; render job facts from the schema

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 5: One owner for the job card's CSS, its delete control, and the danger colour

**Files:**
- Modify: `templates/_shell.html:50-71` (the token block and its dark override)
- Create: `modules/vision/templates/vision/_delete_control.html`
- Modify: `modules/vision/templates/vision/base.html:19-37`
- Modify: `modules/vision/templates/vision/create.html:4-25`, `:44-54` region
- Modify: `modules/vision/templates/vision/gallery.html:4-17`, `:38-57`
- Modify: `modules/vision/templates/vision/_job_card.html:38-54`
- Modify: `console/setup/templates/setup/index.html:30-31`
- Test: `modules/vision/tests/test_views_create.py`, `modules/vision/tests/test_views_gallery.py`, `console/setup/tests/test_views.py`

**Interfaces:**
- Consumes: `_done_job()` in `modules/vision/tests/test_views_create.py` (added in Task 4, Step 5) and the pre-existing `_job_with_output()` in `modules/vision/tests/test_views_gallery.py`. Nothing from a view — this task is templates and CSS only.
- Produces: CSS custom properties `--danger` and `--ok` on `templates/_shell.html`'s `:root` (with dark-scheme values), available to every page that extends the shell; `modules/vision/templates/vision/_delete_control.html`, included with `job` in context and an optional `next_url`.

**Explicitly out of scope:** `console/inference/templates/inference/console.html:52` and `modules/rag/templates/rag/documents.html:11` each declare their own `--danger`/`--danger-text`. Those local declarations still win over the shell's for their own pages, so promoting the token changes nothing for them; `console/inference/*` belongs to the other track and is not touched. Retiring the two local copies belongs to whoever owns those pages next — it is recorded in Forward notes.

- [ ] **Step 1: Write the failing template tests**

Append to `modules/vision/tests/test_views_create.py`:

```python
@pytest.mark.django_db
class TestSharedCardChrome:
    """The card fragment is rendered by three views; the rules and markup it
    needs are owned once, not copied per page."""

    def test_the_create_page_renders_the_shared_delete_control(self, client):
        _done_job()
        response = client.get(reverse("vision-create"))

        assert "vision/_delete_control.html" in [t.name for t in response.templates]

    def test_the_card_rules_come_from_the_module_base_not_the_page(self, client):
        """`vision/base.html` declares them, so the standalone gallery gets
        the same card chrome without a second copy."""
        create = client.get(reverse("vision-create")).content.decode()
        gallery = client.get(reverse("vision-gallery")).content.decode()

        for rule in (".delete-disclosure {", ".delete-confirm {", "button.danger {"):
            assert create.count(rule) == 1
            assert gallery.count(rule) == 1

    def test_the_danger_hex_appears_only_as_the_token_declaration(self, client):
        """`templates/_shell.html` is an inline `<style>` in every page's
        `<head>` (the project ships no external stylesheet), so the token's
        VALUE necessarily reaches the body. The rule this pins is therefore
        "declared once, never used as a property value" -- which is exactly
        what a token is for."""
        for name in ("vision-create", "vision-gallery"):
            body = client.get(reverse(name)).content.decode()
            assert body.count("#b3261e") == 1  # the light-scheme token declaration
            assert "--danger: #b3261e" in body
            assert "color: #b3261e" not in body
            assert "border-color: #b3261e" not in body
            assert "var(--danger)" in body
```

Append to `modules/vision/tests/test_views_gallery.py`:

```python
    def test_the_gallery_delete_control_comes_from_the_shared_fragment(self, client):
        _job_with_output()
        response = client.get(reverse("vision-gallery"))

        assert "vision/_delete_control.html" in [t.name for t in response.templates]
```

(The `next`-field assertion is deliberately absent: `test_row_carries_a_no_confirm_delete_control` at `test_views_gallery.py:80` already pins it, and it must keep passing through this refactor. The only new fact is that the markup now comes from the shared fragment.)

Append to `console/setup/tests/test_views.py`:

```python
@pytest.mark.django_db
class TestStatusColoursUseTheSharedTokens:
    def test_the_setup_page_reads_the_status_colours_from_tokens(self):
        """`setup/index.html` extends `_shell.html`, whose inline `<style>`
        carries the token declarations -- so each hex must appear exactly
        once (that declaration) and never as a property value here."""
        body = Client().get(reverse("setup-index")).content.decode()

        assert body.count("#1b7f4f") == 1
        assert body.count("#b3261e") == 1
        assert "color: var(--ok)" in body
        assert "color: var(--danger)" in body
```

That module drives the page with `Client()` directly rather than a fixture, and already imports `Client`, `pytest`, and `reverse` — no new imports.

`_done_job()` is the factory Task 4 added to `test_views_create.py`; `_job_with_output()` is the one `test_views_gallery.py` already had. Neither test needs `GENERATED_DIR`: both pages link outputs by URL and never read a file.

- [ ] **Step 2: Run them to verify they fail**

Run: `<repo>/.venv/bin/pytest modules/vision/tests/test_views_create.py::TestSharedCardChrome modules/vision/tests/test_views_gallery.py console/setup/tests/test_views.py -q`
Expected: FAIL — `'vision/_delete_control.html'` is not among the rendered templates, and `body.count("#b3261e")` is 4 on the create page / 2 on the gallery (the raw literals), not 1.

- [ ] **Step 3: Promote the tokens onto the shell**

In `templates/_shell.html`, add to the LIGHT `:root` block, after `--accent-text` (line 58) and before `--page-max-width`:

```css
    --danger: #b3261e;
    --ok: #1b7f4f;
```

And to the `@media (prefers-color-scheme: dark)` `:root` block, after ITS `--accent-text` (line 70):

```css
      --danger: #e05c53;
      --ok: #79d68f;
```

`#e05c53` is not a new colour: it is exactly what `modules/rag/templates/rag/documents.html:20` and `console/inference/templates/inference/console.html:63` already declare for the dark scheme. Matching them means that the day those two local copies are retired in favour of this token (Forward notes), nothing changes colour. `--ok` has no existing dark precedent, so `#79d68f` is a free choice.

Add a comment directly above the two new light-scheme lines:

```css
    /* Status colours, owned here with every other token so a page never
       ships a hex the dark scheme has no override for. `--danger` was five
       copies of #b3261e across two vision templates and the setup page,
       none of them dark-aware. */
```

- [ ] **Step 4: Extract the delete control**

Create `modules/vision/templates/vision/_delete_control.html`:

```html
{% comment %}
The ONE two-step delete confirm, shared by the job card and the gallery
figure (it was duplicated verbatim, inline `onclick` included).

Dialog-free by design: a native <details> reveals the real form with no JS
at all -- click "Delete" to open it, click the summary again (or the Cancel
button, wired up by a one-line onclick that is inert without JS) to close
it. Nothing here ever calls confirm()/alert()/prompt().

`job` comes from the including context. `next_url` is optional: the gallery
passes its own URL so a non-XHR delete returns there instead of the create
page, and `job_delete` re-validates it same-origin server-side.
{% endcomment %}
<details class="delete-disclosure">
  <summary>Delete</summary>
  <div class="delete-confirm">
    <span class="muted">Delete this generation and its files?</span>
    <form method="post" action="{% url 'vision-job-delete' job.id %}">
      {% csrf_token %}
      {% if next_url %}<input type="hidden" name="next" value="{{ next_url }}">{% endif %}
      <button type="submit" class="danger">Yes, delete</button>
    </form>
    <button type="button" class="secondary" onclick="this.closest('details').removeAttribute('open')">Cancel</button>
  </div>
</details>
```

In `modules/vision/templates/vision/_job_card.html`, replace the whole `{% comment %}...{% endcomment %}` block and `<details>` element (lines 38-54) with:

```html
  {% include "vision/_delete_control.html" %}
```

In `modules/vision/templates/vision/gallery.html`, replace the comment block and `<details>` element (lines 38-57) with:

```html
    {% url 'vision-gallery' as gallery_url %}
    {% include "vision/_delete_control.html" with job=output.job next_url=gallery_url %}
```

- [ ] **Step 5: Move the shared rules onto the module base**

In `modules/vision/templates/vision/base.html`, insert these rules inside `{% block extra_style %}`, after the `.muted` rule (line 35) and before `{% block vision_style %}`:

```css
  /* Everything the SHARED job-card fragment and the SHARED delete control
     need. They live here, not on a page: `_job_card.html` is rendered by
     the create page, by `generate`, and standalone by `job_status`, and
     `_delete_control.html` by the card and the gallery -- a page cannot own
     rules a fragment three views render. */
  .job-card img { max-width: 100%; border-radius: 6px; display: block; }
  .job-images { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.5rem 0; }
  .job-images img { max-height: 320px; width: auto; }
  .job-head { display: flex; justify-content: space-between; gap: 1rem; align-items: baseline; }
  .job-error { color: var(--danger); }
  button.secondary { background: transparent; color: var(--accent); padding: 0.3rem 0; font-weight: 500; }
  /* 0.5rem, create.html's value -- NOT the gallery's 0.35rem. A6 calls the
     two copies verbatim; they differ here alone. The card's value wins
     because the card is the fragment three views render, and a figure's
     control tightening by 1.5px is the smaller surprise. */
  .delete-disclosure { margin-top: 0.5rem; }
  .delete-disclosure > summary { cursor: pointer; color: var(--accent); font-weight: 500; list-style: none; }
  .delete-disclosure > summary::-webkit-details-marker { display: none; }
  .delete-confirm { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.4rem; }
  .delete-confirm form { display: flex; align-items: center; gap: 0.5rem; margin: 0; }
  button.danger { background: transparent; color: var(--danger); padding: 0.3rem 0.6rem; font-weight: 600; border-color: var(--danger); }
```

In `modules/vision/templates/vision/create.html`, `{% block vision_style %}` keeps only its page-specific rules — delete lines 13-24 (`.job-card img` through `button.danger`) and swap the hex in line 12. `.errorlist` STAYS: it is Django's auto-generated `<ul class="errorlist">` from `{{ field.errors }}`, rendered by `create.html:60` and `:71` and by nothing else in the module (the gallery has no form), so it is not part of the shared card chrome and moving it would make the "one owner for the card chrome" comment false on the line it is written. That leaves:

```css
  .errorlist { color: var(--danger); font-size: 0.85rem; margin: 0.2rem 0 0; padding-left: 1rem; }
  .gen-form { display: grid; gap: 0.75rem; }
  .gen-form .row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0.75rem; }
  .gen-form label { display: block; font-size: 0.85rem; color: var(--muted); }
  .gen-form input, .gen-form select, .gen-form textarea {
    width: 100%; padding: 0.45rem 0.55rem; border: 1px solid var(--border);
    border-radius: 6px; background: var(--bg); color: var(--text); font: inherit;
  }
  .op-chooser { display: flex; gap: 1rem; font-size: 0.95rem; margin-bottom: 1rem; }
  .op-chooser a { color: var(--accent); text-decoration: none; }
  .op-chooser .current { font-weight: 600; }
```

In `modules/vision/templates/vision/gallery.html`, `{% block vision_style %}` keeps only its page-specific rules — delete lines 10-16, leaving:

```css
  .gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 1rem; }
  .gallery figure { margin: 0; }
  .gallery img { width: 100%; height: auto; border-radius: 6px; display: block; }
  .gallery figcaption { font-size: 0.85rem; color: var(--muted); margin-top: 0.35rem; }
  .pager { display: flex; gap: 1rem; margin-top: 1.5rem; }
```

- [ ] **Step 6: Tokenize the setup page's status colours**

In `console/setup/templates/setup/index.html`, replace lines 30-31 with:

```css
  .status.ok { color: var(--ok); }
  .status.down { color: var(--danger); }
```

- [ ] **Step 7: Run the template tests to verify they pass**

Run: `<repo>/.venv/bin/pytest modules/vision console/setup -q`
Expected: PASS.

- [ ] **Step 8: Run the full suite in both orders**

Run: `<repo>/.venv/bin/pytest -q`
Expected: PASS, 992 passed, 1 skipped (987 + 5).

Run: `<repo>/.venv/bin/pytest modules console scripts -q`
Expected: PASS, same counts.

- [ ] **Step 9: Commit**

```bash
git add templates/_shell.html modules/vision/templates/vision/_delete_control.html modules/vision/templates/vision/base.html modules/vision/templates/vision/create.html modules/vision/templates/vision/gallery.html modules/vision/templates/vision/_job_card.html console/setup/templates/setup/index.html modules/vision/tests/test_views_create.py modules/vision/tests/test_views_gallery.py console/setup/tests/test_views.py
git commit -m "refactor(vision): one owner for the card chrome, the delete control, and --danger

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 6: ADR 0012 says what the code now actually delivers

**Files:**
- Modify: `docs/adr/0012-image-generation-engine-adapter.md` (new decision subsection; first Consequences bullet)
- Modify: `docs/ARCHITECTURE.md:124`
- Modify: `docs/DEV.md` ("Install ComfyUI" section)
- Test: `modules/vision/tests/test_setup_guides.py` — none of the above is executable; the pin for this task is that the *claims* the ADR makes are the ones Tasks 1–5 tested. No new test code; Step 4 is the check.

**Interfaces:**
- Consumes: everything Tasks 1–5 produced. This task adds no code.

**Why this is a task and not a footnote:** the ADR's Consequences bullet is the sentence the next planner reads when scoping img2img. It currently promises an additive expansion; Tasks 1–4 made most of that true, and the part that is still not true (`"asset"`-kind params have no widget, per D7) must be stated where the promise is, not only in D7.

- [ ] **Step 1: Record the input-transfer ruling**

In `docs/adr/0012-image-generation-engine-adapter.md`, insert this subsection under `## Decision`, immediately after the `### GenerationRejected — the engine's outright-refusal exception` section:

```markdown
### Input transfer belongs to `submit` (2026-08-23)

`GenerationRequest.inputs` carries paths in the PLATFORM's managed store
(`<GENERATED_DIR>/<job>/inputs/`), and D1 puts the engine on the host while
the platform runs in the `web` container — so those paths are meaningless
to the engine. The transfer is owned by `ImageGenerator.submit`
(`core/inference/engines/base.py`), not by a separate platform-orchestrated
`upload()` member on the protocol. Three reasons, in the order they decided
it:

- The handle a transfer yields is **engine vocabulary**. ComfyUI answers
  `POST /upload/image` with `{"name", "subfolder", "type"}` and its nodes
  address the file as `"<subfolder>/<name>"`; the design spec's §3 forbids
  that vocabulary from crossing the adapter boundary, and a platform-level
  `upload()` would make `modules/vision/services.py` hold it and hand it
  back.
- D5 promises a mode is one `Operation` plus one graph template. A
  two-phase upload-then-submit would put a second engine call, its own
  error path, and its own retry semantics into the service layer for
  **every** file-taking operation — img2img, inpaint, ControlNet, upscale.
- The two failure modes this ADR already rules on cover a transfer with no
  new grammar: a refusal is `GenerationRejected` (final, carrying the
  engine's own words), a transport failure propagates and is retried on the
  next poll.

What `refresh_job` does when the engine's copy of an upload is gone:
nothing special, by construction. Refresh uses exactly one handle — the
job's `engine_ref` — so an engine that lost an uploaded input also lost its
queue entry and its history, reports `lost`, and the operator is told to
resubmit. The platform keeps the authoritative copy of every input (design
spec §5) and never deleted it, so the resubmit re-transfers with nothing
lost. Concretely, `ComfyUIGenerator._upload_inputs` uploads into a
subfolder named after the job's own `client_ref` with `overwrite=true`:
two jobs sending files of the same name cannot collide, and resubmitting
the same job is idempotent.
```

- [ ] **Step 2: Correct the Consequences bullet**

In the same file, replace the first `## Consequences` bullet (the one beginning "Adding img2img, inpaint, ControlNet, or upscale is one new `Operation` registration") with:

```markdown
- Adding img2img, inpaint, ControlNet, or upscale is one new `Operation`
  registration plus one new graph template — never a reshape of
  `modules/vision/` or the operation registry itself. Concretely, the files
  a new operation touches are:
  - `core/inference/operations.py` — the `Operation` definition.
  - `core/inference/engines/comfyui_workflows/<operation>.py` plus its
    entry in that package's `_TEMPLATES`.
  - `modules/vision/apps.py` — one gated `register_operation` call.

  Three files, and the engine adapter is not one of them:
  `ComfyUIEngine.supported_operations` reads the template registry
  (`comfyui_workflows.template_keys()`), so registering a template is the
  whole of teaching the adapter it can run the mode.

  Nothing under `modules/vision/` beyond that one registration line. The
  form is built from the schema (`forms.build_form`); the job card and the
  gallery caption render their facts from it (`GenerationJob.facts`); the
  page's mode chooser is driven by `operations_for("image-generation")` and
  appears by itself once a second operation exists, with `/vision/op/<key>/`
  serving each; and file parameters are carried end to end — reduced to a
  basename in the job's `params` JSONField, stored under
  `<GENERATED_DIR>/<job>/inputs/` with a `JobInput` row, and transferred to
  the engine by `ImageGenerator.submit` itself (see "Input transfer belongs
  to `submit`" above).

  Two caveats stand, and they are the honest limit of that promise. An
  `"asset"`-kind param (LoRA, VAE, ControlNet, or upscaler *selection*) has
  no form widget — `modules/vision/forms.py::_field_for` raises rather than
  rendering nothing, per D7 — so the first operation that needs one ships
  that widget with it. And a `"choice"` param whose options an engine
  reports live needs a mapping in that engine's adapter
  (`comfyui.py::_CHOICE_INPUTS`) before the form can offer them.
```

- [ ] **Step 3: Ripple the two prose docs**

In `docs/ARCHITECTURE.md`, line 124, replace the existing sentence with:

```markdown
  mode (img2img, inpainting, upscaling) is one new operation plus one new template —
  the page renders its form, its job-card facts, and its mode chooser from the
  operation's `Param` schema, and the engine adapter moves any file input itself.
```

In `docs/DEV.md`, at the end of the "Install ComfyUI (image generation)" section (after the "One GPU, two engines" paragraph), add:

```markdown
**Uploads.** An operation that takes an image (img2img, inpainting) sends the file to
ComfyUI over its own `/upload/image` endpoint, into a subfolder named after the job.
farabunker keeps its own copy under `data/generated/<job>/inputs/`, so a ComfyUI restart
that clears its `input/` folder costs nothing: the job reports as lost and a resubmit
re-sends the file. Nothing has to be shared between the container and the host.
```

- [ ] **Step 4: Verify every claim the ADR now makes has a test behind it**

Read the amended Consequences bullet and confirm each clause is pinned:

| Claim | Pinned by |
|---|---|
| the form is built from the schema | `modules/vision/tests/test_forms.py` (existing) |
| job-card facts come from the schema | `test_models.py::TestJobFacts` (Task 4) |
| the chooser is driven by the registry | `test_views_create.py::TestOperationSurface` (Task 4) |
| `/vision/op/<key>/` serves each operation | `test_views_create.py::TestOperationSurface` (Task 4) |
| a file param's value is JSON-safe | `test_operations.py::TestFileParams` (Task 2) |
| inputs are stored with a `JobInput` row | `test_services.py::TestFileInputs` (Task 2) |
| `submit` transfers the file | `test_comfyui_generator.py::TestInputTransfer` (Task 3) |
| the adapter is not touched by a new operation | `test_comfyui_engine.py::test_supported_operations_follows_the_template_registry` (Task 3) |
| the heading names the operation | `test_views_create.py::test_an_operation_url_headlines_that_operations_label` (Task 4) |
| an `"asset"` param still raises | `test_forms.py::test_an_asset_param_is_refused_until_its_ui_ships` (existing) |

If any row has no test, stop and write it before committing — an ADR claim with no pin is exactly the failure this task exists to end.

- [ ] **Step 5: Run the full suite in both orders**

Run: `<repo>/.venv/bin/pytest -q`
Expected: PASS, 992 passed, 1 skipped (docs-only task; the count does not move).

Run: `<repo>/.venv/bin/pytest modules console scripts -q`
Expected: PASS, same counts.

- [ ] **Step 6: Commit**

```bash
git add docs/adr/0012-image-generation-engine-adapter.md docs/ARCHITECTURE.md docs/DEV.md
git commit -m "docs(adr): 0012 records the input-transfer ruling and the real cost of an operation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 7: Final verification and docs sweep

**Files:**
- Modify (only if the sweep finds a stale statement): `modules/vision/README.md`, `console/inference/README.md`, `docs/DEV.md`, `docs/ARCHITECTURE.md`, `docs/adr/0012-image-generation-engine-adapter.md`
- Test: no new tests; this task runs the whole suite and reads the docs against the shipped code.

**Interfaces:**
- Consumes: everything Tasks 1–6 produced.

- [ ] **Step 1: Run the full suite in both orders, from a clean cache**

```bash
cd <worktree>
find . -name __pycache__ -type d -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null; true
<repo>/.venv/bin/pytest -q
<repo>/.venv/bin/pytest modules console scripts -q
```

Expected: both PASS with identical counts (992 passed, 1 skipped). A count that differs between the two orders is a test-isolation bug, not a flake — most likely a `register_operation` or `patch.dict` that leaked; find it before continuing.

- [ ] **Step 2: Confirm the migration applies on an empty database**

```bash
<repo>/.venv/bin/pytest modules/vision -q --create-db
<repo>/.venv/bin/python manage.py makemigrations --check --dry-run
```

Expected: the first PASSes; the second prints `No changes detected` — if it wants to write a migration, `0002_generationjob_endpoint.py` does not match the model and must be regenerated.

- [ ] **Step 3: Sweep the docs against the shipped code**

Read each of these and fix anything the six tasks made untrue:

```bash
grep -rn "PAGE_OPERATION\|one operation\|hardcoded\|txt2img only\|params\.width\|params\.sampler" docs modules/vision/README.md console/inference/README.md
```

Specifically confirm:
- `modules/vision/README.md`'s "how to add an operation" lists five steps and none of them names a template edit.
- `console/inference/README.md`'s per-engine-endpoints note is unchanged (no task touched discovery).
- `docs/DEV.md`'s `/vision/` walkthrough mentions the mode chooser and the upload behaviour.
- `docs/ARCHITECTURE.md`'s operations paragraph matches ADR 0012's corrected Consequences bullet.
- No doc still describes the job card's facts line as txt2img-shaped.

- [ ] **Step 4: Confirm the branch is clean and every commit carries both trailers**

```bash
git status --porcelain
git log --format='%h %s%n%b' 4cf4881..HEAD | grep -c 'Co-Authored-By: Claude Fable 5'
git log --format='%h %s%n%b' 4cf4881..HEAD | grep -c 'Claude-Session: https://claude.ai/code/session_<id>'
```

Expected: no output from `git status --porcelain` (except any doc fix from Step 3, which is committed below); both counts equal the number of commits on the branch since `4cf4881` — 6 after Task 6, 7 once this task commits.

- [ ] **Step 5: Commit any doc fixes the sweep found**

Skip this step if Step 3 changed nothing.

```bash
git add docs modules/vision/README.md console/inference/README.md
git commit -m "docs(vision): sweep the docs against the pre-expansion cleanup

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

- [ ] **Step 6: Verify on the live preview stack**

Per the verification doctrine, nothing is reported complete before fresh pixels. With ComfyUI running natively (`--listen 0.0.0.0`):

```bash
scripts/preview up vision-generation --port 8002 --db-port 5435
```

Then, in the browser at `http://localhost:8002`:
1. `/inference/` — register a ComfyUI checkpoint, bind `vision.generate`.
2. `/vision/` — the mode chooser is **absent** (one operation), the form renders, generate an image; the card's facts line reads `Seed … · Width … · Height … · Steps … · CFG scale … · Sampler … · Scheduler … · Batch size … · Model …`.
3. `/vision/gallery/` — the image is listed with `Size <w>×<h>` leading the same facts line; "Delete" opens the two-step confirm and returns to the gallery.
4. `/setup/` — the reachable/unreachable status colours still read correctly in both light and dark browser themes.
5. `/vision/op/txt2img/` — serves the same page as `/vision/`; `/vision/op/inpaint/` — 404.

Only after those pixels is this branch reportable as done.

---

## Forward notes (next features)

Bucket-B items from the pre-expansion audit. **Not tasks** — a keep-in-mind checklist, each
mapped to the future feature that should absorb it, so the next plan inherits them instead of
rediscovering them.

### Absorb into **img2img** (the first file-taking operation)

- **B3 — `JobInput` is write-only.** Never queried, never served (no URL), absent from
  `_job_json`, not reusable via `?reuse=`. Tasks 2 and 3 fixed the *write* path; the read path
  is naturally part of img2img's UI (showing the operator which init image a job ran, and
  letting a re-run reuse it). Note that Task 2 deliberately makes `?reuse=` leave file fields
  empty — serving `JobInput` is what would let that change.
- **B10 — `preflight()` runs before `validate_params`.** A malformed submission while the engine
  is down reports 503 instead of the 400 that is true, and every rejected submission pays a
  health round-trip. img2img adds a required file param, which makes invalid submissions more
  likely, so swap the order then.
- **B13 (remainder) — defensive-branch coverage.** The `submit_job(files=...)` gap closed in
  Task 2. Still open: `test_views_create.py:190` asserts only that the nav link disappears with
  the feature off, never that `GET /vision/` 404s; `_combo_values`' non-list / typed-input
  branches (`comfyui.py:79-83`); non-JSON ComfyUI bodies (a proxy's HTML 502 escapes
  `response.json()` into `submit_job`'s generic `except`, which is honest but unpinned).

### Absorb into **inpaint** (or the first graph with two output nodes)

- **B4 — `fetch_outputs` sorts node ids as strings.** `sorted((history.get("outputs") or {}).items())`
  makes `"10" < "2"`. Inert with one `SaveImage` node; bites the first graph with ≥10 nodes and
  two output nodes (hi-res fix, upscale-then-save). Fix is a numeric key on the sort, with a
  fallback for a non-numeric node id.

### Absorb into **LoRA** (the first asset-taking operation)

- **B1 — `asset`-kind params raise.** `forms.py:74-77`, pinned by
  `test_forms.py::test_an_asset_param_is_refused_until_its_ui_ships`. ADR 0012 D7 scopes this
  out deliberately and Task 6's amended Consequences bullet now names it as a standing caveat;
  it ships *with* the LoRA work, which is when the widget is actually needed.
- **B6 — one health check + one `list_choices` call per choice param, serially, on every GET
  and every POST.** `views.py:60-86`, each at `DISCOVERY_TIMEOUT = 5.0`; worst case ~15s to
  render. Tolerable at two choice params; grows linearly the moment asset params (LoRA lists)
  are added. Fix with one batched `/object_info` call.

### Absorb into **upscale**

- **B8 — no DB index anywhere.** `modules/vision/models.py`, `migrations/0001_initial.py`. The
  gallery joins `GeneratedOutput -> job` and sorts on unindexed `-job__created_at`. Fine at
  hundreds of rows; batch generation and upscaling grow the table fast. Add `Meta.indexes` when
  the gallery gets slow — and note Task 1 added `0002`, so the index migration is `0003`.

### Absorb into **the second engine adapter** (A1111/Forge, SwarmUI, InvokeAI, diffusers)

- **B5 — `rstrip("/")` inconsistency.** `ComfyUIGenerator.__init__` normalizes the endpoint;
  `is_healthy`, `list_installed`, and `_combo_values` do not, yielding `http://x:8188//system_stats`.
  ComfyUI tolerates it today. `discovery.norm_endpoint` is the canonical normalizer but `core/`
  may not import `console/`, so the fix needs a home decision — which is exactly the decision a
  second adapter forces.
- **B9 — the `#engine-comfyui` fallback anchor.** `create.html:43`, pinned by
  `test_views_create.py`. The one engine-name literal in a layer ADR 0012 says names none.
  Correct while one image adapter is registered; wrong the day a second lands.
- **B7 — `/setup/` health-checks every registered engine serially.** `console/setup/views.py:72-82`,
  each up to `DISCOVERY_TIMEOUT`; two down engines ≈ 10s. This is the page an operator opens
  *because* things are down, so it matters — and it worsens with a second adapter, not with
  more operations.

### Absorb into **the first multi-file family** (the first family that reads `config`)

- **B2 — `ModelConnection.config` (D8) has no console edit surface.** `console/inference/models.py:81`;
  no `config` field in `console/inference/views.py` or any `console/inference/templates/`. Only a
  test or a shell can write it. **Other track's area** — coordinate rather than edit.
- **B14 — `context_window` splat.** `console/inference/bindings.py:85-87` +
  `_connection_edit.html:74`: the edit form labels `context_window` "chat models only" but never
  restricts it, so a ComfyUI connection with a stray value gets `context_window` splatted into
  `ComfyUIGenerator(config=...)`. Completely inert today (`txt2img.build` documents that it
  ignores `config`); a live landmine for the first template that reads it. **Other track's
  area** — settle when B2 gives `config` an edit surface.

### Absorb into **the chatbot tool** (the second caller of `services`)

- **B12 — `int(str(raw).strip())` rejects `1024.0`.** `core/inference/operations.py:134-136`; a
  JSON/LLM caller emitting `{"width": 1024.0}` gets "Width must be a number." Harmless from the
  page (Django's `IntegerField` coerces first); only the chatbot-tool path hits it. Note the
  original deferred list recorded this backwards, as "accepted" — it is a rejection.

### A wording job the second mode inherits

Task 4 rules that the create page's `<h1>` renders `{{ operation.label }}`, and keeps the current
copy by setting `TXT2IMG.label = "Generate an image"`. The chooser renders the same label, so the
day img2img registers, the two modes read "Generate an image" / "Image to image" — a heading that
works and a chooser entry that reads oddly beside its sibling. The plan that adds the second mode
should settle it, most likely by giving `Operation` a separate short chooser label or by renaming
txt2img's to "Text to image" once the heading is no longer the only thing it feeds.

### Already folded in

- **B11 — the duplicated inline `onclick`.** Folded into Task 5's `_delete_control.html`
  extraction; it is now written once and is still the smallest no-JS-inert cancel that works.

### One item this plan deliberately left alone

`console/inference/templates/inference/console.html:52` and
`modules/rag/templates/rag/documents.html:11` each declare their own `--danger`/`--danger-text`.
Task 5 promoted the token onto `templates/_shell.html`, and those local declarations still win
for their own pages, so nothing changed for them. Retiring the two copies belongs to whoever
owns those pages next; `console/inference/*` is the other track's area either way.

---

## Plan review

- **Round 1 — adversarial hygiene review, 2026-08-23: AMEND, 18 edits (2 blocking) — applied.**
  Blocking: the `#b3261e` assertions contradicted the token promotion (the shell's `<style>` is
  inline in every page, so the token's value necessarily reaches the body); and Task 1's delta was
  +5 when it is +7, which invalidated every running total. Substantive: `job_facts` folded into the
  `facts` property; the capability string moved to `core/inference/roles.py`; the duplicated
  file-param comprehension became `Operation.file_param_keys()`; `delete_next`, `GeneratedOutput.size_fact`,
  the partial's `extra=` slot, `resolve_page_operation`'s unreachable empty-registry branch, and one
  no-op template test cut; `.errorlist` kept on the create page; dark `--danger` matched to the
  repo's existing `#e05c53`. Minor: three line references corrected, one range off by one, a
  `django_db` marker added, the `<h1>` ruling written down (schema label; txt2img's label carries the
  current copy), the `.delete-disclosure` margin conflict named, `supported_operations` derived from
  the template registry so `comfyui_workflows`' own docstring claim becomes true, one duplicated
  assertion trimmed.
- **Round 2: CLEAN — cosmetic indent nit fixed inline.**
