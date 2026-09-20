# Vision Enhancements Implementation Plan — img2img, inpaint, LoRA, upscale

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development, run **STRICTLY SEQUENTIALLY** — one task, one subagent, one review gate, then the next. These tasks are **not** independent (that skill's usual parallel dispatch does not apply here): each depends on every task before it, and two run concurrently or out of order produce red suites that look like plan defects. Never dispatch two tasks at once. Steps use checkbox (`- [ ]`) syntax for tracking.

**Task dependencies** (beyond the strict 1→10 order, these are the hard ones — a task run without its dependency fails at import or at validation, not at review):

| Task | Depends on | Why |
|---|---|---|
| 5 | 2, 4 | its tests register `IMG2IMG`; its `generate` calls `services.stored_input` |
| 6 | 5 | `INPAINT.file_params()` needs `Operation.file_params()` |
| 8 | 7 | `LORA_PARAMS` are `"asset"` params, which only render and validate after Task 7 |
| 9 | 4, 7 | `submit_job("upscale", …, files=…)` only validates via Task 4's files/params merge; `upscale_model` only renders via Task 7's asset widget |

**Goal:** Ship the four remaining image-generation modes — img2img, inpainting, LoRA controls, and upscaling — plus the gallery hand-off that makes the image-input modes usable (feeding an existing result back in as an input), entirely through the extension points ADR 0012 already promises, adding no parallel code path anywhere.

**Architecture:** Three seams do all the work and none of them is new. A mode is an `Operation` (schema) plus one ComfyUI graph template; the page renders its form, its facts line, and its mode chooser from that schema; the engine adapter transfers file inputs inside `submit`. This plan adds a shared graph-fragment layer under `comfyui_workflows/` so four templates share one checkpoint/prompt/sampler/decode vocabulary instead of copying it; teaches `"asset"` params to render, validate, and reach the engine's own asset lists; and gives a file already in the managed store a JSON-safe reference (`output:<id>` / `input:<id>`) so a queued job — and the gallery's "use in <mode>" link — can carry an image without carrying bytes.

**Declared deliverable beyond the four modes: the gallery hand-off (Task 5).** Three of the four modes take an image as input, and until this ships the only way to give them one is to download a result and re-upload it. It is also the second caller of Task 4's stored-file resolver — the mechanism the queue needs anyway — so it adds a UI entry point over an existing seam rather than a seam of its own. Recorded here so it reads as stated scope, not as something that arrived inside another task.

**Tech Stack:** Python 3, Django (templates, forms, migrations), `httpx` (engine HTTP), pytest + `pytest-django`, ComfyUI HTTP API (`/prompt`, `/history`, `/queue`, `/view`, `/upload/image`, `/object_info`, `/system_stats`), PostgreSQL.

**Spec:** `docs/superpowers/specs/2026-08-22-image-generation-design.md` (binding design; §9 is the deferred list this plan closes), read with `docs/adr/0012-image-generation-engine-adapter.md` (the decisions as shipped) and `docs/superpowers/plans/2026-08-23-vision-pre-expansion-cleanup.md` (whose Forward notes are binding inputs here — every bucket-B item it assigned to one of these four features is folded into that feature's task below).

**Worktree:** `<worktree>`, branch `vision-generation`. **This branch is not merging to main** — the work stays here.

## Global Constraints

These are project rules. Every task's requirements implicitly include all of them.

- **Every task ships unit tests AND updated docs.** Docs mean `modules/vision/README.md` at minimum; `docs/adr/0012-image-generation-engine-adapter.md` is amended only when a task changes a contract the ADR states (Tasks 4, 7, 9 do; the rest do not).
- **One test command, run in the foreground, one at a time:**
  ```
  DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest <path> -q
  ```
  `python` is this repo's venv interpreter (`<repo>/.venv/bin/python`) — activate the venv or substitute that full path; the rest of the command line stays exactly as written. Never run two pytest processes at once: parallel sessions collide on the test database.
- **No `conftest.py` anywhere.** Shared test helpers live in `modules/vision/tests/_helpers.py`; autouse fixtures are defined per test module and delegate their bodies to it.
- **Class-level `@pytest.mark.django_db`** (on the test class, not per method).
- **Mock at the `httpx` layer, never the engine class** — patch `core.inference.engines.comfyui.httpx.get` / `.post` so the adapter's real URL building, JSON parsing, and error handling run. Service-layer tests drive `StubGenerator`/`StubEngine` through the real `ENGINES` registry instead.
- **New operations are new `Operation` entries plus one graph template each.** `comfyui_workflows.template_keys()` stays the single source of `ComfyUIEngine.supported_operations` — no hand-maintained tuple, ever.
- **LoRA is parameters, not an operation.** Spec §9 lists "LoRA/embedding params and their UI" beside the operations; ADR 0012 D7 makes assets a second axis that adorns a job rather than answering a role. Task 8 adds `"asset"` params to existing operations and registers nothing new.
- **No duplicated form/view/template logic.** If a step would copy a block, it extracts and reuses instead. The schema-driven form (`modules/vision/forms.py`), the shared facts line (`vision/_job_facts.html` ← `GenerationJob.facts`), the shared delete control, and `_job_card.html` are the machinery every mode goes through.
- **ComfyUI's graph vocabulary — node class names, node ids, `/prompt` and `/upload/image` payload shapes — never leaves `core/inference/engines/comfyui.py` and `comfyui_workflows/`** (spec §3). Above that line only `Operation`, `GenerationRequest`, `JobStatus`, `InstalledModel`, `Asset` exist.
- **`core/` never imports `console/` or `modules/`** at module scope; engines stay stateless about bindings (`endpoint` is data passed per call).
- **Offline and content-agnostic.** Nothing downloads a model, checkpoint, LoRA, or upscaler; nothing inspects or filters a prompt, an asset name, or an output. The operator places files; the platform lists and uses them.
- **No baked model defaults.** `COMFYUI_BASE_URL` is a *location* and remains the only default of its kind.
- **Feature flag semantics unchanged.** `FARABUNKER_FEATURES` gates role registration, operation registration, job-kind registration, and the URL mount — nothing else. Every new operation is registered inside the same gate in `modules/vision/apps.py::VisionConfig.ready()`.
- **JSON safety is never weakened.** `GenerationJob.params`, `GenerationJob.engine_payload`, and every queue payload stay JSON-serializable: an upload object, a `Path`, or a file handle in any of them is a defect. Files travel as bytes on disk plus a reference string.
- **The suite must stay green in BOTH orders at every commit** (a total that differs between the orders is an isolation bug, not a flake):
  ```
  DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest -q
  DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules console scripts -q
  ```
- **Every commit carries these two trailers, exactly:**
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  ```

---

## Forward notes this plan absorbs

The pre-expansion cleanup's Forward notes assigned bucket-B items to whichever of these features should carry them. Each is folded into that feature's task rather than left to be rediscovered.

| Item | What it is | Task |
|---|---|---|
| B3 | `JobInput` is write-only — never queried, never served, absent from `_job_json` | Task 3 |
| B10 | `preflight()` runs before `validate_params`, so a malformed submission reports 503 instead of the 400 that is true | Task 2 |
| B4 | `fetch_outputs` sorts node ids as strings (`"10" < "2"`) — inert with one output node, wrong with two | Task 6 |
| B1 | `"asset"`-kind params raise in `forms._field_for` (D7 deferral) | Task 7 |
| B6 | One health check + one `list_choices` round trip per choice param, serially, on every GET and POST | Task 7 |
| B8 | No DB index anywhere; the gallery sorts on unindexed `-job__created_at` | Task 9 |
| Wording | The create page's `<h1>` renders `operation.label`, and `TXT2IMG.label` currently carries the page copy "Generate an image" — which reads oddly beside a sibling once a second mode registers | Task 2 |

Bucket-B items assigned to *other* future work (B5, B9, B7 → the second engine adapter; B2, B14 → the multi-file model families and the model-management track; B12, B13 → the chatbot tool) are **out of scope here** and must not be picked up opportunistically.

---

## File Structure

**Created**

| Path | Responsibility |
|---|---|
| `core/inference/engines/comfyui_workflows/_fragments.py` | The ONE ComfyUI graph vocabulary every template shares: a `Graph` id-allocating builder, a `Checkpoint` link triple, and the checkpoint / prompt / latent / sample / decode / save fragments. Four templates compose it; none of them repeats a node name. |
| `core/inference/engines/comfyui_workflows/img2img.py` | The img2img graph: load image → VAE encode → repeat batch → sample at `denoise` → decode → save. |
| `core/inference/engines/comfyui_workflows/inpaint.py` | The inpaint graph: load image + load mask → `VAEEncodeForInpaint` → sample → decode → save. |
| `core/inference/engines/comfyui_workflows/upscale.py` | The upscale graph: load image → upscale-model loader → `ImageUpscaleWithModel` → save. No checkpoint, no sampler. |
| `modules/vision/migrations/0003_upscale_seed_and_indexes.py` | `GenerationJob.seed` becomes nullable (an operation may have no seed) and the two `GenerationJob` indexes every listing surface sorts or filters on (B8). |

**Modified**

| Path | Change |
|---|---|
| `core/inference/operations.py` | `IMG2IMG`, `INPAINT`, `UPSCALE` definitions; `Operation.file_params()`; `validate_params` gains an `"asset"` branch (list-safe, opaque ids); LoRA params on the three checkpoint operations; `TXT2IMG.label` becomes "Text to image". |
| `core/inference/engines/comfyui_workflows/__init__.py` | Three new `_TEMPLATES` entries. Nothing else — `template_keys()` already drives `supported_operations`. |
| `core/inference/engines/comfyui_workflows/txt2img.py` | Rewritten as a composition of `_fragments`, emitting a byte-identical graph. |
| `core/inference/engines/comfyui.py` | `_object_info` memo (B6) + `clear_object_info_cache()`; numeric node-id ordering in `fetch_outputs` (B4); `upscale_models` named in the `SetupGuide.models_note`. |
| `modules/vision/apps.py` | Three more gated `register_operation` calls. |
| `modules/vision/models.py` | `facts` renders list values and skips a null seed; `Meta.indexes`; `seed` nullable. |
| `modules/vision/store.py` | `StoredFile` — a file already in the managed store, presented with the small slice of Django's upload API `store_input` uses. |
| `modules/vision/services.py` | Validation before preflight (B10); `files` merge into the validated params; `parse_input_reference` / `stored_input`. |
| `modules/vision/forms.py` | `"asset"` field branch; `engine_choices` becomes `engine_options`; `stored_keys` relaxes a file field a stored reference already answers. |
| `modules/vision/views.py` | `live_choices` becomes `live_options` (choices *and* assets); `_serve_stored_file` extracted and shared by `output_file`/`input_file`; `_recent_jobs()` extracted; `stored_input_refs`; `input_targets`. |
| `modules/vision/urls.py` | `inputs/<int:input_id>/file/`. |
| `modules/vision/jobs.py` | `_reject_queued_file_params` → `_payload_files`: a payload carries images by reference. |
| `modules/vision/templates/vision/_job_card.html` | Input thumbnails, rendered through the same markup outputs use. |
| `modules/vision/templates/vision/create.html` | Stored-input hidden fields and their note. |
| `modules/vision/templates/vision/gallery.html` | "Use in <mode>" links, driven by the operation registry. |
| `modules/vision/tests/_helpers.py` | `FakeComfyUI` gains a `combo_shape` switch (it already serves `/object_info/UpscaleModelLoader` through `_NODE_INPUTS`); plus a `reset_engine_caches()` body, the ONE `PNG` fixture that replaces three local copies, and the ONE `stored_output()` builder Tasks 3–5 share. |
| `docs/adr/0012-image-generation-engine-adapter.md` | Amended for the input-reference contract (Task 4), the asset widget shipping (Task 7), and the nullable seed (Task 9). |
| `modules/vision/README.md`, `docs/DEV.md`, `docs/ARCHITECTURE.md` | Doc ripple. |

---

### Task 1: One graph vocabulary — shared ComfyUI fragments

**Depends on:** nothing — this is the first task. Run it before any other; three later templates compose what it creates.

Four templates are about to exist and three of them start with the same three nodes and end with the same two. Extract that vocabulary FIRST, prove it by rebuilding `txt2img` on it with a byte-identical result, and every later template is a five-line composition.

**Files:**
- Create: `core/inference/engines/comfyui_workflows/_fragments.py`
- Modify: `core/inference/engines/comfyui_workflows/txt2img.py` (whole body)
- Test: `modules/vision/tests/test_comfyui_workflows.py` (append a class; the existing `TestTxt2ImgGraph` must pass untouched)
- Docs: `modules/vision/README.md`

**Interfaces:**
- Produces:
  - `_fragments.Graph()` with `.add(class_type: str, **inputs) -> str` (returns the allocated node id, `"1"`, `"2"`, … in call order) and `.as_dict() -> dict`.
  - `_fragments.Checkpoint(model: list, clip: list, vae: list)` — a frozen dataclass of the three output links a checkpoint loader yields.
  - `_fragments.checkpoint(graph, model_id) -> Checkpoint`
  - `_fragments.prompts(graph, ckpt, params) -> tuple[list, list]` → `(positive_link, negative_link)`
  - `_fragments.empty_latent(graph, params) -> list`
  - `_fragments.load_image(graph, reference) -> list`
  - `_fragments.repeat_batch(graph, latent, amount) -> list`
  - `_fragments.sample(graph, ckpt, positive, negative, latent, params) -> list` — reads `params["denoise"]` when the operation declares one, else 1.0
  - `_fragments.save_image(graph, images, request) -> str`
  - `_fragments.decode_and_save(graph, ckpt, samples, request) -> None`
- Consumes: `core.inference.operations.GenerationRequest` (`.params`, `.client_ref`), already shipped.

- [ ] **Step 1: Write the failing fragment tests**

Append to `modules/vision/tests/test_comfyui_workflows.py`:

```python
from core.inference.engines.comfyui_workflows import _fragments


class TestGraphBuilder:
    """Node ids are allocated in call order, so a template reads as the
    graph it builds and no template hand-numbers a node."""

    def test_ids_are_allocated_in_call_order(self):
        graph = _fragments.Graph()
        assert graph.add("CheckpointLoaderSimple", ckpt_name="a.safetensors") == "1"
        assert graph.add("VAEDecode", samples=["1", 0]) == "2"

    def test_as_dict_is_the_api_format_shape(self):
        graph = _fragments.Graph()
        graph.add("SaveImage", filename_prefix="job", images=["9", 0])
        assert graph.as_dict() == {
            "1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "job", "images": ["9", 0]}}
        }


class TestFragments:
    def test_checkpoint_yields_the_three_links_its_loader_outputs(self):
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(graph, "sdxl\\turbo.safetensors")
        assert graph.as_dict()["1"]["inputs"]["ckpt_name"] == "sdxl\\turbo.safetensors"
        assert (ckpt.model, ckpt.clip, ckpt.vae) == (["1", 0], ["1", 1], ["1", 2])

    def test_prompts_encode_positive_then_negative_against_the_checkpoint_clip(self):
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(graph, "sdxl.safetensors")
        positive, negative = _fragments.prompts(graph, ckpt, {"prompt": "a lighthouse", "negative_prompt": "blurry"})
        nodes = graph.as_dict()
        assert positive == ["2", 0] and negative == ["3", 0]
        assert nodes["2"]["inputs"] == {"text": "a lighthouse", "clip": ["1", 1]}
        assert nodes["3"]["inputs"] == {"text": "blurry", "clip": ["1", 1]}

    def test_a_missing_prompt_encodes_as_empty_text_never_none(self):
        """`None` is not a string ComfyUI can encode; an operation that
        declares no negative prompt must still produce a valid graph."""
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(graph, "sdxl.safetensors")
        _fragments.prompts(graph, ckpt, {})
        nodes = graph.as_dict()
        assert nodes["2"]["inputs"]["text"] == ""
        assert nodes["3"]["inputs"]["text"] == ""

    def test_sample_defaults_denoise_to_one_when_the_operation_has_no_such_param(self):
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(graph, "sdxl.safetensors")
        params = {"seed": 7, "steps": 20, "cfg_scale": 6.0, "sampler": "euler", "scheduler": "normal"}
        _fragments.sample(graph, ckpt, ["2", 0], ["3", 0], ["4", 0], params)
        assert graph.as_dict()["2"]["inputs"]["denoise"] == 1.0

    def test_sample_reads_denoise_from_the_params_when_declared(self):
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(graph, "sdxl.safetensors")
        params = {"seed": 7, "steps": 20, "cfg_scale": 6.0, "sampler": "euler",
                  "scheduler": "normal", "denoise": 0.4}
        _fragments.sample(graph, ckpt, ["2", 0], ["3", 0], ["4", 0], params)
        assert graph.as_dict()["2"]["inputs"]["denoise"] == 0.4

    def test_repeat_batch_and_load_image_are_single_nodes(self):
        graph = _fragments.Graph()
        image = _fragments.load_image(graph, "job-uuid/beach.png")
        latent = _fragments.repeat_batch(graph, ["5", 0], 3)
        nodes = graph.as_dict()
        assert image == ["1", 0]
        assert nodes["1"] == {"class_type": "LoadImage", "inputs": {"image": "job-uuid/beach.png"}}
        assert latent == ["2", 0]
        assert nodes["2"] == {"class_type": "RepeatLatentBatch", "inputs": {"samples": ["5", 0], "amount": 3}}
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_comfyui_workflows.py -q`
Expected: FAIL — `ImportError: cannot import name '_fragments'`.

- [ ] **Step 3: Write `_fragments.py`**

Create `core/inference/engines/comfyui_workflows/_fragments.py`:

```python
"""
The ONE ComfyUI graph vocabulary, shared by every template in this package.

A template describes a mode; the nodes it strings together are not the
mode. txt2img, img2img, and inpaint all load a checkpoint, encode two
prompts, sample, decode, and save -- written out four times that is four
places to get a link index wrong. Written here once, a template is the
five lines that are actually different.

Node ids are ALLOCATED, never hand-written: `Graph.add` returns the id it
assigned and a fragment hands back the LINK (`[node_id, output_index]`)
its caller needs, so inserting a node (a LoRA chain, a batch repeat) can
never silently renumber a link someone typed by hand.

This module is the deepest part of the engine-vocabulary boundary
(spec §3): node class names and link shapes live here and in `comfyui.py`
and nowhere above.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.inference.operations import GenerationRequest

# A link in ComfyUI's API format: `[<source node id>, <output index>]`.
Link = list


class Graph:
    """An API-format ComfyUI graph under construction.

    Ids are the string integers ComfyUI's own frontend emits ("1", "2",
    ...), allocated in call order. Nothing reads them back apart from the
    links this module builds, so their only real job is to be unique and
    stable within one submission.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, dict] = {}

    def add(self, class_type: str, **inputs) -> str:
        """Append a node and return the id it was given."""
        node_id = str(len(self._nodes) + 1)
        self._nodes[node_id] = {"class_type": class_type, "inputs": dict(inputs)}
        return node_id

    def as_dict(self) -> dict:
        """The graph as the `/prompt` body's `prompt` value.

        A shallow copy of the id map, and deliberately no deeper: every
        template's last statement is `return graph.as_dict()` and
        `ComfyUIGenerator.submit` posts that dict and stores it verbatim
        (D6) without touching it, so there is no caller to defend the node
        dicts from.
        """
        return dict(self._nodes)


@dataclass(frozen=True)
class Checkpoint:
    """The three links a checkpoint loader yields, named rather than
    indexed. `CheckpointLoaderSimple` outputs MODEL, CLIP, VAE in that
    order; every template downstream wants them by name."""

    model: Link
    clip: Link
    vae: Link


def checkpoint(graph: Graph, model_id: str) -> Checkpoint:
    """Load the bound checkpoint. `model_id` is ComfyUI's own opaque
    string (a Windows host reports `subdir\\file.safetensors`) and is
    passed through untouched."""
    node = graph.add("CheckpointLoaderSimple", ckpt_name=model_id)
    return Checkpoint(model=[node, 0], clip=[node, 1], vae=[node, 2])


def prompts(graph: Graph, ckpt: Checkpoint, params: dict) -> tuple[Link, Link]:
    """Encode the positive and negative prompts, in that order.

    A param the operation does not declare (or declares and left blank)
    encodes as the empty string: `None` is not text ComfyUI can encode,
    and an empty negative prompt is the normal case.
    """
    positive = graph.add("CLIPTextEncode", text=params.get("prompt") or "", clip=ckpt.clip)
    negative = graph.add("CLIPTextEncode", text=params.get("negative_prompt") or "", clip=ckpt.clip)
    return [positive, 0], [negative, 0]


def empty_latent(graph: Graph, params: dict) -> Link:
    """A blank latent at the requested size and batch -- what txt2img
    samples from when there is no input image."""
    node = graph.add(
        "EmptyLatentImage",
        width=params["width"],
        height=params["height"],
        batch_size=params["batch_size"],
    )
    return [node, 0]


def load_image(graph: Graph, reference: str) -> Link:
    """Load a file the engine already holds. `reference` is whatever
    `ImageGenerator.submit` got back from the transfer -- never a path on
    the platform's filesystem."""
    node = graph.add("LoadImage", image=reference)
    return [node, 0]


def encode_image(graph: Graph, ckpt: Checkpoint, image: Link) -> Link:
    """Pixels -> latent, for a mode that starts from an image."""
    node = graph.add("VAEEncode", pixels=image, vae=ckpt.vae)
    return [node, 0]


def repeat_batch(graph: Graph, latent: Link, amount: int) -> Link:
    """Repeat a latent `amount` times so an image-started mode can produce
    a batch the way `EmptyLatentImage`'s `batch_size` does for txt2img.
    `amount=1` is a no-op node, kept unconditionally so the graph shape
    does not depend on a parameter value."""
    node = graph.add("RepeatLatentBatch", samples=latent, amount=amount)
    return [node, 0]


def sample(
    graph: Graph,
    ckpt: Checkpoint,
    positive: Link,
    negative: Link,
    latent: Link,
    params: dict,
) -> Link:
    """The sampler every generating mode shares.

    `denoise` comes from the operation's schema when it declares one
    (img2img's whole point) and is 1.0 otherwise -- sampling a blank
    latent from anything less would be a lie about what was asked.
    """
    denoise = params.get("denoise")
    node = graph.add(
        "KSampler",
        seed=params["seed"],
        steps=params["steps"],
        cfg=params["cfg_scale"],
        sampler_name=params["sampler"],
        scheduler=params["scheduler"],
        denoise=1.0 if denoise is None else float(denoise),
        model=ckpt.model,
        positive=positive,
        negative=negative,
        latent_image=latent,
    )
    return [node, 0]


def save_image(graph: Graph, images: Link, request: GenerationRequest) -> str:
    """Save the produced images. The job UUID prefixes every engine-side
    file, so an output found on the engine can be traced back to its
    job."""
    return graph.add("SaveImage", filename_prefix=request.client_ref, images=images)


def decode_and_save(graph: Graph, ckpt: Checkpoint, samples: Link, request: GenerationRequest) -> None:
    """Latent -> pixels -> saved file: the tail every sampling mode
    shares."""
    decoded = graph.add("VAEDecode", samples=samples, vae=ckpt.vae)
    save_image(graph, [decoded, 0], request)
```

- [ ] **Step 4: Run the fragment tests to verify they pass**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_comfyui_workflows.py -q`
Expected: PASS (the new classes pass; `TestTxt2ImgGraph` still passes against the old template).

- [ ] **Step 5: Rebuild `txt2img.py` on the fragments**

Replace the whole body of `core/inference/engines/comfyui_workflows/txt2img.py`:

```python
"""Text-to-image graph: checkpoint -> two CLIP encodes -> KSampler -> VAE
decode -> SaveImage. Built from ComfyUI's BUILT-IN nodes only, so vanilla
ComfyUI with no custom nodes and no Manager can run it (D1), and composed
from `_fragments` so the nodes it shares with img2img and inpaint are
written once."""
from __future__ import annotations

from core.inference.engines.comfyui_workflows import _fragments
from core.inference.operations import GenerationRequest


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
    uploaded file. txt2img has no file params, so this template ignores it
    too -- img2img reads `inputs["init_image"]`.
    """
    graph = _fragments.Graph()
    ckpt = _fragments.checkpoint(graph, model_id)
    positive, negative = _fragments.prompts(graph, ckpt, request.params)
    latent = _fragments.empty_latent(graph, request.params)
    samples = _fragments.sample(graph, ckpt, positive, negative, latent, request.params)
    _fragments.decode_and_save(graph, ckpt, samples, request)
    return graph.as_dict()
```

