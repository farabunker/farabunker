# Image generation (vision track) — design

**Date:** 2026-08-22
**Branch:** `vision-generation` (worktree `.claude/worktrees/vision-generation`)
**Status:** approved design, pending implementation plan
**Builds on:** ADR 0010 (model-management framework) and its 2026-08-22 amendments; ADR 0011 (branch preview stacks)

## 1. Goal

Add image generation (the diffusion-checkpoint family) to Farabunker as a standalone
feature — a `/vision/` page where the operator types a prompt and gets an image —
integrated through the **same model-management grammar** the RAG module already
uses: a new engine adapter, a new capability, a new role bound in the `/inference/`
console. Later the conversational agent calls the same service layer as a toggleable
tool.

Non-negotiable properties, in priority order:

1. **Offline.** After install, the whole path works with no network. The platform
   never downloads a model, checkpoint, or asset (ADR 0010 §5). Operators place
   files; the platform lists and uses them.
2. **Portable.** The generation engine runs natively on the host (Metal/CUDA/ROCm)
   on macOS, Windows, or Linux, reached from the `web` container the way Ollama is
   (`host.docker.internal`, `extra_hosts: host-gateway`).
3. **Content-agnostic.** No prompt or output filtering, no model inspection. Any
   checkpoint the operator places — including uncensored community merges — is a
   first-class model. What to run is the operator's decision, not the platform's.
4. **Additive growth.** The next generation modes (img2img, inpainting, ControlNet,
   upscaling, LoRA), the next model families, the next engine, and the
   chatbot tool must each be *additions* (new operation, new template, new adapter,
   new caller), never a reshape of what this spec builds.

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | First engine adapter targets **ComfyUI** | Actively maintained; Windows portable build, Linux (CUDA/ROCm), macOS (Metal); fully offline once checkpoints are on disk; stable HTTP API (`/prompt`, `/history`, `/view`, `/object_info`, `/system_stats`); our templates use only built-in nodes, so vanilla ComfyUI with no custom nodes or Manager suffices. |
| D2 | Engine is a `core.inference.engines` adapter, registered in `ENGINES` | The registry is already engine-agnostic: health checks, server scan, "Supported APIs", and the engine dropdown iterate `ENGINES.values()`. A second adapter is one file + `register()`. |
| D3 | New capability `"image-generation"`; existing `"vision"` keeps meaning image-*input* | Output and input capabilities are different needs; `vision` already enriches vision-capable catalog entries. |
| D4 | One role, `vision.generate`, registered by `modules.vision` | "The first non-RAG role" that ADR 0010 says validates the framework. The role binding *is* the checkpoint; switching checkpoints = rebinding in the console. Per-job checkpoint override is deferred (§9). |
| D5 | **Operation-driven**, not mode-driven | Generation modes have wildly different parameters. A platform-level operation registry with per-operation parameter schemas lets the page render its form from the schema and lets an engine map each operation to its own template. Adding inpainting is one operation definition + one template. |
| D6 | Generic job/output records with `media_type` and the stored engine payload | Video/audio later are new `media_type` values; every result is reproducible and exportable. |
| D7 | Assets (LoRA, VAE, ControlNet, upscaler, embeddings) are a second axis, not role-bound models | They adorn a job; they don't answer a role. Interface ships now, UI ships with the first operation that uses it. |
| D8 | `ModelConnection` gains a nullable `config` JSONField | `ResolvedModel.config` already flows into engine builders; the DB row could not carry it. Needed for multi-file families (UNet + CLIPs + VAE) without a later schema change. |
| D9 | Feature flag gates role registration and URL mount | ADR 0010's amendment: a feature toggle has exactly one job — gate role registration. Building it into the first feature establishes the pattern the chatbot's per-tool toggles reuse. |
| D10 | Completion is poll-driven; no worker, no task queue | ComfyUI queues natively. The page polls a job endpoint that refreshes the job. A worker can be added later for unattended/batch runs without changing the service API. |
| D11 | Per-engine discovery endpoints (the one framework change) | `discover()` polls every engine at the single Ollama endpoint today; ComfyUI's checkpoints would never appear. Fixed by a per-engine default-endpoint map plus registered-connection endpoints. |
| D12 | No default model anywhere; `COMFYUI_BASE_URL` is the only default (a *location*) | Same exception `OLLAMA_BASE_URL` already has under the no-baked-defaults amendment. |

