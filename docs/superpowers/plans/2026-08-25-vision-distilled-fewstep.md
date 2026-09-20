# Vision EDIT — distilled variant and speed adapters (follow-up plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** make the two newly acquired weights runnable through the SAME `edit`
operation the platform already ships — a guidance-distilled member of the
`flux2` family (a different graph, not a different family), and a
distillation LoRA for the `qwen_image` family (an ordinary adapter, not a
speed switch) — so an edit that costs ~35 minutes today can cost minutes,
with nothing about it guessed and no model name anywhere in code or copy.

**Architecture:** three strictly sequential tasks plus one independent task,
continuing
`docs/superpowers/plans/2026-08-25-vision-edit-capability.md` (Tasks 1–12) on the
`vision-generation` branch, and its design-decision series continues here as
**D-EDIT-6 … D-EDIT-10**. Task 13 adds ONE new operator declaration —
`ModelConnection.config["variant"]` — the `flux2` edit template branches on,
plus the one seam that lets an engine report *per-model param defaults* the
way it already reports per-model *option lists*, plus the registration and
edit forms that collect the declaration (and the config-wiping bug the edit
form has today). Task 14 gives `edit` the LoRA params it deliberately did not
have, served by a model-only LoRA chain BOTH edit families' bundled workflows
already contain. Task 15 verifies each model live through the governed queue,
one at a time, records the measured footprints, and closes the docs and the
ledger. **Task 16 is independent of all three** (owner requirement,
2026-08-25): it makes every image generation's own timing visible — how long
it waited, how long it ran — on the card, in the gallery, and in the JSON an
agent reads. It shares no function with Tasks 13–15 and may run first or in
parallel.

**Tech Stack:** unchanged — Python 3.12, Django 5, `httpx` against ComfyUI
0.33.0's HTTP API, the ComfyUI-GGUF custom-node pack, pytest + pytest-django,
PostgreSQL on the branch preview port 5435. No new dependencies, no build
step, no JS framework. **Zero migrations** (`ModelConnection.config` is a
JSONField; one more key needs no schema change).

**Spec:** the owner's acquisition approval recorded in
`.superpowers/sdd/2026-08-25-vision-edit-capability/progress.md` (Task 10's
"OWNER APPROVED (2026-08-24)" and the "ACQUIRED (2026-08-25)" entry), and the
two live product notes that motivate it: EDIT's 20 default steps are ~35
minutes of sampling on this hardware (Task 8 ledger), and the 600 s wait
timeout is short for a first load (Task 6 LIVE RUN #3).

---

## Global Constraints

Every constraint of `docs/superpowers/plans/2026-08-25-vision-edit-capability.md`
carries over verbatim. The ones that bite hardest here, plus this plan's own:

- **Depends on Task 12 of the parent plan having LANDED.** That task's docs
  sweep and full both-order suite are the baseline this plan measures
  against. If it has not landed, STOP and report.
- **Governed path only. Never a raw ComfyUI workload.** No `POST /prompt`
  from a shell, a script, or an agent. Read-only `GET /object_info/<Node>`,
  `GET /system_stats`, `GET /queue` are the only direct calls permitted, and
  they load nothing. The machine OOM-crashed on 2026-08-24 doing otherwise.
- **One model resident at a time.** Task 15 verifies the distilled model
  first, unloads, then the LoRA'd one. Never both.
- **No baked model names in code or UI copy.** Not in `core/`, not in
  `console/`, not in a template, not in a form label, not in a help string.
  Fixtures, tests, and the OPERATOR steps of Task 15 may name files. The one
  new vocabulary word this plan introduces — `"distilled"` — is a GRAPH
  vocabulary word (it names which of a family's two bundled edit graphs to
  build) and lives only in
  `core/inference/engines/comfyui_workflows/`, exactly where the family keys
  do (ADR 0012 D1, D-EDIT-2).
- **Never guess.** A fact is either read from the engine or declared by the
  operator. Which quantized file is a distilled build is NOT observable over
  ComfyUI's HTTP API (verified again 2026-08-25: `UnetLoaderGGUF` reports a
  filename list and nothing else), and inferring it from a filename is
  forbidden.
- **`core/` imports nothing from `console/` or `modules/`.**
- **NO changes to `console/jobs/*` or `core/inference/jobkinds.py`.**
- **Never-500. Offline-first. Tests and docs ship with every task.**
- **Test invocation (ONE at a time, foreground):**
  `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest <path> -q`
- **No `conftest.py`.** Class-level `@pytest.mark.django_db` only where a test
  really touches the DB. HTTP-layer mocking only; `FakeComfyUI` is EXTENDED,
  never replaced.
- **Migrations: none expected.** If a step appears to need one, STOP and
  report.
- **Baseline:** re-measure both orders at Task 12's closing commit before
  Task 13 and write the two numbers here. Last recorded on this branch:
  `2252 passed / 1 skipped` at `d270d48` (Task 11 fix round).
- **Commit trailers.** Every commit ends with:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  ```
- **Verification doctrine.** No "done"/"works"/"fixed" language about either
  model until fresh pixels have been seen. Task 15's runs are
  evidence-gathering, not completion claims.
- **Out of scope, deliberately:** the distilled encoder's unused top layers.
  The distilled build reportedly reads only the lower layers of its text
  encoder, so a loader could skip the rest and save memory. That is an
  optimization inside a custom-node pack we do not own, it needs measurement
  this plan does not budget, and nothing here depends on it. Recorded as a
  backlog item in the ledger, not implemented.

---

## Verified facts (read-only, 2026-08-25)

### Files on disk, from the engine's own combos

```
GET /object_info/UnetLoaderGGUF   unet_name -> [["flux-2-klein-9b-Q8_0.gguf",
                                                "flux2-dev-Q4_K_S.gguf",
                                                "qwen-image-edit-2511-Q5_K_M.gguf"]]
GET /object_info/CLIPLoaderGGUF   clip_name -> [["Mistral-Small-3.2-24B-Instruct-2506-Q4_K_M.gguf",
                                                 "Qwen3-8B-Q8_0.gguf",
                                                 "qwen_2.5_vl_7b_fp8_scaled.safetensors"]]
                                  type      -> [[... "qwen_image" ... "flux2" ...]]
GET /object_info/VAELoader        vae_name  -> [["flux2-vae.safetensors",
                                                 "qwen_image_vae.safetensors", "pixel_space"]]
GET /object_info/LoraLoaderModelOnly
      required: model [MODEL], lora_name [[<the two Lightning files>]],
                strength_model [FLOAT default 1.0 min -100 max 100 step 0.01]
      output:   ["MODEL"]                      (no CLIP output, no strength_clip)
GET /object_info/CFGGuider
      required order: model, positive, negative, cfg [FLOAT default 8.0]
      output: ["GUIDER"]
GET /object_info/ConditioningZeroOut
      required: conditioning [CONDITIONING]      output: ["CONDITIONING"]
```

The distilled 9B file is reported by the SAME loader, and its encoder by the
same `CLIPLoaderGGUF` with the same `type="flux2"` value, as the existing
`flux2` connection. There is no observable difference. (ComfyUI's own
`comfy/supported_models.py::Flux2` matches `image_model == "flux2"` for both
and dispatches its text encoder by state-dict prefix, which is a fact of the
weights file and not of anything HTTP serves.)

### The bundled distilled edit graph, decoded from its own link list

Source: `<home>/ComfyUI/.venv/lib/python3.12/site-packages/comfyui_workflow_templates_json/templates/image_flux2_klein_image_edit_9b_distilled.json`,
subgraphs `"Image Edit (Flux.2 Klein 9B Distilled)"` (one-image and two-image).
Node ids below are the bundle's own.

One-image subgraph:

```
UNETLoader(70) ──────────────────────────────► CFGGuider(63).model
CLIPLoader(71) ──► CLIPTextEncode(74) ─┬─────► ReferenceLatent(125) ──► CFGGuider(63).positive
                                       └─► ConditioningZeroOut(82) ──► ReferenceLatent(123) ──► CFGGuider(63).negative
image ──► ImageScaleToTotalPixels(80) ─┬─► VAEEncode(124) ──► ReferenceLatent(125).latent
                                       │                  └─► ReferenceLatent(123).latent   (the SAME latent)
                                       └─► GetImageSize(99) ─┬─► Flux2Scheduler(62).width/height
                                                             └─► EmptyFlux2LatentImage(66).width/height
CFGGuider(63) widgets = [1]            -> cfg = 1
Flux2Scheduler(62) widgets = [4, …]    -> steps = 4
RandomNoise(73) + KSamplerSelect(61 "euler") + Flux2Scheduler(62) + EmptyFlux2LatentImage(66)
      ──► SamplerCustomAdvanced(64) ──► VAEDecode(65) ──► out
```

Two-image subgraph, same shape, with BOTH branches chained twice — the
EDITED image first, the second reference after it:

```
positive: CLIPTextEncode(109) ──► ReferenceLatent(128, latent=VAEEncode(127) = edited)
                             ──► ReferenceLatent(131, latent=VAEEncode(130) = second) ──► CFGGuider.positive
negative: ConditioningZeroOut(86) ──► ReferenceLatent(126, latent=127)
                                 ──► ReferenceLatent(129, latent=130) ──► CFGGuider.negative
```

Three differences from `flux2_edit.py` as it stands, and NOTHING else:

1. **No `FluxGuidance` node at all** (the weights are guidance-distilled).
2. **`CFGGuider(model, positive, negative, cfg)` instead of
   `BasicGuider(model, conditioning)`**, with the negative being
   `ConditioningZeroOut` of the encoded instruction — zeroed BEFORE the
   reference chain, then chained through the same latents.
3. **Its own default step count and cfg** (4 and 1), which are widget
   values, not graph structure.

**Deliberately NOT a difference:** `ImageScaleToTotalPixels.upscale_method`.
The acquisition report read `nearest-exact` off the one-image subgraph; the
two-image subgraph in the same file uses `lanczos`, and the undistilled
family's bundled workflow uses `area`. A value the bundle itself does not
hold consistent across two graphs for the same weights is a cosmetic
authoring choice, not a fact about the model — `_fragments.scale_to_megapixels`
keeps its single fixed `"area"`, and this plan adds no knob for it (D-EDIT-3's
"fixed graph facts" rule).

### The model-only LoRA node, in both bundled edit workflows

```
image_flux2.json                 "Image Edit (Flux.2 Dev)":
      UNETLoader ──► LoraLoaderModelOnly ──► (ComfySwitchNode toggle) ──► guider
      node 89 widgets = ["Flux_2-Turbo-LoRA_comfyui.safetensors", 1]

image_qwen_image_edit_2511.json  "Image Edit (Qwen-Image 2511)":
      ModelSamplingAuraFlow ──► CFGNorm ──► LoraLoaderModelOnly ──► (toggle) ──► KSampler.model
      node 153 widgets = ["Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors", 1]