- [ ] **Step 6: Pin that the rewrite changed nothing**

Append to `modules/vision/tests/test_comfyui_workflows.py`, inside `TestTxt2ImgGraph`:

```python
    def test_the_whole_graph_is_exactly_what_it_was_before_the_fragment_rewrite(self):
        """A golden copy of the shipped txt2img graph. The fragment
        extraction is a refactor: if this changes, it was not one."""
        assert _graph() == {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "sdxl\\turbo.safetensors"}},
            "2": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "a lighthouse at dusk", "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "blurry", "clip": ["1", 1]}},
            "4": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": 832, "height": 1216, "batch_size": 2}},
            "5": {"class_type": "KSampler",
                  "inputs": {"seed": 123456, "steps": 30, "cfg": 6.5,
                             "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0,
                             "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                             "latent_image": ["4", 0]}},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage",
                  "inputs": {"filename_prefix": "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e",
                             "images": ["6", 0]}},
        }
```

- [ ] **Step 7: Run the whole workflows and generator suites**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_comfyui_workflows.py -q`
Expected: PASS, including the golden test and every pre-existing `TestTxt2ImgGraph` assertion.

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_comfyui_generator.py -q`
Expected: PASS — `submit` builds the same payload it did before.

- [ ] **Step 8: Document the vocabulary**

In `modules/vision/README.md`, in the "how to add an operation" list, extend step 2 with:

```markdown
   A template is a composition, not a graph written out by hand:
   `core/inference/engines/comfyui_workflows/_fragments.py` owns the nodes every
   mode shares (checkpoint, the two prompt encodes, the sampler, decode-and-save)
   and allocates node ids as it goes, so a template is the handful of lines that
   are actually specific to its mode and no template hand-numbers a node.
```

- [ ] **Step 9: Commit**

```bash
git add core/inference/engines/comfyui_workflows/_fragments.py \
        core/inference/engines/comfyui_workflows/txt2img.py \
        modules/vision/tests/test_comfyui_workflows.py \
        modules/vision/README.md
git commit -m "refactor(comfyui): one shared graph vocabulary for every template

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 2: img2img — the first file-taking operation

**Depends on:** Task 1 (`_fragments`).

**Files:**
- Modify: `core/inference/operations.py` (`TXT2IMG.label`; new `IMG2IMG` below it)
- Create: `core/inference/engines/comfyui_workflows/img2img.py`
- Modify: `core/inference/engines/comfyui_workflows/__init__.py` (`_TEMPLATES` entry + import)
- Modify: `modules/vision/apps.py` (one gated `register_operation`)
- Modify: `modules/vision/services.py:145-160` (`submit_job`: validate before preflight — B10)
- Test: `modules/vision/tests/test_operations.py`, `modules/vision/tests/test_comfyui_workflows.py`, `modules/vision/tests/test_services.py`, `modules/vision/tests/test_apps.py`, `modules/vision/tests/test_views_create.py`
- Docs: `modules/vision/README.md`

**Interfaces:**
- Produces: `core.inference.operations.IMG2IMG` (key `"img2img"`, params `prompt`, `negative_prompt`, `init_image` (file, required), `denoise`, `steps`, `cfg_scale`, `seed`, `sampler`, `scheduler`, `batch_size`); `comfyui_workflows.img2img.build(request, model_id, config, inputs) -> dict`; `TXT2IMG.label == "Text to image"`.
- Consumes: every `_fragments` function from Task 1; `Operation.file_param_keys()`, `services.submit_job(operation_key, raw_params, files=None)`, and `ComfyUIGenerator._upload_inputs` — all already shipped.

- [ ] **Step 1: Write the failing schema test**

Append to `modules/vision/tests/test_operations.py`:

```python
from core.inference.operations import IMG2IMG


class TestImg2ImgSchema:
    def test_it_declares_its_init_image_as_a_required_image_file(self):
        param = IMG2IMG.param("init_image")
        assert (param.kind, param.required, param.accept) == ("file", True, "image/*")
        assert IMG2IMG.file_param_keys() == frozenset({"init_image"})

    def test_denoise_is_the_parameter_that_makes_this_mode_itself(self):
        param = IMG2IMG.param("denoise")
        assert (param.kind, param.default, param.min, param.max) == ("float", 0.6, 0.0, 1.0)

    def test_it_declares_no_size_because_the_init_image_carries_it(self):
        assert IMG2IMG.param("width") is None
        assert IMG2IMG.param("height") is None

    def test_a_submission_without_an_init_image_is_a_param_error(self):
        with pytest.raises(ParamError) as exc:
            validate_params(IMG2IMG, {"prompt": "a lighthouse", "sampler": "euler", "scheduler": "normal"})
        assert "init_image" in exc.value.errors

    def test_txt2img_is_named_for_its_mode_now_that_it_has_a_sibling(self):
        """The create page renders `operation.label` as its heading and its
        chooser entry; "Generate an image" beside "Image to image" named a
        page, not a mode."""
        assert TXT2IMG.label == "Text to image"
```

(`TXT2IMG`, `ParamError`, `validate_params`, and `pytest` are already imported at the top of that module; add `IMG2IMG` to the existing `from core.inference.operations import ...` line rather than writing a second import.)

- [ ] **Step 2: Run it to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_operations.py -q`
Expected: FAIL — `ImportError: cannot import name 'IMG2IMG'`.

- [ ] **Step 3: Define the operation**

In `core/inference/operations.py`, change `TXT2IMG.label` and append `IMG2IMG` beneath it:

```python
TXT2IMG = Operation(
    key="txt2img",
    label="Text to image",
    ...unchanged...
)

IMG2IMG = Operation(
    key="img2img",
    label="Image to image",
    capability="image-generation",
    output_media="image/png",
    params=(
        Param("prompt", "text", "Prompt", default="", required=True),
        Param("negative_prompt", "text", "Negative prompt", default=""),
        # The image the generation starts from. Its own dimensions decide
        # the output size, which is why this operation declares no
        # width/height: inventing a size here would silently rescale the
        # operator's picture.
        Param("init_image", "file", "Init image", accept="image/*", required=True),
        # How much of the init image to throw away. The one parameter that
        # makes this mode itself: 0 returns the input, 1 ignores it.
        Param("denoise", "float", "Denoise", default=0.6, min=0.0, max=1.0, step=0.05),
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

- [ ] **Step 4: Run the schema test to verify it passes**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_operations.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing template test**

Append to `modules/vision/tests/test_comfyui_workflows.py`:

```python
IMG2IMG_PARAMS = {
    "prompt": "a lighthouse at dusk",
    "negative_prompt": "blurry",
    "denoise": 0.35,
    "steps": 30,
    "cfg_scale": 6.5,
    "seed": 123456,
    "sampler": "dpmpp_2m",
    "scheduler": "karras",
    "batch_size": 2,
}


def _img2img_graph(inputs=None):
    request = GenerationRequest(
        operation="img2img",
        model_id="sdxl.safetensors",
        params=dict(IMG2IMG_PARAMS),
        client_ref="8f14e45f-ceea-467a-9575-1b0e5a1d1f1e",
    )
    references = {"init_image": "8f14e45f/init_image-beach.png"} if inputs is None else inputs
    return get_template("img2img")(request, request.model_id, {}, references)


class TestImg2ImgGraph:
    def test_the_init_image_is_loaded_by_the_engine_side_reference(self):
        """The template never sees a filesystem path: `submit` transferred
        the file first and handed back the engine's own name for it."""
        graph = _img2img_graph()
        assert graph["4"] == {
            "class_type": "LoadImage",
            "inputs": {"image": "8f14e45f/init_image-beach.png"},
        }

    def test_the_loaded_image_is_encoded_with_the_checkpoint_vae(self):
        assert _img2img_graph()["5"] == {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["4", 0], "vae": ["1", 2]},
        }

    def test_batch_size_repeats_the_encoded_latent(self):
        assert _img2img_graph()["6"] == {
            "class_type": "RepeatLatentBatch",
            "inputs": {"samples": ["5", 0], "amount": 2},
        }

    def test_the_sampler_denoises_from_that_latent_at_the_requested_strength(self):
        inputs = _img2img_graph()["7"]["inputs"]
        assert inputs["denoise"] == 0.35
        assert inputs["latent_image"] == ["6", 0]
        assert inputs["positive"] == ["2", 0]
        assert inputs["negative"] == ["3", 0]

    def test_it_decodes_and_saves_under_the_job_prefix(self):
        graph = _img2img_graph()
        assert graph["8"] == {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}}
        assert graph["9"]["class_type"] == "SaveImage"
        assert graph["9"]["inputs"]["filename_prefix"] == "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"

    def test_a_missing_input_reference_is_a_clear_error_not_a_broken_graph(self):
        """`submit` transfers every declared file input before a template
        runs, so an absent reference is a bug in the layer above -- and it
        must say so rather than posting a graph with `image: None`."""
        with pytest.raises(KeyError, match="init_image"):
            _img2img_graph(inputs={})
```

Also extend the existing `TestGetTemplate` in that file:

```python
    def test_img2img_is_registered(self):
        assert get_template("img2img") is not None
```

- [ ] **Step 6: Run it to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_comfyui_workflows.py -q`
Expected: FAIL — `ValueError: No ComfyUI template for operation 'img2img'`.

- [ ] **Step 7: Write the template and register it**

Create `core/inference/engines/comfyui_workflows/img2img.py`:

```python
"""Image-to-image graph: checkpoint -> two CLIP encodes, load the init
image -> VAE encode -> repeat for the batch -> KSampler at the requested
denoise -> VAE decode -> SaveImage. Built-in nodes only (D1)."""
from __future__ import annotations

from core.inference.engines.comfyui_workflows import _fragments
from core.inference.operations import GenerationRequest


def build(request: GenerationRequest, model_id: str, config: dict, inputs: dict) -> dict:
    """The API-format graph for one img2img request.

    `inputs["init_image"]` is the reference ComfyUI gave the transferred
    file (`ImageGenerator.submit` owns that transfer) -- never a path on
    the platform's filesystem. It is indexed, not `.get()`, because
    `init_image` is a REQUIRED file param: an absent reference means the
    layer above skipped the transfer, and a `KeyError` naming the param is
    a far better report than a graph ComfyUI rejects for `image: None`.

    The output size is the init image's own; this operation declares no
    width/height, so nothing here rescales the operator's picture.

    `config` (D8) is ignored, as in txt2img: a single-file SD checkpoint
    needs none of it.
    """
    params = request.params
    graph = _fragments.Graph()
    ckpt = _fragments.checkpoint(graph, model_id)
    positive, negative = _fragments.prompts(graph, ckpt, params)
    image = _fragments.load_image(graph, inputs["init_image"])
    latent = _fragments.encode_image(graph, ckpt, image)
    latent = _fragments.repeat_batch(graph, latent, params["batch_size"])
    samples = _fragments.sample(graph, ckpt, positive, negative, latent, params)
    _fragments.decode_and_save(graph, ckpt, samples, request)
    return graph.as_dict()
```

In `core/inference/engines/comfyui_workflows/__init__.py`, extend the import and the registry:

```python
from core.inference.engines.comfyui_workflows import img2img, txt2img
...
_TEMPLATES: dict[str, Template] = {
    "txt2img": txt2img.build,
    "img2img": img2img.build,
}
```

- [ ] **Step 8: Run the template tests to verify they pass**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_comfyui_workflows.py -q`
Expected: PASS.

- [ ] **Step 9: Register the operation and pin that the adapter needed no edit**

In `modules/vision/apps.py::ready()`, change the import and the registration:

```python
        from core.inference.operations import IMG2IMG, TXT2IMG, register_operation
        ...
        register_operation(TXT2IMG)
        register_operation(IMG2IMG)
```

Append to `modules/vision/tests/test_apps.py`:

```python
    def test_the_enabled_feature_registers_every_mode_it_serves(self):
        keys = [operation.key for operation in operations.all_operations()]
        assert "txt2img" in keys
        assert "img2img" in keys
```

Append to `modules/vision/tests/test_comfyui_engine.py`:

```python
    def test_supported_operations_gained_img2img_with_no_adapter_edit(self):
        """`supported_operations` reads the template registry, so shipping
        a template is the whole of teaching the adapter a mode."""
        assert set(ComfyUIEngine().supported_operations("sdxl.safetensors", ENDPOINT)) >= {
            "txt2img",
            "img2img",
        }
```

- [ ] **Step 10: Run both**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_apps.py modules/vision/tests/test_comfyui_engine.py -q`
Expected: PASS.

- [ ] **Step 11: Write the failing B10 test — an invalid submission is a 400, not a 503**

Append to `modules/vision/tests/test_services.py`, inside `TestSubmitJob`:

```python
    def test_bad_params_report_the_param_error_even_when_nothing_is_bound(self):
        """B10: a malformed submission is malformed whether or not an
        engine is answering. Reporting 503 for it tells the operator to go
        fix their engine over a typo -- and makes every rejected
        submission pay a health round trip first."""
        with pytest.raises(ParamError):
            services.submit_job("txt2img", {**RAW, "steps": "not a number"})

    def test_a_valid_submission_still_reports_an_unbound_role(self):
        with pytest.raises(services.VisionUnavailable) as exc:
            services.submit_job("txt2img", dict(RAW))
        assert exc.value.state == "unbound"

    def test_bad_params_never_reach_the_engine(self):
        _bind()
        engine = StubEngine()
        with _registered(engine):
            with pytest.raises(ParamError):
                services.submit_job("txt2img", {**RAW, "width": "wide"})
        assert engine.built == []
        assert GenerationJob.objects.count() == 0
```

- [ ] **Step 12: Run it to verify the first assertion fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_services.py -q`
Expected: FAIL — `VisionUnavailable` is raised where a `ParamError` was expected.

- [ ] **Step 13: Swap the order in `submit_job`**

In `modules/vision/services.py::submit_job`, move validation ahead of preflight and update the docstring paragraph that describes the order:

```python
    operation = get_operation(operation_key)
    if operation is None:
        raise ValueError(f"Unknown operation {operation_key!r}")

    # Validation first: a submission that does not fit the schema is
    # malformed whether or not an engine is answering, so it must report
    # the 400 that is true rather than a 503 about the engine -- and a
    # rejected submission must not pay a health round trip to find out.
    params = validate_params(operation, raw_params)

    check = preflight()
    if not check.ready:
        raise VisionUnavailable(check.state, check.message)

    resolved = check.resolved
```

and in the same docstring replace the "Order matters" paragraph's first sentence with:

```python
    Order matters and is deliberate: schema validation FIRST (an invalid
    submission is a 400 about the parameters, never a 503 about the
    engine, and never costs a health check), then preflight before any row
    is written (no orphan jobs for an unbound role), then the row, then the
    engine call -- so the job's UUID already exists to be used as the
    engine-side filename prefix.
```

- [ ] **Step 14: Run the service tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_services.py -q`
Expected: PASS. If a pre-existing test asserted `VisionUnavailable` for a submission that was *also* malformed, fix the test's params rather than the order — the order is the point of the change.

- [ ] **Step 15: Prove the page picked up the second mode with no template edit**

Append to `modules/vision/tests/test_views_create.py`:

```python
@pytest.mark.django_db
class TestSecondModeArrivesByRegistrationAlone:
    def test_the_chooser_lists_both_modes(self, client):
        body = client.get(reverse("vision-create")).content.decode()
        assert f'href="{reverse("vision-create-operation", args=["img2img"])}"' in body
        assert "Image to image" in body

    def test_the_img2img_page_renders_its_own_file_field(self, client):
        _bind()
        with _engine(choices={"sampler": ("euler",), "scheduler": ("normal",)}):
            body = client.get(reverse("vision-create-operation", args=["img2img"])).content.decode()
        assert '<h1>Image to image</h1>' in body
        assert 'type="file"' in body
        assert 'name="init_image"' in body
        assert 'accept="image/*"' in body
        assert 'name="denoise"' in body

    def test_the_txt2img_page_headline_names_its_mode(self, client):
        body = client.get(reverse("vision-create")).content.decode()
        assert "<h1>Text to image</h1>" in body
```