## 3. Architecture

```
 /vision/ page ──▶ modules/vision/views.py ──▶ modules/vision/services.py
                                                      │  resolve("vision.generate")  (core.inference.bindings)
                                                      │  get_engine(resolved.engine) (core.inference.engines)
                                                      ▼
                              core/inference/gateway.get_image_generator(role)
                                                      │
                                                      ▼
                         core/inference/engines/comfyui.py :: ComfyUIEngine.build_image_generator()
                                                      │  operation ──▶ comfyui_workflows/<operation>.py (graph template)
                                                      ▼
                                  ComfyUI on the host  (POST /prompt, GET /history, GET /view)
```

Layering rules (all already in force for Ollama, restated for the new pieces):

- `core/` never imports `console/` or `modules/` at module scope.
- Engines are stateless: `endpoint` is data passed per call, never read from settings.
- ComfyUI graph vocabulary (node class names, node ids, `/prompt` payload shape) never
  leaves `core/inference/engines/comfyui*`. Above that line only platform types exist:
  `Operation`, `GenerationRequest`, `JobStatus`, `InstalledModel`, `Asset`.
- The page never knows which engine is bound. The chatbot tool later calls
  `modules.vision.services` and gets identical behaviour.

## 4. Components

### 4.1 `core/inference/operations.py` — platform operation vocabulary (new)

```python
@dataclass(frozen=True)
class Param:
    key: str                     # "prompt", "width", "steps", "init_image", "lora"
    kind: str                    # "text" | "int" | "float" | "choice" | "seed" | "file" | "asset"
    label: str
    default: Any = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: tuple[str, ...] = ()   # for "choice"
    asset_kind: str | None = None   # for "asset": "lora" | "vae" | "controlnet" | "upscale_model" | "embedding"
    accept: str | None = None       # for "file": mime pattern, e.g. "image/*"
    required: bool = False
    multiple: bool = False          # e.g. several LoRAs

@dataclass(frozen=True)
class Operation:
    key: str                     # "txt2img"
    label: str                   # "Text to image"
    capability: str              # "image-generation"
    params: tuple[Param, ...]
    output_media: str            # "image/png" — what a successful run produces

register_operation(op) / all_operations() / get_operation(key) / operations_for(capability)
```

Defines exactly one operation in this cut, `TXT2IMG` (key `txt2img`). Definitions live here in
core; *registration* is done by the feature app that serves them (`modules/vision/apps.py`, §4.7), so
`all_operations()` only lists what an enabled feature can actually run:

| param | kind | default / range |
|---|---|---|
| prompt | text | required |
| negative_prompt | text | "" |
| width, height | int | 1024, 64–4096 step 8 (the page clamps; the engine may reject) |
| steps | int | 25, 1–150 |
| cfg_scale | float | 7.0, 0–30 step 0.5 |
| seed | seed | blank = random; the resolved integer is stored on the job |
| sampler | choice | engine-reported list via `list_choices`; default first |
| scheduler | choice | same |
| batch_size | int | 1, 1–8 |

Validation (`validate_params(operation, raw) -> dict`) coerces and range-checks
against the schema and is the only validation the view does; engines may reject
further (e.g. size not divisible by 8) and that surfaces as a failed job.

