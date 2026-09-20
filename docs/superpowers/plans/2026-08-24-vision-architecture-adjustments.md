# Vision Architecture Adjustments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the vision track tool-ready — one submission path (the page enqueues `vision.generate` like `AskView` enqueues `rag.ask`), one service layer holding every generation behaviour, and machine-readable schema / job / failure contracts an agent can read.

**Architecture:** Three moves, in this order. (1) Pull the last three generation behaviours out of `modules/vision/views.py` into `modules/vision/services.py` (`live_options` + `operation_catalog`, `job_json`, `resolve_inputs`) and add a pure `core.inference.operations.describe()` beneath them, so the queue work that follows is written once against final signatures. (2) Add the schema the convergence needs: `GenerationJob.queue_job_id`, `GenerationJob.failure_kind`, and a nullable `JobInput.job` so a browser upload can be recorded as a stored input *before* the job that will consume it exists. (3) Route `POST /vision/generate/` through `core.inference.queue.enqueue("vision.generate", ...)`, replacing the immediate job card with a queued placeholder card that polls `/vision/queue/<id>/` and swaps itself for the real job card the moment the worker's `submit_job` creates the row.

**Tech Stack:** Django 5 + pytest/pytest-django (no conftest.py; class-level `@pytest.mark.django_db`), PostgreSQL, `httpx`-layer mocking for engine calls, the existing execution queue (`core/inference/queue.py` seam → `console/jobs/backend.py`), ComfyUI adapter behind `core/inference/engines/`.

**Spec:** The two audit reports this plan implements, both authoritative:
- `<scratchpad>/audit2/architecture-tool-readiness.md` — ranked items **1–9** are the approved scope. **Item 10 (moving concrete op definitions out of `core/`) is explicitly excluded/deferred.**
- `<scratchpad>/audit2/duplication-complexity.md` — the three duplication findings (`SAMPLING_PARAMS` extraction, the "vision" naming-collision README note, `test_store._png` folding into `_helpers`).

Supporting context the executor should read before Task 13: `docs/adr/0012-image-generation-engine-adapter.md`, `docs/adr/0013-inference-execution-queue.md`, `modules/rag/views.py::AskView` (the existing queue-routed page pattern), `modules/vision/README.md`.

---

## Global Constraints

- **TDD, always.** Failing test first, minimal implementation, green, commit. No implementation code lands without a test that failed before it.
- **One pytest at a time, in the foreground.** Never run two pytest processes concurrently (parallel sessions collide on the test database).
- **Test invocation, verbatim:**
  `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest <path> -q`
- **No `conftest.py`.** This repo has none and must keep none. Use class-level `@pytest.mark.django_db` and per-module autouse fixtures that delegate to `modules/vision/tests/_helpers.py` bodies.
- **Mock at the `httpx` layer** (`patch("core.inference.engines.comfyui.httpx.get"/".post")` with `FakeComfyUI`), or use `StubEngine`/`StubGenerator` from `_helpers` — never mock the service layer when an engine stub will do.
- **Every task ships tests AND docs.** A task that changes behaviour updates `modules/vision/README.md` (and the ADR where the task says so) in the same commit.
- **Commits carry the two standard trailers**, exactly:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  ```
- **Behaviour preservation.** Everything the audit lists under "Right — do not touch" stays untouched: `ImageGenerator.submit` owning input transfer; recorded-endpoint binding + `_generator_for_job`; `validate_params` as the schema floor with the form as the live layer; `StoredFile` + `output:`/`input:` refs; `refresh_job` idempotence and transient-forgiveness vs `submit_job` fail-fast; media-generic `GenerationJob`/`GeneratedOutput` with verbatim `engine_payload`; `supported_operations` derived from `template_keys`; UUID PK as `client_ref` and directory name; the feature flag gating registration only; the `core` → no-`modules` import direction.
- **The page must never have a dead submit button.** After Task 13 a submission still puts a card at the top of Recent immediately — a *queued placeholder* card that polls and turns into the real job card. No submission may leave the page with nothing to look at.
- **Working tree:** `<worktree>`. Never touch the main checkout.
- **Baseline (post-merge of main, `df251c3`):** 1760 passed / 1 skipped in both orders; `makemigrations --check` clean. Both must hold again at Task 16.
- **`modules/` may not import `console.*`** (the single sanctioned exception, `console.inference.bindings`, is not used by this plan). The queue is reached only through `core.inference.queue`.
- **Item 10 is out of scope.** `TXT2IMG`/`IMG2IMG`/`INPAINT`/`UPSCALE` stay defined in `core/inference/operations.py`.

---

## Dependency Table (strictly sequential — execute in this order)

| Task | Depends on | Why |
|---|---|---|
| 1. Shared param tuples | — | Pure `core` refactor; Task 2 edits the same literals. |
| 2. `description` on `Param`/`Operation` | 1 | Writes descriptions once, on the tuples Task 1 created. |
| 3. `operations.describe()` | 2 | Serializes the `description` field Task 2 added. |
| 4. `services.live_options` + `operation_catalog()` | 3 | `operation_catalog` layers live options onto `describe()` output. |
| 5. `services.job_json()` | — (ordered here) | Task 10 consumes it for output URLs. |
| 6. `services.resolve_inputs()` | — (ordered here) | Task 13's view calls it; `run_generate`'s own call is this task's own edit (Task 9 only stamps `queue_job_id`). |
| 7. Migration 0004 (schema) | — | Fields Tasks 8, 9, 11 write to. |
| 8. `failure_kind` wiring | 7, 5 | Sets the field at every failure site; `job_json` exposes it. |
| 9. `run_generate` stamps `queue_job_id` | 7 | Correlation the Task 12 poll endpoint reads. Consumes `ctx.job_id`, already on main — no contract work. |
| 10. `run_generate` result shape | 5, 9 | Builds `output_urls` from `job_json`. |
| 11. Staged uploads | 7 | Needs the nullable `JobInput.job` + `created_at`. |
| 12. Queued placeholder + poll endpoint | 9 | Correlates by `queue_job_id`. |
| 13. `generate` enqueues | 4, 6, 11, 12 | The convergence; consumes every seam above. |
| 14. ADR 0012 amendment + README | 13 | Documents the contract as shipped. |
| 15. `_helpers.png_bytes` | — | Independent test-helper dedup. |
| 16. Full-suite verification | all | Both-order run + `makemigrations --check`. |

**Ordering ruling (required by the brief):** the services moves (audit items 2/3/4) come **before** the queue convergence (item 1). The convergence rewrites `views.generate` end to end; doing it after the moves means that rewrite is written once, against `services.resolve_inputs`/`services.job_json`/`services.live_options` in their final shape, instead of being written against `views`-local helpers and then rewritten again.

---

## File Structure

**Modified**
- `core/inference/operations.py` — `PROMPT_PARAMS`/`SAMPLING_PARAMS` tuples (T1), `description` on `Param`/`Operation` + every description string (T2), `describe()` (T3).
- `modules/vision/services.py` — gains `live_options`, `operation_catalog`, `job_json`, `resolve_inputs`, `InputReferenceError`, `stage_upload`, `prune_staged_inputs`, `discard_staged_inputs`; `_fail` gains `failure_kind`. `submit_job`'s signature is unchanged.
- `modules/vision/views.py` — loses `live_options` and `_job_json`; `generate` becomes an enqueue; gains `queue_job_status`, `_queue_card_context`, `_queued_placeholder`.
- `modules/vision/jobs.py` — `JOB_KIND` constant, `_payload_files` deleted in favour of `services.resolve_inputs`, `run_generate` stamps `ctx.job_id` onto the generation and returns `timed_out`/`output_urls`.
- `modules/vision/models.py` — `GenerationJob.queue_job_id`, `GenerationJob.FailureKind` + `failure_kind`, `JobInput.job` nullable, `JobInput.created_at`.
- `modules/vision/store.py` — `_write_upload` shared writer, `stage_input`, `remove_staged_input`, `STAGING_DIRNAME`.
- `modules/vision/apps.py` — registers the kind under `jobs.JOB_KIND`.
- `modules/vision/urls.py` — `/vision/queue/<int:queue_job_id>/`.
- `config/settings.py` — `VISION_STAGED_UPLOAD_TTL`.
- `modules/vision/templates/vision/create.html` — renders the `?queued=` placeholder.
- `docs/adr/0012-image-generation-engine-adapter.md`, `modules/vision/README.md`. (ADR 0013 is **read-only** for this plan: its §5/§8 already document the `handler(payload, models, ctx)` contract this work consumes.)

**Created**
- `modules/vision/templates/vision/_queued_card.html` — the placeholder card.
- `modules/vision/migrations/0004_queue_correlation_failure_kind_staged_inputs.py`.
- `modules/vision/tests/test_views_queue.py` — the placeholder + poll endpoint.

**Test files touched:** `modules/vision/tests/` only — `test_operations.py`, `test_services.py`, `test_jobs.py`, `test_views_generate.py`, `test_views_create.py`, `test_views_queue.py` (new), `test_store.py`, `test_models.py`, `test_apps.py`, `_helpers.py`. No `console/` or `modules/rag/` test changes: the handler contract this work rides on is already merged.

---

### Task 1: One definition of the shared generation params

Audit companion finding #1: the 8 sampling `Param` lines are byte-identical across `TXT2IMG`/`IMG2IMG`/`INPAINT`. `LORA_PARAMS` already demonstrates the fix in the same file.

**Ruling:** extract **two** tuples, not one. The 8 shared params are not contiguous — `TXT2IMG` puts `width`/`height` between `negative_prompt` and `steps`, `IMG2IMG` puts `init_image`/`denoise` there. A single 8-tuple would reorder every form (param order drives field order in `create.html`). `PROMPT_PARAMS` (2) + `SAMPLING_PARAMS` (6) splice around the mode-specific middle and preserve declaration order exactly.

**Files:**
- Modify: `core/inference/operations.py:325-421`
- Test: `modules/vision/tests/test_operations.py`

**Interfaces:**
- Produces: `core.inference.operations.PROMPT_PARAMS: tuple[Param, ...]` (`prompt`, `negative_prompt`), `core.inference.operations.SAMPLING_PARAMS: tuple[Param, ...]` (`steps`, `cfg_scale`, `seed`, `sampler`, `scheduler`, `batch_size`). Task 2 attaches descriptions to these tuples.

- [ ] **Step 1: Write the failing test**

Append to `modules/vision/tests/test_operations.py` (import `PROMPT_PARAMS`, `SAMPLING_PARAMS` in the module's existing `from core.inference.operations import (...)` block):

```python
class TestSharedParamTuples:
    """The `LORA_PARAMS` treatment, applied to the params that were three
    byte-identical copies: txt2img, img2img and inpaint cannot drift apart
    on what a prompt or a sampler control is."""

    def test_every_checkpoint_mode_splices_the_same_two_tuples(self):
        for operation in (TXT2IMG, IMG2IMG, INPAINT):
            assert operation.params[:2] == PROMPT_PARAMS
            assert operation.params[-len(LORA_PARAMS) - len(SAMPLING_PARAMS):
                                    -len(LORA_PARAMS)] == SAMPLING_PARAMS

    def test_the_tuples_carry_exactly_the_params_that_were_copied(self):
        assert [param.key for param in PROMPT_PARAMS] == ["prompt", "negative_prompt"]
        assert [param.key for param in SAMPLING_PARAMS] == [
            "steps", "cfg_scale", "seed", "sampler", "scheduler", "batch_size",
        ]

    def test_declaration_order_is_unchanged_by_the_extraction(self):
        """The characterization half: param order drives form field order
        (`create.html` renders prompt/negative_prompt first, then the rest
        in declaration order), so the refactor must move nothing."""
        assert [param.key for param in TXT2IMG.params] == [
            "prompt", "negative_prompt", "width", "height", "steps", "cfg_scale",
            "seed", "sampler", "scheduler", "batch_size", "loras", "lora_strength",
        ]
        assert [param.key for param in IMG2IMG.params] == [
            "prompt", "negative_prompt", "init_image", "denoise", "steps", "cfg_scale",
            "seed", "sampler", "scheduler", "batch_size", "loras", "lora_strength",
        ]
        assert [param.key for param in INPAINT.params] == [
            "prompt", "negative_prompt", "init_image", "mask_image", "mask_grow",
            "denoise", "steps", "cfg_scale", "seed", "sampler", "scheduler",
            "batch_size", "loras", "lora_strength",
        ]

    def test_the_shared_params_are_the_same_objects_not_copies(self):
        """Frozen dataclasses compare by value, so identity is what proves
        one definition rather than three that happen to agree today."""
        assert TXT2IMG.param("steps") is IMG2IMG.param("steps")
        assert IMG2IMG.param("sampler") is INPAINT.param("sampler")
```

Also add `LORA_PARAMS` to that import block if it is not already imported.

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_operations.py::TestSharedParamTuples -q`
Expected: FAIL — `ImportError: cannot import name 'PROMPT_PARAMS' from 'core.inference.operations'`.

- [ ] **Step 3: Extract the tuples**

In `core/inference/operations.py`, directly above `LORA_PARAMS`, add:

```python
# The two prompt fields every checkpoint-based mode opens with. Declared
# once and spliced in, for exactly the reason `LORA_PARAMS` below is:
# txt2img, img2img, and inpaint must not drift apart on what a prompt
# control is.
PROMPT_PARAMS = (
    Param("prompt", "text", "Prompt", default="", required=True),
    Param("negative_prompt", "text", "Negative prompt", default=""),
)

# The sampling controls every checkpoint-based mode shares, in the order
# they were declared in all three. Split from `PROMPT_PARAMS` rather than
# written as one 8-tuple because the modes differ in the MIDDLE (txt2img's
# width/height, img2img's init_image/denoise, inpaint's mask): two tuples
# spliced around the mode-specific params preserve every operation's
# declaration order, and declaration order is form field order.
SAMPLING_PARAMS = (
    Param("steps", "int", "Steps", default=25, min=1, max=150),
    Param("cfg_scale", "float", "CFG scale", default=7.0, min=0, max=30, step=0.5),
    Param("seed", "seed", "Seed", default=None),
    # Choices are engine-reported (`list_choices`) -- see `Param.choices`.
    Param("sampler", "choice", "Sampler"),
    Param("scheduler", "choice", "Scheduler"),
    Param("batch_size", "int", "Batch size", default=1, min=1, max=8),
)
```

Then rewrite the three operations' `params` tuples to splice them (delete the copied lines, keep everything else byte-identical):

```python
TXT2IMG = Operation(
    key="txt2img",
    label="Text to image",
    capability="image-generation",
    output_media="image/png",
    params=(
        *PROMPT_PARAMS,
        Param("width", "int", "Width", default=1024, min=64, max=4096, step=8),
        Param("height", "int", "Height", default=1024, min=64, max=4096, step=8),
        *SAMPLING_PARAMS,
        *LORA_PARAMS,
    ),
)

IMG2IMG = Operation(
    key="img2img",
    label="Image to image",
    capability="image-generation",
    output_media="image/png",
    params=(
        *PROMPT_PARAMS,
        # The image the generation starts from. Its own dimensions decide
        # the output size, which is why this operation declares no
        # width/height: inventing a size here would silently rescale the
        # operator's picture.
        Param("init_image", "file", "Init image", accept="image/*", required=True),
        # How much of the init image to throw away. The one parameter that
        # makes this mode itself: 0 returns the input, 1 ignores it.
        Param("denoise", "float", "Denoise", default=0.6, min=0.0, max=1.0, step=0.05),
        *SAMPLING_PARAMS,
        *LORA_PARAMS,
    ),
)

INPAINT = Operation(
    key="inpaint",
    label="Inpaint",
    capability="image-generation",
    output_media="image/png",
    params=(
        *PROMPT_PARAMS,
        Param("init_image", "file", "Image", accept="image/*", required=True),
        # Which way round a mask reads is the one thing an operator cannot
        # guess, so the label answers it where it is read.
        Param("mask_image", "file", "Mask (white = repaint)", accept="image/*", required=True),
        # Feathering the mask outward hides the seam. ComfyUI's own
        # `VAEEncodeForInpaint` input, exposed rather than hardcoded.
        Param("mask_grow", "int", "Grow mask by", default=6, min=0, max=64),
        # Unlike img2img, the masked area is being REPLACED, so the honest
        # default is full denoise; lowering it keeps some of what was there.
        Param("denoise", "float", "Denoise", default=1.0, min=0.0, max=1.0, step=0.05),
        *SAMPLING_PARAMS,
        *LORA_PARAMS,
    ),
)
```

- [ ] **Step 4: Run the operation tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_operations.py -q`
Expected: PASS (the whole file, including the pre-existing `TestImg2ImgSchema`/`TestInpaintSchema`/`TestLoraParams` classes — they pin the same values from the other side).

- [ ] **Step 5: Run the vision suite for form/label fallout**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Expected: PASS.

- [ ] **Step 6: Update the README**

In `modules/vision/README.md`, in the "The operation registry, and how to add an operation" section, extend the sentence that names `LORA_PARAMS` (or add one beside it):

```markdown
The params three modes share are declared once and spliced in: `PROMPT_PARAMS`
(prompt, negative prompt), `SAMPLING_PARAMS` (steps, CFG, seed, sampler,
scheduler, batch size), and `LORA_PARAMS` (LoRAs, strength). They are two
tuples rather than one because the modes differ in the middle — txt2img's
width/height, img2img's init image, inpaint's mask — and declaration order is
form field order.
```

- [ ] **Step 7: Commit**

```bash
git add core/inference/operations.py modules/vision/tests/test_operations.py modules/vision/README.md
git commit -m "$(cat <<'EOF'
refactor(operations): one definition of the prompt and sampling params

The eight params txt2img, img2img and inpaint shared were three
byte-identical copies. Extracted as PROMPT_PARAMS + SAMPLING_PARAMS and
spliced in, the same treatment LORA_PARAMS already had. Two tuples, not
one: the modes differ in the middle, and declaration order is form field
order.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 2: `description` on `Param` and `Operation`

Audit item 8. The semantic hints ("denoise: 0 returns the input, 1 ignores it") exist only as Python comments, where no caller can read them.

**Ruling (scope):** the field and its values only. Descriptions are **not** wired into `forms.py` `help_text` in this plan — that slot already carries situational copy that must win over static schema prose (the seed's "Leave blank for a random seed.", and the asset fields' "No upscale model is available — …"), so merging two sources into one slot is a UI design question, not this cleanup. `describe()` (Task 3) is the consumer.

**Files:**
- Modify: `core/inference/operations.py` (`Param`, `Operation`, all four operations, both shared tuples)
- Test: `modules/vision/tests/test_operations.py`

**Interfaces:**
- Consumes: `PROMPT_PARAMS`, `SAMPLING_PARAMS` (Task 1).
- Produces: `Param.description: str = ""`, `Operation.description: str = ""` — both **last** in their dataclass field order, so no positional construction anywhere breaks.

- [ ] **Step 1: Write the failing test**

Append to `modules/vision/tests/test_operations.py`:

```python
class TestParamDescriptions:
    """Semantic hints belong in the schema, not in Python comments: the
    same sentence has to reach a form's help text, a `?format=json`
    schema, and a tool's parameter documentation."""

    def test_a_param_defaults_to_no_description(self):
        assert Param("x", "text", "X").description == ""

    def test_an_operation_defaults_to_no_description(self):
        assert Operation(
            key="k", label="L", capability="image-generation",
            params=(), output_media="image/png",
        ).description == ""

    def test_every_shipped_operation_describes_itself(self):
        for operation in (TXT2IMG, IMG2IMG, INPAINT, UPSCALE):
            assert operation.description, f"{operation.key} has no description"

    def test_every_param_of_every_shipped_operation_describes_itself(self):
        for operation in (TXT2IMG, IMG2IMG, INPAINT, UPSCALE):
            for param in operation.params:
                assert param.description, f"{operation.key}.{param.key} has no description"

    def test_denoise_says_what_its_ends_mean(self):
        """The hint the audit named: the one parameter an operator cannot
        guess from its label."""
        assert "0 returns the input" in IMG2IMG.param("denoise").description

    def test_inpaints_denoise_describes_its_own_default_not_img2imgs(self):
        assert IMG2IMG.param("denoise").description != INPAINT.param("denoise").description
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_operations.py::TestParamDescriptions -q`
Expected: FAIL — `TypeError: Param.__init__() got an unexpected keyword argument 'description'` / `AttributeError: 'Param' object has no attribute 'description'`.

- [ ] **Step 3: Add the fields**

In `core/inference/operations.py`, `Param` — add as the LAST field, after `multiple`:

```python
    multiple: bool = False
    # Last in the field order deliberately: every existing construction
    # passes `key`, `kind`, `label` positionally and the rest by keyword,
    # so a field added anywhere earlier would silently change what a
    # positional fourth argument means.
    description: str = ""
```

`Operation` — add as the LAST field, after `output_media`:

