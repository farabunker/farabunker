# Vision EDIT Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `/vision/` an instruction-based `edit` operation that runs on either of the two newly installed multi-file GGUF model families, and give the generation form a per-generation model picker of registered image-generation connections — the role binding staying the default — with the operation chooser reflecting what the *selected* model can actually run.

**Architecture:** Twelve strictly sequential tasks on the `vision-generation` branch. Task 1 opens the registration path: `InstalledModel` gains an engine-shaped `loader` fact so a GGUF/native diffusion-model file appears in `/inference/` beside a checkpoint. Task 2 rekeys the ComfyUI template registry on `(family, operation)`, makes `supported_operations` family-aware, and adds `families()`. Task 3 teaches the adapter to report the engine's OWN family vocabulary (`CLIPLoader.type`, narrowed to what `families()` can run) and its text-encoder list. Task 4 carries `family` + `text_encoder` + `vae` from the console's manual registration form into the `ModelConnection.config` JSONField that D8 already added for exactly this. Task 5 defines ONE `edit` operation whose params are the honest intersection of both families' graphs. Tasks 6–7 write the two edit graph templates, derived node-for-node from ComfyUI 0.33.0's own bundled edit workflows, each ending in a LIVE verification run through the governed farabunker queue. Tasks 8–11 build the picker: a generalized `role_primary(role_key)` and a `connections_for_picker(capability)` on the console side, an optional `resolved=` seam through `submit_job`/`plan_generate`/`run_generate`, a model-aware operation catalog, and finally the create page's `<select>` — the same grammar the Ask page already uses, resolving through the same `console.inference.bindings.resolve_connection`. Task 12 is docs, the ADR 0012 amendment, and the full both-order suite.

**Tech Stack:** Python 3.12, Django 5, `httpx` against ComfyUI 0.33.0's HTTP API (`/object_info`, `/prompt`, `/history`, `/view`, `/upload/image`, `/queue`, `/system_stats`), the ComfyUI-GGUF custom node pack (`UnetLoaderGGUF`, `CLIPLoaderGGUF`), pytest + pytest-django, PostgreSQL on the branch preview port 5435. No new Python dependencies, no build step, no JS framework, no CDN.

**Spec:** `.superpowers/owner-requirements/memory-governance.md` (§ "Model decision" and the binding picker requirement) and `.superpowers/owner-requirements/comfyui-memory-seams-contract.md` — both reproduced in "Spec (owner's requirements)" below, because the second is the plan this one executes *after*.

## Global Constraints

- **Depends on the memory-seams plan landing FIRST.** `docs/superpowers/plans/2026-08-25-comfyui-memory-seams.md` (committed at `27b8993`; Task 1 landed at `a12150a`, Task 2 at `6f435f9`, its ADR amendment at `ba5cf84` — read it before Task 1 and reconcile against whatever has landed by then) implements `ComfyUIEngine.loaded_footprint` / `ComfyUIEngine.unload` per `comfyui-memory-seams-contract.md`. This plan implements NEITHER and must not duplicate either. Sequencing is the owner's: governance seams → verified through a GOVERNED queue job → then this feature. **The first load of the ordinary build happens only through the queue.**
- **Governed path only. Never a raw ComfyUI workload.** No `POST /prompt` from a shell, a script, or an agent. Every generation in this plan goes through `/vision/` → `core.inference.queue.enqueue` → the worker. Read-only `GET /object_info/<Node>`, `GET /system_stats`, `GET /queue` are the only direct calls permitted, and they load nothing. The machine OOM-crashed on 2026-08-24 doing otherwise.
- **`core/` imports nothing from `console/` or `modules/`.** `modules/` may import `console.inference.bindings` (and only that module — `modules/rag/views.py` establishes the pattern); it must never reach `console.inference.models` directly.
- **NO changes to `console/jobs/*` or `core/inference/jobkinds.py`.** The queue track owns those (observed-source labels, affinity batching, peak monitoring, per-endpoint budgets). If a task appears to need one, STOP and report it.
- **No baked model names in code or UI copy.** Not in `core/`, not in `console/`, not in a template, not in a form label, not in a help string. Fixtures and tests MAY name files. The two family keys this plan introduces (`"flux2"`, `"qwen_image"`) are **not** model names: they are the engine's OWN `CLIPLoader.type` combo values, read live from `/object_info/CLIPLoader`, and they name a GRAPH TEMPLATE the same way `"txt2img"` does. They live only in `core/inference/engines/comfyui_workflows/` — the module ADR 0012 D1 already designates as the one place ComfyUI's graph vocabulary exists.
- **Never guess.** No name-pattern matching against a shipped list of models. A fact is either read from the engine or declared by the operator; anything else is left visibly unknown.
- **Never-500.** A missing node, an absent custom-node pack, a stale reference, a deleted connection, a malformed pk — each degrades to an honest message, never an exception out of a view.
- **Offline-first.** Plain POSTs; every page fully works with JavaScript disabled. JS is progressive enhancement only.
- **Tests and docs ship with every task.** TDD: failing test first, run it, minimal implementation, run it, docs, commit.
- **Test invocation (ONE at a time, foreground):**
  `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest <path> -q`
- **No `conftest.py` anywhere.** Class-level `@pytest.mark.django_db` only where a test really touches the DB. Shared helpers live in `modules/vision/tests/_helpers.py` / `console/inference/tests/_helpers.py`. Mocking is HTTP-LAYER only (`patch("core.inference.engines.comfyui.httpx.get", fake.get)`) — engine methods are never mocked away. `FakeComfyUI` is EXTENDED for the GGUF loaders, never replaced.
- **Migrations only if truly needed — flag loudly.** This plan expects **zero migrations**: `ModelConnection.config` (D8) already exists and carries the family/companions, and `GenerationJob` already records `engine`/`model_id`/`endpoint`/`model_fingerprint`/`model_config` — the full identity of the model that ran. If a step appears to need a migration, STOP and report rather than generating one.
- **Branch-only.** No commits to `main`, no merges to `main`, no deploy from this plan. **Merge `main` into `vision-generation` BEFORE Task 1** — the branch is 12 commits behind and main's T9 changed `DiscoveryRow` (now carries a full `capabilities` tuple) and `machine_model_add`/`connection_add` (now build a `capability_list`). Every line number and snippet below is written against the POST-MERGE tree.
- **Baseline:** re-measured post-merge (Task 0, 2026-08-24) at `2149 passed / 1 skipped`, both orders (`pytest -q` and `pytest console modules scripts -q`) — use THAT number as the floor.
- **Commit trailers.** Every commit ends with the two standard trailers:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  ```
- **Verification doctrine.** No "done"/"works"/"fixed" language about the feature until fresh pixels have been seen on the owner's live system. The two live verification steps in Tasks 6 and 7 are evidence-gathering, not completion claims.

---

## Spec (owner's requirements)

### From `memory-governance.md` — "Model decision (owner, 2026-08-25)", verbatim

> - Keep [the ordinary build]'s Q4_K_S GGUF (models/unet) and swap its text encoder to a GGUF Q4 [small instruct encoder] (community conversion, ~14 GB) — fp8 encoder deleted (owner-approved). Pipeline peak ~32 GB: Chrome closed + preview stack down during [ordinary-build] runs; RAG LLM must be evicted first (seams enforce once landed).
> - Keep [the edit-only family]'s Q5_K_M build as the complementary model (text-in-image edits, compound instructions, faster). Owner picks per generation via the model picker (EDIT plan).
> - Sequencing: governance seams plan → verified via a GOVERNED queue job → then EDIT feature. First [ordinary-build] load happens only through the queue.

### The picker requirement (owner, binding)

> Vision works like the RAG LLMs — the role binding is the DEFAULT model; the generation form offers a per-generation picker of other REGISTERED+ACTIVE image-generation connections (same grammar as the Ask-time picker; reuse `console.inference.bindings.resolve_connection` — no second override mechanism); model-family → graph-template dispatch; the operation chooser reflects what the SELECTED model supports.

### Installed files (verified on disk, 2026-08-25)

```
ComfyUI/models/unet/flux2-dev-Q4_K_S.gguf                     19,299,128,288 bytes
ComfyUI/models/unet/qwen-image-edit-2511-Q5_K_M.gguf          15,027,501,664 bytes
ComfyUI/models/vae/flux2-vae.safetensors                         336,213,556 bytes
ComfyUI/models/vae/qwen_image_vae.safetensors                    253,806,246 bytes
ComfyUI/models/text_encoders/Mistral-Small-3.2-24B-Instruct-2506-Q4_K_M.gguf  14,333,922,848 bytes
ComfyUI/models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors             9,384,670,680 bytes
```

The encoder swap the owner required is **done**: `models/text_encoders/`
holds the Q4_K_M GGUF instruct encoder and the vision-language encoder, and
the 18 GB fp8 encoder is deleted. That is what makes the owner's ~32 GB pipeline
peak reachable — an fp8 encoder upcasts to bf16 on MPS and would blow it.

Nothing in this plan bakes an encoder filename anywhere: the operator picks
the encoder at registration time from the engine's own list, so a later
re-quantization or a third encoder needs no code change.

### Loader nodes, verified read-only against the live engine (2026-08-25)

```
GET /object_info/UnetLoaderGGUF
  {"UnetLoaderGGUF": {"input": {"required": {"unet_name": [["flux2-dev-Q4_K_S.gguf",
    "qwen-image-edit-2511-Q5_K_M.gguf"]]}}, "output": ["MODEL"],
    "python_module": "custom_nodes.ComfyUI-GGUF"}}

GET /object_info/CLIPLoaderGGUF
  required: clip_name -> [[<every file in models/text_encoders, .gguf and .safetensors alike>]]
            type      -> [["stable_diffusion", ..., "qwen_image", ..., "flux2", ...]]
  output: ["CLIP"]        (NOTE: no `device` input, unlike the built-in CLIPLoader)

GET /object_info/UNETLoader     (built-in)
  required: unet_name -> [[]]          <- empty: the native loader does not list .gguf files
            weight_dtype -> [["default", "fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2"]]

GET /object_info/CLIPLoader     (built-in)
  required: clip_name -> [[...]]  type -> [[... 28 values incl. "flux2", "qwen_image" ...]]
  optional: device -> [["default", "cpu"]]

GET /object_info/NoSuchNodeXYZ  ->  HTTP 200, body `{}`
```

That last line matters: **a live ComfyUI answers 200 `{}` for a node this
build does not have.** `_combo_values` therefore already returns `()` for an
absent custom-node pack with no new error handling — but the `FakeComfyUI`
double answers **404** for an unknown node, which does NOT match the live
server and would make `list_installed` raise. Task 1 fixes the double.

**Combo shape:** `UnetLoaderGGUF.unet_name` answers in the LEGACY shape
(`[[...values...]]`, a one-element list with no options dict).
`_combo_values` reads `spec[0]`, sees a list, and returns it — already
correct, and `FakeComfyUI(combo_shape="legacy")` already covers that path.

### The two graph templates, read from ComfyUI 0.33.0's own bundle

Source: `<home>/ComfyUI/.venv/lib/python3.12/site-packages/comfyui_workflow_templates_json/templates/`
(the `comfyui_workflow_templates` meta-package no longer exposes a
`templates/` directory; the JSON bundle package is where the files live).

- `image_flux2_fp8.json` → subgraph `"Image Edit (Flux.2 Dev)"` (two reference images)
- `image_flux2.json` → subgraph of the same name (one reference image)
- `image_qwen_image_edit_2511.json` → subgraph `"Image Edit (Qwen-Image 2511)"`

Both bundled workflows wrap an optional distillation-LoRA behind
`ComfySwitchNode`/`PrimitiveInt`/`PrimitiveBoolean` toggles. **This plan
drops that machinery**: it is a UI convenience for a LoRA the platform does
not ship, `LoraLoaderModelOnly` is not `_fragments._lora_chain`'s
`LoraLoader`, and the `edit` operation declares no LoRA param (see Task 5).
Everything else is reproduced node-for-node.

#### Template specification — `flux2` edit

Node classes and their exact `/object_info` input names:

| Node | Inputs used | Output |
| --- | --- | --- |
| `UnetLoaderGGUF` | `unet_name` | MODEL |
| `UNETLoader` (non-`.gguf` fallback) | `unet_name`, `weight_dtype` | MODEL |
| `CLIPLoaderGGUF` | `clip_name`, `type` | CLIP |
| `CLIPLoader` (non-`.gguf` fallback) | `clip_name`, `type`, `device` | CLIP |
| `VAELoader` | `vae_name` | VAE |
| `LoadImage` | `image` | IMAGE, MASK |
| `ImageScaleToTotalPixels` | `image`, `upscale_method`, `megapixels`, `resolution_steps` | IMAGE |
| `GetImageSize` | `image` | INT (width), INT (height), INT (batch) |
| `CLIPTextEncode` | `text`, `clip` | CONDITIONING |
| `FluxGuidance` | `conditioning`, `guidance` | CONDITIONING |
| `VAEEncode` | `pixels`, `vae` | LATENT |
| `ReferenceLatent` | `conditioning`, `latent` (optional) | CONDITIONING |
| `EmptyFlux2LatentImage` | `width`, `height`, `batch_size` (fixed `1`) | LATENT |
| `Flux2Scheduler` | `steps`, `width`, `height` | SIGMAS |
| `KSamplerSelect` | `sampler_name` | SAMPLER |
| `RandomNoise` | `noise_seed` | NOISE |
| `BasicGuider` | `model`, `conditioning` | GUIDER |
| `SamplerCustomAdvanced` | `noise`, `guider`, `sampler`, `sigmas`, `latent_image` | LATENT, LATENT |
| `VAEDecode` | `samples`, `vae` | IMAGE |
| `SaveImage` | `images`, `filename_prefix` | — |

Wiring (from the subgraph's own link list):

```
unet ─────────────────────────────────────────────► BasicGuider.model
clip ──► CLIPTextEncode.clip
instruction ──► CLIPTextEncode.text ──► FluxGuidance.conditioning
FluxGuidance ──► ReferenceLatent#1.conditioning ──► ReferenceLatent#2.conditioning ──► BasicGuider.conditioning
init_image  ──► LoadImage ──► ImageScaleToTotalPixels ──┬─► VAEEncode ──► ReferenceLatent#1.latent
                                                        └─► GetImageSize ─┬─► EmptyFlux2LatentImage.width/height
                                                                          └─► Flux2Scheduler.width/height
reference_image (optional) ──► LoadImage ──► ImageScaleToTotalPixels ──► VAEEncode ──► ReferenceLatent#2.latent
Flux2Scheduler(steps, w, h) ──► SamplerCustomAdvanced.sigmas
KSamplerSelect("euler")     ──► SamplerCustomAdvanced.sampler
RandomNoise(seed)           ──► SamplerCustomAdvanced.noise
EmptyFlux2LatentImage(w, h, 1)     ──► SamplerCustomAdvanced.latent_image
SamplerCustomAdvanced.output ──► VAEDecode.samples ──► SaveImage.images
vae ──► VAEEncode.vae (each), VAEDecode.vae
```

Chain order is the reference NUMBERING the instruction can refer to: the
bundled two-image workflow's prompt reads *"Apply the design from Reference
Image 1 onto objects in Reference Image 2"*, and Reference Image 1 is the
image whose `ReferenceLatent` comes FIRST in the chain and whose scaled
pixels feed `GetImageSize`. So `init_image` is first (and owns the output
size); `reference_image` is second.

Fixed graph facts (not operator params, because only one family honours
them): `upscale_method="area"`, `megapixels=1.0`, `resolution_steps=1`,
`sampler_name="euler"`, `weight_dtype="default"`, `device="default"`.

#### Template specification — `qwen_image` edit

| Node | Inputs used | Output |
| --- | --- | --- |
| `UnetLoaderGGUF` / `UNETLoader` | as above | MODEL |
| `ModelSamplingAuraFlow` | `model`, `shift` | MODEL |
| `CFGNorm` | `model`, `strength` (`pre_cfg` optional) | MODEL |
| `CLIPLoaderGGUF` / `CLIPLoader` | as above | CLIP |
| `VAELoader` | `vae_name` | VAE |
| `LoadImage` | `image` | IMAGE, MASK |
| `FluxKontextImageScale` | `image` | IMAGE |
| `TextEncodeQwenImageEditPlus` | `clip`, `prompt`; optional `vae`, `image1`, `image2`, `image3` | CONDITIONING |
| `FluxKontextMultiReferenceLatentMethod` | `conditioning`, `reference_latents_method` | CONDITIONING |
| `VAEEncode` | `pixels`, `vae` | LATENT |
| `KSampler` | `model`, `seed`, `steps`, `cfg`, `sampler_name`, `scheduler`, `positive`, `negative`, `latent_image`, `denoise` | LATENT |
| `VAEDecode`, `SaveImage` | as above | — |

The bundled subgraph's COMPLETE node-class list, so nothing can be invented
into this graph: `CFGNorm`, `CLIPLoader`, `ComfySwitchNode`,
`FluxKontextImageScale`, `FluxKontextMultiReferenceLatentMethod`, `KSampler`,
`LoraLoaderModelOnly`, `ModelSamplingAuraFlow`, `TextEncodeQwenImageEditPlus`,
`UNETLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`. There is **no**
`RepeatLatentBatch` and no batching node of any kind — which is why `edit`
declares no `batch_size` (R1: one output per edit). The `ComfySwitchNode` /
`LoraLoaderModelOnly` cluster is the distillation-LoRA toggle this plan
drops.

Wiring:

```
unet ──► ModelSamplingAuraFlow(shift=3.1) ──► CFGNorm(strength=1.0) ──► KSampler.model
init_image ──► LoadImage ──► FluxKontextImageScale ─┬─► TextEncodeQwenImageEditPlus(+).image1
                                                    ├─► TextEncodeQwenImageEditPlus(−).image1
                                                    └─► VAEEncode ──► KSampler.latent_image