Then run the whole vision view suite and fix any pre-existing assertion that still expects the old `"Generate an image"` heading:

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_views_create.py -q`
Expected: PASS. Search first with `grep -rn "Generate an image" modules docs` — the `vision.generate` JOB KIND's label (`modules/vision/apps.py`, `register_job_kind(... label="Generate an image" ...)`) is deliberately left alone: it names the queued action, not the mode.

- [ ] **Step 16: Prove an img2img job runs end to end at the httpx layer**

Append to `modules/vision/tests/test_services.py`:

```python
@pytest.mark.django_db
class TestImg2ImgEndToEnd:
    """The real ComfyUI adapter, mocked only at `httpx`: an upload, a
    graph carrying the engine's own reference for it, and a stored
    output.

    Note the engine-side name: `store.store_input` writes
    `<param_key>-<basename>`, so the file uploaded (and therefore the
    reference the graph carries) is `init_image-beach.png`. That prefix is
    what keeps an inpaint job's image and mask from colliding when an
    operator picks two files with the same name."""

    @pytest.fixture(autouse=True)
    def _bound(self, db):
        ModelConnection.objects.create(
            name="comfy sdxl", engine="comfyui", endpoint="http://comfy.local:8188",
            model_id="sdxl.safetensors", capabilities=["image-generation"],
        ).rolebinding_set.create(role_key=VISION_GENERATE_ROLE)

    def test_the_uploaded_file_reaches_the_engine_and_the_graph(self, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path
        fake = FakeComfyUI(prompt_id="p-9")
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with patch.dict(operations._OPERATIONS, {"img2img": operations.IMG2IMG}), \
             patch("core.inference.engines.comfyui.httpx.get", fake.get), \
             patch("core.inference.engines.comfyui.httpx.post", fake.post):
            job = services.submit_job(
                "img2img",
                {"prompt": "a lighthouse", "negative_prompt": "", "denoise": "0.4",
                 "steps": "20", "cfg_scale": "7", "seed": "42", "sampler": "euler",
                 "scheduler": "normal", "batch_size": "1", "init_image": upload},
                files={"init_image": upload},
            )

        assert job.status == GenerationJob.Status.QUEUED
        assert job.params["init_image"] == "beach.png"
        assert JobInput.objects.get(job=job).param_key == "init_image"
        assert fake.uploads[0]["data"]["subfolder"] == str(job.id)
        graph = job.engine_payload["prompt"]
        load_image = next(node for node in graph.values() if node["class_type"] == "LoadImage")
        assert load_image["inputs"]["image"] == f"{job.id}/init_image-beach.png"
```

(`FakeComfyUI` is already exported by `_helpers.py`; add it to that module's existing import line. `ModelConnection`/`RoleBinding` are imported there too — use `RoleBinding.objects.create(role_key=..., connection=...)` if the reverse accessor name differs; check `console/inference/models.py` rather than guessing.)

- [ ] **Step 17: Run it**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_services.py -q`
Expected: PASS.

- [ ] **Step 18: Update the module README**

In `modules/vision/README.md`, replace the sentence "`TXT2IMG` is the one operation shipped today." with:

```markdown
`TXT2IMG` ("Text to image") and `IMG2IMG` ("Image to image") ship today; the mode
chooser on `/vision/` appears by itself because a second operation is registered, and
`/vision/op/img2img/` serves its form — neither needed a template edit.
```

And in the "Deferred" section, delete `img2img` from the "Operations beyond `txt2img`" bullet, leaving inpaint, ControlNet, and upscale.

- [ ] **Step 19: Run both suite orders and commit**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest -q`
Expected: PASS.

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules console scripts -q`
Expected: PASS with the same counts.

```bash
git add core/inference/operations.py core/inference/engines/comfyui_workflows/ \
        modules/vision/apps.py modules/vision/services.py modules/vision/tests/ \
        modules/vision/README.md
git commit -m "feat(vision): img2img, and a malformed submission reports 400 not 503

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 3: A job's inputs are readable — served, shown, reported (B3)

**Depends on:** Task 2 (img2img is what writes `JobInput` rows in the first place).

img2img writes `JobInput` rows and nobody can see them. The card shows what a job produced but not what it was given, `_job_json` omits them, and there is no URL. This task closes the read path — and does it by SHARING the file-serving and recent-jobs code the output path already has, not by copying it.

**Files:**
- Modify: `modules/vision/views.py` (extract `_serve_stored_file`, extract `_recent_jobs`, add `input_file`, extend `_job_json`)
- Modify: `modules/vision/urls.py`
- Modify: `modules/vision/templates/vision/_job_card.html`
- Modify: `modules/vision/templates/vision/base.html` (one rule for the inputs strip)
- Test: `modules/vision/tests/test_views_gallery.py` (it already owns the `output_file` tests), `modules/vision/tests/test_views_create.py`
- Docs: `modules/vision/README.md`

**Interfaces:**
- Produces: URL name `vision-input-file` at `inputs/<int:input_id>/file/`; `views.input_file(request, input_id)`; `views._serve_stored_file(request, path, media_type, missing) -> FileResponse`; `views._recent_jobs() -> list[GenerationJob]`; `_job_json(job)["inputs"] = [{"id", "param_key", "url", "media_type"}]`.
- Consumes: `modules.vision.models.JobInput` (`param_key`, `path`, `media_type`), already shipped.

- [ ] **Step 1: Put the shared test fixtures in `_helpers.py`, then write the failing tests**

Tasks 3, 4, and 5 all need "a finished job with one output whose file is really on disk", and three modules currently each carry their own PNG constant with different dimensions (`test_jobs.py:38`, `test_services.py:32` — 1024×1024 — and `test_views_generate.py:21` — 512×512). Define both ONCE first, so nine call sites across three tasks share one name.

Append to `modules/vision/tests/_helpers.py`:

```python
# The smallest byte string `store.png_dimensions` can measure: the PNG
# signature plus an IHDR chunk whose width/height sit at bytes 16-24
# (`modules/vision/store.py`). 512x512, matching what the view tests
# already assumed. ONE definition -- `test_jobs.py`, `test_services.py`,
# and `test_views_generate.py` each carried their own, with two different
# sizes between them.
PNG = (
    b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR"
    + b"\x00\x00\x02\x00" + b"\x00\x00\x02\x00" + b"\x08\x06\x00\x00\x00"
)


def stored_output(tmp_path, job=None):
    """A finished `GenerationJob` plus one `GeneratedOutput` whose `path`
    is a real file on disk.

    The shared fixture for every test that needs an image the platform
    ALREADY HOLDS: the input-serving tests (Task 3), the stored-reference
    resolver (Task 4), and the gallery's "use in ..." links (Task 5). The
    existing `_job_with_output` (`test_views_gallery.py`) writes no file,
    so it cannot serve any of them.
    """
    from modules.vision.models import GeneratedOutput, GenerationJob

    if job is None:
        job = GenerationJob.objects.create(
            operation="txt2img",
            params={
                "prompt": "a lighthouse", "negative_prompt": "", "width": 512, "height": 512,
                "steps": 20, "cfg_scale": 7.0, "seed": 42, "sampler": "euler",
                "scheduler": "normal", "batch_size": 1,
            },
            seed=42, engine="stubengine", model_id="stub.safetensors",
            endpoint="http://stub:9999",
            model_fingerprint="stubengine:stub.safetensors:None",
            status=GenerationJob.Status.DONE,
        )
    source = tmp_path / "0-job_00001_.png"
    source.write_bytes(PNG)
    return GeneratedOutput.objects.create(
        job=job, index=0, path=str(source), media_type="image/png", width=512, height=512
    )
```

Then delete the local `PNG = ...` lines in `modules/vision/tests/test_jobs.py`, `test_services.py`, and `test_views_generate.py`, and import `PNG` from `_helpers` in each instead. Run those three modules once after the swap — `test_services.py`'s constant was 1024×1024, so any assertion reading `output.width` there must be updated to 512.

Append to `modules/vision/tests/test_views_gallery.py`:

```python
@pytest.mark.django_db
class TestInputFile:
    """A stored input is served exactly the way a stored output is --
    strictly by primary key, so the request never supplies a path."""

    def _job_with_input(self, tmp_path):
        output = stored_output(tmp_path)
        source = tmp_path / "init_image-beach.png"
        source.write_bytes(PNG)
        return JobInput.objects.create(
            job=output.job, param_key="init_image", path=str(source), media_type="image/png"
        )

    def test_it_streams_the_stored_file(self, client, tmp_path):
        job_input = self._job_with_input(tmp_path)
        response = client.get(reverse("vision-input-file", args=[job_input.id]))
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        assert b"".join(response.streaming_content) == PNG

    def test_download_forces_an_attachment(self, client, tmp_path):
        job_input = self._job_with_input(tmp_path)
        response = client.get(reverse("vision-input-file", args=[job_input.id]) + "?download=1")
        assert "attachment" in response["Content-Disposition"]

    def test_a_file_no_longer_on_disk_is_a_404_that_says_why(self, client, tmp_path):
        job_input = self._job_with_input(tmp_path)
        Path(job_input.path).unlink()
        response = client.get(reverse("vision-input-file", args=[job_input.id]))
        assert response.status_code == 404

    def test_an_unknown_id_is_a_404(self, client):
        assert client.get(reverse("vision-input-file", args=[9999])).status_code == 404
```

Append to `modules/vision/tests/test_views_create.py`:

```python
@pytest.mark.django_db
class TestJobInputsAreVisible:
    def _job_with_input(self):
        job = _done_job()
        return JobInput.objects.create(
            job=job, param_key="init_image", path="/tmp/init_image-beach.png",
            media_type="image/png",
        )

    def test_the_card_links_the_input_it_ran_on(self, client):
        job_input = self._job_with_input()
        body = client.get(reverse("vision-create")).content.decode()
        assert reverse("vision-input-file", args=[job_input.id]) in body

    def test_the_json_form_reports_inputs_by_url_never_by_path(self, client):
        job_input = self._job_with_input()
        payload = client.get(
            reverse("vision-job-status", args=[job_input.job_id]) + "?format=json"
        ).json()
        assert payload["inputs"] == [
            {
                "id": job_input.id,
                "param_key": "init_image",
                "url": reverse("vision-input-file", args=[job_input.id]),
                "media_type": "image/png",
            }
        ]
        assert "/tmp/" not in json.dumps(payload)
```

(add `JobInput` to each module's model import, `json` and `Path` to the gallery module's imports, and import `PNG` and `stored_output` from `modules.vision.tests._helpers` in both modules — they are defined once, at the top of this step, and every later reference in Tasks 3, 4, and 5 uses those two names.)

- [ ] **Step 2: Run them to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_views_gallery.py modules/vision/tests/test_views_create.py -q`
Expected: FAIL — `NoReverseMatch: 'vision-input-file' is not a valid view function or pattern name`.

- [ ] **Step 3: Extract the shared file server and add the input view**

In `modules/vision/views.py`, replace `output_file`'s body with a call to a shared helper and add `input_file` beside it:

```python
def _serve_stored_file(request, path: str, media_type: str, missing: str) -> FileResponse:
    """Stream one file out of the managed store.

    Shared by `output_file` and `input_file`: both serve strictly by
    primary key (the request never supplies a filesystem path, so there is
    no path-traversal surface), both honour `?download=1`, and both must
    give the same honest 404 when a job's directory has been deleted out
    from under the row. Written once so the two can never drift.

    Unauthenticated in this Phase-1 skeleton, the same known gap
    `/inference/` and `/rag/`'s file view carry (spec §9).
    """
    if not path or not os.path.isfile(path):
        raise Http404(missing)
    download = request.GET.get("download", "").lower() in ("1", "true")
    return FileResponse(
        open(path, "rb"),
        as_attachment=download,
        filename=os.path.basename(path),
        content_type=media_type or None,
    )


def output_file(request, output_id: int):
    """GET /vision/outputs/<id>/file/ -- stream one generated file."""
    output = get_object_or_404(GeneratedOutput, pk=output_id)
    return _serve_stored_file(
        request,
        output.path,
        output.media_type,
        f"The file for output {output_id} is no longer on disk "
        "(the job's directory may have been deleted).",
    )


def input_file(request, input_id: int):
    """GET /vision/inputs/<id>/file/ -- stream one stored input.

    The counterpart to `output_file`: an operator looking at an img2img
    job needs to see the picture it started from, and a caller reading
    `?format=json` needs a URL for it. The bytes are the platform's own
    copy (spec §5), not the engine's.
    """
    job_input = get_object_or_404(JobInput, pk=input_id)
    return _serve_stored_file(
        request,
        job_input.path,
        job_input.media_type,
        f"The file for input {input_id} is no longer on disk "
        "(the job's directory may have been deleted).",
    )
```

Add `JobInput` to the models import at the top of the module.

In `modules/vision/urls.py`, add the route and the import:

```python
from modules.vision.views import (
    CreatePageView, gallery, generate, input_file, job_delete, job_status, output_file,
)

urlpatterns = [
    ...
    path("outputs/<int:output_id>/file/", output_file, name="vision-output-file"),
    path("inputs/<int:input_id>/file/", input_file, name="vision-input-file"),
]
```

- [ ] **Step 4: Report inputs in the JSON form**

In `modules/vision/views.py::_job_json`, add the block beside `"outputs"`:

```python
        "inputs": [
            {
                "id": job_input.id,
                "param_key": job_input.param_key,
                "url": reverse("vision-input-file", args=[job_input.id]),
                "media_type": job_input.media_type,
            }
            for job_input in job.inputs.all()
        ],
```

- [ ] **Step 5: Extract the recent-jobs query and prefetch inputs**

`CreatePageView.get_context_data` and `_create_page_response` each build the same queryset; both now need one more prefetch, which is exactly when a second copy starts to drift. In `modules/vision/views.py`:

```python
def _recent_jobs() -> list[GenerationJob]:
    """The cards the create page shows, in one place.

    Both the GET path and the re-render-around-an-invalid-form path need
    the same list with the same prefetches; a job's inputs are rendered on
    its card, so they are prefetched here rather than fetched per card.
    """
    return list(
        GenerationJob.objects.prefetch_related("outputs", "inputs")[:RECENT_JOBS]
    )
```

and replace both `list(GenerationJob.objects.prefetch_related("outputs")[:RECENT_JOBS])` occurrences with `_recent_jobs()`.

- [ ] **Step 6: Show the inputs on the card**

In `modules/vision/templates/vision/_job_card.html`, insert above the existing `{% if job.outputs.all %}` block:

```html
  {% comment %}
  What the job was GIVEN, above what it produced. Rendered with the same
  `.job-images` strip the outputs use -- an input is a picture on this card
  for exactly the same reason an output is, so it gets no markup of its own.
  Empty for a mode with no file params, which is why there is no heading.
  {% endcomment %}
  {% if job.inputs.all %}
  <div class="job-images job-inputs">
    {% for job_input in job.inputs.all %}
    <a href="{% url 'vision-input-file' job_input.id %}" target="_blank" rel="noopener"
       title="{{ job_input.param_key }}">
      <img src="{% url 'vision-input-file' job_input.id %}" alt="Input {{ job_input.param_key }}">
    </a>
    {% endfor %}
  </div>
  {% endif %}
```

In `modules/vision/templates/vision/base.html`, beside the existing `.job-images` rules, add the one rule that distinguishes an input from a result:

```css
  .job-inputs img { opacity: 0.85; max-height: 96px; width: auto; }
```

- [ ] **Step 7: Run the view tests to verify they pass**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_views_gallery.py modules/vision/tests/test_views_create.py -q`
Expected: PASS.

- [ ] **Step 8: Update the README**

In `modules/vision/README.md`, under "Storage layout", after the directory diagram, add:

```markdown
Both halves of that directory are served the same way and only by primary key:
`GET /vision/outputs/<id>/file/` for a result, `GET /vision/inputs/<id>/file/` for the
image a job was given (`?download=1` on either forces an attachment). The job card
renders a job's inputs above its outputs, and `?format=json` reports them as URLs —
a filesystem path never leaves this module.
```

- [ ] **Step 9: Run both suite orders and commit**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest -q`
Expected: PASS.

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules console scripts -q`
Expected: PASS with the same counts.

```bash
git add modules/vision/views.py modules/vision/urls.py modules/vision/templates/vision/ \
        modules/vision/tests/ modules/vision/README.md
git commit -m "feat(vision): a job's stored inputs are served, shown, and reported

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 4: A stored file has a reference — queued jobs carry images by id

**Depends on:** Task 3 (the shared `PNG` / `stored_output` test fixtures it defines).

`run_generate` rejects any payload naming a file param, because a queue payload is JSON and an upload object is not. The fix is not a second submit path: it is a JSON-safe REFERENCE to a file the platform already holds (`output:<id>` for a gallery result, `input:<id>` for another job's input), resolved into the same file-like object `submit_job(files=...)` already takes. The JSON-safety rule is not weakened anywhere — the payload carries a string.

**Files:**
- Modify: `modules/vision/store.py` (`StoredFile`)
- Modify: `modules/vision/services.py` (`parse_input_reference`, `stored_input`, `submit_job`'s files/params merge)
- Modify: `modules/vision/jobs.py` (`_reject_queued_file_params` → `_payload_files`; `run_generate`; module docstring)
- Test: `modules/vision/tests/test_store.py`, `modules/vision/tests/test_services.py`, `modules/vision/tests/test_jobs.py`
- Docs: `modules/vision/README.md`, `docs/adr/0012-image-generation-engine-adapter.md`

**Interfaces:**
- Produces:
  - `store.StoredFile(path, name=None, content_type="")` with `.name: str`, `.content_type: str`, `.chunks(chunk_size=...) -> Iterator[bytes]` — the slice of Django's uploaded-file API `store.store_input` and `operations._file_reference` actually use.
  - `services.parse_input_reference(reference: str) -> tuple[str, int]` → `("output" | "input", pk)`; raises `ValueError` with operator-facing text.
  - `services.stored_input(reference: str) -> store.StoredFile`; raises `ValueError` for an unknown or malformed reference.
  - `services.stored_input_exists(reference: str) -> bool` — the display-only question, answered without building anything.
  - `services.submit_job(operation_key, raw_params, files=None)` — unchanged signature; now merges declared `files` into the params it validates, so no caller has to put the same object in twice.
  - `jobs._payload_files(operation_key: str, payload: dict) -> dict[str, store.StoredFile]`.
- Consumes: `Operation.file_param_keys()`, `store.store_input`, `GeneratedOutput`/`JobInput` (`path`, `media_type`) — all shipped.

- [ ] **Step 1: Write the failing `StoredFile` test**

Append to `modules/vision/tests/test_store.py`:

```python
class TestStoredFile:
    """A file already in the managed store, presented as the small slice
    of Django's upload API `store_input` uses -- so a caller that has one
    reaches `submit_job(files=...)` with no second code path."""

    def test_it_reads_back_in_chunks_and_keeps_its_name(self, tmp_path):
        source = tmp_path / "0-job_00001_.png"
        source.write_bytes(b"abcdef")
        stored = store.StoredFile(source, name="beach.png", content_type="image/png")
        assert stored.name == "beach.png"
        assert stored.content_type == "image/png"
        assert b"".join(stored.chunks(chunk_size=4)) == b"abcdef"

    def test_the_name_defaults_to_the_files_own_basename(self, tmp_path):
        source = tmp_path / "0-job_00001_.png"
        source.write_bytes(b"x")
        assert store.StoredFile(source).name == "0-job_00001_.png"

    def test_store_input_accepts_one_exactly_like_an_upload(self, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path / "generated"
        source = tmp_path / "beach.png"
        source.write_bytes(b"abcdef")
        path = store.store_input("job-1", "init_image", store.StoredFile(source))
        assert Path(path).read_bytes() == b"abcdef"
        assert Path(path).name == "init_image-beach.png"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_store.py -q`
Expected: FAIL — `AttributeError: module 'modules.vision.store' has no attribute 'StoredFile'`.

- [ ] **Step 3: Add `StoredFile`**

Append to `modules/vision/store.py`:

```python
# How much of a stored file to read at a time when handing it back as an
# upload-shaped object. Matches Django's own `UploadedFile.DEFAULT_CHUNK_SIZE`
# so a re-used input costs the same memory as a fresh upload.
_CHUNK_SIZE = 64 * 2 ** 10


class StoredFile:
    """A file ALREADY in the managed store, presented as the small slice of
    Django's uploaded-file API the rest of this module uses: `.name`,
    `.content_type`, `.chunks()`.

    This is what lets an image the platform already holds -- a gallery
    output, another job's input -- reach `services.submit_job(files=...)`
    through exactly the path a browser upload takes, instead of a second
    "submit from a stored file" code path with its own storing, its own
    `JobInput` write, and its own bugs.

    Each job still gets its OWN copy under its own directory: deleting a
    job deletes its directory, and a job whose input lived in another
    job's folder would lose it.
    """

    def __init__(self, path, name: str | None = None, content_type: str = "") -> None:
        self.path = Path(path)
        self.name = name or self.path.name
        self.content_type = content_type

    def chunks(self, chunk_size: int = _CHUNK_SIZE):
        """Yield the file's bytes, matching `UploadedFile.chunks()`."""
        with open(self.path, "rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    return
                yield chunk
```

- [ ] **Step 4: Run it to verify it passes**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_store.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing reference-resolution tests**

Append to `modules/vision/tests/test_services.py`:

```python
@pytest.mark.django_db
class TestStoredInputReferences:
    """A file the platform already holds, named by a JSON-safe string so a
    queue payload -- and a link on the gallery -- can carry an image
    without carrying bytes."""

    def test_it_parses_both_kinds(self):
        assert services.parse_input_reference("output:12") == ("output", 12)
        assert services.parse_input_reference("input:3") == ("input", 3)

    def test_a_malformed_reference_says_what_a_reference_looks_like(self):
        for bad in ("", "12", "output:", "output:abc", "gallery:12", "output:1:2"):
            with pytest.raises(ValueError, match="output:<id>"):
                services.parse_input_reference(bad)

    def test_it_resolves_an_output_to_its_stored_bytes(self, tmp_path):
        output = stored_output(tmp_path)
        stored = services.stored_input(f"output:{output.id}")
        assert b"".join(stored.chunks()) == PNG
        assert stored.content_type == "image/png"
        assert stored.name == "0-job_00001_.png"

    def test_it_resolves_an_input_to_its_stored_bytes(self, tmp_path):
        output = stored_output(tmp_path)
        source = tmp_path / "init_image-beach.png"
        source.write_bytes(PNG)
        job_input = JobInput.objects.create(
            job=output.job, param_key="init_image", path=str(source), media_type="image/png"
        )
        assert services.stored_input(f"input:{job_input.id}").name == "init_image-beach.png"

    def test_an_unknown_id_is_a_value_error_naming_the_reference(self):
        with pytest.raises(ValueError, match="output:9999"):
            services.stored_input("output:9999")

    def test_a_row_whose_file_is_gone_says_so_rather_than_failing_later(self, tmp_path):
        output = stored_output(tmp_path)
        Path(output.path).unlink()
        with pytest.raises(ValueError, match="no longer on disk"):
            services.stored_input(f"output:{output.id}")

    def test_exists_answers_the_display_only_question_without_raising(self, tmp_path):
        """A page rendering a carried reference wants "is it still there",
        not a file object it would throw away -- and a stale link must
        leave a normal empty form behind, never an error page."""
        output = stored_output(tmp_path)
        assert services.stored_input_exists(f"output:{output.id}") is True
        assert services.stored_input_exists("output:9999") is False
        assert services.stored_input_exists("nonsense") is False

    def test_exists_is_false_once_the_file_is_gone(self, tmp_path):
        output = stored_output(tmp_path)
        Path(output.path).unlink()
        assert services.stored_input_exists(f"output:{output.id}") is False


@pytest.mark.django_db
class TestSubmitJobMergesFiles:
    def test_a_file_passed_only_as_a_file_still_validates_and_is_recorded(self):
        """A caller with a file has to name it once, not twice: `files` IS
        the value of that param as far as validation is concerned."""
        _bind()
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with patch.dict(operations._OPERATIONS, {"img2img": operations.IMG2IMG}), \
             _registered(StubEngine(StubGenerator())):
            job = services.submit_job(
                "img2img",
                {"prompt": "p", "negative_prompt": "", "denoise": "0.5", "steps": "20",
                 "cfg_scale": "7", "seed": "1", "sampler": "euler", "scheduler": "normal",
                 "batch_size": "1"},
                files={"init_image": upload},
            )
        assert job.params["init_image"] == "beach.png"
        assert JobInput.objects.get(job=job).param_key == "init_image"

    def test_an_undeclared_upload_is_ignored_not_stored(self):
        _bind()
        with _registered(StubEngine(StubGenerator())):
            job = services.submit_job(
                "txt2img", dict(RAW),
                files={"init_image": SimpleUploadedFile("beach.png", PNG)},
            )
        assert job.inputs.count() == 0
        assert "init_image" not in job.params
```

- [ ] **Step 6: Run them to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_services.py -q`
Expected: FAIL — `AttributeError: module 'modules.vision.services' has no attribute 'parse_input_reference'`.

- [ ] **Step 7: Implement the resolver and the merge**

In `modules/vision/services.py`, above `submit_job`:

```python
# What a stored-file reference looks like, and the two things it can name.
# A REFERENCE, not a path: it is JSON-safe (so a queue payload or a future
# tool call can carry it), it names a row this module owns, and resolving
# it re-reads the platform's own copy of the bytes -- nothing about the
# filesystem crosses a caller's hands.
INPUT_REFERENCE_KINDS = ("output", "input")
_REFERENCE_SHAPE = "a reference looks like output:<id> or input:<id>"


def parse_input_reference(reference: str) -> tuple[str, int]:
    """Split `"output:12"` into `("output", 12)`.

    Raises `ValueError` with operator-facing text for anything else: this
    value arrives from a queue payload, a link, or a tool call, so it is
    untrusted input and a clear refusal beats a confusing failure later.
    """
    kind, _, raw_id = str(reference or "").partition(":")
    if kind not in INPUT_REFERENCE_KINDS or not raw_id.isdigit():
        raise ValueError(f"{reference!r} is not a stored-image reference — {_REFERENCE_SHAPE}.")
    return kind, int(raw_id)


def stored_input(reference: str) -> store.StoredFile:
    """The file `reference` names, as an upload-shaped object.

    The point of the indirection: `submit_job(files=...)` takes one kind of
    thing, and a browser upload and a gallery image both become that thing
    here -- so a queued job, a "use this image" link, and a form post all
    travel the SAME submission path.

    Raises `ValueError` for a malformed reference, a row that does not
    exist, or a row whose file has been deleted (a job's directory is
    removed with the job).
    """
    row = _referenced_row(reference)
    if row is None:
        raise ValueError(f"{reference} does not name a stored image.")
    if not row.path or not os.path.isfile(row.path):
        raise ValueError(f"The file for {reference} is no longer on disk.")
    return store.StoredFile(row.path, content_type=row.media_type or "")


def _referenced_row(reference: str):
    """The `GeneratedOutput` or `JobInput` row `reference` names, or None."""
    kind, pk = parse_input_reference(reference)
    model = GeneratedOutput if kind == "output" else JobInput
    return model.objects.filter(pk=pk).first()


def stored_input_exists(reference: str) -> bool:
    """True when `reference` still names a file on disk.

    What a caller that only wants to DISPLAY a carried reference needs --
    the create page checking whether the thumbnail it is about to render
    still exists. `stored_input` would answer the same question by
    building a `StoredFile` and throwing it away; this asks it directly and
    treats a malformed reference as "no" rather than raising, because a
    stale link should leave a normal empty form behind, not an error page.
    """
    try:
        row = _referenced_row(reference)
    except ValueError:
        return False
    return row is not None and bool(row.path) and os.path.isfile(row.path)
```

Add `import os` to that module's imports.

In `submit_job`, merge the declared files into the raw params BEFORE validating, and drop the separate iteration's filtering duplication:

```python
    operation = get_operation(operation_key)
    if operation is None:
        raise ValueError(f"Unknown operation {operation_key!r}")

    # Only files answering a `"file"` param the operation actually declares
    # count: `request.FILES` carries whatever was posted, and an undeclared
    # upload has no meaning, no `JobInput` row, and no business in the
    # managed store.
    file_params = operation.file_param_keys()
    supplied_files = {key: value for key, value in (files or {}).items() if key in file_params}

    # A file IS the value of its param. Merging here means a caller names
    # it once (`files=`) and validation still sees it -- `_file_reference`
    # reduces it to a JSON-safe basename for the row, and the bytes go to
    # the store below.
    params = validate_params(operation, {**raw_params, **supplied_files})

    check = preflight()
    ...
    inputs: dict[str, Path] = {}
    for param_key, uploaded in supplied_files.items():
        path = store.store_input(job.id, param_key, uploaded)
        JobInput.objects.create(
            job=job,
            param_key=param_key,
            path=path,
            media_type=getattr(uploaded, "content_type", "") or "",
        )
        inputs[param_key] = Path(path)
```

- [ ] **Step 8: Run the service tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_services.py -q`
Expected: PASS.

- [ ] **Step 9: Write the failing queue tests**

In `modules/vision/tests/test_jobs.py`, replace `TestRunGenerateRejectsFileParams` (`:96-141`) with the class below. Two of its three tests carry assertions the new behaviour still needs and they are CARRIED OVER, not dropped: `test_a_payload_for_the_same_operation_without_the_file_key_is_not_rejected_here` is the only test that pins how `run_generate` calls `submit_job` (the call shape changes in Step 11), and `test_an_unregistered_operation_falls_through_to_submit_jobs_own_error` pins the `if operation is None: return {}` branch `_payload_files` keeps verbatim. Only the file-param REJECTION test goes away, because the rejection does.

```python
@pytest.mark.django_db
class TestPayloadInputs:
    """A queued job carries an image by REFERENCE. The payload stays JSON;
    the bytes stay in the managed store."""

    OPERATION = Operation(
        key="img2img", label="Image to image", capability="image-generation",
        output_media="image/png",
        params=(
            Param("prompt", "text", "Prompt", default="", required=True),
            Param("init_image", "file", "Init image", accept="image/*", required=True),
            Param("seed", "seed", "Seed"),
        ),
    )

    def test_it_resolves_every_referenced_input(self, tmp_path):
        output = stored_output(tmp_path)
        with patch.dict(operations_module._OPERATIONS, {"img2img": self.OPERATION}):
            files = jobs._payload_files(
                "img2img", {"operation": "img2img", "params": {"prompt": "p"},
                            "inputs": {"init_image": f"output:{output.id}"}}
            )
        assert b"".join(files["init_image"].chunks()) == PNG

    def test_a_payload_naming_an_undeclared_file_param_is_refused(self, tmp_path):
        output = stored_output(tmp_path)
        with patch.dict(operations_module._OPERATIONS, {"img2img": self.OPERATION}):
            with pytest.raises(ValueError, match="mask_image"):
                jobs._payload_files(
                    "img2img", {"inputs": {"mask_image": f"output:{output.id}"}}
                )

    def test_a_payload_with_no_inputs_resolves_to_nothing(self):
        with patch.dict(operations_module._OPERATIONS, {"img2img": self.OPERATION}):
            assert jobs._payload_files("img2img", {"params": {"prompt": "p"}}) == {}

    def test_an_unresolvable_reference_raises_with_the_operator_facing_text(self):
        with patch.dict(operations_module._OPERATIONS, {"img2img": self.OPERATION}):
            with pytest.raises(ValueError, match="does not name a stored image"):
                jobs._payload_files("img2img", {"inputs": {"init_image": "output:4242"}})

    def test_the_payload_itself_stays_json(self, tmp_path):
        """The whole reason references exist: nothing in a payload has to
        be anything but a string."""
        output = stored_output(tmp_path)
        payload = {"operation": "img2img", "params": {"prompt": "p"},
                   "inputs": {"init_image": f"output:{output.id}"}}
        assert json.loads(json.dumps(payload)) == payload

    # --- carried over from TestRunGenerateRejectsFileParams -------------

    def test_an_unregistered_operation_is_left_to_submit_jobs_own_error(self):
        """`_payload_files` has no schema to check for an unknown operation
        key, so it lets the payload through -- `services.submit_job` raises
        the one operator-facing "Unknown operation" message, and a second
        copy of that check here would be a second place to keep in sync."""
        assert jobs._payload_files("not-a-real-operation", {"inputs": {"x": "output:1"}}) == {}
        with pytest.raises(ValueError, match="Unknown operation"):
            jobs.run_generate({"operation": "not-a-real-operation", "params": {}}, [])

    def test_run_generate_passes_the_resolved_files_through_to_submit_job(self):
        """The ONE test that pins how the handler calls the service layer.
        A payload with no `inputs` still passes `files=None`, so the page
        path and the queue path reach `submit_job` in exactly one shape."""
        stub_job = MagicMock()
        stub_job.id = "stub-id"
        stub_job.status = GenerationJob.Status.DONE
        stub_job.outputs.values_list.return_value = []

        with patch.dict(operations_module._OPERATIONS, {"img2img": self.OPERATION}), patch(
            "modules.vision.jobs.services.submit_job", return_value=stub_job
        ) as mock_submit, patch("modules.vision.jobs.services.wait_for", return_value=stub_job):
            jobs.run_generate({"operation": "img2img", "params": {"prompt": "x"}}, [])

        mock_submit.assert_called_once_with("img2img", {"prompt": "x"}, files=None)

    def test_a_referenced_input_reaches_submit_job_as_a_stored_file(self, tmp_path):
        output = stored_output(tmp_path)
        stub_job = MagicMock()
        stub_job.id = "stub-id"
        stub_job.status = GenerationJob.Status.DONE
        stub_job.outputs.values_list.return_value = []

        with patch.dict(operations_module._OPERATIONS, {"img2img": self.OPERATION}), patch(
            "modules.vision.jobs.services.submit_job", return_value=stub_job
        ) as mock_submit, patch("modules.vision.jobs.services.wait_for", return_value=stub_job):
            jobs.run_generate(
                {"operation": "img2img", "params": {"prompt": "x"},
                 "inputs": {"init_image": f"output:{output.id}"}},
                [],
            )

        _key, _params = mock_submit.call_args.args
        files = mock_submit.call_args.kwargs["files"]
        assert list(files) == ["init_image"]
        assert b"".join(files["init_image"].chunks()) == PNG
```

(`MagicMock` is already imported in that module; `PNG` and `stored_output` come from `_helpers` per Task 3 Step 1.)

- [ ] **Step 10: Run it to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_jobs.py -q`
Expected: FAIL — `AttributeError: module 'modules.vision.jobs' has no attribute '_payload_files'`.

- [ ] **Step 11: Replace the rejection with resolution**

In `modules/vision/jobs.py`, delete `_reject_queued_file_params` and add:

```python
def _payload_files(operation_key: str, payload: dict) -> dict:
    """Resolve a payload's `"inputs"` references into stored files.

    A queue payload is JSON, so it cannot carry an upload -- but it can
    carry a REFERENCE to a file the platform already holds
    (`{"init_image": "output:12"}`, a gallery result; `"input:3"`, another
    job's input). `modules.vision.services.stored_input` turns each into
    the same upload-shaped object a browser post produces, so a queued
    generation goes through `submit_job`'s one submission path with no
    second store-and-record branch anywhere.

    A reference for a param the operation does not declare is refused
    rather than dropped: silently ignoring it would run a generation that
    is not the one that was asked for. An unknown operation is let through
    un-refused -- `services.submit_job` raises the operator-facing
    "Unknown operation" for that case, and a second copy of that check
    here is a second place to keep in sync.
    """
    references = payload.get("inputs") or {}
    if not references:
        return {}
    operation = get_operation(operation_key)
    if operation is None:
        return {}
    undeclared = sorted(set(references) - operation.file_param_keys())
    if undeclared:
        raise ValueError(
            f"operation {operation_key!r} declares no file parameter named "
            f"{', '.join(undeclared)} — it takes {sorted(operation.file_param_keys())}."
        )
    return {key: services.stored_input(reference) for key, reference in references.items()}
```

and in `run_generate`, replace the rejection call with:

```python
    operation_key = payload["operation"]
    params = payload.get("params") or {}
    files = _payload_files(operation_key, payload)

    job = services.submit_job(operation_key, params, files=files or None)
    job = services.wait_for(job, timeout=GENERATE_WAIT_TIMEOUT_SECONDS)
```

Update `run_generate`'s docstring item 1 to:

```python
    1. Resolves any `"inputs"` references into stored files
       (`_payload_files`) -- the JSON-safe way a queued job carries an
       image.
```

and replace the module docstring's `payload` paragraph with:

```python
`payload` shape: `{"operation": str, "params": dict, "inputs": dict}` --
`operation` is a registered `core.inference.operations.Operation` key,
`params` is that operation's RAW (unvalidated) parameter dict, and the
optional `inputs` maps a `"file"` param's key
(`Operation.file_param_keys()`) to a stored-image REFERENCE:
`"output:<GeneratedOutput id>"` or `"input:<JobInput id>"`. The payload
therefore stays plain JSON -- no upload object, no path, no bytes -- while
a queued img2img/inpaint/upscale job still runs on a real image, because
`modules.vision.services.stored_input` re-reads the platform's own copy
and hands `submit_job` the same upload-shaped object a browser post
produces.
```

- [ ] **Step 12: Run the queue tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_jobs.py -q`
Expected: PASS.

- [ ] **Step 13: Check nothing else builds a vision payload**

```bash
grep -rn "vision.generate" console modules scripts docs --include=*.py --include=*.html --include=*.md | grep -v tests
```

Any caller constructing a payload (there is none today outside `modules/vision/jobs.py` and the console's generic enqueue form) must not need a change — references are an ADDITIVE optional key. Confirm and move on.

- [ ] **Step 14: Document the contract**

In `modules/vision/README.md`, replace the "**No file parameters through the queue yet.**" bullet with:

```markdown
- **File parameters travel as references.** A payload is JSON, so it carries
  `{"inputs": {"init_image": "output:12"}}` — `output:<GeneratedOutput id>` for a
  gallery result or `input:<JobInput id>` for another job's input — and
  `services.stored_input` re-reads the platform's own copy of those bytes and hands
  `submit_job` the same upload-shaped object (`store.StoredFile`) a browser post
  produces. One submission path, no second store-and-record branch, and nothing but
  strings in the payload. A reference naming a param the operation does not declare
  is refused, not dropped.
```

In `docs/adr/0012-image-generation-engine-adapter.md`, add a subsection immediately after "Input transfer belongs to `submit` (2026-08-23)":

```markdown
### A stored file has a reference (2026-08-24)

`ImageGenerator.submit` owns getting bytes to the ENGINE; this is the
counterpart on the caller's side — getting bytes to `submit_job` from
somewhere that is not a browser upload. A queue payload is JSON and a
future chatbot tool call is JSON, so neither can carry an upload object,
and weakening that would put a non-serializable value into
`GenerationJob.params`' JSONField the moment anything went wrong.

The reference is `"output:<GeneratedOutput id>"` or
`"input:<JobInput id>"` (`modules/vision/services.py::parse_input_reference`),
resolved by `services.stored_input` into a `store.StoredFile` — the small
slice of Django's uploaded-file API (`.name`, `.content_type`,
`.chunks()`) that `store.store_input` and `operations._file_reference`
actually use. Every caller therefore reaches ONE submission path:
`submit_job(operation_key, raw_params, files=...)`, which merges the
declared files into the params it validates so a file is named once, not
twice.

Each job still copies the bytes into its OWN directory. Deleting a job
deletes its directory (spec §5), and a job whose input lived in another
job's folder would lose the record of what it ran on.
```

- [ ] **Step 15: Run both suite orders and commit**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest -q`
Expected: PASS.

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules console scripts -q`
Expected: PASS with the same counts.

```bash
git add modules/vision/store.py modules/vision/services.py modules/vision/jobs.py \
        modules/vision/tests/ modules/vision/README.md \
        docs/adr/0012-image-generation-engine-adapter.md
git commit -m "feat(vision): queued jobs carry images by reference, not by bytes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 5: "Use in …" — the gallery feeds an image into any file-taking mode

**Depends on:** Tasks 2 and 4 — its tests register `IMG2IMG`, and its `generate` calls `services.stored_input`.

The reference from Task 4 has one more caller: the page. A gallery result becomes the init image of an img2img run — or, once Task 9 lands, the input of an upscale — by a link, with no download-and-re-upload round trip. Driven by the operation registry, so the links appear for a mode the moment it registers and name no operation here.

**Files:**
- Modify: `core/inference/operations.py` (`Operation.file_params()`; `file_param_keys()` derived from it)
- Modify: `modules/vision/forms.py` (`build_form(..., stored_keys=frozenset())`)
- Modify: `modules/vision/views.py` (`input_targets`, `stored_input_refs`, `_stored_input_context`, `build_form_for`, `CreatePageView`, `generate`, `gallery`)
- Modify: `modules/vision/templates/vision/create.html`, `modules/vision/templates/vision/gallery.html`
- Test: `modules/vision/tests/test_operations.py`, `modules/vision/tests/test_forms.py`, `modules/vision/tests/test_views_create.py`, `modules/vision/tests/test_views_gallery.py`, `modules/vision/tests/test_views_generate.py`
- Docs: `modules/vision/README.md`

**Interfaces:**
- Produces:
  - `Operation.file_params() -> tuple[Param, ...]` (declaration order; `file_param_keys()` becomes `frozenset(p.key for p in self.file_params())`).
  - `forms.build_form(operation, engine_options, data=None, files=None, initial=None, stored_keys=frozenset())` — a file field whose key is in `stored_keys` is not required (a stored reference already answers it).
  - `views.input_targets() -> list[dict]` → `[{"label", "param_key", "url"}]`, one per registered operation with at least one file param.
  - `views.stored_input_refs(request, operation) -> dict[str, str]` — reads `input_<param key>` from GET or POST.
  - URL query/POST field convention: `input_<param key>=<reference>`.
- Consumes: `services.stored_input`, `services.stored_input_exists`, `services.parse_input_reference` (Task 4); `page_operations()`, `resolve_page_operation` (shipped).

- [ ] **Step 1: Write the failing `file_params` and form tests**

Append to `modules/vision/tests/test_operations.py`:

```python
class TestFileParamOrder:
    def test_file_params_keep_declaration_order(self):
        """A link that pre-fills "the image" must pick the FIRST file
        param, which a frozenset cannot tell it."""
        operation = Operation(
            key="two_files", label="Two files", capability="image-generation",
            output_media="image/png",
            params=(
                Param("init_image", "file", "Image", required=True),
                Param("steps", "int", "Steps", default=1),
                Param("mask_image", "file", "Mask", required=True),
            ),
        )
        assert [param.key for param in operation.file_params()] == ["init_image", "mask_image"]
        assert operation.file_param_keys() == frozenset({"init_image", "mask_image"})

    def test_an_operation_with_no_file_params_has_an_empty_tuple(self):
        assert TXT2IMG.file_params() == ()
```

Append to `modules/vision/tests/test_forms.py`:

```python
class TestStoredKeys:
    OPERATION = Operation(
        key="img2img", label="Image to image", capability="image-generation",
        output_media="image/png",
        params=(
            Param("prompt", "text", "Prompt", default="", required=True),
            Param("init_image", "file", "Init image", accept="image/*", required=True),
        ),
    )

    def test_a_file_field_a_stored_reference_answers_is_not_required(self):
        """The operator picked a gallery image; asking them to also attach
        a file would be asking twice for the same thing."""
        form = build_form(self.OPERATION, {}, stored_keys=frozenset({"init_image"}))
        assert form.fields["init_image"].required is False

    def test_without_a_stored_reference_the_file_stays_required(self):
        form = build_form(self.OPERATION, {})
        assert form.fields["init_image"].required is True

    def test_a_bound_form_validates_with_the_file_left_empty(self):
        form = build_form(
            self.OPERATION, {}, data={"prompt": "p"}, stored_keys=frozenset({"init_image"})
        )
        assert form.is_valid(), form.errors
```

- [ ] **Step 2: Run them to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_operations.py modules/vision/tests/test_forms.py -q`
Expected: FAIL — `AttributeError: 'Operation' object has no attribute 'file_params'` and `TypeError: build_form() got an unexpected keyword argument 'stored_keys'`.

- [ ] **Step 3: Implement both**

In `core/inference/operations.py`, add `file_params()` and derive `file_param_keys()` from it:

```python
    def file_params(self) -> tuple[Param, ...]:
        """Every `"file"` param, in DECLARATION order.

        Order matters to one caller and only one: a "use this image here"
        link pre-fills the first file param an operation declares (an
        inpaint job's image, leaving its mask to be chosen), and a
        frozenset cannot say which that is.
        """
        return tuple(param for param in self.params if param.kind == "file")

    def file_param_keys(self) -> frozenset[str]:
        """Every `"file"` param's key.
        ...existing docstring body unchanged...
        """
        return frozenset(param.key for param in self.file_params())
```

In `modules/vision/forms.py`, add the argument and apply it after the fields are built:

```python
def build_form(
    operation: Operation,
    engine_options: dict[str, tuple[str, ...]],
    data=None,
    files=None,
    initial: dict | None = None,
    stored_keys=frozenset(),
) -> forms.Form:
    """...existing docstring, plus:

    `stored_keys` names file params a STORED image already answers (the
    gallery's "use in ..." link, carried as `input_<key>`): those fields
    stop being required, because the operator has already said which image
    to use and attaching a second one would be answering twice. They stay
    RENDERED and optional -- attaching a file replaces the stored choice.
    """
    fields = {param.key: _field_for(param, engine_options) for param in operation.params}
    ...existing initial handling...
    for key in stored_keys:
        if key in fields:
            fields[key].required = False
    form_class = type("GenerationForm", (forms.Form,), fields)
    return form_class(data=data, files=files)
```

Rename the parameter `engine_choices` to `engine_options` in `build_form` and `_field_for` (it will carry asset options too from Task 7) and update the two call sites in `modules/vision/views.py`. Run a grep to catch every reference:

```bash
grep -rn "engine_choices" modules core console
```

- [ ] **Step 4: Run them to verify they pass**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_operations.py modules/vision/tests/test_forms.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing page tests**

Append to `modules/vision/tests/test_views_gallery.py`:

```python
@pytest.mark.django_db
class TestUseInLinks:
    def test_the_gallery_offers_every_file_taking_mode_by_registration_alone(self, client, tmp_path):
        output = stored_output(tmp_path)
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "img2img": IMG2IMG}):
            body = client.get(reverse("vision-gallery")).content.decode()

        expected = (
            reverse("vision-create-operation", args=["img2img"])
            + f"?input_init_image=output:{output.id}"
        )
        assert expected in body
        assert "Use in Image to image" in body

    def test_a_mode_with_no_file_params_gets_no_link(self, client, tmp_path):
        stored_output(tmp_path)
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG}):
            body = client.get(reverse("vision-gallery")).content.decode()
        assert "Use in Text to image" not in body
```

(`stored_output` and `PNG` are the shared helpers Task 3 Step 1 added to `modules/vision/tests/_helpers.py`; import them here too. Do not write a second builder — the whole point of defining it once is that Tasks 3, 4, and 5 assert against the same fixture.)

Append to `modules/vision/tests/test_views_create.py`:

```python
@pytest.mark.django_db
class TestStoredInputPrefill:
    def test_the_page_carries_the_reference_as_a_hidden_field(self, client, tmp_path):
        _bind()
        output = stored_output(tmp_path)
        url = (
            reverse("vision-create-operation", args=["img2img"])
            + f"?input_init_image=output:{output.id}"
        )
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "img2img": IMG2IMG}), _engine():
            body = client.get(url).content.decode()

        assert f'<input type="hidden" name="input_init_image" value="output:{output.id}">' in body
        assert reverse("vision-output-file", args=[output.id]) in body
        assert "Init image" in body

    def test_an_unresolvable_reference_is_ignored_rather_than_breaking_the_page(self, client):
        _bind()
        url = reverse("vision-create-operation", args=["img2img"]) + "?input_init_image=output:4242"
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "img2img": IMG2IMG}), _engine():
            response = client.get(url)
        assert response.status_code == 200
        assert 'name="input_init_image"' not in response.content.decode()
```

Append to `modules/vision/tests/test_views_generate.py`:

```python
@pytest.mark.django_db
class TestGenerateFromAStoredImage:
    def test_a_referenced_image_becomes_the_jobs_input_with_no_upload(self, client, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path / "generated"
        _bind()
        output = stored_output(tmp_path)
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "img2img": IMG2IMG}), _engine():
            response = client.post(
                reverse("vision-generate"),
                {"operation": "img2img", "prompt": "a lighthouse", "negative_prompt": "",
                 "denoise": "0.5", "steps": "20", "cfg_scale": "7", "seed": "1",
                 "sampler": "euler", "scheduler": "normal", "batch_size": "1",
                 "input_init_image": f"output:{output.id}"},
                **XHR,
            )

        assert response.status_code == 200
        job = GenerationJob.objects.get(operation="img2img")
        assert job.inputs.get(param_key="init_image").path.endswith(".png")
        assert job.params["init_image"]

    def test_an_unresolvable_reference_is_a_400_that_names_it(self, client):
        _bind()
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "img2img": IMG2IMG}), _engine():
            response = client.post(
                reverse("vision-generate"),
                {"operation": "img2img", "prompt": "p", "negative_prompt": "", "denoise": "0.5",
                 "steps": "20", "cfg_scale": "7", "seed": "1", "sampler": "euler",
                 "scheduler": "normal", "batch_size": "1",
                 "input_init_image": "output:4242"},
                **XHR,
            )
        assert response.status_code == 400
        assert "does not name a stored image" in response.content.decode()
```

- [ ] **Step 6: Run them to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_views_gallery.py modules/vision/tests/test_views_create.py modules/vision/tests/test_views_generate.py -q`
Expected: FAIL — no "Use in" link is rendered and the stored reference is ignored.

- [ ] **Step 7: Implement the view side**

First rename `views.live_choices` to `views.live_options` (both definition and its two call sites) — it is about to answer for asset params too (Task 7), and renaming it here, in the same commit that renames `build_form`'s `engine_choices` argument, keeps one vocabulary instead of two. Its body is unchanged in this task; only the name and the docstring's first line move:

```python
def live_options(operation: Operation, resolved) -> dict[str, tuple[str, ...]]:
    """Engine-reported options for every param of `operation` whose options
    the ENGINE owns -- today the `"choice"` params (samplers, schedulers).
    ...rest of the existing docstring and body unchanged...
    """
```

Then, in `modules/vision/views.py`:

```python
def input_targets() -> list[dict]:
    """Every registered mode an existing image can be fed into.

    Read from the REGISTRY, so a mode appears here the day it registers a
    file param and this function names none. Each target pre-fills the
    operation's FIRST file param (`Operation.file_params()`): inpaint's
    image, leaving its mask for the operator to choose.
    """
    targets = []
    for operation in page_operations():
        params = operation.file_params()
        if not params:
            continue
        targets.append(
            {
                "label": operation.label,
                "param_key": params[0].key,
                "url": reverse("vision-create-operation", args=[operation.key]),
            }
        )
    return targets


def stored_input_refs(request, operation: Operation) -> dict[str, str]:
    """`{param key: reference}` for every file param this request answers
    with an image already in the store.

    Read from the query string on a GET (the gallery's link) and from the
    POST on a submission (the hidden field the page carries back), so the
    two halves of the flow agree without the page inventing a session.
    """
    source = request.POST if request.method == "POST" else request.GET
    refs = {}
    for param in operation.file_params():
        raw = (source.get(f"input_{param.key}") or "").strip()
        if raw:
            refs[param.key] = raw
    return refs


def _stored_input_context(refs: dict[str, str], operation: Operation) -> list[dict]:
    """What the create page needs to SHOW a carried reference: the param's
    own label, the reference to post back, and a URL to preview it.

    A reference that no longer resolves (its job was deleted between the
    gallery link and this render) is dropped silently -- the operator gets
    a normal empty file field, which is exactly what a stale link should
    leave behind.
    """
    shown = []
    for param in operation.file_params():
        reference = refs.get(param.key)
        if not reference:
            continue
        try:
            kind, pk = services.parse_input_reference(reference)
        except ValueError:
            logger.debug("Ignoring malformed stored input %r", reference, exc_info=True)
            continue
        if not services.stored_input_exists(reference):
            logger.debug("Ignoring stored input %r that no longer resolves", reference)
            continue
        url_name = "vision-output-file" if kind == "output" else "vision-input-file"
        shown.append(
            {
                "param_key": param.key,
                "label": param.label,
                "reference": reference,
                "url": reverse(url_name, args=[pk]),
            }
        )
    return shown
```

In `CreatePageView.get_context_data`, after `operation` is resolved:

```python
        refs = stored_input_refs(self.request, operation)
        context["stored_inputs"] = _stored_input_context(refs, operation)
        context["form"] = build_form(
            operation,
            live_options(operation, check.resolved),
            initial=_reuse_initial(reuse_job),
            stored_keys=frozenset(item["param_key"] for item in context["stored_inputs"]),
        )
```

In `build_form_for`, thread the same set through:

```python
def build_form_for(request, operation: Operation, check, stored_keys=frozenset()):
    """The bound form for a submission, with the engine's live options and
    any file param a stored image already answers."""
    from modules.vision.forms import build_form

    return build_form(
        operation,
        live_options(operation, check.resolved),
        data=request.POST,
        files=request.FILES or None,
        stored_keys=stored_keys,
    )
```

In `generate`, resolve the references into the same `files` mapping an upload lands in:

```python
    operation = resolve_page_operation(request.POST.get("operation"))
    check = services.preflight()
    refs = stored_input_refs(request, operation)
    form = build_form_for(request, operation, check, stored_keys=frozenset(refs))

    if not form.is_valid():
        return _invalid_form_response(request, operation, check, form)

    # An attached file always wins over a carried reference: the operator
    # picking a new file is them changing their mind, and the form still
    # rendered the field for exactly that.
    files = dict(request.FILES.items())
    for param_key, reference in refs.items():
        if param_key in files:
            continue
        try:
            files[param_key] = services.stored_input(reference)
        except ValueError as exc:
            form.add_error(None, str(exc))
            return _invalid_form_response(request, operation, check, form)

    try:
        job = services.submit_job(operation.key, dict(form.cleaned_data), files=files or None)
```

In `_create_page_response`, carry the same context so a re-rendered invalid form does not lose the reference:

```python
    refs = stored_input_refs(request, operation)
    return render(
        request,
        "vision/create.html",
        {
            "operation": operation,
            "operations": page_operations(),
            "preflight": check,
            "form": form,
            "stored_inputs": _stored_input_context(refs, operation),
            "jobs": _recent_jobs(),
        },
        status=status,
    )
```

In `gallery`, add the targets:

```python
def gallery(request):
    """GET /vision/gallery/ -- every generated output, newest first."""
    outputs = GeneratedOutput.objects.select_related("job").order_by("-job__created_at", "index")
    page = Paginator(outputs, GALLERY_PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "vision/gallery.html",
        {"page_obj": page, "input_targets": input_targets()},
    )
```

- [ ] **Step 8: Implement the template side**

In `modules/vision/templates/vision/create.html`, immediately after the hidden `operation` field inside the form:

```html
  {% comment %}
  An image the operator picked from the gallery, carried as a reference
  (`views.stored_input_refs`) rather than re-uploaded. The file field for the
  same param is still rendered below and is optional: attaching a file
  replaces this choice.
  {% endcomment %}
  {% for stored in stored_inputs %}
  <div class="stored-input">
    <input type="hidden" name="input_{{ stored.param_key }}" value="{{ stored.reference }}">
    <a href="{{ stored.url }}" target="_blank" rel="noopener">
      <img src="{{ stored.url }}" alt="{{ stored.label }}">
    </a>
    <span class="muted">Using this image as {{ stored.label }} — attach a file below to replace it.</span>
  </div>
  {% endfor %}
```

and one rule in that page's `{% block vision_style %}`:

```css
  .stored-input { display: flex; align-items: center; gap: 0.75rem; }
  .stored-input img { max-height: 96px; width: auto; border-radius: 6px; display: block; }
```

In `modules/vision/templates/vision/gallery.html`, inside the `figcaption`, before the "Reuse settings" link:

```html
      {% for target in input_targets %}
      <a href="{{ target.url }}?input_{{ target.param_key }}=output:{{ output.id }}">Use in {{ target.label }}</a> ·
      {% endfor %}
```

- [ ] **Step 9: Run the page tests to verify they pass**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_views_gallery.py modules/vision/tests/test_views_create.py modules/vision/tests/test_views_generate.py -q`
Expected: PASS.

- [ ] **Step 10: Document the flow**

In `modules/vision/README.md`, after the "The poll-driven page and its no-JS fallback" section, add:

```markdown
## Feeding an image back in

The gallery renders one "Use in <mode>" link per registered operation that declares a
file param — driven by `views.input_targets()`, so a mode appears there the day it
registers and the template names none. The link is
`/vision/op/<key>/?input_<param>=output:<id>`; the create page carries that reference
in a hidden field, shows a thumbnail of it, and makes the matching file field optional
(`forms.build_form(stored_keys=...)`). On submit, `generate` resolves the reference with
`services.stored_input` into the same upload-shaped object an attached file produces —
an attached file always wins — so nothing about the submission path changes. It is the
same reference a queued payload carries (`{"inputs": {"init_image": "output:12"}}`),
with one resolver behind both.
```

- [ ] **Step 11: Run both suite orders and commit**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest -q`
Expected: PASS.

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules console scripts -q`
Expected: PASS with the same counts.

```bash
git add core/inference/operations.py modules/vision/forms.py modules/vision/views.py \
        modules/vision/templates/vision/ modules/vision/tests/ modules/vision/README.md
git commit -m "feat(vision): send a gallery image into any file-taking mode

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 6: inpaint — two file inputs, and the node ordering it exposes (B4)

**Depends on:** Task 5 (`Operation.file_params()`, which `INPAINT`'s two file params need in declaration order).

Inpainting is the first mode with two file params and the first graph big enough to make `fetch_outputs`' string-sorted node ids matter. Both belong to this task: the sort is inert until a graph has ten nodes and two outputs, and this is the graph that gets there.

**Files:**
- Modify: `core/inference/operations.py` (`INPAINT`)
- Create: `core/inference/engines/comfyui_workflows/inpaint.py`
- Modify: `core/inference/engines/comfyui_workflows/__init__.py`, `core/inference/engines/comfyui_workflows/_fragments.py` (`load_mask`, `encode_for_inpaint`)
- Modify: `core/inference/engines/comfyui.py` (`fetch_outputs` ordering)
- Modify: `modules/vision/apps.py`
- Test: `modules/vision/tests/test_operations.py`, `modules/vision/tests/test_comfyui_workflows.py` (including repointing `TestGetTemplate`'s unknown-operation example off `"inpaint"`), `modules/vision/tests/test_comfyui_generator.py`, `modules/vision/tests/test_apps.py`
- Docs: `modules/vision/README.md`

**Interfaces:**
- Produces: `operations.INPAINT` (key `"inpaint"`, params `prompt`, `negative_prompt`, `init_image` (file, required), `mask_image` (file, required), `mask_grow`, `denoise`, `steps`, `cfg_scale`, `seed`, `sampler`, `scheduler`, `batch_size`); `comfyui_workflows.inpaint.build(...)`; `_fragments.load_mask(graph, reference, channel="red") -> Link`; `_fragments.encode_for_inpaint(graph, ckpt, image, mask, grow_by) -> Link`; `comfyui._output_order(item) -> tuple`.
- Consumes: everything Tasks 1–2 produced; `ComfyUIGenerator._upload_inputs` already uploads EVERY entry of `request.inputs`, so two files need no adapter change.

- [ ] **Step 1: Write the failing schema test**

Append to `modules/vision/tests/test_operations.py`:

```python
class TestInpaintSchema:
    def test_it_takes_an_image_and_a_mask_in_that_order(self):
        assert [param.key for param in INPAINT.file_params()] == ["init_image", "mask_image"]
        assert all(param.required for param in INPAINT.file_params())
        assert all(param.accept == "image/*" for param in INPAINT.file_params())

    def test_the_mask_label_says_which_way_round_it_is(self):
        """A mask is useless if the operator has to guess whether white
        means keep or repaint. The label answers it where it is read."""
        assert "repaint" in INPAINT.param("mask_image").label.lower()

    def test_it_denoises_fully_by_default_because_the_masked_area_is_being_replaced(self):
        assert INPAINT.param("denoise").default == 1.0

    def test_growing_the_mask_is_a_bounded_int(self):
        param = INPAINT.param("mask_grow")
        assert (param.kind, param.default, param.min, param.max) == ("int", 6, 0, 64)

    def test_both_files_are_required_by_validation(self):
        with pytest.raises(ParamError) as exc:
            validate_params(INPAINT, {"prompt": "p", "sampler": "euler", "scheduler": "normal"})
        assert set(exc.value.errors) >= {"init_image", "mask_image"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_operations.py -q`
Expected: FAIL — `ImportError: cannot import name 'INPAINT'`.

- [ ] **Step 3: Define the operation**

In `core/inference/operations.py`, after `IMG2IMG`:

```python
INPAINT = Operation(
    key="inpaint",
    label="Inpaint",
    capability="image-generation",
    output_media="image/png",
    params=(
        Param("prompt", "text", "Prompt", default="", required=True),
        Param("negative_prompt", "text", "Negative prompt", default=""),
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
        Param("steps", "int", "Steps", default=25, min=1, max=150),
        Param("cfg_scale", "float", "CFG scale", default=7.0, min=0, max=30, step=0.5),
        Param("seed", "seed", "Seed", default=None),
        Param("sampler", "choice", "Sampler"),
        Param("scheduler", "choice", "Scheduler"),
        Param("batch_size", "int", "Batch size", default=1, min=1, max=8),
    ),
)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_operations.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing template test**

Append to `modules/vision/tests/test_comfyui_workflows.py`:

```python
INPAINT_PARAMS = {
    "prompt": "a lighthouse at dusk",
    "negative_prompt": "blurry",
    "mask_grow": 12,
    "denoise": 1.0,
    "steps": 30,
    "cfg_scale": 6.5,
    "seed": 123456,
    "sampler": "dpmpp_2m",
    "scheduler": "karras",
    "batch_size": 1,
}


def _inpaint_graph():
    request = GenerationRequest(
        operation="inpaint",
        model_id="sdxl.safetensors",
        params=dict(INPAINT_PARAMS),
        client_ref="8f14e45f-ceea-467a-9575-1b0e5a1d1f1e",
    )
    return get_template("inpaint")(
        request,
        request.model_id,
        {},
        {"init_image": "8f14e45f/beach.png", "mask_image": "8f14e45f/mask.png"},
    )


class TestInpaintGraph:
    def test_the_image_and_the_mask_are_loaded_by_their_own_references(self):
        graph = _inpaint_graph()
        assert graph["4"] == {"class_type": "LoadImage", "inputs": {"image": "8f14e45f/beach.png"}}
        assert graph["5"] == {
            "class_type": "LoadImageMask",
            "inputs": {"image": "8f14e45f/mask.png", "channel": "red"},
        }

    def test_the_masked_latent_carries_the_grow_amount(self):
        assert _inpaint_graph()["6"] == {
            "class_type": "VAEEncodeForInpaint",
            "inputs": {"pixels": ["4", 0], "vae": ["1", 2], "mask": ["5", 0], "grow_mask_by": 12},
        }

    def test_the_batch_repeat_and_sampler_follow_the_masked_latent(self):
        graph = _inpaint_graph()
        assert graph["7"]["class_type"] == "RepeatLatentBatch"
        assert graph["8"]["inputs"]["latent_image"] == ["7", 0]
        assert graph["8"]["inputs"]["denoise"] == 1.0

    def test_it_decodes_and_saves_under_the_job_prefix(self):
        graph = _inpaint_graph()
        assert graph["9"]["class_type"] == "VAEDecode"
        assert graph["10"]["class_type"] == "SaveImage"
        assert graph["10"]["inputs"]["filename_prefix"] == "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"
```

- [ ] **Step 6: Run it to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_comfyui_workflows.py -q`
Expected: FAIL — `ValueError: No ComfyUI template for operation 'inpaint'`.

- [ ] **Step 7: Add the two fragments and the template**

Append to `core/inference/engines/comfyui_workflows/_fragments.py`:

```python
def load_mask(graph: Graph, reference: str, channel: str = "red") -> Link:
    """Load a transferred image AS A MASK.

    ComfyUI's `LoadImageMask` reads one channel of an image file into a
    MASK, which is what an inpainting encoder wants; `"red"` is the channel
    a plain black-and-white mask carries its shape in, and white reads as
    the area to repaint.
    """
    node = graph.add("LoadImageMask", image=reference, channel=channel)
    return [node, 0]


def encode_for_inpaint(graph: Graph, ckpt: Checkpoint, image: Link, mask: Link, grow_by: int) -> Link:
    """Pixels + mask -> a latent whose masked area is erased, ready to be
    resampled. `grow_by` feathers the mask outward to hide the seam."""
    node = graph.add(
        "VAEEncodeForInpaint",
        pixels=image,
        vae=ckpt.vae,
        mask=mask,
        grow_mask_by=grow_by,
    )
    return [node, 0]
```

Create `core/inference/engines/comfyui_workflows/inpaint.py`:

```python
"""Inpainting graph: checkpoint -> two CLIP encodes, load the image and its
mask -> VAEEncodeForInpaint -> repeat for the batch -> KSampler -> VAE
decode -> SaveImage. Built-in nodes only (D1)."""
from __future__ import annotations

from core.inference.engines.comfyui_workflows import _fragments
from core.inference.operations import GenerationRequest


def build(request: GenerationRequest, model_id: str, config: dict, inputs: dict) -> dict:
    """The API-format graph for one inpaint request.

    Two file params, both required, both already transferred by
    `ImageGenerator.submit`: `inputs["init_image"]` is the picture and
    `inputs["mask_image"]` is the black-and-white mask whose WHITE area is
    repainted. Both are indexed rather than `.get()` for the reason
    img2img's is -- an absent reference is a bug above this line and should
    name itself.

    `config` (D8) is ignored, as in every single-file-checkpoint template.
    """
    params = request.params
    graph = _fragments.Graph()
    ckpt = _fragments.checkpoint(graph, model_id)
    positive, negative = _fragments.prompts(graph, ckpt, params)
    image = _fragments.load_image(graph, inputs["init_image"])
    mask = _fragments.load_mask(graph, inputs["mask_image"])
    latent = _fragments.encode_for_inpaint(graph, ckpt, image, mask, params["mask_grow"])
    latent = _fragments.repeat_batch(graph, latent, params["batch_size"])
    samples = _fragments.sample(graph, ckpt, positive, negative, latent, params)
    _fragments.decode_and_save(graph, ckpt, samples, request)
    return graph.as_dict()
```

In `comfyui_workflows/__init__.py`:

```python
from core.inference.engines.comfyui_workflows import img2img, inpaint, txt2img
...
_TEMPLATES: dict[str, Template] = {
    "txt2img": txt2img.build,
    "img2img": img2img.build,
    "inpaint": inpaint.build,
}
```

Registering `inpaint` breaks an existing test that used it as the example of an UNKNOWN operation — `modules/vision/tests/test_comfyui_workflows.py::TestGetTemplate::test_unknown_operation_is_a_clear_error` calls `get_template("inpaint")` and expects a `ValueError`. Repoint it at an operation this plan explicitly does not ship, so the assertion keeps meaning something:

```python
    def test_unknown_operation_is_a_clear_error(self):
        with pytest.raises(ValueError, match="No ComfyUI template"):
            get_template("controlnet")
```

- [ ] **Step 8: Run the template tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_comfyui_workflows.py -q`
Expected: PASS.

- [ ] **Step 9: Write the failing output-ordering test (B4)**

Append to `modules/vision/tests/test_comfyui_generator.py`:

```python
class TestOutputOrdering:
    """B4: node ids are strings, so `"10" < "2"` -- inert while a graph has
    one output node, wrong the moment a graph has ten nodes and two."""

    def _history_with_two_output_nodes(self, ref="p-1"):
        return {
            ref: {
                "status": {"status_str": "success", "completed": True, "messages": []},
                "outputs": {
                    "10": {"images": [{"filename": "second.png", "subfolder": "", "type": "output"}]},
                    "2": {"images": [{"filename": "first.png", "subfolder": "", "type": "output"}]},
                },
            }
        }

    def test_outputs_come_back_in_numeric_node_order(self):
        fake = FakeComfyUI(
            history=self._history_with_two_output_nodes(),
            images={"first.png": b"one", "second.png": b"two"},
        )
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            outputs = _generator().fetch_outputs("p-1")

        assert [name for name, _content, _media in outputs] == ["first.png", "second.png"]

    def test_a_non_numeric_node_id_sorts_last_instead_of_raising(self):
        """A third-party node pack may use a non-numeric id. Ordering is a
        display nicety; crashing a finished job over it is not."""
        history = self._history_with_two_output_nodes()
        history["p-1"]["outputs"]["save_final"] = {
            "images": [{"filename": "third.png", "subfolder": "", "type": "output"}]
        }
        fake = FakeComfyUI(
            history=history,
            images={"first.png": b"one", "second.png": b"two", "third.png": b"three"},
        )
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            outputs = _generator().fetch_outputs("p-1")

        assert [name for name, _content, _media in outputs] == ["first.png", "second.png", "third.png"]
```

- [ ] **Step 10: Run it to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_comfyui_generator.py -q`
Expected: FAIL — the order comes back `["second.png", "first.png"]`.

- [ ] **Step 11: Order node ids numerically**

In `core/inference/engines/comfyui.py`, add beside the other module-level helpers:

```python
def _output_order(item) -> tuple:
    """Sort key for one `outputs` entry, ordering node ids NUMERICALLY.

    ComfyUI's node ids are strings, so a plain sort puts `"10"` before
    `"2"` -- invisible while a graph has one `SaveImage` node, wrong the
    moment one has two (a hi-res pass, an upscale-then-save). A node id
    that is not a number at all (a third-party node pack) sorts after the
    numeric ones by its own text rather than raising: output ORDER is a
    display nicety and must never fail a finished job.
    """
    node_id = item[0]
    text = str(node_id)
    return (0, int(text), "") if text.isdigit() else (1, 0, text)
```

and use it in `fetch_outputs`:

```python
        for _node_id, node_output in sorted((history.get("outputs") or {}).items(), key=_output_order):
```

- [ ] **Step 12: Run it to verify it passes**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_comfyui_generator.py -q`
Expected: PASS.

- [ ] **Step 13: Register the operation**

In `modules/vision/apps.py::ready()`:

```python
        from core.inference.operations import IMG2IMG, INPAINT, TXT2IMG, register_operation
        ...
        register_operation(TXT2IMG)
        register_operation(IMG2IMG)
        register_operation(INPAINT)
```

Extend the Task 2 assertion in `modules/vision/tests/test_apps.py` to include `"inpaint"`, and the `supported_operations` assertion in `modules/vision/tests/test_comfyui_engine.py` likewise.

- [ ] **Step 14: Run those**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_apps.py modules/vision/tests/test_comfyui_engine.py -q`
Expected: PASS.

- [ ] **Step 15: Document it**

In `modules/vision/README.md`, update the modes sentence written in Task 2 to name three modes, and in the "Deferred" section remove inpaint from the operations bullet. Add, in the storage-layout section:

```markdown
An operation may declare more than one file param — inpaint takes an image and a mask —
and `submit_job` stores each under its own `inputs/<param_key>-<basename>` name while
`ImageGenerator.submit` transfers all of them before the graph is built. Nothing about
two files differs from one at any layer.
```

- [ ] **Step 16: Run both suite orders and commit**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest -q`
Expected: PASS.

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules console scripts -q`
Expected: PASS with the same counts.

```bash
git add core/inference/operations.py core/inference/engines/ modules/vision/apps.py \
        modules/vision/tests/ modules/vision/README.md
git commit -m "feat(vision): inpainting, and numeric node ordering for multi-output graphs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 7: Asset parameters get a widget — and the combo shape the live engine actually sends (B1, B6, M3)

**Depends on:** Task 6 (order only — nothing in Task 7 imports from it).

D7 shipped `Asset`, `list_assets`, and the `"asset"` param kind as a seam and deliberately left the UI to "the first operation that uses one". That operation is next. This task makes an `"asset"` param validate, render, and be filled from the engine's own list. Two things ride with it because they are the same code path: the adapter must read the `["COMBO", {"options": [...]}]` spec live ComfyUI 0.33.0 sends for `UpscaleModelLoader` (without it, Task 9's upscale form can never be filled — see Step 13), and, since this adds a third `/object_info` lookup per render to a page that already did two serially, those lookups are memoized so the page gets faster rather than slower.

**Files:**
- Modify: `core/inference/operations.py` (`validate_params` asset branch)
- Modify: `modules/vision/forms.py` (`_field_for` asset branch, replacing the raise)
- Modify: `modules/vision/views.py` (`live_options` answers asset params too)
- Modify: `core/inference/engines/comfyui.py` (`_combo_values` reads BOTH live combo shapes (M3); `_object_info` memo; `clear_object_info_cache`)
- Modify: `modules/vision/tests/_helpers.py` (`object_info(..., combo_shape)`, `FakeComfyUI.combo_shape` defaulting to the modern shape, `reset_engine_caches()`)
- Test: `modules/vision/tests/test_operations.py`, `test_forms.py`, `test_views_create.py`, `test_comfyui_engine.py`
- Docs: `modules/vision/README.md`, `docs/adr/0012-image-generation-engine-adapter.md`

**Interfaces:**
- Produces:
  - `validate_params` returns a `list[str]` for a `multiple=True` asset param and a `str | None` for a single one; asset ids stay OPAQUE (never basenamed, never case-folded).
  - `forms._field_for` renders `MultipleChoiceField`/`ChoiceField` for `"asset"`, filled from `engine_options[param.key]`.
  - `views.live_options` includes `"asset"` params, mapping `param.asset_kind` through `InferenceEngine.list_assets`.
  - `comfyui._combo_values` parses `[[...], {...}]` AND `["COMBO", {"options": [...]}]`.
  - `comfyui.clear_object_info_cache() -> None`; `comfyui._OBJECT_INFO_TTL` (module constant).
  - `_helpers.object_info(node, inputs, combo_shape="v3")` and `FakeComfyUI.combo_shape`.
  - `_helpers.reset_engine_caches() -> None` — the body every adapter-touching test module's autouse fixture calls.
- Consumes: `core.inference.engines.base.Asset`, `ComfyUIEngine.list_assets` (shipped).

- [ ] **Step 1: Write the failing validation tests**

Append to `modules/vision/tests/test_operations.py`:

```python
class TestAssetParams:
    OPERATION = Operation(
        key="assets", label="Assets", capability="image-generation", output_media="image/png",
        params=(
            Param("loras", "asset", "LoRAs", asset_kind="lora", multiple=True, default=()),
            Param("upscale_model", "asset", "Upscale model", asset_kind="upscale_model", required=True),
        ),
    )

    def test_a_multiple_asset_param_cleans_to_a_list_of_strings(self):
        clean = validate_params(
            self.OPERATION,
            {"loras": ["a.safetensors", "b.safetensors"], "upscale_model": "4x.pth"},
        )
        assert clean["loras"] == ["a.safetensors", "b.safetensors"]

    def test_a_single_asset_param_cleans_to_one_string(self):
        clean = validate_params(self.OPERATION, {"upscale_model": "4x.pth"})
        assert clean["upscale_model"] == "4x.pth"

    def test_asset_ids_stay_opaque(self):
        """An asset id is the engine's own string -- a Windows host reports
        a backslash subfolder, and it must go back exactly as it came."""
        clean = validate_params(
            self.OPERATION,
            {"loras": [r"style\\detail.safetensors"], "upscale_model": "4x.pth"},
        )
        assert clean["loras"] == [r"style\\detail.safetensors"]

    def test_a_blank_multiple_asset_param_cleans_to_an_empty_list(self):
        clean = validate_params(self.OPERATION, {"upscale_model": "4x.pth"})
        assert clean["loras"] == []

    def test_blank_entries_are_dropped_rather_than_submitted(self):
        clean = validate_params(
            self.OPERATION, {"loras": ["a.safetensors", "", "  "], "upscale_model": "4x.pth"}
        )
        assert clean["loras"] == ["a.safetensors"]

    def test_a_required_asset_param_is_required(self):
        with pytest.raises(ParamError) as exc:
            validate_params(self.OPERATION, {"loras": []})
        assert "upscale_model" in exc.value.errors

    def test_an_empty_list_for_a_required_asset_param_is_also_missing(self):
        with pytest.raises(ParamError) as exc:
            validate_params(self.OPERATION, {"upscale_model": []})
        assert "upscale_model" in exc.value.errors

    def test_the_cleaned_params_stay_json_safe(self):
        clean = validate_params(self.OPERATION, {"loras": ["a.safetensors"], "upscale_model": "4x.pth"})
        assert json.loads(json.dumps(clean)) == clean
```

(add `import json` to that module if it is not already imported.)

- [ ] **Step 2: Run them to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_operations.py -q`
Expected: FAIL — a `multiple` asset param currently falls through to the generic branch and comes back as `"['a.safetensors', 'b.safetensors']"`, a string.

- [ ] **Step 3: Add the asset branch to `validate_params`**

In `core/inference/operations.py::validate_params`, inside the `if blank:` block, before `clean[param.key] = param.default`:

```python
            if param.kind == "asset":
                # An unanswered asset param is "none chosen", which for a
                # multi-select is an empty LIST -- not the `None` a
                # scalar default would put in a JSONField for a value the
                # graph template iterates.
                clean[param.key] = [] if param.multiple else None
                continue
```

and add the kind branch beside the `"file"` one:

```python
        if param.kind == "asset":
            # An asset id is the ENGINE's own opaque string (a Windows host
            # reports `subdir\file.safetensors`): stripped of surrounding
            # whitespace and otherwise passed through untouched. Never
            # basenamed the way a `"file"` param's upload name is -- that
            # separator is part of the id, not a path we own.
            supplied_values = supplied if isinstance(supplied, (list, tuple)) else [supplied]
            chosen = [str(value).strip() for value in supplied_values if str(value).strip()]
            if not chosen and param.required:
                errors[param.key] = f"{param.label} is required."
                continue
            clean[param.key] = chosen if param.multiple else (chosen[0] if chosen else None)
            continue
```

Note that an empty list is not "blank" by the existing check (`supplied is None or a blank string`), so it reaches this branch and the `required` test above is what refuses it.

- [ ] **Step 4: Run them to verify they pass**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_operations.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing widget tests**

In `modules/vision/tests/test_forms.py`, REPLACE `test_an_asset_param_is_refused_until_its_ui_ships` (the D7 deferral is over) with:

```python
class TestAssetFields:
    MULTI = Param("loras", "asset", "LoRAs", asset_kind="lora", multiple=True, default=())
    SINGLE = Param("upscale_model", "asset", "Upscale model", asset_kind="upscale_model", required=True)

    def _operation(self, *params):
        return Operation(
            key="assets", label="Assets", capability="image-generation",
            output_media="image/png", params=params,
        )

    def test_a_multiple_asset_param_renders_a_multi_select_of_the_engines_own_list(self):
        form = build_form(
            self._operation(self.MULTI),
            {"loras": ("a.safetensors", "b.safetensors")},
        )
        field = form.fields["loras"]
        assert isinstance(field, django_forms.MultipleChoiceField)
        assert field.choices == [("a.safetensors", "a.safetensors"), ("b.safetensors", "b.safetensors")]
        assert field.required is False

    def test_a_single_required_asset_param_renders_a_required_select(self):
        form = build_form(self._operation(self.SINGLE), {"upscale_model": ("4x.pth",)})
        field = form.fields["upscale_model"]
        assert isinstance(field, django_forms.ChoiceField)
        assert not isinstance(field, django_forms.MultipleChoiceField)
        assert field.required is True

    def test_with_nothing_reported_a_required_asset_refuses_at_the_field(self):
        """THE RULING (S4): the form keeps the field required and carries
        the explanation on the field itself, so the refusal lands where the
        operator is looking -- never a form that says "optional" and a 400
        that says "required". `validate_params` still refuses it too; that
        is the schema floor a non-form caller (the chatbot tool) lands on."""
        form = build_form(self._operation(self.SINGLE), {})
        field = form.fields["upscale_model"]
        assert field.choices == []
        assert field.required is True
        assert "no upscale model" in field.help_text.lower()
        assert "cannot run" in field.help_text.lower()

    def test_the_message_names_both_causes_and_never_asserts_an_engine_said_so(self):
        """R2-3: an empty option list means EITHER a reachable engine with
        nothing installed OR no model assigned at all -- `live_options`
        returns `{}` for both, and this layer cannot tell them apart. The
        copy must not claim the engine reported anything."""
        help_text = build_form(self._operation(self.SINGLE), {}).fields["upscale_model"].help_text
        assert "engine has none installed" in help_text
        assert "no image model is assigned yet" in help_text
        assert reverse("setup-index") in help_text
        assert reverse("inference-console") in help_text
        assert "reports no" not in help_text

    def test_that_refusal_is_the_message_a_submission_gets_back(self):
        form = build_form(self._operation(self.SINGLE), {}, data={})
        assert form.is_valid() is False
        assert "no upscale model" in " ".join(form.errors["upscale_model"]).lower()

    def test_an_optional_multi_asset_with_nothing_reported_is_simply_empty(self):
        """"No LoRAs" is a normal answer, so nothing is refused here."""
        form = build_form(self._operation(self.MULTI), {})
        assert form.fields["loras"].required is False

    def test_a_bound_form_refuses_an_asset_the_engine_never_reported(self):
        form = build_form(
            self._operation(self.SINGLE), {"upscale_model": ("4x.pth",)},
            data={"upscale_model": "made-up.pth"},
        )
        assert form.is_valid() is False
        assert "upscale_model" in form.errors

    def test_a_bound_multi_select_cleans_to_a_list(self):
        form = build_form(
            self._operation(self.MULTI), {"loras": ("a.safetensors", "b.safetensors")},
            data={"loras": ["a.safetensors", "b.safetensors"]},
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["loras"] == ["a.safetensors", "b.safetensors"]
```

- [ ] **Step 6: Run them to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_forms.py -q`
Expected: FAIL — `ValueError: Param kind 'asset' ('loras') has no form field yet`.

- [ ] **Step 7: Render asset fields**

In `modules/vision/forms.py`, replace the trailing `raise ValueError(...)` in `_field_for` with the asset branch (and keep a raise for any kind that genuinely has no field):

```python
    if param.kind == "asset":
        # Options come from the ENGINE (`InferenceEngine.list_assets`), the
        # same way a live `"choice"` param's do -- this platform never
        # ships a list of LoRAs or upscalers, it reports what the operator
        # placed. A required asset with nothing reported degrades to an
        # OPTIONAL empty field carrying an honest sentence: an operator
        # cannot pick a file the engine does not have, and blocking the
        # whole form behind a dropdown with no entries explains nothing.
        options = engine_options.get(param.key, ())
        field_class = forms.MultipleChoiceField if param.multiple else forms.ChoiceField
        if not options:
            # THE RULING (S4): nothing to choose from. For an OPTIONAL
            # asset ("no LoRAs" is a normal answer) that is simply an empty
            # field. For a REQUIRED one the generation genuinely cannot
            # run, so the field stays required and carries the reason --
            # the refusal belongs on the field the operator is looking at,
            # never in a 400 about a field the form called optional.
            # `validate_params` refuses it as well; that is the schema
            # floor a caller with no form (the chatbot tool) lands on.
            missing = (
                f"No {param.label.lower()} is available — the image engine has none "
                f"installed, or no image model is assigned yet. See "
                f"{reverse('setup-index')} and {reverse('inference-console')}, then reload "
                "this page. This generation cannot run without one."
            )
            return field_class(
                label=param.label,
                choices=[],
                required=param.required and not param.multiple,
                help_text=missing,
                error_messages={"required": missing},
            )
        return field_class(
            label=param.label,
            choices=[(value, value) for value in options],
            required=param.required and not param.multiple,
        )
    raise ValueError(
        f"Param kind {param.kind!r} ({param.key!r}) has no form field yet."
    )
```

(`required=param.required and not param.multiple`: a multi-select of adornments is never required — "no LoRAs" is a normal answer — while a single required asset like an upscaler is. Add `from django.urls import reverse` to `forms.py`, and to `modules/vision/tests/test_forms.py` for the assertions above — a URL is never hardcoded in either.)

**Why the message names TWO causes and points at both pages.** An empty option list has two possible meanings and this layer cannot tell them apart: `views.live_options` returns `{}` outright when the role is UNBOUND (`if resolved is None: return {}`) and also when a reachable engine simply reports nothing. Saying "the image engine reports no upscale model" for an unbound role would report a fact no engine ever stated — the same "wrong problem, confidently" defect Task 2 removes for B10, reintroduced at the form layer. So the copy names both causes and points at `/setup/` (how to install and where the files go) and `/inference/` (where a model is assigned).

Note what this deliberately does NOT do: it does not hoist the `check.ready` 503 above `form.is_valid()` in `views.generate`. That would make every malformed submission report 503 again, which is exactly the B10 defect Task 2 just fixed. An unbound role with a VALID submission still reaches `submit_job` and still gets the honest 503; this message is only what an operator sees on the field itself.

**Why it points at `/setup/` rather than naming the directory.** `ComfyUI/models/upscale_models/` is engine knowledge, and spec §3 keeps engine vocabulary out of this layer — `modules/vision/forms.py` never learns which engine is bound. `/setup/` renders the bound adapter's own `SetupGuide.models_note`, which names the real directory (Task 8 adds `models/upscale_models/` to it), so the operator gets the exact path from the one place that legitimately knows it, and a second adapter needs no new copy here.

- [ ] **Step 8: Run the form tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_forms.py -q`
Expected: PASS.

- [ ] **Step 9: Write the failing `live_options` test**

Append to `modules/vision/tests/test_views_create.py`:

```python
@pytest.mark.django_db
class TestLiveAssetOptions:
    OPERATION = Operation(
        key="assets", label="Assets", capability="image-generation", output_media="image/png",
        params=(
            Param("prompt", "text", "Prompt", default="", required=True),
            Param("loras", "asset", "LoRAs", asset_kind="lora", multiple=True, default=()),
        ),
    )

    def _asset_engine(self, assets=(), blow_up=False):
        engine = StubEngine()
        engine.list_choices = lambda endpoint, key: ()

        def list_assets(endpoint, kind):
            if blow_up:
                raise RuntimeError("boom")
            return [Asset(kind=kind, asset_id=asset_id) for asset_id in assets]

        engine.list_assets = list_assets
        return patch.dict(ENGINES, {"stubengine": engine})

    def test_the_page_offers_the_engines_own_assets(self, client):
        _bind()
        with patch.dict(operations._OPERATIONS, {"assets": self.OPERATION}), \
             self._asset_engine(assets=("style.safetensors",)):
            body = client.get(reverse("vision-create-operation", args=["assets"])).content.decode()

        assert '<option value="style.safetensors"' in body
        assert 'name="loras"' in body

    def test_an_engine_that_cannot_list_assets_never_500s_the_page(self, client):
        _bind()
        with patch.dict(operations._OPERATIONS, {"assets": self.OPERATION}), \
             self._asset_engine(blow_up=True):
            response = client.get(reverse("vision-create-operation", args=["assets"]))

        assert response.status_code == 200
        assert "reports no loras" in response.content.decode().lower()

    def test_an_engine_with_no_list_assets_member_is_not_an_error(self, client):
        """Every optional protocol member is read with `getattr` -- an
        adapter that predates assets must still serve the page."""
        _bind()
        engine = StubEngine()
        engine.list_choices = lambda endpoint, key: ()
        with patch.dict(operations._OPERATIONS, {"assets": self.OPERATION}), \
             patch.dict(ENGINES, {"stubengine": engine}):
            assert client.get(reverse("vision-create-operation", args=["assets"])).status_code == 200
```

- [ ] **Step 10: Run it to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_views_create.py -q`
Expected: FAIL — no `<option>` is rendered; `live_options` never asks for assets.

- [ ] **Step 11: Teach `live_options` about assets**

Replace `modules/vision/views.py::live_options` with:

```python
def live_options(operation: Operation, resolved) -> dict[str, tuple[str, ...]]:
    """Engine-reported options for every param of `operation` whose options
    the ENGINE owns: `"choice"` params with no fixed `choices` (samplers,
    schedulers) and every `"asset"` param (LoRAs, upscalers, VAEs).

    Returns `{}` when the role is unbound or the engine cannot be reached;
    a single param that fails reports `()` and the form degrades for that
    field alone. Never raises -- an option list is not worth a 500, and
    every optional protocol member is read with `getattr` so an adapter
    that predates assets simply reports nothing.
    """
    if resolved is None:
        return {}
    try:
        engine = get_engine(resolved.engine)
    except Exception:  # noqa: BLE001 -- an engine name that isn't registered is not a 500
        logger.debug("get_engine(%r) failed", resolved.engine, exc_info=True)
        return {}

    list_choices = getattr(engine, "list_choices", None)
    list_assets = getattr(engine, "list_assets", None)

    def safe(call, key: str) -> tuple[str, ...]:
        try:
            return tuple(call())
        except Exception:  # noqa: BLE001 -- never 500 over an option list
            logger.debug("Listing options for %r failed", key, exc_info=True)
            return ()

    options: dict[str, tuple[str, ...]] = {}
    for param in operation.params:
        if param.kind == "choice" and not param.choices and list_choices is not None:
            options[param.key] = safe(
                lambda p=param: list_choices(resolved.endpoint, p.key), param.key
            )
        elif param.kind == "asset" and list_assets is not None:
            options[param.key] = safe(
                lambda p=param: [asset.asset_id for asset in list_assets(resolved.endpoint, p.asset_kind)],
                param.key,
            )
    return options
```

- [ ] **Step 12: Run it to verify it passes**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_views_create.py -q`
Expected: PASS.

- [ ] **Step 13: Write the failing combo-shape and round-trip tests (M3, B6)**

**The combo shape first — this is the finding that would otherwise make Task 9's upscale form unfillable in the real world.** Live ComfyUI `0.33.0` (confirmed via `/system_stats`) answers `curl http://localhost:8188/object_info/UpscaleModelLoader` with:

```json
{"UpscaleModelLoader": {"input": {"required": {"model_name": ["COMBO", {"multiselect": false, "options": []}]}}}}
```

The shipped `_combo_values` (`core/inference/engines/comfyui.py:82-94`) reads `spec[0]` and returns `()` unless it is a list — and here `spec[0]` is the **string** `"COMBO"`, with the real values at `spec[1]["options"]`. So `list_assets(endpoint, "upscale_model")` returns `[]` no matter how many `.pth` files the operator placed. This is a per-node schema migration inside 0.33.0, not a "nothing installed" artefact: the same server still answers the LEGACY shape for other nodes — `CheckpointLoaderSimple.ckpt_name` is `[["sd_xl_base_1.0.safetensors"], {...}]` and `LoraLoader.lora_name` is `[[], {...}]`. Both shapes are live at once, so the parser must read both, and `FakeComfyUI` must emit the MODERN one by default — a double that only ever speaks the legacy shape lets a green suite hide a form the live server can never fill.

Append to `modules/vision/tests/test_comfyui_engine.py`:

```python
class TestComboShapes:
    """ComfyUI 0.33.0 serves two combo shapes at once: the legacy
    `[[...values...], {...}]` (CheckpointLoaderSimple, LoraLoader) and the
    newer `["COMBO", {"multiselect": false, "options": [...]}]`
    (UpscaleModelLoader). The adapter reads both, and the double defaults
    to the newer one so a green suite cannot hide an unfillable form."""

    def test_a_combo_spec_is_read_from_its_options_list(self):
        fake = FakeComfyUI(upscalers=("4x-ultrasharp.pth",))
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assets = ComfyUIEngine().list_assets(ENDPOINT, "upscale_model")
        assert [asset.asset_id for asset in assets] == ["4x-ultrasharp.pth"]

    def test_the_legacy_shape_is_still_read(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), combo_shape="legacy")
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)
        assert [model.model_id for model in installed] == ["sdxl.safetensors"]

    def test_an_empty_combo_reports_nothing_rather_than_raising(self):
        fake = FakeComfyUI(upscalers=())
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().list_assets(ENDPOINT, "upscale_model") == []

    def test_a_typed_input_is_still_not_a_combo(self):
        """`KSampler.seed` is `["INT", {...}]` -- a two-element list whose
        first entry is a string, exactly like a COMBO spec. It must not be
        mistaken for one.

        Calls the PARSER directly: `list_choices` short-circuits on
        `_CHOICE_INPUTS` (which holds only "sampler" and "scheduler") and
        returns `()` without issuing a request at all, so going through it
        would pin the lookup table rather than the guard under test."""
        fake = FakeComfyUI()
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert comfyui._combo_values(ENDPOINT, "KSampler", "seed") == ()
```

(`modules/vision/tests/test_comfyui_engine.py` currently imports only `ComfyUIEngine` from the adapter — `from core.inference.engines.comfyui import ComfyUIEngine`. Both the class above and the memo tests below reach the MODULE (`comfyui._combo_values`, `patch.object(comfyui, "_OBJECT_INFO_TTL", -1)`) and raise `httpx.HTTPError`, so add these two imports to that module once, at the top, alongside the existing ones:

```python
import httpx

from core.inference.engines import comfyui
```

`patch`, `pytest`, `ENDPOINT`, and `FakeComfyUI` are already there.)

Then the memo tests:

```python
class TestObjectInfoMemo:
    """B6: one page render asks for a sampler list, a scheduler list, and
    every asset param's list -- serially, each at DISCOVERY_TIMEOUT. The
    first two are the SAME node. A short-lived memo collapses the burst
    without pretending the engine's model folders never change."""

    def test_two_lookups_of_the_same_node_cost_one_request(self):
        fake = FakeComfyUI()
        calls = []

        def counting_get(url, params=None, timeout=None):
            calls.append(url)
            return fake.get(url, params=params, timeout=timeout)

        with patch("core.inference.engines.comfyui.httpx.get", counting_get):
            engine = ComfyUIEngine()
            assert engine.list_choices(ENDPOINT, "sampler") == ("euler", "dpmpp_2m")
            assert engine.list_choices(ENDPOINT, "scheduler") == ("normal", "karras")

        assert len(calls) == 1

    def test_different_nodes_are_still_fetched_separately(self):
        fake = FakeComfyUI(loras=("style.safetensors",))
        calls = []

        def counting_get(url, params=None, timeout=None):
            calls.append(url)
            return fake.get(url, params=params, timeout=timeout)

        with patch("core.inference.engines.comfyui.httpx.get", counting_get):
            engine = ComfyUIEngine()
            engine.list_choices(ENDPOINT, "sampler")
            engine.list_assets(ENDPOINT, "lora")

        assert len(calls) == 2

    def test_the_memo_expires_so_a_newly_placed_file_appears(self):
        fake = FakeComfyUI(loras=())
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            engine = ComfyUIEngine()
            assert engine.list_assets(ENDPOINT, "lora") == []
            fake.loras = ("style.safetensors",)
            with patch.object(comfyui, "_OBJECT_INFO_TTL", -1):
                assert [asset.asset_id for asset in engine.list_assets(ENDPOINT, "lora")] == [
                    "style.safetensors"
                ]

    def test_a_failed_lookup_is_not_remembered(self):
        """Caching an outage would make a recovered engine look down for
        as long as the TTL."""
        fake = FakeComfyUI(healthy=False)

        def failing_get(url, params=None, timeout=None):
            raise httpx.ConnectError("connection refused")

        with patch("core.inference.engines.comfyui.httpx.get", failing_get):
            with pytest.raises(httpx.HTTPError):
                ComfyUIEngine().list_choices(ENDPOINT, "sampler")

        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().list_choices(ENDPOINT, "sampler") == ("euler", "dpmpp_2m")
```

Add to that module (and to every other test module that patches `comfyui.httpx`) an autouse fixture delegating to the shared body:

```python
@pytest.fixture(autouse=True)
def _reset_engine_caches():
    reset_engine_caches()
    yield
    reset_engine_caches()
```

and in `modules/vision/tests/_helpers.py`:

```python
def reset_engine_caches() -> None:
    """Body of the `_reset_engine_caches` autouse fixture every test module
    that patches `comfyui.httpx` uses.

    The adapter memoizes `/object_info` for a few seconds (B6), and a memo
    that survives between tests is exactly the kind of state that makes a
    suite pass in one order and fail in the other. Cleared before AND after
    each test, so a module that forgets the fixture is the only thing that
    can leak.
    """
    from core.inference.engines.comfyui import clear_object_info_cache

    clear_object_info_cache()
```

- [ ] **Step 14: Run them to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_comfyui_engine.py -q`
Expected: FAIL twice over — `ImportError: cannot import name 'clear_object_info_cache'` once the memo tests are collected, and (after stubbing that import out, or on the next run) `TestComboShapes::test_a_combo_spec_is_read_from_its_options_list` returning `[]` because `spec[0]` is the string `"COMBO"`. Both are the point; do not fix either yet.

- [ ] **Step 15: Memoize `/object_info`**

In `core/inference/engines/comfyui.py`, add above `_combo_values`:

```python
# How long a `/object_info/<node>` body is reused. One page render asks for
# a sampler list, a scheduler list (the SAME node), and one list per asset
# param -- serially, each at DISCOVERY_TIMEOUT. A few seconds collapses that
# burst into one request per node while staying far shorter than the time
# it takes an operator to drop a new file in and reload: what the page
# shows is still what the engine has.
_OBJECT_INFO_TTL = 5.0

# (endpoint, node) -> (monotonic timestamp, parsed body). Module-level, and
# deliberately the ONLY state in this adapter: engines stay stateless about
# BINDINGS (endpoint and model are passed per call, never held), which this
# does not change. Tests clear it via `clear_object_info_cache`.
_OBJECT_INFO_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}


def clear_object_info_cache() -> None:
    """Forget every memoized `/object_info` body. For tests, and for any
    caller that must see the engine's current state immediately."""
    _OBJECT_INFO_CACHE.clear()


def _object_info(endpoint: str, node: str, timeout: float | None = None) -> dict:
    """`/object_info/<node>`, memoized for `_OBJECT_INFO_TTL` seconds.

    A FAILURE is never cached: caching an outage would keep a recovered
    engine looking down for the rest of the TTL, and the honest report is
    the one the next call makes.
    """
    key = (endpoint, node)
    now = time.monotonic()
    cached = _OBJECT_INFO_CACHE.get(key)
    if cached is not None and now - cached[0] < _OBJECT_INFO_TTL:
        return cached[1]
    response = httpx.get(
        f"{endpoint}/object_info/{node}",
        timeout=DISCOVERY_TIMEOUT if timeout is None else timeout,
    )
    response.raise_for_status()
    body = response.json() or {}
    _OBJECT_INFO_CACHE[key] = (now, body)
    return body
```

and reduce `_combo_values` to parsing — reading BOTH combo shapes ComfyUI 0.33.0 serves (M3):

```python
def _combo_values(endpoint: str, node: str, input_key: str, timeout: float | None = None) -> tuple[str, ...]:
    """The allowed values of one node input, from `/object_info/<node>`.

    ComfyUI serves two combo shapes at once, and a live 0.33.0 server
    answers with each of them depending on the node:

    - legacy -- `[[...values...], {...options...}]`, still what
      `CheckpointLoaderSimple.ckpt_name` and `LoraLoader.lora_name` return;
    - newer  -- `["COMBO", {"multiselect": false, "options": [...]}]`, what
      `UpscaleModelLoader.model_name` returns.

    Reading only the first would report NOTHING for an upscaler the
    operator really installed -- a form that can never be filled, with no
    error to explain it. Anything that is neither (a typed input like
    `["INT", {...}]`, a node this build doesn't have) yields `()` rather
    than raising: an absent optional node is not an error, an unreachable
    server is (and propagates).
    """
    spec = (
        _object_info(endpoint, node, timeout)
        .get(node, {})
        .get("input", {})
        .get("required", {})
        .get(input_key)
    )
    if not spec or not isinstance(spec, list):
        return ()
    values = spec[0]
    if values == "COMBO":
        options = spec[1] if len(spec) > 1 else None
        values = options.get("options") if isinstance(options, dict) else None
    if not isinstance(values, list):
        return ()
    return tuple(str(value) for value in values)
```

Add `import time` to the module's imports.

Then teach the double both shapes, defaulting to the newer one. In `modules/vision/tests/_helpers.py`, replace `object_info` and add the switch to `FakeComfyUI`:

```python
def object_info(node: str, inputs: dict[str, Any], combo_shape: str = "v3") -> dict:
    """A `/object_info/<node>` body in ComfyUI's real shape.

    Every required input maps to `[<spec>, <options dict>]`. A COMBO
    input's spec has TWO live forms in ComfyUI 0.33.0 and this double can
    emit either:

    - `"v3"` (default) -- `["COMBO", {"multiselect": False, "options":
      [...]}]`, what `UpscaleModelLoader.model_name` really returns today;
    - `"legacy"` -- `[[...values...], {...}]`, what
      `CheckpointLoaderSimple.ckpt_name` and `LoraLoader.lora_name` still
      return.

    The default is the NEWER shape on purpose: a double that only speaks
    the legacy form lets the suite stay green while the live server hands
    the adapter something it cannot parse.
    """
    rendered = {}
    for input_key, spec in inputs.items():
        if isinstance(spec, list) and isinstance(spec[0], list) and combo_shape == "v3":
            rendered[input_key] = ["COMBO", {"multiselect": False, "options": list(spec[0])}]
        else:
            rendered[input_key] = spec
    return {node: {"input": {"required": rendered}}}
```

and on `FakeComfyUI`, add the field and thread it through both `/object_info` branches:

```python
    # Which combo shape this server speaks -- "v3" (default, what live
    # ComfyUI 0.33.0 returns for UpscaleModelLoader) or "legacy".
    combo_shape: str = "v3"
```

```python
            if node == "KSampler":
                return _Response(
                    payload=object_info(
                        "KSampler",
                        {
                            "seed": ["INT", {"default": 0}],
                            "sampler_name": [list(self.samplers), {"default": self.samplers[0]}],
                            "scheduler": [list(self.schedulers), {"default": self.schedulers[0]}],
                        },
                        self.combo_shape,
                    )
                )
            if node in self._NODE_INPUTS:
                attribute, input_key = self._NODE_INPUTS[node]
                values = list(getattr(self, attribute))
                return _Response(
                    payload=object_info(node, {input_key: [values, {"tooltip": "x"}]}, self.combo_shape)
                )
```

Note that `["INT", {"default": 0}]` is passed through unchanged by that rendering (its `spec[0]` is a string, not a list), which is exactly the case `test_a_typed_input_is_still_not_a_combo` pins — and it pins it by calling `comfyui._combo_values` directly, because `list_choices` never reaches the parser for a key outside `_CHOICE_INPUTS`.

- [ ] **Step 16: Run it to verify it passes**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_comfyui_engine.py -q`
Expected: PASS.

Then run every module that patches the adapter's HTTP, to catch a missing cache-reset fixture:

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision -q`
Expected: PASS. Run it a second time with `-p no:randomly` off/on if the repo uses ordering plugins; a test that passes alone and fails in the module run is a missing `_reset_engine_caches` fixture, not a flake.

- [ ] **Step 17: Document the widget and retire the ADR caveat**

In `modules/vision/README.md`, in the operation-adding list, add a step 6:

```markdown
6. An `"asset"` param (a LoRA, a VAE, an upscaler) needs nothing else either.
   `validate_params` cleans it to a JSON-safe list (or one string) of the engine's own
   opaque ids; `forms.build_form` renders a multi-select (or a select) filled from
   `views.live_options`, which reads `InferenceEngine.list_assets` for the param's
   `asset_kind`; an engine reporting none says so in the field's help text rather than
   offering an empty dropdown. The adapter memoizes `/object_info` for a few seconds so
   a page with several such params costs one request per loader node, not one per param.
```

The ADR states the D7 deferral in TWO places, and both become false in this task. First, in `docs/adr/0012-image-generation-engine-adapter.md`, in the **D7 row of the decision table** (`:49`), replace the parenthetical "(`modules/vision/forms.py` raises rather than silently rendering nothing for a `"asset"`-kind param today)" with:

```markdown
(`modules/vision/forms.py::_field_for` renders an asset param as a select
filled from `InferenceEngine.list_assets`; an asset kind an adapter cannot
list reports nothing rather than raising)
```

Second, in the Consequences' "Two caveats stand" paragraph, replace the first caveat with:

```markdown
  One caveat stands, and it is the honest limit of that promise: a
  `"choice"` param whose options an engine reports live needs a mapping in
  that engine's adapter (`comfyui.py::_CHOICE_INPUTS`) before the form can
  offer them. The `"asset"`-kind caveat is retired — D7's deferred widget
  shipped with the LoRA/upscale work: `modules/vision/forms.py::_field_for`
  renders an asset param as a select filled from
  `InferenceEngine.list_assets`, and an asset KIND an adapter cannot list
  reports nothing rather than raising (`comfyui.py::_ASSET_NODES`, where
  `"embedding"` is deliberately absent).
```

- [ ] **Step 18: Run both suite orders and commit**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest -q`
Expected: PASS.

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules console scripts -q`
Expected: PASS with the same counts — a difference between the orders is the `/object_info` memo leaking; find the module missing `_reset_engine_caches`.

```bash
git add core/inference/operations.py core/inference/engines/comfyui.py modules/vision/forms.py \
        modules/vision/views.py modules/vision/tests/ modules/vision/README.md \
        docs/adr/0012-image-generation-engine-adapter.md
git commit -m "feat(vision): asset parameters render, validate, and cost one lookup

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 8: LoRA — assets adorn every checkpoint mode at once

**Depends on:** Task 7 — `LORA_PARAMS` are `"asset"` params, which neither render nor validate before it.

LoRA is not a mode (D7: assets adorn a job, they do not answer a role), so this task registers no operation. It adds two params to the three checkpoint-based operations and puts the loader chain inside the shared `checkpoint` fragment — so txt2img, img2img, and inpaint all gain it in one place, and any future checkpoint mode is born with it.

**Files:**
- Modify: `core/inference/engines/comfyui_workflows/_fragments.py` (`checkpoint` takes `params` and applies the chain)
- Modify: `core/inference/engines/comfyui_workflows/txt2img.py`, `img2img.py`, `inpaint.py` (one line each)
- Modify: `core/inference/operations.py` (`LORA_PARAMS` on the three operations)
- Modify: `modules/vision/models.py` (`facts` renders a list value)
- Modify: `core/inference/engines/comfyui.py` (`SetupGuide.models_note` names `models/loras/` — verify it already does — and `models/upscale_models/`)
- Test: `modules/vision/tests/test_comfyui_workflows.py`, `test_operations.py`, `test_models.py`, `test_setup_guides.py`
- Docs: `modules/vision/README.md`

**Interfaces:**
- Produces: `_fragments.checkpoint(graph, model_id, params) -> Checkpoint` (SIGNATURE CHANGE — three template call sites in the package and five in `TestFragments`, all updated in this task; `params` deliberately gets NO default, because a default would let a future template silently skip the LoRA chain, which is the drift this fragment exists to prevent); `operations.LORA_PARAMS: tuple[Param, ...]` = (`loras` asset/multiple, `lora_strength` float); `GenerationJob.facts` renders a list as `", ".join`.
- Consumes: the asset validation and widget from Task 7.

- [ ] **Step 1: Write the failing fragment test**

Append to `modules/vision/tests/test_comfyui_workflows.py`:

```python
class TestLoraChain:
    """One LoRA loader per selected file, chained MODEL->MODEL and
    CLIP->CLIP, between the checkpoint and everything downstream -- so the
    prompt encoders see the adorned CLIP, which is the whole point."""

    def test_no_loras_adds_no_nodes(self):
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(graph, "sdxl.safetensors", {"loras": []})
        assert list(graph.as_dict()) == ["1"]
        assert (ckpt.model, ckpt.clip, ckpt.vae) == (["1", 0], ["1", 1], ["1", 2])

    def test_an_operation_with_no_lora_params_at_all_adds_no_nodes(self):
        graph = _fragments.Graph()
        _fragments.checkpoint(graph, "sdxl.safetensors", {})
        assert list(graph.as_dict()) == ["1"]

    def test_one_lora_wires_model_and_clip_through_it(self):
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(
            graph, "sdxl.safetensors", {"loras": ["style.safetensors"], "lora_strength": 0.8}
        )
        assert graph.as_dict()["2"] == {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "style.safetensors", "strength_model": 0.8,
                       "strength_clip": 0.8, "model": ["1", 0], "clip": ["1", 1]},
        }
        assert (ckpt.model, ckpt.clip) == (["2", 0], ["2", 1])

    def test_the_vae_still_comes_from_the_checkpoint(self):
        """`LoraLoader` outputs MODEL and CLIP only -- a graph that took a
        VAE link from it would be wired to nothing."""
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(graph, "sdxl.safetensors", {"loras": ["style.safetensors"]})
        assert ckpt.vae == ["1", 2]

    def test_several_loras_chain_in_order(self):
        graph = _fragments.Graph()
        ckpt = _fragments.checkpoint(
            graph, "sdxl.safetensors", {"loras": ["a.safetensors", "b.safetensors"]}
        )
        nodes = graph.as_dict()
        assert nodes["2"]["inputs"]["model"] == ["1", 0]
        assert nodes["3"]["inputs"]["model"] == ["2", 0]
        assert nodes["3"]["inputs"]["clip"] == ["2", 1]
        assert (ckpt.model, ckpt.clip) == (["3", 0], ["3", 1])

    def test_the_strength_defaults_to_one_when_the_operation_omits_it(self):
        graph = _fragments.Graph()
        _fragments.checkpoint(graph, "sdxl.safetensors", {"loras": ["a.safetensors"]})
        assert graph.as_dict()["2"]["inputs"]["strength_model"] == 1.0

    def test_a_single_asset_value_is_accepted_as_well_as_a_list(self):
        """`validate_params` gives a non-`multiple` asset param one string;
        a template must not have to know which shape it got."""
        graph = _fragments.Graph()
        _fragments.checkpoint(graph, "sdxl.safetensors", {"loras": "a.safetensors"})
        assert graph.as_dict()["2"]["inputs"]["lora_name"] == "a.safetensors"


class TestLorasReachEveryCheckpointMode:
    def test_txt2img_encodes_its_prompts_against_the_adorned_clip(self):
        request = GenerationRequest(
            operation="txt2img", model_id="sdxl.safetensors",
            params={**PARAMS, "loras": ["style.safetensors"], "lora_strength": 0.7},
            client_ref="job",
        )
        graph = get_template("txt2img")(request, request.model_id, {}, {})
        assert graph["2"]["class_type"] == "LoraLoader"
        assert graph["3"]["inputs"]["clip"] == ["2", 1]
        assert graph["6"]["inputs"]["model"] == ["2", 0]

    def test_img2img_does_too_with_no_edit_of_its_own(self):
        request = GenerationRequest(
            operation="img2img", model_id="sdxl.safetensors",
            params={**IMG2IMG_PARAMS, "loras": ["style.safetensors"]},
            client_ref="job",
        )
        graph = get_template("img2img")(request, request.model_id, {}, {"init_image": "job/beach.png"})
        assert graph["2"]["class_type"] == "LoraLoader"

    def test_inpaint_does_too(self):
        request = GenerationRequest(
            operation="inpaint", model_id="sdxl.safetensors",
            params={**INPAINT_PARAMS, "loras": ["style.safetensors"]},
            client_ref="job",
        )
        graph = get_template("inpaint")(
            request, request.model_id, {}, {"init_image": "job/b.png", "mask_image": "job/m.png"}
        )
        assert graph["2"]["class_type"] == "LoraLoader"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_comfyui_workflows.py -q`
Expected: FAIL — `TypeError: checkpoint() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Put the chain inside `checkpoint`**

In `core/inference/engines/comfyui_workflows/_fragments.py`, replace `checkpoint` and add the private chain builder:

```python
def _lora_chain(graph: Graph, ckpt: Checkpoint, params: dict) -> Checkpoint:
    """Chain one `LoraLoader` per selected LoRA, MODEL->MODEL and
    CLIP->CLIP, and hand back the adorned links.

    Both strengths take the operation's single `lora_strength`: the schema
    has one value per param, so a per-LoRA strength would need a param kind
    that carries options per item -- honestly out of scope, and recorded as
    such in `modules/vision/README.md` rather than faked here.

    The VAE is NOT rewired: `LoraLoader` outputs MODEL and CLIP only.
    """
    names = params.get("loras") or ()
    if isinstance(names, str):
        names = (names,)
    strength = float(params.get("lora_strength") or 1.0)
    adorned = ckpt
    for name in names:
        node = graph.add(
            "LoraLoader",
            lora_name=name,
            strength_model=strength,
            strength_clip=strength,
            model=adorned.model,
            clip=adorned.clip,
        )
        adorned = Checkpoint(model=[node, 0], clip=[node, 1], vae=ckpt.vae)
    return adorned


def checkpoint(graph: Graph, model_id: str, params: dict) -> Checkpoint:
    """Load the bound checkpoint and apply any adornments the operation's
    params carry (today: the LoRA chain).

    `model_id` is ComfyUI's own opaque string (a Windows host reports
    `subdir\\file.safetensors`) and is passed through untouched.

    Adornments live HERE rather than in each template so every
    checkpoint-based mode -- txt2img, img2img, inpaint, and whatever comes
    next -- gets them from the same three lines. A mode that declares no
    `loras` param simply gets no extra nodes.
    """
    node = graph.add("CheckpointLoaderSimple", ckpt_name=model_id)
    return _lora_chain(graph, Checkpoint(model=[node, 0], clip=[node, 1], vae=[node, 2]), params)
```

In `txt2img.py`, `img2img.py`, and `inpaint.py`, change the one line each:

```python
    ckpt = _fragments.checkpoint(graph, model_id, params)
```

(in `txt2img.py`, `params` is `request.params` — bind it to a local `params = request.params` at the top of `build`, matching the other two templates.)

The signature change also breaks five call sites in the TEST module, which Task 1 wrote against the two-argument form. In `modules/vision/tests/test_comfyui_workflows.py::TestFragments`, pass `{}` as the third argument to every `_fragments.checkpoint(graph, "sdxl.safetensors")` call — those tests assert the bare loader, and `{}` is exactly what "no adornments" looks like:

```python
        ckpt = _fragments.checkpoint(graph, "sdxl\\turbo.safetensors", {})
        ...
        ckpt = _fragments.checkpoint(graph, "sdxl.safetensors", {})
```

Find them all with `grep -n "_fragments.checkpoint(graph" modules/vision/tests/test_comfyui_workflows.py` — there are five, in `test_checkpoint_yields_the_three_links_its_loader_outputs`, `test_prompts_encode_positive_then_negative_against_the_checkpoint_clip`, `test_a_missing_prompt_encodes_as_empty_text_never_none`, `test_sample_defaults_denoise_to_one_when_the_operation_has_no_such_param`, and `test_sample_reads_denoise_from_the_params_when_declared`.

- [ ] **Step 4: Run the workflow tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_comfyui_workflows.py -q`
Expected: PASS — including the Task 1 golden graph, which has no `loras` key and therefore no extra nodes.

- [ ] **Step 5: Write the failing schema test**

Append to `modules/vision/tests/test_operations.py`:

```python
class TestLoraParams:
    def test_every_checkpoint_mode_declares_the_same_two_params(self):
        for operation in (TXT2IMG, IMG2IMG, INPAINT):
            loras = operation.param("loras")
            strength = operation.param("lora_strength")
            assert (loras.kind, loras.asset_kind, loras.multiple) == ("asset", "lora", True)
            assert (strength.kind, strength.default, strength.min, strength.max) == (
                "float", 1.0, 0.0, 2.0,
            )

    def test_they_are_optional_because_no_lora_is_a_normal_answer(self):
        clean = validate_params(TXT2IMG, {"prompt": "p", "sampler": "euler", "scheduler": "normal"})
        assert clean["loras"] == []
        assert clean["lora_strength"] == 1.0

    def test_a_chosen_lora_survives_validation_as_a_json_safe_list(self):
        clean = validate_params(
            TXT2IMG,
            {"prompt": "p", "sampler": "euler", "scheduler": "normal",
             "loras": ["style.safetensors"], "lora_strength": "0.75"},
        )
        assert clean["loras"] == ["style.safetensors"]
        assert clean["lora_strength"] == 0.75
```

- [ ] **Step 6: Run it to verify it fails, then add the params**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_operations.py -q`
Expected: FAIL — `AttributeError: 'NoneType' object has no attribute 'kind'`.

In `core/inference/operations.py`, above `TXT2IMG`:

```python
# The adornments every checkpoint-based operation shares (D7: assets are a
# second axis -- they decorate a job, they don't answer a role). Declared
# once and spliced into each operation's params, so txt2img, img2img, and
# inpaint cannot drift apart on what a LoRA control is.
#
# ONE strength for the whole selection, deliberately: a `Param` carries one
# value, so per-LoRA strengths would need a param kind that holds options
# per item. Two chained LoRAs at one strength is the common case; the
# limitation is written down in `modules/vision/README.md` rather than
# faked with a parallel list.
LORA_PARAMS = (
    # No `default`: the blank branch of `validate_params` answers an
    # unfilled asset param with `[]` (or `None`) and never reads one, so a
    # default here would be a value nothing consults.
    Param("loras", "asset", "LoRAs", asset_kind="lora", multiple=True),
    Param("lora_strength", "float", "LoRA strength", default=1.0, min=0.0, max=2.0, step=0.05),
)
```

and append `*LORA_PARAMS,` to the `params=(...)` tuple of `TXT2IMG`, `IMG2IMG`, and `INPAINT` (after `batch_size`, so the form's grid keeps the sampling controls together and the adornments last).

- [ ] **Step 7: Run it to verify it passes**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_operations.py -q`
Expected: PASS.

- [ ] **Step 8: Write the failing facts test**

Append to `modules/vision/tests/test_models.py`, inside the existing `TestJobFacts` class:

```python
    def test_a_list_valued_param_reads_as_a_list_not_a_repr(self):
        """An asset param holds a list. `str(["a", "b"])` on a card is a
        Python repr leaking onto a page."""
        job = _job(params={**TXT2IMG_PARAMS, "loras": ["style.safetensors", "detail.safetensors"]})
        assert ("LoRAs", "style.safetensors, detail.safetensors") in job.facts

    def test_an_empty_list_is_skipped_like_any_other_absent_value(self):
        job = _job(params={**TXT2IMG_PARAMS, "loras": []})
        assert all(label != "LoRAs" for label, _value in job.facts)
```

(`_job(**overrides)` is that module's existing module-level helper (`test_models.py:17`) and does accept `seed=None`. `TXT2IMG_PARAMS` does NOT exist yet: lift the params dict currently inlined inside `TestJobFacts._job` (`test_models.py:104-110`) out to a module-level `TXT2IMG_PARAMS` constant, have that method's default read it, and use the constant in the two new tests. One dict, one place.)

- [ ] **Step 9: Run it to verify it fails, then render lists**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_models.py -q`
Expected: FAIL — the fact reads `"['style.safetensors', 'detail.safetensors']"`.

In `modules/vision/models.py`, add a module-level helper and use it in both branches of `facts`:

```python
def _fact_value(value) -> str:
    """One param value as the card shows it.

    A list (an asset param's selection) reads as a list, not as a Python
    repr: `str(["a", "b"])` on a page is a bug the operator has to decode.
    """
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)
```

replacing `str(value)` with `_fact_value(value)` in the unregistered-operation branch and in the schema branch. The existing `value is None or value == ""` skip already drops an empty list (`[] == ""` is False — so add `or value == []` to the schema branch's skip, and to the unregistered branch's filter):

```python
                if value is None or value == "" or value == []:
                    continue
```

- [ ] **Step 10: Run it to verify it passes**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_models.py -q`
Expected: PASS.

- [ ] **Step 11: Make sure the setup guide names where LoRAs and upscalers go**

Read `ComfyUIEngine.setup_guide.models_note`. It already names `models/checkpoints/`, `models/loras/`, and `models/vae/`; add upscalers so `/setup/` stays the living version of the install instructions:

```python
        models_note=(
            "Place model files yourself: checkpoints (.safetensors) in "
            "ComfyUI/models/checkpoints/, LoRAs in models/loras/, VAEs in models/vae/, "
            "upscale models in models/upscale_models/. "
            "farabunker never downloads a model — community checkpoints come from Civitai "
            "or Hugging Face, and which one to run is your decision, not the platform's."
        ),
```

Append to `modules/vision/tests/test_setup_guides.py`:

```python
    def test_the_models_note_names_every_folder_an_operation_can_ask_for(self):
        note = ComfyUIEngine.setup_guide.models_note
        for folder in ("models/checkpoints/", "models/loras/", "models/upscale_models/"):
            assert folder in note
```

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_setup_guides.py -q`
Expected: PASS.

- [ ] **Step 12: Document the LoRA controls and their one limitation**

In `modules/vision/README.md`, after the operation-adding list, add:

```markdown
## LoRAs

LoRAs are not a mode. They are two params — `loras` (an `"asset"` param of kind
`"lora"`, multi-select) and `lora_strength` — declared once as
`core.inference.operations.LORA_PARAMS` and spliced into every checkpoint-based
operation, so text-to-image, image-to-image, and inpainting offer the same control and
cannot drift apart. The graph side is one place too:
`comfyui_workflows/_fragments.py::checkpoint` chains a `LoraLoader` per selection
between the checkpoint and everything downstream (MODEL→MODEL, CLIP→CLIP, VAE
untouched), so a future checkpoint mode is born with LoRA support and no template
mentions it.

**One strength for the whole selection.** A `Param` carries one value, so per-LoRA
strengths would need a param kind that holds options per item. Two chained LoRAs at one
strength is the common case; the day per-item strengths are worth it, that is a new
param kind, not a second LoRA code path.
```

- [ ] **Step 13: Run both suite orders and commit**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest -q`
Expected: PASS.

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules console scripts -q`
Expected: PASS with the same counts.

```bash
git add core/inference/ modules/vision/models.py modules/vision/tests/ modules/vision/README.md
git commit -m "feat(vision): LoRA controls on every checkpoint mode, from one fragment

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 9: upscale — a mode with no prompt, no sampler, and no seed (and B8)

**Depends on:** Tasks 4 and 7 — `submit_job("upscale", …, files=…)` only validates through Task 4's files/params merge, and `upscale_model` only renders through Task 7's asset widget.

Upscaling is the mode that proves the grammar is really additive: it uses neither the checkpoint the role is bound to nor a prompt nor a seed, and it still ships as one `Operation` plus one template. It also forces the one honest schema change in this plan — `GenerationJob.seed` cannot be `NOT NULL` for an operation that has no seed — which is the same migration that carries the indexes (B8) the gallery has always wanted.

**Files:**
- Modify: `core/inference/operations.py` (`UPSCALE`)
- Create: `core/inference/engines/comfyui_workflows/upscale.py`
- Modify: `core/inference/engines/comfyui_workflows/__init__.py`, `_fragments.py` (`upscale_with_model`)
- Modify: `modules/vision/models.py` (`seed` nullable, `facts` skips a null seed, `Meta.indexes`)
- Create: `modules/vision/migrations/0003_upscale_seed_and_indexes.py`
- Modify: `modules/vision/services.py` (`seed=params.get("seed")`)
- Modify: `modules/vision/apps.py`
- Test: `modules/vision/tests/test_operations.py`, `test_comfyui_workflows.py`, `test_models.py`, `test_services.py`, `test_apps.py`, `test_views_create.py`, `test_views_generate.py`
- Docs: `modules/vision/README.md`, `docs/adr/0012-image-generation-engine-adapter.md`

**Interfaces:**
- Produces: `operations.UPSCALE` (key `"upscale"`, params `init_image` (file, required), `upscale_model` (asset, required, kind `upscale_model`)); `comfyui_workflows.upscale.build(...)`; `_fragments.upscale_with_model(graph, model_asset_id, image) -> Link`; `GenerationJob.seed: int | None`.
- Consumes: the asset widget and validation from Task 7; `_fragments.load_image`/`save_image` from Task 1.

- [ ] **Step 1: Write the failing schema and template tests**

Append to `modules/vision/tests/test_operations.py`:

```python
class TestUpscaleSchema:
    def test_it_asks_for_an_image_and_an_upscaler_and_nothing_else(self):
        assert [param.key for param in UPSCALE.params] == ["init_image", "upscale_model"]
        assert UPSCALE.param("init_image").required is True
        assert UPSCALE.param("upscale_model").asset_kind == "upscale_model"

    def test_it_has_no_seed_because_nothing_about_it_is_random(self):
        assert UPSCALE.param("seed") is None

    def test_validation_returns_no_seed_for_it(self):
        clean = validate_params(UPSCALE, {"init_image": "beach.png", "upscale_model": "4x.pth"})
        assert "seed" not in clean
```

Append to `modules/vision/tests/test_comfyui_workflows.py`:

```python
class TestUpscaleGraph:
    def _graph(self):
        request = GenerationRequest(
            operation="upscale",
            model_id="sdxl.safetensors",
            params={"init_image": "beach.png", "upscale_model": "4x-ultrasharp.pth"},
            client_ref="8f14e45f-ceea-467a-9575-1b0e5a1d1f1e",
        )
        return get_template("upscale")(request, request.model_id, {}, {"init_image": "job/beach.png"})

    def test_it_loads_the_image_and_the_chosen_upscaler(self):
        graph = self._graph()
        assert graph["1"] == {"class_type": "LoadImage", "inputs": {"image": "job/beach.png"}}
        assert graph["2"] == {
            "class_type": "UpscaleModelLoader",
            "inputs": {"model_name": "4x-ultrasharp.pth"},
        }

    def test_it_upscales_and_saves_under_the_job_prefix(self):
        graph = self._graph()
        assert graph["3"] == {
            "class_type": "ImageUpscaleWithModel",
            "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]},
        }
        assert graph["4"]["class_type"] == "SaveImage"
        assert graph["4"]["inputs"]["filename_prefix"] == "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"

    def test_it_never_loads_the_bound_checkpoint(self):
        """The role binding answers "which image engine and which
        checkpoint", and this mode needs only the first half. Loading a
        multi-gigabyte checkpoint it does not use would cost the operator
        VRAM for nothing."""
        classes = {node["class_type"] for node in self._graph().values()}
        assert "CheckpointLoaderSimple" not in classes
        assert "KSampler" not in classes
```

- [ ] **Step 2: Run them to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_operations.py modules/vision/tests/test_comfyui_workflows.py -q`
Expected: FAIL — `ImportError: cannot import name 'UPSCALE'`.

- [ ] **Step 3: Define the operation, the fragment, and the template**

In `core/inference/operations.py`, after `INPAINT`:

```python
UPSCALE = Operation(
    key="upscale",
    label="Upscale",
    capability="image-generation",
    output_media="image/png",
    params=(
        Param("init_image", "file", "Image", accept="image/*", required=True),
        # The upscaler is an ASSET, not a model that answers a role (D7):
        # it adorns this job, and the operator picks it from what the
        # engine reports having.
        Param("upscale_model", "asset", "Upscale model", asset_kind="upscale_model", required=True),
    ),
)
```

Append to `core/inference/engines/comfyui_workflows/_fragments.py`:

```python
def upscale_with_model(graph: Graph, model_asset_id: str, image: Link) -> Link:
    """Run an image through a loaded upscale model. Two nodes, no
    checkpoint and no sampler: an upscaler is a small standalone network,
    which is exactly why this mode needs neither."""
    loader = graph.add("UpscaleModelLoader", model_name=model_asset_id)
    node = graph.add("ImageUpscaleWithModel", upscale_model=[loader, 0], image=image)
    return [node, 0]
```

Create `core/inference/engines/comfyui_workflows/upscale.py`:

```python
"""Upscaling graph: load the image, load the chosen upscale model, run it,
save. Built-in nodes only (D1) -- and notably no checkpoint and no
sampler."""
from __future__ import annotations

from core.inference.engines.comfyui_workflows import _fragments
from core.inference.operations import GenerationRequest


def build(request: GenerationRequest, model_id: str, config: dict, inputs: dict) -> dict:
    """The API-format graph for one upscale request.

    `model_id` -- the checkpoint `vision.generate` is bound to -- is
    deliberately UNUSED: an upscale model is a small standalone network,
    and loading a multi-gigabyte checkpoint this graph never samples from
    would cost the operator VRAM for nothing. The binding still decides
    which ENGINE runs the job, which is the half that matters here.

    `config` (D8) is ignored for the same reason every current template
    ignores it.
    """
    graph = _fragments.Graph()
    image = _fragments.load_image(graph, inputs["init_image"])
    upscaled = _fragments.upscale_with_model(graph, request.params["upscale_model"], image)
    _fragments.save_image(graph, upscaled, request)
    return graph.as_dict()
```

In `comfyui_workflows/__init__.py`, import `upscale` and add `"upscale": upscale.build` to `_TEMPLATES`.

- [ ] **Step 4: Run them to verify they pass**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_operations.py modules/vision/tests/test_comfyui_workflows.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing seed tests**

Append to `modules/vision/tests/test_services.py`:

```python
@pytest.mark.django_db
class TestAnOperationWithNoSeed:
    def test_the_job_records_no_seed_rather_than_a_made_up_one(self, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path
        _bind()
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with patch.dict(operations._OPERATIONS, {"upscale": operations.UPSCALE}), \
             _registered(StubEngine(StubGenerator())):
            job = services.submit_job(
                "upscale", {"upscale_model": "4x.pth"}, files={"init_image": upload}
            )

        assert job.seed is None
        assert job.status == GenerationJob.Status.QUEUED
```

Append to `modules/vision/tests/test_models.py`:

```python
    def test_a_job_with_no_seed_shows_no_seed_fact(self):
        """"Seed None" on a card is a value the operator cannot reuse and
        a mode that never had one."""
        job = _job(operation="upscale", params={"upscale_model": "4x.pth"}, seed=None)
        assert all(label != "Seed" for label, _value in job.facts)
```

- [ ] **Step 6: Run them to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_services.py modules/vision/tests/test_models.py -q`
Expected: FAIL — `KeyError: 'seed'` from `submit_job`, and an `IntegrityError`/`Seed None` fact from the model.

- [ ] **Step 7: Make the seed optional**

In `modules/vision/models.py`:

```python
    # The resolved seed, duplicated out of `params` because it is the one
    # value an operator reuses by hand ("same seed, one more step"). NULL
    # for an operation that has no seed at all -- upscaling is
    # deterministic, and recording a random number it never used would be
    # a fact that is not true.
    seed = models.BigIntegerField(null=True, blank=True)
```

and in `facts`, lead with the seed only when there is one:

```python
        params = self.params or {}
        facts: list[tuple[str, str]] = []
        if self.seed is not None:
            facts.append(("Seed", str(self.seed)))
```

In `modules/vision/services.py::submit_job`:

```python
        seed=params.get("seed"),
```

- [ ] **Step 8: Add the indexes and write the migration**

In `modules/vision/models.py`:

```python
class GenerationJob(models.Model):
    ...
    class Meta:
        ordering = ["-created_at"]
        # B8: every surface that lists jobs sorts on `-created_at` (the
        # create page's recent cards, the gallery's `-job__created_at`
        # join) and the queue-facing views filter on `status`. Fine at
        # hundreds of rows; batch generation and upscaling grow this table
        # fast.
        indexes = [
            models.Index(fields=["-created_at"], name="vision_job_created_idx"),
            models.Index(fields=["status"], name="vision_job_status_idx"),
        ]
```

`GeneratedOutput` gets NO index of its own. Django already indexes the `job_id` FK, which is what `job.outputs.all()` uses, and the gallery's actual query — `GeneratedOutput.objects.select_related("job").order_by("-job__created_at", "index")` (`modules/vision/views.py:163`) — sorts across the join, which a `(job_id, index)` composite cannot serve. B8's complaint is about `-job__created_at`, and `vision_job_created_idx` above is what answers it.

Generate the migration rather than hand-writing it, then read it:

```bash
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python manage.py makemigrations vision --name upscale_seed_and_indexes
```

Confirm the file is `modules/vision/migrations/0003_upscale_seed_and_indexes.py` and contains exactly one `AlterField` (`seed`) and two `AddIndex` operations, then add a docstring at the top of it:

```python
"""`seed` becomes nullable -- an operation may legitimately have no seed
(upscaling is deterministic) -- and the two `GenerationJob` indexes every
listing surface sorts or filters on (B8)."""
```

- [ ] **Step 9: Run the model and service tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_models.py modules/vision/tests/test_services.py -q`
Expected: PASS.

- [ ] **Step 10: Confirm nothing else assumed a seed**

```bash
grep -rn "\.seed" modules/vision console core --include=*.py --include=*.html
```

Every hit must tolerate `None`: `_job_json["seed"]` (JSON `null` — fine), `facts` (fixed above), the create form's `seed` field (a `"seed"` param the operation may simply not declare — fine). Fix anything that does not, in this task.

- [ ] **Step 11: Register the operation and check the page**

In `modules/vision/apps.py::ready()`, import and register `UPSCALE` beside the other three. Extend the Task 2/6 assertions in `test_apps.py` and `test_comfyui_engine.py` to include `"upscale"`.

Append to `modules/vision/tests/test_views_generate.py` (the first class, which posts) and `modules/vision/tests/test_views_create.py` (the second, which renders):

```python
@pytest.mark.django_db
class TestUpscaleWithNoUpscalersInstalled:
    """The S4 ruling, end to end, in BOTH the cases that empty the option
    list: a reachable engine with no upscale models installed, and no image
    model assigned at all. Either way upscaling is impossible, the page
    says so ON THE FIELD rather than accepting the submission, and the
    message names the cause honestly instead of blaming whichever one it
    is not (R2-3)."""

    def test_submitting_an_upscale_with_no_engine_upscalers_is_a_400_naming_the_field(
        self, client, tmp_path, settings
    ):
        settings.GENERATED_DIR = tmp_path / "generated"
        _bind()
        engine = StubEngine()
        engine.list_choices = lambda endpoint, key: ()
        engine.list_assets = lambda endpoint, kind: []
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "upscale": UPSCALE}), \
             patch.dict(ENGINES, {"stubengine": engine}):
            response = client.post(
                reverse("vision-generate"),
                {"operation": "upscale", "init_image": upload},
                **XHR,
            )

        assert response.status_code == 400
        body = response.content.decode().lower()
        assert "no upscale model" in body
        assert GenerationJob.objects.filter(operation="upscale").count() == 0

    def test_an_unbound_role_is_not_blamed_on_the_engines_model_folder(
        self, client, tmp_path, settings
    ):
        """R2-3: with NOTHING bound, `live_options` returns `{}` without
        asking any engine -- so the field is empty for a completely
        different reason. The operator must be pointed at the binding they
        are missing, never told an engine reported something it never
        did."""
        settings.GENERATED_DIR = tmp_path / "generated"
        upload = SimpleUploadedFile("beach.png", PNG, content_type="image/png")
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "upscale": UPSCALE}):
            response = client.post(
                reverse("vision-generate"),
                {"operation": "upscale", "init_image": upload},
                **XHR,
            )

        body = response.content.decode()
        assert response.status_code == 400
        assert "no image model is assigned yet" in body
        assert reverse("inference-console") in body
        assert "reports no" not in body
        assert GenerationJob.objects.filter(operation="upscale").count() == 0
        # Deliberate consequence of the S4 ruling, recorded here so it is
        # not read as a regression: an unbound role makes the upscaler
        # field unfillable, so this POST is refused at the FORM (400) and
        # never reaches `submit_job`'s 503. The message carries the same
        # information the 503 would -- assign a model, here is where -- so
        # nothing is hidden from the operator, and the test below pins that
        # operations needing no asset are untouched.

    def test_an_operation_that_needs_no_asset_still_reports_the_binding_not_a_field(
        self, client
    ):
        """The boundary this fix must not cross. The asset field's message
        is for a control that cannot be filled; it must not become the
        universal answer. txt2img declares no asset param, so an unbound
        role there still reaches `submit_job` and still gets the honest
        503 -- and a MALFORMED txt2img submission still gets its 400,
        which is B10."""
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "upscale": UPSCALE}):
            unavailable = client.post(reverse("vision-generate"), dict(FORM), **XHR)
            malformed = client.post(
                reverse("vision-generate"), {**FORM, "steps": "not a number"}, **XHR
            )

        assert unavailable.status_code == 503
        assert "No model assigned for Image generation" in unavailable.content.decode()
        assert malformed.status_code == 400