```python
    output_media: str
    description: str = ""
```

- [ ] **Step 4: Fill in every description**

`PROMPT_PARAMS`:

```python
PROMPT_PARAMS = (
    Param(
        "prompt", "text", "Prompt", default="", required=True,
        description=(
            "What to generate. Describe the subject, the setting, and the style; most "
            "checkpoints respond well to comma-separated phrases."
        ),
    ),
    Param(
        "negative_prompt", "text", "Negative prompt", default="",
        description="What to keep out of the image — subjects, styles, or artifacts you don't want.",
    ),
)
```

`SAMPLING_PARAMS`:

```python
SAMPLING_PARAMS = (
    Param(
        "steps", "int", "Steps", default=25, min=1, max=150,
        description=(
            "How many denoising steps to run. More steps cost more time; most checkpoints "
            "stop improving somewhere past 30-40."
        ),
    ),
    Param(
        "cfg_scale", "float", "CFG scale", default=7.0, min=0, max=30, step=0.5,
        description=(
            "How strictly to follow the prompt. Low values wander off it, high values "
            "over-bake the image; 5-8 suits most checkpoints."
        ),
    ),
    Param(
        "seed", "seed", "Seed", default=None,
        description=(
            "The random seed. Leave it blank for a new one every run; reuse a finished "
            "job's seed to reproduce that job exactly."
        ),
    ),
    Param(
        "sampler", "choice", "Sampler",
        description=(
            "The sampling algorithm, named as the bound engine names it. The options are "
            "read from that engine, never from a list this platform ships."
        ),
    ),
    Param(
        "scheduler", "choice", "Scheduler",
        description=(
            "The noise schedule the sampler follows, named as the bound engine names it. "
            "Read from that engine, like the sampler."
        ),
    ),
    Param(
        "batch_size", "int", "Batch size", default=1, min=1, max=8,
        description=(
            "How many images this one submission produces. They share the prompt and every "
            "other setting, including the seed the job records."
        ),
    ),
)
```

`LORA_PARAMS`:

```python
LORA_PARAMS = (
    Param(
        "loras", "asset", "LoRAs", asset_kind="lora", multiple=True,
        description=(
            "Style or subject adapters to apply on top of the checkpoint, chosen from what "
            "the engine reports having installed. Selecting none is a normal answer."
        ),
    ),
    Param(
        "lora_strength", "float", "LoRA strength", default=1.0, min=0.0, max=2.0, step=0.05,
        description=(
            "How strongly to apply every selected LoRA — one strength covers the whole "
            "selection: 0 disables them, 1 applies them fully."
        ),
    ),
)
```

`TXT2IMG`'s own two params and its operation description:

```python
TXT2IMG = Operation(
    key="txt2img",
    label="Text to image",
    capability="image-generation",
    output_media="image/png",
    description="Generate an image from a text prompt alone.",
    params=(
        *PROMPT_PARAMS,
        Param(
            "width", "int", "Width", default=1024, min=64, max=4096, step=8,
            description=(
                "Output width in pixels. Stay near the checkpoint's native resolution "
                "(512 for SD 1.5, 1024 for SDXL); most engines need a multiple of 8."
            ),
        ),
        Param(
            "height", "int", "Height", default=1024, min=64, max=4096, step=8,
            description="Output height in pixels. Same rule as width — native resolution, multiple of 8.",
        ),
        *SAMPLING_PARAMS,
        *LORA_PARAMS,
    ),
)
```

`IMG2IMG`'s own two params and its operation description (the existing comments above them stay — they explain the *design ruling*; the descriptions explain the *parameter*):

```python
    description=(
        "Generate a new image guided by an existing one, keeping as much of the original "
        "as the denoise setting allows."
    ),
    ...
        Param(
            "init_image", "file", "Init image", accept="image/*", required=True,
            description=(
                "The image this generation starts from. Its own dimensions decide the "
                "output size, which is why this mode has no width or height."
            ),
        ),
        Param(
            "denoise", "float", "Denoise", default=0.6, min=0.0, max=1.0, step=0.05,
            description=(
                "How much of the init image to throw away: 0 returns the input untouched, "
                "1 ignores it entirely. 0.4-0.7 keeps the composition while changing the content."
            ),
        ),
```

`INPAINT`'s own params and description:

```python
    description=(
        "Repaint the masked area of an existing image, leaving everything outside the mask "
        "untouched."
    ),
    ...
        Param(
            "init_image", "file", "Image", accept="image/*", required=True,
            description="The image to repaint part of. Everything outside the mask is preserved.",
        ),
        Param(
            "mask_image", "file", "Mask (white = repaint)", accept="image/*", required=True,
            description=(
                "A black-and-white image the same size as the picture: white marks what to "
                "repaint, black is left alone."
            ),
        ),
        Param(
            "mask_grow", "int", "Grow mask by", default=6, min=0, max=64,
            description=(
                "Pixels to expand the mask outward before repainting. A few pixels of growth "
                "hides the seam at the edge of the masked area."
            ),
        ),
        Param(
            "denoise", "float", "Denoise", default=1.0, min=0.0, max=1.0, step=0.05,
            description=(
                "How much of the masked area to throw away: 1 replaces it entirely, lower "
                "values keep some of what was there. The default is full denoise here, "
                "unlike image-to-image, because the masked area is being replaced."
            ),
        ),
```

`UPSCALE`:

```python
    description=(
        "Enlarge an existing image with an upscaling model. No prompt and no seed — "
        "nothing about it is random."
    ),
    ...
        Param(
            "init_image", "file", "Image", accept="image/*", required=True,
            description=(
                "The image to enlarge. Nothing is regenerated — the upscale model resamples "
                "what is already there."
            ),
        ),
        Param(
            "upscale_model", "asset", "Upscale model", asset_kind="upscale_model", required=True,
            description=(
                "The upscaling model to run, chosen from what the engine reports having "
                "installed (ESRGAN-family weights and the like)."
            ),
        ),
```

- [ ] **Step 5: Run the tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Expected: PASS.

- [ ] **Step 6: Update the README**

In `modules/vision/README.md`, "The operation registry, and how to add an operation", add to the `Param` field list (or as a new bullet):

```markdown
- `description` — one or two sentences saying what the parameter *means*, for a
  reader who cannot see the code: what a value does at each end of its range,
  where the options come from. Required in practice for every shipped param
  (`test_operations.py::TestParamDescriptions` fails a param that has none) and
  serialized by `operations.describe()` for schema consumers. The form layer does
  not render it today — `help_text` already carries situational copy (a blank
  seed, an asset the engine has none of) that must win over static schema prose.
```

- [ ] **Step 7: Commit**