```

Both edit families' shipped workflows apply a model-only LoRA as the LAST
model-wrapping step before the guider/sampler. That is what makes a LoRA
param honest for `edit` under D-EDIT-1's intersection rule — and it is
exactly the `ComfySwitchNode` cluster the parent plan dropped, restored as a
real param instead of a toggle.

---

## Design decisions, stated explicitly

### D-EDIT-6 — A distilled build is a `variant` on the connection, not a second family

**Decision:** the operator declares `ModelConnection.config["variant"]`
alongside `family`/`text_encoder`/`vae`. The template registry stays keyed by
`(family, operation)`; `flux2_edit.build` reads `config.get("variant")` and
builds the distilled wiring for the one value the module implements
(`"distilled"`). An absent, blank, or unrecognized variant builds the
undistilled graph — the behaviour every existing connection already has.

**Why not a second family key (`flux2_klein`)?** It would be unofferable.
`ComfyUIEngine.list_families` is the intersection of the ENGINE's own
`CLIPLoader.type` combo with `comfyui_workflows.families()` (D-EDIT-2). A
made-up family string is not in `CLIPLoader.type`, so the registration form
could never offer it, and if it somehow were stored it would be handed to a
`CLIPLoader`'s `type` input, which ComfyUI would reject. The distilled build
genuinely IS `flux2` to the engine — same loaders, same `type`, same latent
format, same VAE. Additionally, Tasks 10/11 narrow the operation chooser on
`family`; a parallel family would double every narrowing key for a
distinction the chooser does not care about (both offer `edit`, and only
`edit`).

**Why an operator declaration at all?** Same reason as the family
(D-EDIT-2): verified again today, ComfyUI exposes no GGUF metadata over
HTTP — no architecture, no distillation flag, no per-file introspection. The
only alternative is matching a filename, which is forbidden and would
silently mis-dispatch a renamed file.

**Where the vocabulary comes from:** the template package, not the engine.
Families are the engine's own word and are read live; a variant names which
of a family's graphs a module has code for, so
`comfyui_workflows.variants()` IS the whole vocabulary, and the adapter
republishes it as `ComfyUIEngine.list_variants()`. The registration
form offers only that list, so a wrong value takes tampering.

**Not validated on save**, exactly like `family` (parent plan Task 4): a
declaration the adapter has no graph for is stored honestly and simply
produces the undistilled graph and no variant defaults. Validating here would
recreate the circular dependency Task 4 deliberately broke.

### D-EDIT-7 — `guidance` stays declared and stays wired; only its DEFAULT is per-model

**Decision:** `edit` keeps `guidance` and `steps` for every family and
variant. The distilled graph wires `guidance` to `CFGGuider.cfg` — the same
"one operator control, two honest wirings" rule that already sends it to
`FluxGuidance.guidance` on the undistilled graph and `KSampler.cfg` on the
other family. What differs for a distilled model is what the form should
START at (4 steps, cfg 1), and that is delivered by ONE new seam:
`services.live_defaults(operation, resolved)`, the exact twin of the existing
`services.live_options(operation, resolved)`.

**Why not hide the param for distilled models?** Because hiding it would be
LESS honest, not more. `submit_job` re-fetches the operation from the
registry and runs `validate_params` against it (services.py), so the schema
floor would still fill `guidance` with its schema default and record it on
the job row — a value shown to nobody, honoured by nothing, and written into
the reproducibility record. Hiding also needs a second mechanism (per-model
param REMOVAL) that the catalog, the form, and the schema floor would each
have to agree about. Wiring the param and moving its default is one
mechanism.

**Why an engine-reported seam rather than a fact in `core/inference/operations.py`?**
`Operation`/`Param` are pure schema, shipped model-free; a "4 steps" default
is a fact about a graph, which is engine vocabulary. The precedent is already
in the codebase and is followed exactly: `live_options` asks the bound engine
for the option lists the schema cannot know (`list_choices`, `list_assets`),
merges them into `describe()` output for the catalog, and passes them to
`build_form`. `live_defaults` asks the bound engine for the defaults the
schema cannot know, merges them into the catalog's `param["default"]`, and
passes them through `build_form`'s EXISTING `initial=` argument — which means
`modules/vision/forms.py` needs no change at all. Reported keys the operation
does not declare are dropped (an engine cannot invent a param).

`validate_params` is untouched: it stays the schema floor every caller
shares, exactly as it is for engine-owned `"choice"` options today.

### D-EDIT-8 — The registration surfaces that must learn the new word

**Already landed, NOT this plan's work:** the connection Edit form's
family/companion fields and the absent-vs-blank config rule
(`_declared_config`: a field POSTED BLANK clears, a field ABSENT from the
form keeps what was stored) landed on the base branch in the parent plan's
final-review fix round, which fixed the config-wiping bug this plan was
originally going to fix. **Do not re-implement any of it.**

**What T13 adds, and only this:**

- `"variant"` joins `_declared_config`'s posted-key tuple, so the new word
  round-trips through the same rule as the other three (blank clears, absent
  keeps);
- one `<select name="variant">` on each form that already carries the family
  fieldset — the manual registration form in `console.html` (both copies of
  it) and `_connection_edit.html`, the latter prefilled from
  `connection.config.variant`;
- `context["variant_options"]` in `_build_context`, from the engine.

**Read the landed code before editing it.** The fix round wrote
`_declared_config`'s exact name, signature, and posted-key tuple after this
plan was drafted; T13 Step 9 quotes the intended shape, not a transcription.
If what landed differs, follow what landed.

### D-EDIT-9 — A speed adapter is an ordinary LoRA, picked by the operator

**Decision:** `edit` splices in the existing
`core.inference.operations.LORA_PARAMS` (`loras` + `lora_strength`), and BOTH
edit templates apply the selection through one new shared fragment,
`_fragments.model_lora_chain`, built on `LoraLoaderModelOnly`. There is no
"speed" toggle, no speed-adapter-shaped switch, and no automatic step/cfg
change when a particular adapter is chosen.

**Why this reverses D-EDIT-1's `loras` exclusion — honestly.** That row
excluded LoRAs on the ground that "both families' graphs use a model-only
loader that `_fragments._lora_chain` cannot serve". The premise was right and
the conclusion was scoped to the fragment that existed. Verified above:
BOTH bundled edit workflows contain a `LoraLoaderModelOnly` in the same
position, so a model-only chain is exactly as honest for `edit` as
`_lora_chain` is for the checkpoint modes. The exclusion becomes a
**served** row: one fragment, two call sites, the same two params every other
operation already offers. D-EDIT-1's intersection rule is satisfied, not
bent.

**Why no automatic steps/cfg:** the platform would have to recognize which
adapter is a distillation adapter — from its filename. That is the forbidden
guess, in the one place it would be most tempting. The operator picks the
adapter and sets the two numbers, and the copy says so: the `loras`
description gains one family-neutral sentence ("some adapters are
distillation adapters — they replace the guidance setting: set Steps to the
count the adapter names and Guidance to 1"), naming no file and no vendor.

**Position in each graph follows each bundled workflow:** last model-wrapping
step before the guider/sampler — after `components()` for the `flux2` family
(which wraps nothing else), after `ModelSamplingAuraFlow`→`CFGNorm` for the
other. The fragment therefore takes and returns a MODEL link, not a
`Checkpoint`.

**One strength for the whole selection**, unchanged from the checkpoint modes,
and `LoraLoaderModelOnly` has only `strength_model`, so nothing is silently
dropped.

### D-EDIT-10 — Live verification: one model, one queue, measured

Each model is verified by ONE governed generation through
`/vision/` → `core.inference.queue.enqueue` → the worker, with ComfyUI
otherwise unloaded, the distilled model first (expected ~19.5 GB resident)
and the LoRA'd one second. `/system_stats` `ram_free` is recorded before,
during, and at rest; `ComfyUIEngine.loaded_footprint`'s own number is
recorded from the job. Between the two, the model is unloaded through the
seam (`ComfyUIEngine.unload`), never by restarting anything by hand. Measured
footprints go into the ledger, because the next planning decision (what else
can be resident) depends on real numbers and not on estimates.

### D-EDIT-11 — Durations are DERIVED from clocks that already exist (owner requirement, 2026-08-25)

**The requirement:** "track when an item starts being processed and when it
finishes so request durations are visible — present for ALL image generation
runs."

**What the tree already has, verified 2026-08-25 (this is the whole reason
Task 16 is small):**

- `GenerationJob.created_at` (`auto_now_add`), `started_at`, and
  `finished_at` are **already columns** in `modules/vision/models.py`
  (`created_at` at line 124 and the two nullable stamps at 125-126 as this
  is written), shipped in `0001_initial`. Match on the FIELD NAMES, not the
  numbers: this file moves under every landing fix round.
- `services.refresh_job` **already sets `started_at` at the right moment**:
  the first poll on which the engine reports `state == "running"`
  (`services.py:643-646`), and `ComfyUIGenerator.status` reports `running`
  from ComfyUI's own `/queue` running list — i.e. when the prompt has LEFT
  the engine queue and execution has begun, which is exactly the moment the
  requirement names. It is written once (`job.started_at or timezone.now()`).
- `finished_at` is set on the done path (`services.py:675`) and on every
  failure (`_fail`, `services.py:788`).

**Decision: add no column and no migration.** Task 16 adds DERIVED properties
and the four surfaces that show them. A duration is arithmetic on two
timestamps; storing it would be a third copy that can disagree with them.

**Decision: `GenerationJob` keeps its OWN clocks; it does not derive from
`InferenceJob`.** The queue's row (`console/jobs/models.py:91-96`) has
`created_at`/`started_at`/`heartbeat_at`/`finished_at` too, and they are
**different events**: `InferenceJob.started_at` is when a WORKER CLAIMED the
queue item (before the generation was even submitted to ComfyUI), while
`GenerationJob.started_at` is when the ENGINE began executing this graph.
Neither is a copy of the other and neither can replace the other. Two more
reasons this is not a choice: `modules/` may not import `console.jobs`
(core-purity rule, and `queue_job_id` is deliberately a bare integer, not a
FK), and a generation submitted directly by a tool has no `InferenceJob` at
all. This plan therefore duplicates no clock — it reads the clocks vision
already owns, and touches nothing in `console/jobs/`.

**Three derived values**, all in seconds, all `None` when they are genuinely
unknown:

| Value | From | To | While running | Never started |
| --- | --- | --- | --- | --- |
| `queued` | `created_at` | `started_at` | `now` (still waiting) | `finished_at` (a job that failed at submit waited and never ran) |
| `processing` | `started_at` | `finished_at` | `now` | `None` — not zero: "it never ran" and "it ran instantly" are different facts |
| `total` | `created_at` | `finished_at` | `now` | `finished_at` |

**One honest caveat, stated in the docstring rather than papered over:** a
generation that completes BETWEEN two polls is never observed running, and
the done path back-fills `started_at` from `created_at`
(`services.py:674`, pre-existing behaviour this plan does not change) — so
such a job reports `queued = 0`. At minutes-long image generations against a
seconds-long poll interval that is a corner, not the common case, and the
alternative (leaving `started_at` NULL) would lose the fact that it ran at
all.

**Decision: NO `sampling_started_at`, and here is the evidence.** A
load-vs-sampling split would need a signal that separates "execution began"
from "the first sampler step ran". The adapter's polled surface cannot give
it: `ComfyUIGenerator.status` reads `/history/<id>` and `/queue` only, and
`JobStatus.progress` — the protocol field that exists for exactly this — is
**never set by the ComfyUI adapter**, because neither endpoint reports a step
count. (`modules/vision/jobs.py`'s progress ticks are wall-clock elapsed
seconds with `total=None`, and that docstring already says why: "nothing in
ComfyUI's HTTP surface reports how much of a generation is done".) The only
per-step signal ComfyUI emits is on its `/ws` websocket, which would mean a
persistent connection held open inside the worker — a new transport, a new
failure mode, and a new reconnection story, for one number. Recorded as a
backlog item; not built. What the operator gets instead is honest and
already useful: "waited 0:42, ran 32:10", with the load time inside the
second number and the README saying so.

**Surfaces (all four, because "present for ALL runs" is the requirement):**
the job card in every state (it re-renders on the existing poll, so it
updates live and needs no new endpoint), the gallery card, `job_json` (which
`?format=json` and the chatbot tool both read), and `run_generate`'s result
dict (which is what an agent driving the queue sees beside `output_urls` and
`timed_out`).

---

## File Structure

| File | Responsibility after this plan |
| --- | --- |
| `core/inference/engines/comfyui_workflows/__init__.py` | `_VARIANTS: dict[tuple[str, str], dict[str, dict]]`; `variants()`; `variant_defaults(operation_key, family="", variant="")`. Registry key shape unchanged. |
| `core/inference/engines/comfyui_workflows/flux2_edit.py` | `DISTILLED`, `DISTILLED_DEFAULTS`; `build` branches on `config["variant"]`; new `_zeroed()`, `_cfg_guider()`, `_basic_guider()`; `_sample()` takes a GUIDER link. Applies the model-only LoRA chain. |
| `core/inference/engines/comfyui_workflows/qwen_edit.py` | Applies the model-only LoRA chain after its own model chain. |
| `core/inference/engines/comfyui_workflows/_fragments.py` | `model_lora_chain(graph, model, params) -> Link`. |
| `core/inference/operations.py` | `EDIT` splices `*LORA_PARAMS`; `LORA_PARAMS`' `loras` description gains the distillation-adapter sentence. |
| `core/inference/engines/comfyui.py` | `list_variants()`; `param_defaults(operation_key, config)`. |
| `core/inference/engines/base.py` | Both documented on the `InferenceEngine` protocol as OPTIONAL members, beside `list_families`/`supported_operations`. |
| `modules/vision/services.py` | `live_defaults(operation, resolved)`; `operation_catalog` merges it into `param["default"]`. |
| `modules/vision/views.py` | `CreatePageView` passes `initial={**live_defaults, **reuse_initial}` to `build_form`. |
| `console/inference/views.py` | `_engine_options(..., "variants")`; `"variant"` added to the ALREADY-LANDED `_declared_config`'s posted-key tuple (its absent-vs-blank rule is not this plan's work — D-EDIT-8). |
| `console/inference/templates/inference/console.html` | Variant `<select>` in both copies of the manual registration form's family fieldset. |
| `console/inference/templates/inference/_connection_edit.html` | One Variant `<select>` added to the family fieldset the fix round already put there. |
| `modules/vision/tests/_helpers.py` | `FakeComfyUI` unchanged unless a test needs the two speed-adapter files in `loras` (it already has that field). |
| `modules/vision/models.py` | **(T16)** `GenerationJob.durations` / `.durations_display` — derived, no new column. |
| `modules/vision/jobs.py` | **(T16)** the queue result dict carries `durations`. |
| `modules/vision/templates/vision/_job_card.html`, `gallery.html` | **(T16)** the timing line, in every state. |
| `docs/adr/0012-image-generation-engine-adapter.md` | Amendment: D-EDIT-6…D-EDIT-11, the D-EDIT-1 `loras` row moved from excluded to served with its reason, and the result-dict contract at line ~200 gaining `durations`. |
| `modules/vision/README.md` | The variant, the per-model defaults seam, LoRAs on `edit`. |
| `console/inference/README.md` | "Registering a multi-file model family" gains the variant and the edit-form round-trip. |

---

## Dependency Table (13→14→15 strictly sequential; 16 independent)

| Task | Depends on | Why this order |
| --- | --- | --- |
| 13 | Parent plan Task 12 landed; baseline re-measured | The variant declaration, the graph branch, the defaults seam, and the two forms are one coherent unit: the graph is unreachable without the declaration, and the declaration is unusable without a form. |
| 14 | 13 | The LoRA chain touches `flux2_edit.build`, which T13 restructures. Doing it second means one rewrite of that function, not two. `EDIT`'s param tuple also changes, and T13's tests pin that tuple. |
| 15 | 14 | Live verification exercises BOTH features on the same two models, one at a time; docs and the ledger close on measured numbers rather than estimates. |
| 16 | parent plan Task 12 only | **Independent.** Durations are a property of every generation, not of these two models — nothing in it reads a family, a variant, or a LoRA, and nothing in 13–15 reads a timestamp. Run it first, last, or beside them. Overlapping FILES but no overlapping function: `modules/vision/services.py` (`job_json`, not `live_defaults`), `modules/vision/README.md`, and ADR 0012 — expect trivial text merges if it runs in parallel, and nothing worse. If Task 15's live runs happen after it, they get their measured durations for free from the card. |

---

## Task 13: A distilled variant of an existing family

**Files:**
- Modify: `core/inference/engines/comfyui_workflows/__init__.py`
- Modify: `core/inference/engines/comfyui_workflows/flux2_edit.py`
- Modify: `core/inference/engines/comfyui.py`, `core/inference/engines/base.py`
- Modify: `modules/vision/services.py`, `modules/vision/views.py`
- Modify: `console/inference/views.py`, `console/inference/templates/inference/console.html`, `console/inference/templates/inference/_connection_edit.html`
- Test: `modules/vision/tests/test_comfyui_workflows.py`, `test_comfyui_engine.py`, `test_services.py`, `test_views_create.py`; `console/inference/tests/test_views.py`
- Docs: `modules/vision/README.md`, `console/inference/README.md`

**Interfaces:**
- Consumes: `_TEMPLATES[("flux2", "edit")]`, `ModelConnection.config` (D8), `services.live_options`'s tolerance idioms, `build_form(initial=...)`.
- Produces: `comfyui_workflows.variants() -> tuple[str, ...]`;
  `comfyui_workflows.variant_defaults(operation_key, family="", variant="") -> dict`;
  `ComfyUIEngine.list_variants() -> tuple[str, ...]`;
  `ComfyUIEngine.param_defaults(operation_key, config) -> dict`;
  `services.live_defaults(operation, resolved) -> dict`;
  `context["variant_options"]` on the console page. (`_declared_config` is
  CONSUMED, not produced: it landed with the parent plan's fix round and this
  task only widens its posted-key tuple by one.)

- [ ] **Step 1: Re-confirm the bundled graph and the node signatures (read-only, loads nothing)**

```bash
ls <home>/ComfyUI/.venv/lib/python3.12/site-packages/comfyui_workflow_templates_json/templates/ | grep -i klein
curl -s http://localhost:8188/object_info/CFGGuider
curl -s http://localhost:8188/object_info/ConditioningZeroOut
```
Expected: `CFGGuider` required input ORDER `model, positive, negative, cfg`;
`ConditioningZeroOut` takes one `conditioning`. Decode the distilled
subgraph's own `links` list and confirm the wiring block in "Verified facts"
above before writing a line of graph code. If anything differs, STOP and
report — the wiring is transcribed, never invented.

- [ ] **Step 2: Write the failing tests**

Append to `modules/vision/tests/test_comfyui_workflows.py` (reuse that file's
existing `EDIT_PARAMS`, `_by_class`, `_edit_graph`, `FLUX2_CONFIG` helpers):

```python
DISTILLED_CONFIG = {**FLUX2_CONFIG, "variant": "distilled"}