reference_image (optional, UNSCALED per the bundled workflow) ──► both TextEncode nodes' image2
instruction ──► TextEncodeQwenImageEditPlus(+).prompt        (the NEGATIVE node's prompt is "")
TextEncode(+) ──► FluxKontextMultiReferenceLatentMethod ──► KSampler.positive
TextEncode(−) ──► FluxKontextMultiReferenceLatentMethod ──► KSampler.negative
KSampler ──► VAEDecode ──► SaveImage
```

Fixed graph facts: `shift=3.1`, `strength=1.0`,
`reference_latents_method="index_timestep_zero"`, `sampler_name="euler"`,
`scheduler="simple"`, `denoise=1.0`.

---

## Design decisions, stated explicitly

### D-EDIT-1 — ONE `edit` operation, dispatched by family

**Decision:** a single `edit` operation (instruction + one required image +
one optional reference image), dispatched to a family-specific graph
template. NOT one operation per family.

**Why:** per-family operations would put model-family identity into
`core/inference/operations.py`, which ships no model names and no engine
vocabulary; they would multiply with every model the owner adds; and they
would show the operator two chooser entries for one intent. The operator's
question is "edit this image with this instruction", and which model answers
it is the picker's job — exactly the split D4/D5 already draw between a role
binding and an operation.

**Honesty rule this forces:** `edit` may declare ONLY params both graphs
genuinely wire. Concretely it declares `instruction`, `init_image`,
`reference_image`, `guidance`, `steps`, and `seed` — and nothing else:

| Param | `flux2` wiring | `qwen_image` wiring | Verdict |
| --- | --- | --- | --- |
| `instruction` | `CLIPTextEncode.text` | `TextEncodeQwenImageEditPlus.prompt` | honest |
| `init_image` | `LoadImage` → scale → ReferenceLatent#1 + GetImageSize | `LoadImage` → `FluxKontextImageScale` → image1 + VAEEncode | honest |
| `reference_image` | second ReferenceLatent | `image2` on both TextEncode nodes | honest |
| `guidance` | `FluxGuidance.guidance` | `KSampler.cfg` | honest — both are the instruction-adherence knob, both ship `4` as the bundled default |
| `steps` | `Flux2Scheduler.steps` | `KSampler.steps` | honest |
| `seed` | `RandomNoise.noise_seed` | `KSampler.seed` | honest |
| `batch_size` | `EmptyFlux2LatentImage.batch_size`, pinned at `1` | the bundled workflow has no batching node at all | **excluded** — R1: one output per edit |
| `negative_prompt` | no negative path at all (BasicGuider takes one conditioning) | the negative TextEncode's prompt is fixed `""` in the bundled workflow | **excluded** |
| `sampler` / `scheduler` | `KSamplerSelect` is fixed `euler`; the schedule is `Flux2Scheduler`, which has no scheduler combo | `euler`/`simple`, fixed | **excluded** |
| `width` / `height` | derived from the input image via `GetImageSize` | derived by `FluxKontextImageScale` | **excluded** — inventing a size would rescale the operator's picture |
| `denoise` | no denoise input on `SamplerCustomAdvanced` | `KSampler.denoise`, fixed `1.0` | **excluded** |
| `megapixels` | `ImageScaleToTotalPixels.megapixels` | `FluxKontextImageScale` has no such input | **excluded** |
| `loras` | `LoraLoaderModelOnly` (model-only, not `_lora_chain`'s `LoraLoader`) | same | **excluded** — a second axis, out of scope, and `_fragments._lora_chain` cannot serve it |

Every exclusion above is a param the platform will NOT render rather than a
param one of the two models would silently ignore.

### D-EDIT-2 — Family is declared by the operator, stored on the connection

**Decision:** the model family is an **operator declaration** made at
registration/edit time and stored in `ModelConnection.config["family"]`. The
vocabulary of families offered is **read live from the engine**
(`/object_info/CLIPLoader` → `type` combo), intersected with the families
the adapter has a template for.

**Why not derive it from the engine?** Verified read-only on 2026-08-25:
`GET /object_info/UnetLoaderGGUF` returns nothing but a filename list. The
ComfyUI-GGUF pack exposes no GGUF metadata over HTTP — no `general.architecture`
field, no per-file introspection endpoint. There is **no observable engine
fact** that distinguishes one family's GGUF unet from the other's.

**Why not name-match?** Forbidden, and rightly: it would require shipping a
list of model names and would silently mis-dispatch any file the operator
renamed.

**What IS derived from an observable engine fact:** *which loader reported
the model.* `list_installed` tags each `InstalledModel` with the node it came
from (`CheckpointLoaderSimple` vs `UnetLoaderGGUF` vs `UNETLoader`). That is
a loader fact, not a family fact, and its one job is to decide whether the
console must ASK for a family and companions at all: a single-file checkpoint
needs neither, a diffusion-model file needs both.

**Where the family literals live:** `core/inference/engines/comfyui_workflows/__init__.py`,
as the keys of `_TEMPLATES`. They are ComfyUI's own `CLIPLoader.type` strings,
they are what a `CLIPLoader`/`CLIPLoaderGGUF` node is actually given as its
`type` input, and ADR 0012 D1 already designates that package as the one
place ComfyUI's graph vocabulary exists. No `core/` module outside that
package, no `console/` module, no template, and no UI string contains them.

### D-EDIT-3 — Companions are picked by the operator from the engine's own lists

`config["text_encoder"]` and `config["vae"]` are chosen at registration from
`ComfyUIEngine.list_assets(endpoint, "text_encoder")` and
`list_assets(endpoint, "vae")`. No filename is ever baked, defaulted, or
guessed. Auto-selecting a companion "from the family" was rejected: it would
mean shipping a family→filename map, i.e. baked model names.

**Which loader node reads a companion** is decided by the file's own
extension — `.gguf` → `CLIPLoaderGGUF`, otherwise `CLIPLoader`; likewise
`UnetLoaderGGUF` vs `UNETLoader` for the diffusion model. That is a fact of
the file the operator named, not a pattern match against a shipped list, and
it lives in `_fragments.py` where the rest of ComfyUI's node vocabulary does.

### D-EDIT-4 — The picker carries a connection pk in the queue payload

`payload["connection"]` is the chosen `ModelConnection`'s pk **as a string**
— byte-identical to `rag.ask`'s field, resolved through the same
`console.inference.bindings.resolve_connection`. Absent or blank means "use
the role binding", which is exactly today's behaviour. There is no second
override mechanism: no new column, no session key, no per-operation binding.

**No migration.** `GenerationJob` already records `engine`, `model_id`,
`endpoint`, `model_fingerprint`, and `model_config` — the complete identity
of the model that ran, which is what D6 promises and what the card and the
gallery already read. A `connection_id` column would add only the console
row's pk, which is display sugar and goes stale the moment the connection is
deleted. Rejected alternative recorded here so it is not re-litigated.

### D-EDIT-5 — The template registry is keyed by `(family, operation)`

`_TEMPLATES: dict[tuple[str, str], Template]`. The four existing
checkpoint-based templates key on the **empty family** `""` — "no family
declared", which is exactly what a single-file checkpoint connection has.
`supported_operations(model_id, endpoint, family="")` returns the operation
keys registered for that family, so:

- a checkpoint connection (`config` has no `family`) offers `txt2img`,
  `img2img`, `inpaint`, `upscale` — unchanged;
- a `flux2` or `qwen_image` connection offers `edit` — and nothing else,
  because neither graph is a checkpoint graph and offering `txt2img` for
  them would be a lie;
- a connection whose declared family has no template offers nothing, and the
  page says so honestly.

ADR 0012's derivation honesty is preserved: `supported_operations` still
reads the registry and never a hand-maintained tuple. `template_keys()`
keeps its name and gains an optional `family` argument.

---

## File Structure

| File | Responsibility after this plan |
| --- | --- |
| `core/inference/engines/base.py` | `InstalledModel.loader` (new, defaulted); `supported_operations(..., family="")` and `list_families(endpoint)` documented on the `InferenceEngine` protocol as optional members. |
| `core/inference/engines/comfyui.py` | `_MODEL_NODES` (three loader nodes); `list_installed` reports every one, tagged with its loader; `_ASSET_NODES["text_encoder"]` (union of the two CLIP loaders); `list_families`; family-aware `supported_operations`. |
| `core/inference/engines/comfyui_workflows/__init__.py` | `(family, operation)`-keyed `_TEMPLATES`; `get_template(op, family="")`; `template_keys(family="")`; `families()`. |
| `core/inference/engines/comfyui_workflows/_fragments.py` | ONLY the genuinely shared vocabulary: `_declared()`, `_diffusion_model()`, `_text_encoder()`, `components()` (the three separate loaders as one `Checkpoint`), `encode_text()`, `scale_to_megapixels()`, `image_size()`. Family-specific wiring lives in that family's own template module, not here. |
| `core/inference/engines/comfyui_workflows/flux2_edit.py` | **New.** The `("flux2", "edit")` graph, plus its own `_flux_guidance()`, `_reference_latent()`, `_empty_latent()`, `_sigmas()`, `_sample()`. |
| `core/inference/engines/comfyui_workflows/qwen_edit.py` | **New.** The `("qwen_image", "edit")` graph, plus its own `_kontext_scale()`, `_model_chain()`, `_conditioning()`, `_sample()`. |
| `core/inference/operations.py` | `EDIT` operation definition. (`ASSET_KINDS` is deliberately untouched — see Task 3.) |
| `modules/vision/apps.py` | Registers `EDIT` alongside the four existing operations. |
| `modules/vision/services.py` | `_health_check(resolved)`; `preflight(resolved=None)` delegates to it; `submit_job(..., resolved=None)`; `operations_for_model(resolved)`; `operation_catalog(resolved=None)`. |
| `modules/vision/jobs.py` | `plan_generate`/`run_generate` honour `payload["connection"]`. |
| `modules/vision/views.py` | `picked_connection(request)`, `_connection_picker_options(selected)`; model-aware `page_operations(resolved)`/`resolve_page_operation(key, available)`; `connection` carried into the payload and into every chooser link. |
| `modules/vision/templates/vision/create.html` | The model `<select>` in its OWN sibling GET form (never inside the multipart generate form — that would drop the attached file), a hidden `connection` field inside the generate form, optional auto-submit, and the model-aware operation chooser. |
| `console/inference/discovery.py` | `DiscoveryRow.loader`, carried from `InstalledModel`. |
| `console/inference/bindings.py` | `role_primary(role_key)` (generalized from `answer_role_primary`); `connections_for_picker(capability)` (generalized from `chat_connections_for_picker`). |
| `console/inference/views.py` | `connection_add` reads/writes `family`, `text_encoder`, `vae`; `machine_model_add` refuses a companions-needing loader and points at the manual form; the manual form's context carries the engine's family/companion option lists. |
| `console/inference/templates/console/inference.html` | Family + companion selects on the manual form; the loader-aware machine row. |
| `modules/vision/tests/_helpers.py` | `FakeComfyUI` gains `unets_gguf`, `unets`, `text_encoders`, `families`; unknown-node responses become `200 {}` to match the live server. |
| `docs/adr/0012-image-generation-engine-adapter.md` | Amendment: D-EDIT-1..5. |
| `modules/vision/README.md` | The `edit` operation, family dispatch, the picker. |
| `console/inference/README.md` | Registering a multi-file model family. |

---

## Dependency Table (strictly sequential — execute in this order)

| Task | Depends on | Why this order |
| --- | --- | --- |
| 1 | merge of `main`; the memory-seams plan having landed | A GGUF unet must be discoverable before anything can register or bind one. Touches `InstalledModel`, so it must precede every consumer. |
| 2 | 1 | The registry must be rekeyed (and `families()` must exist) before the adapter can intersect the engine's family vocabulary with "families we actually have a graph for". Rekeying is pure refactor at this point — no family key exists yet. |
| 3 | 2 | `list_families` intersects the engine's `CLIPLoader.type` combo with T2's `families()`; the fixed `FakeComfyUI` unknown-node behaviour from T1 is what lets an absent `CLIPLoaderGGUF` degrade to nothing. |
| 4 | 3 | The registration form's option lists come from T3's `list_families` / `list_assets("text_encoder")`. It deliberately does NOT validate the stored family against them (see D-EDIT-2 note in Task 4) — which is what breaks the otherwise-circular dependency with the template tasks. |
| 5 | 4 | `EDIT`'s registration is only honest once `supported_operations` can report it per family — otherwise a checkpoint connection would offer `edit`. |
| 6 | 5 | The `("flux2", "edit")` template needs the `edit` schema to read params from and the `(family, op)` registry to be registered in. |
| 7 | 6 | The `("qwen_image", "edit")` template reuses `_fragments` helpers T6 introduces; and only ONE model may be loaded at a time for live verification. |
| 8 | 7 | Console-side picker sources. Independent of the templates but placed after them so the first live evidence exists before UI work begins. |
| 9 | 8 | The service/jobs plumbing resolves through T8's `connections_for_picker(capability)`/`role_primary(role_key)`. |
| 10 | 9 | The model-aware catalog needs a *selected* model, which only T9's `resolved=` seam provides. |
| 11 | 10 | The page renders the picker and the filtered chooser; both are already true underneath by then. |
| 12 | 11 | Docs describe finished behaviour; the full both-order suite is the last gate. |

---

## Task 0 (prerequisite, not a code task)

- [ ] Confirm `docs/superpowers/plans/2026-08-25-comfyui-memory-seams.md` has LANDED on this branch and its `ComfyUIEngine.loaded_footprint` / `ComfyUIEngine.unload` exist. If it has not, STOP and report — this plan executes after it.
- [ ] `git merge main` into `vision-generation` and resolve. Confirm `DiscoveryRow` carries a `capabilities` tuple and `console/inference/views.py` builds a `capability_list` (main's T9). If those are absent, the merge did not take.
- [ ] Confirm main's **T9.5** landed: `core.inference.jobkinds.JobKind` gains an OPTIONAL `on_terminal` member. `vision.generate` leaves it `None` — this plan registers no terminal hook and **must not edit `core/inference/jobkinds.py`** (queue track owns it). If `JobKind` construction in `modules/vision/apps.py` now fails for a missing argument, the field is not optional and that is a STOP-and-report, not something to work around.
- [ ] Re-measure the baseline, both orders, and write the two numbers into this file's Global Constraints before Task 1:
  ```
  DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest -q
  DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest -q -p no:randomly
  ```
- [ ] `ls -la <home>/ComfyUI/models/text_encoders/` and confirm the listing still matches the "Installed files" block above (the encoder swap is done; this is a re-confirmation, not a wait).

---

### Task 1: A diffusion-model file is discoverable, tagged with the loader that found it

**Files:**
- Modify: `core/inference/engines/base.py` (`InstalledModel`)
- Modify: `core/inference/engines/comfyui.py` (`_CHECKPOINT_NODE` → `_MODEL_NODES`, `list_installed`)
- Modify: `console/inference/discovery.py` (`DiscoveryRow.loader`, the installed merge)
- Modify: `modules/vision/tests/_helpers.py` (`FakeComfyUI`: unknown node → `200 {}`, plus `unets_gguf`/`unets`/`missing_nodes`)
- Test: `modules/vision/tests/test_comfyui_engine.py`, `console/inference/tests/test_discovery.py`
- Docs: `modules/vision/README.md`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `InstalledModel.loader: str` (the engine-shaped node name that reported the model, `""` when the adapter doesn't say); `DiscoveryRow.loader: str`; `core.inference.engines.comfyui._MODEL_NODES: tuple[tuple[str, str], ...]`; `FakeComfyUI(unets_gguf=..., unets=..., missing_nodes=...)`.
- **Overlaps the memory-seams plan's Task 2, which already rewrote `list_installed` (landed at `6f435f9`).** That task gave `list_installed` a run-memo residency read: it sets `loaded` / `loaded_size` from `_run_memo(endpoint)` and believes at most ONE resident checkpoint per endpoint. Step 5 quotes that real post-seams body and widens it. Three consequences, all mandatory:
  - Step 5 EXTENDS the landed function; it does not replace it. Keep every residency line, and keep the `memo` read ONCE before the loop — never per row.
  - The three new tests must ALSO assert `loaded is False` for the rows that are not the believed-resident one, so this task cannot silently drop residency reporting.
  - The loader dedupe (`seen`) must not shadow the memo's single-resident belief: a file reported by two loaders collapses to one row, and that row must carry the residency the memo believes for that model id — never a second row's `loaded=False` overwriting it.

- [ ] **Step 1: Fix the test double so an unknown node answers like the live server**

A live ComfyUI 0.33.0 answers `200 {}` for a node it does not have (verified
read-only, 2026-08-25). The double answers **404**, which would make a
three-node `list_installed` raise on any install without ComfyUI-GGUF.

**Re-read `FakeComfyUI.get` before editing it.** The memory-seams work is
landing in this same file and this same class, so its `object_info` branch
may not look the way it did when this plan was written — at plan time there
was no `missing_nodes` guard at all, and `/system_stats` already reported
`ram_free` while the seams tasks add `vram_free` and a `POST /free` handler.
Do not paste a verbatim replacement.

The change is exactly two things, applied to whatever the branch's current
`object_info` handling is:

1. Whatever the branch's `object_info` FALLTHROUGH then is (a 404 `_Response`
   at plan time), make it `return _Response(payload={})`, with this comment:
   ```python
            # A live ComfyUI answers 200 with an EMPTY body for a node this
            # build does not have (verified against 0.33.0), not a 404 --
            # which is exactly how an install without the ComfyUI-GGUF
            # custom-node pack reports its absent loaders. `_combo_values`
            # reads that as `()`; a double that 404s here would make the
            # adapter raise where the real server does not.
   ```
2. Gate the `_NODE_INPUTS` lookup on the new `missing_nodes` field, so a test
   can say "this build does not have that node" explicitly:
   ```python
            if node in self._NODE_INPUTS and node not in self.missing_nodes:
   ```

Then add the three new fields beside `checkpoints`:

```python
    checkpoints: tuple[str, ...] = ()
    # Diffusion-model files, listed by their own loader nodes: ComfyUI-GGUF's
    # `UnetLoaderGGUF` and the built-in `UNETLoader`. Separate fields because
    # they are separate nodes on the real server -- a build without the
    # custom-node pack reports the second and not the first.
    unets_gguf: tuple[str, ...] = ()
    unets: tuple[str, ...] = ()
    # Nodes this fake server does NOT have: it answers `200 {}` for each,
    # exactly as a live ComfyUI does for a node no installed pack provides.
    missing_nodes: tuple[str, ...] = ()
```

and the two loader rows in `_NODE_INPUTS`:

```python
    _NODE_INPUTS = {
        "CheckpointLoaderSimple": ("checkpoints", "ckpt_name"),
        "UnetLoaderGGUF": ("unets_gguf", "unet_name"),
        "UNETLoader": ("unets", "unet_name"),
        "LoraLoader": ("loras", "lora_name"),
        "VAELoader": ("vaes", "vae_name"),
        "ControlNetLoader": ("controlnets", "control_net_name"),
        "UpscaleModelLoader": ("upscalers", "model_name"),
    }
```

- [ ] **Step 2: Write the failing tests**

Append to `modules/vision/tests/test_comfyui_engine.py`'s `TestListInstalled`:

```python
    def test_gguf_and_native_unets_are_listed_beside_checkpoints(self):
        """A multi-file family's diffusion-model file is a model this engine
        can run, so it belongs in the listing next to a checkpoint -- tagged
        with the loader node that reported it, which is the ONE observable
        engine fact about it."""
        fake = FakeComfyUI(
            checkpoints=("sdxl.safetensors",),
            unets_gguf=("family-a-Q4_K_S.gguf",),
            unets=("family-b.safetensors",),
        )
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)

        assert [(m.model_id, m.loader) for m in installed] == [
            ("sdxl.safetensors", "CheckpointLoaderSimple"),
            ("family-a-Q4_K_S.gguf", "UnetLoaderGGUF"),
            ("family-b.safetensors", "UNETLoader"),
        ]
        assert all(m.capabilities == ("image-generation",) for m in installed)
        # Residency is the run memo's to report (memory-seams Task 2), and
        # nothing has run here -- so every row is honestly not resident.
        # Asserted so widening the listing cannot silently drop the
        # residency reporting that task added.
        assert all(m.loaded is False and m.loaded_size is None for m in installed)

    def test_an_absent_loader_node_reports_nothing_and_never_raises(self):
        """An install without the ComfyUI-GGUF pack has no `UnetLoaderGGUF`;
        the live server answers 200 with an empty body for it. The listing
        must lose that one node's models and keep every other node's."""
        fake = FakeComfyUI(
            checkpoints=("sdxl.safetensors",),
            unets_gguf=("never-seen.gguf",),
            missing_nodes=("UnetLoaderGGUF",),
        )
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)

        assert [m.model_id for m in installed] == ["sdxl.safetensors"]
        assert installed[0].loaded is False

    def test_the_same_file_reported_by_two_loaders_is_one_model(self):
        """Two loader nodes can see the same file. It is one model, and the
        FIRST loader to report it owns the row -- never a duplicate the
        console would render twice."""
        fake = FakeComfyUI(unets_gguf=("shared.gguf",), unets=("shared.gguf",))
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)

        assert [(m.model_id, m.loader) for m in installed] == [
            ("shared.gguf", "UnetLoaderGGUF")
        ]
        # The dedupe keeps ONE row; it must not let the second loader's
        # sighting overwrite what the run memo believes about residency for
        # that model id (memory-seams Task 2's single-resident belief).
        assert installed[0].loaded is False
```

Append to `console/inference/tests/test_discovery.py` (import `InstalledModel`
and use whatever stub-engine idiom that module already uses; the assertion is
the point):

```python
    def test_a_row_carries_the_loader_that_reported_the_model(self):
        """The console must be able to tell a single-file checkpoint from a
        diffusion-model file that needs companions, and the loader node is
        the only honest signal for that."""
        engine = _StubEngine(
            name="comfyui",
            installed=[
                InstalledModel(
                    model_id="family-a-Q4_K_S.gguf",
                    capabilities=("image-generation",),
                    loader="UnetLoaderGGUF",
                )
            ],
        )
        with patch.dict(ENGINES, {"comfyui": engine}, clear=True):
            rows = discover({"comfyui": ["http://comfy.test:8188"]}, [])

        assert rows[0].loader == "UnetLoaderGGUF"

    def test_an_adapter_that_reports_no_loader_degrades_to_blank(self):
        """`loader` is read with `getattr`, like `loaded_size` before it, so
        a third-party adapter that predates the field is never an
        AttributeError."""
        engine = _StubEngine(
            name="comfyui",
            installed=[InstalledModel(model_id="sdxl.safetensors", capabilities=("image-generation",))],
        )
        with patch.dict(ENGINES, {"comfyui": engine}, clear=True):
            rows = discover({"comfyui": ["http://comfy.test:8188"]}, [])

        assert rows[0].loader == ""
```

- [ ] **Step 3: Run them to verify they fail**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_engine.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console/inference/tests/test_discovery.py -q
```
Expected: FAIL — `TypeError: InstalledModel.__init__() got an unexpected keyword argument 'loader'`.

- [ ] **Step 4: Add the field to `InstalledModel`**

In `core/inference/engines/base.py`, after `embedding_length`:

```python
    embedding_length: int | None = None  # raw engine-reported hidden/embedding size
    # Which of the engine's OWN loader nodes reported this model -- an
    # opaque, engine-shaped string (ComfyUI: "CheckpointLoaderSimple" vs
    # "UnetLoaderGGUF" vs "UNETLoader"). The one observable fact that
    # separates a self-contained checkpoint from a diffusion-model file
    # that needs companion encoder/VAE files, which is what the console
    # needs in order to know whether registration must ask for them. It is
    # NOT a model family and must never be treated as one -- no HTTP
    # surface on ComfyUI reports a GGUF file's architecture (verified
    # 2026-08-25), so the family is an operator declaration (ADR 0012
    # D-EDIT-2). Read downstream with `getattr(..., "loader", "")` so an
    # adapter predating this field degrades to blank, exactly like
    # `loaded_size` above.
    loader: str = ""
```

- [ ] **Step 5: List every loader node in the ComfyUI adapter**

**Read `list_installed` as it stands on the branch first.** The body quoted
below is the real post-seams one as of `6f435f9`; if the seams plan has moved
again since, re-derive against what is actually there. The change is:
iterate `_MODEL_NODES` instead of one checkpoint node, dedupe by model id,
tag each row with the loader that reported it — applied ON TOP of the
residency logic, never in place of it.

In `core/inference/engines/comfyui.py`, replace the `_CHECKPOINT_NODE`
constant — **line 87** as of `6f435f9`, which reads:

```python
# The node whose `ckpt_name` combo IS the installed-checkpoint list.
_CHECKPOINT_NODE = ("CheckpointLoaderSimple", "ckpt_name")
```

with:

```python
# Every node whose combo IS a list of models this engine can run, in the
# order a listing reports them, and the input that holds the list.
#
# A single-file checkpoint (`CheckpointLoaderSimple`) is self-contained. The
# other two report a DIFFUSION MODEL file, which is only half a pipeline:
# it needs a text encoder and a VAE named alongside it on the connection
# (`ModelConnection.config`, D8). Which of the two reported a model is
# recorded on `InstalledModel.loader` so the console can tell those two
# cases apart without guessing from a filename.
#
# `UnetLoaderGGUF` belongs to the ComfyUI-GGUF custom-node pack. An install
# without it answers `/object_info/UnetLoaderGGUF` with `200 {}` (verified
# against a live 0.33.0), which `_combo_values` already reads as `()` --
# an absent pack costs this listing nothing and raises nothing.
_MODEL_NODES: tuple[tuple[str, str], ...] = (
    ("CheckpointLoaderSimple", "ckpt_name"),
    ("UnetLoaderGGUF", "unet_name"),
    ("UNETLoader", "unet_name"),
)
```

and reshape `list_installed` — **line 440** as of `6f435f9`. That commit
(memory-seams Task 2) already rewrote this function to report run-memo
residency, and it now reads:

```python
    def list_installed(self, endpoint: str) -> list[InstalledModel]:
        """Every checkpoint ComfyUI can load at `endpoint`.
        ... (docstring continues; see the file)
        """
        node, input_key = _CHECKPOINT_NODE
        memo = _run_memo(endpoint)
        return [
            InstalledModel(
                model_id=name,
                capabilities=("image-generation",),
                loaded=memo is not None and memo.model_key == norm_tag(name),
                loaded_size=(
                    memo.footprint
                    if memo is not None and memo.model_key == norm_tag(name)
                    else None
                ),
            )
            for name in _combo_values(endpoint, node, input_key)
        ]
```

Widen it to every loader node WITHOUT losing a single residency line. The
comprehension becomes a loop (it has to — the dedupe needs state), and the
`memo` read stays exactly where it is: ONCE, before the loop, never per row.
`norm_tag` is already imported in this module by the seams work; confirm that
before relying on it:

```python
    def list_installed(self, endpoint: str) -> list[InstalledModel]:
        """Every model ComfyUI can load at `endpoint`: checkpoints AND the
        diffusion-model files a multi-file family is built from.

        `model_id` is ComfyUI's exact string and is OPAQUE -- a Windows host
        reports `subdir\\file.safetensors`, which must go back unchanged as
        the loader's own name input. ComfyUI reports no size for a model on
        disk, so `size` stays `None` rather than being guessed.

        `loaded`/`loaded_size` are the RUN MEMO's belief, unchanged from
        what memory-seams Task 2 established here: the memo is read once for
        the whole listing, and a row is resident exactly when its
        `norm_tag`-normalized id is the model the memo remembers this
        endpoint last running. Widening the listing must not weaken that --
        a warm model this function stopped reporting as loaded is a warm
        model the scheduler can no longer evict.

        A file two loaders both report is ONE model: the first loader in
        `_MODEL_NODES` to report it owns the row, so the console never
        renders the same file twice. That surviving row carries the memo's
        residency for its own id, so a second loader's sighting can never
        overwrite a `True` with a default `False`.
        """
        memo = _run_memo(endpoint)
        installed: list[InstalledModel] = []
        seen: set[str] = set()
        for node, input_key in _MODEL_NODES:
            for name in _combo_values(endpoint, node, input_key):
                if name in seen:
                    continue
                seen.add(name)
                resident = memo is not None and memo.model_key == norm_tag(name)
                installed.append(
                    InstalledModel(
                        model_id=name,
                        capabilities=("image-generation",),
                        loaded=resident,
                        loaded_size=memo.footprint if resident else None,
                        loader=node,
                    )
                )
        return installed
```

(The `resident` local is not a tidy-up: the seams version evaluates the same
comparison twice because a comprehension has nowhere to put it. A loop does,
and computing it once is what keeps `loaded` and `loaded_size` from ever
disagreeing.)

- [ ] **Step 6: Carry the loader onto the discovery row**

In `console/inference/discovery.py`, add to `DiscoveryRow` (beside `endpoint`):

```python
    # Which of the engine's own loader nodes reported this model
    # (`InstalledModel.loader`). Empty for a catalog-only or connection-only
    # row -- nothing was polled to produce them -- and empty for an adapter
    # that does not report one. The console reads it for exactly one
    # decision: whether registering this model must also ask for the
    # companion files a multi-file family needs.
    loader: str = ""
```

and in the installed merge's `replace(...)` call, beside `endpoint=engine_endpoint`:

```python
            loader=getattr(installed, "loader", ""),
```

- [ ] **Step 7: Run both test files to verify they pass**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_engine.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console/inference/tests/test_discovery.py -q
```
Expected: PASS.

- [ ] **Step 8: Run the two neighbouring suites that share the double**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console/inference/tests -q
```
Expected: PASS. If a test broke on the 404 → `200 {}` change, that test was pinning a behaviour the live server does not have — fix the test, not the double.