@pytest.mark.django_db
class TestUpscalePage:
    def test_it_renders_a_form_with_no_prompt_and_no_sampler(self, client):
        _bind()
        engine = StubEngine()
        engine.list_choices = lambda endpoint, key: ()
        engine.list_assets = lambda endpoint, kind: [Asset(kind=kind, asset_id="4x.pth")]
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "upscale": UPSCALE}), \
             patch.dict(ENGINES, {"stubengine": engine}):
            body = client.get(reverse("vision-create-operation", args=["upscale"])).content.decode()

        assert "<h1>Upscale</h1>" in body
        assert 'name="init_image"' in body
        assert '<option value="4x.pth"' in body
        assert 'name="prompt"' not in body
        assert 'name="sampler"' not in body
```

- [ ] **Step 12: Run the page and app tests**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision/tests/test_views_create.py modules/vision/tests/test_views_generate.py modules/vision/tests/test_apps.py modules/vision/tests/test_comfyui_engine.py -q`
Expected: PASS.

- [ ] **Step 13: Confirm the migration applies to an empty database**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision -q --create-db`
Expected: PASS.

```bash
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python manage.py makemigrations --check --dry-run
```
Expected: `No changes detected`. If Django wants to write another migration, `0003` does not match the models — regenerate it rather than editing it by hand.

- [ ] **Step 14: Document it**

In `modules/vision/README.md`, update the modes sentence to name all four, empty the "Operations beyond `txt2img`" deferral (delete the bullet — every operation the spec deferred now exists), and add under the operation-adding list:

```markdown
Not every mode uses every seam, and that is the point. Upscaling declares no prompt, no
sampler, and no seed, and its template loads no checkpoint at all — the role binding
still decides which ENGINE runs it, which is the half an upscale needs. `GenerationJob.seed`
is nullable for exactly this reason: recording a random number a deterministic mode never
used would be a fact that is not true, so the card simply shows no seed.
```

In `docs/adr/0012-image-generation-engine-adapter.md`, in D6's row or immediately below the D-table, add:

```markdown
**D6 addendum (2026-08-24): `GenerationJob.seed` is nullable.** D6 says the
job record is media-generic and reproducible; a mode with no randomness has
nothing to reproduce. `upscale` declares no `"seed"` param, so its jobs
record `seed = NULL` and their cards show no seed fact, rather than a
random number the generation never used. Every other operation still
resolves and records one (`validate_params` turns a blank seed into an
integer), which is what keeps a txt2img result reproducible.
```

- [ ] **Step 15: Run both suite orders and commit**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest -q`
Expected: PASS.

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules console scripts -q`
Expected: PASS with the same counts.

```bash
git add core/inference/ modules/vision/ docs/adr/0012-image-generation-engine-adapter.md
git commit -m "feat(vision): upscaling, a nullable seed, and the indexes the listings need

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