class TestFlux2DistilledEditGraph:
    def test_the_variant_vocabulary_is_what_the_templates_implement(self):
        """Unlike a family, a variant is not a word the engine knows: it
        names which of a family's graphs this package has code for, so the
        package IS the vocabulary -- ONE list, because the registration form
        that offers it has not chosen a family yet."""
        assert variants() == ("distilled",)

    def test_a_distilled_graph_has_no_guidance_node(self):
        """These weights are guidance-distilled: the bundled workflow
        carries no `FluxGuidance` at all."""
        nodes = _by_class(_edit_graph("flux2", DISTILLED_CONFIG, {"init_image": "in/beach.png"}))
        assert "FluxGuidance" not in nodes
        assert "BasicGuider" not in nodes

    def test_a_distilled_graph_guides_with_a_zeroed_negative(self):
        graph = _edit_graph("flux2", DISTILLED_CONFIG, {"init_image": "in/beach.png"})
        nodes = _by_class(graph)
        text_id = next(i for i, n in graph.items() if n["class_type"] == "CLIPTextEncode")
        zero = nodes["ConditioningZeroOut"][0]
        assert zero["inputs"]["conditioning"] == [text_id, 0]
        guider = nodes["CFGGuider"][0]
        assert set(guider["inputs"]) == {"model", "positive", "negative", "cfg"}

    def test_the_guidance_param_is_this_variants_cfg(self):
        """One operator control, three honest wirings: this family's
        guidance node, the other family's `KSampler.cfg`, and here the
        `CFGGuider`'s own."""
        nodes = _by_class(_edit_graph("flux2", DISTILLED_CONFIG, {"init_image": "in/beach.png"}))
        assert nodes["CFGGuider"][0]["inputs"]["cfg"] == 4.0  # EDIT_PARAMS' value, unchanged

    def test_every_reference_is_chained_on_both_branches_from_the_same_latents(self):
        """Two images, four `ReferenceLatent`s: the positive chain and the
        zeroed negative chain each see BOTH images, and they share the two
        `VAEEncode` latents rather than encoding anything twice."""
        graph = _edit_graph(
            "flux2", DISTILLED_CONFIG,
            {"init_image": "in/beach.png", "reference_image": "in/style.png"},
        )
        nodes = _by_class(graph)
        assert len(nodes["ReferenceLatent"]) == 4
        assert len(nodes["VAEEncode"]) == 2
        latents = [node["inputs"]["latent"] for node in nodes["ReferenceLatent"]]
        assert sorted(map(str, latents)) == sorted(map(str, latents[:2] * 2))

    def test_the_edited_image_is_chained_first_on_both_branches(self):
        """Chain order IS the reference numbering an instruction can refer
        to, and the bundled two-image distilled subgraph chains the edited
        image first on both branches (link ids 201-216 of
        `image_flux2_klein_image_edit_9b_distilled.json`)."""
        graph = _edit_graph(
            "flux2", DISTILLED_CONFIG,
            {"init_image": "in/beach.png", "reference_image": "in/style.png"},
        )
        nodes = _by_class(graph)
        first_encode = next(i for i, n in graph.items() if n["class_type"] == "VAEEncode")
        heads = [n for n in nodes["ReferenceLatent"] if n["inputs"]["latent"] == [first_encode, 0]]
        assert len(heads) == 2  # one per branch, and they come first

    def test_an_undeclared_variant_still_builds_the_undistilled_graph(self):
        """Every connection registered before variants existed declares
        none, and must keep the graph it has always had."""
        nodes = _by_class(_edit_graph("flux2", FLUX2_CONFIG, {"init_image": "in/beach.png"}))
        assert "FluxGuidance" in nodes and "BasicGuider" in nodes
        assert "CFGGuider" not in nodes

    def test_an_unrecognized_variant_degrades_to_the_undistilled_graph(self):
        """A declaration this adapter has no graph for is not a crash: it is
        the family's ordinary graph, which is what the family word promised."""
        nodes = _by_class(
            _edit_graph("flux2", {**FLUX2_CONFIG, "variant": "no-such-variant"},
                        {"init_image": "in/beach.png"})
        )
        assert "BasicGuider" in nodes

    def test_the_variant_defaults_are_the_bundled_widgets(self):
        assert variant_defaults("edit", "flux2", "distilled") == {"steps": 4, "guidance": 1.0}
        assert variant_defaults("edit", "flux2", "") == {}
        assert variant_defaults("txt2img", "flux2", "distilled") == {}
```

Append to `modules/vision/tests/test_comfyui_engine.py`:

```python
    def test_the_engine_republishes_the_variants_its_templates_implement(self):
        engine = ComfyUIEngine()
        assert engine.list_variants() == ("distilled",)

    def test_param_defaults_come_from_the_connections_own_declaration(self):
        engine = ComfyUIEngine()
        assert engine.param_defaults(
            "edit", {"family": "flux2", "variant": "distilled"}
        ) == {"steps": 4, "guidance": 1.0}
        assert engine.param_defaults("edit", {"family": "flux2"}) == {}
        assert engine.param_defaults("edit", None) == {}