- [ ] **Step 9: Docs**

In `modules/vision/README.md`, under "Role, capability, and the feature flag",
add a short subsection:

```markdown
### What counts as an image model

`ComfyUIEngine.list_installed` reports two kinds of file, and tags each with
the loader node that found it (`InstalledModel.loader`):

- a **checkpoint** (`CheckpointLoaderSimple`) — self-contained; bind it and
  generate.
- a **diffusion model** (`UnetLoaderGGUF`, `UNETLoader`) — half a pipeline.
  It needs a text encoder and a VAE named alongside it on the connection,
  and it needs its model family declared, because nothing ComfyUI serves
  over HTTP says which family a weights file belongs to. Both are recorded
  on `ModelConnection.config` when the model is registered.

The loader is an engine fact, not a family. The platform never infers a
family from a filename.
```

- [ ] **Step 10: Commit**

```bash
git add core/inference/engines/base.py core/inference/engines/comfyui.py \
        console/inference/discovery.py modules/vision/tests/_helpers.py \
        modules/vision/tests/test_comfyui_engine.py \
        console/inference/tests/test_discovery.py modules/vision/README.md
git commit -m "$(cat <<'EOF'
feat(vision): a diffusion-model file is discoverable, tagged with its loader

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 2: The template registry is keyed by `(family, operation)`

**Files:**
- Modify: `core/inference/engines/comfyui_workflows/__init__.py`
- Modify: `core/inference/engines/comfyui.py` (`supported_operations`)
- Modify: `core/inference/engines/base.py` (`InferenceEngine.supported_operations` signature + docstring)
- Test: `modules/vision/tests/test_comfyui_workflows.py`, `modules/vision/tests/test_comfyui_engine.py`
- Docs: `core/inference/engines/comfyui_workflows/__init__.py` module docstring

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `get_template(operation_key: str, family: str = "") -> Template`; `template_keys(family: str = "") -> tuple[str, ...]`; `families() -> tuple[str, ...]`; `ComfyUIEngine.supported_operations(model_id: str, endpoint: str, family: str = "") -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

Append to `modules/vision/tests/test_comfyui_workflows.py`:

```python
class TestFamilyKeyedRegistry:
    def test_the_checkpoint_operations_belong_to_the_no_family_key(self):
        """A single-file checkpoint connection declares no family, so the
        four checkpoint-based modes live under the empty family -- and are
        exactly what an undeclared connection is offered."""
        assert set(template_keys()) == {"txt2img", "img2img", "inpaint", "upscale"}
        assert set(template_keys("")) == set(template_keys())

    def test_an_unknown_family_has_no_templates_and_says_so(self):
        """A family the operator declared and this adapter has no graph for
        offers nothing -- an honest empty list, never a checkpoint graph
        run against weights that are not a checkpoint."""
        assert template_keys("not-a-family-we-have") == ()

    def test_get_template_names_the_family_it_could_not_find(self):
        with pytest.raises(ValueError) as excinfo:
            get_template("txt2img", "not-a-family-we-have")
        assert "not-a-family-we-have" in str(excinfo.value)
        assert "txt2img" in str(excinfo.value)

    def test_families_lists_only_non_empty_family_keys(self):
        """`families()` is what the adapter offers an operator to declare,
        so the empty 'no family' key must never appear in it."""
        assert "" not in families()
```

Append to `modules/vision/tests/test_comfyui_engine.py`:

```python
class TestSupportedOperations:
    def test_a_connection_with_no_family_gets_the_checkpoint_modes(self):
        assert set(ComfyUIEngine().supported_operations("sdxl.safetensors", ENDPOINT)) == {
            "txt2img", "img2img", "inpaint", "upscale",
        }

    def test_a_family_with_no_template_offers_nothing(self):
        """Honest silence, not a checkpoint graph: the page shows 'this
        model offers no modes here' rather than a form that cannot run."""
        assert ComfyUIEngine().supported_operations(
            "weights.gguf", ENDPOINT, family="not-a-family-we-have"
        ) == ()

    def test_it_still_reads_the_registry_and_never_a_hand_written_tuple(self):
        """ADR 0012's derivation honesty: adding a template is still the
        only edit that changes what this reports."""
        assert set(ComfyUIEngine().supported_operations("sdxl.safetensors", ENDPOINT)) == set(
            template_keys()
        )
```

- [ ] **Step 2: Run them to verify they fail**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_workflows.py -q
```
Expected: FAIL — `NameError: name 'families' is not defined` / `template_keys() takes 0 positional arguments`.

- [ ] **Step 3: Rekey the registry**

In `core/inference/engines/comfyui_workflows/__init__.py`, replace `_TEMPLATES`,
`get_template`, and `template_keys` with:

```python
# (model family, operation key) -> (request, model_id, connection config,
# engine-side input references) -> graph.
#
# The EMPTY family is "no family declared", which is what a single-file
# checkpoint connection has: a `CheckpointLoaderSimple` graph needs to know
# nothing else about the weights. A multi-file family is only runnable once
# an operator has declared which one it is (ADR 0012 D-EDIT-2 -- ComfyUI
# reports no architecture for a weights file over HTTP), and each family
# gets its OWN graphs, because a Flux-shaped pipeline and a Qwen-shaped one
# share almost no wiring.
#
# The family keys are ComfyUI's OWN `CLIPLoader.type` combo values -- the
# string a CLIP loader node is literally given as its `type` input. They
# are graph vocabulary, which is why they live here and nowhere above this
# package (spec §3, ADR 0012 D1); they are not, and must never become, a
# list of model names.
#
# The fourth template argument is what `ComfyUIGenerator._upload_inputs`
# got back for each of `request.inputs`: `{param key: the string a ComfyUI
# node's image input takes}`. It is empty for an operation with no file
# params.
Template = Callable[[GenerationRequest, str, dict, dict], dict]

_TEMPLATES: dict[tuple[str, str], Template] = {
    ("", "txt2img"): txt2img.build,
    ("", "img2img"): img2img.build,
    ("", "inpaint"): inpaint.build,
    ("", "upscale"): upscale.build,
}


def get_template(operation_key: str, family: str = "") -> Template:
    """The graph builder for `operation_key` under `family`.

    Raises `ValueError` naming BOTH when this engine has no such template --
    the console never offers such a pairing (role options are filtered by
    capability and `supported_operations`, which reads this same registry),
    so reaching this is a bug, and it says so plainly.
    """
    try:
        return _TEMPLATES[(family, operation_key)]
    except KeyError:
        raise ValueError(
            f"No ComfyUI template for operation {operation_key!r} in family "
            f"{family!r}; this engine implements "
            f"{sorted(f'{fam}:{op}' for fam, op in _TEMPLATES)}"
        ) from None


def template_keys(family: str = "") -> tuple[str, ...]:
    """Every operation key this engine has a template for in `family`, in
    registration order.

    `ComfyUIEngine.supported_operations` returns this rather than a
    hand-maintained tuple, so adding a template really is the only edit --
    which is what this package's docstring has always claimed. A family
    with no templates answers `()`: an operator who declared a family this
    adapter cannot run is told so honestly, never handed a checkpoint graph.
    """
    return tuple(op for fam, op in _TEMPLATES if fam == family)


def families() -> tuple[str, ...]:
    """Every non-empty family key with at least one template, in
    registration order -- what the adapter can honestly offer an operator to
    declare at registration time.

    The empty key is excluded on purpose: "no family" is not a family an
    operator picks, it is what a single-file checkpoint already is.
    """
    ordered: list[str] = []
    for family, _operation in _TEMPLATES:
        if family and family not in ordered:
            ordered.append(family)
    return tuple(ordered)
```

- [ ] **Step 4: Make `supported_operations` family-aware**

In `core/inference/engines/comfyui.py`, replace `supported_operations`:

```python
    def supported_operations(
        self, model_id: str, endpoint: str, family: str = ""
    ) -> tuple[str, ...]:
        """Operations this adapter has a graph template for, for a
        connection in `family` -- read straight from the template registry,
        so adding a mode is one template and nothing else (spec §9).

        `family` is the connection's declared model family
        (`ModelConnection.config["family"]`, ADR 0012 D-EDIT-2); the empty
        default is "no family declared", i.e. a single-file checkpoint,
        which is every connection that existed before families did.
        """
        return template_keys(family)
```

In `core/inference/engines/base.py`, update the protocol member:

```python
    def supported_operations(
        self, model_id: str, endpoint: str, family: str = ""
    ) -> tuple[str, ...]:
        """Operation keys this engine can run for `model_id`, optionally
        narrowed to a connection's declared model `family`. An engine with
        one pipeline shape ignores `family`; an engine whose graphs differ
        per family reports only that family's operations, and `()` for a
        family it has no graph for."""
        ...
```

- [ ] **Step 5: Run the tests to verify they pass**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_workflows.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_engine.py -q
```
Expected: PASS. Add `families` and `template_keys`/`get_template` to each file's imports as needed.

- [ ] **Step 6: Update the package docstring**

In `core/inference/engines/comfyui_workflows/__init__.py`, replace the
"Adding a mode ..." paragraph with:

```
Adding a mode is one module here plus one entry in `_TEMPLATES` plus one
`Operation` definition -- never a change to the page or the service layer,
and none to the engine adapter either: `ComfyUIEngine.supported_operations`
reads `template_keys(family)`. `_TEMPLATES` is keyed by
`(model family, operation key)`: the empty family is a single-file
checkpoint, and a multi-file family (whose graphs share almost no wiring
with a checkpoint's) gets its own entries under its own key.
```

- [ ] **Step 7: Run the vision suite**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests -q
```
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add core/inference/engines/comfyui_workflows/__init__.py \
        core/inference/engines/comfyui.py core/inference/engines/base.py \
        modules/vision/tests/test_comfyui_workflows.py \
        modules/vision/tests/test_comfyui_engine.py
git commit -m "$(cat <<'EOF'
refactor(vision): key the ComfyUI template registry by (family, operation)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 3: The family vocabulary and the text-encoder list come from the engine

**Files:**
- Modify: `core/inference/engines/comfyui.py` (`_ASSET_NODES` shape, `list_assets`, `_FAMILY_NODE`, `list_families`)
- Modify: `core/inference/engines/base.py` (`InferenceEngine.list_families` optional member)
- Modify: `modules/vision/tests/_helpers.py` (`FakeComfyUI.text_encoders`, `.text_encoders_gguf`, `.families`)
- Test: `modules/vision/tests/test_comfyui_engine.py`, `modules/vision/tests/test_operations.py`

**Interfaces:**
- Consumes: `families()` (Task 2).
- Produces: `ComfyUIEngine.list_families(endpoint: str) -> tuple[str, ...]`; `ComfyUIEngine.list_assets(endpoint, "text_encoder") -> list[Asset]`.

- [ ] **Step 1: Extend the double**

In `modules/vision/tests/_helpers.py`, add three fields and two `_NODE_INPUTS`
rows, and teach `FakeComfyUI.get` to answer `CLIPLoader`'s TWO required
inputs at once (it is the only node in this double with more than one):

```python
    # Text encoders as each CLIP loader reports them. ComfyUI-GGUF's loader
    # sees `.gguf` files AND the safetensors beside them; the built-in one
    # sees only what core ComfyUI can load. Separate fields because they are
    # separate nodes on the real server.
    text_encoders_gguf: tuple[str, ...] = ()
    text_encoders: tuple[str, ...] = ()
    # `CLIPLoader.type` -- the engine's OWN model-family vocabulary.
    families: tuple[str, ...] = ("stable_diffusion", "flux2", "qwen_image")
```

In `FakeComfyUI.get`'s `object_info` branch, ABOVE the `_NODE_INPUTS` lookup:

```python
            if node == "CLIPLoader" and node not in self.missing_nodes:
                # The one node in this double with two required combos: the
                # encoder file list and the family vocabulary.
                return _Response(
                    payload=object_info(
                        "CLIPLoader",
                        {
                            "clip_name": [list(self.text_encoders), {"tooltip": "x"}],
                            "type": [list(self.families), {"tooltip": "x"}],
                        },
                        self.combo_shape,
                    )
                )
```

and add to `_NODE_INPUTS`:

```python
        "CLIPLoaderGGUF": ("text_encoders_gguf", "clip_name"),
```

- [ ] **Step 2: Write the failing tests**

Append to `modules/vision/tests/test_comfyui_engine.py`:

```python
class TestTextEncodersAndFamilies:
    def test_text_encoders_union_both_clip_loaders_first_seen_order(self):
        """A text encoder can be a `.gguf` (only the GGUF loader sees it) or
        a safetensors (both loaders do). The honest answer is the union, and
        a file both report is listed once."""
        fake = FakeComfyUI(
            text_encoders_gguf=("enc-a.gguf", "enc-b.safetensors"),
            text_encoders=("enc-b.safetensors", "enc-c.safetensors"),
        )
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assets = ComfyUIEngine().list_assets(ENDPOINT, "text_encoder")

        assert [a.asset_id for a in assets] == [
            "enc-a.gguf", "enc-b.safetensors", "enc-c.safetensors",
        ]
        assert all(a.kind == "text_encoder" for a in assets)

    def test_text_encoders_survive_an_absent_gguf_pack(self):
        fake = FakeComfyUI(
            text_encoders_gguf=("never-seen.gguf",),
            text_encoders=("enc-c.safetensors",),
            missing_nodes=("CLIPLoaderGGUF",),
        )
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assets = ComfyUIEngine().list_assets(ENDPOINT, "text_encoder")

        assert [a.asset_id for a in assets] == ["enc-c.safetensors"]

    def test_families_are_the_engines_own_vocabulary_narrowed_to_what_we_can_run(self):
        """The strings come from ComfyUI's `CLIPLoader.type` combo -- this
        platform ships no list of model families -- and only those this
        adapter has a graph template for are offered, so an operator can
        never declare a family that would then run nothing."""
        fake = FakeComfyUI(families=("stable_diffusion", "flux2", "qwen_image"))
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            offered = ComfyUIEngine().list_families(ENDPOINT)

        assert set(offered) <= set(fake.families)
        assert set(offered) == set(families())

    def test_a_family_the_engine_does_not_report_is_never_offered(self):
        """Even a family we have a template for is not offered by an engine
        build that does not know the string -- the engine's vocabulary is
        the authority on what its own loader will accept."""
        fake = FakeComfyUI(families=("stable_diffusion",))
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().list_families(ENDPOINT) == ()
```


- [ ] **Step 3: Run them to verify they fail**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_engine.py -q
```
Expected: FAIL — `AttributeError: 'ComfyUIEngine' object has no attribute 'list_families'`.

- [ ] **Step 4: Implement**

**`core.inference.operations.ASSET_KINDS` is deliberately NOT extended.**
That set is the vocabulary a `Param(asset_kind=...)` is validated against —
the kinds an OPERATION may declare as a form field. A text encoder is never
an operation param: it is a connection-level companion the console asks for
once, at registration. Adding it there would advertise a param kind nothing
declares and nothing renders. `ComfyUIEngine.list_assets` does not validate
its `kind` against that set (it looks the kind up in `_ASSET_NODES` and
returns `[]` for a miss), so the console's registration read works with no
change to `core/inference/operations.py` at all.

In `core/inference/engines/comfyui.py`, replace `_ASSET_NODES` and
`list_assets`, and add `_FAMILY_NODE` / `list_families`:

```python
# Which ComfyUI loader node(s) report each platform asset kind, and which
# of each node's required inputs holds the file list. Engine-specific by
# nature, so the mapping lives here rather than in the platform layer.
#
# A TUPLE of nodes per kind, because one kind can have more than one
# reporter: a text encoder is listed by ComfyUI-GGUF's `CLIPLoaderGGUF`
# (which sees `.gguf` files as well as safetensors) and by the built-in
# `CLIPLoader` (which sees only what core ComfyUI can load). The union in
# first-seen order is the honest answer; a file both report is one asset.
#
# "embedding" is deliberately absent: ComfyUI has no embedding LOADER node
# (embeddings are referenced from prompt text), so that kind honestly
# reports nothing.
_ASSET_NODES: dict[str, tuple[tuple[str, str], ...]] = {
    "lora": (("LoraLoader", "lora_name"),),
    "vae": (("VAELoader", "vae_name"),),
    "controlnet": (("ControlNetLoader", "control_net_name"),),
    "upscale_model": (("UpscaleModelLoader", "model_name"),),
    "text_encoder": (("CLIPLoaderGGUF", "clip_name"), ("CLIPLoader", "clip_name")),
}

# The node input whose combo IS this engine's model-family vocabulary: the
# `type` a CLIP loader is given for a multi-file family. Read live, so this
# platform ships no list of families and no list of models -- and so a
# ComfyUI build that does not know a family string never offers it.
_FAMILY_NODE = ("CLIPLoader", "type")
```

```python
    def list_assets(self, endpoint: str, kind: str) -> list[Asset]:
        """Assets of `kind` installed at `endpoint`; `[]` for a kind ComfyUI
        has no loader node for (e.g. "embedding").

        A kind with several reporting nodes is the union of what they
        report, in first-seen order, de-duplicated -- see `_ASSET_NODES`.
        """
        nodes = _ASSET_NODES.get(kind)
        if not nodes:
            return []
        assets: list[Asset] = []
        seen: set[str] = set()
        for node, input_key in nodes:
            for name in _combo_values(endpoint, node, input_key):
                if name in seen:
                    continue
                seen.add(name)
                assets.append(Asset(kind=kind, asset_id=name))
        return assets

    def list_families(self, endpoint: str) -> tuple[str, ...]:
        """Model families an operator may declare for a connection at
        `endpoint` (ADR 0012 D-EDIT-2).

        Two authorities, intersected, and no third:

        - the ENGINE's own vocabulary (`CLIPLoader.type`), because that
          string is what a CLIP loader is literally given, and a build that
          does not know it would reject the graph;
        - the families this adapter actually has a graph template for
          (`comfyui_workflows.families()`), because offering one we cannot
          run would let an operator declare a model into silence.

        This platform ships neither list. Order follows the engine's.
        """
        node, input_key = _FAMILY_NODE
        runnable = set(families())
        return tuple(
            value for value in _combo_values(endpoint, node, input_key) if value in runnable
        )
```

Add `families` to this module's `comfyui_workflows` import:

```python
from core.inference.engines.comfyui_workflows import families, get_template, template_keys
```

In `core/inference/engines/base.py`, add beside the other optional members:

```python
    def list_families(self, endpoint: str) -> tuple[str, ...]:
        """Model families an operator may declare for a connection at
        `endpoint`; `()` for an engine whose pipelines don't vary by family.
        Read downstream via `getattr(engine, "list_families", None)`, so an
        adapter without one simply never asks for a family."""
        ...
```

- [ ] **Step 5: Run the tests to verify they pass**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_engine.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_operations.py -q
```
Expected: PASS. Note that at this point `families()` is empty, so
`test_families_are_the_engines_own_vocabulary_narrowed_to_what_we_can_run`
asserts `set(offered) == set()` — correct and honest: no family has a graph
yet. Tasks 6 and 7 each add one assertion that their family now appears.

- [ ] **Step 6: Run the vision suite, then commit**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests -q
```

```bash
git add core/inference/engines/comfyui.py core/inference/engines/base.py \
        core/inference/operations.py modules/vision/tests/_helpers.py \
        modules/vision/tests/test_comfyui_engine.py modules/vision/tests/test_operations.py
git commit -m "$(cat <<'EOF'
feat(vision): read the family vocabulary and text-encoder list from the engine

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 4: Registration carries the family and its companion files

**Files:**
- Modify: `console/inference/views.py` (`connection_add`, `machine_model_add`, `_build_context`)
- Modify: `console/inference/templates/console/inference.html`
- Test: `console/inference/tests/test_views.py`
- Docs: `console/inference/README.md`

**Interfaces:**
- Consumes: `InstalledModel.loader`/`DiscoveryRow.loader` (Task 1), `ComfyUIEngine.list_families` and `list_assets(..., "text_encoder")` (Task 3).
- Produces: `ModelConnection.config == {"family": str, "text_encoder": str, "vae": str}` for a multi-file connection; console context keys `family_options`, `text_encoder_options`, `vae_options`.

**Note on validation (this is the decision that keeps the plan acyclic):**
the form stores the family the operator picked from an engine-sourced
`<select>`; it does **not** re-validate the stored string against
`list_families()`. Storing a family this adapter has no graph for is not a
lie — `supported_operations` then honestly reports no operations and the
create page says the selected model offers no modes here. Blocking at the
form would also make the form untestable until a template exists, and would
be a second, weaker copy of a rule `supported_operations` already enforces
where it matters.

- [ ] **Step 1: Write the failing tests**

Append to `console/inference/tests/test_views.py` (inside the class that
already exercises `connection_add`; reuse that class's `client`/login idiom):

```python
    def test_manual_registration_stores_family_and_companions_in_config(self):
        """A multi-file family needs three facts nothing on the machine can
        supply: which family it is, which text encoder, which VAE. They land
        in `ModelConnection.config`, the JSONField D8 added for exactly
        this, and nowhere else."""
        response = self.client.post(
            reverse("inference-connection-add"),
            {
                "name": "edit model", "engine": "comfyui",
                "endpoint": "http://comfy.test:8188",
                "model_id": "weights-Q4_K_S.gguf",
                "capability": "image-generation",
                "family": "flux2",
                "text_encoder": "enc-a.gguf",
                "vae": "vae-a.safetensors",
            },
        )
        assert response.status_code in (302, 200)
        connection = ModelConnection.objects.get(name="edit model")
        assert connection.config == {
            "family": "flux2",
            "text_encoder": "enc-a.gguf",
            "vae": "vae-a.safetensors",
        }

    def test_a_checkpoint_registration_stores_no_config_at_all(self):
        """A single-file checkpoint needs none of it, and an empty dict in
        the JSONField would be three keys of noise on every existing row."""
        self.client.post(
            reverse("inference-connection-add"),
            {
                "name": "plain checkpoint", "engine": "comfyui",
                "endpoint": "http://comfy.test:8188",
                "model_id": "sdxl.safetensors",
                "capability": "image-generation",
            },
        )
        assert ModelConnection.objects.get(name="plain checkpoint").config is None

    def test_editing_a_connection_can_clear_a_companion_back_to_unset(self):
        """Blank means unset -- an operator who mis-picked must be able to
        undo it without deleting the connection."""
        connection = ModelConnection.objects.create(
            name="edit model", engine="comfyui", endpoint="http://comfy.test:8188",
            model_id="weights-Q4_K_S.gguf", capabilities=["image-generation"],
            config={"family": "flux2", "text_encoder": "enc-a.gguf", "vae": "vae-a.safetensors"},
        )
        self.client.post(
            reverse("inference-connection-add"),
            {
                "connection_id": str(connection.pk),
                "name": "edit model", "engine": "comfyui",
                "endpoint": "http://comfy.test:8188",
                "model_id": "weights-Q4_K_S.gguf",
                "capability": "image-generation",
                "family": "flux2", "text_encoder": "", "vae": "vae-a.safetensors",
            },
        )
        connection.refresh_from_db()
        assert connection.config == {"family": "flux2", "vae": "vae-a.safetensors"}

    def test_one_click_registration_refuses_a_model_that_needs_companions(self):
        """`machine_model_add` registers ONLY from detection, and detection
        cannot supply a family or its companion files -- so a diffusion-model
        row is sent to the manual form instead of being registered half-
        configured."""
        response = self.client.post(
            reverse("inference-machine-model-add"),
            {
                "engine": "comfyui", "model_id": "weights-Q4_K_S.gguf",
                "endpoint": "http://comfy.test:8188",
                "capability": "image-generation",
                "loader": "UnetLoaderGGUF",
            },
            follow=True,
        )
        assert not ModelConnection.objects.filter(model_id="weights-Q4_K_S.gguf").exists()
        assert "manual form" in response.content.decode()