```bash
git add core/inference/operations.py modules/vision/tests/test_operations.py modules/vision/README.md
git commit -m "$(cat <<'EOF'
feat(operations): describe every param and operation in the schema

The semantic hints an operator (or a tool) needs -- what denoise's ends
mean, where sampler options come from, why inpaint defaults to full
denoise -- lived only as Python comments. They are schema data now, on
Param.description and Operation.description, with a test that fails any
shipped param that carries none.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 3: `operations.describe()` — the machine-readable schema

Audit item 2, first half: "the one thing an agent cannot get today is what to send." Pure function in `core`, no Django, no engine.

**Files:**
- Modify: `core/inference/operations.py` (add after `operations_for`)
- Test: `modules/vision/tests/test_operations.py`

**Interfaces:**
- Consumes: `Param.description`, `Operation.description` (Task 2).
- Produces: `core.inference.operations.describe(operation: Operation) -> dict` — JSON-safe, `{"key","label","description","capability","output_media","params":[…]}`; every param dict carries one key per `Param` dataclass field. Task 4 layers live engine options onto this.

- [ ] **Step 1: Write the failing test**

Append to `modules/vision/tests/test_operations.py` (add `describe` to the module's import block, and `import dataclasses` at the top):

```python
class TestDescribe:
    """The schema an agent reads to learn what to send. Pure data: no
    engine, no DB, no Django."""

    def test_it_names_the_operation_and_its_media(self):
        described = describe(UPSCALE)
        assert described["key"] == "upscale"
        assert described["label"] == "Upscale"
        assert described["capability"] == "image-generation"
        assert described["output_media"] == "image/png"
        assert described["description"] == UPSCALE.description

    def test_it_is_json_safe(self):
        for operation in (TXT2IMG, IMG2IMG, INPAINT, UPSCALE):
            assert json.loads(json.dumps(describe(operation))) == describe(operation)

    def test_every_param_field_is_serialized(self):
        """Drift-proof: a field added to `Param` and not to `describe` is a
        field no schema consumer can see, so the test reads the dataclass
        itself rather than a hand-written list."""
        described = describe(IMG2IMG)
        expected = {field.name for field in dataclasses.fields(Param)}
        for param in described["params"]:
            assert set(param) == expected

    def test_params_keep_declaration_order(self):
        assert [param["key"] for param in describe(UPSCALE)["params"]] == [
            "init_image", "upscale_model",
        ]

    def test_a_choice_param_with_engine_reported_options_reports_no_choices(self):
        """`sampler` has no fixed `choices` -- the engine owns them. The
        schema says so honestly (an empty list) rather than inventing one;
        `services.operation_catalog` is what fills them in live."""
        sampler = next(p for p in describe(TXT2IMG)["params"] if p["key"] == "sampler")
        assert sampler["choices"] == []
        assert sampler["kind"] == "choice"

    def test_an_asset_param_reports_its_asset_kind_and_multiplicity(self):
        loras = next(p for p in describe(TXT2IMG)["params"] if p["key"] == "loras")
        assert (loras["asset_kind"], loras["multiple"], loras["required"]) == ("lora", True, False)

    def test_a_file_param_reports_what_it_accepts(self):
        init = next(p for p in describe(IMG2IMG)["params"] if p["key"] == "init_image")
        assert (init["kind"], init["accept"], init["required"]) == ("file", "image/*", True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_operations.py::TestDescribe -q`
Expected: FAIL — `ImportError: cannot import name 'describe'`.

- [ ] **Step 3: Implement `describe`**

In `core/inference/operations.py`, after `operations_for`:

```python
def describe(operation: Operation) -> dict:
    """`operation` as plain, JSON-safe data -- the machine-readable answer
    to "what do I send?".

    The schema dataclasses are the platform's generation vocabulary; this
    is the one place that turns them into data a caller who is not Python
    can read: a `?format=json` schema endpoint, a chatbot tool's parameter
    list, an MCP tool definition. Pure, like everything else in this
    module -- it takes no engine, so it reports what the SCHEMA knows and
    nothing about a particular install. `modules.vision.services.
    operation_catalog` is what layers a bound engine's live option lists
    on top.

    A `"choice"` param whose options the engine owns reports `choices: []`
    -- an honest "the schema does not know", never a guessed list.
    """
    return {
        "key": operation.key,
        "label": operation.label,
        "description": operation.description,
        "capability": operation.capability,
        "output_media": operation.output_media,
        "params": [_describe_param(param) for param in operation.params],
    }


def _describe_param(param: Param) -> dict:
    """One `Param` as JSON-safe data, one key per dataclass field.

    Written out explicitly rather than via `dataclasses.asdict` so the
    tuple-valued `choices` becomes a list (JSON has no tuple) and so a
    field added to `Param` without a decision about how it serializes
    fails a test instead of silently appearing in a public contract.
    """
    return {
        "key": param.key,
        "kind": param.kind,
        "label": param.label,
        "description": param.description,
        "default": param.default,
        "min": param.min,
        "max": param.max,
        "step": param.step,
        "choices": list(param.choices),
        "asset_kind": param.asset_kind,
        "accept": param.accept,
        "required": param.required,
        "multiple": param.multiple,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_operations.py -q`
Expected: PASS.

- [ ] **Step 5: Update the README**

In `modules/vision/README.md`, "The operation registry, and how to add an operation", add:

```markdown
`core.inference.operations.describe(operation)` returns the schema as JSON-safe
data — `{"key", "label", "description", "capability", "output_media", "params"}`,
one dict per param carrying every `Param` field. It is pure: it knows nothing
about a bound engine, so a `"choice"` param whose options the engine owns comes
back with `"choices": []`. `services.operation_catalog()` is the version with a
live engine's options filled in.
```

- [ ] **Step 6: Commit**

```bash
git add core/inference/operations.py modules/vision/tests/test_operations.py modules/vision/README.md
git commit -m "$(cat <<'EOF'
feat(operations): serialize an operation's schema with describe()

The registry held everything a caller needs to know what to send and had
no way to hand it over. describe() is that: pure, JSON-safe, one key per
Param field, with a test that reads the dataclass so a new field cannot
quietly go missing from the contract.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 4: `live_options` moves to services; `operation_catalog()` layers it on `describe()`

Audit item 2, second half, plus the gap table's "Live choices / asset lists — only a view calls it". `views.live_options` is generation behaviour living in the HTTP layer; `views.py` is supposed to hold none.

**Ruling:** `operation_catalog()` returns a **list of described operations**, not a wrapper carrying readiness. A caller that needs to know whether the engine is answering calls `preflight()` — the function that exists to answer exactly that. `operation_catalog()` calls `preflight()` **once** for the whole catalog rather than per operation, because a health check is a blocking round trip (the same reasoning `AskView._precheck_models` uses when it dedups health checks by endpoint).

**Files:**
- Modify: `modules/vision/services.py` (add `live_options`, `operation_catalog`), `modules/vision/views.py:108-148` (delete `live_options`; call `services.live_options` at both call sites, lines ~180 and ~475)
- Test: `modules/vision/tests/test_services.py`

**Interfaces:**
- Consumes: `describe()` (Task 3), `services.preflight()`.
- Produces:
  - `services.live_options(operation: Operation, resolved: ResolvedModel | None) -> dict[str, tuple[str, ...]]` — moved verbatim, same behaviour, same never-raises contract.
  - `services.operation_catalog() -> list[dict]` — `describe()` for every registered `image-generation` operation, with `"options": [...]` added to each param whose options the engine owns.

- [ ] **Step 1: Write the failing test**

Append to `modules/vision/tests/test_services.py` (follow the file's existing import style; it already imports `services`, `StubEngine`, `StubGenerator`, `clear_bindings`, `ENGINES`, `patch.dict`):

```python
@pytest.mark.django_db
class TestLiveOptions:
    """Moved out of `views.py` unchanged: an engine-reported option list is
    generation behaviour, and the page is supposed to hold none."""

    def test_no_binding_reports_nothing_without_touching_an_engine(self):
        assert services.live_options(TXT2IMG, None) == {}

    def test_a_choice_param_gets_the_engines_list(self):
        engine = StubEngine(StubGenerator())
        engine.list_choices = lambda endpoint, key: ("euler", "dpmpp_2m") if key == "sampler" else ()
        resolved = ResolvedModel(
            engine="stubengine", model_id="m.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with _registered(engine):
            options = services.live_options(TXT2IMG, resolved)
        assert options["sampler"] == ("euler", "dpmpp_2m")

    def test_one_failing_param_degrades_alone_and_never_raises(self):
        def boom(endpoint, key):
            if key == "sampler":
                raise RuntimeError("engine said no")
            return ("normal",)

        engine = StubEngine(StubGenerator())
        engine.list_choices = boom
        resolved = ResolvedModel(
            engine="stubengine", model_id="m.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with _registered(engine):
            options = services.live_options(TXT2IMG, resolved)
        assert options["sampler"] == ()
        assert options["scheduler"] == ("normal",)


@pytest.mark.django_db
class TestOperationCatalog:
    """The schema a tool caller reads, with a live engine's option lists
    filled in -- `describe()` plus the one thing `describe()` cannot know."""

    def test_it_lists_every_registered_image_generation_operation(self):
        keys = [entry["key"] for entry in services.operation_catalog()]
        assert keys == [operation.key for operation in operations_for(IMAGE_GENERATION_CAPABILITY)]

    def test_each_entry_is_describe_output(self):
        entry = next(e for e in services.operation_catalog() if e["key"] == "txt2img")
        described = describe(TXT2IMG)
        assert entry["label"] == described["label"]
        assert entry["description"] == described["description"]
        assert [p["key"] for p in entry["params"]] == [p["key"] for p in described["params"]]

    def test_engine_reported_options_are_filled_in_live(self):
        _bind()
        engine = StubEngine(StubGenerator())
        engine.list_choices = lambda endpoint, key: ("euler",) if key == "sampler" else ()
        with _registered(engine):
            entry = next(e for e in services.operation_catalog() if e["key"] == "txt2img")
        sampler = next(p for p in entry["params"] if p["key"] == "sampler")
        assert sampler["options"] == ["euler"]

    def test_a_param_the_engine_does_not_own_carries_no_options_key(self):
        _bind()
        engine = StubEngine(StubGenerator())
        engine.list_choices = lambda endpoint, key: ()
        with _registered(engine):
            entry = next(e for e in services.operation_catalog() if e["key"] == "txt2img")
        steps = next(p for p in entry["params"] if p["key"] == "steps")
        assert "options" not in steps

    def test_it_is_json_safe(self):
        assert json.loads(json.dumps(services.operation_catalog())) == services.operation_catalog()

    def test_nothing_bound_still_returns_the_schema_with_no_options(self):
        """An unbound role is not an error here: the shapes are still true,
        only the engine's lists are missing. Readiness is `preflight`'s
        question, and this function does not pretend to answer it."""
        catalog = services.operation_catalog()
        assert catalog
        for entry in catalog:
            for param in entry["params"]:
                assert param.get("options", []) == []
```

`test_services.py` already provides everything these tests bind with: `_bind(engine_name="stubengine")` (line 53), `_registered(engine)` (line 62, the `patch.dict(ENGINES, ...)` wrapper this file uses), and MODULE-LEVEL autouse `_clear_bindings`/`_reset_engine_caches` fixtures (lines 40-49) that already cover every class in the file — do not add per-class copies of them. `json`, `ENGINES`, `Operation`, `Param`, `ParamError`, `JobStatus`, `GenerationRejected`, `SimpleUploadedFile`, `override_settings`, `JobInput` and `stored_output` are already imported there too; add only `ResolvedModel`, `TXT2IMG`, `describe`, `operations_for`, `IMAGE_GENERATION_CAPABILITY`, `reverse`, `timezone` and `timedelta`.

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_services.py::TestOperationCatalog -q`
Expected: FAIL — `AttributeError: module 'modules.vision.services' has no attribute 'operation_catalog'`.

- [ ] **Step 3: Move `live_options` into services and add `operation_catalog`**

Cut `live_options` out of `modules/vision/views.py` (lines 108–148, the whole function and its docstring) and paste it into `modules/vision/services.py`, after `preflight`. Import changes in `services.py`, exactly: `get_engine` is already imported (line 26) and so is `VISION_GENERATE_ROLE` (line 29) — add only `describe` and `operations_for` to the existing `from core.inference.operations import (...)` line, and `IMAGE_GENERATION_CAPABILITY` to the existing `from core.inference.roles import ...` line. Then append below it:

```python
def operation_catalog() -> list[dict]:
    """Every operation this platform can generate with, as data, with the
    BOUND engine's live option lists filled in.

    `core.inference.operations.describe` answers "what does the schema
    say"; only an engine can answer "what does this install actually
    have" (`sampler`, `scheduler`, the LoRAs and upscalers on disk). This
    is those two answers in one object -- the thing a tool reads before it
    submits, and the thing a `?format=json` schema endpoint would return.

    `preflight()` is called ONCE for the whole catalog, not once per
    operation: a health check is a blocking HTTP round trip (up to seconds
    when an engine is down), and every operation would resolve the same
    role and check the same endpoint -- the same dedup reasoning
    `modules.rag.views.AskView._precheck_models` applies to its two roles.

    Readiness is deliberately NOT part of the return value: `preflight()`
    is the function that answers "is the engine there", and a caller that
    needs to know asks it. With nothing bound, every shape here is still
    true -- only the engine's lists are missing, and a param whose options
    the engine owns simply carries none.
    """
    check = preflight()
    catalog = []
    for operation in operations_for(IMAGE_GENERATION_CAPABILITY):
        entry = describe(operation)
        options = live_options(operation, check.resolved)
        for param in entry["params"]:
            if param["key"] in options:
                param["options"] = list(options[param["key"]])
        catalog.append(entry)
    return catalog
```

- [ ] **Step 4: Point the view's two call sites at services**

In `modules/vision/views.py`:
- `CreatePageView.get_context_data` (~line 180): `live_options(operation, check.resolved)` → `services.live_options(operation, check.resolved)`.
- `build_form_for` (~line 475): same substitution.
- Delete the now-unused `from core.inference.engines import get_engine` import if nothing else in `views.py` uses it (grep first).
- In the module docstring, the line "The page holds NO generation logic" now needs no caveat — leave it, it just became true again.

- [ ] **Step 5: Run the tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Expected: PASS. (`test_forms.py` and `test_views_generate.py` mention `live_options` only in docstrings — update those docstrings to say `services.live_options`.)

- [ ] **Step 6: Update the README**

In `modules/vision/README.md`, "The service layer — the seam a chatbot tool will call", add two bullets after `preflight`:

```markdown
- **`live_options(operation, resolved) -> {param key: (option, ...)}`** — the bound
  engine's own lists for every param whose options it owns: a `"choice"` param with
  no fixed `choices` (samplers, schedulers) and every `"asset"` param (LoRAs,
  upscalers). Never raises — an unbound role reports `{}`, and a single param whose
  listing fails reports `()` so the form degrades for that field alone. It lived in
  `views.py` until the tool-readiness pass; a view was the only caller, and it was
  the last piece of generation behaviour outside this module.
- **`operation_catalog() -> [dict, ...]`** — `operations.describe()` for every
  registered image-generation operation with those live lists merged in as
  `"options"`. One `preflight()` for the whole catalog. Readiness is not in the
  return value: `preflight()` answers that question, and this one does not pretend to.
```

- [ ] **Step 7: Commit**

```bash
git add modules/vision/services.py modules/vision/views.py modules/vision/tests/test_services.py modules/vision/tests/test_forms.py modules/vision/tests/test_views_generate.py modules/vision/README.md
git commit -m "$(cat <<'EOF'
refactor(vision): live options belong to services, with a catalog on top

live_options was generation behaviour sitting in views.py, reachable
only by rendering a page. It moves to services unchanged, and
operation_catalog() layers it onto operations.describe() -- the schema a
tool reads before it submits, with the bound engine's own sampler,
scheduler and asset lists filled in and one preflight for the lot.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 5: `job_json` moves to services as the canonical job representation

Audit item 3. `views._job_json` is documented as "the shape the future chatbot tool reads" while living in a view, so a non-view caller cannot reach it.

**Ruling on `unreachable`:** the transient attribute stays, read with `getattr(job, "unreachable", False)`, and the docstring now says why plainly — `refresh_job` sets it on EVERY call before anything else, so any caller that refreshed has a real value, and a row read straight from the DB honestly reports `False` (nothing has tried to reach the engine for it yet). Promoting it to a column would make a network hiccup part of the job's durable record, which `refresh_job`'s whole contract refuses.

**Files:**
- Modify: `modules/vision/services.py` (add `job_json`), `modules/vision/views.py` (delete `_job_json`, call `services.job_json` from `job_status`)
- Test: `modules/vision/tests/test_services.py`, `modules/vision/tests/test_views_generate.py` (existing JSON-endpoint tests stay, unchanged)

**Interfaces:**
- Produces: `services.job_json(job: GenerationJob) -> dict` — `{"id","operation","status","error","seed","params","engine","model_id","created_at","finished_at","stale","unreachable","outputs":[{"id","url","media_type","width","height"}],"inputs":[{"id","param_key","url","media_type"}]}`. Task 8 adds `"failure_kind"` and `"queue_job_id"`; Task 10 reads `["outputs"][*]["url"]`.

- [ ] **Step 1: Write the failing test**

Append to `modules/vision/tests/test_services.py`:

```python
@pytest.mark.django_db
class TestJobJson:
    """One job contract for HTML, for `?format=json`, and for the queue
    result -- which means it cannot live in a view."""

    def test_it_reports_the_jobs_own_facts(self, tmp_path):
        output = stored_output(tmp_path)
        data = services.job_json(output.job)
        assert data["id"] == str(output.job.id)
        assert data["operation"] == "txt2img"
        assert data["status"] == GenerationJob.Status.DONE
        assert data["seed"] == 42
        assert data["engine"] == "stubengine"
        assert data["params"]["prompt"] == "a lighthouse"

    def test_outputs_are_named_by_url_never_by_path(self, tmp_path):
        output = stored_output(tmp_path)
        data = services.job_json(output.job)
        assert data["outputs"] == [
            {
                "id": output.id,
                "url": reverse("vision-output-file", args=[output.id]),
                "media_type": "image/png",
                "width": 512,
                "height": 512,
            }
        ]
        assert "path" not in json.dumps(data)

    def test_it_is_json_safe(self, tmp_path):
        data = services.job_json(stored_output(tmp_path).job)
        assert json.loads(json.dumps(data)) == data

    def test_a_row_that_was_never_refreshed_reports_reachable(self, tmp_path):
        """The transient attribute `refresh_job` sets is read with a
        default, so a caller that never polled gets `False` -- honest:
        nothing has tried to reach the engine for this row yet."""
        job = stored_output(tmp_path).job
        assert not hasattr(job, "unreachable")
        assert services.job_json(job)["unreachable"] is False

    def test_a_refreshed_unreachable_job_says_so(self):
        job = _queued_job()
        # `StubGenerator` raises a scripted state that IS an exception
        # (`_helpers.py:338`) -- the file's own way of saying "the engine
        # could not be reached this poll".
        generator = StubGenerator(states=[RuntimeError("engine down")])
        with _registered(StubEngine(generator)):
            job = services.refresh_job(job)
        assert services.job_json(job)["unreachable"] is True

    def test_inputs_are_listed_with_their_param_and_url(self, tmp_path):
        job = stored_output(tmp_path).job
        job_input = JobInput.objects.create(
            job=job, param_key="init_image",
            path=str(tmp_path / "in.png"), media_type="image/png",
        )
        data = services.job_json(job)
        assert data["inputs"] == [
            {
                "id": job_input.id,
                "param_key": "init_image",
                "url": reverse("vision-input-file", args=[job_input.id]),
                "media_type": "image/png",
            }
        ]
```

Add ONE module-level helper to `test_services.py` (the file has no equivalent; every class below reuses it):

```python
def _queued_job():
    """A submitted, not-yet-finished job -- the row `refresh_job` polls."""
    return GenerationJob.objects.create(
        operation="txt2img", params=dict(RAW), seed=42, engine="stubengine",
        model_id="stub.safetensors", endpoint="http://stub:9999",
        model_fingerprint="f", engine_ref="p-1",
        status=GenerationJob.Status.QUEUED,
    )
```

and add `reverse` to the imports (`JobInput` and `stored_output` are already imported there).

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_services.py::TestJobJson -q`
Expected: FAIL — `AttributeError: module 'modules.vision.services' has no attribute 'job_json'`.

- [ ] **Step 3: Move the function**

Delete `_job_json` from `modules/vision/views.py` and add to `modules/vision/services.py` (after `refresh_job`, before `wait_for`); add `from django.urls import reverse` to the services imports (already present):

```python
def job_json(job: GenerationJob) -> dict:
    """One job as plain data -- the canonical representation every caller
    reads: the page's `?format=json`, the queue job's result
    (`modules.vision.jobs.run_generate`), and the chatbot tool.

    Paths are never exposed; a file is named by the URL that serves it, so
    a caller holds a handle to the platform's copy and never a location on
    a disk it cannot see.

    `unreachable` is the TRANSIENT attribute `refresh_job` sets, read with
    a default rather than assumed: `refresh_job` sets it on every call
    before anything else, so a caller that polled has a real value, and a
    row read straight out of the database honestly reports `False` --
    nothing has tried to reach the engine for it yet. It is not a column
    and must not become one: a network hiccup is not part of a job's
    durable record (see `refresh_job`).
    """
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
        "inputs": [
            {
                "id": job_input.id,
                "param_key": job_input.param_key,
                "url": reverse("vision-input-file", args=[job_input.id]),
                "media_type": job_input.media_type,
            }
            for job_input in job.inputs.all()
        ],
    }
```

In `views.job_status`, replace `JsonResponse(_job_json(job))` with `JsonResponse(services.job_json(job))` and update its docstring reference from `_job_json` to `services.job_json`.

- [ ] **Step 4: Run the tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Expected: PASS — including the existing `?format=json` view tests, which must not need editing (the shape is unchanged).

- [ ] **Step 5: Update the README**

In `modules/vision/README.md`, "The service layer", add:

```markdown
- **`job_json(job) -> dict`** — one job as plain data: the shape `?format=json`
  returns, the shape the queue result reports, and the shape a tool reads. Files
  are named by their serving URL, never by a path. `unreachable` is the transient
  attribute `refresh_job` sets (never a column) read with a default, so a row that
  was never polled honestly reports `False`.
```

- [ ] **Step 6: Commit**

```bash
git add modules/vision/services.py modules/vision/views.py modules/vision/tests/test_services.py modules/vision/README.md
git commit -m "$(cat <<'EOF'
refactor(vision): the canonical job representation lives in services

_job_json documented itself as the shape a chatbot tool reads while
sitting in views.py, where only an HTTP request could reach it. It is
services.job_json now -- one job contract for HTML, ?format=json and the
queue result -- with the transient `unreachable` attribute's default read
spelled out rather than left as a quiet getattr.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 6: One input-reference resolver

Audit item 4: `jobs._payload_files` refuses refs naming an undeclared file param; `views.generate` silently ignores them and applies its own rule. Two half-copies, two semantics.

**Ruling (which half wins):** the **strict** one. A reference naming a param the operation does not declare is refused, never dropped — silently ignoring it runs a generation that is not the one that was asked for.

**Ruling (error placement):** the page attaches a bad reference's message to the field it belongs to. To keep that with one resolver, `resolve_inputs` raises `services.InputReferenceError` (a `ValueError` subclass) carrying `.param_key`, so the view still calls `form.add_error(exc.param_key, ...)` and a non-form caller still catches plain `ValueError`.

**Ruling (the "attached file wins" rule):** that rule is page UX — an operator attaching a new file is changing their mind — and stays in the view, where the request's uploads and carried refs are both visible. `resolve_inputs` sees one reference per param and has no opinion about it.

**Files:**
- Modify: `modules/vision/services.py` (add `InputReferenceError`, `resolve_inputs`), `modules/vision/jobs.py` (delete `_payload_files`, call `services.resolve_inputs`), `modules/vision/views.py::generate` (replace its per-ref loop)
- Test: `modules/vision/tests/test_services.py`, `modules/vision/tests/test_jobs.py`, `modules/vision/tests/test_views_generate.py`

**Interfaces:**
- Produces:
  - `services.InputReferenceError(ValueError)` with `.param_key: str` and the operator-facing message as `str(exc)`.
  - `services.resolve_inputs(operation: Operation, references: dict[str, str]) -> dict[str, store.StoredFile]` — raises `InputReferenceError` for an undeclared param key, for a malformed reference, for a row that does not exist, and for a row whose file is gone.
- Task 13's view calls it to VALIDATE carried refs before enqueuing (the returned `StoredFile`s are lazy handles — nothing is read — and are discarded on that path).

- [ ] **Step 1: Write the failing test**

Append to `modules/vision/tests/test_services.py`:

```python
@pytest.mark.django_db
class TestResolveInputs:
    """One resolver, one semantics: the page, the queued job and a tool
    all get the same answer for the same reference."""

    OPERATION = Operation(
        key="img2img", label="Image to image", capability="image-generation",
        output_media="image/png",
        params=(
            Param("prompt", "text", "Prompt", default="", required=True),
            Param("init_image", "file", "Init image", accept="image/*", required=True),
        ),
    )

    def test_no_references_is_no_files_and_no_queries(self):
        assert services.resolve_inputs(self.OPERATION, {}) == {}

    def test_a_reference_resolves_to_the_stored_bytes(self, tmp_path):
        output = stored_output(tmp_path)
        files = services.resolve_inputs(
            self.OPERATION, {"init_image": f"output:{output.id}"}
        )
        assert list(files) == ["init_image"]
        assert b"".join(files["init_image"].chunks()) == PNG

    def test_an_undeclared_param_is_refused_not_dropped(self, tmp_path):
        """Silently ignoring it would run a generation that is not the one
        that was asked for."""
        output = stored_output(tmp_path)
        with pytest.raises(services.InputReferenceError) as caught:
            services.resolve_inputs(self.OPERATION, {"mask_image": f"output:{output.id}"})
        assert "declares no file parameter named mask_image" in str(caught.value)
        assert caught.value.param_key == "mask_image"

    def test_a_malformed_reference_names_the_param_it_came_from(self):
        with pytest.raises(services.InputReferenceError) as caught:
            services.resolve_inputs(self.OPERATION, {"init_image": "beach.png"})
        assert caught.value.param_key == "init_image"
        assert "output:<id> or input:<id>" in str(caught.value)

    def test_a_reference_whose_file_is_gone_is_refused(self, tmp_path):
        output = stored_output(tmp_path)
        os.remove(output.path)
        with pytest.raises(services.InputReferenceError) as caught:
            services.resolve_inputs(self.OPERATION, {"init_image": f"output:{output.id}"})
        assert caught.value.param_key == "init_image"
        assert "no longer on disk" in str(caught.value)

    def test_it_is_still_a_value_error_for_a_caller_with_no_form(self):
        with pytest.raises(ValueError):
            services.resolve_inputs(self.OPERATION, {"init_image": "nonsense"})
```

Add to `modules/vision/tests/test_jobs.py`, replacing the two `_payload_files` tests in `TestRunGenerateInputs` (keep the class's other tests):

```python
    def test_the_handler_resolves_inputs_through_the_one_service_resolver(self, tmp_path):
        """`jobs.py` keeps no resolver of its own: the page and the queue
        must refuse and accept exactly the same references."""
        assert not hasattr(jobs, "_payload_files")
        output = stored_output(tmp_path)
        with patch.dict(operations_module._OPERATIONS, {"img2img": self.OPERATION}), patch(
            "modules.vision.jobs.services.resolve_inputs", return_value={}
        ) as mock_resolve, patch(
            "modules.vision.jobs.services.submit_job"
        ) as mock_submit, patch("modules.vision.jobs.services.wait_for") as mock_wait:
            mock_submit.return_value = mock_wait.return_value = _stub_done_job()
            jobs.run_generate(
                {"operation": "img2img", "params": {"prompt": "x"},
                 "inputs": {"init_image": f"output:{output.id}"}},
                [],
                make_job_ctx(),
            )
        operation, references = mock_resolve.call_args.args
        assert operation.key == "img2img"
        assert references == {"init_image": f"output:{output.id}"}

    def test_an_undeclared_file_param_still_refuses_the_whole_job(self, tmp_path):
        output = stored_output(tmp_path)
        with patch.dict(operations_module._OPERATIONS, {"img2img": self.OPERATION}):
            with pytest.raises(ValueError, match="declares no file parameter named mask_image"):
                jobs.run_generate(
                    {"operation": "img2img", "params": {"prompt": "x"},
                     "inputs": {"mask_image": f"output:{output.id}"}},
                    [],
                    make_job_ctx(),
                )
```

with a module-level helper in `test_jobs.py`:

```python
def _stub_done_job():
    """A `GenerationJob`-shaped stand-in for the tests that pin how the
    handler CALLS the service layer rather than what it produces."""
    stub = MagicMock()
    stub.id = "stub-id"
    stub.status = GenerationJob.Status.DONE
    stub.is_terminal = True
    stub.outputs.values_list.return_value = []
    return stub
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_services.py::TestResolveInputs -q`
Expected: FAIL — `AttributeError: module 'modules.vision.services' has no attribute 'InputReferenceError'`.

- [ ] **Step 3: Implement the resolver**

In `modules/vision/services.py`, after `stored_input_exists`:

```python
class InputReferenceError(ValueError):
    """A stored-image reference that cannot be used, and the param it
    answered.

    A `ValueError` still, so a caller with no form (a queued job, a tool)
    catches it exactly as it caught `stored_input`'s refusals before. The
    `param_key` exists for the caller that DOES have a form: the create
    page attaches the message to the field the operator is looking at,
    which is the one thing a single shared resolver would otherwise cost.
    """

    def __init__(self, param_key: str, message: str):
        super().__init__(message)
        self.param_key = param_key


def resolve_inputs(operation, references: dict[str, str]) -> dict[str, store.StoredFile]:
    """Every `{param key: reference}` pair as upload-shaped files.

    THE one resolver: the page's carried "use this image" references and a
    queue payload's `"inputs"` both land here, so a reference that the
    page accepts is a reference the queue accepts, and a reference either
    refuses is refused with the same sentence.

    A reference naming a param the operation does not declare as a file
    param is REFUSED, never dropped: running the generation without it
    would run a generation nobody asked for. That is the stricter of the
    two rules this replaced, and it is the correct one.

    Returns lazy handles: `store.StoredFile` opens nothing until its
    `chunks()` is iterated, so a caller resolving purely to prove a
    reference is live (the create page, before it enqueues) pays a
    database read and no file read at all.
    """
    if not references:
        return {}
    declared = operation.file_param_keys()
    undeclared = sorted(set(references) - declared)
    if undeclared:
        raise InputReferenceError(
            undeclared[0],
            f"operation {operation.key!r} declares no file parameter named "
            f"{', '.join(undeclared)} — it takes {sorted(declared)}.",
        )
    resolved: dict[str, store.StoredFile] = {}
    for param_key, reference in references.items():
        try:
            resolved[param_key] = stored_input(reference)
        except ValueError as exc:
            raise InputReferenceError(param_key, str(exc)) from exc
    return resolved
```

- [ ] **Step 4: Delete the copy in `jobs.py`**

Remove `_payload_files` entirely from `modules/vision/jobs.py`. `get_operation` is still used; `store` is NOT — `_payload_files` was its only consumer — so line 75 becomes `from modules.vision import services`. In `run_generate` (whose signature is `(payload, models, ctx)` after the merge — leave that signature alone here):

```python
    operation_key = payload["operation"]
    params = payload.get("params") or {}
    operation = get_operation(operation_key)
    # An unknown operation key is left to `submit_job`, which raises the
    # one operator-facing "Unknown operation" message -- a second copy of
    # that check here would be a second place to keep in sync.
    files = (
        services.resolve_inputs(operation, payload.get("inputs") or {})
        if operation is not None
        else {}
    )

    job = services.submit_job(operation_key, params, files=files or None)
```

Update the module docstring: replace the `_payload_files` paragraph with one naming `services.resolve_inputs` as the shared resolver and the strict-refusal rule.

- [ ] **Step 5: Point the view at it — the swap only**

`views.generate` is rewritten wholesale in Task 13, so this task changes the SMALLEST thing that makes the page share the resolver: its per-reference `for param_key, reference in refs.items(): ... services.stored_input(reference)` loop becomes one call. Everything around it — the `files = dict(request.FILES.items())` line above it and the `submit_job` call below it — is left exactly as it is and is Task 13's to replace.

```python
    files = dict(request.FILES.items())
    # An attached file always wins over a carried reference; the rule
    # stays HERE, in the only layer that sees both candidates, and the
    # resolver is handed one reference per param.
    carried = {key: ref for key, ref in refs.items() if key not in files}
    try:
        files.update(services.resolve_inputs(operation, carried))
    except services.InputReferenceError as exc:
        form.add_error(exc.param_key if exc.param_key in form.fields else None, str(exc))
        return _invalid_form_response(request, operation, check, form)
```

- [ ] **Step 6: Run the tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Expected: PASS. One existing view test may assert the old silent-ignore behaviour for a reference naming an undeclared param — if so, rewrite it to assert the 400 with the refusal message, and note in its docstring that the strict rule is now shared with the queue path.

- [ ] **Step 7: Update the README**

In `modules/vision/README.md`, "Feeding an image back in", add:

```markdown
Both halves of the flow resolve a reference through `services.resolve_inputs
(operation, {param: reference})` — the page's carried `input_<key>` field and a
queue payload's `"inputs"` map alike. A reference naming a param the operation
does not declare as a file param is refused, not dropped, on both paths. The
"an attached file wins over a carried reference" rule stays in the view: it is a
page rule about an operator changing their mind, and the resolver never sees
both candidates.
```

- [ ] **Step 8: Commit**

```bash
git add modules/vision/services.py modules/vision/jobs.py modules/vision/views.py modules/vision/tests/ modules/vision/README.md
git commit -m "$(cat <<'EOF'
refactor(vision): one resolver for stored-input references

jobs._payload_files refused a reference naming an undeclared file param;
views.generate silently ignored it. One rule now -- the strict one --
in services.resolve_inputs, with an InputReferenceError carrying the
param key so the page keeps attaching the message to the right field.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 7: Migration 0004 — correlation, failure kind, and staged inputs

Audit items 5 and 6's schema, plus the one schema change the upload seam needs (Task 11/13). One migration, because they land together and a second migration for two columns on the same table is noise.

**Confirmed against the real tree:** `modules/vision/migrations/` currently ends at `0003_upscale_seed_and_indexes.py`, so this is **0004**.

**Ruling (`JobInput.job` nullable):** a browser upload cannot ride a JSON queue payload, so the page must record it as a stored file *before* the job that will consume it exists. A `JobInput` with `job = NULL` is exactly that — "an upload staged by the page, waiting for the queued job that will consume it" — and it is referenced by the SAME `input:<id>` reference kind, resolved by the SAME `services.stored_input`, and served by the SAME `/vision/inputs/<id>/file/` view. No second reference kind, no second table, no second file-handling path.

**Ruling (`JobInput.created_at` nullable):** `auto_now_add=True, null=True` so existing rows take `NULL` and no one-off default is prompted for. `NULL` never matches the `created_at__lt` cutoff in Task 11's prune, which is correct: every pre-existing row is attached to a job and must never be swept.

**Files:**
- Modify: `modules/vision/models.py`
- Create: `modules/vision/migrations/0004_queue_correlation_failure_kind_staged_inputs.py` (generated)
- Test: `modules/vision/tests/test_models.py`

**Interfaces:**
- Produces: `GenerationJob.queue_job_id: int | None` (indexed), `GenerationJob.FailureKind` (TextChoices), `GenerationJob.failure_kind: str` (blank default `""`), `JobInput.job` nullable, `JobInput.created_at: datetime | None`.

- [ ] **Step 1: Write the failing test**

Append to `modules/vision/tests/test_models.py`:

```python
@pytest.mark.django_db
class TestFailureKind:
    """A machine-readable discriminator beside the prose: an agent has to
    be able to tell 'fix your params' from 'try again later'."""

    def test_a_new_job_carries_no_failure_kind(self):
        job = _job()
        assert job.failure_kind == ""

    def test_the_vocabulary_covers_every_failure_a_caller_can_observe(self):
        assert set(GenerationJob.FailureKind.values) == {
            "params_invalid", "role_unbound", "engine_unreachable",
            "engine_rejected", "engine_failed", "lost",
        }


@pytest.mark.django_db
class TestQueueCorrelation:
    def test_a_job_submitted_outside_the_queue_has_no_queue_job_id(self):
        assert _job().queue_job_id is None

    def test_a_queued_generation_is_findable_by_its_queue_job_id(self):
        job = _job(queue_job_id=77)
        assert GenerationJob.objects.get(queue_job_id=77) == job


@pytest.mark.django_db
class TestStagedInput:
    """An upload the page recorded before the job that will consume it
    exists -- the JSON-safe way a browser file reaches a queue payload."""

    def test_a_job_input_can_exist_before_its_job(self, tmp_path):
        staged = JobInput.objects.create(
            job=None, param_key="init_image",
            path=str(tmp_path / "beach.png"), media_type="image/png",
        )
        assert staged.job_id is None
        assert staged.created_at is not None

    def test_it_is_reachable_by_the_ordinary_input_reference(self, tmp_path):
        source = tmp_path / "beach.png"
        source.write_bytes(PNG)
        staged = JobInput.objects.create(
            job=None, param_key="init_image", path=str(source), media_type="image/png",
        )
        assert services.stored_input(f"input:{staged.id}").path == source
```

Add a `_job(**overrides)` module-level helper to `test_models.py` if the file has none (creates a `GenerationJob` with the minimum required fields, applying `overrides`), plus imports for `JobInput`, `services`, `PNG`.

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_models.py -q`
Expected: FAIL — `AttributeError: type object 'GenerationJob' has no attribute 'FailureKind'`.

- [ ] **Step 3: Add the fields**

In `modules/vision/models.py`, inside `GenerationJob`, after the `Status` class:

```python
    class FailureKind(models.TextChoices):
        """WHY a generation failed, as a value rather than a sentence.

        `error` is the operator's copy; this is the caller's. An agent
        needs to tell "fix your parameters and resubmit" from "the engine
        is down, try later" without pattern-matching prose that is allowed
        to change.

        Three of these are carried by an EXCEPTION rather than by a row,
        because they happen before a row exists -- `submit_job` refuses to
        write an orphan job for an unbound role or an invalid submission:
        `PARAMS_INVALID` is what a `core.inference.operations.ParamError`
        means, and `ROLE_UNBOUND`/`ENGINE_UNREACHABLE` are what
        `services.VisionUnavailable.failure_kind` reports. One vocabulary,
        two carriers -- so a caller reading either surface reads the same
        six values.
        """

        PARAMS_INVALID = "params_invalid", "Parameters invalid"
        ROLE_UNBOUND = "role_unbound", "No image model assigned"
        ENGINE_UNREACHABLE = "engine_unreachable", "Image engine unreachable"
        ENGINE_REJECTED = "engine_rejected", "Image engine refused the job"
        ENGINE_FAILED = "engine_failed", "Image engine failed the job"
        LOST = "lost", "Image engine forgot the job"
```

and, beside `error`:

```python
    error = models.TextField(blank=True)
    # The machine-readable half of `error` -- see `FailureKind`. Blank on
    # every job that has not failed, which is the honest reading: a job
    # that succeeded has no failure kind, and `""` is not a member of the
    # vocabulary.
    failure_kind = models.CharField(
        max_length=32, blank=True, default="", choices=FailureKind.choices
    )
    # The execution-queue job (`console.jobs.models.InferenceJob`) that
    # submitted this generation, or NULL for one submitted directly
    # (a management command, a test, a tool calling `services.submit_job`
    # itself). NOT a foreign key: the queue's tables are console-side and
    # `modules/` may not import `console.*` -- it is the same
    # snapshot-by-id relationship `InferenceJob.model_refs` already uses
    # in the other direction. Indexed because the page's queued-card poll
    # looks a generation up by it on every tick.
    queue_job_id = models.BigIntegerField(null=True, blank=True, db_index=True)
```

In `JobInput`, replace the `job` field and add `created_at`, and extend the class docstring:

```python
class JobInput(models.Model):
    """A file parameter's stored input (img2img's init image, an inpaint
    mask). Empty for txt2img; the table exists so file params are a data
    addition later, not a schema change.

    `job` is NULLABLE, and a row with no job is a STAGED upload: a file
    the page recorded before the generation that will consume it exists.
    A queue payload is JSON and cannot carry an upload, so the page writes
    the bytes into the managed store, records them here, and puts the
    ordinary `input:<id>` reference in the payload -- the same reference
    kind, the same `services.stored_input` resolver, and the same
    `/vision/inputs/<id>/file/` view a job's own input uses. When the
    worker's `submit_job` runs, it copies those bytes into the job's own
    directory and writes the job's own row, exactly as it does for a
    browser upload; the staged row is then disposable and is swept by
    `services.prune_staged_inputs` after `settings.VISION_STAGED_UPLOAD_TTL`.
    """

    job = models.ForeignKey(
        GenerationJob, on_delete=models.CASCADE, related_name="inputs",
        null=True, blank=True,
    )
    param_key = models.CharField(max_length=64)
    path = models.CharField(max_length=1024)
    media_type = models.CharField(max_length=128, blank=True)
    # Nullable `auto_now_add`: rows written before this column existed
    # take NULL, and NULL never matches the staged-upload sweep's cutoff
    # -- which is right, since every one of them is attached to a job.
    created_at = models.DateTimeField(auto_now_add=True, null=True)
```

- [ ] **Step 4: Generate the migration**

Run:
```bash
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python manage.py makemigrations vision --name queue_correlation_failure_kind_staged_inputs
```
Expected: creates `modules/vision/migrations/0004_queue_correlation_failure_kind_staged_inputs.py` with `AddField` for `failure_kind`, `queue_job_id`, `created_at` and `AlterField` for `jobinput.job`. Read the generated file and confirm there are no other operations in it.

- [ ] **Step 5: Verify the migration state is clean**

Run:
```bash
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python manage.py makemigrations --check --dry-run
```
Expected: `No changes detected`.

- [ ] **Step 6: Run the tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Expected: PASS.

- [ ] **Step 7: Update the README**

In `modules/vision/README.md`, "Storage layout", add after the directory tree:

```markdown
A **staged** upload lives at `data/generated/uploads/<uuid>/<filename>` and is
recorded as a `JobInput` with no job — the page writes it there before it
enqueues, because a JSON queue payload cannot carry a file. When the queued job
runs, `submit_job` copies those bytes into the job's own directory exactly as it
does for a browser upload, so every job still owns its inputs and deleting a job
still deletes them. Staged rows and their directories are swept after
`VISION_STAGED_UPLOAD_TTL` (default 24 hours).
```

- [ ] **Step 8: Commit**

```bash
git add modules/vision/models.py modules/vision/migrations/0004_queue_correlation_failure_kind_staged_inputs.py modules/vision/tests/test_models.py modules/vision/README.md
git commit -m "$(cat <<'EOF'
feat(vision): schema for queue correlation, failure kinds, staged uploads

Three columns and one nullable FK: queue_job_id so a running generation
can be found from the queue row that submitted it, failure_kind so a
caller can tell 'fix your params' from 'try again later', and a
job-less JobInput so a browser upload can be recorded as a stored input
before the queued job that will consume it exists.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 8: Set `failure_kind` at every failure site

Audit item 6's behaviour. The four row-writing sites are enumerated from the code: `services.py:280` (`GenerationRejected` in `submit_job`), `services.py:283` (the generic submit failure), `services.py:325` (`refresh_job`'s `state == "failed"`), `services.py:328` (`refresh_job`'s `state == "lost"`). The two pre-row sites raise instead: `submit_job`'s `raise VisionUnavailable(check.state, check.message)` and `validate_params`' `ParamError`.

**Ruling:** `_fail` takes the kind as a required argument — a failure with no kind is exactly the thing this task exists to abolish, so it must not be defaultable. `VisionUnavailable` derives its own kind from its `state`, using `.get(state, "")` rather than indexing: raising a `KeyError` from inside an exception's constructor would replace an honest 503 with a 500.

**Files:**
- Modify: `modules/vision/services.py` (`_fail`, its 4 call sites, `VisionUnavailable`, `job_json`)
- Test: `modules/vision/tests/test_services.py`

**Interfaces:**
- Consumes: `GenerationJob.FailureKind` (Task 7), `services.job_json` (Task 5).
- Produces: `services.VisionUnavailable.failure_kind: str`; `job_json(job)["failure_kind"]` and `job_json(job)["queue_job_id"]`. (`params_invalid` gets no constant of its own — it is named in `FailureKind`'s docstring and in ADR 0012.)

- [ ] **Step 1: Write the failing test**

Append to `modules/vision/tests/test_services.py`:

```python
@pytest.mark.django_db
class TestFailureKinds:
    """Every failure a caller of `submit_job`/`refresh_job` can observe
    names itself with a value, not only with a sentence."""

    def test_an_engine_rejection_is_engine_rejected(self):
        _bind()
        generator = StubGenerator(submit_error=GenerationRejected("unknown checkpoint"))
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW))
        assert job.status == GenerationJob.Status.FAILED
        assert job.failure_kind == GenerationJob.FailureKind.ENGINE_REJECTED

    def test_any_other_submission_failure_is_engine_failed(self):
        _bind()
        generator = StubGenerator(submit_error=RuntimeError("connection reset"))
        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW))
        assert job.failure_kind == GenerationJob.FailureKind.ENGINE_FAILED

    def test_a_poll_reporting_failure_is_engine_failed(self):
        job = _queued_job()
        generator = StubGenerator(states=[JobStatus("failed", error="CUDA out of memory")])
        with _registered(StubEngine(generator)):
            job = services.refresh_job(job)
        assert job.failure_kind == GenerationJob.FailureKind.ENGINE_FAILED
        assert job.error == "CUDA out of memory"

    def test_a_forgotten_job_is_lost(self):
        job = _queued_job()
        generator = StubGenerator(states=[JobStatus("lost")])
        with _registered(StubEngine(generator)):
            job = services.refresh_job(job)
        assert job.failure_kind == GenerationJob.FailureKind.LOST
        assert job.error == services.LOST_MESSAGE

    def test_an_unbound_role_reports_its_kind_on_the_exception(self):
        """No row exists to carry it -- `submit_job` refuses to write an
        orphan job for an unbound role -- so the exception carries it."""
        with pytest.raises(services.VisionUnavailable) as caught:
            services.submit_job("txt2img", dict(RAW))
        assert caught.value.failure_kind == GenerationJob.FailureKind.ROLE_UNBOUND

    def test_an_unreachable_engine_reports_its_kind_on_the_exception(self):
        _bind()
        with _registered(StubEngine(StubGenerator(), healthy=False)):
            with pytest.raises(services.VisionUnavailable) as caught:
                services.submit_job("txt2img", dict(RAW))
        assert caught.value.failure_kind == GenerationJob.FailureKind.ENGINE_UNREACHABLE

    def test_bad_params_still_raise_before_any_row_exists(self):
        """The vocabulary's `params_invalid` names THIS failure -- said in
        `FailureKind`'s docstring and in ADR 0012, not aliased in code
        nothing reads yet. What matters behaviourally is what this test
        pins: an invalid submission raises and writes no orphan job."""
        _bind()
        with _registered(StubEngine(StubGenerator())):
            with pytest.raises(ParamError):
                services.submit_job("txt2img", {"prompt": ""})
        assert GenerationJob.objects.count() == 0

    def test_a_failed_job_reports_its_kind_in_job_json(self):
        job = _queued_job()
        generator = StubGenerator(states=[JobStatus("lost")])
        with _registered(StubEngine(generator)):
            job = services.refresh_job(job)
        data = services.job_json(job)
        assert data["failure_kind"] == GenerationJob.FailureKind.LOST
        assert data["queue_job_id"] is None

    def test_a_job_that_has_not_failed_reports_no_kind(self, tmp_path):
        assert services.job_json(stored_output(tmp_path).job)["failure_kind"] == ""