```

Append to `modules/vision/tests/test_services.py` (follow that file's own
`ResolvedModel` construction idiom):

```python
class TestLiveDefaults:
    def test_the_selected_models_own_defaults_are_reported(self):
        resolved = _resolved(config={"family": "flux2", "variant": "distilled"})
        assert services.live_defaults(EDIT, resolved) == {"steps": 4, "guidance": 1.0}

    def test_nothing_selected_reports_nothing(self):
        assert services.live_defaults(EDIT, None) == {}

    def test_a_key_the_operation_does_not_declare_is_dropped(self):
        """An engine reports what its graph wants; only the SCHEMA says what
        a param is. A reported key with no param is not a param."""
        engine = _StubEngine(param_defaults=lambda op, config: {"steps": 4, "not_a_param": 1})
        with patch.dict(ENGINES, {"comfyui": engine}, clear=True):
            assert services.live_defaults(EDIT, _resolved()) == {"steps": 4}

    def test_an_adapter_with_no_such_member_reports_nothing(self):
        with patch.dict(ENGINES, {"comfyui": _StubEngine()}, clear=True):
            assert services.live_defaults(EDIT, _resolved()) == {}

    def test_a_raising_adapter_costs_the_defaults_and_never_a_500(self):
        engine = _StubEngine(param_defaults=_raise)
        with patch.dict(ENGINES, {"comfyui": engine}, clear=True):
            assert services.live_defaults(EDIT, _resolved()) == {}

    def test_the_catalog_reports_the_selected_models_defaults(self):
        """`operation_catalog` is what a tool reads before it submits: the
        default it sees must be the one the page starts at."""
        resolved = _resolved(config={"family": "flux2", "variant": "distilled"})
        entry = next(e for e in services.operation_catalog(resolved) if e["key"] == "edit")
        assert {p["key"]: p["default"] for p in entry["params"]}["steps"] == 4
```

Append to `modules/vision/tests/test_views_create.py`:

```python
    def test_the_form_starts_at_the_picked_models_own_defaults(self, client):
        """Switching to a distilled model changes what the Steps field says
        before the operator types anything -- the whole point of the seam."""
        connection = _image_connection(config={"family": "flux2", "variant": "distilled"})
        response = client.get(f"{reverse('vision-create')}?connection={connection.pk}")
        assert response.context["form"].fields["steps"].initial == 4
```

Append to `console/inference/tests/test_views.py`'s `TestConnectionAdd`:

```python
    def test_manual_registration_stores_a_declared_variant(self, client):
        self._post(
            client, name="distilled edit model", engine="comfyui",
            endpoint="http://comfy.test:8188", model_id="weights-Q8_0.gguf",
            capability="image-generation", family="flux2", variant="distilled",
            text_encoder="enc-b.gguf", vae="vae-a.safetensors",
        )
        assert ModelConnection.objects.get(name="distilled edit model").config == {
            "family": "flux2", "variant": "distilled",
            "text_encoder": "enc-b.gguf", "vae": "vae-a.safetensors",
        }

    def test_an_edit_form_save_round_trips_the_variant(self, client):
        """The new word rides the rule the fix round already landed: posted
        blank clears, absent keeps. This pins that adding a fourth key did
        not give it a fourth behaviour."""
        connection = ModelConnection.objects.create(
            name="edit model", engine="comfyui", endpoint="http://comfy.test:8188",
            model_id="weights-Q8_0.gguf", capabilities=["image-generation"],
            config={"family": "flux2", "variant": "distilled",
                    "text_encoder": "enc-b.gguf", "vae": "vae-a.safetensors"},
        )
        self._post(
            client, connection_id=str(connection.pk), name="edit model",
            engine="comfyui", endpoint="http://comfy.test:8188",
            model_id="weights-Q8_0.gguf", capability="image-generation",
            family="flux2", variant="", text_encoder="enc-b.gguf",
            vae="vae-a.safetensors",
        )
        connection.refresh_from_db()
        assert "variant" not in connection.config   # posted blank -> cleared
        assert connection.config["family"] == "flux2"
```

and, in whichever class of `console/inference/tests/test_views.py` renders the
registered-connections section, one test that the Edit form offers the new
field (assert `name="variant"` and the connection's stored variant appear in
the rendered edit form).

The fix round's own config tests must keep passing UNCHANGED. If one breaks,
the posted-key tuple was widened into something more than a fourth key.

- [ ] **Step 3: Run them to verify they fail**

```
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_workflows.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console/inference/tests/test_views.py -q
```
Expected: FAIL (`ImportError: cannot import name 'variants'`, and the
config-wipe test asserting the wiped dict).

- [ ] **Step 4: The variant vocabulary, in the package that owns graph words**

In `core/inference/engines/comfyui_workflows/__init__.py`, beside `_TEMPLATES`:

```python
# (family, variant) -> {operation key: {param key: the default that variant
# wants}}.
#
# A VARIANT is a second graph for the SAME family: same loaders, same
# `CLIPLoader.type`, same latent format -- different wiring, because the
# weights were distilled differently. It is an operator DECLARATION on the
# connection (`config["variant"]`, ADR 0012 D-EDIT-6), because nothing
# ComfyUI serves over HTTP distinguishes the two files, and this platform
# never infers one from a filename.
#
# Unlike a family, a variant is NOT a word the engine knows: it names which
# of a family's graphs THIS package has code for, so this mapping is the
# entire vocabulary. The defaults are widget values transcribed from the
# bundled workflow the variant's graph was derived from -- facts about a
# graph, which is why they live here and not in `core.inference.operations`
# (ADR 0012 D-EDIT-7).
_VARIANTS: dict[tuple[str, str], dict[str, dict]] = {
    ("flux2", flux2_edit.DISTILLED): {"edit": flux2_edit.DISTILLED_DEFAULTS},
}


def variants() -> tuple[str, ...]:
    """Every variant this package implements, in registration order,
    deduped.

    Deliberately NOT per-family. The one caller is a registration form that
    has not chosen a family yet -- one plain HTML form, no JavaScript, so it
    cannot re-fetch a list when the family select changes. It offers the
    union, and a pairing that does not exist simply builds the family's
    ordinary graph (`variant_defaults` is keyed by `(family, variant)` and
    answers `{}`). A per-family argument with no caller would be a parameter
    the tests exercise and nothing else does.
    """
    ordered: list[str] = []
    for _family, variant in _VARIANTS:
        if variant not in ordered:
            ordered.append(variant)
    return tuple(ordered)


def variant_defaults(operation_key: str, family: str = "", variant: str = "") -> dict:
    """The param defaults `(family, variant)` wants for `operation_key`, or
    `{}` -- for no declared variant, an unknown one, or an operation that
    variant changes nothing about. Never raises: a stored declaration is
    operator text and this is a lookup, not a validation."""
    if not variant:
        return {}
    return dict(_VARIANTS.get((family, variant), {}).get(operation_key, {}))
```

- [ ] **Step 5: The distilled branch of the `flux2` edit graph**

In `core/inference/engines/comfyui_workflows/flux2_edit.py`, add the module
constants and rewrite `build` (the undistilled path must come out
byte-equivalent — the tests from Task 6 are the guard):

```python
# The one variant of this family this module has a graph for (ADR 0012
# D-EDIT-6). Derived node-for-node from the "Image Edit (Flux.2 Klein 9B
# Distilled)" subgraphs in ComfyUI 0.33.0's own template bundle, whose two
# copies differ from the undistilled workflow in exactly three ways: no
# `FluxGuidance` (the weights are guidance-distilled), a `CFGGuider` whose
# negative is the zeroed-out instruction instead of a `BasicGuider`, and
# their own step/cfg widget values.
DISTILLED = "distilled"

# Those widget values: `Flux2Scheduler.steps` = 4, `CFGGuider.cfg` = 1.
# Reported to the platform through `variant_defaults` so the form STARTS
# where this graph expects, while `guidance` and `steps` stay ordinary
# operator params (ADR 0012 D-EDIT-7).
DISTILLED_DEFAULTS = {"steps": 4, "guidance": 1.0}


def build(request: GenerationRequest, model_id: str, config: dict, inputs: dict) -> dict:
    params = request.params
    graph = _fragments.Graph()
    parts = _fragments.components(graph, model_id, config)
    distilled = str(config.get("variant") or "") == DISTILLED
    guidance = float(params["guidance"])

    text = _fragments.encode_text(graph, parts, params.get("instruction"))
    # Distilled: the instruction goes in unguided, and its ZEROED copy is
    # the negative half of a CFG pair. Undistilled: one guided conditioning
    # and no negative path at all, which is why `edit` declares no negative
    # prompt for either.
    positive = text if distilled else _flux_guidance(graph, text, guidance)
    negative = _zeroed(graph, text) if distilled else None

    # Reference Image 1: the picture being edited. Its size is the output's.
    edited = _fragments.scale_to_megapixels(
        graph, _fragments.load_image(graph, inputs["init_image"])
    )
    width, height = _fragments.image_size(graph, edited)
    latents = [_fragments.encode_image(graph, parts, edited)]

    # Reference Image 2, when the operator attached one.
    reference = inputs.get("reference_image")
    if reference:
        extra = _fragments.scale_to_megapixels(graph, _fragments.load_image(graph, reference))
        latents.append(_fragments.encode_image(graph, parts, extra))

    # Chain order IS the reference numbering, and every branch sees the same
    # latents in the same order -- the bundled distilled two-image subgraph
    # chains the edited image first on BOTH branches.
    for latent in latents:
        positive = _reference_latent(graph, positive, latent)
        if negative is not None:
            negative = _reference_latent(graph, negative, latent)

    guider = (
        _cfg_guider(graph, parts.model, positive, negative, guidance)
        if distilled
        else _basic_guider(graph, parts.model, positive)
    )
    latent = _empty_latent(graph, width, height)
    sigmas = _sigmas(graph, params["steps"], width, height)
    samples = _sample(graph, guider, sigmas, latent, params["seed"])
    _fragments.decode_and_save(graph, parts, samples, request)
    return graph.as_dict()


def _zeroed(graph: _fragments.Graph, conditioning: list) -> list:
    """The instruction with its embedding zeroed -- this variant's negative.
    Zeroed BEFORE the reference chain, so both branches still see every
    reference image (the bundled subgraph's own order)."""
    return [graph.add("ConditioningZeroOut", conditioning=conditioning), 0]


def _basic_guider(graph: _fragments.Graph, model: list, conditioning: list) -> list:
    """The undistilled guider: one conditioning, no negative."""
    return [graph.add("BasicGuider", model=model, conditioning=conditioning), 0]


def _cfg_guider(
    graph: _fragments.Graph, model: list, positive: list, negative: list, cfg: float
) -> list:
    """The distilled guider. `guidance` lands on `cfg` here, on
    `FluxGuidance.guidance` in the undistilled graph, and on `KSampler.cfg`
    in the other family -- one operator control, three honest wirings."""
    return [
        graph.add("CFGGuider", model=model, positive=positive, negative=negative, cfg=cfg),
        0,
    ]
```

and narrow `_sample` to take the guider its caller built:

```python
def _sample(graph, guider: list, sigmas: list, latent: list, seed: int) -> list:
    """Guider + sampler + noise into `SamplerCustomAdvanced`. Which guider
    is the variant's business, not this function's."""
    sampler = graph.add("KSamplerSelect", sampler_name=SAMPLER)
    noise = graph.add("RandomNoise", noise_seed=seed)
    node = graph.add(
        "SamplerCustomAdvanced",
        noise=[noise, 0], guider=guider, sampler=[sampler, 0],
        sigmas=sigmas, latent_image=latent,
    )
    return [node, 0]