---

### Task 10: Docs sweep, ADR coherence, and live verification

**Depends on:** Tasks 1–9.

Nine tasks each updated the docs they touched. This one reads the whole story end to end against the shipped code, fixes what the four features made untrue elsewhere, and puts fresh pixels behind the claim that this works.

**Files:**
- Modify (as the sweep finds them): `docs/ARCHITECTURE.md`, `docs/DEV.md`, `docs/adr/0012-image-generation-engine-adapter.md`, `modules/vision/README.md`, `console/inference/README.md`, `docs/ROADMAP.md` (if it carries an image-generation line)
- Test: no new tests; this task runs the whole suite and reads the docs against the code.

**Interfaces:**
- Consumes: everything Tasks 1–9 produced.

- [ ] **Step 1: Run the full suite in both orders from a clean cache**

```bash
cd <worktree>
find . -name __pycache__ -type d -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null; true
```

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest -q`
Expected: PASS.

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules console scripts -q`
Expected: PASS with IDENTICAL counts. A difference between the orders is a test-isolation bug — the two candidates this plan introduced are a leaked `register_operation`/`patch.dict` and the `/object_info` memo (a module missing `_reset_engine_caches`). Find it before continuing.

- [ ] **Step 2: Confirm the schema is exactly what the models say**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python -m pytest modules/vision -q --create-db`
Expected: PASS.

```bash
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' python manage.py makemigrations --check --dry-run
```
Expected: `No changes detected`.

- [ ] **Step 3: Sweep the prose docs**

```bash
grep -rn "txt2img only\|one operation\|img2img, inpaint\|deferred\|no template renders one yet\|asset.*raises" docs modules/vision/README.md console/inference/README.md
```

Fix every statement the four features made untrue. Specifically confirm, and correct where not:

- `modules/vision/README.md`'s "how to add an operation" list still describes the SHIPPED route (schema → template entry → gated registration → nothing under `modules/vision/`) and now covers file params, asset params, and the fragment layer.
- `modules/vision/README.md`'s "Deferred" section no longer lists any of the four operations or the LoRA UI, and still lists what genuinely remains: multi-file families, per-job checkpoint override, retention/quota, the chatbot tool, a second engine adapter, ControlNet, embeddings, and the auth gap.
- `docs/adr/0012`'s Consequences bullet (the one a future planner reads when scoping the NEXT mode) names the files a mode touches and no longer promises a widget that now exists — and its "Two caveats" paragraph is the single-caveat version from Task 7.
- `console/inference/README.md`'s per-engine-endpoints note is untouched (no task in this plan went near discovery).

In `docs/ARCHITECTURE.md`, replace the operations sentence with:

```markdown
  mode (img2img, inpainting, upscaling) is one new operation plus one new template —
  the page renders its form, its job-card facts, and its mode chooser from the
  operation's `Param` schema, the engine adapter moves any file input itself, and the
  templates share one graph vocabulary (`comfyui_workflows/_fragments.py`) so a mode
  is the handful of lines that are actually different. Assets (LoRAs, upscalers) are a
  second axis: parameters that adorn a job, filled from what the engine reports having.