```

No new helpers: `RAW` (line 34), `_bind` (line 53), `_registered` (line 62) and `GenerationRejected`/`JobStatus` (imported at line 19) already exist in `test_services.py`, and `_queued_job()` was added by Task 5. `StubGenerator` is scripted through its constructor — `submit_error=` for a submission that fails, `states=[JobStatus(...)]` for what a poll reports (`_helpers.py:310-345`) — never by assigning over its methods.

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_services.py::TestFailureKinds -q`
Expected: FAIL — `AssertionError` on `job.failure_kind == 'engine_rejected'` (the column exists and is `""`).

- [ ] **Step 3: Wire the kinds**

In `modules/vision/services.py`:

```python
# `PreflightResult.state` -> the failure kind a caller reads off
# `VisionUnavailable`. The third pre-row kind needs no constant of its
# own: `ParamError` means `GenerationJob.FailureKind.PARAMS_INVALID`, said
# once in that enum's docstring and once in ADR 0012, and an alias here
# would be a second name for a value nothing in this repo reads yet.
_UNAVAILABLE_FAILURE_KINDS = {
    "unbound": GenerationJob.FailureKind.ROLE_UNBOUND,
    "unreachable": GenerationJob.FailureKind.ENGINE_UNREACHABLE,
}
```

`VisionUnavailable.__init__` gains one line (and its docstring gains a sentence):

```python
    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state = state
        self.message = message
        # The machine-readable kind, from the same vocabulary a FAILED
        # row carries (`GenerationJob.FailureKind`) -- one vocabulary, two
        # carriers, because these two failures happen before any row
        # exists. `.get`, not `[...]`: a KeyError raised from inside an
        # exception's constructor would turn an honest 503 into a 500.
        self.failure_kind = _UNAVAILABLE_FAILURE_KINDS.get(state, "")
```

`_fail` gains the required argument:

```python
def _fail(job: GenerationJob, error: str, failure_kind: str) -> GenerationJob:
    """Mark `job` failed with `error` and `failure_kind`, stamping the
    finish time.

    `failure_kind` is REQUIRED, not defaulted: a failure that cannot say
    which kind it is, is exactly the thing this argument exists to
    abolish, and a default would let a new failure site quietly skip it.
    """
    job.status = GenerationJob.Status.FAILED
    job.error = error
    job.failure_kind = failure_kind
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error", "failure_kind", "finished_at"])
    return job
```

The four call sites:

```python
    except GenerationRejected as exc:
        return _fail(job, str(exc), GenerationJob.FailureKind.ENGINE_REJECTED)
    except Exception as exc:  # noqa: BLE001 -- the job exists; report why it never started
        logger.exception("Submitting job %s to %s failed", job.id, resolved.engine)
        return _fail(
            job,
            f"The image engine did not accept the job: {exc}",
            GenerationJob.FailureKind.ENGINE_FAILED,
        )
```

```python
    if state.state == "failed":
        return _fail(
            job,
            state.error or "The image engine reported a failure.",
            GenerationJob.FailureKind.ENGINE_FAILED,
        )

    if state.state == "lost":
        return _fail(job, LOST_MESSAGE, GenerationJob.FailureKind.LOST)
```

And `job_json` gains two keys, next to `error`:

```python
        "error": job.error,
        "failure_kind": job.failure_kind,
        "queue_job_id": job.queue_job_id,
```

- [ ] **Step 4: Run the tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Expected: PASS.

- [ ] **Step 5: Update the README**

In `modules/vision/README.md`, "The service layer", extend the `submit_job` bullet and add a paragraph:

```markdown
**Failures name themselves.** A failed job carries `failure_kind` beside its
prose `error` — `engine_rejected` (the engine refused the submission),
`engine_failed` (it accepted and then failed, or the submission failed some other
way), `lost` (it no longer knows the job). The two failures that happen *before* a
row exists carry the same vocabulary on the exception instead:
`VisionUnavailable.failure_kind` is `role_unbound` or `engine_unreachable`, and a
`ParamError` means `params_invalid` (named in `FailureKind`'s own docstring). One
vocabulary, two carriers — so "fix your parameters" and "try again later" are
distinguishable without reading English.
```

- [ ] **Step 6: Commit**

```bash
git add modules/vision/services.py modules/vision/tests/test_services.py modules/vision/README.md
git commit -m "$(cat <<'EOF'
feat(vision): a failed generation says which kind of failure it was

ParamError.errors and VisionUnavailable.state were machine-readable and
then flattened to prose at the service boundary. failure_kind is set at
all four row-writing failure sites and carried on both pre-row
exceptions, so a caller can tell 'fix your params' from 'retry later'
without pattern-matching a sentence.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 9: The generation records the queue job that asked for it

Audit item 5. A queued generation's `GenerationJob` UUID was revealed only in the queue job's result, at the end: no correlation while it runs, so no `/queue/` → `/vision/` link, and (Task 12) no way for the page's queued placeholder to become the real card until the whole thing had finished.

**Post-merge note — there is no contract work left to do.** An earlier draft of this task extended `JobKind.handler` with a `queue_job_id` keyword. That is obsolete: main (merged as `df251c3`) already gives every handler a third positional argument, `ctx: core.inference.jobkinds.JobContext` — a frozen dataclass carrying `job_id`, `attempt`, `checkpoint_state` and the `report_progress`/`checkpoint` write paths (`core/inference/jobkinds.py:57-140`). `modules/vision/jobs.py::run_generate` already takes it (line 149) and currently documents it as unused; every handler in the repo is migrated; `modules/vision/tests/_helpers.py::make_job_ctx` (line 55) builds one for a direct call and every existing `run_generate` test already passes it. **This task touches `console/jobs/`, `modules/rag/` and ADR 0013 not at all** — ADR 0013 §5/§8 already document the contract, and are read-only here.

So all that is left is the one line of vision behaviour: `run_generate` stamps `ctx.job_id` onto the `GenerationJob` immediately after `services.submit_job` returns.

**Ruling (stamp after, not inside `submit_job`).** `submit_job` keeps its signature. The alternative — threading `queue_job_id` through as a fourth parameter so the row is created carrying it — buys a window of a few milliseconds and costs the service layer a queue-shaped argument that every non-queued caller must pass `None` for. The window is real but harmless, and Task 12's poll endpoint is written to tolerate it: it looks the generation up by `queue_job_id` and simply renders the placeholder for one more tick when it is not there yet.

**Files:**
- Modify: `modules/vision/jobs.py::run_generate` (lines 149-215)
- Test: `modules/vision/tests/test_jobs.py`

**Interfaces:**
- Consumes: `GenerationJob.queue_job_id` (Task 7); `JobContext.job_id` (already on main); `_helpers.make_job_ctx(**overrides)`.
- Produces: a `GenerationJob` whose `queue_job_id` is the queue row that submitted it. `run_generate`'s signature is unchanged: `(payload, models, ctx)`.

- [ ] **Step 1: Write the failing test**

Append to `modules/vision/tests/test_jobs.py`'s `TestRunGenerateEndToEnd`:

```python
    def test_the_generation_records_the_queue_job_that_asked_for_it(self, tmp_path):
        """Correlation while it runs: `/vision/queue/<id>/` finds the
        generation by this column, so the page's queued card can become
        the real card mid-generation instead of at the very end."""
        _bind_comfyui()
        fake = FakeComfyUI(
            prompt_id="p-99",
            history=history_success("p-99", filenames=("out.png",)),
            images={"out.png": PNG},
        )
        with patch("core.inference.engines.comfyui.httpx.get", fake.get), patch(
            "core.inference.engines.comfyui.httpx.post", fake.post
        ), override_settings(GENERATED_DIR=tmp_path):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx(job_id=41)
            )

        job = GenerationJob.objects.get(pk=result["job_id"])
        assert job.queue_job_id == 41

    def test_a_failed_generation_is_still_correlated(self, tmp_path):
        """The stamp happens on whatever `submit_job` returned, including
        a job it already failed -- an engine rejection is a normal
        outcome, and a rejected job the queue cannot be traced back to is
        exactly the debugging hole this column closes."""
        _bind_comfyui()
        fake = FakeComfyUI(
            prompt_id="p-1", prompt_status=400,
            prompt_body={"error": {"message": "unknown checkpoint"}},
        )
        with patch("core.inference.engines.comfyui.httpx.get", fake.get), patch(
            "core.inference.engines.comfyui.httpx.post", fake.post
        ), override_settings(GENERATED_DIR=tmp_path):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx(job_id=42)
            )

        job = GenerationJob.objects.get(pk=result["job_id"])
        assert job.status == GenerationJob.Status.FAILED
        assert job.queue_job_id == 42