```

Update the module docstring to say the module owns TWO graphs for one family
and name the bundled workflow each came from.

**And retire the unconfirmed note next door.** `qwen_edit.py:29-33` carries a
hedge from the Task 6 review ("the other edit family's own bundled
two-reference template may chain its last reference first instead -- not
verified here"). It is now verified, and the hedge is wrong: the distilled
two-image subgraph chains the EDITED image first on BOTH branches (link ids
201-216 of `image_flux2_klein_image_edit_9b_distilled.json`, decoded in
"Verified facts" above), which is the same order this package already
builds. Delete those lines and replace them with the verified fact and its
citation. `modules/vision/README.md:194-198` carries the same hedge (Task 7's
review nit) — retire it there too, in this task's docs step.

- [ ] **Step 6: The adapter republishes both facts**

In `core/inference/engines/comfyui.py` (import `variants`/`variant_defaults`
beside `families`/`get_template`/`template_keys`), next to `list_families`:

```python
    def list_variants(self) -> tuple[str, ...]:
        """Variants an operator may declare for a connection (ADR 0012
        D-EDIT-6): a second graph for the SAME family, for weights this
        adapter cannot tell apart over HTTP.

        Unlike `list_families`, nothing is intersected with an engine
        vocabulary -- ComfyUI has no word for this. The template package's
        own list IS the honest answer, and it is ONE list rather than one
        per family: the form that renders it has no family chosen yet.
        """
        return variants()

    def param_defaults(self, operation_key: str, config: dict | None = None) -> dict:
        """Where a form for `operation_key` should START for a connection
        registered with `config` -- `{}` when this adapter has no opinion.

        The twin of `list_choices`/`list_assets`: those report options the
        SCHEMA cannot know, this reports defaults the schema cannot know,
        because a step count is a fact of a graph. It never narrows, adds,
        or removes a param -- the schema alone declares those (ADR 0012
        D-EDIT-7).
        """
        cfg = dict(config or {})
        return variant_defaults(
            operation_key, str(cfg.get("family") or ""), str(cfg.get("variant") or "")
        )
```

Document both on the `InferenceEngine` protocol in
`core/inference/engines/base.py` as OPTIONAL members, in the same words the
existing optional members use ("read with `getattr`; an adapter that predates
it degrades to nothing").

- [ ] **Step 7: One service seam, twinned with `live_options`**

In `modules/vision/services.py`, directly BELOW `live_options` (they are read
together):

```python
def live_defaults(operation: Operation, resolved) -> dict:
    """Where `operation`'s form should START for the SELECTED model.

    The exact twin of `live_options` above, and for the same reason: the
    schema declares what a param IS, and only the engine knows what THIS
    model's graph wants it to be -- a distilled build samples in a handful
    of steps and fixes its guidance, and a form that opened at the schema's
    numbers would cost the operator a wrong, slow generation to discover it.

    Reported keys the operation does not declare are DROPPED: an engine may
    describe a param, never invent one. Returns `{}` for an unbound role, an
    unregistered engine name, an adapter with no such member, or one that
    raises -- an opening value is never worth a 500, and the schema's own
    default is always a truthful fallback.
    """
    if resolved is None:
        return {}
    try:
        engine = get_engine(resolved.engine)
    except Exception:  # noqa: BLE001 -- an unregistered engine name is not a 500
        logger.debug("get_engine(%r) failed", resolved.engine, exc_info=True)
        return {}
    reader = getattr(engine, "param_defaults", None)
    if reader is None:
        return {}
    try:
        reported = dict(reader(operation.key, dict(resolved.config or {})))
    except Exception:  # noqa: BLE001 -- never 500 over a default
        logger.debug("param_defaults failed for %r", operation.key, exc_info=True)
        return {}
    declared = {param.key for param in operation.params}
    return {key: value for key, value in reported.items() if key in declared}
```

and in `operation_catalog`, beside the options merge already there:

```python
        options = live_options(operation, check.resolved)
        defaults = live_defaults(operation, check.resolved)
        for param in entry["params"]:
            if param["key"] in options:
                param["options"] = list(options[param["key"]])
            if param["key"] in defaults:
                param["default"] = defaults[param["key"]]
```

- [ ] **Step 8: The page opens at those defaults**

In `modules/vision/views.py`, `CreatePageView.get_context_data` — the ONE
place an UNBOUND form is built (`_create_page_response` re-renders a form it
was handed; `build_form_for` binds POST data, which always wins):

```python
        # The engine's opening values, under anything the operator is
        # reusing: a reused job's own settings are a stronger statement
        # about what to run than a model's defaults.
        context["form"] = build_form(
            operation,
            services.live_options(operation, check.resolved),
            initial={
                **services.live_defaults(operation, check.resolved),
                **(_reuse_initial(reuse_job) or {}),
            },
            stored_keys=frozenset(item["param_key"] for item in context["stored_inputs"]),
        )
```

`modules/vision/forms.py` is NOT modified: `build_form(initial=...)` already
applies a mapping to field initials and already skips file params.

- [ ] **Step 9: The console asks for the variant, on the forms that already ask for a family**

The Edit form's fields and the absent-vs-keep rule LANDED in the parent
plan's final-review fix round (D-EDIT-8). Read
`console/inference/views.py`'s `_declared_config` (and whatever the fix round
actually named it) plus both templates' family fieldsets FIRST; this step
extends them by one field and adds no rule.

In `console/inference/views.py`:

1. `_engine_options` gains a `"variants"` branch beside `"families"`:
   ```python
        if what == "variants":
            reader = getattr(engine, "list_variants", None)
            return [] if reader is None else list(reader())
   ```
   and `_build_context` adds `context["variant_options"] = _engine_options(family_engine, endpoint, "variants")`.
2. `"variant"` is added to `_declared_config`'s posted-key tuple — the one
   place that decides which POST keys are config keys:
   ```python
        for key in ("family", "variant", "text_encoder", "vae"):
   ```
   Nothing else in that function changes: blank still clears, absent still
   keeps, and the variant inherits both behaviours for free.

In `console/inference/templates/inference/console.html`, add a Variant
`<select>` to the family fieldset in BOTH copies of the manual form, after
the family select:

```html
          <label for="variant">Variant (only some families have one)</label>
          <select id="variant" name="variant">
            <option value="">— none —</option>
            {% for option in variant_options %}
            <option value="{{ option }}">{{ option }}</option>
            {% endfor %}
          </select>
```

In `console/inference/templates/inference/_connection_edit.html`, add the
same `<select>` to the family fieldset the fix round put there, prefilled and
id-namespaced the way that fieldset's other selects already are:

```html
    <label for="variant-{{ connection.id }}">Variant</label>
    <select id="variant-{{ connection.id }}" name="variant">
      <option value="">— none —</option>
      {% for option in variant_options %}
      <option value="{{ option }}"{% if connection.config.variant == option %} selected{% endif %}>{{ option }}</option>
      {% endfor %}
    </select>
```

(If that fieldset is gated — e.g. on the connection being an image model —
leave the gate exactly as it is: absent-means-keep is what makes it safe, and
that rule is already landed and already tested.)

- [ ] **Step 10: Run the tests**

```
DATABASE_URL='…' … -m pytest modules/vision/tests/test_comfyui_workflows.py -q
DATABASE_URL='…' … -m pytest modules/vision/tests/test_comfyui_engine.py modules/vision/tests/test_services.py -q
DATABASE_URL='…' … -m pytest modules/vision/tests/test_views_create.py -q
DATABASE_URL='…' … -m pytest console/inference/tests -q
DATABASE_URL='…' … -m pytest modules/vision/tests -q
```
Expected: PASS, and no Task-6 `flux2` test edited to make it so.

- [ ] **Step 11: Docs**

- `modules/vision/README.md`, in "`edit` — one operation, several graphs":
  a short subsection on the variant (what it is, why it is declared and not
  detected, that an unknown one degrades to the family's ordinary graph) and
  one paragraph on the defaults seam. That paragraph must state the seam's
  one real edge in so many words: **the catalog's default is where a FORM
  opens; a caller that omits the param still gets the SCHEMA default.**
  `validate_params` is the floor every caller shares and it knows nothing
  about a model (D-EDIT-7), so `GET /vision/operations/?connection=<pk>` can
  report `steps: 4` while a scripted POST that sends no `steps` records 20 —
  which is honest (the schema's own value) but surprising, and a tool author
  reading the catalog must be told to SEND what it read rather than assume
  the omission means the same thing.
- `modules/vision/README.md:194-198`: retire the Task 7 hedge about the other
  family's reference order (see Step 5) — the distilled two-image subgraph
  verifies it, and the two families agree.
- `console/inference/README.md`, "Registering a multi-file model family":
  the fourth field, on both forms. (The absent-vs-blank sentence is the fix
  round's to write, not this task's — check whether it is already there
  before adding a second copy.)

- [ ] **Step 12: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(vision): a distilled variant of an edit family, declared not guessed

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

- [ ] **Step 13: REVIEW GATE.** Dispatch a code review (SPEC + QUALITY)
  before Task 14. Reviewer must specifically check: (a) the undistilled graph
  is unchanged, node-for-node; (b) the distilled wiring matches the bundled
  subgraph's link list, including the zero-out sitting BEFORE the reference
  chain; (c) `live_defaults` never widens or narrows a param set;
  (d) that the landed `_declared_config` / Edit-form work was EXTENDED by one
  key and not reimplemented, and that the fix round's own config tests pass
  unedited; (e) no model name anywhere outside tests.

---

## Task 14: `edit` gets the LoRA params both families' graphs can serve

**Files:**
- Modify: `core/inference/operations.py` (`EDIT`, `LORA_PARAMS` copy)
- Modify: `core/inference/engines/comfyui_workflows/_fragments.py` (`model_lora_chain`)
- Modify: `core/inference/engines/comfyui_workflows/flux2_edit.py`, `qwen_edit.py`
- Test: `modules/vision/tests/test_comfyui_workflows.py`, `test_operations.py`, `test_forms.py` (if it pins `edit`'s fields)
- Docs: `modules/vision/README.md` ("LoRAs" + the `edit` section), `docs/adr/0012-image-generation-engine-adapter.md`

**Interfaces:**
- Consumes: `LORA_PARAMS`, `Checkpoint`, both edit templates' model links.
- Produces: `_fragments.model_lora_chain(graph, model, params) -> Link`.

- [ ] **Step 1: Confirm the node, read-only**

```bash
curl -s http://localhost:8188/object_info/LoraLoaderModelOnly
```
Expected: required `model`, `lora_name`, `strength_model`; output `["MODEL"]`
and no CLIP. If it reports a `strength_clip`, STOP — the fragment below would
be silently dropping a control.

- [ ] **Step 2: Write the failing tests**

In `modules/vision/tests/test_comfyui_workflows.py`:

```python
LORA_EDIT_PARAMS = {**EDIT_PARAMS, "loras": ["speed-a.safetensors"], "lora_strength": 0.8}


class TestEditLoRAs:
    def test_no_selection_adds_no_node_to_either_family(self):
        """"No adapters" is a normal answer, and it must not change the
        graph's shape."""
        for family, config in (("flux2", FLUX2_CONFIG), ("qwen_image", QWEN_CONFIG)):
            nodes = _by_class(_edit_graph(family, config, {"init_image": "in/beach.png"},
                                          {**EDIT_PARAMS, "loras": [], "lora_strength": 1.0}))
            assert "LoraLoaderModelOnly" not in nodes

    def test_each_selected_adapter_is_chained_model_only(self):
        for family, config in (("flux2", FLUX2_CONFIG), ("qwen_image", QWEN_CONFIG)):
            graph = _edit_graph(family, config, {"init_image": "in/beach.png"},
                                {**LORA_EDIT_PARAMS,
                                 "loras": ["speed-a.safetensors", "style-b.safetensors"]})
            chain = _by_class(graph)["LoraLoaderModelOnly"]
            assert [n["inputs"]["lora_name"] for n in chain] == [
                "speed-a.safetensors", "style-b.safetensors"
            ]
            assert all(n["inputs"]["strength_model"] == 0.8 for n in chain)
            assert all("strength_clip" not in n["inputs"] for n in chain)
            assert chain[1]["inputs"]["model"] == [
                next(i for i, n in graph.items() if n is chain[0]), 0
            ]

    def test_the_chain_is_the_last_model_step_before_the_sampler(self):
        """Both bundled edit workflows put the adapter last: straight after
        the loader where nothing else wraps the model, and after this
        family's own two model nodes where they do."""
        graph = _edit_graph("qwen_image", QWEN_CONFIG, {"init_image": "in/beach.png"},
                            LORA_EDIT_PARAMS)
        nodes = _by_class(graph)
        norm_id = next(i for i, n in graph.items() if n["class_type"] == "CFGNorm")
        assert nodes["LoraLoaderModelOnly"][0]["inputs"]["model"] == [norm_id, 0]
        lora_id = next(i for i, n in graph.items() if n["class_type"] == "LoraLoaderModelOnly")
        assert nodes["KSampler"][0]["inputs"]["model"] == [lora_id, 0]

    def test_a_distilled_flux_graph_takes_them_too(self):
        graph = _edit_graph("flux2", DISTILLED_CONFIG, {"init_image": "in/beach.png"},
                            LORA_EDIT_PARAMS)
        nodes = _by_class(graph)
        lora_id = next(i for i, n in graph.items() if n["class_type"] == "LoraLoaderModelOnly")
        assert nodes["CFGGuider"][0]["inputs"]["model"] == [lora_id, 0]