```

In `docs/DEV.md`, at the end of the "Install ComfyUI (image generation)" section, after the existing "Uploads." paragraph, add:

```markdown
**The four modes.** `/vision/` serves text-to-image, image-to-image, inpainting, and
upscaling, chosen from the mode links at the top of the page. Image-to-image and
inpainting take an image you attach (inpainting also takes a mask — white marks the
area to repaint); upscaling takes an image and one of the upscale models you placed in
`ComfyUI/models/upscale_models/`. Any result in the gallery can be fed straight back
in with its "Use in …" link, with no download-and-re-upload round trip. LoRAs are a
control on the three checkpoint modes, listed from `ComfyUI/models/loras/` — the
platform never downloads one, and which LoRA to run is your decision.
```

If `docs/ROADMAP.md` carries an image-generation line, tick the four modes there.

- [ ] **Step 4: Verify every claim the docs now make has a test behind it**

| Claim | Pinned by |
|---|---|
| a mode is one `Operation` plus one template | `test_apps.py` (registration) + `test_comfyui_engine.py::test_supported_operations_*` |
| templates share one graph vocabulary | `test_comfyui_workflows.py::TestFragments`, `TestGraphBuilder` |
| the txt2img graph did not change | `test_comfyui_workflows.py::test_the_whole_graph_is_exactly_what_it_was_before_the_fragment_rewrite` |
| a file input is transferred by the adapter | `test_comfyui_generator.py::TestInputTransfer` (existing) + `test_services.py::TestImg2ImgEndToEnd` |
| an invalid submission is a 400, not a 503 | `test_services.py::TestSubmitJob::test_bad_params_report_the_param_error_even_when_nothing_is_bound` |
| stored inputs are served and reported | `test_views_gallery.py::TestInputFile`, `test_views_create.py::TestJobInputsAreVisible` |
| a queued job carries an image by reference | `test_jobs.py::TestPayloadInputs` |
| a gallery image feeds any file-taking mode | `test_views_gallery.py::TestUseInLinks`, `test_views_generate.py::TestGenerateFromAStoredImage` |
| two file params work | `test_comfyui_workflows.py::TestInpaintGraph` |
| multi-output graphs come back in order | `test_comfyui_generator.py::TestOutputOrdering` |
| asset params render, validate, and are filled from the engine | `test_forms.py::TestAssetFields`, `test_operations.py::TestAssetParams`, `test_views_create.py::TestLiveAssetOptions` |
| the option burst costs one request per node | `test_comfyui_engine.py::TestObjectInfoMemo` |
| LoRAs reach every checkpoint mode | `test_comfyui_workflows.py::TestLorasReachEveryCheckpointMode` |
| a mode may have no seed | `test_services.py::TestAnOperationWithNoSeed`, `test_models.py::test_a_job_with_no_seed_shows_no_seed_fact` |

If any row has no test, write it before committing.

- [ ] **Step 5: Commit the sweep**

Skip if Steps 3–4 changed nothing.

```bash
git add docs modules/vision/README.md console/inference/README.md
git commit -m "docs(vision): sweep the docs against the four shipped modes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>"
```

- [ ] **Step 6: Confirm the branch is clean and every commit carries both trailers**

```bash
git status --porcelain
git log --format='%h %s%n%b' HEAD~10..HEAD | grep -c 'Co-Authored-By: Claude Fable 5'
git log --format='%h %s%n%b' HEAD~10..HEAD | grep -c 'Claude-Session: https://claude.ai/code/session_<id>'
```

Expected: no output from `git status --porcelain`; both counts equal the number of commits this plan added.

- [ ] **Step 7: Verify on the live preview stack**

Per the verification doctrine, nothing is reported complete before fresh pixels. With ComfyUI running natively (`--listen 0.0.0.0`) and at least one checkpoint, one LoRA, and one upscale model placed in its `models/` folders:

```bash
scripts/preview up vision-generation --port 8002 --db-port 5435
```

Then, in the browser at `http://localhost:8002`, with the network disabled:

1. `/inference/` — register the ComfyUI connection and bind `vision.generate`.
2. `/vision/` — the mode chooser lists **Text to image · Image to image · Inpaint · Upscale**. Generate an image; the card's facts line ends with the LoRA fields when any are chosen.
3. `/vision/op/img2img/` — attach a photo, set denoise, generate. The card shows the INPUT above the result.
4. `/vision/gallery/` — a result's caption offers "Use in Image to image", "Use in Inpaint", and "Use in Upscale". Follow the Upscale one: the create page shows the carried thumbnail, the file field is optional, and submitting produces a larger image.
5. `/vision/op/inpaint/` — attach an image and a white-on-black mask; the repainted area is the white one.
6. `/setup/` — the ComfyUI guide names `models/loras/` and `models/upscale_models/`.
7. `/vision/op/controlnet/` — 404 (an unregistered mode is still a 404, not a broken form).

And confirm the combo shape against the real server rather than against the double (M3):

```bash
curl -s http://localhost:8188/system_stats | head -c 200
curl -s http://localhost:8188/object_info/UpscaleModelLoader
curl -s http://localhost:8188/object_info/CheckpointLoaderSimple | head -c 300
```

Expected: the first prints the ComfyUI version; the second's `model_name` spec is either `["COMBO", {"multiselect": false, "options": [...]}]` or the legacy `[[...], {...}]` — both of which `_combo_values` now reads — and the upscale form at `/vision/op/upscale/` lists exactly the files that spec enumerates. If the form is empty while the folder is not, the parser missed a third shape: capture the raw JSON before changing anything.