```

- [ ] **Step 2: Run them to verify they fail**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console/inference/tests/test_views.py -q
```
Expected: FAIL — `connection.config is None` where a dict was expected.

- [ ] **Step 3: Read the three fields in `connection_add`**

Beside the existing `request.POST.get(...)` block:

```python
    # A multi-file model family's three declared facts (ADR 0012 D-EDIT-2/3).
    # `family` is the operator's declaration -- nothing ComfyUI serves over
    # HTTP says which family a weights file belongs to, and this platform
    # never infers one from a filename. `text_encoder` and `vae` name the
    # companion files, picked from what the engine reports having installed.
    # All three blank is the normal case: a single-file checkpoint needs
    # none of them.
    family = request.POST.get("family", "").strip()
    text_encoder = request.POST.get("text_encoder", "").strip()
    vae = request.POST.get("vae", "").strip()
```

- [ ] **Step 4: Build the `config` value where the connection is created/updated**

Add this helper next to `connection_add` in `console/inference/views.py`:

```python
def _family_config(family: str, text_encoder: str, vae: str) -> dict | None:
    """The connection `config` for a declared model family, or `None`.

    Blank means UNSET, and an unset key is absent rather than present-and-
    empty: a graph template asks `config.get("text_encoder")` and an empty
    string would be a filename it would then hand a loader. All three blank
    -- the single-file checkpoint case, which is every connection that
    existed before families did -- is `None`, so no existing row grows three
    keys of noise.
    """
    declared = {"family": family, "text_encoder": text_encoder, "vae": vae}
    kept = {key: value for key, value in declared.items() if value}
    return kept or None
```

In the create branch's `ModelConnection.objects.create(...)`, add:

```python
            config=_family_config(family, text_encoder, vae),
```

In the update branch, beside the other `instance.<field> = ...` assignments:

```python
    instance.config = _family_config(family, text_encoder, vae)
```

- [ ] **Step 5: Refuse one-click registration for a companions-needing loader**

In `machine_model_add`, after the `capability`/`capability_list` checks and
before the reuse check:

```python
    # Detection-only registration cannot supply a model family or its
    # companion files, and this console never guesses (the standing
    # never-guess rulings). A model reported by a DIFFUSION-MODEL loader is
    # half a pipeline; the manual form -- where the operator declares all
    # three -- is the way in. Which loader reported it is the row's own
    # `DiscoveryRow.loader`, posted by the template.
    loader = request.POST.get("loader", "").strip()
    if loader and loader != _MODEL_LOADER_SELF_CONTAINED:
        messages.error(
            request,
            f"“{model_id}” is a diffusion model, not a self-contained "
            "checkpoint -- it needs a model family, a text encoder, and a "
            "VAE named alongside it. Use the manual form below to register "
            "it with all three.",
        )
        return _redirect_console(request)
```

with, near the module's other constants:

```python
# The loader whose models need nothing else named alongside them. Compared
# against `DiscoveryRow.loader` (the engine's own node name) so the console
# reads the ENGINE's fact rather than guessing from a filename; any other
# loader means "this is half a pipeline". Not an engine-name literal: it is
# the one loader node a self-contained model is reported by, and the
# console's only job with it is inequality.
_MODEL_LOADER_SELF_CONTAINED = "CheckpointLoaderSimple"
```

- [ ] **Step 6: Offer the option lists on the page**

In `_build_context`, alongside the other engine reads, add (guarded, because
this page must never 500 on an unreachable engine):

```python
    context["family_options"] = _engine_options(engine, endpoint, "families")
    context["text_encoder_options"] = _engine_options(engine, endpoint, "text_encoder")
    context["vae_options"] = _engine_options(engine, endpoint, "vae")
```

with:

```python
def _engine_options(engine, endpoint: str, what: str) -> list[str]:
    """The engine's own list for one registration field, or `[]`.

    `what` is either `"families"` (read via the optional `list_families`
    member) or an asset kind (`"text_encoder"`, `"vae"`). Every optional
    protocol member is read with `getattr` and every call is wrapped: an
    engine that is down, or an adapter that predates a member, costs this
    page an empty `<select>` and never a 500 -- the same tolerance
    `modules.vision.services.live_options` applies for the same reason.
    """
    try:
        if what == "families":
            reader = getattr(engine, "list_families", None)
            return [] if reader is None else list(reader(endpoint))
        reader = getattr(engine, "list_assets", None)
        return [] if reader is None else [asset.asset_id for asset in reader(endpoint, what)]
    except Exception:  # noqa: BLE001 -- an option list is never worth a 500
        logger.debug("Listing %r for %s failed", what, endpoint, exc_info=True)
        return []
```

- [ ] **Step 7: Render the three selects and the loader-aware machine row**

In `console/inference/templates/console/inference.html`, inside the manual
registration form, after the capability field:

```html
{% if family_options %}
<fieldset class="family-fields">
  <legend>Multi-file model (leave blank for a self-contained checkpoint)</legend>
  <label for="family">Model family</label>
  <select id="family" name="family">
    <option value="">— none —</option>
    {% for option in family_options %}
    <option value="{{ option }}"{% if form_values.family == option %} selected{% endif %}>{{ option }}</option>
    {% endfor %}
  </select>
  <label for="text_encoder">Text encoder</label>
  <select id="text_encoder" name="text_encoder">
    <option value="">— none —</option>
    {% for option in text_encoder_options %}
    <option value="{{ option }}"{% if form_values.text_encoder == option %} selected{% endif %}>{{ option }}</option>
    {% endfor %}
  </select>
  <label for="vae">VAE</label>
  <select id="vae" name="vae">
    <option value="">— none —</option>
    {% for option in vae_options %}
    <option value="{{ option }}"{% if form_values.vae == option %} selected{% endif %}>{{ option }}</option>
    {% endfor %}
  </select>
</fieldset>
{% endif %}
```

and in the "On this machine" row's Add form, carry the loader so the view can
read the engine's own fact rather than re-deriving it:

```html
      <input type="hidden" name="loader" value="{{ item.row.loader }}">
```

(`_installed_rows` already builds `item` view-models around `item.row`; if
that dict does not expose `row`, add `"loader": row.loader` to the view-model
and use `item.loader` here instead — match whatever the surrounding template
already does.)

- [ ] **Step 8: Run the console tests to verify they pass**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console/inference/tests/test_views.py -q
```
Expected: PASS.

- [ ] **Step 9: Docs**

Append to `console/inference/README.md`:

```markdown
## Registering a multi-file model family

Some image models are not one file. A diffusion-model file (`.gguf`, or a
`.safetensors` the native UNet loader reports) is half a pipeline: it needs a
text encoder and a VAE named alongside it, and it needs its model family
declared.

The family cannot be detected. Nothing ComfyUI serves over HTTP reports what
architecture a weights file is, and this platform never infers one from a
filename — so the operator declares it, from a `<select>` whose options are
the engine's OWN family vocabulary (`CLIPLoader.type`) narrowed to the
families the adapter has a graph template for. The encoder and VAE are picked
the same way, from what the engine reports having installed.

All three land in `ModelConnection.config` (the JSONField ADR 0012 D8 added
for exactly this) and reach the graph template as `**ResolvedModel.config`.
Leave all three blank for a self-contained checkpoint.

The "Add to registered" button on an "On this machine" row is
detection-only, so it refuses a diffusion-model file and points at this form
— it cannot supply three facts detection does not have.
```

- [ ] **Step 10: Commit**

```bash
git add console/inference/views.py console/inference/templates/console/inference.html \
        console/inference/tests/test_views.py console/inference/README.md
git commit -m "$(cat <<'EOF'
feat(console): registration declares a model family and its companion files

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 5: The `edit` operation

**Files:**
- Modify: `core/inference/operations.py` (add `EDIT`)
- Modify: `modules/vision/apps.py` (register it)
- Test: `modules/vision/tests/test_operations.py`, `modules/vision/tests/test_apps.py`
- Docs: `modules/vision/README.md` ("The operation registry, and how to add an operation")

**Interfaces:**
- Consumes: family-aware `supported_operations` (Task 2) — without it, registering `EDIT` would offer it for checkpoints too.
- Produces: `core.inference.operations.EDIT` with param keys `instruction`, `init_image`, `reference_image`, `guidance`, `steps`, `seed`, in that order.

- [ ] **Step 1: Write the failing tests**

Append to `modules/vision/tests/test_operations.py`:

```python
class TestEditOperation:
    def test_it_declares_only_params_every_edit_family_really_wires(self):
        """The honesty rule for a family-dispatched operation: a param this
        schema declares must be wired by EVERY graph that can run it. A
        sampler, a scheduler, a negative prompt, a denoise, a size, and a
        LoRA are each honoured by at most one of the two edit graphs, so
        none of them is offered. `batch_size` is excluded for a second
        reason on top of that: one bundled edit workflow carries no
        batching node at all, and an edit produces ONE output."""
        assert [param.key for param in EDIT.params] == [
            "instruction", "init_image", "reference_image",
            "guidance", "steps", "seed",
        ]

    def test_the_image_being_edited_is_the_first_file_param(self):
        """`Operation.file_params()` order is what a gallery 'use this image
        here' link pre-fills, and the image being EDITED is the one it
        should land on -- not the optional reference."""
        assert [param.key for param in EDIT.file_params()] == [
            "init_image", "reference_image",
        ]
        assert EDIT.file_param_keys() == frozenset({"init_image", "reference_image"})

    def test_the_reference_image_is_optional_and_the_edited_one_is_not(self):
        assert EDIT.param("init_image").required is True
        assert EDIT.param("reference_image").required is False

    def test_a_submission_without_a_reference_image_validates(self):
        clean = validate_params(
            EDIT,
            {
                "instruction": "put a red hat on the woman",
                "init_image": "beach.png",
                "guidance": "4.0", "steps": "20", "seed": "42",
            },
        )
        assert clean["instruction"] == "put a red hat on the woman"
        assert clean["init_image"] == "beach.png"
        assert clean["reference_image"] is None
        assert clean["guidance"] == 4.0
        assert clean["seed"] == 42

    def test_a_blank_instruction_is_refused(self):
        """An edit with no instruction is not an edit."""
        with pytest.raises(ParamError) as excinfo:
            validate_params(EDIT, {"instruction": "", "init_image": "beach.png"})
        assert "instruction" in excinfo.value.errors

    def test_it_names_no_model_and_no_family_anywhere_in_its_copy(self):
        """`core/` ships no model names. The schema is family-agnostic; the
        graph template is where a family is named."""
        copy = " ".join(
            [EDIT.label, EDIT.description]
            + [param.label for param in EDIT.params]
            + [param.description for param in EDIT.params]
        ).lower()
        for forbidden in ("flux", "qwen", "gguf", "mistral", "sdxl"):
            assert forbidden not in copy
```

Extend the registration assertion in `modules/vision/tests/test_apps.py`:

```python
    def test_every_shipped_operation_is_registered_behind_the_feature_flag(self):
        assert {operation.key for operation in all_operations()} == {
            "txt2img", "img2img", "inpaint", "upscale", "edit",
        }
```

- [ ] **Step 2: Run them to verify they fail**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_operations.py -q
```
Expected: FAIL — `ImportError: cannot import name 'EDIT'`.

- [ ] **Step 3: Define `EDIT`**

At the end of `core/inference/operations.py`, after `UPSCALE`:

```python
# The one instruction-driven mode, and the first operation whose GRAPH
# differs per model family (ADR 0012 D-EDIT-1). It is deliberately ONE
# operation, not one per family: the operator's question is "change this
# image like so", and which model answers it is the picker's job -- exactly
# the split D4/D5 already draw between a binding and an operation.
#
# Its params are the honest INTERSECTION of what every edit graph really
# wires. A param only one family honours is not declared here, because a
# form that offers it would be lying about the other: no sampler or
# scheduler (one family's schedule has no scheduler input and both fix the
# sampler), no negative prompt (one family has no negative path at all), no
# width/height (both derive the size from the image being edited), no
# denoise (one family's sampler has no such input), and no LoRA (both
# families' graphs use a model-only loader that `_fragments._lora_chain`
# cannot serve).
EDIT = Operation(
    key="edit",
    label="Edit an image",
    capability="image-generation",
    output_media="image/png",
    description=(
        "Change an existing image by describing the change in words. Everything the "
        "instruction does not ask for is left as it was."
    ),
    params=(
        Param(
            "instruction", "text", "Instruction", default="", required=True,
            description=(
                "What to change, phrased as an instruction rather than a description — "
                "\"put a red hat on the woman\", \"replace the leather with fur\". The "
                "image you attach is the thing being changed."
            ),
        ),
        # First file param on purpose: a gallery "use this image here" link
        # pre-fills an operation's FIRST file param, and the image being
        # edited is the one it should land on.
        Param(
            "init_image", "file", "Image", accept="image/*", required=True,
            description=(
                "The image to edit. Its own dimensions decide the output size, which is "
                "why this mode has no width or height."
            ),
        ),
        Param(
            "reference_image", "file", "Reference image", accept="image/*",
            description=(
                "An optional second image the instruction can refer to — a style to "
                "apply, a texture to borrow, a face to carry over. Leaving it empty is "
                "the normal case."
            ),
        ),
        Param(
            "guidance", "float", "Guidance", default=4.0, min=0, max=30, step=0.5,
            description=(
                "How strictly to follow the instruction. Low values drift off it, high "
                "values over-bake the image; 3-5 suits most edits."
            ),
        ),
        Param(
            "steps", "int", "Steps", default=20, min=1, max=150,
            description=(
                "How many denoising steps to run. More steps cost more time; an edit "
                "usually needs fewer than a generation from nothing."
            ),
        ),
        Param(
            "seed", "seed", "Seed", default=None,
            description=(
                "The random seed. Leave it blank for a new one every run; reuse a "
                "finished job's seed to reproduce that job exactly."
            ),
        ),
        # No `batch_size`, unlike every checkpoint-based mode: one of the
        # two bundled edit workflows carries no batching node whatsoever, so
        # a batch could only ever be honoured by half the models this
        # operation runs on. An edit produces ONE output, and each edit
        # graph pins its own latent's batch at 1.
    ),
)
```

- [ ] **Step 4: Register it**

In `modules/vision/apps.py`, extend the import and add one line:

```python
        from core.inference.operations import (
            EDIT, IMG2IMG, INPAINT, TXT2IMG, UPSCALE, register_operation,
        )
        ...
        register_operation(UPSCALE)
        register_operation(EDIT)
```

- [ ] **Step 5: Run the tests to verify they pass**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_operations.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_apps.py -q
```
Expected: PASS.

- [ ] **Step 6: Run the vision suite**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests -q
```
Expected: PASS. `views.page_operations()` now lists five operations, and
`input_targets()` gains an "Edit an image" entry pointing at `init_image` —
both driven by the registry, so no view or template changes. If a test
pinned the count at four, update it to five; if one pinned the LIST, it is
asserting the registry and should be updated in place.

- [ ] **Step 7: Docs**

In `modules/vision/README.md`, under "The operation registry, and how to add
an operation", add:

```markdown
### `edit` — one operation, several graphs

`edit` is the first operation whose GRAPH depends on the connection, not just
on the operation: two model families both do instruction-based editing, and
their pipelines share almost no wiring. The registry
(`comfyui_workflows._TEMPLATES`) is therefore keyed by
`(family, operation)`, and `supported_operations(model_id, endpoint, family)`
reports only what that family has a graph for — so a checkpoint connection is
never offered `edit`, and an edit connection is never offered `txt2img`.

`edit` declares only the params EVERY edit graph really wires. A param one
family honours and the other ignores is not declared at all: offering it
would be a lie about half the models that can run this mode.
```

- [ ] **Step 8: Commit**

```bash
git add core/inference/operations.py modules/vision/apps.py \
        modules/vision/tests/test_operations.py modules/vision/tests/test_apps.py \
        modules/vision/README.md
git commit -m "$(cat <<'EOF'
feat(vision): the edit operation — one instruction, one image, several graphs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 6: The `flux2` edit graph, verified live through the queue

**Files:**
- Create: `core/inference/engines/comfyui_workflows/flux2_edit.py`
- Modify: `core/inference/engines/comfyui_workflows/_fragments.py` (shared multi-file loaders)
- Modify: `core/inference/engines/comfyui_workflows/__init__.py` (register `("flux2", "edit")`)
- Test: `modules/vision/tests/test_comfyui_workflows.py`
- Docs: `modules/vision/README.md`

**Interfaces:**
- Consumes: `EDIT`'s param keys (Task 5); `_TEMPLATES` keyed by `(family, operation)` (Task 2); `ModelConnection.config` shape (Task 4).
- Produces: `_fragments.components(graph, model_id, config) -> Checkpoint`; `_fragments.encode_text(graph, ckpt, text) -> Link`; `_fragments.scale_to_megapixels(graph, image, megapixels=1.0) -> Link`; `_fragments.image_size(graph, image) -> tuple[Link, Link]`; the `("flux2", "edit")` template.

- [ ] **Step 1: Write the failing tests**

Append to `modules/vision/tests/test_comfyui_workflows.py`:

```python
EDIT_PARAMS = {
    "instruction": "put a red hat on the woman",
    "init_image": "beach.png",
    "reference_image": None,
    "guidance": 4.0,
    "steps": 20,
    "seed": 987654,
}


def _by_class(graph: dict) -> dict[str, list[dict]]:
    """Every node in `graph`, grouped by class_type, in id order. Node ids
    are ALLOCATED (`_fragments.Graph.add`), so a test that indexed them by
    hand would break the moment a node is inserted -- which is exactly the
    renumbering `Graph` exists to make safe."""
    grouped: dict[str, list[dict]] = {}
    for _node_id, node in sorted(graph.items(), key=lambda item: int(item[0])):
        grouped.setdefault(node["class_type"], []).append(node)
    return grouped


def _edit_graph(family: str, config: dict, inputs: dict, params: dict | None = None) -> dict:
    request = GenerationRequest(
        operation="edit",
        model_id="weights-Q4_K_S.gguf",
        params=dict(params or EDIT_PARAMS),
        client_ref="8f14e45f-ceea-467a-9575-1b0e5a1d1f1e",
    )
    return get_template("edit", family)(request, request.model_id, config, inputs)


FLUX2_CONFIG = {
    "family": "flux2",
    "text_encoder": "encoder-Q4_K_M.gguf",
    "vae": "family-vae.safetensors",
}


class TestFlux2EditGraph:
    def test_the_family_is_registered_and_offered(self):
        assert "flux2" in families()
        assert "edit" in template_keys("flux2")

    def test_each_declared_file_is_loaded_by_the_loader_that_can_read_it(self):
        """A `.gguf` goes to ComfyUI-GGUF's loader, a safetensors to the
        built-in one. The extension of the file the OPERATOR named decides
        -- never a pattern matched against a shipped list of models."""
        nodes = _by_class(_edit_graph("flux2", FLUX2_CONFIG, {"init_image": "in/beach.png"}))
        assert nodes["UnetLoaderGGUF"][0]["inputs"]["unet_name"] == "weights-Q4_K_S.gguf"
        assert nodes["CLIPLoaderGGUF"][0]["inputs"] == {
            "clip_name": "encoder-Q4_K_M.gguf", "type": "flux2",
        }
        assert nodes["VAELoader"][0]["inputs"]["vae_name"] == "family-vae.safetensors"

    def test_a_safetensors_pair_uses_the_built_in_loaders(self):
        request = GenerationRequest(
            operation="edit", model_id="weights.safetensors",
            params=dict(EDIT_PARAMS), client_ref="ref",
        )
        graph = get_template("edit", "flux2")(
            request, request.model_id,
            {"family": "flux2", "text_encoder": "enc.safetensors", "vae": "v.safetensors"},
            {"init_image": "in/beach.png"},
        )
        nodes = _by_class(graph)
        assert nodes["UNETLoader"][0]["inputs"] == {
            "unet_name": "weights.safetensors", "weight_dtype": "default",
        }
        assert nodes["CLIPLoader"][0]["inputs"] == {
            "clip_name": "enc.safetensors", "type": "flux2", "device": "default",
        }

    def test_the_instruction_is_encoded_once_and_guided(self):
        """One conditioning path: this family's guider takes a single
        conditioning, which is why `edit` declares no negative prompt."""
        nodes = _by_class(_edit_graph("flux2", FLUX2_CONFIG, {"init_image": "in/beach.png"}))
        assert len(nodes["CLIPTextEncode"]) == 1
        assert nodes["CLIPTextEncode"][0]["inputs"]["text"] == "put a red hat on the woman"
        assert nodes["FluxGuidance"][0]["inputs"]["guidance"] == 4.0

    def test_the_edited_image_is_scaled_encoded_and_referenced(self):
        graph = _edit_graph("flux2", FLUX2_CONFIG, {"init_image": "in/beach.png"})
        nodes = _by_class(graph)
        assert nodes["LoadImage"][0]["inputs"]["image"] == "in/beach.png"
        scale = nodes["ImageScaleToTotalPixels"][0]["inputs"]
        assert scale["upscale_method"] == "area"
        assert scale["megapixels"] == 1.0
        assert scale["resolution_steps"] == 1
        assert len(nodes["VAEEncode"]) == 1
        assert len(nodes["ReferenceLatent"]) == 1

    def test_the_output_size_comes_from_the_edited_image_never_a_number_we_invented(self):
        graph = _edit_graph("flux2", FLUX2_CONFIG, {"init_image": "in/beach.png"})
        nodes = _by_class(graph)
        size_id = next(nid for nid, n in graph.items() if n["class_type"] == "GetImageSize")
        latent = nodes["EmptyFlux2LatentImage"][0]["inputs"]
        sigmas = nodes["Flux2Scheduler"][0]["inputs"]
        assert latent["width"] == [size_id, 0]
        assert latent["height"] == [size_id, 1]
        # Pinned at 1 by the template, not read from a param -- `EDIT`
        # declares none (R1: one output per edit).
        assert latent["batch_size"] == 1
        assert sigmas["width"] == [size_id, 0]
        assert sigmas["height"] == [size_id, 1]
        assert sigmas["steps"] == 20

    def test_a_reference_image_adds_a_second_reference_latent_after_the_first(self):
        """Chain order IS the reference numbering an instruction can name:
        the image being edited is Reference Image 1, the optional second one
        is Reference Image 2."""
        graph = _edit_graph(
            "flux2", FLUX2_CONFIG,
            {"init_image": "in/beach.png", "reference_image": "in/style.png"},
        )
        nodes = _by_class(graph)
        assert [n["inputs"]["image"] for n in nodes["LoadImage"]] == [
            "in/beach.png", "in/style.png",
        ]
        first, second = nodes["ReferenceLatent"]
        first_id = next(nid for nid, n in graph.items() if n is first)
        assert second["inputs"]["conditioning"] == [first_id, 0]
        assert len(nodes["ImageScaleToTotalPixels"]) == 2
        assert len(nodes["VAEEncode"]) == 2

    def test_the_sampler_is_wired_and_the_seed_is_the_jobs_own(self):
        nodes = _by_class(_edit_graph("flux2", FLUX2_CONFIG, {"init_image": "in/beach.png"}))
        assert nodes["RandomNoise"][0]["inputs"]["noise_seed"] == 987654
        assert nodes["KSamplerSelect"][0]["inputs"]["sampler_name"] == "euler"
        sampler = nodes["SamplerCustomAdvanced"][0]["inputs"]
        assert set(sampler) == {"noise", "guider", "sampler", "sigmas", "latent_image"}
        assert nodes["SaveImage"][0]["inputs"]["filename_prefix"] == (
            "8f14e45f-ceea-467a-9575-1b0e5a1d1f1e"
        )

    def test_a_connection_missing_a_companion_is_refused_in_the_operators_words(self):
        """A half-registered connection must fail the JOB with a sentence
        that says what to do, not a KeyError in a traceback."""
        with pytest.raises(GenerationRejected) as excinfo:
            _edit_graph("flux2", {"family": "flux2", "vae": "v.safetensors"},
                        {"init_image": "in/beach.png"})
        assert "text encoder" in str(excinfo.value)
```