```

In `modules/vision/tests/test_operations.py`:

```python
    def test_edit_declares_the_same_lora_controls_as_every_other_mode(self):
        """Both edit families' bundled workflows carry a model-only LoRA
        loader, so the control is honest for `edit` under D-EDIT-1's
        intersection rule -- and it is the SAME two params, not a second
        vocabulary."""
        keys = [param.key for param in EDIT.params]
        assert keys[-2:] == ["loras", "lora_strength"]
        assert EDIT.param("loras").asset_kind == "lora"
```

- [ ] **Step 3: Run them to verify they fail**

- [ ] **Step 4: One shared fragment**

In `core/inference/engines/comfyui_workflows/_fragments.py`, beside
`_lora_chain`:

```python
def model_lora_chain(graph: Graph, model: Link, params: dict) -> Link:
    """Chain one `LoraLoaderModelOnly` per selected adapter, MODEL->MODEL,
    and hand back the adorned link.

    The MODEL-ONLY sibling of `_lora_chain` above, and a separate function
    rather than a flag on it: that one rewires CLIP as well because a
    checkpoint's `LoraLoader` outputs both, while this node outputs MODEL
    alone (verified against a live ComfyUI 0.33.0 -- there is no
    `strength_clip` input to pass). Both edit families' bundled workflows
    use exactly this node, which is what makes a LoRA param honest for
    `edit` at all (ADR 0012 D-EDIT-9).

    Called by each edit template at the position ITS bundled workflow puts
    the node: last model step before the guider or sampler.

    One strength for the whole selection, like every other mode -- a `Param`
    carries one value.
    """
    names = params.get("loras") or ()
    if isinstance(names, str):
        names = (names,)
    strength = float(params.get("lora_strength") or 1.0)
    for name in names:
        model = [
            graph.add(
                "LoraLoaderModelOnly", model=model, lora_name=name, strength_model=strength
            ),
            0,
        ]
    return model
```

- [ ] **Step 5: Both templates apply it, each in its own place**

- `flux2_edit.build`, right after `components(...)`:
  ```python
      parts = _fragments.components(graph, model_id, config)
      # This family's bundled workflow puts the adapter straight after the
      # loader; nothing else wraps the model here.
      model = _fragments.model_lora_chain(graph, parts.model, params)
  ```
  and both guiders take `model` instead of `parts.model`.
- `qwen_edit.build`, after its own chain:
  ```python
      model = _fragments.model_lora_chain(graph, _model_chain(graph, parts.model), params)
  ```
  matching that bundled workflow's own order (`ModelSamplingAuraFlow` →
  `CFGNorm` → adapter → `KSampler`).

**Both module docstrings become FALSE with this change and must be rewritten
in the same commit.** `flux2_edit.py:5-8` and `qwen_edit.py:5-8` each say the
template drops the bundled workflow's LoRA cluster because "the platform
ships no such LoRA, and the `edit` operation declares no LoRA param" — after
this task the platform ships two of them and `edit` declares both params. Each
paragraph becomes: the template drops the bundled workflow's `ComfySwitchNode`
/ `Primitive*` TOGGLE (a UI convenience for a graph builder, not a pipeline
fact) and keeps its `LoraLoaderModelOnly` as a real operator param, applied
through `_fragments.model_lora_chain` at THIS family's own position —
straight after the loader for one, after `ModelSamplingAuraFlow`→`CFGNorm`
for the other. A stale docstring here is worse than none: it is the file
future readers will trust about why a node is missing.

- [ ] **Step 6: `EDIT` declares them, and says the one true thing about distillation adapters**

In `core/inference/operations.py`, splice `*LORA_PARAMS` after `seed` in
`EDIT.params`, replace the "no LoRA" clause of `EDIT`'s leading comment with
the reason it is now served (both bundled workflows carry
`LoraLoaderModelOnly`; the exclusion was about the checkpoint-shaped fragment,
which no longer has to serve it), and extend `LORA_PARAMS`' `loras`
description with ONE family-neutral sentence:

```python
            "Style or subject adapters to apply on top of the model, chosen from what "
            "the engine reports having installed. Selecting none is a normal answer. "
            "Some adapters are distillation adapters: they replace the guidance "
            "setting — set Steps to the count the adapter names and Guidance to 1."
```

(Note the same edit changes "on top of the checkpoint" to "on top of the
model": `edit` runs on a diffusion-model file, and one shared sentence must
be true for every mode that splices it.)

- [ ] **Step 7: Run the tests, then the suites**

```
DATABASE_URL='…' … -m pytest modules/vision/tests/test_comfyui_workflows.py modules/vision/tests/test_operations.py -q
DATABASE_URL='…' … -m pytest modules/vision/tests -q
DATABASE_URL='…' … -m pytest console modules scripts -q
```
Expected: PASS. Tests that pin `edit`'s param list or its rendered form
(`test_forms.py`, `test_views_create.py`, `test_views_operations.py`) may need
their expected tuples extended — extend them; do not narrow the operation.

- [ ] **Step 8: Docs and commit**

- `modules/vision/README.md`: "LoRAs" gains a paragraph on the model-only
  chain (`model_lora_chain`, why it is a second function, that `edit` uses
  it in both families and where each puts it); the `edit` section notes the
  params are now declared.
- `docs/adr/0012-image-generation-engine-adapter.md`: amend the D-EDIT-1
  table — the `loras` row moves from excluded to served, with the verified
  reason — and record D-EDIT-6…D-EDIT-9 (D-EDIT-10 is process and belongs in
  the ledger, not the ADR; D-EDIT-11 is Task 16's own docs step).

```bash
git commit -m "$(cat <<'EOF'
feat(vision): edit takes the model-only LoRA chain both families ship

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

- [ ] **Step 9: REVIEW GATE.** Reviewer must check: (a) the chain position in
  each template against that family's bundled workflow; (b) that no graph
  gains a node when nothing is selected; (c) that `_lora_chain` was not
  refactored into a shared flag (two nodes, two behaviours, two functions);
  (d) that the new description sentence names no file, vendor, or step count
  as a shipped fact.

---

## Task 15: Live verification, one model at a time, and the close

**Files:** none in `core/`, `console/`, or `modules/` unless a live run finds
a defect. Docs + ledger only.

- [ ] **Step 1: Preconditions (abort on any failure)**

- [ ] Tasks 13 and 14 committed and reviewed; full suite green in BOTH orders; `makemigrations --check --dry-run` clean.
- [ ] `ls -la <home>/ComfyUI/models/{unet,text_encoders,vae,loras}/` — confirm the four files this task uses are present and that no fp8 encoder has reappeared for the undistilled model.
- [ ] Chrome quit (`ps aux | grep -c "[C]hrome"` → 0) for the sampling window; no other preview stack up; the branch preview is the only one running.
- [ ] `curl -s http://localhost:8188/queue` — both lists empty.
- [ ] `curl -s http://localhost:8188/system_stats` — record `system.ram_free`. It must clear the model's expected resident size with real margin (the distilled 9B + Q8 encoder ≈ 19.5 GB expected; measured on 2026-08-25 the machine has reported as little as 5 GB free with other work resident, so this is a REAL gate, not a formality).
- [ ] No RAG LLM resident. The queue's eviction should have done it — verify that it did; do not evict by hand.

- [ ] **Step 2: OPERATOR — register the distilled connection**

In `/inference/` → "Add a connection manually", exactly these values (the
only place in this plan that may name files):

| Field | Value |
| --- | --- |
| Name | operator's own label |
| Model server | `comfyui` |
| Endpoint | the endpoint the existing image connection already uses (visible on `/inference/`) |
| Model ID | `flux-2-klein-9b-Q8_0.gguf` |
| Capability | `image-generation` (checked) |
| Model family | `flux2` |
| Variant | `distilled` |
| Text encoder | `Qwen3-8B-Q8_0.gguf` |
| VAE | `flux2-vae.safetensors` |

- [ ] Confirm the saved row's config carries all four keys (the connection's own Edit form now shows them — that is also this step's proof of Task 13's form fix).

- [ ] **Step 3: LIVE RUN A — the distilled model, through the queue**

- [ ] In `/vision/`, pick the new connection in the model picker. Confirm the form OPENS at Steps 4 and Guidance 1 without typing anything (Task 13's seam, live).
- [ ] Attach one small image, type a short instruction, submit. `/vision/generate/` → `core.inference.queue.enqueue` → the worker. No other route.
- [ ] Watch `/queue/`. Record: admission, the elapsed-seconds line, terminal state, wall-clock seconds per step.
- [ ] Record the outcome: the image on the card, or the job's honest failure text (the GGUF custom-node pack's tokenizer patch from LIVE RUN #2 applies to a `flux2`-type CLIP loader — if a comparable `KeyError` appears for this encoder, record it and STOP rather than patching under time pressure).
- [ ] Record `ComfyUIEngine.loaded_footprint`'s number for the job and `system.ram_free` at peak and at rest.
- [ ] Unload through the seam before Step 4. Confirm `ram_free` recovers.

- [ ] **Step 4: LIVE RUN B — the other family with a speed adapter**

- [ ] Re-check every Step 1 precondition. The undistilled `qwen_image`
      connection uses an **fp8** text encoder, which upcasts on MPS (the
      unified-memory lesson): its pipeline peak may be far above the
      distilled one's. If `ram_free` does not clear it with margin, STOP and
      report — the honest follow-up is a GGUF encoder for that family, which
      is an owner download decision and out of this plan's scope.
- [ ] In `/vision/`, pick the `qwen_image` connection, choose the 8-step
      speed adapter in **LoRAs**, set **Steps 8** and **Guidance 1**
      (the form does not do this for you, by design — D-EDIT-9), attach the
      same image and instruction, submit.
- [ ] Record the same five facts as Step 3, plus the LoRA name on the job
      row's recorded params (`GenerationJob.params["loras"]`), which is what
      makes the run reproducible.
- [ ] Unload through the seam.

- [ ] **Step 5: Evidence, docs, ledger**

- [ ] Write both runs into this plan's "Live verification log" below —
      admission, timings, footprints, outcome — with no "works"/"done"
      language.
- [ ] `modules/vision/README.md`'s "Known limits (operational, raised on the
      live preview)": replace the estimated step-cost note with the MEASURED
      numbers for both models.