```

`prompt_status`/`prompt_body` are `FakeComfyUI`'s own `POST /prompt` fields (`_helpers.py:140-141`), so the rejection travels the adapter's real error path — the same way `test_comfyui_generator.py`'s rejection tests drive it.

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_jobs.py::TestRunGenerateEndToEnd -q`
Expected: FAIL — `assert None == 41`.

- [ ] **Step 3: Stamp it**

In `modules/vision/jobs.py::run_generate`, right after the `submit_job` call:

```python
    job = services.submit_job(operation_key, params, files=files or None)
    # Correlate the generation with the queue row that asked for it,
    # while it is still running: without this the `GenerationJob`'s id is
    # revealed only in this handler's own result, at the very end, so
    # nothing could link a running queue job to the images it was
    # producing -- no `/queue/` -> `/vision/` link, and the page's queued
    # card could not become the real card until the whole generation had
    # finished. `ctx.job_id` is the claimed row's id exactly as the worker
    # resolved it at claim time (`core.inference.jobkinds.JobContext`).
    #
    # Stamped HERE rather than passed into `submit_job`: the service layer
    # stays free of a queue-shaped argument every direct caller would have
    # to pass `None` for, and the millisecond window this opens is one the
    # poll endpoint already tolerates (it renders the placeholder for one
    # more tick when the row is not correlated yet).
    GenerationJob.objects.filter(pk=job.pk).update(queue_job_id=ctx.job_id)
    job.queue_job_id = ctx.job_id

    job = services.wait_for(job, timeout=GENERATE_WAIT_TIMEOUT_SECONDS)
```

`.update()` rather than `job.save(update_fields=...)`: `submit_job` may have just written a FAILED status on this row, and a queryset update touches this one column without any chance of writing a stale in-memory copy of the others back over it. The in-memory attribute is set too so the object this function goes on using matches the row.

Update `run_generate`'s docstring: the `ctx` paragraph currently says the argument is unused. It is used now — replace that paragraph's first sentence with what it does (`ctx.job_id` is stamped onto the generation for correlation), and KEEP the rest of it, which explains why `ctx.report_progress` is still not wired (`wait_for` blocks in its own polling loop with no callback seam) — that remains true and remains deferred. Add `GenerationJob` to the module's imports (`from modules.vision.models import GenerationJob`); `jobs.py` does not import it today.

- [ ] **Step 4: Run the tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_jobs.py -q`
Expected: PASS.

- [ ] **Step 5: Run the module suite**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Expected: PASS.

- [ ] **Step 6: Update the README**

In `modules/vision/README.md`, "Queued generation", add to the handler bullet:

```markdown
`run_generate` stamps the queue row's own id (`ctx.job_id`, from the
`JobContext` every handler receives — ADR 0013 §8) onto the `GenerationJob` as
soon as `submit_job` returns, so a running generation can be found from the queue
job that submitted it: that is what `/vision/queue/<id>/` looks up. The context's
other seam, `ctx.report_progress`, is deliberately not wired — `wait_for` blocks
inside its own polling loop with no callback seam, and threading one through is a
separate change.
```

- [ ] **Step 7: Commit**

```bash
git add modules/vision/jobs.py modules/vision/tests/test_jobs.py modules/vision/README.md
git commit -m "$(cat <<'EOF'
feat(vision): a queued generation records the queue job that asked for it

The GenerationJob's id was revealed only in the handler's result, at the
end, so nothing could link a running queue row to the images it was
producing. run_generate stamps ctx.job_id onto the row as soon as
submit_job returns -- no contract change needed, the JobContext third
argument is already there.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 10: `run_generate` reports a timeout as a timeout

Audit item 7: a generation still running when `GENERATE_WAIT_TIMEOUT_SECONDS` elapses returns a SUCCEEDED queue job whose `status` is `"queued"` — readable only by someone who knows that `"queued"` in a finished result means "we stopped waiting". The gap table's "Output retrieval — include output URLs in result" lands in the same return dict.

**Ruling:** `output_ids` stays (it is a published shape; nothing gains from renaming it) and `output_urls` joins it, both derived from `services.job_json` so the URLs have exactly one owner. The wall-clock constant stays where it is — the audit says its worker-side location is right.

**Files:**
- Modify: `modules/vision/jobs.py::run_generate`
- Test: `modules/vision/tests/test_jobs.py`

**Interfaces:**
- Consumes: `services.job_json` (Task 5).
- Produces: `run_generate(...) -> {"job_id": str, "status": str, "timed_out": bool, "output_ids": [int], "output_urls": [str]}`.

- [ ] **Step 1: Write the failing test**

In `modules/vision/tests/test_jobs.py`, extend `TestRunGenerateEndToEnd`:

```python
    def test_a_finished_generation_reports_it_did_not_time_out(self, tmp_path):
        _bind_comfyui()
        fake = FakeComfyUI(
            prompt_id="p-99",
            history=history_success("p-99", filenames=("out.png",)),
            images={"out.png": PNG},
        )
        with patch("core.inference.engines.comfyui.httpx.get", fake.get), patch(
            "core.inference.engines.comfyui.httpx.post", fake.post
        ), override_settings(GENERATED_DIR=tmp_path):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx()
            )

        assert result["timed_out"] is False
        assert result["output_urls"] == [
            reverse("vision-output-file", args=[output_id]) for output_id in result["output_ids"]
        ]

    def test_a_generation_still_running_at_the_timeout_says_so(self, tmp_path):
        """The unreadable shape this replaces: a SUCCEEDED queue job whose
        status is "queued". The flag says what happened; the status still
        says what the generation was doing when we stopped watching."""
        _bind_comfyui()
        fake = FakeComfyUI(prompt_id="p-1", queue_pending=[[1, "p-1", {}, {}, []]])
        with patch("core.inference.engines.comfyui.httpx.get", fake.get), patch(
            "core.inference.engines.comfyui.httpx.post", fake.post
        ), patch("modules.vision.jobs.GENERATE_WAIT_TIMEOUT_SECONDS", 0.0):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx()
            )

        assert result["timed_out"] is True
        assert result["status"] == GenerationJob.Status.QUEUED
        assert result["output_ids"] == []
        assert result["output_urls"] == []

    def test_a_failed_generation_is_not_a_timeout(self, tmp_path):
        _bind_comfyui()
        fake = FakeComfyUI(prompt_id="p-2", history=history_error("p-2", "CUDA out of memory"))
        with patch("core.inference.engines.comfyui.httpx.get", fake.get), patch(
            "core.inference.engines.comfyui.httpx.post", fake.post
        ), override_settings(GENERATED_DIR=tmp_path):
            result = jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)}, [], make_job_ctx()
            )

        assert result["status"] == GenerationJob.Status.FAILED
        assert result["timed_out"] is False
```

Add `from django.urls import reverse` to the test module's imports (`make_job_ctx` is already imported there, line 34, and every existing `run_generate` call site already passes it).

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_jobs.py::TestRunGenerateEndToEnd -q`
Expected: FAIL — `KeyError: 'timed_out'`.

- [ ] **Step 3: Implement the result shape**

In `modules/vision/jobs.py::run_generate`, replace the return:

```python
    job = services.wait_for(job, timeout=GENERATE_WAIT_TIMEOUT_SECONDS)

    # One owner for what a job looks like as data: the outputs' serving
    # URLs are `services.job_json`'s, not a second copy of `reverse()`
    # here that could name a different route.
    representation = services.job_json(job)
    return {
        "job_id": representation["id"],
        "status": representation["status"],
        # Explicit, because the alternative is unreadable: a SUCCEEDED
        # queue job whose `status` is "queued" means "we stopped waiting",
        # and nothing but this flag says so. The generation itself is
        # unaffected -- it is still running on the engine, and the job's
        # own card keeps polling it to completion.
        "timed_out": not job.is_terminal,
        "output_ids": [output["id"] for output in representation["outputs"]],
        "output_urls": [output["url"] for output in representation["outputs"]],
    }
```

Update `run_generate`'s docstring: replace the `Returns {...}` paragraph with the new keys, keeping the existing rulings (an engine-side failure is a normal outcome reported through `status`, never a raised handler) and adding: "`timed_out` is `True` when the wall-clock budget elapsed with the generation still `queued`/`running`. The queue job still SUCCEEDS: waiting is what timed out, not generating — the engine is still working, and the job's own card polls it to completion. `output_ids` and `output_urls` name the same files in the same order."

- [ ] **Step 4: Run the tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Expected: PASS.

- [ ] **Step 5: Update the README**

In `modules/vision/README.md`, "Queued generation", replace the result-shape sentence:

```markdown
the handler calls `services.submit_job` then `services.wait_for` and reports
`{"job_id", "status", "timed_out", "output_ids", "output_urls"}`. An engine-side
generation failure is a normal, honestly-reported outcome (`status: "failed"`),
not a raised exception. `timed_out` is `True` when the handler's wall-clock
budget elapsed with the generation still running — the queue job still succeeds,
because waiting is what timed out, not generating: the engine keeps working and
the job's own card polls it to completion. The queue page shows nothing about
results, so nothing there changes; the flag is for the caller reading the result.
```

- [ ] **Step 6: Commit**

```bash
git add modules/vision/jobs.py modules/vision/tests/test_jobs.py modules/vision/README.md
git commit -m "$(cat <<'EOF'
feat(vision): a timed-out wait says so, and the result carries output URLs

A generation still running when the handler's budget elapsed produced a
SUCCEEDED queue job whose status read "queued" -- legible only to
someone who knew the trick. timed_out says it outright, and output_urls
joins output_ids, both read off services.job_json so the serving routes
have one owner.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 11: Staged uploads — the seam that lets a browser file reach a JSON payload

This is the hard part of audit item 1, isolated so it can be tested on its own before the view moves. **A browser upload cannot ride a JSON queue payload**, and `GenerationJob` does not exist until the worker's `submit_job` creates it — so the page needs somewhere to put the bytes in between.

**The decision, and the alternatives it beat:**

- **Chosen — a `JobInput` with no job yet, referenced as `input:<id>`.** The page writes the upload into the managed store under `data/generated/uploads/<uuid>/`, records a `JobInput` row with `job = NULL`, and puts the ordinary `input:<id>` reference in the payload. Everything downstream is code that already exists: `parse_input_reference` parses it, `stored_input` resolves it, `resolve_inputs` batches it, `StoredFile` hands it to `submit_job`, `submit_job` copies the bytes into the job's own directory and writes the job's own row exactly as it does for a browser upload, and `/vision/inputs/<id>/file/` can serve it. **No second reference kind, no second table, no second file-handling path** — which was the binding requirement.
- **Rejected — a new `upload:<token>` reference kind over a token directory with no row.** Avoids a migration, but adds a third reference kind, a third branch in the resolver, a new serving view (or no way to show the file at all), and a `parse_input_reference` that no longer means "a row this module owns".
- **Rejected — pre-creating the `GenerationJob` in the view.** It would produce two rows per submission (the view's and `submit_job`'s), or force `submit_job` to adopt an existing row and grow a second create path. That splits the one submission path in exactly the way this whole plan exists to avoid.

The chosen design writes the bytes twice — once staged, once into the job's own directory. That is deliberate, not a cost that went unnoticed: ADR 0012 already rules that **each job copies its inputs into its own directory** (deleting a job deletes its directory, and a job whose input lived elsewhere would lose the record of what it ran on), and a staged upload reaching `submit_job` as an ordinary `StoredFile` is exactly what makes that rule hold for the queue path without a second store-and-record branch.

**Lifecycle ruling.** A staged row is **not** deleted when it is consumed. The worker's orphan sweep can re-run a job from scratch, and a payload whose references had been deleted would fail a re-run that would otherwise have worked. They are swept by AGE instead — `prune_staged_inputs()`, called from the view on every submission (unconditionally, staging or not), the same prune-on-write grammar `console.jobs.backend._prune_finished_jobs` and `modules.rag.services._prune_ask_records` already use (this codebase has no cron). `discard_staged_inputs()` cleans up immediately in the one case where the answer is certain: the enqueue itself failed, so nothing will ever consume them.

**Files:**
- Modify: `modules/vision/store.py`, `modules/vision/services.py`, `config/settings.py:207`
- Test: `modules/vision/tests/test_store.py`, `modules/vision/tests/test_services.py`

**Interfaces:**
- Consumes: nullable `JobInput.job` + `JobInput.created_at` (Task 7).
- Produces:
  - `store.STAGING_DIRNAME = "uploads"`; `store.stage_input(uploaded) -> str` (absolute path); `store.remove_staged_input(path: str) -> None`.
  - `services.stage_upload(param_key: str, uploaded) -> str` — returns the `"input:<id>"` reference.
  - `services.prune_staged_inputs(older_than: timedelta | None = None) -> int`.
  - `services.discard_staged_inputs(references: Iterable[str]) -> None`.
  - `settings.VISION_STAGED_UPLOAD_TTL: timedelta`.

- [ ] **Step 1: Write the failing store test**

Append to `modules/vision/tests/test_store.py`:

```python
class TestStageInput:
    """An upload with no job yet: the page has the bytes, and the
    GenerationJob that will own them is created later, on a worker."""

    def test_it_writes_under_the_staging_directory(self, tmp_path):
        upload = SimpleUploadedFile("beach.png", b"bytes", content_type="image/png")
        with override_settings(GENERATED_DIR=tmp_path):
            path = Path(store.stage_input(upload))
        assert path.parent.parent == tmp_path / store.STAGING_DIRNAME
        assert path.read_bytes() == b"bytes"

    def test_each_staged_upload_gets_its_own_directory(self, tmp_path):
        with override_settings(GENERATED_DIR=tmp_path):
            first = Path(store.stage_input(SimpleUploadedFile("a.png", b"1")))
            second = Path(store.stage_input(SimpleUploadedFile("a.png", b"2")))
        assert first.parent != second.parent
        assert first.read_bytes() == b"1"
        assert second.read_bytes() == b"2"

    def test_only_the_basename_is_kept(self, tmp_path):
        upload = SimpleUploadedFile("beach.png", b"bytes")
        upload.name = r"C:\Users\op\beach.png"
        with override_settings(GENERATED_DIR=tmp_path):
            path = Path(store.stage_input(upload))
        assert path.name == "beach.png"

    def test_a_staged_upload_is_removed_with_its_directory(self, tmp_path):
        with override_settings(GENERATED_DIR=tmp_path):
            path = store.stage_input(SimpleUploadedFile("a.png", b"1"))
            store.remove_staged_input(path)
        assert not Path(path).exists()
        assert not Path(path).parent.exists()

    def test_removing_a_path_outside_the_staging_directory_does_nothing(self, tmp_path):
        """A guard, not politeness: this function takes a path off a
        database row, and a job's own input must never be deletable
        through it."""
        job_file = tmp_path / "some-job" / "inputs" / "init_image-beach.png"
        job_file.parent.mkdir(parents=True)
        job_file.write_bytes(b"1")
        with override_settings(GENERATED_DIR=tmp_path):
            store.remove_staged_input(str(job_file))
        assert job_file.exists()
```

Add `from pathlib import Path` if the module lacks it (it has it).

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_store.py::TestStageInput -q`
Expected: FAIL — `AttributeError: module 'modules.vision.store' has no attribute 'stage_input'`.

- [ ] **Step 3: Implement the store half**

In `modules/vision/store.py`, add `import uuid` and:

```python
# Where an upload with no job yet lives: `<GENERATED_DIR>/uploads/<uuid>/`.
# A sibling of the per-job directories and never confusable with one --
# a job directory is named by the job's UUID, and "uploads" is not one.
STAGING_DIRNAME = "uploads"


def _write_upload(dest_dir: Path, filename: str, uploaded) -> str:
    """Write `uploaded` into `dest_dir` as `filename`, creating the
    directory, and return the absolute path as a string.

    The one chunked write in this module: a job's input and a staged
    upload are the same operation to two different places, and two copies
    of the loop would be two places to get the chunking wrong.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename
    with open(dest_path, "wb") as handle:
        for chunk in uploaded.chunks():
            handle.write(chunk)
    return str(dest_path)


def store_input(job_id, param_key: str, uploaded) -> str:
    """Write an uploaded file for `param_key` into the job's `inputs/`
    subdirectory and return its absolute path as a string."""
    return _write_upload(
        job_dir(job_id) / "inputs", f"{param_key}-{Path(uploaded.name).name}", uploaded
    )


def stage_input(uploaded) -> str:
    """Write an upload that has NO JOB YET, and return its absolute path.

    The page must get a browser file into the managed store before it
    enqueues, because a queue payload is JSON: what travels in the payload
    is the `input:<id>` reference of the `JobInput` row
    `modules.vision.services.stage_upload` records for this path.

    Its own directory per upload, named by a fresh UUID, so two files with
    the same name never collide and no param prefix is needed in the
    filename -- the job's own copy gets the `<param>-<name>` form when
    `submit_job` stores it under the job (see `store_input`).
    """
    return _write_upload(
        settings.GENERATED_DIR / STAGING_DIRNAME / str(uuid.uuid4()),
        Path(uploaded.name).name,
        uploaded,
    )


def remove_staged_input(path: str) -> None:
    """Delete one staged upload's own directory, if it is really one.

    The path comes off a database row, so this checks that it lives
    directly inside the staging directory before removing anything: a
    job's own input must never be deletable through this function, no
    matter what a row says.
    """
    staged = Path(path)
    if staged.parent.parent != settings.GENERATED_DIR / STAGING_DIRNAME:
        return
    if staged.parent.exists():
        shutil.rmtree(staged.parent)
```

- [ ] **Step 4: Write the failing services test**

Append to `modules/vision/tests/test_services.py`:

```python
@pytest.mark.django_db
class TestStagedUploads:
    """The page's half of the queue seam: a browser file becomes an
    ordinary `input:<id>` reference a JSON payload can carry."""

    def test_staging_returns_an_ordinary_input_reference(self, tmp_path):
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with override_settings(GENERATED_DIR=tmp_path):
            reference = services.stage_upload("init_image", upload)
        assert services.parse_input_reference(reference)[0] == "input"
        assert b"".join(services.stored_input(reference).chunks()) == PNG

    def test_the_staged_row_belongs_to_no_job_and_remembers_its_param(self, tmp_path):
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with override_settings(GENERATED_DIR=tmp_path):
            reference = services.stage_upload("init_image", upload)
        row = JobInput.objects.get(pk=int(reference.split(":")[1]))
        assert row.job_id is None
        assert row.param_key == "init_image"
        assert row.media_type == "image/png"

    def test_a_staged_upload_is_swept_once_it_is_old_enough(self, tmp_path):
        with override_settings(GENERATED_DIR=tmp_path):
            reference = services.stage_upload("init_image", SimpleUploadedFile("a.png", PNG))
            row = JobInput.objects.get(pk=int(reference.split(":")[1]))
            JobInput.objects.filter(pk=row.pk).update(
                created_at=timezone.now() - timedelta(hours=48)
            )
            assert services.prune_staged_inputs() == 1
        assert not JobInput.objects.filter(pk=row.pk).exists()
        assert not Path(row.path).exists()

    def test_a_fresh_staged_upload_survives_the_sweep(self, tmp_path):
        with override_settings(GENERATED_DIR=tmp_path):
            reference = services.stage_upload("init_image", SimpleUploadedFile("a.png", PNG))
            assert services.prune_staged_inputs() == 0
        assert services.stored_input_exists(reference)

    def test_a_jobs_own_input_is_never_swept(self, tmp_path):
        """Only rows with no job are staged uploads. A job's input is the
        job's, however old it is."""
        job = stored_output(tmp_path).job
        source = tmp_path / "in.png"
        source.write_bytes(PNG)
        row = JobInput.objects.create(job=job, param_key="init_image", path=str(source))
        JobInput.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=30))
        with override_settings(GENERATED_DIR=tmp_path):
            assert services.prune_staged_inputs() == 0
        assert JobInput.objects.filter(pk=row.pk).exists()
        assert source.exists()

    def test_discarding_removes_the_rows_and_the_files_at_once(self, tmp_path):
        """For the one case where the answer is certain: the enqueue
        failed, so nothing will ever consume them."""
        with override_settings(GENERATED_DIR=tmp_path):
            reference = services.stage_upload("init_image", SimpleUploadedFile("a.png", PNG))
            row = JobInput.objects.get(pk=int(reference.split(":")[1]))
            services.discard_staged_inputs([reference])
        assert not JobInput.objects.filter(pk=row.pk).exists()
        assert not Path(row.path).exists()

    def test_discarding_never_touches_a_job_owned_input(self, tmp_path):
        job = stored_output(tmp_path).job
        source = tmp_path / "in.png"
        source.write_bytes(PNG)
        row = JobInput.objects.create(job=job, param_key="init_image", path=str(source))
        with override_settings(GENERATED_DIR=tmp_path):
            services.discard_staged_inputs([f"input:{row.id}"])
        assert JobInput.objects.filter(pk=row.pk).exists()

    def test_discarding_an_unresolvable_reference_is_not_an_error(self, tmp_path):
        with override_settings(GENERATED_DIR=tmp_path):
            services.discard_staged_inputs(["output:999", "nonsense", "input:999"])