- [ ] **Step 2: Run them to verify they fail**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_workflows.py -q
```
Expected: FAIL — `ValueError: No ComfyUI template for operation 'edit' in family 'flux2'`.

- [ ] **Step 3: Add the shared multi-file fragments**

In `core/inference/engines/comfyui_workflows/_fragments.py`, add the import
and the helpers:

```python
from core.inference.engines.base import GenerationRejected
```

```python
def _declared(config: dict, key: str, what: str) -> str:
    """One operator-declared companion from the connection's `config` (D8),
    or a refusal in the operator's own terms.

    A multi-file family is only runnable once all three of `family`,
    `text_encoder`, and `vae` have been declared at registration time (ADR
    0012 D-EDIT-2/3). A half-registered connection must fail the JOB with a
    sentence that says what to do -- `GenerationRejected` is exactly that
    signal, and `modules.vision.services.submit_job` already turns it into
    an immediately-failed job carrying this text.
    """
    value = str(config.get(key) or "").strip()
    if not value:
        raise GenerationRejected(
            f"This model's connection does not name {what}. Register it again with "
            "a model family, a text encoder, and a VAE."
        )
    return value


def _diffusion_model(graph: Graph, model_id: str) -> Link:
    """Load a diffusion-model file with the loader that can read it.

    ComfyUI-GGUF's `UnetLoaderGGUF` reads a `.gguf`; the built-in
    `UNETLoader` reads a safetensors (and takes a `weight_dtype` the GGUF
    loader has no input for). The EXTENSION of the file the operator named
    decides -- a fact of that file, never a pattern matched against a list
    of model names this platform ships.
    """
    if model_id.lower().endswith(".gguf"):
        return [graph.add("UnetLoaderGGUF", unet_name=model_id), 0]
    return [graph.add("UNETLoader", unet_name=model_id, weight_dtype="default"), 0]


def _text_encoder(graph: Graph, clip_name: str, family: str) -> Link:
    """Load the connection's declared text encoder, as the declared family's
    `type`. Same extension rule as `_diffusion_model`, for the same reason.

    `CLIPLoaderGGUF` takes NO `device` input (verified against a live
    ComfyUI 0.33.0), unlike the built-in `CLIPLoader` -- passing one would
    be a graph ComfyUI rejects.
    """
    if clip_name.lower().endswith(".gguf"):
        return [graph.add("CLIPLoaderGGUF", clip_name=clip_name, type=family), 0]
    return [
        graph.add("CLIPLoader", clip_name=clip_name, type=family, device="default"), 0
    ]


def components(graph: Graph, model_id: str, config: dict) -> Checkpoint:
    """A multi-file family's three loaders, handed back with the SAME names
    a checkpoint loader's three outputs have -- so every fragment downstream
    (`encode_text`, `encode_image`, `decode_and_save`) works
    unchanged for a family pipeline.

    `config` is the bound connection's own `ModelConnection.config` (D8):
    `family`, `text_encoder`, and `vae`, all three declared by the operator
    at registration time. `model_id` is ComfyUI's own opaque string and is
    passed through untouched.
    """
    family = _declared(config, "family", "a model family")
    return Checkpoint(
        model=_diffusion_model(graph, model_id),
        clip=_text_encoder(graph, _declared(config, "text_encoder", "a text encoder"), family),
        vae=[graph.add("VAELoader", vae_name=_declared(config, "vae", "a VAE")), 0],
    )


def encode_text(graph: Graph, ckpt: Checkpoint, text: str) -> Link:
    """Encode ONE piece of text against the loaded CLIP.

    A param the operation does not declare (or declares and left blank)
    encodes as the empty string: `None` is not text ComfyUI can encode.
    """
    return [graph.add("CLIPTextEncode", text=text or "", clip=ckpt.clip), 0]


def scale_to_megapixels(graph: Graph, image: Link, megapixels: float = 1.0) -> Link:
    """Resample an image to a total pixel budget, the way every edit graph
    this engine ships normalizes its input before encoding it.

    `upscale_method`, `megapixels`, and `resolution_steps` are fixed graph
    facts, not operator params: only one of the two edit families has a node
    with a megapixel input at all, so exposing one would be a control that
    silently does nothing for the other (ADR 0012 D-EDIT-1).
    """
    return [
        graph.add(
            "ImageScaleToTotalPixels",
            image=image,
            upscale_method="area",
            megapixels=megapixels,
            resolution_steps=1,
        ),
        0,
    ]


def image_size(graph: Graph, image: Link) -> tuple[Link, Link]:
    """`(width, height)` links read off an image already in the graph -- so
    an edit's output size is the operator's own picture's size and never a
    number this platform invented."""
    node = graph.add("GetImageSize", image=image)
    return [node, 0], [node, 1]
```

Also refactor the existing `prompts()` to build on `encode_text` (identical
node order and ids, one definition of "encode a prompt"):

```python
def prompts(graph: Graph, ckpt: Checkpoint, params: dict) -> tuple[Link, Link]:
    """Encode the positive and negative prompts, in that order."""
    return (
        encode_text(graph, ckpt, params.get("prompt")),
        encode_text(graph, ckpt, params.get("negative_prompt")),
    )
```

- [ ] **Step 4: Write the template**

Create `core/inference/engines/comfyui_workflows/flux2_edit.py`:

```python
"""Instruction-based image editing for the `flux2` model family.

Derived node-for-node from the "Image Edit (Flux.2 Dev)" workflow ComfyUI
0.33.0 ships in its own `comfyui_workflow_templates_json` bundle, minus that
workflow's optional distillation-LoRA toggle (a `ComfySwitchNode` /
`PrimitiveInt` / `PrimitiveBoolean` cluster around a `LoraLoaderModelOnly`)
-- the platform ships no such LoRA, and the `edit` operation declares no
LoRA param.

The pipeline: load the three declared files, encode the instruction once and
guide it, then chain one `ReferenceLatent` per input image so the model can
see what it is editing. Chain order IS the reference numbering an
instruction can refer to -- the image being edited is Reference Image 1.
Sampling is `SamplerCustomAdvanced` over this family's own scheduler, whose
sigmas are computed from the edited image's real size, so nothing here
rescales the operator's picture.

`family`, `text_encoder`, and `vae` come from the connection's `config`
(D8, ADR 0012 D-EDIT-2/3) -- declared by the operator at registration time,
never inferred from a filename.
"""
from __future__ import annotations

from core.inference.engines.comfyui_workflows import _fragments
from core.inference.operations import GenerationRequest

# The sampler this family's shipped edit workflow selects. Fixed here rather
# than exposed as a param: the schedule comes from `Flux2Scheduler`, which
# has no scheduler input at all, so a sampler/scheduler pair on the form
# would be half a control (ADR 0012 D-EDIT-1).
SAMPLER = "euler"


def build(request: GenerationRequest, model_id: str, config: dict, inputs: dict) -> dict:
    """The API-format graph for one `flux2` edit request.

    `inputs["init_image"]` is the reference ComfyUI gave the transferred
    file and is INDEXED, not `.get()`: it is a required file param, so an
    absent reference means the layer above skipped the transfer, and a
    `KeyError` naming the param is a far better report than a graph ComfyUI
    rejects for `image: None`. `reference_image` is optional and is read
    with `.get`.
    """
    params = request.params
    graph = _fragments.Graph()
    parts = _fragments.components(graph, model_id, config)

    conditioning = _fragments.encode_text(graph, parts, params.get("instruction"))
    conditioning = _flux_guidance(graph, conditioning, float(params["guidance"]))

    # Reference Image 1: the picture being edited. Its size is the output's.
    edited = _fragments.scale_to_megapixels(
        graph, _fragments.load_image(graph, inputs["init_image"])
    )
    width, height = _fragments.image_size(graph, edited)
    conditioning = _reference_latent(
        graph, conditioning, _fragments.encode_image(graph, parts, edited)
    )

    # Reference Image 2, when the operator attached one.
    reference = inputs.get("reference_image")
    if reference:
        extra = _fragments.scale_to_megapixels(graph, _fragments.load_image(graph, reference))
        conditioning = _reference_latent(
            graph, conditioning, _fragments.encode_image(graph, parts, extra)
        )

    latent = _empty_latent(graph, width, height)
    sigmas = _sigmas(graph, params["steps"], width, height)
    samples = _sample(graph, parts.model, conditioning, sigmas, latent, params["seed"])
    _fragments.decode_and_save(graph, parts, samples, request)
    return graph.as_dict()


def _flux_guidance(graph: _fragments.Graph, conditioning: list, guidance: float) -> list:
    """This family's instruction-adherence control, applied to the encoded
    instruction."""
    return [graph.add("FluxGuidance", conditioning=conditioning, guidance=guidance), 0]


def _reference_latent(graph: _fragments.Graph, conditioning: list, latent: list) -> list:
    """Attach one encoded image to the conditioning as a reference. Chained
    once per input image, in the order the operation declares them."""
    return [graph.add("ReferenceLatent", conditioning=conditioning, latent=latent), 0]


def _empty_latent(graph: _fragments.Graph, width: list, height: list) -> list:
    """The latent this family samples into, at the edited image's own size.

    `batch_size` is pinned at 1 rather than taken from a param: `edit`
    declares none, because the other edit family's bundled workflow has no
    batching node at all and a control only half the models honour is a lie
    (ADR 0012 D-EDIT-1).
    """
    return [
        graph.add("EmptyFlux2LatentImage", width=width, height=height, batch_size=1),
        0,
    ]


def _sigmas(graph: _fragments.Graph, steps: int, width: list, height: list) -> list:
    """This family's own noise schedule, which is resolution-aware -- which
    is why it takes the image's real size rather than a fixed pair."""
    return [graph.add("Flux2Scheduler", steps=steps, width=width, height=height), 0]


def _sample(
    graph: _fragments.Graph,
    model: list,
    conditioning: list,
    sigmas: list,
    latent: list,
    seed: int,
) -> list:
    """Guider + sampler + noise into `SamplerCustomAdvanced`. One
    conditioning, not two: this family's guider takes a single one, which is
    why `edit` declares no negative prompt."""
    guider = graph.add("BasicGuider", model=model, conditioning=conditioning)
    sampler = graph.add("KSamplerSelect", sampler_name=SAMPLER)
    noise = graph.add("RandomNoise", noise_seed=seed)
    node = graph.add(
        "SamplerCustomAdvanced",
        noise=[noise, 0],
        guider=[guider, 0],
        sampler=[sampler, 0],
        sigmas=sigmas,
        latent_image=latent,
    )
    return [node, 0]
```

- [ ] **Step 5: Register it**

In `core/inference/engines/comfyui_workflows/__init__.py`, extend the import
and add the entry:

```python
from core.inference.engines.comfyui_workflows import (
    flux2_edit, img2img, inpaint, txt2img, upscale,
)
...
    ("flux2", "edit"): flux2_edit.build,
```

- [ ] **Step 6: Run the tests to verify they pass**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_workflows.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_engine.py -q
```
Expected: PASS. `test_families_are_the_engines_own_vocabulary_narrowed_to_what_we_can_run`
now sees one family; if it asserted emptiness, it already asserts
`set(offered) == set(families())` and needs no edit.

- [ ] **Step 7: Run the vision suite**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests -q
```
Expected: PASS.

- [ ] **Step 8: LIVE VERIFICATION — one generation, through the governed queue**

**This is the first time this family's weights are loaded, and it happens
only through the queue.** The machine OOM-crashed on 2026-08-24 running this
model outside it. Do every precondition, in order, and abort on any failure.

Preconditions:
- [ ] The memory-seams work has landed and `ComfyUIEngine.loaded_footprint` / `unload` exist (Task 0). If not, STOP.
- [ ] `ls -la <home>/ComfyUI/models/text_encoders/` and confirm it still matches the "Installed files" block: the Q4_K_M GGUF instruct encoder and the vision-language encoder, no fp8 encoder. An fp8 encoder upcasts to bf16 on MPS and blows the ~32 GB budget; if one has reappeared, STOP.
- [ ] Quit Chrome. `ps aux | grep -c "[C]hrome"` should be 0.
- [ ] Bring the branch preview stack DOWN.
- [ ] `curl -s http://localhost:8188/system_stats` — record `system.ram_free`. It must be comfortably above the pipeline's ~32 GB peak with the Q4 GGUF encoder; abort if not.
- [ ] `curl -s http://localhost:8188/queue` — both lists must be empty.
- [ ] Confirm no RAG LLM is resident (the queue's eviction is what should enforce this — verify it did, don't do it by hand).

Then, entirely through the UI:
- [ ] In `/inference/`, register the diffusion-model file with `family` = the family key, and the encoder and VAE the report names. Bind `vision.generate` to it.
- [ ] In `/vision/`, choose **Edit an image**, attach one small image, type a short instruction, submit. The submission goes through `/vision/generate/` → the queue.
- [ ] Watch `/queue/`. Record: admission, the elapsed-seconds progress line, and the terminal state.
- [ ] Record the outcome: the generated image on the card, or the job's honest failure text.
- [ ] `curl -s http://localhost:8188/system_stats` again and record `ram_free` at rest.

If the graph is rejected, ComfyUI's own words are on the job row — read them,
fix the template, re-run the unit tests, and repeat. **Do not** debug by
posting a graph to `/prompt` yourself.

**Evidence, not a completion claim.** Write what happened into the plan's
"Live verification log" section at the bottom. No "works"/"done" language.

- [ ] **Step 9: Docs and commit**

Add to `modules/vision/README.md`'s edit section a sentence naming
`flux2_edit.py` as the first family template and pointing at the bundled
ComfyUI workflow it was derived from.

```bash
git add core/inference/engines/comfyui_workflows/flux2_edit.py \
        core/inference/engines/comfyui_workflows/_fragments.py \
        core/inference/engines/comfyui_workflows/__init__.py \
        modules/vision/tests/test_comfyui_workflows.py modules/vision/README.md \
        docs/superpowers/plans/2026-08-25-vision-edit-capability.md
git commit -m "$(cat <<'EOF'
feat(vision): the first family edit graph, and the multi-file loaders it needs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 7: The `qwen_image` edit graph, verified live through the queue

**Files:**
- Create: `core/inference/engines/comfyui_workflows/qwen_edit.py`
- Modify: `core/inference/engines/comfyui_workflows/__init__.py` (register `("qwen_image", "edit")`)
- Test: `modules/vision/tests/test_comfyui_workflows.py`
- Docs: `modules/vision/README.md`

**Interfaces:**
- Consumes: every `_fragments` helper Task 6 added, unchanged.
- Produces: the `("qwen_image", "edit")` template. No new shared fragments.

- [ ] **Step 1: Write the failing tests**

Append to `modules/vision/tests/test_comfyui_workflows.py`:

```python
QWEN_CONFIG = {
    "family": "qwen_image",
    "text_encoder": "vl-encoder.safetensors",
    "vae": "family-vae.safetensors",
}


class TestQwenEditGraph:
    def test_the_family_is_registered_and_offered(self):
        assert "qwen_image" in families()
        assert template_keys("qwen_image") == ("edit",)

    def test_it_reuses_the_same_declared_files_and_loaders(self):
        nodes = _by_class(_edit_graph("qwen_image", QWEN_CONFIG, {"init_image": "in/sofa.png"}))
        assert nodes["UnetLoaderGGUF"][0]["inputs"]["unet_name"] == "weights-Q4_K_S.gguf"
        assert nodes["CLIPLoader"][0]["inputs"] == {
            "clip_name": "vl-encoder.safetensors", "type": "qwen_image", "device": "default",
        }

    def test_the_model_passes_through_this_familys_own_two_stage_chain(self):
        graph = _edit_graph("qwen_image", QWEN_CONFIG, {"init_image": "in/sofa.png"})
        nodes = _by_class(graph)
        unet_id = next(nid for nid, n in graph.items() if n["class_type"] == "UnetLoaderGGUF")
        shift_id = next(
            nid for nid, n in graph.items() if n["class_type"] == "ModelSamplingAuraFlow"
        )
        assert nodes["ModelSamplingAuraFlow"][0]["inputs"] == {
            "model": [unet_id, 0], "shift": 3.1,
        }
        assert nodes["CFGNorm"][0]["inputs"] == {"model": [shift_id, 0], "strength": 1.0}
        assert nodes["KSampler"][0]["inputs"]["model"] == [
            next(nid for nid, n in graph.items() if n["class_type"] == "CFGNorm"), 0
        ]

    def test_the_instruction_and_an_empty_negative_are_encoded_against_the_same_images(self):
        """This family's sampler takes a positive and a negative, and its
        shipped workflow leaves the negative's prompt empty -- which is why
        `edit` declares no negative-prompt param."""
        nodes = _by_class(_edit_graph("qwen_image", QWEN_CONFIG, {"init_image": "in/sofa.png"}))
        positive, negative = nodes["TextEncodeQwenImageEditPlus"]
        assert positive["inputs"]["prompt"] == "put a red hat on the woman"
        assert negative["inputs"]["prompt"] == ""
        assert positive["inputs"]["image1"] == negative["inputs"]["image1"]
        assert "image2" not in positive["inputs"]

    def test_both_conditionings_pass_through_the_reference_method_node(self):
        graph = _edit_graph("qwen_image", QWEN_CONFIG, {"init_image": "in/sofa.png"})
        nodes = _by_class(graph)
        assert len(nodes["FluxKontextMultiReferenceLatentMethod"]) == 2
        assert all(
            node["inputs"]["reference_latents_method"] == "index_timestep_zero"
            for node in nodes["FluxKontextMultiReferenceLatentMethod"]
        )
        method_ids = [
            nid for nid, n in sorted(graph.items(), key=lambda i: int(i[0]))
            if n["class_type"] == "FluxKontextMultiReferenceLatentMethod"
        ]
        sampler = nodes["KSampler"][0]["inputs"]
        assert sampler["positive"] == [method_ids[0], 0]
        assert sampler["negative"] == [method_ids[1], 0]

    def test_only_the_edited_image_is_scaled_and_it_is_what_gets_encoded(self):
        """The bundled workflow scales the image being edited and wires any
        second reference raw -- reproduced rather than improved upon."""
        graph = _edit_graph(
            "qwen_image", QWEN_CONFIG,
            {"init_image": "in/sofa.png", "reference_image": "in/fur.png"},
        )
        nodes = _by_class(graph)
        assert len(nodes["FluxKontextImageScale"]) == 1
        scale_id = next(
            nid for nid, n in graph.items() if n["class_type"] == "FluxKontextImageScale"
        )
        assert nodes["VAEEncode"][0]["inputs"]["pixels"] == [scale_id, 0]
        positive = nodes["TextEncodeQwenImageEditPlus"][0]["inputs"]
        assert positive["image1"] == [scale_id, 0]
        assert positive["image2"] == [
            next(
                nid for nid, n in sorted(graph.items(), key=lambda i: int(i[0]))
                if n["class_type"] == "LoadImage" and n["inputs"]["image"] == "in/fur.png"
            ),
            0,
        ]

    def test_the_encoded_input_is_the_latent_sampled_from_directly(self):
        """This family samples from the input image's OWN latent, with no
        batching node between them -- the bundled workflow has none, which
        is why `edit` declares no `batch_size` (R1: one output per edit)."""
        graph = _edit_graph("qwen_image", QWEN_CONFIG, {"init_image": "in/sofa.png"})
        nodes = _by_class(graph)
        assert "RepeatLatentBatch" not in nodes
        encode_id = next(nid for nid, n in graph.items() if n["class_type"] == "VAEEncode")
        assert nodes["KSampler"][0]["inputs"]["latent_image"] == [encode_id, 0]

    def test_the_sampler_carries_the_shared_params_and_this_familys_fixed_ones(self):
        nodes = _by_class(_edit_graph("qwen_image", QWEN_CONFIG, {"init_image": "in/sofa.png"}))
        inputs = nodes["KSampler"][0]["inputs"]
        assert inputs["seed"] == 987654
        assert inputs["steps"] == 20
        assert inputs["cfg"] == 4.0
        assert inputs["sampler_name"] == "euler"
        assert inputs["scheduler"] == "simple"
        assert inputs["denoise"] == 1.0

    def test_a_connection_missing_a_companion_is_refused_the_same_way(self):
        with pytest.raises(GenerationRejected):
            _edit_graph("qwen_image", {"family": "qwen_image"}, {"init_image": "in/sofa.png"})
```

- [ ] **Step 2: Run them to verify they fail**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_workflows.py -q
```
Expected: FAIL — `ValueError: No ComfyUI template for operation 'edit' in family 'qwen_image'`.

- [ ] **Step 3: Write the template**

Create `core/inference/engines/comfyui_workflows/qwen_edit.py`:

```python
"""Instruction-based image editing for the `qwen_image` model family.

Derived node-for-node from the "Image Edit (Qwen-Image 2511)" workflow
ComfyUI 0.33.0 ships in its own `comfyui_workflow_templates_json` bundle,
minus that workflow's optional distillation-LoRA toggle (a `ComfySwitchNode`
/ `PrimitiveInt` / `PrimitiveFloat` cluster around a `LoraLoaderModelOnly`)
-- the platform ships no such LoRA, and the `edit` operation declares no
LoRA param.

Same operation, an entirely different pipeline from the other edit family:
the model passes through a two-stage chain before sampling, the instruction
and an empty negative are encoded by one node that takes the IMAGES as well
as the text, both conditionings pass through a reference-method node, and
sampling is a plain `KSampler` from the input image's own latent, with no
batching node between the two (the bundled workflow has none, which is one
of the reasons `edit` declares no `batch_size`). This is exactly why the
template registry is keyed by `(family, operation)`.

`family`, `text_encoder`, and `vae` come from the connection's `config`
(D8, ADR 0012 D-EDIT-2/3) -- declared by the operator at registration time,
never inferred from a filename.
"""
from __future__ import annotations

from core.inference.engines.comfyui_workflows import _fragments
from core.inference.operations import GenerationRequest

# Fixed graph facts from this family's shipped edit workflow. None of them is
# an operator param: each is honoured by this family alone, and `edit`
# declares only what EVERY edit family wires (ADR 0012 D-EDIT-1).
SHIFT = 3.1
CFG_NORM_STRENGTH = 1.0
REFERENCE_METHOD = "index_timestep_zero"
SAMPLER = "euler"
SCHEDULER = "simple"


def build(request: GenerationRequest, model_id: str, config: dict, inputs: dict) -> dict:
    """The API-format graph for one `qwen_image` edit request.

    `inputs["init_image"]` is INDEXED (a required file param — an absent
    reference is a bug above this line and should name itself);
    `reference_image` is optional and read with `.get`.
    """
    params = request.params
    graph = _fragments.Graph()
    parts = _fragments.components(graph, model_id, config)

    # Only the image being EDITED is resized, exactly as the shipped
    # workflow wires it: a second reference goes in at its own size.
    edited = _kontext_scale(graph, _fragments.load_image(graph, inputs["init_image"]))
    images = [edited]
    reference = inputs.get("reference_image")
    if reference:
        images.append(_fragments.load_image(graph, reference))

    positive = _conditioning(graph, parts, params.get("instruction"), images)
    negative = _conditioning(graph, parts, "", images)

    latent = _fragments.encode_image(graph, parts, edited)
    samples = _sample(
        graph, _model_chain(graph, parts.model), positive, negative, latent, params
    )
    _fragments.decode_and_save(graph, parts, samples, request)
    return graph.as_dict()


def _kontext_scale(graph: _fragments.Graph, image: _fragments.Link) -> _fragments.Link:
    """This family's own input normalizer. It takes no megapixel budget --
    which is precisely why `edit` declares no megapixels param."""
    return [graph.add("FluxKontextImageScale", image=image), 0]


def _model_chain(graph: _fragments.Graph, model: _fragments.Link) -> _fragments.Link:
    """The two model-wrapping nodes this family's shipped workflow puts
    between its loader and its sampler."""
    shifted = graph.add("ModelSamplingAuraFlow", model=model, shift=SHIFT)
    normed = graph.add("CFGNorm", model=[shifted, 0], strength=CFG_NORM_STRENGTH)
    return [normed, 0]


def _conditioning(
    graph: _fragments.Graph,
    parts: _fragments.Checkpoint,
    prompt: str | None,
    images: list,
) -> _fragments.Link:
    """One conditioning: this family's text encoder takes the IMAGES as well
    as the text, and the result passes through a reference-method node
    before it reaches the sampler.

    Called twice -- once with the instruction, once with the empty string --
    because this family's sampler takes a positive AND a negative, and the
    shipped workflow leaves the negative's prompt empty. That is why `edit`
    declares no negative-prompt param: there is nothing honest for an
    operator to put there on the other family.
    """
    node_inputs = {"clip": parts.clip, "vae": parts.vae, "prompt": prompt or ""}
    for index, image in enumerate(images, start=1):
        node_inputs[f"image{index}"] = image
    encoded = graph.add("TextEncodeQwenImageEditPlus", **node_inputs)
    method = graph.add(
        "FluxKontextMultiReferenceLatentMethod",
        conditioning=[encoded, 0],
        reference_latents_method=REFERENCE_METHOD,
    )
    return [method, 0]


def _sample(
    graph: _fragments.Graph,
    model: _fragments.Link,
    positive: _fragments.Link,
    negative: _fragments.Link,
    latent: _fragments.Link,
    params: dict,
) -> _fragments.Link:
    """A plain `KSampler` at full denoise. `guidance` lands on `cfg` here and
    on the other family's own guidance node -- one operator control, two
    honest wirings."""
    node = graph.add(
        "KSampler",
        model=model,
        seed=params["seed"],
        steps=params["steps"],
        cfg=float(params["guidance"]),
        sampler_name=SAMPLER,
        scheduler=SCHEDULER,
        positive=positive,
        negative=negative,
        latent_image=latent,
        denoise=1.0,
    )
    return [node, 0]
```