- [ ] `.superpowers/sdd/2026-08-25-vision-edit-capability/progress.md`: entries
      for Tasks 13–15, the measured footprints, and the two backlog items
      this plan deliberately did not take: the unused top encoder layers,
      and the operator-tunable generation wait timeout (already promoted
      during LIVE RUN #3).
- [ ] Full suite, both orders, one last time; then the branch's normal
      finish path (`superpowers:finishing-a-development-branch`).

- [ ] **Step 6: REVIEW GATE.** A review of the docs/ledger diff only —
  no code should have changed. If a live run forced a code change, that
  change gets its own TDD cycle and its own review before this gate.

---

---

## Task 16 (INDEPENDENT — may run first, last, or in parallel with 13–15): how long a generation waited and how long it ran

**Files:**
- Modify: `modules/vision/models.py` (derived properties only — **no new field, no migration**)
- Modify: `modules/vision/services.py` (`job_json`)
- Modify: `modules/vision/jobs.py` (`run_generate`'s result dict)
- Modify: `modules/vision/templates/vision/_job_card.html`, `modules/vision/templates/vision/gallery.html`
- Test: `modules/vision/tests/test_models.py`, `test_services.py`, `test_jobs.py`, `test_views_create.py` (or wherever that file's card-rendering tests live), `test_views_gallery.py`
- Docs: `modules/vision/README.md` (the `job_json` contract at ~line 291 and the queue-result contract at ~line 361), `docs/adr/0012-image-generation-engine-adapter.md` (the result-dict contract at ~line 200)

**Interfaces:**
- Consumes: `GenerationJob.created_at`/`started_at`/`finished_at` (existing columns), `core.format.format_timecode` (the codebase's ONE duration renderer — do not write a second).
- Produces: `GenerationJob.durations -> dict[str, float | None]` (seconds) and `GenerationJob.durations_display -> dict[str, str]` (rendered); `job_json(...)["started_at"]` and `["durations"]`; `run_generate(...)["durations"]`.

- [ ] **Step 1: Confirm the ground truth before writing anything**

Read, and confirm each still says what D-EDIT-11 quotes:
`modules/vision/models.py` (the three columns exist — grep the field names,
not line 124);
`modules/vision/services.py:643-646` (`started_at` written once, on the first
`running` poll), `:674-676` (done path), `:788` (`_fail`);
`console/jobs/models.py:91-96` (the queue's separate clocks).
If `started_at` is NOT already a column or is NOT already written on the
`running` transition, STOP and report — this task's shape depends on it.

Also confirm there is no migration to write:
```
DATABASE_URL='…' … python manage.py makemigrations --check --dry-run
```
Expected: clean, before AND after this task. **If this task ever produces a
migration, something was added that D-EDIT-11 says must not be.**

- [ ] **Step 2: Write the failing tests**

`modules/vision/tests/test_models.py` — **the three timestamps cannot be
passed to `create()`**: `created_at` is declared
`models.DateTimeField(auto_now_add=True)`, so
Django overwrites whatever a factory sets. That file already has the idiom
for this (`test_a_long_queued_job_reads_as_stale`, lines 57-62: create, then
`GenerationJob.objects.filter(pk=...).update(created_at=...)`, then
`refresh_from_db()`), so add ONE helper beside `_job` that does it for all
three, and mark the class `@pytest.mark.django_db` like every other class in
the file:

```python
def _ago(**delta) -> "datetime":
    """A timestamp `delta` before now -- `timedelta`'s own keywords."""
    return timezone.now() - timedelta(**delta)


def _timed_job(*, created=None, started=None, finished=None, **overrides) -> GenerationJob:
    """A job whose three lifecycle stamps are exactly what this test says.

    `created_at` is `auto_now_add`, so it CANNOT be passed to `create()` --
    Django replaces it on insert. The file's own answer (see
    `test_a_long_queued_job_reads_as_stale`) is to write the stamps with a
    queryset `.update()` afterwards, which bypasses `auto_now_add` and every
    `save()` hook, then re-read the row.
    """
    job = _job(**overrides)
    stamps = {"created_at": created, "started_at": started, "finished_at": finished}
    GenerationJob.objects.filter(pk=job.pk).update(
        **{key: value for key, value in stamps.items() if value is not None}
    )
    job.refresh_from_db()
    return job


@pytest.mark.django_db
class TestGenerationJobDurations:
    def test_a_queued_job_is_still_waiting(self):
        """The clock an operator is watching while nothing has started."""
        job = _timed_job(created=_ago(seconds=42), status=GenerationJob.Status.QUEUED)
        durations = job.durations
        assert 42 <= durations["queued"] < 45
        assert durations["processing"] is None
        assert 42 <= durations["total"] < 45

    def test_a_running_job_reports_both_halves_live(self):
        job = _timed_job(
            created=_ago(seconds=100), started=_ago(seconds=60),
            status=GenerationJob.Status.RUNNING,
        )
        durations = job.durations
        assert 39 <= durations["queued"] <= 41       # created -> started, fixed
        assert 60 <= durations["processing"] < 63    # started -> now, still moving
        assert 100 <= durations["total"] < 103

    def test_a_finished_job_stops_moving(self):
        job = _timed_job(
            created=_ago(seconds=300), started=_ago(seconds=280),
            finished=_ago(seconds=10), status=GenerationJob.Status.DONE,
        )
        assert job.durations["queued"] == pytest.approx(20.0, abs=1)
        assert job.durations["processing"] == pytest.approx(270.0, abs=1)
        assert job.durations["total"] == pytest.approx(290.0, abs=1)

    def test_a_job_that_never_ran_reports_no_processing_time(self):
        """A submission the engine refused waited and then failed. Its
        processing time is UNKNOWN, not zero -- `_fail` writes
        `finished_at` and never `started_at`."""
        job = _timed_job(
            created=_ago(seconds=30), finished=_ago(seconds=25),
            status=GenerationJob.Status.FAILED,
        )
        durations = job.durations
        assert durations["processing"] is None
        assert durations["queued"] == pytest.approx(5.0, abs=1)
        assert durations["total"] == pytest.approx(5.0, abs=1)

    def test_the_rendered_form_uses_the_codebases_one_timecode(self):
        job = _timed_job(
            created=_ago(seconds=300), started=_ago(seconds=280),
            finished=_ago(seconds=10), status=GenerationJob.Status.DONE,
        )
        assert job.durations_display["processing"] == format_timecode(270)
        assert job.durations_display["processing"] == "4:30"

    def test_an_unknown_duration_renders_as_nothing_at_all(self):
        """`format_timecode(None)` is `""` by contract -- a card must show
        no time rather than a made-up `0:00`."""
        job = _timed_job(
            created=_ago(seconds=30), finished=_ago(seconds=25),
            status=GenerationJob.Status.FAILED,
        )
        assert job.durations_display["processing"] == ""
```

(`timedelta`, `timezone`, and `pytest` are already imported in that file;
`format_timecode` is the one new import.)

`modules/vision/tests/test_services.py`:

```python
    def test_refresh_stamps_the_start_exactly_once(self):
        """Two `running` polls must not move the start -- the second would
        erase however long the job had already been running."""
        job = _submitted_job()
        fake = FakeComfyUI(queue_running=[[None, job.engine_ref]])
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            first = services.refresh_job(job).started_at
            second = services.refresh_job(GenerationJob.objects.get(pk=job.pk)).started_at
        assert first is not None and second == first

    def test_job_json_carries_the_start_and_the_durations(self):
        """The tool API's own contract: an agent must be able to read how
        long a generation took without parsing a card."""
        job = _finished_job(queued=20, processing=270)
        payload = services.job_json(job)
        assert payload["started_at"] == job.started_at.isoformat()
        assert payload["durations"]["processing"] == pytest.approx(270.0, abs=1)
        assert set(payload["durations"]) == {"queued", "processing", "total"}
```

`modules/vision/tests/test_jobs.py`:

```python
    def test_the_queue_result_carries_the_durations(self):
        """Beside `output_urls` and `timed_out`, so an agent driving the
        queue sees the cost of what it just ran."""
        result = jobs.run_generate(payload, models, ctx)
        assert set(result["durations"]) == {"queued", "processing", "total"}
```

Card/gallery rendering tests (in the file that already renders a card):

```python
    def test_a_waiting_card_shows_only_the_wait(self, client):
        """Each state asserts the string THAT state renders. A blanket
        "total is in the body" would pass on `0:00` in the states that
        render no total at all, and would race the clock besides."""
        job = _timed_job(created=_ago(seconds=90), status=GenerationJob.Status.QUEUED)
        body = client.get(reverse("vision-job-status", args=[job.id])).content.decode()
        assert "Waiting" in body
        assert "total" not in body.lower().split("waiting")[1][:80]

    def test_a_running_card_shows_the_wait_and_the_run(self, client):
        job = _timed_job(
            created=_ago(seconds=90), started=_ago(seconds=30),
            status=GenerationJob.Status.RUNNING,
        )
        body = client.get(reverse("vision-job-status", args=[job.id])).content.decode()
        assert "Waited" in body and "running" in body
        assert job.durations_display["queued"] in body    # fixed once started

    def test_a_finished_card_shows_all_three_from_fixed_stamps(self, client):
        """Fixed stamps, so every rendered string is exact -- nothing here
        depends on how long the test took to run."""
        job = _timed_job(
            created=_ago(seconds=300), started=_ago(seconds=280),
            finished=_ago(seconds=10), status=GenerationJob.Status.DONE,
        )
        body = client.get(reverse("vision-job-status", args=[job.id])).content.decode()
        assert "total" in body
        for key in ("queued", "processing", "total"):
            assert job.durations_display[key] in body

    def test_a_failed_card_that_never_ran_shows_no_run_time(self, client):
        job = _timed_job(
            created=_ago(seconds=60), finished=_ago(seconds=55),
            status=GenerationJob.Status.FAILED,
        )
        body = client.get(reverse("vision-job-status", args=[job.id])).content.decode()
        assert "total" in body
        assert "ran" not in body.lower().split("waited")[1][:60]
```

(These live in whichever test module already renders a card through
`vision-job-status`; reuse `_timed_job`/`_ago` from Step 2 — import them or
mirror them, following that file's own idiom.)

- [ ] **Step 3: Run them to verify they fail**

- [ ] **Step 4: The derivation, in the model that owns the timestamps**

In `modules/vision/models.py`, beside `is_stale` (which already reads
`timezone.now()` for the same kind of live answer):

```python
    @property
    def durations(self) -> dict[str, float | None]:
        """How long this generation waited, how long it ran, and how long it
        took altogether -- in SECONDS, `None` where the answer is genuinely
        unknown (owner requirement 2026-08-25, ADR 0012 D-EDIT-11).

        DERIVED from the three timestamps this row already carries: a stored
        duration would be a third copy that can disagree with them. A
        non-terminal job's numbers are LIVE (they count against `now`), which
        is what makes the card's existing poll show a moving clock without a
        new endpoint.

        `processing` is `None` -- never `0` -- for a job that never started:
        a submission the engine refused waited and then failed, and "it ran
        for no time" is a different claim from "it never ran".

        The wait is the ENGINE's queue plus this platform's, and the run
        INCLUDES loading the weights: `started_at` is stamped when ComfyUI
        reports the prompt executing, and no HTTP surface it serves
        separates the load from the first sampler step (D-EDIT-11). A job
        that finished between two polls was never observed running and
        back-fills its start from its creation (`services.refresh_job`), so
        it honestly reports no wait.
        """
        now = timezone.now()
        # ONE end for the whole row, computed once: the stamp when there is
        # one, `now` while the job can still change, and `None` for a
        # terminal job that somehow never stamped one -- a duration with no
        # end is not a duration. Recomputing it per key is how two of these
        # three numbers start disagreeing.
        end = self.finished_at or (None if self.is_terminal else now)
        started = self.started_at
        return {
            "queued": _seconds(self.created_at, started or end),
            "processing": None if started is None else _seconds(started, end),
            "total": _seconds(self.created_at, end),
        }

    @property
    def durations_display(self) -> dict[str, str]:
        """`durations`, rendered with the codebase's ONE duration
        formatter (`core.format.format_timecode`) -- which answers `""` for
        an unknown value, so a template shows nothing rather than a
        made-up `0:00`. Two properties, one truth: the numbers are the
        API's, the strings are the page's, and neither recomputes the
        other's arithmetic."""
        return {key: format_timecode(value) for key, value in self.durations.items()}
```

with a module-level helper beside the class:

```python
def _seconds(start, end) -> float | None:
    """Elapsed seconds between two timestamps, or `None` when either is
    missing -- a duration with an unknown end is not a duration."""
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds())
```

(`max(0.0, …)` because clock skew between a worker host and the DB must
degrade to zero, never to a negative duration on a card.)

- [ ] **Step 5: The two data surfaces**

`services.job_json`, beside the existing `created_at`/`finished_at` keys:

```python
        "started_at": job.started_at.isoformat() if job.started_at else None,
        # Seconds, derived (`GenerationJob.durations`) -- an agent reading
        # this must not have to subtract ISO strings, and a caller that
        # polls a RUNNING job gets a live number.
        "durations": job.durations,
```

`jobs.run_generate`'s returned dict, beside `output_urls`:

```python
        # What it cost, from the same single owner of that arithmetic.
        "durations": representation["durations"],
```

and extend that function's docstring's `Returns {...}` line to name the new
key (it is a documented contract, quoted in ADR 0012 and the README).

- [ ] **Step 6: The two page surfaces**

`_job_card.html` — one line inside the `job-details` column (the right half
of the `job-body` band), directly under the existing `job-meta` paragraph
that already names the engine and the submission time. Phrased per state so
it is never ambiguous. All four states, and
`{{ ... }}` values that are `""` simply render nothing:

```html
      <p class="job-meta muted">{{ job.engine }} · {{ job.created_at|date:"j M Y, H:i" }}</p>
      {% comment %}
      Durations (owner requirement 2026-08-25). Live for a job that has not
      finished: this whole fragment is what `job_status` re-renders on every
      poll tick, so the numbers move with no new endpoint and no JS of their
      own. A blank value renders as nothing (`format_timecode(None) == ""`).
      {% endcomment %}
      <p class="job-timing muted">
        {% if job.status == "queued" %}Waiting {{ job.durations_display.queued }}
        {% elif job.status == "running" %}Waited {{ job.durations_display.queued }} · running {{ job.durations_display.processing }}
        {% else %}Waited {{ job.durations_display.queued }}{% if job.durations_display.processing %} · ran {{ job.durations_display.processing }}{% endif %} · total {{ job.durations_display.total }}{% endif %}
      </p>
```

`gallery.html` — the finished-job caption gains `· ran {{ output.job.durations_display.processing }}` beside the engine it already names. Nothing else; the gallery lists finished work only.

Style `.job-timing` in `vision/base.html` beside `.job-meta` (this codebase
styles card classes there, never on a page).

- [ ] **Step 7: Run the tests, then the suites**

```
DATABASE_URL='…' … -m pytest modules/vision/tests -q
DATABASE_URL='…' … -m pytest console modules scripts -q
DATABASE_URL='…' … python manage.py makemigrations --check --dry-run
```
Expected: PASS, and NO migration.

- [ ] **Step 8: Docs and commit**

- `modules/vision/README.md`: the `job_json` bullet (~line 291) lists the two
  new keys; the queue-result paragraph (~line 361) lists `durations` beside
  `output_urls`/`timed_out`; and a short "How long it took" subsection says
  what the two halves mean — that the wait covers both queues, that the run
  INCLUDES loading the weights, and that no finer split is available from
  ComfyUI's HTTP surface.
- `docs/adr/0012-image-generation-engine-adapter.md`: the result-dict
  contract (~line 200) gains `durations`, and D-EDIT-11 is recorded with its
  "derive, never store; vision's clocks, not the queue's" reasoning.

```bash
git commit -m "$(cat <<'EOF'
feat(vision): every generation says how long it waited and how long it ran

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

- [ ] **Step 9: REVIEW GATE.** Reviewer must check: (a) no migration and no
  new column; (b) `started_at` is still written exactly once, and no code
  path was added that rewrites it; (c) `processing` is `None` and never `0`
  for a job that never ran; (d) the card renders in all four states and
  updates on the existing poll with no new endpoint; (e) nothing in
  `console/jobs/` was touched and no queue clock was duplicated.

---

## Live verification log

_(Evidence only — no completion language.)_

**The distilled 9B build — registration.** Registered as `ModelConnection` 3
(`config`: family `flux2`, variant `distilled`, text encoder
`Qwen3-8B-Q8_0.gguf`, VAE `flux2-vae.safetensors`) via the console's manual
connection form. Not bound as `vision.generate`'s primary — see Open
Question 1 below. Registering it surfaced a UX defect: recorded under Open
Question 3.

**The distilled 9B build — LIVE RUN #1 (InferenceJob 22 → GenerationJob
c4fd3bd0 → output 67).** ComfyUI ran 4 steps at ~32 s/step; the prompt
executed in 137.9 s; `system.ram_free` after completion was 24.0 GiB. The
QUEUE job itself was lost to the worker token race described below — the
generation completed and the output exists, but the queue row did not
report success cleanly until `refresh_job` recovered it. Root cause and fix:
`b3801b9` (see "Worker incident" below).

**Worker incident and hotfix.** Investigating LIVE RUN #1's lost queue job
found `console/jobs/worker.py::_execute`'s `finally` clause popped
`_active_tokens[job_id]` unconditionally, so a same-job re-claim (triggered
here by whole-process heartbeat starvation during its cold load — the
120 s staleness sweep is tighter than a multi-minute first load) could have
its live token deleted by a superseded attempt's cleanup, orphaning the
generation from the queue's point of view even though it kept running and
succeeded. Fixed by making that pop token-conditional, matching every other
token-conditional writeback already in the module (commit `b3801b9`, one
regression test, `modules console scripts` 2505/1). ADR 0013 carries a dated
amendment recording the incident and the rule. Peer handoff (a dedicated
heartbeat thread and/or a longer staleness threshold for cold loads) is
written up at
`.superpowers/owner-requirements/peer-handoff-worker-token-race.md` for the
`console/jobs` maintainer — not built on this branch, out of a hotfix's
scope.

**The distilled 9B build — LIVE RUN #2, post-hotfix (InferenceJob 23 →
GenerationJob c2cad7e6 → output 68).** Queue path proven end-to-end after
`b3801b9`: the queue job succeeded on attempt 0, total wall-clock 2:14
(processing 2:13).

**The edit-only family — registration only, no run.** Registered as
`ModelConnection` 4 (`config`: family `qwen_image`, encoder
`qwen_2.5_vl_7b_fp8_scaled.safetensors`, VAE `qwen_image_vae`). NOT
live-verified. Its fp8 text encoder upcasts on MPS to roughly 19 GB
resident; added to the ~15 GB model, the combined footprint (~34 GB) is the
same shape that produced the earlier unified-memory OOM crash on this
machine. Verification is deferred until a GGUF vision-language encoder is
downloaded — the GGUF custom-node pack already supports the `qwen2vl`
architecture, so the blocker is the file, not the loader — which is an
owner download decision, out of this plan's scope. The 4-step and 8-step
speed-adapter LoRAs are installed and offered in the edit form's LoRA field but
have not been run against this connection.

**Measured footprints.** `ComfyUIEngine.loaded_footprint` recorded the ordinary
build (connection 2) at 26,370,441,216 bytes (24.6 GiB) — plausible, the
model was not resident at submit. The distilled build (connection 3) recorded at
2,537,799,680 bytes (2.4 GiB) — this number is KNOWN TO BE
UNDER-MEASURED: it was already resident in ComfyUI at submit time, so
the delta-based measurement strategy only saw the run's incremental growth,
not its real resident size. See Open Question 4 below for the backlog
fix. The edit-only family (connection 4) has no measurement — never run.

**The edit-only family — LIVE RUN #1 (InferenceJob 32 → GenerationJob output 74).**
Connection registered with GGUF text encoder `Qwen2.5-VL-7B-Instruct-Q8_0.gguf`
(8.10 GB) plus mmproj `Qwen2.5-VL-7B-Instruct-mmproj-F16.gguf` (1.35 GB). Plain
run: 20 steps at ~43 s/step; processing 14:53. Resident ≈ 14.4 GB of the
edit-only family's weights (mps) + 8.9 GB text encoder (cpu) + VAE ≈ 22 GB;
no memory pressure.

**The edit-only family — LIVE RUN #2 with speed adapter (InferenceJob 33 → GenerationJob
output 75).** LoRA `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors`;
4 steps at ~21.5 s/step, total 1:47. Form opened at Steps 4 and Guidance 1 (the
distilled defaults seam, applied post-LIVE RUN #1), operator manually set
Guidance 1 to match the distilled weight's own tuning. No abnormal memory
pressure.

## Open questions for the owner

1. **The distilled model's own connection replaces nothing.** Both `flux2`
   connections (distilled and not) stay registered, and the role binding
   still points wherever the owner left it. Should the distilled one become
   the bound default for `vision.generate` once it is verified, with the
   undistilled one reachable through the picker as a finishing pass?
   **Recommendation:** yes — bind the distilled 9B build as the default. Its
   governed run times (137.9 s and 2:13 processing, both under the ordinary
   build's 20-step run's ~35 minutes of sampling alone) make it the better fit
   for iteration, with the ordinary build reachable through the picker as a
   finishing pass, matching the ADVISED ordering already recorded in the
   ledger (the distilled build first, the edit-only family plus its speed
   adapter second, the ordinary build as finishing pass).
2. **Adapters are engine-wide.** `list_assets("lora")` reports every file in
   the engine's `loras/` directory to every operation, including the
   checkpoint modes — so an edit-only-family adapter appears in a baseline job's LoRA
   list. The platform reports what the engine has and never guesses
   compatibility; a mismatched pick fails honestly at the engine. Acceptable,
   or worth an honest note near the field?
3. **Registration UX defect: the family/companion fields are hidden unless
   the console page is scoped to the ComfyUI endpoint.** Registering the
   distilled build's connection surfaced this: the "Add to registered" banner
   tells the operator to "use the manual form below," but that form's
   family/variant/text-encoder/VAE fields only render when the console page's
   query string already names the ComfyUI endpoint (`?endpoint=…:8188`) —
   because
   `console/inference/views.py` (~line 1132) queries family options at the
   PAGE's endpoint, not the row's or the engine's own. An operator who
   follows the banner's own instruction from the plain `/inference/` page
   gets a form with no way to declare a family. Fix candidate (not
   implemented here, backlog): query family options at the family engine's
   own endpoint — the connection being added, or the row being edited —
   instead of the page's.
4. **The distilled build's measured footprint is a known under-measurement.**
   Because it was already resident at submit time for both governed runs, its
   recorded `loaded_footprint` (2.4 GiB) reflects only the run's
   incremental growth, not its real resident size (~19.5 GB estimated at
   acquisition). Any future eviction/budget planning against this number
   will under-budget it. Backlog fix candidate: the delta-measurement
   strategy should only record a footprint when the model was confirmed NOT
   resident at submit, and should never lower an existing plausible value
   for the same model.
5. **The distilled encoder's unused top layers stay loaded.** Out of scope
   for this plan (declared at the top of this document); recorded here as a
   backlog item, not a defect — no evidence was gathered that it costs
   anything at this hardware's headroom.
6. **The generation wait timeout is not operator-tunable.** Already promoted
   to `modules/vision/README.md`'s "Known limits" during LIVE RUN #3 of the
   edit-capability plan; repeated here because Task 15's own live runs (the
   distilled build's 16-minute cold load in particular) are further evidence the fixed 600 s
   budget is tight for a first load on this hardware.

## Plan review

- Round 1 (2026-08-25): AMEND — 7 findings (A1 duplicate edit-form work vs. final-review fix round; A2 T16 tests unrunnable under auto_now_add; A3 card test racy/wrong-reason; A4 T14 falsifies two template docstrings; A5 variants(family) parameter unused; A6 live_defaults vs schema floor undocumented; A7 durations `end` reuse + citations). Graph claims verified against ComfyUI's bundled distilled/edit-only templates; T16 timestamp ground truth verified. Author applied all seven.
- Round 2 (2026-08-25): CLEAN — scoped re-check confirmed every edit landed with no stragglers (variants() parameterless everywhere; T13 extends _declared_config by one key only). No orchestrator rulings needed. FINAL.