```

Add the imports these need: `SimpleUploadedFile`, `override_settings`, `timezone`, `timedelta`, `Path`, `JobInput`.

- [ ] **Step 5: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_services.py::TestStagedUploads -q`
Expected: FAIL — `AttributeError: module 'modules.vision.services' has no attribute 'stage_upload'`.

- [ ] **Step 6: Add the setting**

In `config/settings.py`, beside `VISION_STALE_AFTER` (line 207 after the media-ingestion merge):

```python
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
```

- [ ] **Step 7: Implement the services half**

In `modules/vision/services.py`, after `resolve_inputs`:

```python
def stage_upload(param_key: str, uploaded) -> str:
    """Record one browser upload as a stored input that has no job yet,
    and return the `input:<id>` reference that names it.

    THE seam that lets the page enqueue. A queue payload is JSON and a
    browser file is not, so the bytes go into the managed store here and
    the payload carries a reference to the row that owns them -- the same
    reference kind a gallery image or another job's input uses, resolved
    by the same `resolve_inputs`, and handed to `submit_job` as the same
    `store.StoredFile`. The queued job's `submit_job` then copies those
    bytes into ITS OWN directory and writes its own `JobInput`, exactly as
    it does for a file posted directly, so a job still owns its inputs and
    deleting a job still deletes them.
    """
    return "input:%d" % JobInput.objects.create(
        job=None,
        param_key=param_key,
        path=store.stage_input(uploaded),
        media_type=getattr(uploaded, "content_type", "") or "",
    ).id


def prune_staged_inputs(older_than=None) -> int:
    """Delete staged uploads older than `settings.VISION_STAGED_UPLOAD_TTL`
    (or `older_than`), with their files, and return how many went.

    Prune-on-write, called by the page on EVERY submission (staging
    something or not -- an unconditional call is one rule instead of two,
    and the query is indexed and usually empty) -- the same grammar `console.jobs.backend._prune_finished_jobs` and
    `modules.rag.services._prune_ask_records` use, because this codebase
    has no cron and will not grow one for this.

    A staged upload is NOT deleted when it is consumed: the queue's orphan
    sweep can re-run a job from scratch, and a payload whose references had
    already been deleted would fail a re-run that would otherwise have
    worked. Age is the honest rule instead.

    Only rows with NO job are touched -- a job's own input belongs to the
    job for as long as the job exists, however old it is -- and a row
    written before `created_at` existed reads NULL, never matches the
    cutoff, and is therefore never swept (every one of them is attached).
    """
    cutoff = timezone.now() - (older_than or settings.VISION_STAGED_UPLOAD_TTL)
    stale = list(JobInput.objects.filter(job__isnull=True, created_at__lt=cutoff))
    for row in stale:
        store.remove_staged_input(row.path)
        row.delete()
    return len(stale)


def discard_staged_inputs(references) -> None:
    """Delete the staged uploads `references` names, with their files.

    For the one case where waiting for the sweep would be pointless: the
    enqueue that was going to consume them failed, so nothing ever will.
    A reference that does not name a staged upload -- a gallery output, a
    job's own input, a row that is gone, a malformed string -- is skipped
    in silence: this is cleanup, and cleanup that raises leaves the caller
    reporting the wrong failure.
    """
    for reference in references:
        try:
            row = _referenced_row(reference)
        except ValueError:
            continue
        if row is None or not isinstance(row, JobInput) or row.job_id is not None:
            continue
        store.remove_staged_input(row.path)
        row.delete()
```

Add `from django.conf import settings` to the services imports — `services.py` does not import it today (checked: its Django imports are `transaction`, `reverse`, `timezone`).

- [ ] **Step 8: Run the tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Expected: PASS.

- [ ] **Step 9: Update the README**

In `modules/vision/README.md`, "The service layer", add:

```markdown
- **`stage_upload(param_key, uploaded) -> "input:<id>"`** — records a browser
  upload as a stored input that has no job yet, and returns the ordinary
  reference for it. This is what lets the page enqueue: a queue payload is JSON
  and a file is not, so the bytes go into the managed store and the payload
  carries the reference. The queued job's `submit_job` copies them into the job's
  own directory as it would any upload.
- **`prune_staged_inputs()` / `discard_staged_inputs(references)`** — the sweep
  (by age, on write, default 24h — `VISION_STAGED_UPLOAD_TTL`) and the immediate
  cleanup for a submission whose enqueue failed. A staged upload is never deleted
  on consumption: the queue can re-run a job, and a payload with deleted
  references would fail a re-run that would otherwise have worked.
```

- [ ] **Step 10: Commit**