Only after those pixels is this branch reportable as done. It is **not** merged to main — the work stays on `vision-generation`.

---

## Plan review

**Outcome: CLEAN by adjudication (round 3, 2026-08-24) — this plan is FINAL and ready to execute.** Three review rounds: 16 findings, then 3, then 1 editorial, all applied. Run the checklist below anyway before starting — it is the executor's own sanity pass, not a fourth review.

**Spec coverage.** The gallery hand-off (Task 5) is a DECLARED deliverable of this plan, stated in the Goal and argued in the header — it is not in spec §9, and it is here because three of the four modes take an image and because it is the second caller of the resolver the queue needs anyway. Spec §9's deferred list is closed for: `img2img` (Task 2), `inpaint` (Task 6), `upscale` (Task 9), and "LoRA/embedding params and their UI" — LoRA params in Task 8 and the asset widget in Task 7. Embeddings remain deferred by the ADAPTER, not by this plan: ComfyUI has no embedding loader node (`comfyui.py::_ASSET_NODES` says so), so an `"embedding"` asset param would honestly report nothing; ControlNet is likewise still deferred (it needs a preprocessor story this plan does not open). Both stay on the deferred list in Task 10's sweep. Everything else in §9 (multi-file families, per-job checkpoint override, retention, the chatbot tool, a second adapter, auth) is untouched and stays deferred.

**Forward notes.** B3 → Task 3, B10 → Task 2, B4 → Task 6, B1 and B6 → Task 7, B8 → Task 9, and the heading/chooser wording ruling → Task 2. B5, B7, B9, B2, B12, B13, B14 are explicitly out of scope.

**Duplication.** Every place a copy would have appeared instead extracts: the graph vocabulary (Task 1), the file server (Task 3), the recent-jobs query (Task 3), the shared `PNG` / `stored_output` test fixtures that replace three local PNG constants and would-be per-task builders (Task 3), the stored-file resolver shared by the queue and the page (Tasks 4–5), `live_options` answering both option kinds (Task 7), the LoRA chain inside `checkpoint` (Task 8). No task adds a template, a view, or a form branch per mode.

**Type consistency.** `_fragments.checkpoint` is `(graph, model_id)` in Task 1 and becomes `(graph, model_id, params)` in Task 8 — the only signature change in the plan. Task 8 updates ALL EIGHT call sites: the three templates and the five two-argument calls in `TestFragments` that Task 1 wrote. `params` gets no default, deliberately. `build_form`'s option argument is `engine_choices` until Task 5 renames it to `engine_options`, which is what Task 7's asset branch reads. `views.live_choices` becomes `views.live_options` in Task 5 and grows the asset branch in Task 7. `Operation.file_param_keys()` (frozenset) exists throughout; `Operation.file_params()` (ordered tuple) arrives in Task 5 and is used by Tasks 5, 6, and 9. A stored reference is `"output:<id>"` / `"input:<id>"` everywhere.


**Rulings this plan makes explicitly** (so an executor does not re-litigate them mid-task):

- **The gallery hand-off is in scope** (Task 5), declared in the Goal.
- **An unlistable REQUIRED asset is refused on the field, not in a silent 400** (Task 7 Step 7, pinned by `test_forms.py::TestAssetFields` and `test_views_generate.py::TestUpscaleWithNoUpscalersInstalled`). `validate_params` still refuses it as the schema floor for callers with no form.
- **That message names BOTH causes** — engine has none installed, or no model assigned — because `live_options` returns `{}` for either, and claiming the engine reported something it never did is B10's defect at the form layer (R2-3). The `check.ready` 503 is NOT hoisted above `form.is_valid()`.
- **It points at `/setup/` and `/inference/` rather than naming `ComfyUI/models/upscale_models/`** — spec §3 keeps engine paths out of `modules/vision/`, and `/setup/` renders the bound adapter's own `models_note`.
- **`_combo_values` reads both live combo shapes, and `FakeComfyUI` speaks the newer one by default** (Task 7 Steps 13/15), because ComfyUI 0.33.0 serves both and a legacy-only double hides an unfillable form.
- **`GeneratedOutput` gets no index** (Task 9): the FK is already indexed and the gallery's sort crosses the join.
- **`Graph.as_dict` copies the id map only** (Task 1): nothing mutates the node dicts it returns.

## Review history

- **Round 1 — adversarial hygiene review, 2026-08-24: AMEND, 16 findings — all applied.**
  Highest-priority: M3, the `["COMBO", {"options": [...]}]` spec live ComfyUI 0.33.0 sends for
  `UpscaleModelLoader`, which the shipped parser reads as `()` and which `FakeComfyUI` could not
  have caught (Task 7 Steps 13/15, verified again against the live server in Task 10 Step 7).
  Mechanics: the five two-argument `checkpoint()` test call sites (M1), `TestGetTemplate`'s
  unknown-operation example repointed off `"inpaint"` (M2), the engine-side upload name
  `init_image-beach.png` (M4), the two `run_generate` assertions carried over rather than deleted
  (M5), strict sequencing plus a dependency table (M6). Value: the gallery hand-off declared as
  scope instead of arriving inside a task (V1), `stored_input_exists` instead of building a
  `StoredFile` to test existence (V2), the unused `vision_output_job_idx` dropped (V3).
  Duplication: the already-present `UpscaleModelLoader` helper clause struck (D1), one `PNG`
  constant replacing three (D2), the D7 row amended alongside the Consequences caveat (D3).
  Simplicity: the copy-on-read test deleted (S1), one `stored_output` fixture for nine call sites
  (S2), `TXT2IMG_PARAMS` created rather than assumed (S3), and the unlistable-required-asset
  ruling decided and pinned (S4).
- **Round 2 — scoped re-check of the amended sections, 2026-08-24: AMEND, 3 new findings — all
  applied.** R2-1: the File-Structure row still said "three indexes" after V3 cut the migration
  to two. R2-2: `test_a_typed_input_is_still_not_a_combo` went through `list_choices`, which
  short-circuits on `_CHOICE_INPUTS` before reaching the parser, so the `["INT", …]` guard was
  unpinned and the coverage note claiming otherwise was false — the test now calls
  `comfyui._combo_values` directly. R2-3: S4's message asserted "the image engine reports no
  <asset>", but `live_options` returns `{}` for an UNBOUND role without asking any engine, so an
  unbound-role upscale POST was told a fact no engine stated — B10's "wrong problem, confidently"
  defect on a new path. The copy now names both causes and links `/setup/` and `/inference/`; the
  `check.ready` 503 was deliberately NOT hoisted above `form.is_valid()` (that would re-create
  B10 for every malformed submission); three tests pin it — the wording, the unbound-role path,
  and the boundary that an operation needing no asset still reports the binding.
- **Round 3 — scoped re-check, 2026-08-24: AMEND, 1 editorial (R3-1) — orchestrator adjudication:
  ACCEPTED and applied; no further review round.** R2-2's test reaches
  `comfyui._combo_values` and the memo tests reach `comfyui._OBJECT_INFO_TTL` and `httpx.HTTPError`,
  but `modules/vision/tests/test_comfyui_engine.py` imports only `ComfyUIEngine` — Task 7 Step 13
  now instructs the two module-level imports explicitly. **Plan declared FINAL: CLEAN by
  adjudication.**