`GenerationRequest` (frozen): `operation: str`, `model_id: str`, `params: dict`,
`inputs: dict[str, Path]` (file params resolved to paths in the job dir),
`client_ref: str` (the job's UUID, used as the output filename prefix).

### 4.2 `core/inference/engines/base.py` — protocol additions (additive)

```python
@dataclass(frozen=True)
class Asset:
    kind: str          # "lora" | "vae" | "controlnet" | "upscale_model" | "embedding"
    asset_id: str      # opaque engine identifier (filename, may contain OS separators)
    size: int | None = None

@dataclass(frozen=True)
class JobStatus:
    state: str                 # "queued" | "running" | "done" | "failed" | "lost"
    error: str | None = None
    progress: float | None = None   # 0..1 when the engine reports it

class ImageGenerator(Protocol):
    def submit(self, request: GenerationRequest) -> tuple[str, dict]:
        """Returns (engine_ref, payload) — payload is the exact engine submission, stored verbatim."""
    def status(self, engine_ref: str) -> JobStatus: ...
    def fetch_outputs(self, engine_ref: str) -> list[tuple[str, bytes, str]]:
        """(filename, content, media_type) per output."""

class InferenceEngine(Protocol):            # existing members unchanged; new optional members:
    def supported_operations(self, model_id: str, endpoint: str) -> tuple[str, ...]: ...
    def list_assets(self, endpoint: str, kind: str) -> list[Asset]: ...
    def list_choices(self, endpoint: str, param_key: str) -> tuple[str, ...]: ...   # samplers, schedulers
    def build_image_generator(self, model_id: str, endpoint: str, **cfg) -> ImageGenerator: ...
```

Downstream reads every optional member via `getattr(engine, name, None)`, matching
the existing convention, so `OllamaEngine` is untouched.

### 4.3 `core/inference/engines/comfyui.py` — the adapter (new)

- `name = "comfyui"`, `api_description = "the ComfyUI HTTP API"`,
  `library_url = "https://github.com/comfyanonymous/ComfyUI"`,
  `install_cmd_template = "copy <model>.safetensors into ComfyUI/models/checkpoints/"`,
  `well_known_ports = (8188,)`.
- `is_healthy(endpoint)` → `GET /system_stats` 200.
- `list_installed(endpoint)` → `GET /object_info/CheckpointLoaderSimple`, each entry of
  `input.required.ckpt_name[0]` → `InstalledModel(model_id=<name>, capabilities=("image-generation",))`.
  `model_id` is opaque (Windows hosts return `subdir\file.safetensors`).
- `list_assets(endpoint, kind)` → `/object_info/LoraLoader` (`lora_name`), `/object_info/VAELoader`
  (`vae_name`), `/object_info/ControlNetLoader`, `/object_info/UpscaleModelLoader`; unknown kind → `[]`.
- `list_choices(endpoint, key)` → `/object_info/KSampler` → `sampler_name` / `scheduler` lists.
- `supported_operations(model_id, endpoint)` → `("txt2img",)` for this cut.
- `build_image_generator(model_id, endpoint, **cfg)` → `ComfyUIGenerator(endpoint, model_id, cfg)`:
  - `submit(request)`: builds the graph via `comfyui_workflows.get_template(request.operation)(request, model_id, cfg)`,
    `POST /prompt {"prompt": graph, "client_id": request.client_ref}` → `prompt_id`. Returns `(prompt_id, {"prompt": graph, ...})`.
  - `status(ref)`: `GET /history/{ref}`; empty → check `GET /queue` (running/pending contain `ref`) → `running`/`queued`;
    absent from both → `lost`. History present with `status.status_str == "error"` → `failed` with the
    first `messages` `execution_error` text; with `outputs` → `done`.
  - `fetch_outputs(ref)`: history `outputs.*.images[]` → `GET /view?filename=&subfolder=&type=output`, media type from
    the filename extension.
- `build_llm` / `build_embedder` raise `NotImplementedError("comfyui serves image generation only")`;
  they can never be reached through a role because role options are filtered by capability.
- HTTP: `httpx`, per-call timeouts, no retries in the adapter (the service layer decides).

### 4.4 `core/inference/engines/comfyui_workflows/` — graph templates (new)

`__init__.py` exposes `get_template(operation_key)`; `txt2img.py` builds the API-format graph:

```
"1" CheckpointLoaderSimple(ckpt_name=model_id)
"2" CLIPTextEncode(text=prompt,          clip=["1",1])
"3" CLIPTextEncode(text=negative_prompt, clip=["1",1])
"4" EmptyLatentImage(width, height, batch_size)
"5" KSampler(seed, steps, cfg, sampler_name, scheduler, denoise=1.0,
             model=["1",0], positive=["2",0], negative=["3",0], latent_image=["4",0])
"6" VAEDecode(samples=["5",0], vae=["1",2])
"7" SaveImage(filename_prefix=client_ref, images=["6",0])
```

`cfg` from the connection (D8) is reserved here for family-specific loaders (a multi-file template
will read `cfg["unet"]`, `cfg["clip"]`, `cfg["vae"]`); `txt2img.py` ignores it.

### 4.5 `core/inference/roles.py`, `gateway.py` (small additive edits)

- `CAPABILITIES = {"chat", "embeddings", "vision", "image-generation"}`.
- `VISION_GENERATE_ROLE = "vision.generate"`.
- `gateway.get_image_generator(role: str = VISION_GENERATE_ROLE) -> ImageGenerator`:
  `resolve(role)` → `get_engine(...)` → `build_image_generator(model_id, endpoint, **config)`;
  raises `ValueError` if the engine lacks `build_image_generator`.

### 4.6 Console / settings — the coordination commit (touches the other track's files)

One early, self-contained commit containing only:

1. `config/settings.py`: `COMFYUI_BASE_URL = os.environ.get("COMFYUI_BASE_URL", "http://localhost:8188")`;
   `INFERENCE_DEFAULT_ENDPOINTS = {"ollama": OLLAMA_BASE_URL, "comfyui": COMFYUI_BASE_URL}`;
   `FARABUNKER_FEATURES` parsed from a comma-separated env var (default: `"vision"`, i.e. enabled — an
   operator opts *out*); `GENERATED_DIR = DATA_DIR / "generated"`; `"modules.vision"` in `INSTALLED_APPS`.
2. `config/urls.py`: `path("vision/", include("modules.vision.urls"))`, mounted only when the feature is enabled.
3. `console/inference/discovery.py`: `discover(endpoints: dict[str, set[str]], connections)` — for each engine,
   poll the union of `INFERENCE_DEFAULT_ENDPOINTS[engine]` (if any) and that engine's registered-connection
   endpoints; per-(engine, endpoint) try/except as today. `scan_for_servers` unchanged.
4. `console/inference/views.py`: `_default_endpoint()` → `_engine_endpoints(connections)` building that map;
   `_CAPABILITY_NEED_PHRASES["image-generation"] = "an image-generation model"`.
5. `console/inference/models.py`: `ModelConnection.config = models.JSONField(null=True, blank=True)` + migration
   `0007_modelconnection_config` (renumbered from `0003_...` when the model-management track's
   `0003`-`0006` landed first); `db_provider` passes it through as `ResolvedModel.config` (`{}` when null).
6. `.env.example`, `compose.yaml`, `compose.preview.yaml`: `COMFYUI_BASE_URL: http://host.docker.internal:8188`,
   `./data/generated` in the durable-data comment; `scripts/preview` creates `data/preview/<branch>/generated`.

Existing tests for discovery/views are updated in the same commit; Ollama behaviour is unchanged
(its default endpoint is still polled when nothing is registered).

### 4.7 `modules/vision/` — the feature (new)

```
modules/vision/
  apps.py          VisionConfig.ready(): if "vision" in settings.FARABUNKER_FEATURES:
                       register_role(RoleSpec(VISION_GENERATE_ROLE, "Image generation", "image-generation"))
                       register_operation(TXT2IMG)     # operations ship in core; the app only registers what it serves
  models.py        GenerationJob, JobInput, GeneratedOutput
  store.py         job_dir(job_id) -> GENERATED_DIR/<uuid>/ ; store_input ; store_output ; remove_job_files
  services.py      preflight() ; submit_job() ; refresh_job() ; wait_for() ; delete_job()
  forms.py         build_form(operation, engine_choices) — renders Param schema to fields
  views.py         CreatePageView, generate, job_status, gallery, output_file, job_delete
  urls.py
  templates/vision/{base.html, create.html, _job_card.html, gallery.html}
  migrations/0001_initial.py
  tests/
  README.md
```

**Models**

- `GenerationJob`: `id` (UUID pk — also the ComfyUI filename prefix), `operation`, `params` JSON (validated),
  `seed` (resolved int), `engine`, `model_id`, `model_fingerprint`, `model_config` JSON (snapshot of the
  resolved model at submit time), `engine_ref`, `engine_payload` JSON (verbatim submission, D6),
  `status` (`queued|running|done|failed`), `error`, `created_at`, `started_at`, `finished_at`.
- `JobInput`: `job` FK, `param_key`, `path`, `media_type` — empty for txt2img, used by file params later.
- `GeneratedOutput`: `job` FK, `index`, `path`, `media_type`, `width`, `height`, `created_at`.

**Services** (the future chatbot-tool surface)

- `preflight() -> PreflightResult(state: "ready"|"unbound"|"unreachable", resolved, message)` — same shape as
  `AskView._precheck_models`: `resolve(VISION_GENERATE_ROLE)` then `engine.is_healthy(endpoint)`.
- `submit_job(operation_key, raw_params, files) -> GenerationJob` — validate via operation schema, resolve
  seed, create the job row, store inputs, build `GenerationRequest`, `generator.submit`, persist
  `engine_ref` + `engine_payload`, status `queued`. Raises `VisionUnavailable(state, message)` if preflight fails.
- `refresh_job(job) -> GenerationJob` — `generator.status(ref)`; `running` → set `started_at`; `done` →
  `fetch_outputs`, `store_output` each, create `GeneratedOutput` rows (dimensions read from the PNG header),
  `finished_at`; `failed`/`lost` → `status=failed`, `error` (lost: "ComfyUI no longer has this job — it was
  probably restarted; resubmit"). Engine unreachable during refresh leaves the job untouched and returns it
  with a transient `unreachable=True` attribute for the card to display. Idempotent; safe to call repeatedly.
- `wait_for(job, timeout, interval=1.0)` — loop `refresh_job` until terminal or timeout; for synchronous callers.
- `delete_job(job)` — removes rows and `remove_job_files`.

**Views / URLs** (`/vision/`)

| route | view | behaviour |
|---|---|---|
| `""` | `CreatePageView` | form rendered from the `txt2img` schema (samplers/schedulers from `list_choices`, cached per request), preflight banner (three honest states, link to `/inference/` when unbound/unreachable), recent jobs as cards |
| `generate/` POST | `generate` | `submit_job`; on success returns the job card fragment (or redirects for non-JS); on `VisionUnavailable` 503 with the honest message |
| `jobs/<uuid>/` GET | `job_status` | `refresh_job` then render `_job_card.html` (HTML fragment; `?format=json` returns the job dict for the future tool/tests) |
| `jobs/<uuid>/delete/` POST | `job_delete` | |
| `gallery/` | `gallery` | paginated outputs with prompt, seed, params, model; "reuse settings" pre-fills the form |
| `outputs/<int:id>/file/` | `output_file` | serves the stored file (mirrors `rag-document-file`) |

Polling: ~15 lines of inline JS in `create.html` — every 2 s fetch `jobs/<uuid>/` for each non-terminal
card and swap the fragment; stop on terminal state. No CDN, no external assets. Non-JS fallback: the card
shows "refresh to update".

**Templates** reuse the design tokens of `rag/base.html` in a module-local `vision/base.html` (the three
module base templates are a known consolidation candidate, out of scope here).

## 5. Data & storage

- `./data/generated/<job-uuid>/` holds inputs and outputs for one job (mirrors `./data/documents/<doc-id>/`).
  Inside the container it is `/app/data/generated`; preview stacks use `data/preview/<branch>/generated`.
- Outputs are fetched from ComfyUI and copied into the managed store; ComfyUI's own `output/` folder is
  not relied on afterwards (ComfyUI may be on another machine or wiped).
- Deleting a job deletes its directory. No retention policy in this cut (§9).

## 6. Error handling

| situation | behaviour |
|---|---|
| role unbound | page banner + `generate` 503 "No model assigned for Image generation — assign one at /inference/" |
| engine unreachable at submit | banner + 503 "ComfyUI at `<endpoint>` is not reachable"; link to /inference/ |
| ComfyUI rejects `/prompt` (400: unknown checkpoint, invalid graph) | job created then immediately `failed` with ComfyUI's `node_errors` text |
| execution error (OOM, bad size) | `failed`, error from history `messages` |
| history + queue both lack the ref | `failed`, "lost" message |
| engine unreachable during poll | card shows "engine unreachable, still checking"; job unchanged |
| queued longer than `VISION_STALE_AFTER` (default 10 min) | card marked stale with "check ComfyUI" hint; still polled |
| invalid params | 400 from schema validation; form re-rendered with messages |

## 7. Testing

All offline; HTTP mocked at the `httpx` layer (never the adapter's own methods), same convention as
`fake_ollama_get`. New helper `modules/vision/tests/_helpers.py::fake_comfyui(...)` dispatching on path:
`/system_stats`, `/object_info/<node>`, `/prompt`, `/history/<id>`, `/queue`, `/view`.

| area | tests |
|---|---|
| `operations` | schema validation: coercion, ranges, required, unknown keys rejected, seed resolution |
| `comfyui` engine | health, `list_installed` parsing (incl. backslash ids), `list_assets`, `list_choices`, `submit` payload shape, `status` state mapping (queued/running/done/failed/lost), `fetch_outputs` |
| `txt2img` template | every param lands on the right node/input; prefix = client_ref; cfg ignored |
| `gateway` | `get_image_generator` resolves and builds; clear error when the engine can't |
| coordination commit | `discover` polls each engine at its own endpoints; Ollama-only behaviour unchanged; `config` round-trips through `db_provider`; checklist phrase |
| `modules/vision` | role registered only when feature enabled; store paths; services state machine with a fake `ImageGenerator` (submit/refresh/wait/delete, unreachable-during-poll, lost); views for each route incl. 503 states and the JSON form of `job_status`; migration applies on an empty DB (preview stack) |

Acceptance on the live preview stack (`scripts/preview up vision-generation --port 8002 --db-port 5434`,
ComfyUI running natively with `--listen 0.0.0.0`): register ComfyUI + a checkpoint in `/inference/`, bind
`vision.generate`, generate an image on `/vision/`, see it in the gallery, open the file, delete it — with
the network disabled. Per the verification doctrine, nothing is reported complete before that screenshot.

## 8. Documentation

- **ADR 0012 — Image generation engine adapter**: decisions D1–D12, the operation registry, the per-engine
  discovery change, the `config` column, the feature flag, and the content-agnostic stance.
- **DEV.md**: "Install ComfyUI" (macOS/Windows portable/Linux; `--listen 0.0.0.0`; where `models/checkpoints`
  is; that community checkpoints come from Civitai/Hugging Face and are placed by the operator; what
  "offline" means here; GPU contention with Ollama on single-GPU hosts), the first-run bind flow for
  `vision.generate`, and the `/vision/` page.
- **ARCHITECTURE.md**: engines/operations/assets axes; `modules/vision` in the module list.
- `modules/vision/README.md`, `console/inference/README.md` (note the per-engine endpoints), `.env.example`,
  compose comments, ROADMAP tick for image generation.

## 9. Deferred (explicitly not in this cut)

- Operations: `img2img`, `inpaint`, `controlnet`, `upscale`, LoRA/embedding params and their UI.
- Model families needing multi-file loaders: template + `config` fields; the column exists.
- Per-job checkpoint override (choose among registered image-generation connections on the page).
- A background worker / batch runs; WebSocket progress (ComfyUI offers `/ws`; polling suffices now).
- Retention/quota for `./data/generated`.
- Chatbot tool wrapper over `services` and per-tool toggles (the feature-flag pattern is the hook).
- A second engine adapter (A1111/Forge, SwarmUI, InvokeAI, own diffusers service) mapping the same operations.
- Authentication for `/vision/` mutation endpoints — shared Phase-1 gap with `/inference/`.

## 10. Coordination with the model-management track

The other active worktree (`worktree-model-management-framework`) owns `console/inference/*`,
`core/inference/bindings.py`, `config/settings.py`, `docs/DEV.md`. This track touches those only in the
single coordination commit of §4.6, landed first and announced so the other track can rebase. Everything
else is new files or additive edits to `core/inference/roles.py`, `gateway.py`, `engines/base.py`.