```bash
git add modules/vision/store.py modules/vision/services.py config/settings.py modules/vision/tests/ modules/vision/README.md
git commit -m "$(cat <<'EOF'
feat(vision): stage a browser upload as an ordinary stored input

A queue payload is JSON and cannot carry a file, and the GenerationJob
does not exist until the worker creates it. An upload is now recorded as
a JobInput with no job yet and travels as the same input:<id> reference
a gallery image already used -- one reference kind, one resolver, one
file-handling path -- swept by age rather than on consumption so the
queue's orphan re-run still works.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 12: The queued placeholder card and its poll endpoint

The other half of the "no dead submit button" constraint, built and tested BEFORE the view starts enqueuing (Task 13), so the page never spends a commit with a submit button that produces nothing.

**What it is:** a card shown for a `vision.generate` queue job that has not yet produced a `GenerationJob`. It polls `/vision/queue/<id>/`, which returns either the same placeholder (still queued/running) or — the moment the worker's `submit_job` has created the row — the REAL job card, whose own `data-job-poll` then takes over. **No JavaScript change is needed:** the existing script in `create.html` polls whatever `data-job-poll` a card carries and follows the attribute on each returned fragment.

**Ruling (what the placeholder says):** the job kind's label and its queue state — not the prompt. `console.jobs.backend.JobStatus` deliberately carries no payload (its sibling `QueueRow`'s docstring rules on exactly that), and the alternatives were worse: widening that seam for a display string, or reflecting the operator's prompt through the poll URL so it survives each swap. The prompt reappears one poll after the worker claims the job, on the real card, which is also where every other fact about the generation lives.

**Files:**
- Create: `modules/vision/templates/vision/_queued_card.html`, `modules/vision/tests/test_views_queue.py`
- Modify: `modules/vision/views.py`, `modules/vision/urls.py`, `modules/vision/jobs.py` (the `JOB_KIND` constant), `modules/vision/apps.py`, `modules/vision/templates/vision/create.html`

**Interfaces:**
- Consumes: `GenerationJob.queue_job_id` (Tasks 7/9).
- Produces:
  - `jobs.JOB_KIND = "vision.generate"`.
  - `views._queue_card_context(queue_job_id: int, job_status) -> dict` with keys `queue_job_id`, `poll_url`, `label`, `state_label`, `position`, `error`, `terminal`.
  - `views.queue_job_status(request, queue_job_id: int)` at `path("queue/<int:queue_job_id>/", ..., name="vision-queue-status")`, which looks the generation up by `queue_job_id` BEFORE it asks the queue anything.
  - `views._generation_from_result(job_status) -> GenerationJob | None`.
  - `views._queued_placeholder(request) -> dict | None` (the `?queued=<id>` card for the create page).
  - Template contract: `_queued_card.html` reads a single `card` variable.

- [ ] **Step 1: Write the failing test**

Create `modules/vision/tests/test_views_queue.py`:

```python
"""Unit tests for the queued placeholder card and its poll endpoint.

The queue itself is stubbed at `modules.vision.views.get_job` -- the seam
this module actually calls. What the queue does with a job is
`console/jobs/tests/`' business; what the PAGE does with the queue's
answer is this file's.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from modules.vision.models import GenerationJob
from modules.vision.tests._helpers import clear_bindings, stored_output

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    """Module-level, like every other vision test module's copy: both
    classes below need it, and a per-class duplicate would be a second
    place to keep the same line."""
    clear_bindings()


class _Status:
    """The shape `core.inference.queue.get_job` returns (console-side
    `JobStatus`), reduced to what this page reads."""

    def __init__(self, state="queued", position=1, result=None, error=""):
        self.state = state
        self.position = position
        self.result = result
        self.error = error


@pytest.mark.django_db
class TestQueueJobStatus:
    def test_a_queued_job_renders_the_placeholder_with_its_position(self, client):
        with patch("modules.vision.views.get_job", return_value=_Status(position=3)):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        assert response.status_code == 200
        body = response.content.decode()
        assert "position 3" in body
        assert reverse("vision-queue-status", args=[7]) in body

    def test_a_running_job_still_polls(self, client):
        with patch("modules.vision.views.get_job", return_value=_Status(state="running", position=None)):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        assert 'data-job-poll' in response.content.decode()

    def test_the_placeholder_becomes_the_real_card_as_soon_as_the_row_exists(
        self, client, tmp_path
    ):
        """The whole point of `queue_job_id`: the swap happens while the
        generation is running, not when the queue job finishes."""
        job = stored_output(tmp_path).job
        GenerationJob.objects.filter(pk=job.pk).update(queue_job_id=7)

        with patch("modules.vision.views.get_job", return_value=_Status(state="running")):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        body = response.content.decode()
        assert f"job-{job.id}" in body
        assert "a lighthouse" in body

    def test_a_finished_queue_job_finds_the_generation_through_its_result(
        self, client, tmp_path
    ):
        """The fallback for a row written before correlation existed, or a
        result read after the row was found by id: the result names the
        job it created."""
        job = stored_output(tmp_path).job
        with patch(
            "modules.vision.views.get_job",
            return_value=_Status(state="succeeded", position=None, result={"job_id": str(job.id)}),
        ):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        assert f"job-{job.id}" in response.content.decode()

    def test_a_failed_queue_job_shows_its_error_and_stops_polling(self, client):
        with patch(
            "modules.vision.views.get_job",
            return_value=_Status(state="failed", position=None, error="No model assigned"),
        ):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        body = response.content.decode()
        assert "No model assigned" in body
        assert "data-job-poll" not in body

    def test_a_pruned_queue_row_never_hides_a_live_generation(self, client, tmp_path):
        """Retention prunes terminal queue rows on every enqueue, and a
        queue job can succeed (`timed_out`) while its generation is still
        running. The generation is the durable record, so it is looked up
        first and a vanished queue row is never reported as a removed
        generation."""
        job = stored_output(tmp_path).job
        GenerationJob.objects.filter(pk=job.pk).update(queue_job_id=7)

        with patch("modules.vision.views.get_job", return_value=None) as mock_get_job:
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        assert response.status_code == 200
        assert f"job-{job.id}" in response.content.decode()
        mock_get_job.assert_not_called()

    def test_an_unknown_queue_job_with_no_generation_is_a_404(self, client):
        with patch("modules.vision.views.get_job", return_value=None):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        assert response.status_code == 404  # nothing in the DB, nothing in the queue

    def test_an_unreachable_queue_reports_503_without_500ing(self, client):
        from core.inference.queue import QueueUnavailable

        with patch("modules.vision.views.get_job", side_effect=QueueUnavailable()):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        assert response.status_code == 503
        assert "migrations" in response.content.decode()

    def test_a_malformed_job_id_in_a_result_does_not_500(self, client):
        with patch(
            "modules.vision.views.get_job",
            return_value=_Status(state="succeeded", position=None, result={"job_id": "not-a-uuid"}),
        ):
            response = client.get(reverse("vision-queue-status", args=[7]), **XHR)

        assert response.status_code == 200


@pytest.mark.django_db
class TestCreatePageQueuedPlaceholder:
    """The no-JS path: a plain POST redirects back with `?queued=<id>`, and
    the page has to show something for it."""

    def test_the_placeholder_is_rendered_for_a_live_queue_job(self, client):
        with patch("modules.vision.views.get_job", return_value=_Status(position=2)):
            response = client.get(reverse("vision-create") + "?queued=7")

        assert "position 2" in response.content.decode()

    def test_no_placeholder_once_the_generation_row_exists(self, client, tmp_path):
        """The Recent list is already showing the real card; a second card
        for the same submission would be a lie about how much is queued.

        Asserted by ELEMENT ID, never by counting `data-job-poll`: the
        literal appears five more times inside create.html's own polling
        script, and a finished job's card carries none at all."""
        job = stored_output(tmp_path).job
        GenerationJob.objects.filter(pk=job.pk).update(queue_job_id=7)
        with patch("modules.vision.views.get_job", return_value=_Status(state="running")):
            response = client.get(reverse("vision-create") + "?queued=7")

        body = response.content.decode()
        assert 'id="queue-job-7"' not in body
        assert f'id="job-{job.id}"' in body

    def test_a_stale_or_malformed_queued_parameter_leaves_a_normal_page(self, client):
        with patch("modules.vision.views.get_job", return_value=None):
            assert client.get(reverse("vision-create") + "?queued=7").status_code == 200
        assert client.get(reverse("vision-create") + "?queued=nonsense").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_views_queue.py -q`
Expected: FAIL — `django.urls.exceptions.NoReverseMatch: Reverse for 'vision-queue-status' not found`.

- [ ] **Step 3: Name the job kind once**

In `modules/vision/jobs.py`, near `GENERATE_WAIT_TIMEOUT_SECONDS`:

```python
# This module's job-kind key, named once: `apps.py` registers it, the page
# enqueues under it, and the queued card reads its label from the registry.
JOB_KIND = "vision.generate"
```

`modules/vision/apps.py::ready()` KEEPS the literal `"vision.generate"`. That method deliberately imports no handler module (its docstring: registration "never imports `modules.vision.jobs` itself" — the dotted paths are strings for exactly that reason), so importing `jobs` for a constant would undo a documented decision for a cosmetic gain. Add a comment saying so, and a test that the two strings agree:

```python
        register_job_kind(
            JobKind(
                # The literal, not `modules.vision.jobs.JOB_KIND`: this
                # method imports no handler module by design (see the
                # docstring above), which is also why the paths below are
                # strings. `jobs.JOB_KIND` is the same string, and
                # `test_apps.py` asserts they agree.
                key="vision.generate",
```

and add to `modules/vision/tests/test_apps.py` (which imports neither name today — add `from core.inference.jobkinds import get_job_kind` to its imports):

```python
    def test_the_registered_key_and_the_modules_constant_agree(self):
        from modules.vision.jobs import JOB_KIND

        assert JOB_KIND == "vision.generate"
        assert get_job_kind(JOB_KIND).handler == "modules.vision.jobs.run_generate"
```

- [ ] **Step 4: Write the placeholder template**

Create `modules/vision/templates/vision/_queued_card.html`:

```html
{% comment %}
The card for a submission that is IN THE QUEUE and has not started
generating yet. It stands in the Recent list exactly where the real job
card will stand, and `data-job-poll` points at `/vision/queue/<id>/`,
which answers with the REAL card (`_job_card.html`) the moment the worker
has created the generation row -- the script in create.html follows
whatever `data-job-poll` the fragment it receives carries, so the swap
needs no JavaScript of its own.

It names the job KIND, not the prompt: the queue's single-job read shape
(`console.jobs.backend.JobStatus`) deliberately carries no payload, and
neither widening that seam for a display string nor reflecting the
operator's prompt through the poll URL is worth it -- the prompt appears
one poll later, on the real card, with everything else about the job.
{% endcomment %}
<div class="card job-card" id="queue-job-{{ card.queue_job_id }}"
     {% if not card.terminal %}data-job-poll="{{ card.poll_url }}"{% endif %}>
  <div class="job-head">
    <strong>{{ card.label }}</strong>
    <span class="muted">{{ card.state_label }}</span>
  </div>

  {% if card.position %}
  <p class="muted">Waiting in the queue — position {{ card.position }}.</p>
  {% endif %}
  {% if card.error %}<p class="job-error">{{ card.error }}</p>{% endif %}
  {% if not card.terminal %}<p class="muted no-js-note">Refresh to update.</p>{% endif %}
</div>
```

- [ ] **Step 5: Write the view half**

In `modules/vision/views.py`, add imports:

```python
from core.inference.jobkinds import get_job_kind
from core.inference.queue import QueueUnavailable, get_job
from modules.vision.jobs import JOB_KIND
```

and the copy + helpers (put the message constants near the top, beside `RECENT_JOBS`):

```python
# The two honest failures a page that talks to the queue can hit, worded
# the way `modules/rag/views.py` words the same two.
_QUEUE_UNAVAILABLE_MESSAGE = "The queue isn't ready yet — run database migrations, then try again."
_QUEUE_ENQUEUE_FAILED_MESSAGE = "Couldn't add your generation to the queue — nothing was queued."

# How a queue state reads on a card. Written here rather than taken from
# the queue's own vocabulary because these are sentences for an operator
# watching one submission, not the queue page's status column.
_QUEUE_STATE_LABELS = {
    "queued": "Queued",
    "running": "Starting…",
    "succeeded": "Finished",
    "failed": "Failed",
    "cancelled": "Cancelled",
}
_QUEUE_TERMINAL_STATES = ("succeeded", "failed", "cancelled")


def _queue_card_context(queue_job_id: int, job_status) -> dict:
    """What `_queued_card.html` needs for one queued submission."""
    state = getattr(job_status, "state", "queued")
    # No raw `state` key: the template renders `state_label` and branches
    # on `terminal`, and a third spelling of the same fact is one more
    # thing to keep in sync. The queue's own vocabulary word is documented
    # in ADR 0012's mapping table, which is where it belongs.
    return {
        "queue_job_id": queue_job_id,
        "poll_url": reverse("vision-queue-status", args=[queue_job_id]),
        "label": get_job_kind(JOB_KIND).label,
        "state_label": _QUEUE_STATE_LABELS.get(state, state),
        "position": getattr(job_status, "position", None),
        "error": getattr(job_status, "error", "") or "",
        "terminal": state in _QUEUE_TERMINAL_STATES,
    }


def _generation_from_result(job_status) -> GenerationJob | None:
    """The generation a FINISHED queue job's result names, if any.

    The queue job's own `result` is the fallback for a generation whose
    row is not correlated yet -- one submitted before the column existed,
    or deleted and re-created. The `queue_job_id` lookup that runs first
    lives in `queue_job_status`. A malformed id in a result is treated as
    no job at all rather than a 500 -- a result is data, not a promise.
    """
    result = getattr(job_status, "result", None) or {}
    raw = result.get("job_id")
    if not raw:
        return None
    try:
        return GenerationJob.objects.filter(pk=raw).first()
    except (ValueError, ValidationError):
        return None


def queue_job_status(request, queue_job_id: int):
    """GET /vision/queue/<id>/ -- the queued placeholder's poll target.

    Answers with the REAL job card as soon as the queue job has produced a
    generation (correlated by `GenerationJob.queue_job_id`, looked up
    BEFORE the queue is consulted at all), and with the placeholder until
    then. The card the page is holding is replaced
    either way, so a submission goes queued -> running -> images with no
    gap and no second card.

    404 for a queue job the queue does not know (it may have aged out of
    the retention limit) -- the page's script replaces the card with "This
    generation was removed." 503 for a queue that cannot be read at all,
    the same unmigrated-window tolerance `AskView` gives its own enqueue.
    """
    # THE GENERATION FIRST, the queue second. A queue row is pruned to
    # `JobSettings.retention_limit` on every enqueue, so a succeeded queue
    # job -- including one that succeeded with `timed_out: true` while its
    # generation kept running -- can vanish from under a card that is
    # still polling. Asking the queue first would answer 404, and the
    # page's script would replace a live generation's card with "This
    # generation was removed." The generation is the durable record; only
    # when there is no generation AND no queue row is there nothing to
    # show.
    job = GenerationJob.objects.filter(queue_job_id=queue_job_id).first()
    if job is not None:
        return _render_card(request, services.refresh_job(job))

    try:
        job_status = get_job(queue_job_id)
    except QueueUnavailable:
        return render(
            request, "vision/_unavailable.html",
            {"message": _QUEUE_UNAVAILABLE_MESSAGE}, status=503,
        )
    if job_status is None:
        raise Http404(f"Queue job {queue_job_id} is no longer in the queue.")

    job = _generation_from_result(job_status)
    if job is not None:
        return _render_card(request, services.refresh_job(job))
    return render(
        request, "vision/_queued_card.html",
        {"card": _queue_card_context(queue_job_id, job_status)},
    )


def _queued_placeholder(request) -> dict | None:
    """The `?queued=<id>` card for the create page -- the no-JS path's
    answer to "where did my submission go".

    `None` for a missing, malformed, or unknown id, and `None` once the
    generation row exists: the Recent list is already showing the real
    card, and a second card for one submission would misreport how much is
    queued.
    """
    raw = request.GET.get("queued", "").strip()
    if not raw.isdigit():
        return None
    queue_job_id = int(raw)
    if GenerationJob.objects.filter(queue_job_id=queue_job_id).exists():
        return None
    try:
        job_status = get_job(queue_job_id)
    except QueueUnavailable:
        return None
    if job_status is None:
        return None
    return _queue_card_context(queue_job_id, job_status)
```

In `CreatePageView.get_context_data`, before `return context`:

```python
        context["queued_card"] = _queued_placeholder(self.request)
```

and in `_create_page_response`, add `"queued_card": _queued_placeholder(request),` to the context dict so a re-rendered page keeps the card too.

- [ ] **Step 6: Route it**

In `modules/vision/urls.py`, add `queue_job_status` to the import and:

```python
    # The queued placeholder's poll target: a queue job id, not a
    # generation UUID -- the generation does not exist yet when the page
    # starts polling. It answers with the real job card the moment it does.
    path("queue/<int:queue_job_id>/", queue_job_status, name="vision-queue-status"),
```

- [ ] **Step 7: Render it on the create page**

In `modules/vision/templates/vision/create.html`, inside `<div id="jobs">`, above the `{% for job in jobs %}` loop:

```html
  {% if queued_card %}{% include "vision/_queued_card.html" with card=queued_card %}{% endif %}
```

and change the empty branch so a page with only a queued card does not also say "Nothing generated yet": wrap the `{% empty %}` paragraph in `{% if not queued_card %}...{% endif %}`.

- [ ] **Step 8: Run the tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Expected: PASS.

- [ ] **Step 9: Update the README**

In `modules/vision/README.md`, "The poll-driven page and its no-JS fallback", add:

```markdown
A submission that is still in the execution queue shows a **queued placeholder
card** in the same place its real card will appear. It polls
`/vision/queue/<queue job id>/`, which answers with the real job card
(`_job_card.html`) as soon as the worker's `submit_job` has created the
generation row — found by `GenerationJob.queue_job_id`, looked up before the
queue is asked anything (so a queue row pruned by the retention limit can never
hide a live generation), so the swap happens while the generation runs, not when
the queue job finishes. The page's script needs no
change for this: it follows whatever `data-job-poll` the fragment it receives
carries. Without JavaScript, the plain POST redirects back with `?queued=<id>` and
the page renders the same card server-side. The placeholder names the job kind
rather than the prompt — the queue's single-job read shape carries no payload, and
the prompt arrives one poll later with the real card.
```

- [ ] **Step 10: Commit**

```bash
git add modules/vision/views.py modules/vision/urls.py modules/vision/jobs.py modules/vision/apps.py modules/vision/templates/vision/ modules/vision/tests/ modules/vision/README.md
git commit -m "$(cat <<'EOF'
feat(vision): a queued submission has a card of its own

Built before the page starts enqueuing, so the submit button is never
dead for a commit. The placeholder stands where the real card will
stand and polls /vision/queue/<id>/, which hands back the real job card
the moment queue_job_id correlates one -- no JavaScript change, because
the script already follows whatever data-job-poll it is given.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 13: `/vision/generate/` goes through the queue

Audit item 1, the defect the whole plan is for: the page calls `services.submit_job` directly while the registered `vision.generate` job kind has **zero callers**. Two paths means the scheduler's memory admission — which `plan_generate` declares `exclusive=True` against — is blind to every generation the UI starts, so it can admit a `rag.ask` LLM into VRAM while ComfyUI is mid-generation; and an agent arriving on the queue path gets different behaviour from the page.

**The shape, mirroring `AskView.post`:** validate → pre-check → enqueue → hand back something that polls. What the vision page adds is the file seam: uploads are staged (Task 11) into `input:<id>` references before the payload is built, because the payload must be JSON.

**Rulings this task makes explicit:**

1. **The form still validates in the view, and `validate_params` still runs there too.** The form is the live layer (real option lists, widget bounds) and `validate_params` is the schema floor; running both before enqueuing keeps today's UX exactly — an invalid submission is a 400 with per-field messages, immediately, and nothing is queued. `submit_job` validates again on the worker, which is not waste: `validate_params` is pure and idempotent, and the worker must never trust an enqueue-time decision (the same rule `run_ask` follows for its own re-check).
2. **The payload carries VALIDATED params, minus the file keys.** Validation resolves a blank seed to a real number, so the seed is pinned at submit time and visible in the queue payload rather than being redrawn on the worker. File params are named once, in `"inputs"`, exactly as `jobs.py`'s documented payload shape says.
3. **Preflight stays.** An unbound or unreachable engine is still a 503 with the page's own banner, before anything is queued — the same pre-check `AskView` runs, and the reason `plan_generate` is allowed to raise on an unbound role (a caller is expected to have preflighted).
4. **"An attached file wins over a carried reference" stays in the view** (Task 6's ruling) — it decides which reference goes in the payload.
5. **A failed enqueue discards what it staged.** Nothing will ever consume those uploads, so they go immediately rather than waiting for the sweep.

**Files:**
- Modify: `modules/vision/views.py::generate`
- Test: `modules/vision/tests/test_views_generate.py`

**Interfaces:**
- Consumes: `services.live_options` (T4), `services.resolve_inputs`/`InputReferenceError` (T6), `services.stage_upload`/`prune_staged_inputs`/`discard_staged_inputs` (T11), `_queue_card_context` (T12), `core.inference.queue.enqueue`.
- Produces: `POST /vision/generate/` → XHR: **202** with `_queued_card.html`; plain POST: redirect to the create page with `?queued=<queue job id>`. Payload: `{"operation": str, "params": dict, "inputs": {param_key: "input:<id>" | "output:<id>"}}`.

- [ ] **Step 1: Write the failing test**

Rewrite the submission tests in `modules/vision/tests/test_views_generate.py`. Keep every existing test that asserts *validation* behaviour (400s, form errors, 503s) — they must still pass, with `mock_enqueue.assert_not_called()` added where they assert nothing was submitted. Add:

```python
@pytest.mark.django_db
class TestGenerateEnqueues:
    """One submission path: the page enqueues `vision.generate`, the same
    job kind an agent enqueues, so the scheduler sees every generation and
    both callers get identical behaviour."""

    @patch("modules.vision.views.enqueue", return_value=12)
    def test_a_valid_submission_is_queued_never_submitted_directly(self, mock_enqueue, client):
        _bind()
        with _engine(), patch("modules.vision.views.services.submit_job") as mock_submit:
            response = client.post(reverse("vision-generate"), FORM, **XHR)

        assert response.status_code == 202
        mock_submit.assert_not_called()
        kind, payload = mock_enqueue.call_args.args
        assert kind == "vision.generate"
        assert payload["operation"] == "txt2img"
        assert payload["params"]["prompt"] == "a lighthouse"
        assert payload["inputs"] == {}

    @patch("modules.vision.views.enqueue", return_value=12)
    def test_the_payload_is_json(self, mock_enqueue, client):
        _bind()
        with _engine():
            client.post(reverse("vision-generate"), FORM, **XHR)
        payload = mock_enqueue.call_args.args[1]
        assert json.loads(json.dumps(payload)) == payload

    @patch("modules.vision.views.enqueue", return_value=12)
    def test_the_seed_is_pinned_at_submit_time(self, mock_enqueue, client):
        """Validated params, not raw ones: a blank seed becomes a real
        number here, so the queue payload records what will actually run."""
        _bind()
        blank_seed = {**FORM, "seed": ""}
        with _engine():
            client.post(reverse("vision-generate"), blank_seed, **XHR)
        assert isinstance(mock_enqueue.call_args.args[1]["params"]["seed"], int)

    @patch("modules.vision.views.enqueue", return_value=12)
    def test_an_uploaded_file_is_staged_and_travels_as_a_reference(self, mock_enqueue, client, tmp_path):
        _bind()
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        form = {**IMG2IMG_FORM, "init_image": upload}
        with _engine(), override_settings(GENERATED_DIR=tmp_path):
            response = client.post(reverse("vision-generate"), form, **XHR)

        assert response.status_code == 202
        payload = mock_enqueue.call_args.args[1]
        reference = payload["inputs"]["init_image"]
        assert reference.startswith("input:")
        staged = JobInput.objects.get(pk=int(reference.split(":")[1]))
        assert staged.job_id is None
        assert Path(staged.path).read_bytes() == PNG
        assert "init_image" not in payload["params"]

    @patch("modules.vision.views.enqueue", return_value=12)
    def test_a_carried_reference_travels_as_itself(self, mock_enqueue, client, tmp_path):
        _bind()
        output = stored_output(tmp_path)
        form = {**IMG2IMG_FORM, "input_init_image": f"output:{output.id}"}
        with _engine():
            client.post(reverse("vision-generate"), form, **XHR)
        assert mock_enqueue.call_args.args[1]["inputs"] == {
            "init_image": f"output:{output.id}"
        }

    @patch("modules.vision.views.enqueue", return_value=12)
    def test_an_attached_file_still_wins_over_a_carried_reference(
        self, mock_enqueue, client, tmp_path
    ):
        _bind()
        output = stored_output(tmp_path)
        form = {
            **IMG2IMG_FORM,
            "input_init_image": f"output:{output.id}",
            "init_image": SimpleUploadedFile("new.png", PNG, content_type="image/png"),
        }
        with _engine(), override_settings(GENERATED_DIR=tmp_path):
            client.post(reverse("vision-generate"), form, **XHR)
        assert mock_enqueue.call_args.args[1]["inputs"]["init_image"].startswith("input:")

    @patch("modules.vision.views.enqueue", return_value=12)
    def test_a_dead_carried_reference_is_a_400_on_its_own_field(
        self, mock_enqueue, client, tmp_path
    ):
        _bind()
        output = stored_output(tmp_path)
        os.remove(output.path)
        form = {**IMG2IMG_FORM, "input_init_image": f"output:{output.id}"}
        with _engine():
            response = client.post(reverse("vision-generate"), form, **XHR)

        assert response.status_code == 400
        assert "no longer on disk" in response.content.decode()
        mock_enqueue.assert_not_called()

    @patch("modules.vision.views.enqueue", return_value=12)
    def test_an_xhr_submission_gets_a_queued_card_that_polls(self, mock_enqueue, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM, **XHR)

        body = response.content.decode()
        assert reverse("vision-queue-status", args=[12]) in body
        assert "Queued" in body

    @patch("modules.vision.views.enqueue", return_value=12)
    def test_a_plain_post_redirects_with_the_queue_job_id(self, mock_enqueue, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM)

        assert response.status_code == 302
        assert response["Location"] == f"{reverse('vision-create')}?queued=12"

    @patch("modules.vision.views.enqueue", return_value=12)
    def test_a_plain_post_for_a_named_operation_redirects_to_that_operation(
        self, mock_enqueue, client, tmp_path
    ):
        _bind()
        with _engine(), override_settings(GENERATED_DIR=tmp_path):
            form = {**IMG2IMG_FORM, "init_image": SimpleUploadedFile("a.png", PNG)}
            response = client.post(reverse("vision-generate"), form)

        expected = reverse("vision-create-operation", args=["img2img"])
        assert response["Location"] == f"{expected}?queued=12"

    @patch("modules.vision.views.enqueue")
    def test_an_unbound_role_is_a_503_and_queues_nothing(self, mock_enqueue, client):
        response = client.post(reverse("vision-generate"), FORM, **XHR)

        assert response.status_code == 503
        mock_enqueue.assert_not_called()

    @patch("modules.vision.views.enqueue", side_effect=QueueUnavailable())
    def test_an_unmigrated_queue_is_a_503_that_says_so(self, mock_enqueue, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), FORM, **XHR)

        assert response.status_code == 503
        assert "migrations" in response.content.decode()

    @patch("modules.vision.views.enqueue", side_effect=RuntimeError("planner exploded"))
    def test_a_failed_enqueue_is_a_503_and_discards_what_it_staged(
        self, mock_enqueue, client, tmp_path
    ):
        """Nothing will ever consume those bytes, so they go now rather
        than waiting out the sweep."""
        _bind()
        form = {**IMG2IMG_FORM, "init_image": SimpleUploadedFile("a.png", PNG)}
        with _engine(), override_settings(GENERATED_DIR=tmp_path):
            response = client.post(reverse("vision-generate"), form, **XHR)

        assert response.status_code == 503
        assert JobInput.objects.count() == 0

    @patch("modules.vision.views.enqueue", return_value=12)
    def test_invalid_params_never_reach_the_queue(self, mock_enqueue, client):
        _bind()
        with _engine():
            response = client.post(reverse("vision-generate"), {**FORM, "steps": "9999"}, **XHR)

        assert response.status_code == 400
        mock_enqueue.assert_not_called()

    @patch("modules.vision.views.enqueue", return_value=12)
    def test_staging_sweeps_expired_uploads_as_it_goes(self, mock_enqueue, client, tmp_path):
        _bind()
        with override_settings(GENERATED_DIR=tmp_path):
            stale = services.stage_upload("init_image", SimpleUploadedFile("old.png", PNG))
            stale_id = int(stale.split(":")[1])
            JobInput.objects.filter(pk=stale_id).update(
                created_at=timezone.now() - timedelta(hours=48)
            )
            with _engine():
                client.post(
                    reverse("vision-generate"),
                    {**IMG2IMG_FORM, "init_image": SimpleUploadedFile("new.png", PNG)},
                    **XHR,
                )
        assert not JobInput.objects.filter(pk=stale_id).exists()
```

Add to the module: `IMG2IMG_FORM` (the same shape as `FORM` but with `denoise` and without `width`/`height`), and the imports `json`, `Path`, `timedelta`, `timezone`, `override_settings`, `QueueUnavailable`, `JobInput`, `services`, `stored_output`.

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_views_generate.py::TestGenerateEnqueues -q`
Expected: FAIL — `AttributeError: <module 'modules.vision.views'> does not have the attribute 'enqueue'`.

- [ ] **Step 3: Rewrite `generate`**

In `modules/vision/views.py`, add `enqueue` to the queue import and `validate_params` to the operations import, then replace `generate` entirely:

```python
@require_POST
def generate(request):
    """POST /vision/generate/ -- validate, then QUEUE one generation.

    The page enqueues `vision.generate` (`core.inference.queue.enqueue`)
    rather than calling `services.submit_job` itself, exactly as
    `modules.rag.views.AskView.post` enqueues `rag.ask`. One submission
    path for the page, an agent, and a management command means the
    scheduler's memory admission sees every generation there is -- while
    the page submitted directly, it could admit a language model into VRAM
    with a generation already running -- and it means a tool cannot get
    behaviour the page does not have.

    What still happens HERE, before anything is queued:

    - the FORM validates (the live layer: real option lists, widget
      bounds) and `validate_params` validates (the schema floor), so an
      invalid submission is a 400 with per-field messages immediately and
      nothing is queued. The worker validates again inside `submit_job`;
      that is not waste but the rule every queued job follows -- a job
      never trusts an enqueue-time decision as its run-time truth.
    - `preflight()` runs, so an unbound role or an unreachable engine is
      the same honest 503 banner it has always been, and `plan_generate`
      is called (at enqueue time) by a caller that has already checked.
    - every file becomes a REFERENCE: an upload is staged into the managed
      store (`services.stage_upload`) and a carried "use this image"
      reference is resolved once to prove it is live. A queue payload is
      JSON; nothing but strings go into it.

    Answers with the QUEUED placeholder card (202, XHR) or a redirect
    carrying `?queued=<id>` (no JS) -- either way the operator sees a card
    in Recent immediately, and it becomes the real job card the moment the
    worker creates the generation row.
    """
    operation = resolve_page_operation(request.POST.get("operation"))
    check = services.preflight()
    refs = stored_input_refs(request, operation)
    form = build_form_for(request, operation, check, stored_keys=frozenset(refs))

    if not form.is_valid():
        return _invalid_form_response(request, operation, check, form)

    if not check.ready:
        if _is_xhr(request):
            return render(
                request, "vision/_unavailable.html", {"message": check.message}, status=503
            )
        return _create_page_response(request, operation, check, form, status=503)

    # An attached file always wins over a carried reference: the operator
    # picking a new file is them changing their mind, and the form still
    # rendered the field for exactly that. This is the only layer that can
    # see both candidates, so the rule lives here and the resolver never
    # sees a conflict.
    file_keys = operation.file_param_keys()
    uploads = {key: value for key, value in request.FILES.items() if key in file_keys}
    carried = {key: ref for key, ref in refs.items() if key not in uploads}

    try:
        files = dict(uploads)
        # Resolved to prove the reference is live before anything is
        # queued -- a `StoredFile` is a lazy handle, so this costs a row
        # read and no file read. A dead reference must fail on the page,
        # where the operator can fix it, not on a worker minutes later.
        files.update(services.resolve_inputs(operation, carried))
        params = validate_params(operation, {**form.cleaned_data, **files})
    except services.InputReferenceError as exc:
        form.add_error(exc.param_key if exc.param_key in form.fields else None, str(exc))
        return _invalid_form_response(request, operation, check, form)
    except ParamError as exc:
        for key, message in exc.errors.items():
            form.add_error(key if key in form.fields else None, message)
        return _invalid_form_response(request, operation, check, form)

    # A file is named ONCE, in `inputs` -- `submit_job` merges the resolved
    # files back into the params it validates on the worker and derives the
    # basename label itself, so a file key in `params` here would be a
    # second, staler name for the same thing.
    payload_params = {key: value for key, value in params.items() if key not in file_keys}

    services.prune_staged_inputs()
    staged: list[str] = []
    inputs = dict(carried)
    for key, uploaded in uploads.items():
        reference = services.stage_upload(key, uploaded)
        staged.append(reference)
        inputs[key] = reference

    payload = {"operation": operation.key, "params": payload_params, "inputs": inputs}

    try:
        queue_job_id = enqueue(JOB_KIND, payload)
    except QueueUnavailable:
        services.discard_staged_inputs(staged)
        return _queue_failure_response(request, operation, check, form, _QUEUE_UNAVAILABLE_MESSAGE)
    except Exception:  # noqa: BLE001 -- log detail, then degrade to a clean 503
        logger.exception("Failed to enqueue a %s job", JOB_KIND)
        services.discard_staged_inputs(staged)
        return _queue_failure_response(request, operation, check, form, _QUEUE_ENQUEUE_FAILED_MESSAGE)

    if _is_xhr(request):
        return render(
            request,
            "vision/_queued_card.html",
            {"card": _queue_card_context(queue_job_id, None)},
            status=202,
        )
    return redirect(f"{_create_url(operation)}?queued={queue_job_id}")


def _create_url(operation: Operation) -> str:
    """The create page's URL for `operation` -- the bare `/vision/` for the
    default one, the named route for any other, matching where a GET for
    that operation lands."""
    if operation.key == page_operations()[0].key:
        return reverse("vision-create")
    return reverse("vision-create-operation", args=[operation.key])


def _queue_failure_response(request, operation: Operation, check, form, message: str):
    """The 503 for a submission that could not be queued -- the same two
    shapes an unavailable engine gets, so the page has one way of saying
    "not now": the small banner fragment for XHR, the whole page for a
    plain POST."""
    if _is_xhr(request):
        return render(request, "vision/_unavailable.html", {"message": message}, status=503)
    form.add_error(None, message)
    return _create_page_response(request, operation, check, form, status=503)
```

`JOB_KIND` is imported from `modules.vision.jobs` in Task 12; `enqueue` and `QueueUnavailable` come from `core.inference.queue`, imported there too.

- [ ] **Step 4: Run the tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Expected: PASS. Two categories of existing test will need rewriting rather than passing as-is, and both are honest changes to assert:
- tests that asserted a `GenerationJob` row exists after a POST → assert the payload that was enqueued instead;
- tests that asserted the response body contains a job card → assert the queued placeholder and its poll URL.
Each rewritten test keeps its original name where the behaviour it names still exists, and gains a docstring line saying the submission is queued now.

- [ ] **Step 5: Run the whole suite**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Update the README**

In `modules/vision/README.md`, rewrite the head of "Queued generation":

```markdown
## Queued generation

**Every generation goes through the queue, including the page's own.**
`POST /vision/generate/` validates (form + `validate_params`), preflights, turns
each file into a reference, and calls
`core.inference.queue.enqueue("vision.generate", payload)` — the same call an
agent makes. It never calls `submit_job` itself. That is what lets the
scheduler's memory admission see every generation there is: while the page
submitted directly, the scheduler could admit a language model into VRAM with a
generation already running, because it had never heard of it.

A payload is `{"operation": str, "params": dict, "inputs": dict}`. `params` are
the VALIDATED params minus every file key — so the seed is pinned at submit time
and a file is named once, in `inputs`, as `"output:<id>"` (a gallery result),
`"input:<id>"` (another job's input) or the `"input:<id>"` of an upload the page
staged for exactly this submission.
```

- [ ] **Step 7: Commit**

```bash
git add modules/vision/views.py modules/vision/tests/test_views_generate.py modules/vision/README.md
git commit -m "$(cat <<'EOF'
feat(vision): the page submits through the queue like everything else

/vision/generate/ called submit_job directly while the registered
vision.generate job kind had no callers at all -- so the scheduler was
blind to every generation the UI started, and an agent on the queue path
got different behaviour from the page. The page enqueues now: validate,
preflight, stage each file into a reference, enqueue, and hand back a
queued card that becomes the real one.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 14: ADR 0012 amendment — the queue payload is a contract

Audit item 9 ("document the queue payload as a contract; don't rename `done`"), plus the duplication report's naming-collision note. Docs only — no behaviour changes, so the tests that must pass are the ones already written.

**Files:**
- Modify: `docs/adr/0012-image-generation-engine-adapter.md`, `modules/vision/README.md`

- [ ] **Step 1: Write the ADR amendment**

In `docs/adr/0012-image-generation-engine-adapter.md`, after the section "A stored file has a reference (2026-08-24)", insert verbatim:

```markdown
### One submission path, and the payload that travels it (2026-08-24)

D10 recorded that "a worker can be added later for unattended/batch runs
without changing the service API". The worker arrived (ADR 0013), and this
records what happened when it did: **the `/vision/` page no longer calls
`services.submit_job` itself.** `POST /vision/generate/` validates, preflights,
and calls `core.inference.queue.enqueue("vision.generate", payload)` — the same
call any other caller makes — exactly as `modules/rag/views.py::AskView.post`
enqueues `rag.ask`.

The reason is not tidiness. While the page submitted directly, the execution
queue's scheduler had never heard of any generation the UI started, so its
memory admission — which `modules.vision.jobs.plan_generate` declares
`exclusive=True` against — could admit a language model into VRAM with ComfyUI
mid-generation. A second submission path is also a second behaviour: an agent
arriving on the queue path would get retention, priority and cancellation that
the page did not.

**The payload is a contract.** `{"operation": str, "params": dict, "inputs":
dict}`:

- `operation` — a registered `core.inference.operations.Operation` key.
- `params` — that operation's parameter dict, minus every file param. The page
  enqueues `validate_params` OUTPUT (so a blank seed is already resolved and the
  queue payload records what will actually run); a caller that enqueues raw
  params is equally valid, because `submit_job` validates on the worker either
  way. Validation is pure and idempotent, and a queued job never trusts an
  enqueue-time decision as its run-time truth.
- `inputs` — `{file param key: reference}`, where a reference is
  `"output:<GeneratedOutput id>"` or `"input:<JobInput id>"`. A file is named
  here and nowhere else. A browser upload cannot ride a JSON payload, so the page
  writes it into the managed store first and records it as a `JobInput` with no
  job yet — a *staged* upload, referenced by the same `input:<id>` kind as
  anything else, resolved by the same `services.resolve_inputs`, and copied into
  the job's own directory by the same `submit_job`. One reference kind, one
  resolver, one file-handling path. Staged uploads are swept by age
  (`VISION_STAGED_UPLOAD_TTL`), never on consumption: the queue's orphan sweep
  can re-run a job, and a payload whose references had been deleted would fail a
  re-run that would otherwise have worked.

**The result is a contract too.** `run_generate` returns `{"job_id": str,
"status": str, "timed_out": bool, "output_ids": [int], "output_urls": [str]}`.
`status` is the `GenerationJob.Status` value at the moment the handler stopped
waiting, and `timed_out` says whether it stopped because the wall-clock budget
elapsed rather than because the generation finished. An engine-side failure is a
normal outcome reported through `status`, never a raised handler — the same rule
`submit_job` already followed for an engine rejection.

**Two status vocabularies, both kept.** The queue's job states are `queued`,
`running`, `succeeded`, `failed`, `cancelled`
(`console/jobs/models.py`); a generation's own are `queued`, `running`, `done`,
`failed` (`GenerationJob.Status`). They are not the same axis and are not
merged: a `vision.generate` queue job SUCCEEDS when the handler returned
honestly, including when the generation it ran `failed`. `done` is not renamed to
`succeeded` — it is the word the page has always shown for a finished image, and
renaming a display value to match an unrelated table's vocabulary would trade a
real user-facing word for a false symmetry. The mapping is written down instead:

| Queue job state | What it means for the generation |
|---|---|
| `queued` / `running` | The generation may not exist yet. The page shows a placeholder card and polls `/vision/queue/<id>/`. |
| `succeeded` | The handler returned. The generation is `done`, `failed`, or (with `timed_out: true`) still running on the engine. |
| `failed` | The handler raised — an unbound role (`VisionUnavailable`), invalid params (`ParamError`), or an unresolvable input reference. No generation row exists. |
| `cancelled` | The operator cancelled it before it was claimed. No generation row exists. |

**Correlation while it runs.** `GenerationJob.queue_job_id` records the queue row
that submitted the generation: `modules.vision.jobs.run_generate` stamps
`ctx.job_id` — the claimed row's id, from the `JobContext` every handler receives
(ADR 0013 §8) — as soon as `submit_job` returns. Without it, a generation's id
was revealed only in the finished result, so nothing could link a running queue
row to the images it was producing. `/vision/queue/<id>/` reads this column
before it consults the queue at all, so a queue row pruned by the retention
limit never hides a generation that is still running.

**Failures name themselves.** A failed generation carries `failure_kind` beside
its prose `error` — `engine_rejected`, `engine_failed`, `lost` on the row, and
`params_invalid` / `role_unbound` / `engine_unreachable` on the exceptions that
are raised before any row exists. One vocabulary
(`GenerationJob.FailureKind`), two carriers, so a caller can tell "fix your
parameters" from "try again later" without pattern-matching English.
```

- [ ] **Step 2: Retire the stale "no worker" line**

In the same ADR, D10's cell says "Completion is poll-driven; no worker, no task queue." Add a sentence to the end of that cell rather than rewriting the decision (the reasoning still stands and the polling design is unchanged):

```markdown
*(2026-08-24: a worker exists now — ADR 0013's execution queue — and the page
submits through it. `refresh_job` is unchanged and still poll-driven; what
changed is who calls `submit_job`. See "One submission path, and the payload that
travels it" below.)*
```

- [ ] **Step 3: Write the naming-collision note**

In `modules/vision/README.md`, immediately under the "## Role, capability, and the feature flag" heading:

```markdown
> **"vision" names two unrelated things in this codebase.** The pre-existing
> `"vision"` *capability* (`core/inference/roles.py::CAPABILITIES`) means a model
> that can **read** images — LLaVA-style multimodal chat. This app, its Django
> label, and its `FARABUNKER_FEATURES` flag are also called `vision`, and it
> **generates** images: its operations register under the separate
> `"image-generation"` capability, and its role is `vision.generate`. Nothing is
> wrong — capability routing is correct everywhere — but a grep for "vision"
> returns both, so check which one you are looking at before changing anything.
> Renaming either is a deliberate call nobody has made.
```

- [ ] **Step 4: Verify the docs claim nothing untrue**

Re-read the amendment against the shipped code. Every sentence must be checkable:
- payload shape → `modules/vision/views.py::generate` and `modules/vision/jobs.py`'s module docstring;
- result shape → `run_generate`'s return;
- the state table → `console/jobs/models.py` and `modules/vision/models.py::GenerationJob.Status`;
- correlation → `services.submit_job`'s `queue_job_id` argument;
- failure kinds → `GenerationJob.FailureKind`.

- [ ] **Step 5: Run the suite (docs must not have broken anything)**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add docs/adr/0012-image-generation-engine-adapter.md modules/vision/README.md
git commit -m "$(cat <<'EOF'
docs(adr-0012): the queue payload, result and status vocabularies

Records what the page's convergence on the queue actually promises: the
{operation, params, inputs} payload, the result dict, the correlation
column, the failure-kind vocabulary, and the mapping between the queue's
job states and a generation's own -- with `done` deliberately NOT renamed
to `succeeded`. Also notes the "vision" capability/app name collision in
the module README.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 15: One PNG builder in the test helpers

Duplication report's third finding: `test_store.py` builds a PNG header with `struct.pack` while `_helpers.PNG` is a fixed 512×512 constant written specifically to end that duplication.

**Files:**
- Modify: `modules/vision/tests/_helpers.py`, `modules/vision/tests/test_store.py`

**Interfaces:**
- Produces: `_helpers.png_bytes(width: int, height: int) -> bytes`, with `PNG` redefined in terms of it.

- [ ] **Step 1: Write the failing test**

In `modules/vision/tests/test_store.py`, replace the local `_png` definition's uses by importing the helper, and add one test that pins the relationship:

```python
from modules.vision.tests._helpers import PNG, png_bytes


class TestPngHelper:
    def test_the_shared_constant_is_the_builder_at_512(self):
        """One piece of PNG-header knowledge in the suite, not two: the
        fixed constant every other module uses IS this builder's output."""
        assert PNG == png_bytes(512, 512)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_store.py -q`
Expected: FAIL — `ImportError: cannot import name 'png_bytes'`.

- [ ] **Step 3: Move the builder into `_helpers`**

In `modules/vision/tests/_helpers.py`, replace the `PNG` constant block (lines 377-388 after the merge) with:

```python
def png_bytes(width: int, height: int) -> bytes:
    """The smallest byte string `store.png_dimensions` can measure: the PNG
    signature plus an IHDR chunk whose width/height sit at bytes 16-24
    (`modules/vision/store.py`).

    ONE piece of PNG-header knowledge in this suite. `test_store.py` needs
    varying dimensions (it is testing the measurement); every other module
    needs one image and uses `PNG` below.
    """
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"
    )


# The one image the rest of the suite uses, 512x512 -- what the view tests
# already assumed. `test_jobs.py`, `test_services.py` and
# `test_views_generate.py` each carried their own before this existed.
PNG = png_bytes(512, 512)
```

Add `import struct` to `_helpers.py` — it imports only `dataclasses`, `typing`, `httpx`, the console models and `JobContext` today.

- [ ] **Step 4: Drop the local copy**

In `modules/vision/tests/test_store.py`, delete the local `_png` function and replace every `_png(` call with `png_bytes(`. Remove the now-unused `import struct` if nothing else in the file uses it.

- [ ] **Step 5: Run the tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add modules/vision/tests/_helpers.py modules/vision/tests/test_store.py
git commit -m "$(cat <<'EOF'
test(vision): one PNG-header builder in the helpers

test_store.py built the same signature-plus-IHDR bytes a second way,
parametrized by size. png_bytes(width, height) lives in _helpers now and
the shared PNG constant is its 512x512 output, so the suite knows the
format in exactly one place.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 16: Full verification

The charter's rule: run natively, both orders, one at a time. Nothing here is optional and nothing here is a claim until its output has been read.

**Files:** none modified unless a failure demands it.

- [ ] **Step 1: Migration state**

Run:
```bash
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python manage.py makemigrations --check --dry-run
```
Expected: `No changes detected`.

- [ ] **Step 2: Full suite, default order**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest -q`
Expected: PASS, with a count at or above the 1760-passed / 1-skipped baseline (the post-merge number — this plan adds tests and removes only tests whose behaviour no longer exists).

- [ ] **Step 3: Full suite, the other order**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console modules scripts -q`
Expected: PASS with the same counts. This is the same set of tests as Step 2 (`pytest.ini`'s `testpaths` is `modules console scripts`), collected in a different order: `console/` first, so a registry the vision app populates at import time (operations, roles, job kinds) is exercised in the opposite sequence — the leak this repo's no-conftest convention guards against. Naming the paths explicitly is what makes the order differ; nothing else about the run changes.

- [ ] **Step 4: Read both counts and compare them**

Write down the two `N passed, M skipped` lines. They must match each other. If they do not, a test is order-dependent — fix the test (usually a registry left dirty by a `patch.dict` that was not scoped), never the ordering.

- [ ] **Step 5: Confirm the audit's scope is actually closed**

Walk the audit's ranked list and name the task that closed each item: 1 → Task 13; 2 → Tasks 3+4; 3 → Task 5; 4 → Task 6; 5 → Tasks 7+9; 6 → Tasks 7+8; 7 → Task 10; 8 → Task 2 **in part** (the schema half: the field and every description string; the "UI help text needs the same source" half is declined by Task 2's ruling, because `help_text` already carries situational copy that must win — report it that way, not as closed); 9 → Task 14; duplication → Tasks 1, 14 (naming note), 15. Item 10 is out of scope by instruction. Anything else unaccounted for is a gap to report, not to quietly leave.

Also run the grep gate the review asked for, so no handler is left on an old shape by a future edit: `grep -rn "payload, models" --include="*.py" . | grep -v /\.venv/` — every hit must be a `handler(payload, models, ctx)` signature or a docstring quoting it.

- [ ] **Step 6: Report, do not claim**

Report the two suite results verbatim, the migration check output, and the scope walk. Per the verification doctrine, no "done"/"working" language for the page itself until someone has actually loaded `/vision/`, submitted a generation, and watched the queued card become a real one — that browser pass is the follow-up to this plan, not part of it.

---

## Notes for the executor

**The one thing to get right in Task 13.** The seam is: *a browser file cannot ride a JSON payload, and the `GenerationJob` does not exist yet.* Everything else follows from that. The upload is written into the managed store and recorded as a `JobInput` with `job = NULL`; the payload carries its ordinary `input:<id>` reference; the worker's `submit_job` resolves it, copies the bytes into the job's own directory, and writes the job's own `JobInput` — exactly what it already does for a file posted directly. If you find yourself writing a second function that stores bytes for a job, or a second reference kind, stop: that is the failure mode this design exists to avoid.

**Progress reporting is deferred, by ruling.** Every handler now receives a `JobContext` with `report_progress` on it, and `run_generate` does not call it: `services.wait_for` blocks inside its own polling loop with no callback seam, so wiring progress means changing `wait_for`'s signature. That belongs to the UI phase (orchestrator ruling), not to this plan — do not add it here, and do not remove the docstring paragraph in `run_generate` that explains the deferral.

**What must still be true when you finish.**
- `grep -rn "submit_job" modules/vision/views.py` returns nothing.
- `grep -rn "payload, models" --include="*.py" . | grep -v /\.venv/` shows only `handler(payload, models, ctx)`-shaped signatures and docstrings quoting them.
- `services.py` holds every generation behaviour; `views.py` holds none.
- A submission with JavaScript off still shows a card.
- `data-job-poll` is the only thing the page's script knows about polling — no queue-specific JavaScript was added.

**Known limits, recorded rather than fixed (out of scope, do not expand into them):**
- `/vision/`'s mutation endpoints remain unauthenticated (the Phase-1 gap ADR 0012 already records; Phase 2 owns it).
- There is no cancel button for a queued generation. `queue_job_id` is what a future one needs; the button is not in this plan.
- `operation_catalog()` has no HTTP endpoint yet — it is a service function with tests as its callers, and a `?format=json` schema endpoint is the obvious next consumer. Adding one was left out deliberately: the audit's item 2 is the seam, not the route.
- `Param.description` is not rendered by the form layer (Task 2's ruling explains why).
- No engine reports a `loaded_footprint` for `image-generation`, so `plan_generate` still declares `exclusive=True` unconditionally. Unchanged by this plan, and still the honest default.

---

## Review history

**r1 (2026-08-24) — verdict AMEND, 18 findings, applied in one round together with a contract shift.**

- Mechanics: **M2** (`StubGenerator` is scripted through its constructor — `states=[JobStatus(...)]`, `submit_error=` — never by assigning over its methods), **M4** (counting `data-job-poll` cannot work: the literal appears five more times in create.html's own script; assert element ids), **M5** (`test_services.py`'s binder is `_bind()` at line 53, and its `_clear_bindings`/`_reset_engine_caches` fixtures are module-level autouse — the plan's per-class copies are gone), **M6** (`test_apps.py` needs `get_job_kind` imported), **M7** (`services.py` does not import `settings` today), **M8** (`live_options` is `views.py:108-148`; Task 16's second order is the same test set collected in a different sequence, and says so).
- Simplicity: **S1** (`queue_job_status` now reads `GenerationJob` by `queue_job_id` BEFORE it asks the queue — retention prunes terminal queue rows on every enqueue, and a pruned row must never turn a live generation's card into "removed"), **S2** (Task 6 changes only the resolver call inside `generate`; the body is Task 13's), **S4** (prune-on-write is unconditional in both code and prose).
- Duplication/alignment: **D1** (the chosen upload design copies the bytes twice as well — stated as the deliberate consequence of ADR 0012's "each job owns its inputs" rule instead of being used as a rejection argument), **D2** (`jobs.py` loses its `store` import with `_payload_files`), **D3** (`VISION_GENERATE_ROLE` is already imported in `services.py`; only `IMAGE_GENERATION_CAPABILITY` is new).
- Value/YAGNI: **V1** (the scope walk reports audit item 8 as half-closed — schema yes, help-text wiring declined), **V2** (`_queue_card_context` drops its unrendered `state` key), **V3** (`PARAM_ERROR_FAILURE_KIND` dropped; `params_invalid` is named in `FailureKind`'s docstring and in ADR 0012, and the test now pins the behaviour instead of the alias).
- **M1, M3, S3 superseded** by the contract shift below (they targeted the keyword-argument design). Their failure modes are not reintroduced: no handler signature changes at all, so `run_reencode`/`run_ask` cannot break, no worker test handler is rewritten, and no fabricated worker-test helper is invoked. M1's grep gate survives as a verification step in Task 16 and in the executor notes.

**Contract shift (merge `df251c3`, main into this branch).** The queue's handler contract is now `handler(payload, models, ctx)` with `ctx: core.inference.jobkinds.JobContext` (frozen; `job_id`, `attempt`, `checkpoint_state`, `report_progress`, `checkpoint`), and every handler in the repo — `run_ask`, `run_reencode`, `run_generate` — is already migrated, with `make_job_ctx` in `modules/vision/tests/_helpers.py` for direct calls. **Task 9 was rewritten wholesale** to consume it: `run_generate` stamps `ctx.job_id` onto the `GenerationJob` right after `submit_job` returns, and nothing else. All edits to `console/jobs/worker.py`, its tests, `modules/rag/jobs.py` and ADR 0013 are deleted; ADR 0013 §5/§8 are referenced, never amended. `submit_job` keeps its signature. Progress wiring via `ctx.report_progress` is deferred to the UI phase by orchestrator ruling.

**r2 (2026-08-24) — scoped re-check, verdict AMEND (minor), 4 nits, applied.** **N1**: `_generation_from_result`'s docstring still described the `queue_job_id`-first lookup that S1 moved up into `queue_job_status` — it now describes only the result fallback and points at where the first lookup lives. **N2**: `test_views_queue.py` gets ONE module-level `_clear_bindings` autouse fixture instead of two per-class copies, and drops the unused `PNG` import (`stored_output` writes those bytes itself). **N3**: the grep gate is `--include="*.py"` in both places — unquoted, zsh tries to expand the glob. **N4**: citation off-by-ones corrected (`test_services.py:33` → `:34` for `RAW`, `:63` → `:62` for `_registered`, `_helpers.py:318-345` → `:310-345` for the `StubGenerator` class) and the dependency table's Task 6 row no longer credits Task 9 with a `resolve_inputs` call it does not make.

**Post-merge re-verification.** Every file:line citation was re-checked against the merged worktree. Corrected: `views.py:108-152` → `108-148`; `config/settings.py:178` → `:207`. Confirmed unchanged: `core/inference/operations.py:325-421`, the four `_fail` sites at `services.py:280/283/325/328` (definition at `:375`), migration `0004` as the next number. Newly cited: `jobkinds.py:57-140` (`JobContext`), `jobs.py:149` (`run_generate`), `_helpers.py:55` (`make_job_ctx`), `:310-345` (`StubGenerator`), `:377-388` (`PNG`), `test_services.py:34/40-49/53/62`. Suite baseline for Task 16's arithmetic is now **1760 passed / 1 skipped** in both orders.