- [ ] **Step 4: Register it**

In `core/inference/engines/comfyui_workflows/__init__.py`:

```python
from core.inference.engines.comfyui_workflows import (
    flux2_edit, img2img, inpaint, qwen_edit, txt2img, upscale,
)
...
    ("qwen_image", "edit"): qwen_edit.build,
```

- [ ] **Step 5: Run the tests to verify they pass**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_workflows.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests -q
```
Expected: PASS.

- [ ] **Step 6: LIVE VERIFICATION — one generation, through the governed queue, ONE MODEL AT A TIME**

- [ ] Confirm the other family's model is NOT resident: `curl -s http://localhost:8188/system_stats` and compare `ram_free` against the at-rest number recorded in Task 6. If it is still warm, let the queue's own eviction handle it — enqueue the job and watch it evict. Do NOT `POST /free` by hand.
- [ ] Chrome quit; preview stack down; `/queue` empty.
- [ ] In `/inference/`, register the second diffusion-model file with its own family, encoder, and VAE.
- [ ] In `/vision/`, choose **Edit an image**, attach one small image with legible text in it (this family's stated strength), give a compound instruction, submit through the page.
- [ ] Watch `/queue/`: record admission, whether the other model was evicted first, progress, and the terminal state.
- [ ] Record the outcome and the at-rest `ram_free`.

**Evidence, not a completion claim.** Append to the "Live verification log".

- [ ] **Step 7: Docs and commit**

```bash
git add core/inference/engines/comfyui_workflows/qwen_edit.py \
        core/inference/engines/comfyui_workflows/__init__.py \
        modules/vision/tests/test_comfyui_workflows.py modules/vision/README.md \
        docs/superpowers/plans/2026-08-25-vision-edit-capability.md
git commit -m "$(cat <<'EOF'
feat(vision): the second family edit graph — same operation, its own pipeline

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 8: One picker-option rule, two capabilities

**Files:**
- Modify: `console/inference/bindings.py`
- Test: `console/inference/tests/test_db_bindings.py`
- Docs: `console/inference/bindings.py` module docstring

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `connections_for_picker(capability: str) -> list[ModelConnection]`; `role_primary(role_key: str) -> tuple[str, int | None]`. `chat_connections_for_picker()` and `answer_role_primary()` survive as thin wrappers with unchanged behaviour.

- [ ] **Step 1: Write the failing tests**

Append to `console/inference/tests/test_db_bindings.py`:

```python
class TestPickerSourcesAreOneRule:
    def test_connections_for_picker_filters_by_capability_in_picker_order(self):
        ModelConnection.objects.create(
            name="zeta image", engine="comfyui", endpoint="http://c:8188",
            model_id="z.safetensors", capabilities=["image-generation"], rank=2,
        )
        ModelConnection.objects.create(
            name="alpha image", engine="comfyui", endpoint="http://c:8188",
            model_id="a.safetensors", capabilities=["image-generation"], rank=1,
        )
        ModelConnection.objects.create(
            name="a chat model", engine="ollama", endpoint="http://o:11434",
            model_id="llama", capabilities=["chat"],
        )

        picked = connections_for_picker("image-generation")

        assert [c.name for c in picked] == ["alpha image", "zeta image"]

    def test_a_multi_capability_connection_appears_in_every_picker_it_answers(self):
        """Registration stores a model's FULL reported capability list, so a
        model that both chats and generates belongs in both pickers -- and
        the filter must be membership, never `capabilities[0]`."""
        ModelConnection.objects.create(
            name="both", engine="comfyui", endpoint="http://c:8188",
            model_id="b.safetensors", capabilities=["chat", "image-generation"],
        )

        assert [c.name for c in connections_for_picker("image-generation")] == ["both"]
        assert [c.name for c in connections_for_picker("chat")] == ["both"]

    def test_chat_connections_for_picker_is_the_same_rule(self):
        ModelConnection.objects.create(
            name="a chat model", engine="ollama", endpoint="http://o:11434",
            model_id="llama", capabilities=["chat"],
        )
        assert chat_connections_for_picker() == connections_for_picker("chat")

    def test_role_primary_names_the_bound_connection_for_any_role(self):
        connection = ModelConnection.objects.create(
            name="bound image", engine="comfyui", endpoint="http://c:8188",
            model_id="b.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.update_or_create(
            role_key="vision.generate", defaults={"connection": connection}
        )

        assert role_primary("vision.generate") == ("bound image", connection.pk)

    def test_role_primary_is_empty_for_a_genuinely_unassigned_role(self):
        assert role_primary("vision.generate") == ("", None)

    def test_answer_role_primary_is_the_same_rule(self):
        connection = ModelConnection.objects.create(
            name="bound chat", engine="ollama", endpoint="http://o:11434",
            model_id="llama", capabilities=["chat"],
        )
        RoleBinding.objects.update_or_create(
            role_key="rag.answer", defaults={"connection": connection}
        )
        assert answer_role_primary() == role_primary("rag.answer")
```

- [ ] **Step 2: Run them to verify they fail**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console/inference/tests/test_db_bindings.py -q
```
Expected: FAIL — `ImportError: cannot import name 'connections_for_picker'`.

- [ ] **Step 3: Generalize both reads**

In `console/inference/bindings.py`, replace `chat_connections_for_picker`:

```python
def connections_for_picker(capability: str) -> list["ModelConnection"]:
    """Registered connections answering `capability`, in picker order -- the
    option source for EVERY per-request model picker there is.

    Reuses `ModelConnection.objects.picker_order()` unmodified, filtered in
    Python on capability MEMBERSHIP (never `capabilities[0]`: a connection
    stores its model's full reported capability list, so one model can
    honestly belong in two pickers). This is the same filter idiom
    `console/inference/views.py::_role_options` already uses per role,
    rather than a second sort/filter rule.

    One function, not one per capability, because the rule is the same one
    and a second copy is a second thing to drift.
    """
    from console.inference.models import ModelConnection

    return [
        connection
        for connection in ModelConnection.objects.picker_order()
        if capability in connection.capabilities
    ]


def chat_connections_for_picker() -> list["ModelConnection"]:
    """Chat-capable registered connections -- the Ask-time picker's option
    source, and modules/rag's original name for it. A thin wrapper over
    `connections_for_picker` so the two pickers cannot drift apart on what
    "in picker order, filtered to a capability" means.
    """
    return connections_for_picker("chat")
```

and replace `answer_role_primary` with a parameterized `role_primary` plus a
wrapper (the docstring below keeps every case the original documented):

```python
def role_primary(role_key: str) -> tuple[str, int | None]:
    """`(display_name, connection_pk)` for whatever currently answers
    `role_key` -- what a per-request model picker marks "(primary)" and
    preselects, and what a job's "answered by" / "generated by" line names on
    the un-overridden path.

    Three cases:
    - a `ModelConnection` is bound: `(connection.name, connection.pk)`.
    - no connection bound, but an explicit environment override resolves
      the role: `(f"{model_id} (environment override)", None)` -- there is
      no registry row to carry a pk, so a picker renders this as a
      synthetic, blank-value option instead of a pk-valued one. Only the
      two roles `env_provider` knows can ever reach this case; every other
      role falls through to the third.
    - neither: `("", None)` -- the role is genuinely unassigned.
    """
    from core.inference.bindings import env_provider

    connection = _bound_connection(role_key)
    if connection is not None:
        return connection.name, connection.pk

    try:
        override = env_provider(role_key)
    except Exception:  # noqa: BLE001 -- an unresolvable override just means no primary
        override = None
    if override is not None:
        return f"{override.model_id} (environment override)", None

    return "", None


def answer_role_primary() -> tuple[str, int | None]:
    """`role_primary` for `core.inference.roles.RAG_ANSWER_ROLE` --
    modules/rag's original name for it, kept so that module's one console
    import surface is unchanged."""
    from core.inference.roles import RAG_ANSWER_ROLE

    return role_primary(RAG_ANSWER_ROLE)
```

Update the module docstring's paragraph about `chat_connections_for_picker()`
and `answer_role_primary()` to name `connections_for_picker(capability)` and
`role_primary(role_key)` as the general forms, and to say that
`modules/vision` is now a second caller of this same surface.

- [ ] **Step 4: Run the tests to verify they pass**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console/inference/tests/test_db_bindings.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/rag/tests -q
```
Expected: PASS — the RAG suite is the proof that the wrappers preserved behaviour.

- [ ] **Step 5: Commit**

```bash
git add console/inference/bindings.py console/inference/tests/test_db_bindings.py
git commit -m "$(cat <<'EOF'
refactor(console): one picker-option rule and one role-primary rule, per capability

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 9: A generation can run on a connection the caller picked

**Files:**
- Modify: `modules/vision/services.py` (`preflight`, `_health_check`, `submit_job`)
- Modify: `modules/vision/jobs.py` (`_resolve_model`, `plan_generate`, `run_generate`, module docstring)
- Test: `modules/vision/tests/test_services.py`, `modules/vision/tests/test_jobs.py`
- Docs: `modules/vision/README.md` ("The service layer")

**Interfaces:**
- Consumes: `console.inference.bindings.resolve_connection` / `resolve_connection_named` (existing), `core.inference.roles.IMAGE_GENERATION_CAPABILITY`.
- Produces: `services.preflight(resolved: ResolvedModel | None = None) -> PreflightResult`; `services.submit_job(operation_key, raw_params, files=None, resolved=None) -> GenerationJob`; `jobs._resolve_model(payload) -> tuple[ResolvedModel, str]`; payload key `"connection"` (a pk as a string).

- [ ] **Step 1: Write the failing tests**

Append to `modules/vision/tests/test_services.py`:

```python
class TestPreflightOnAPickedConnection:
    def test_a_supplied_binding_is_health_checked_instead_of_the_role(self):
        """The picked model stands in for the role binding for this one
        call -- so an unbound role is NOT an error when the caller brought
        its own model."""
        engine = StubEngine(healthy=True)
        picked = ResolvedModel(
            engine="stubengine", model_id="picked.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with patch.dict(ENGINES, {"stubengine": engine}, clear=True):
            check = services.preflight(picked)

        assert check.state == "ready"
        assert check.resolved is picked

    def test_a_supplied_binding_whose_engine_is_down_is_unreachable_not_unbound(self):
        engine = StubEngine(healthy=False)
        picked = ResolvedModel(
            engine="stubengine", model_id="picked.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with patch.dict(ENGINES, {"stubengine": engine}, clear=True):
            check = services.preflight(picked)

        assert check.state == "unreachable"
        assert "http://stub:9999" in check.message

    def test_no_argument_still_resolves_the_role(self):
        """Every existing caller passes nothing and must behave exactly as
        it did."""
        assert services.preflight().state == "unbound"


class TestSubmitJobOnAPickedConnection:
    def test_the_job_records_the_binding_that_actually_ran_it(self):
        """D6: a job documents the model that ran it, not the one bound now
        -- which is why no new column is needed to record the pick."""
        generator = StubGenerator()
        engine = StubEngine(generator=generator)
        picked = ResolvedModel(
            engine="stubengine", model_id="picked.safetensors",
            endpoint="http://picked:9999", config={"family": "flux2"},
        )
        with patch.dict(ENGINES, {"stubengine": engine}, clear=True):
            job = services.submit_job("txt2img", dict(RAW), resolved=picked)

        assert job.engine == "stubengine"
        assert job.model_id == "picked.safetensors"
        assert job.endpoint == "http://picked:9999"
        assert job.model_config == {"family": "flux2"}
        assert engine.built == [("picked.safetensors", "http://picked:9999", {"family": "flux2"})]
```

Append to `modules/vision/tests/test_jobs.py`:

```python
class TestPickedConnectionTravelsInThePayload:
    def test_the_planner_plans_against_the_picked_connection(self):
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://picked:9999",
            model_id="picked.safetensors", capabilities=["image-generation"],
        )
        with patch.dict(ENGINES, {"stubengine": StubEngine()}, clear=True):
            models, exclusive = plan_generate(
                {"operation": "txt2img", "params": {}, "connection": str(connection.pk)}
            )

        assert exclusive is True
        assert [(m.engine, m.model_id, m.endpoint) for m in models] == [
            ("stubengine", "picked.safetensors", "http://picked:9999")
        ]
        assert models[0].connection_name == "picked"

    def test_a_payload_with_no_connection_still_plans_against_the_role(self):
        """A blank or absent field is exactly today's behaviour, which is
        what every job enqueued before the picker existed carries."""
        connection = ModelConnection.objects.create(
            name="bound", engine="stubengine", endpoint="http://bound:9999",
            model_id="bound.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.update_or_create(
            role_key="vision.generate", defaults={"connection": connection}
        )
        with patch.dict(ENGINES, {"stubengine": StubEngine()}, clear=True):
            models, _exclusive = plan_generate({"operation": "txt2img", "params": {}})
            blank, _ = plan_generate(
                {"operation": "txt2img", "params": {}, "connection": ""}
            )

        assert models[0].model_id == "bound.safetensors"
        assert blank[0].model_id == "bound.safetensors"

    def test_a_pk_that_no_longer_resolves_fails_the_job_loudly(self):
        """A deleted connection is a bad payload, not something to quietly
        substitute the role binding for -- the operator asked for a model
        that is gone and must be told."""
        with pytest.raises(ValueError):
            plan_generate({"operation": "txt2img", "params": {}, "connection": "999999"})

    def test_the_handler_runs_the_generation_on_the_picked_connection(self):
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://picked:9999",
            model_id="picked.safetensors", capabilities=["image-generation"],
        )
        generator = StubGenerator(states=[JobStatus(state="done")], outputs=[])
        engine = StubEngine(generator=generator)
        with patch.dict(ENGINES, {"stubengine": engine}, clear=True):
            run_generate(
                {
                    "operation": "txt2img",
                    "params": dict(RAW),
                    "connection": str(connection.pk),
                },
                [],
                make_job_ctx(),
            )

        assert engine.built[0][0] == "picked.safetensors"
```

- [ ] **Step 2: Run them to verify they fail**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_services.py -q
```
Expected: FAIL — `preflight() takes 0 positional arguments but 1 was given`.

- [ ] **Step 3: Split the health check out of `preflight`**

In `modules/vision/services.py`, replace `preflight`:

```python
def _health_check(resolved: ResolvedModel) -> PreflightResult:
    """Health-check an ALREADY-resolved binding.

    The half of `preflight` that is identical whether the model came from
    the role or from a caller's own pick, written once so a picked model can
    never be checked by a different rule than a bound one. Engine-agnostic:
    the message names the engine and endpoint from the binding rather than
    hardcoding a product name.
    """
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


def preflight(resolved: ResolvedModel | None = None) -> PreflightResult:
    """Resolve the image-generation role and health-check its engine.

    `resolved` is a binding the CALLER already picked -- the per-generation
    model picker's choice, resolved through
    `console.inference.bindings.resolve_connection` before it gets here. It
    stands in for the role binding for this one call and never changes it,
    exactly as the Ask-time picker's override does for `rag.answer`. When it
    is given, the role is not consulted at all: a caller who brought their
    own model is not blocked by an unbound role.

    Three honest states either way, mirroring `AskView._precheck_models`:
    `ready`, `unbound` (nothing assigned -- only reachable on the role
    path), or `unreachable` (assigned but the engine isn't answering).
    """
    if resolved is not None:
        return _health_check(resolved)

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

    return _health_check(resolved)
```

- [ ] **Step 4: Thread it through `submit_job`**

Change the signature and the one line that uses it:

```python
def submit_job(
    operation_key: str,
    raw_params: dict,
    files: dict | None = None,
    resolved: ResolvedModel | None = None,
) -> GenerationJob:
```

Add to its docstring, after the "Order matters" paragraph:

```
    `resolved` is the per-generation model the caller picked, already
    resolved (`console.inference.bindings.resolve_connection`). Passing it
    substitutes that binding for the role's, for this one job -- and the row
    written below records THAT binding, which is why recording the pick
    needs no new column: `engine`, `model_id`, `endpoint`,
    `model_fingerprint`, and `model_config` already document exactly what
    ran (D6).
```

and replace the preflight call:

```python
    check = preflight(resolved)
```

- [ ] **Step 5: Resolve the payload's pick in the job kind**

In `modules/vision/jobs.py`, add the import and the resolver:

```python
from console.inference.bindings import resolve_connection_named
from core.inference.roles import IMAGE_GENERATION_CAPABILITY, VISION_GENERATE_ROLE
```

```python
def _resolve_model(payload: dict) -> tuple[ResolvedModel, str]:
    """`(resolved, connection_name)` for the model this job runs on.

    An explicit `payload["connection"]` -- the per-generation picker's
    chosen `ModelConnection` pk, carried as a STRING so the payload stays
    plain JSON -- resolves through `console.inference.bindings.
    resolve_connection_named` (the pk-addressed twin of the role lookup, and
    the ONE override seam this platform has; there is no second mechanism).
    Anything else, including a blank field, resolves the `vision.generate`
    role, which is exactly what every job enqueued before the picker existed
    carries.

    A pk that no longer names a usable image-generation connection raises
    `ValueError`, uncaught: the operator asked for a model that is gone, and
    quietly substituting the role binding would run a generation on a model
    nobody chose.
    """
    connection = payload.get("connection")
    if connection not in (None, ""):
        return resolve_connection_named(int(connection), IMAGE_GENERATION_CAPABILITY)
    return resolve(VISION_GENERATE_ROLE), ""
```

Replace `plan_generate`'s body (and drop its `noqa: ARG001`, since `payload`
is now read):

```python
def plan_generate(payload: dict) -> tuple[list[ModelRef], bool]:
    """Resolve the model a `vision.generate` job needs, at ENQUEUE time.

    One `ModelRef` for the model this job will actually run on -- the
    payload's picked connection when it names one, else the current
    `vision.generate` binding (`_resolve_model`). `footprint_bytes` stays
    `None` per `console/jobs/scheduler.py`'s provenance contract: claim-time
    code fills it in fresh, never from this snapshot.

    `exclusive=True` always -- see the module docstring. Raises `ValueError`
    for an unbound role or an unusable picked pk, uncaught.
    """
    resolved, connection_name = _resolve_model(payload)
    model_refs = [
        ModelRef(
            role=VISION_GENERATE_ROLE,
            engine=resolved.engine,
            endpoint=resolved.endpoint,
            model_id=resolved.model_id,
            connection_name=connection_name,
        )
    ]
    return model_refs, True
```

In `run_generate`, replace the `submit_job` call:

```python
    # Re-resolved FRESH at claim time, never trusted from the enqueue-time
    # plan: the picked connection may have been edited, and the role may
    # have been rebound, between enqueue and claim.
    picked, _name = _resolve_model(payload)
    job = services.submit_job(operation_key, params, files=files or None, resolved=picked)
```

Update the module docstring: the `payload` shape gains `"connection": str | None`
(same field and same meaning as `rag.ask`'s), and the "Module boundary"
paragraph's claim that no `console.inference.*` import is needed is now false
— replace it with:

```
Module boundary: imports `core.inference.*`, `modules.vision.*`, and exactly
one console function (`console.inference.bindings.resolve_connection_named`),
the same single console import surface `modules/rag/jobs.py` uses for the
same reason: a per-job connection override is a pk this module must resolve,
and `console.inference.bindings` is the ONE place a §5 module reaches for it
-- never `console.inference.models` directly.
```

- [ ] **Step 6: Run the tests to verify they pass**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_services.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_jobs.py -q
```
Expected: PASS.

- [ ] **Step 7: Docs**

In `modules/vision/README.md`, under "The service layer", add:

```markdown
### Picking a model for one generation

`vision.generate`'s role binding is the DEFAULT model, not the only one. A
caller may hand `submit_job` a `resolved=` binding it picked itself, and the
queue payload carries that choice as `"connection": "<ModelConnection pk>"` —
byte-identical to the field `rag.ask` already carries, resolved through the
same `console.inference.bindings.resolve_connection_named`. There is exactly
one override mechanism, and this is it.

No new column records the pick: the `GenerationJob` row already stores
`engine`, `model_id`, `endpoint`, `model_fingerprint`, and `model_config` —
the full identity of the model that ran, which is what D6 promises and what
polling a job after a rebind already depends on.
```

- [ ] **Step 8: Commit**

```bash
git add modules/vision/services.py modules/vision/jobs.py \
        modules/vision/tests/test_services.py modules/vision/tests/test_jobs.py \
        modules/vision/README.md
git commit -m "$(cat <<'EOF'
feat(vision): a generation can run on a connection the caller picked

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 10: The operation list reflects the selected model

**Files:**
- Modify: `modules/vision/services.py` (`operations_for_model`, `operation_catalog`)
- Modify: `modules/vision/views.py` (`page_operations`, `resolve_page_operation`, `vision_operations`)
- Test: `modules/vision/tests/test_services.py`, `modules/vision/tests/test_views_operations.py`
- Docs: `modules/vision/README.md`

**Interfaces:**
- Consumes: `ComfyUIEngine.supported_operations(model_id, endpoint, family="")` (Task 2), `services.preflight(resolved)` (Task 9).
- Produces: `services.operations_for_model(resolved: ResolvedModel | None) -> list[Operation]`; `services.operation_catalog(resolved: ResolvedModel | None = None) -> list[dict]`; `views.page_operations(resolved=None) -> list[Operation]`; `views.resolve_page_operation(key, available=None) -> Operation`.

- [ ] **Step 1: Write the failing tests**

Append to `modules/vision/tests/test_services.py`:

```python
class TestOperationsForModel:
    def test_nothing_selected_lists_every_registered_operation(self):
        assert {op.key for op in services.operations_for_model(None)} == {
            "txt2img", "img2img", "inpaint", "upscale", "edit",
        }

    def test_a_model_only_offers_what_its_engine_has_a_graph_for(self):
        class _Narrow(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",) if family else ("txt2img",)

        picked = ResolvedModel(
            engine="stubengine", model_id="w.gguf",
            endpoint="http://stub:9999", config={"family": "flux2"},
        )
        with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
            assert [op.key for op in services.operations_for_model(picked)] == ["edit"]

    def test_a_connection_with_no_family_gets_the_checkpoint_modes(self):
        class _Narrow(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",) if family else ("txt2img",)

        picked = ResolvedModel(
            engine="stubengine", model_id="c.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
            assert [op.key for op in services.operations_for_model(picked)] == ["txt2img"]

    def test_an_engine_that_cannot_say_never_narrows_anything(self):
        """`supported_operations` is an OPTIONAL protocol member. An adapter
        without one has no opinion, and no opinion must never be read as
        'nothing supported'."""
        picked = ResolvedModel(
            engine="stubengine", model_id="c.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with patch.dict(ENGINES, {"stubengine": StubEngine()}, clear=True):
            assert len(services.operations_for_model(picked)) == 5

    def test_an_engine_that_raises_never_narrows_anything_either(self):
        class _Broken(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                raise RuntimeError("boom")

        picked = ResolvedModel(
            engine="stubengine", model_id="c.safetensors",
            endpoint="http://stub:9999", config={},
        )
        with patch.dict(ENGINES, {"stubengine": _Broken()}, clear=True):
            assert len(services.operations_for_model(picked)) == 5


class TestOperationCatalogFollowsTheSelectedModel:
    def test_the_catalog_lists_only_the_selected_models_operations(self):
        class _Narrow(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",)

        picked = ResolvedModel(
            engine="stubengine", model_id="w.gguf",
            endpoint="http://stub:9999", config={"family": "flux2"},
        )
        with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
            catalog = services.operation_catalog(picked)

        assert [entry["key"] for entry in catalog] == ["edit"]
```

Append to `modules/vision/tests/test_views_operations.py`:

```python
    def test_the_schema_endpoint_answers_for_the_picked_model(self):
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )

        class _Narrow(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",)

        with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
            response = self.client.get(
                reverse("vision-operations"), {"connection": str(connection.pk)}
            )

        assert [entry["key"] for entry in response.json()["operations"]] == ["edit"]

    def test_an_unusable_connection_falls_back_to_the_full_schema(self):
        """A stale link must never be an error: the endpoint answers for the
        role binding instead, which with nothing bound is every schema this
        platform ships."""
        response = self.client.get(reverse("vision-operations"), {"connection": "999999"})
        assert response.status_code == 200
        assert len(response.json()["operations"]) == 5
```

- [ ] **Step 2: Run them to verify they fail**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_services.py -q
```
Expected: FAIL — `AttributeError: module 'modules.vision.services' has no attribute 'operations_for_model'`.

- [ ] **Step 3: Implement the service-layer narrowing**

In `modules/vision/services.py`, add above `operation_catalog`:

```python
def operations_for_model(resolved: ResolvedModel | None) -> list[Operation]:
    """Every registered image-generation operation the SELECTED model can
    actually run, in registration order.

    The platform's operation registry says what modes EXIST; only the engine
    can say which of them it has a graph for THIS model -- and for a
    multi-file family that answer depends on the family the operator
    declared on the connection (`config["family"]`, ADR 0012 D-EDIT-2). A
    checkpoint connection declares none and gets the checkpoint modes.

    Silence is never read as refusal. `None` (nothing selected and nothing
    bound), an engine name that is not registered, an adapter with no
    `supported_operations` member at all, or one that raises -- each returns
    the FULL registered list, because "this engine has no opinion" and "this
    engine supports nothing" are different facts and only the second should
    ever empty the page.
    """
    available = operations_for(IMAGE_GENERATION_CAPABILITY)
    if resolved is None:
        return available
    try:
        engine = get_engine(resolved.engine)
    except Exception:  # noqa: BLE001 -- an unregistered engine name is not a 500
        logger.debug("get_engine(%r) failed", resolved.engine, exc_info=True)
        return available

    reader = getattr(engine, "supported_operations", None)
    if reader is None:
        return available
    try:
        supported = set(
            reader(
                resolved.model_id,
                resolved.endpoint,
                family=str((resolved.config or {}).get("family") or ""),
            )
        )
    except Exception:  # noqa: BLE001 -- never 500 over an operation list
        logger.debug("supported_operations failed for %r", resolved.model_id, exc_info=True)
        return available
    return [operation for operation in available if operation.key in supported]
```

and change `operation_catalog`'s first two lines:

```python
def operation_catalog(resolved: ResolvedModel | None = None) -> list[dict]:
    """... `resolved` is the model the caller picked; with none, the role
    binding answers, exactly as before. The catalog lists only what that
    model can run (`operations_for_model`) -- a tool reading this before it
    submits must never be told about a mode the model it is about to use
    cannot perform."""
    check = preflight(resolved)
    catalog = []
    for operation in operations_for_model(check.resolved):
```

- [ ] **Step 4: Make the page's two registry reads model-aware**

In `modules/vision/views.py`:

```python
def page_operations(resolved=None) -> list[Operation]:
    """Every operation this page can serve for the SELECTED model, in
    registration order. The first is the default a bare `/vision/` lands on.

    The page reads the REGISTRY, narrowed by the engine
    (`services.operations_for_model`) -- it names no operation of its own,
    so registering a mode in `VisionConfig.ready()` is the whole of making
    it appear here, and a model that cannot run one is the whole of making
    it disappear.
    """
    return services.operations_for_model(resolved)


def resolve_page_operation(key: str | None, available: list[Operation] | None = None) -> Operation:
    """The `Operation` a request names, or the default when it names none.

    A key that is not a registered image-generation operation AT ALL is a
    plain `Http404`: a stale link or a hand-typed URL must land on a 404,
    never on a form built from a schema this page cannot run.

    A key that IS registered but that the SELECTED model has no graph for is
    a different thing, and a 404 would be a lie about the platform: the
    operator switched models and their mode went away with it. That falls
    back to the selected model's first mode, and the chooser shows what that
    model does offer.

    A selected model that supports NOTHING falls back to the named (or
    default) registered operation, so the page still renders and its banner
    can explain -- an empty page explains nothing.
    """
    registered = operations_for(IMAGE_GENERATION_CAPABILITY)
    supported = available if available else registered
    if not key:
        return supported[0]
    named = next((operation for operation in registered if operation.key == key), None)
    if named is None:
        raise Http404(f"Unknown image-generation operation {key!r}.")
    if named.key not in {operation.key for operation in supported}:
        return supported[0]
    return named
```

and make the schema endpoint read the picked model (the picker helper itself
lands in Task 11; here it is the raw pk, resolved defensively):

```python
def vision_operations(request):
    """GET /vision/operations/ -- `services.operation_catalog()` as JSON
    (ADR 0012's payload-contract section).

    `?connection=<pk>` answers for THAT model rather than the role binding,
    the same pk the generation form posts -- so a tool reads the schema for
    the model it is about to use. An absent, malformed, or no-longer-usable
    pk answers for the role binding instead: a stale link must leave a
    normal page behind, never an error.
    """
    picked, _raw, _usable = picked_connection(request)
    return JsonResponse({"operations": services.operation_catalog(picked)})
```

(`picked_connection` is written in Task 11; if executing Tasks 10 and 11 as
separate commits, add the three-line helper in Task 10 and extend it in Task
11 rather than leaving `vision_operations` referring to something that does
not exist.)

- [ ] **Step 5: Run the tests to verify they pass**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_services.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_views_operations.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests -q
```
Expected: PASS. `CreatePageView`, `generate`, `input_targets`, and
`_create_page_response` all call `page_operations()` with no argument, which
still returns the full registry — Task 11 is what threads the selection
through them.

- [ ] **Step 6: Docs and commit**

Add to `modules/vision/README.md`'s edit section:

```markdown
The chooser is not the registry. `services.operations_for_model(resolved)`
narrows the registered modes to the ones the SELECTED model's engine has a
graph for, so switching models changes which modes the page offers. An
adapter that cannot answer the question narrows nothing: "no opinion" and
"supports nothing" are different facts, and only the second empties the list.
```

```bash
git add modules/vision/services.py modules/vision/views.py \
        modules/vision/tests/test_services.py modules/vision/tests/test_views_operations.py \
        modules/vision/README.md
git commit -m "$(cat <<'EOF'
feat(vision): the operation list reflects the selected model

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 11: The generation form picks a model

**Files:**
- Modify: `modules/vision/views.py`
- Modify: `modules/vision/templates/vision/create.html`
- Test: `modules/vision/tests/test_views_create.py`, `modules/vision/tests/test_views_generate.py`
- Docs: `modules/vision/README.md`

**Interfaces:**
- Consumes: `console.inference.bindings.connections_for_picker` / `role_primary` (Task 8); `services.preflight(resolved)` (Task 9); `page_operations(resolved)` / `resolve_page_operation(key, available)` (Task 10).
- Produces: `views.picked_connection(request) -> tuple[ResolvedModel | None, str, bool]`; `views._connection_picker_options(primary_pk) -> list[dict] | None`; context keys `connection_options`, `selected_connection`; the payload key `"connection"`.

- [ ] **Step 1: Write the failing tests**

Append to `modules/vision/tests/test_views_create.py`:

```python
class TestModelPicker:
    def test_the_bound_model_is_the_preselected_primary(self):
        connection = ModelConnection.objects.create(
            name="bound", engine="comfyui", endpoint="http://c:8188",
            model_id="b.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.update_or_create(
            role_key="vision.generate", defaults={"connection": connection}
        )

        options = self.client.get(reverse("vision-create")).context["connection_options"]

        assert options == [
            {"value": str(connection.pk), "label": "bound (primary)", "selected": True}
        ]

    def test_every_registered_image_connection_is_offered_with_its_descriptor(self):
        ModelConnection.objects.create(
            name="second", engine="comfyui", endpoint="http://c:8188",
            model_id="s.gguf", capabilities=["image-generation"],
            descriptor="faster, good with text", rank=2,
        )
        ModelConnection.objects.create(
            name="first", engine="comfyui", endpoint="http://c:8188",
            model_id="f.gguf", capabilities=["image-generation"], rank=1,
        )

        options = self.client.get(reverse("vision-create")).context["connection_options"]

        assert [opt["label"] for opt in options] == [
            "first", "second — faster, good with text",
        ]

    def test_a_chat_only_connection_is_never_offered(self):
        ModelConnection.objects.create(
            name="a chat model", engine="ollama", endpoint="http://o:11434",
            model_id="llama", capabilities=["chat"],
        )
        assert self.client.get(reverse("vision-create")).context["connection_options"] is None

    def test_the_picked_model_drives_the_banner_and_the_chooser(self):
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )

        class _Narrow(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",)

        with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
            response = self.client.get(
                reverse("vision-create"), {"connection": str(connection.pk)}
            )

        assert [op.key for op in response.context["operations"]] == ["edit"]
        assert response.context["operation"].key == "edit"
        assert response.context["preflight"].resolved.model_id == "w.gguf"
        assert response.context["selected_connection"] == str(connection.pk)

    def test_a_url_naming_a_mode_the_picked_model_cannot_run_falls_back(self):
        """Switching models must not 404 the page an operator is standing
        on -- the mode went away, the platform did not."""
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )

        class _Narrow(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",)

        with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
            response = self.client.get(
                reverse("vision-create-operation", args=["inpaint"]),
                {"connection": str(connection.pk)},
            )

        assert response.status_code == 200
        assert response.context["operation"].key == "edit"

    def test_an_unregistered_mode_is_still_a_404(self):
        assert self.client.get("/vision/not-a-mode/").status_code == 404

    def test_switching_models_cannot_cost_the_operator_their_work(self):
        """The picker is its OWN GET form, not a `formmethod="get"` button
        inside the multipart generate form: submitting that form as a GET
        would drop the attached image and the typed instruction, because the
        create page's GET handler reads only `?connection=`. The generate
        form learns the choice from a hidden field instead."""
        ModelConnection.objects.create(
            name="picked", engine="comfyui", endpoint="http://c:8188",
            model_id="w.safetensors", capabilities=["image-generation"],
        )
        body = self.client.get(reverse("vision-create")).content.decode()

        assert 'formmethod="get"' not in body
        assert '<form class="model-picker" method="get"' in body
        assert '<input type="hidden" name="connection"' in body

    def test_every_chooser_link_carries_the_picked_model(self):
        connection = ModelConnection.objects.create(
            name="picked", engine="comfyui", endpoint="http://c:8188",
            model_id="w.safetensors", capabilities=["image-generation"],
        )
        response = self.client.get(
            reverse("vision-create"), {"connection": str(connection.pk)}
        )
        body = response.content.decode()
        assert f"connection={connection.pk}" in body
```

Append to `modules/vision/tests/test_views_generate.py`. Everything these
tests need is ALREADY imported there as of `6f435f9` —
`SimpleUploadedFile`, `PNG`, `StubEngine`, `ModelConnection`, `RoleBinding`,
`ENGINES`, `patch`, `reverse` — except `RAW`, which must be added to the
existing `from modules.vision.tests._helpers import (...)` block:

```python
class TestGenerateCarriesThePickedModel:
    def test_the_pick_travels_in_the_queue_payload_as_a_pk_string(self):
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.safetensors", capabilities=["image-generation"],
        )
        with patch.dict(ENGINES, {"stubengine": StubEngine()}, clear=True), patch(
            "modules.vision.views.enqueue", return_value=7
        ) as enqueued:
            self.client.post(
                reverse("vision-generate"),
                {**RAW, "operation": "txt2img", "connection": str(connection.pk)},
            )

        payload = enqueued.call_args.args[1]
        assert payload["connection"] == str(connection.pk)

    def test_no_pick_enqueues_exactly_what_it_always_did(self):
        """Every existing caller, link, and bookmark sends no `connection`
        field, and must keep behaving identically."""
        connection = ModelConnection.objects.create(
            name="bound", engine="stubengine", endpoint="http://stub:9999",
            model_id="b.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.update_or_create(
            role_key="vision.generate", defaults={"connection": connection}
        )
        with patch.dict(ENGINES, {"stubengine": StubEngine()}, clear=True), patch(
            "modules.vision.views.enqueue", return_value=7
        ) as enqueued:
            self.client.post(reverse("vision-generate"), {**RAW, "operation": "txt2img"})

        assert "connection" not in enqueued.call_args.args[1]

    def test_an_edit_keeps_its_image_instruction_and_model_in_one_submission(self):
        """The behavioural guard for the picker's form separation: a real
        `edit` POST carries an uploaded file, a typed instruction, AND the
        picked model, and all three must reach the payload together.

        This is the exact regression a `formmethod="get"` picker button
        inside the multipart generate form would cause — the file dropped,
        the instruction discarded — and it is invisible to a template
        assertion, so it is pinned here instead.
        """
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )
        uploaded = SimpleUploadedFile("beach.png", PNG, content_type="image/png")

        class _EditOnly(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",)

        with patch.dict(ENGINES, {"stubengine": _EditOnly()}, clear=True), patch(
            "modules.vision.views.enqueue", return_value=7
        ) as enqueued:
            response = self.client.post(
                reverse("vision-generate"),
                {
                    "operation": "edit",
                    "instruction": "put a red hat on the woman",
                    "init_image": uploaded,
                    "guidance": "4.0",
                    "steps": "20",
                    "seed": "42",
                    "connection": str(connection.pk),
                },
            )

        assert response.status_code in (202, 302)
        payload = enqueued.call_args.args[1]
        assert payload["operation"] == "edit"
        assert payload["params"]["instruction"] == "put a red hat on the woman"
        # The file became a staged REFERENCE (`services.stage_upload`), which
        # is how a browser upload survives into a JSON queue payload at all.
        assert payload["inputs"]["init_image"].startswith("input:")
        assert payload["connection"] == str(connection.pk)

    def test_a_pick_that_no_longer_resolves_is_refused_and_nothing_is_queued(self):
        with patch("modules.vision.views.enqueue") as enqueued:
            response = self.client.post(
                reverse("vision-generate"),
                {**RAW, "operation": "txt2img", "connection": "999999"},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        assert response.status_code == 503
        assert "no longer registered" in response.content.decode()
        enqueued.assert_not_called()
```

- [ ] **Step 2: Run them to verify they fail**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_views_create.py -q
```
Expected: FAIL — `KeyError: 'connection_options'`.

- [ ] **Step 3: Add the picker helpers to the view module**

In `modules/vision/views.py`, add the imports and the three helpers:

```python
from console.inference.bindings import connections_for_picker, role_primary, resolve_connection
from core.inference.bindings import ResolvedModel
from core.inference.roles import IMAGE_GENERATION_CAPABILITY, VISION_GENERATE_ROLE
```

```python
# The picked pk named no usable image-generation connection -- deleted,
# or its capability removed, since the page rendered. Worded the way
# `modules/rag/views.py` words the same failure for the Ask picker.
_UNREGISTERED_CONNECTION_MESSAGE = (
    "That model is no longer registered for image generation — reload the page to see "
    "the current list, then pick again. Nothing was queued."
)


def picked_connection(request) -> tuple[ResolvedModel | None, str, bool]:
    """`(resolved, raw_pk, usable)` for the model this request picked.

    Read from the query string on a GET (the picker's own reload, and every
    chooser link) and from the POST on a submission, so the two halves of
    the flow agree without the page inventing a session -- exactly how
    `stored_input_refs` already reads a carried image reference.

    A blank or absent value is `(None, "", True)`: use the role binding,
    which is what every link, bookmark, and existing caller sends and is
    precisely today's behaviour. A pk that no longer resolves is
    `(None, raw, False)` -- the CALLER decides what that means, because the
    two callers want opposite things: a GET falls back to the role binding
    silently (a stale link must leave a normal page behind), while a POST
    refuses, since the operator explicitly chose a model and running a
    different one would be worse than saying no.
    """
    source = request.POST if request.method == "POST" else request.GET
    raw = (source.get("connection") or "").strip()
    if not raw:
        return None, "", True
    try:
        return resolve_connection(int(raw), IMAGE_GENERATION_CAPABILITY), raw, True
    except (TypeError, ValueError):
        logger.debug("Ignoring unusable image connection %r", raw, exc_info=True)
        return None, raw, False


def _connection_picker_options(selected: str) -> list[dict] | None:
    """The form's `Model` `<select>` options, in picker order, or `None` when
    there is nothing to pick from.

    The same grammar the Ask page's picker uses, from the same console
    surface: options are `connections_for_picker("image-generation")` (its
    own `picker_order()`, never re-derived here) with labels
    `{name} — {descriptor}` (bare `{name}` when undescribed). Whichever
    connection currently answers `vision.generate` (`role_primary`) gets
    ` (primary)` appended, and is preselected while the request has picked
    nothing else -- the role binding IS the default model, and the picker
    says so.

    `None` -- no `<select>` at all -- only when no registered
    image-generation connection exists; a lone option still renders as a
    (trivial) select rather than collapsing to plain text.
    """
    primary_name, primary_pk = role_primary(VISION_GENERATE_ROLE)
    connections = connections_for_picker(IMAGE_GENERATION_CAPABILITY)
    if not connections:
        return None

    options = []
    for connection in connections:
        label = (
            f"{connection.name} — {connection.descriptor}"
            if connection.descriptor
            else connection.name
        )
        if connection.pk == primary_pk:
            label = f"{label} (primary)"
        value = str(connection.pk)
        options.append(
            {
                "value": value,
                "label": label,
                "selected": value == selected or (not selected and connection.pk == primary_pk),
            }
        )
    return options
```

- [ ] **Step 4: Thread the selection through the page**

Replace `CreatePageView.get_context_data`'s body from `operation_key` down:

```python
        context = super().get_context_data(**kwargs)
        operation_key = kwargs.get("operation_key")
        reuse_job = _reuse_job(self.request)

        # A GET's unusable pk falls back to the role binding in silence: a
        # stale link should leave a normal page behind, never an error.
        picked, raw_connection, usable = picked_connection(self.request)
        selected = raw_connection if usable else ""
        check = services.preflight(picked)
        available = page_operations(check.resolved)

        if not operation_key and reuse_job is not None and get_operation(reuse_job.operation):
            operation_key = reuse_job.operation
        operation = resolve_page_operation(operation_key, available)

        context["operation"] = operation
        context["operations"] = available
        context["preflight"] = check
        context["connection_options"] = _connection_picker_options(selected)
        context["selected_connection"] = selected
        refs = stored_input_refs(self.request, operation)
        context["stored_inputs"] = _stored_input_context(refs, operation)
        context["form"] = build_form(
            operation,
            services.live_options(operation, check.resolved),
            initial=_reuse_initial(reuse_job),
            stored_keys=frozenset(item["param_key"] for item in context["stored_inputs"]),
        )
        context["jobs"] = _recent_jobs()
        context["input_targets"] = input_targets()
        context["queued_card"] = _queued_placeholder(self.request)
        return context
```

Give `_create_url` and `_create_page_response` the selection too:

```python
def _create_url(operation: Operation, connection: str = "") -> str:
    """The create page's URL for `operation`, carrying the picked model.

    The pick lives in the query string, not a session, so a link is a
    complete description of what the page will show -- which is what makes
    the chooser links, the redirect after a submission, and a bookmark all
    agree.
    """
    if operation.key == operations_for(IMAGE_GENERATION_CAPABILITY)[0].key:
        url = reverse("vision-create")
    else:
        url = reverse("vision-create-operation", args=[operation.key])
    return f"{url}?connection={connection}" if connection else url
```

(Note the change from `page_operations()[0]` to the REGISTRY's first
operation: the bare `/vision/` URL always serves the first REGISTERED mode,
and which modes a model supports must not change which URL is the bare one.)

In `_create_page_response`, add the same three context keys the page view
sets, reading them from the request:

```python
def _create_page_response(request, operation: Operation, check, form, status: int):
    """Re-render the create page around an invalid form."""
    refs = stored_input_refs(request, operation)
    _picked, raw_connection, usable = picked_connection(request)
    selected = raw_connection if usable else ""
    return render(
        request,
        "vision/create.html",
        {
            "operation": operation,
            "operations": page_operations(check.resolved),
            "preflight": check,
            "form": form,
            "connection_options": _connection_picker_options(selected),
            "selected_connection": selected,
            "stored_inputs": _stored_input_context(refs, operation),
            "jobs": _recent_jobs(),
            "input_targets": input_targets(),
            "queued_card": _queued_placeholder(request),
        },
        status=status,
    )
```

- [ ] **Step 5: Carry the pick through the submission**

In `generate`, replace the first three lines and the payload/redirect lines:

```python
    picked, raw_connection, usable = picked_connection(request)
    if not usable:
        # The operator explicitly chose a model that is gone. Running a
        # different one would be worse than saying no, so nothing is
        # queued and nothing is staged.
        return _picker_failure_response(request)

    check = services.preflight(picked)
    operation = resolve_page_operation(
        request.POST.get("operation"), page_operations(check.resolved)
    )
    refs = stored_input_refs(request, operation)
    form = build_form_for(request, operation, check, stored_keys=frozenset(refs))
```

```python
    payload = {"operation": operation.key, "params": payload_params, "inputs": inputs}
    if raw_connection:
        # The pk, as a string -- never a resolved model, never a name. The
        # worker re-resolves it fresh at claim time (`jobs._resolve_model`),
        # so an edited connection is honoured and a deleted one fails
        # loudly instead of silently running something else.
        payload["connection"] = raw_connection
```

```python
    return redirect(f"{_create_url(operation, raw_connection)}&queued={queue_job_id}"
                    if raw_connection
                    else f"{_create_url(operation)}?queued={queue_job_id}")
```

and add the refusal responder beside `_queue_failure_response`:

```python
def _picker_failure_response(request):
    """The 503 for a submission whose picked model no longer resolves.

    The same two shapes an unavailable engine gets, so the page has one way
    of saying "not now": the small banner fragment for XHR, a redirect back
    to a clean page for a plain POST (there is no form state worth
    preserving when the model it was written for is gone).
    """
    if _is_xhr(request):
        return render(
            request, "vision/_unavailable.html",
            {"message": _UNREGISTERED_CONNECTION_MESSAGE}, status=503,
        )
    return render(
        request, "vision/_unavailable.html",
        {"message": _UNREGISTERED_CONNECTION_MESSAGE}, status=503,
    )
```

- [ ] **Step 6: Render the picker and the model-aware chooser**

In `modules/vision/templates/vision/create.html`, replace the `op-chooser`
nav and add the picker above the form:

```html
{% if operations|length > 1 %}
<nav class="op-chooser" aria-label="Generation modes">
  {% for choice in operations %}
    {% if choice.key == operation.key %}<span class="current">{{ choice.label }}</span>
    {% else %}<a href="{% url 'vision-create-operation' choice.key %}{% if selected_connection %}?connection={{ selected_connection }}{% endif %}">{{ choice.label }}</a>{% endif %}
  {% endfor %}
</nav>
{% endif %}
```

then add the picker as its OWN form, a SIBLING of the generate form and
placed immediately above it:

```html
{% if connection_options %}
{% comment %}
The role binding is the DEFAULT model, not the only one.

This is a SEPARATE form on purpose. The generate form is
`enctype="multipart/form-data"` and carries the operator's attached image
and typed instruction; submitting THAT form as a GET (a `formmethod="get"`
button inside it) would drop the file outright and throw away everything
else, because the create page reads only `?connection=` off a GET. Switching
models must not cost the operator their work, so switching is its own GET of
its own two fields, and the generate form learns the choice from a hidden
input below.
{% endcomment %}
<form class="model-picker" method="get"
      action="{% url 'vision-create-operation' operation.key %}">
  <label for="connection">Model</label>
  <select id="connection" name="connection" data-reload-on-change>
    {% for opt in connection_options %}
    <option value="{{ opt.value }}"{% if opt.selected %} selected{% endif %}>{{ opt.label }}</option>
    {% endfor %}
  </select>
  <button type="submit" id="use-model">Use this model</button>
  <span class="field-hint">The model this generation runs on. The mode list above follows it.</span>
</form>
{% endif %}
```

and inside the generate form, immediately after `{% csrf_token %}`:

```html
{% comment %}
Which model this submission runs on, carried as a hidden field so the POST
says it explicitly rather than the view re-deriving it from a referer or a
session. Blank means "the role binding", which is what every existing link,
bookmark, and caller sends. `views.picked_connection` reads this name off
the POST and the query string alike, which is what makes the picker's GET
and this POST agree.
{% endcomment %}
<input type="hidden" name="connection" value="{{ selected_connection }}">
```

Add the style rules to `{% block vision_style %}`:

```css
  .model-picker { display: flex; align-items: end; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 1rem; }
  .model-picker select { flex: 1 1 16rem; }
  .model-picker label { display: block; font-size: 0.85rem; color: var(--muted); }
```

and, in the page's existing progressive-enhancement script block, append:

```javascript
  // Progressive enhancement ONLY. Without JS the operator changes the
  // select and presses "Use this model"; the same GET happens either way,
  // and nothing here is required for the picker to work. The button is
  // hidden only once we know we can submit for them.
  var picker = document.querySelector('[data-reload-on-change]');
  var useModel = document.getElementById('use-model');
  if (picker && useModel && picker.form) {
    useModel.hidden = true;
    picker.addEventListener('change', function () { picker.form.submit(); });
  }
```

**Do not** merge these two forms, and do not give the picker's button a
`formaction`/`formmethod` that targets the generate form: a GET submission
of a multipart form discards the attached file, and the create page's GET
handler reads only `?connection=`, so the operator's instruction would
vanish the moment they changed models.

- [ ] **Step 7: Run the tests to verify they pass**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_views_create.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_views_generate.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests -q
```
Expected: PASS.

- [ ] **Step 8: Docs and commit**

Add to `modules/vision/README.md`, under "The poll-driven page":

```markdown
### The model picker

The form carries a `Model` select of every REGISTERED image-generation
connection, in the console's own picker order, with the connection currently
bound to `vision.generate` marked `(primary)` and preselected — the same
grammar and the same console surface the Ask page's picker uses. Picking a
model changes three things at once: the mode list (only what that model has a
graph for), the form (that mode's schema and that engine's live option
lists), and the payload (`"connection": "<pk>"`).

The pick lives in the query string, never a session, so a link is a complete
description of what the page will show. The picker is its OWN small GET form,
a sibling of the generate form rather than a control inside it — the generate
form is multipart and carries the operator's attached image and typed
instruction, and submitting it as a GET to switch models would throw both
away. The generate form learns the choice from a hidden `connection` field.
Without JavaScript the operator presses "Use this model"; with it, changing
the select submits that little form for them.
```

```bash
git add modules/vision/views.py modules/vision/templates/vision/create.html \
        modules/vision/tests/test_views_create.py modules/vision/tests/test_views_generate.py \
        modules/vision/README.md
git commit -m "$(cat <<'EOF'
feat(vision): pick the model for one generation, the way Ask already does

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

### Task 11 amendment (owner rulings 2026-08-24)

Three owner rulings, recorded live on the branch's SDD ledger while the
owner used the preview build, amend Task 11 as written above. They were
prompted by two real confusions on the preview stack (see
`.superpowers/sdd/2026-08-25-vision-edit-capability/progress.md`, Task 9's "what
is the difference between image to image and edit image" entry, and Task
8/9's repeated `img2img`/`flux2` refusals). They are BINDING and take
precedence over any conflicting sentence in Task 11's Steps 1-8 above.

**(a) One "Image to image" tab; the form adapts to the selected model's
family.**

`img2img` and `edit` answer the same operator question ("change this
picture") and, by D-EDIT-5, are never BOTH offered by the same selected
model — a checkpoint family (`""`) offers `img2img` and never `edit`; an
edit family (`flux2`/`qwen_image`) offers `edit` and never `img2img`. Only
when nothing is selected yet (the full registry answers, "no opinion" per
`operations_for_model`) can both appear in the same `available` list at
once. The page collapses them to ONE entry everywhere it names a mode: the
mode chooser nav, the page's own `<h1>`, and every "Use in …" action link a
job card or gallery figure offers — all labelled `"Image to image"` —
`IMG2IMG.label`'s own string, not a new one. `EDIT.label` ("Edit an image")
is untouched and keeps answering the tool-facing catalog
(`GET /vision/operations/`, `operation_catalog`) and any other
schema-reading caller; this merge is presentation-only, inside
`modules/vision/views.py` and `create.html`, and touches no `Operation`
definition in `core/inference/operations.py`.

Mechanically: a private helper `_merge_image_to_image(operations, prefer=None)`
collapses the first `img2img`/`edit` entry it meets in registry order,
preferring `prefer`'s member of the pair when `prefer` names one and it is
present (so the nav's own "current" highlight and the merged tab's `href`
never disagree with the operation the page actually rendered). `page_tabs`
(nav) calls it with `prefer=operation.key`; `input_targets` calls it with no
preference (registry order — `img2img` before `edit` — decides when both are
genuinely present). `context["operations"]` is **unchanged**: it stays the
raw, unmerged `available` list Task 10 already produces, because
`services.operation_catalog` / the schema-endpoint tests key off it
directly. The merge lives ONLY in a new `context["operation_tabs"]` the nav
partial iterates, and inside `input_targets()`'s own return value.

`input_targets()` gains the same `resolved: ResolvedModel | None = None`
parameter `page_operations` already has, and threads it into its own
`page_operations(resolved)` call — the fourth of the "four bare call sites"
Task 10's report named (`CreatePageView`, `generate`, `input_targets`,
`_create_page_response`); the base Task 11 steps above already thread the
other three via `check.resolved`. `CreatePageView` and `_create_page_response`
now call `input_targets(check.resolved)` instead of the bare `input_targets()`;
`gallery()` and `_render_card()` (used by `job_status` / `queue_job_status`,
which have no "currently selected model" of their own) keep calling it with
no argument, which is unchanged behaviour (the merge still applies, narrowing
does not).

Inpaint/Upscale/Text-to-image tabs already narrow correctly once
`page_operations(check.resolved)` and `input_targets(check.resolved)` are
threaded through the same four sites — no separate mechanism is needed for
them; the merge above is the ONLY special-case.

**(b) A job's headline, for `edit`, is the instruction.**

One place: `GenerationJob.headline` (`modules/vision/models.py`). Today it
reads `params.get("prompt")` only, so an `edit` job — whose schema has no
`prompt` param, only `instruction` — falls through to the operation's own
schema label ("Edit an image"), which is what the owner saw and flagged.
Fixed by reading `params.get("prompt") or params.get("instruction")` before
falling back to the label; no second property, no per-operation branch, no
template change (the card and gallery caption both already read
`job.headline`, per that property's own docstring).

**(c) A model that supports nothing gets an honest banner and a disabled
form.**

This is the case Task 10's fix-round explicitly handed off (see
`views.resolve_page_operation`'s comment: "Task 11's banner is what makes
that fallback honest"). `CreatePageView` / `_create_page_response` compute
`operations_supported = bool(available)` (`available` is the same
`page_operations(check.resolved)` list Step 4 already computes) and pass it
as `context["operations_supported"]`. The template, only when
`preflight.state == "ready"` (an unbound or unreachable role already shows
its own banner and this one would be a second, contradictory message) and
`operations_supported` is `False`, shows a third banner reading the
resolved model's engine offers no operations for the family it declares —
naming `preflight.resolved.model_id` and `preflight.resolved.engine`, never
a baked model name — and wraps the generate form's fields and submit button
in `<fieldset {% if not operations_supported %}disabled{% endif %}>`
(`all: unset; display: contents;` in `vision_style` so the fieldset does not
disturb the existing CSS grid). This is a page-level honesty measure, not a
new server-side refusal: `generate()`'s existing validation and the engine's
own `engine_failed` response (already exercised live — see Task 6's ledger
entries) remain the backstop for a submission that reaches the server
anyway (a scripted POST, a stale disabled-fieldset bypass); Task 11 adds no
new 4xx/5xx path for this case.

---

### Task 12: ADR amendment, docs sweep, full verification

**Files:**
- Modify: `docs/adr/0012-image-generation-engine-adapter.md`
- Modify: `modules/vision/README.md`, `console/inference/README.md`, `docs/ROADMAP.md` (if it tracks the vision phase)
- Modify: this plan (the live verification log)

- [ ] **Step 1: Amend ADR 0012**

Append a new section after "Engine-declared setup guides":

```markdown
### Instruction-based editing across model families (2026-08-25)

D4 said the role binding IS the checkpoint and deferred a per-job override.
Both halves changed, and this records why.

**D-EDIT-1 — one `edit` operation, dispatched by family.** Two model
families both do instruction-based editing with pipelines that share almost
no wiring. They are still ONE operation: the operator's question is "change
this image like so", and which model answers it is the picker's job — the
same split D4/D5 already draw between a binding and an operation. Per-family
operations would have put model-family identity into `core/`, multiplied with
every model added, and shown two chooser entries for one intent.

The rule this forces: `edit` declares ONLY the params EVERY edit graph really
wires. A negative prompt, a sampler, a scheduler, a denoise, a width/height,
a megapixel budget, and a LoRA are each honoured by at most one of the two
graphs, so none is offered. A control that silently does nothing on half the
models it is shown for is a lie about the platform, not a convenience.

**D-EDIT-2 — the model family is an operator declaration.** Verified
read-only against a live ComfyUI 0.33.0 on 2026-08-25: `/object_info` reports
a GGUF diffusion model's FILENAME and nothing else — no architecture field,
no per-file introspection. There is no observable engine fact that says which
family a weights file belongs to, and name-pattern matching against a shipped
list of models is forbidden by the never-guess ruling. So the operator
declares it, at registration, into `ModelConnection.config["family"]` (the
JSONField D8 added for exactly this).

The VOCABULARY is still read from the engine: `/object_info/CLIPLoader`'s
`type` combo is ComfyUI's own family list — the string a CLIP loader is
literally given — narrowed to the families this adapter has a graph template
for (`comfyui_workflows.families()`). The platform ships neither list.

What IS derived from an engine fact is which LOADER reported a model
(`InstalledModel.loader`). That is what tells the console whether
registration must ask for a family and companions at all: a
`CheckpointLoaderSimple` model is self-contained, anything else is half a
pipeline. The one-click "Add to registered" button refuses the latter and
points at the manual form, because detection cannot supply three facts it
does not have.

**D-EDIT-3 — companions are picked, never defaulted.**
`config["text_encoder"]` and `config["vae"]` are chosen at registration from
`InferenceEngine.list_assets`. Auto-selecting them "from the family" was
rejected: it would mean shipping a family→filename map, i.e. baked model
names. Which loader NODE reads a companion is decided by that file's own
extension (`.gguf` → the GGUF loader), a fact of the operator's chosen file.

**D-EDIT-4 — one override mechanism, and it is the Ask picker's.** The
generation form offers a per-generation picker of registered
image-generation connections; the role binding is the DEFAULT and is marked
primary. The pick travels as `payload["connection"]`, a pk as a string,
resolved through `console.inference.bindings.resolve_connection_named` — the
identical field, type, and seam `rag.ask` already uses. **No new column
records it:** `GenerationJob` already stores `engine`, `model_id`,
`endpoint`, `model_fingerprint`, and `model_config`, which is D6's own
promise that a job documents the model that ran it.

**D-EDIT-5 — the template registry is keyed by `(family, operation)`.** The
empty family is "no family declared", i.e. a single-file checkpoint, which is
every connection that existed before families did.
`supported_operations(model_id, endpoint, family)` reads that registry, so
ADR 0012's derivation honesty is preserved: adding a graph is still the only
edit that changes what an engine reports it can do. `modules.vision.services.
operations_for_model` layers the platform's side of it — an adapter with no
`supported_operations`, or one that raises, narrows nothing, because "no
opinion" and "supports nothing" are different facts.
```

Also strike the Consequences line that says a per-job checkpoint override is
deferred, replacing it with a pointer to D-EDIT-4.

- [ ] **Step 2: Docs sweep**

- [ ] `modules/vision/README.md` — confirm the `edit` section, the family
      dispatch note, the picker section, and the service-layer `resolved=`
      note are all present and accurate.
- [ ] `console/inference/README.md` — confirm the multi-file registration
      section is present.
- [ ] `modules/vision/README.md`'s "Deferred (spec §9)" section — remove
      "per-job model override" if it is listed there.
- [ ] `docs/ROADMAP.md` — if it names the vision phase, record edit +
      picker as delivered on the branch (NOT as verified on live; the
      verification log below is the evidence).
- [ ] Grep the whole tree for accidental model names in shipped code and
      copy:
      ```
      grep -rniE "flux|qwen|mistral|sdxl|gguf" --include='*.py' --include='*.html' \
        core/ console/ modules/ | grep -v "/tests/" | grep -v comfyui_workflows/
      ```
      Expected: no hits outside `comfyui_workflows/` and tests. Any hit in
      `core/inference/operations.py`, a template, or a form label is a
      constraint violation — fix it.

- [ ] **Step 3: Full suite, BOTH orders**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest -q -p no:randomly
```
Expected: both at or above the Task 0 baseline, with the new tests added.
Record both numbers in the log below.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0012-image-generation-engine-adapter.md modules/vision/README.md \
        console/inference/README.md docs/ROADMAP.md \
        docs/superpowers/plans/2026-08-25-vision-edit-capability.md
git commit -m "$(cat <<'EOF'
docs(vision): record the edit-across-families and per-generation-picker decisions

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

## Live verification log

Filled in by Tasks 6 and 7. Evidence only — no success language.

| When | Family | Preconditions met | Queue outcome | `ram_free` before / after | Notes |
| --- | --- | --- | --- | --- | --- |
| | | | | | |

## Baseline

| When | Random order | `-p no:randomly` |
| --- | --- | --- |
| Task 0 (measure) | 2149 passed / 1 skipped | 2149 passed / 1 skipped |
| Task 12 (final) | 2439 passed / 1 skipped | 2439 passed / 1 skipped |

## Open questions for the owner

1. **LoRAs on an edit.** Both bundled workflows wire an optional
   distillation LoRA through `LoraLoaderModelOnly`, which
   `_fragments._lora_chain` cannot serve (it chains `LoraLoader`, which needs
   CLIP). Out of scope here. Worth a follow-up, or not wanted?
2. **A third reference image.** One family's encoder takes `image3`; the
   other chains reference latents without limit. `edit` declares two. Enough?
3. **The generation wait timeout is a hardcoded 600s.** `modules/vision/jobs.
   py`'s `GENERATE_WAIT_TIMEOUT_SECONDS` fires honestly (`timed_out: True`,
   generation keeps running) on an ordinary-build cold load on the reference hardware
   (Task 6 LIVE RUN #3). Worth an operator-facing setting (per-connection, or
   a `JobSettings` field alongside the memory budget), or is the honest
   timeout with continued background progress good enough?
4. **EDIT's default `steps` (20) is ~35 minutes of sampling on this
   hardware** (~100-107 s/step observed, Task 8's ledger entry). Lower the
   operation's default (8-12, matching the bundled workflows' own
   distillation-LoRA-tuned step counts), surface an expected-time estimate
   next to the field, or leave it as an informed operator choice?
5. ~~`_connection_edit.html` cannot declare a family/companions.~~ **Resolved
   (final-review B1):** the Edit form now renders the same
   `inference/_family_fields.html` partial the Add form does, preselected
   from `connection.config`; an already-registered multi-file connection
   declares or changes its family/companions there, no remove-and-re-add
   needed.
6. **The ordinary build's memory headroom is tight.** System free memory bottomed at ~4.6
   GB during a governed edit run with nothing else loaded (Task 6 LIVE RUN
   #3). No margin exists today for a second concurrent model or a busier
   host. Is this an accepted hardware ceiling, or does it want a guard (e.g.
   a hard-floor check before admission)? **Correction (final-review finding
   5, 2026-08-25):** this question's original wording claimed the queue's
   exclusive-by-default posture was intact for this job kind -- it is NOT,
   across a timed-out wait: `run_generate`'s `timed_out: true` result
   succeeds the QUEUE job (releasing `plan_generate`'s `exclusive=True`
   hold) while the generation is still running and the checkpoint still
   resident (see ADR 0012's "Two status vocabularies" section and
   modules/vision/README.md's "Known limits"). Any answer to this question
   should account for that gap, not assume the posture holds throughout a
   generation's real lifetime.

## Review history

**r1 — adversarial plan-hygiene review: AMEND, 8 findings. All applied.**

| ID | Finding | Applied as |
| --- | --- | --- |
| F1 | R1 violation: `batch_size` on `edit`. The bundled edit-only-family subgraph has NO `RepeatLatentBatch` — its complete node list is `CFGNorm`, `CLIPLoader`, `ComfySwitchNode`, `FluxKontextImageScale`, `FluxKontextMultiReferenceLatentMethod`, `KSampler`, `LoraLoaderModelOnly`, `ModelSamplingAuraFlow`, `TextEncodeQwenImageEditPlus`, `UNETLoader`, `VAEDecode`, `VAEEncode`, `VAELoader`. | `batch_size` dropped from `EDIT` and every test payload; `EmptyFlux2LatentImage.batch_size` pinned at `1`; the edit-only-family batch test replaced with one asserting NO batching node; that subgraph's full node list pinned into the template spec; the D-EDIT-1 row moved to the excluded half with "R1: one output per edit"; Open Question 2 deleted. |
| F2 | Stale facts: the encoder swap is done and the seams plan is committed. | Installed-files block replaced with the real listing (Q4_K_M GGUF instruct encoder, 14,333,922,848 B; fp8 deleted); divergence paragraph and Open Question 1 deleted; the seams dependency now cites `27b8993` / `a12150a`; Task 0 and the Task 6 precondition re-confirm the listing rather than wait for a report. |
| F3 | Main's T9.5 adds optional `JobKind.on_terminal`. | Third Task 0 merge bullet: `vision.generate` leaves it `None`; `core/inference/jobkinds.py` stays untouched; a non-optional field is a STOP-and-report. |
| F4 | Task 1 Step 5 overlaps the seams plan's Task 2, which also rewrites `list_installed` (run-memo residency, one believed-resident checkpoint per endpoint). | Task 1 Interfaces gained the overlap contract; Step 5 reframed as "extend the then-current function", not a verbatim rewrite; all three new tests now assert `loaded is False` for non-resident rows; the dedupe is required not to shadow the memo's single-resident belief. |
| F5 | Task 1 Step 1 quoted a `FakeComfyUI.get` that does not exist at HEAD. | Reworded to "re-read the current `FakeComfyUI.get`, then make whatever the `object_info` fallthrough then is return `200 {}`"; notes that there is no `missing_nodes` guard at HEAD, that `ram_free` already exists, and that the seams work adds `vram_free` and `/free`. |
| F6 | `ASSET_KINDS` is the vocabulary an operation `Param` is validated against; a text encoder is never an operation param. | The `ASSET_KINDS` edit and its test removed; only `_ASSET_NODES["text_encoder"]` remains, with a paragraph explaining why `list_assets` needs no vocabulary change. |
| F7 | UX bug: a `formmethod="get"` picker button inside the multipart generate form drops the attached file, and the GET handler reads only `?connection=`, so a typed instruction vanishes on model change. | The picker is now its own sibling `<form method="get">` holding only the select and button; the generate form carries a hidden `connection` input; auto-submit stays optional; a new test pins the separation. |
| F8 | Prose named `image_connections_for_picker()` (Task 8 defines `connections_for_picker(capability)`), and the Architecture narrative's task order contradicted the dependency table. | Both renamed; the narrative rewritten to T1 loader discovery → T2 registry → T3 families → T4 registration form → T5 `EDIT` → T6–7 templates → T8–11 picker → T12 docs. |

**r2 — scoped re-check: CLEAN, all 8 verified.** Two optional nits adopted as
a final adjudicated pass (no further review round):

- **N1** — memory-seams Task 2 has landed (`6f435f9`), so Task 1 Step 5 now
  quotes the REAL post-seams `list_installed` body (`memo = _run_memo(endpoint)`
  read once, then per row `loaded=memo is not None and memo.model_key ==
  norm_tag(name)` and `loaded_size=memo.footprint if <that> else None`) and
  shows the widened loop as a merge onto it. The `_CHECKPOINT_NODE` citation
  is corrected from "~57" to **line 87**, transcribed from the file, and
  `list_installed`'s own location given as line 440. The Global Constraints
  bullet and the Task 1 overlap note now say "landed" rather than "in flight".
- **N2** — added `test_an_edit_keeps_its_image_instruction_and_model_in_one_submission`
  to Task 11: a real `edit` POST with a `SimpleUploadedFile`, an instruction,
  and `connection=<pk>`, asserting all three survive into the enqueued
  payload. That is the exact F7 regression, and it is invisible to the
  template assertion that sits beside it.

**Status: FINAL.**

Also corrected while amending (not a review finding, but a type-consistency
error the self-review missed): the File Structure table listed the
family-specific graph helpers as `_fragments` members and named
`preflight_for` / `operations_for_resolved` / `_connection_picker_options()`
where the tasks define `_health_check` / `operations_for_model` /
`picked_connection` and put each family's wiring in its own template module.

