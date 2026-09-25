# tools/vision/ — Image generation

**Type a prompt at `/vision/`, get an image, generated fully offline by an engine the
operator installed and a checkpoint the operator placed.**

This is farabunker's second feature module (after `tools/rag/`) and the first to
validate the model-management framework with a non-RAG role — see
[ADR 0012](../../docs/adr/0012-image-generation-engine-adapter.md) for the decisions and
[the design spec](../../docs/superpowers/specs/2026-08-22-image-generation-design.md) for
the full shape this was built from.

## What it does

The operator resolves the `vision.generate` role at `/inference/` (register an engine
connection, bind it to "Image generation") exactly the way `rag.answer`/`rag.embed` are
resolved. `/vision/` then renders a form from the current operation's parameter schema,
submits it, polls the resulting job until it finishes, and shows the result — with a
gallery of everything generated so far and "reuse settings" to resubmit an earlier job's
parameters. The module holds no opinion about *which* checkpoint is bound or *which*
engine serves it; both come from the resolved role, exactly like RAG's chat/embeddings
roles.

## Role, capability, and the feature flag

> **"vision" names two unrelated things in this codebase.** The pre-existing
> `"vision"` *capability* (`models/contracts/roles.py::CAPABILITIES`) means a model
> that can **read** images — ordinary vision-capable multimodal chat, not a
> text-only model. This app, its Django
> label, and its `FARABUNKER_FEATURES` flag are also called `vision`, and it
> **generates** images: its operations register under the separate
> `"image-generation"` capability, and its role is `vision.generate`. Nothing is
> wrong — capability routing is correct everywhere — but a grep for "vision"
> returns both, so check which one you are looking at before changing anything.
> Renaming either is a deliberate call nobody has made.

`tools/vision/apps.py::VisionConfig.ready()` registers everything this module serves,
but only while `"vision"` is present in `settings.FARABUNKER_FEATURES`:

```python
register_role(RoleSpec(VISION_GENERATE_ROLE, "Image generation", "image-generation"))
register_operation(TXT2IMG)
register_operation(IMG2IMG)
register_operation(INPAINT)
register_operation(UPSCALE)
register_operation(EDIT)
```

`VISION_GENERATE_ROLE = "vision.generate"` and the `"image-generation"` capability live in
`models/contracts/roles.py`; each operation is *defined* in `models/contracts/operations.py`
(pure, dependency-free, like every core module) but only *registered* — and therefore only
ever returned by `all_operations()` — while this app's feature is enabled. With the flag unset
to empty (`FARABUNKER_FEATURES=`), none of this runs: no role in the console's "Getting
models" checklist, no operation to build a form from, and `config/urls.py` doesn't mount
`/vision/` at all.

The same flag reaches every template as `farabunker_features`, a frozenset, through
`tools/vision/context_processors.py::features` (registered in
`config/settings.py`'s `TEMPLATES[0]["OPTIONS"]["context_processors"]`). This is how the
shared shell (`foundation/templates/_shell.html`) decides -- together with UI-1's availability rule, which also requires `vision.generate` to have a model bound -- whether to render its **Images** nav
link — with the feature off there is no `/vision/` route, so the link must not exist
either; a context processor is the one mechanism that reaches every rendered page,
including `/rag/` and `/inference/`, without either of those importing this module.

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

## The operation registry, and how to add an operation

An `Operation` (`models/contracts/operations.py`) is a generation mode plus its parameter
schema — a tuple of `Param`s, each declaring its kind (`text`/`int`/`float`/`choice`/
`seed`/`file`/`asset`), default, bounds, and whether it's required. `TXT2IMG` ("Text to
image"), `IMG2IMG` ("Image to image"), `INPAINT` ("Inpaint"), `UPSCALE` ("Upscale"), and
`EDIT` ("Edit an image") ship today; the mode chooser on `/vision/` appears by itself
because a second operation is
registered, and `/vision/?operation=img2img` (or `?operation=inpaint`,
`?operation=upscale`) serves its form — `/vision/op/<key>/` still works as an alias, and
none needed a template edit. Adding another mode (ControlNet — spec §9)
is:

1. Define a new `Operation` in `models/contracts/operations.py` (or wherever the schema
   should live — it's pure data, no Django).
2. Give the engine adapter(s) that can run it a graph template
   (`models/contracts/engines/comfyui_workflows/` for ComfyUI) -- adding its entry to
   `_TEMPLATES` in `comfyui_workflows/__init__.py` is what makes `ComfyUIEngine` advertise
   it: `supported_operations()` derives from `template_keys()`, so there is no separate
   list to keep in sync.

   A template that takes a file input receives the engine-side reference as its fourth
   argument (`inputs["init_image"]`), never a filesystem path: `ImageGenerator.submit` has
   already transferred the file — for ComfyUI, `POST /upload/image` into a subfolder named
   after the job. The platform keeps the authoritative copy in
   `<GENERATED_DIR>/<job>/inputs/`, so an engine that was restarted and lost its copy
   simply reports the job `lost` and a resubmit re-transfers it.

   A template is a composition, not a graph written out by hand:
   `models/contracts/engines/comfyui_workflows/_fragments.py` owns the nodes every
   mode shares (checkpoint, the two prompt encodes, the sampler, decode-and-save)
   and allocates node ids as it goes, so a template is the handful of lines that
   are actually specific to its mode and no template hand-numbers a node.
3. Register it from `VisionConfig.ready()` alongside `TXT2IMG`, gated the same way.
4. Nothing under `tools/vision/` changes. `forms.build_form` renders any `Param` kind it
   knows a field for; the job card and the gallery caption render their facts from the
   schema (`GenerationJob.facts`); the page's Operation select (`services.operation_states`)
   is driven by `operations_for("image-generation")` NARROWED to what the selected model's
   engine reports it can run (Task 10) — which narrows only which options are ENABLED,
   never which exist (ADR 0012 D-EDIT-13) — with nothing selected, "no opinion" answers the
   full registry, and a mode appears there once a SECOND operation is registered.
   `/vision/?operation=<key>` serves that operation's form (`op/<key>/` still works as an
   alias) and the create form posts the same key back so `generate` validates against the
   right schema.
5. A `"file"` param needs nothing else from this module. `validate_params` reduces the
   upload to its basename so `GenerationJob.params` (a JSONField) stays JSON-safe;
   `submit_job` writes the bytes into `<GENERATED_DIR>/<job>/inputs/`, records a `JobInput`
   row, and puts the path in `GenerationRequest.inputs`; the engine adapter moves it from
   there (see `ImageGenerator.submit`). The create form already posts
   `enctype="multipart/form-data"`, and `?reuse=` never prefills a file field — a browser
   cannot re-send a file from a name, so the operator picks it again.
6. An `"asset"` param (a LoRA, a VAE, an upscaler) needs nothing else either.
   `validate_params` cleans it to a JSON-safe list (or one string) of the engine's own
   opaque ids; `forms.build_form` renders a multi-select (or a select) filled from
   `views.live_options`, which reads `InferenceEngine.list_assets` for the param's
   `asset_kind`; an engine reporting none says so in the field's help text rather than
   offering an empty dropdown. The adapter memoizes `/object_info` for a few seconds so
   a page with several such params costs one request per loader node, not one per param.

The params three modes share are declared once and spliced in: `PROMPT_PARAMS`
(prompt, negative prompt), `SAMPLING_PARAMS` (steps, CFG, seed, sampler,
scheduler, batch size), and `LORA_PARAMS` (LoRAs, strength). They are two
tuples rather than one because the modes differ in the middle — txt2img's
width/height, img2img's init image, inpaint's mask — and declaration order is
form field order.

- `description` — one or two sentences saying what the parameter *means*, for a
  reader who cannot see the code: what a value does at each end of its range,
  where the options come from. Required in practice for every shipped param
  (`models/contracts/tests/test_operations.py::TestParamDescriptions` fails a param
  that has none) and
  serialized by `operations.describe()` for schema consumers. The form layer
  (`forms._field_for`) renders it as the field's `help_text` -- but only when
  the field carries no SITUATIONAL copy of its own (a blank seed, an asset the
  engine has none of), which must win over the static schema prose when both
  exist.

`models.contracts.operations.describe(operation)` returns the schema as JSON-safe
data — `{"key", "label", "description", "capability", "output_media", "params"}`,
one dict per param carrying every `Param` field. It is pure: it knows nothing
about a bound engine, so a `"choice"` param whose options the engine owns comes
back with `"choices": []`. `services.operation_catalog()` is the version with a
live engine's options filled in.

Not every mode uses every seam, and that is the point. Upscaling declares no prompt, no
sampler, and no seed, and its template loads no checkpoint at all — the role binding
still decides which ENGINE runs it, which is the half an upscale needs. `GenerationJob.seed`
is nullable for exactly this reason: recording a random number a deterministic mode never
used would be a fact that is not true, so the card simply shows no seed.

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
would be a lie about half the models that can run this mode. LoRAs ARE
declared (`*LORA_PARAMS`, spliced after `seed`) — see "LoRAs" below for
why that is honest for `edit` too.

`comfyui_workflows/flux2_edit.py` is the first family template, registered
under `("flux2", "edit")`. It is derived node-for-node from this family's
ordinary "Image Edit" workflow, which ComfyUI 0.33.0 ships in its own
`comfyui_workflow_templates_json` bundle, minus that workflow's
`ComfySwitchNode`/`PrimitiveInt`/`PrimitiveBoolean` TOGGLE cluster (a UI
convenience for a graph builder, not a pipeline fact) — its
`LoraLoaderModelOnly` stays. It loads its three declared files
(`_fragments.components`, reading `config["family"]`/`["text_encoder"]`/
`["vae"]`), applies any selected LoRAs straight after the loader, encodes
the instruction once, and chains one `ReferenceLatent` per input image —
the edited image first, so it is always Reference Image 1.

`comfyui_workflows/qwen_edit.py` is the second family template, registered
under `("qwen_image", "edit")`. It is derived node-for-node from this
family's own "Image Edit" workflow, from the same ComfyUI bundle,
again minus its own TOGGLE cluster around its own `LoraLoaderModelOnly`
(kept, same reason), and it shares almost no wiring with `flux2_edit.py`:
the loaded model passes through
`ModelSamplingAuraFlow` then `CFGNorm`, then any selected LoRAs, before it
reaches the sampler, one
node (`TextEncodeQwenImageEditPlus`) encodes both the prompt AND the input
images together, called twice — once with the instruction, once with an
empty string — because this family's plain `KSampler` takes a positive
*and* a negative, and both conditionings pass through
`FluxKontextMultiReferenceLatentMethod` before sampling. Sampling reads
directly from the edited image's own `VAEEncode` latent, with no batching
node between them. Reference numbering follows the bundled workflow's own
wiring: the image being edited is scaled by `FluxKontextImageScale` and is
`image1` on the encoder node; an optional second reference is wired in at
its own size as `image2` — attachment order. The other family agrees: the `flux2`
family's own bundled distilled two-image subgraph also chains the edited image first, on
BOTH its conditioning branches (link ids 201-216 of
`image_flux2_klein_image_edit_9b_distilled.json`) — both families number references the
same way, verified rather than assumed.

### A distilled variant of a family (ADR 0012 D-EDIT-6)

Some weights inside a family are guidance-distilled and need an entirely different
graph — no `FluxGuidance`, a `CFGGuider` whose negative is a zeroed-out copy of the same
instruction (`ConditioningZeroOut`, wired BEFORE the reference chain so both branches
still see every reference image), and a handful of steps instead of many. ComfyUI reports
nothing over HTTP that distinguishes a distilled build from an ordinary one — verified
against `UnetLoaderGGUF`/`CLIPLoaderGGUF`: same filename list, same `type` — so which
graph to build is an operator DECLARATION, `ModelConnection.config["variant"]`, exactly
like `family` is.

`comfyui_workflows.variants()` is the whole vocabulary — not one list per family, because
the registration form that offers it has not chosen a family yet — and today it answers
`("distilled",)`, the one graph `flux2_edit.py` implements alongside its ordinary one.
`ComfyUIEngine.list_variants()` republishes it unchanged: unlike `list_families`, nothing
here is intersected with an engine vocabulary, because ComfyUI has no word for this at
all. An undeclared, blank, or unrecognized variant simply builds the family's ordinary
graph — the behaviour every connection registered before variants existed already has,
the same degrade-quietly rule `family` itself follows.

### Per-model defaults (ADR 0012 D-EDIT-7)

A distilled build's graph doesn't just change shape: it wants a different STARTING point
for `guidance` and `steps` (cfg `1`, `4` steps, vs. the schema's `4.0`/`20`). `edit` keeps
both params for every family and variant — hiding one for a distilled model would still
let a caller that posts nothing get the SCHEMA's own default from `validate_params`, which
knows nothing about a model, so hiding would be less honest, not more. Instead
`ComfyUIEngine.param_defaults(operation_key, config)` reports the connection's own opening
values, and `services.live_defaults(operation, resolved)` — the exact twin of
`live_options` above it in `services.py` — merges them into `operation_catalog`'s
`param["default"]` and into the create page's unbound form (`CreatePageView`'s own
`initial=`, under anything a reused job supplies).

**The one real edge, stated plainly rather than papered over:** the catalog's `default`
is where a FORM opens; a caller that omits the param still gets the SCHEMA's own default.
`GET /vision/operations/?connection=<pk>` can report `"steps": 4` for a distilled
connection while a scripted `POST` that sends no `steps` at all records `20` — both
honest (the schema's own value is what `validate_params` fills in for a missing param,
unchanged by this seam), but surprising if a tool author assumes the catalog's default is
also what an omission means. **A tool driving this operation must SEND what it read from
the catalog** — never assume omitting a param reproduces the catalog's own opening value.

The chooser is not the registry. `services.operations_for_model(resolved)`
narrows the registered modes to the ones the SELECTED model's engine has a
graph for, so switching models changes which modes the page offers — the
create page threads the selection through itself (`views.CreatePageView`,
`generate`, `input_targets`, `_create_page_response`) and
`GET /vision/operations/?connection=<pk>` narrows the same way. An adapter
that cannot answer the question narrows nothing: "no opinion" and "supports
nothing" are different facts, and only the second empties the list — and
only the second shows the page's own honest banner (below).

`img2img` and `edit` never appear together in what a SELECTED model offers
(a checkpoint family offers one, an edit family the other, never both), so
at most one of them is ever an enabled option — but both are always
LISTED, each under its own schema label, the unavailable one disabled
with its reason (ADR 0012 D-EDIT-13).

### Models × operations

What `comfyui_workflows._TEMPLATES` (and therefore `supported_operations`)
actually offers, by connection family — the source of truth is the
registry itself; this is a reading of it, current as of R1:

| Capability                                     | Config key                       | Operations                                     |
| ----------------------------------------------- | --------------------------------- | ----------------------------------------------- |
| generic checkpoint (no family declared)         | *(none)*                          | `txt2img`, `img2img`, `inpaint`, `upscale`      |
| edit-and-text-to-image family, ordinary build   | `flux2`                           | `edit`, `txt2img`                               |
| edit-and-text-to-image family, distilled build  | `flux2` (`variant="distilled"`)   | `edit`, `txt2img` — same two, distilled graph   |
| edit-only family                                | `qwen_image`                      | `edit`                                          |

`flux2` `txt2img` (R1) is `comfyui_workflows/flux2_txt2img.py`, registered
under `("flux2", "txt2img")`. Its ORDINARY graph is derived node-for-node
from this family's own ordinary Text to Image workflow, in
`image_flux2_text_to_image.json`.
Its DISTILLED graph is derived node-for-node from the distilled Text to
Image subgraph in the separate
`image_flux2_klein_text_to_image.json` bundle — a real second subgraph in
that file, sibling to that file's own ordinary Text to Image subgraph.
That sibling (`CFGGuider` at `cfg=5` over an empty-text
`CLIPTextEncode` negative, 20 steps) is NOT what the ordinary graph above
is drawn from, and this module does not build its shape at all — the same
node-for-node derivation `flux2_edit`'s own distilled graph separately
follows from its own family's bundle
(`FluxGuidance`+`BasicGuider` ordinary, `ConditioningZeroOut`+`CFGGuider`
distilled — see "A distilled variant of a family" above). Because both
templates build the exact same guider/negative wiring for the exact same
guidance-distilled BUILD of the family, the wiring itself lives once, in
`_fragments.py` (`flux_guidance`/`basic_guider`/`zeroed_conditioning`/
`cfg_guider`/`flux2_sigmas`/`flux2_sample`), and both templates call it —
not two independent derivations of the same graph. What is genuinely
different from `edit`: `width`/`height`/`batch_size`/`sampler` are this
operation's OWN params (`edit` reads size off the picture being edited,
pins batch at 1, and pins the sampler at `"euler"` because `EDIT` declares
none of the three).

**Two `TXT2IMG` params `flux2` cannot honour**, named by
`flux2_txt2img.IGNORES` and readable through
`comfyui_workflows.ignored_params("txt2img", "flux2")`:

- `negative_prompt` — neither `flux2` graph has a negative-prompt input; the
  distilled graph's own "negative" is a fixed zeroed copy of the positive
  prompt, not something an operator writes.
- `scheduler` — `Flux2Scheduler` has no scheduler input at all, same as
  `edit` (ADR 0012 D-EDIT-1).

`build` never reads either key. `ignored_params` is a pure lookup, like
`variant_defaults`: an unregistered pairing, or one with nothing to
ignore, answers `{}`, never raises. The engine republishes it through the
optional `InferenceEngine.ignored_params(operation_key, config)` member,
and `services.live_ignored` is what carries it to the page — which
renders those two fields DISABLED, each showing this sentence, rather
than accepting a value and silently dropping it (ADR 0012 D-EDIT-13).

## LoRAs

LoRAs are not a mode. They are two params — `loras` (an `"asset"` param of kind
`"lora"`, picking any number — rendered as a chip group, see "One constant form"
above) and `lora_strength` — declared once as
`models.contracts.operations.LORA_PARAMS` and spliced into every checkpoint-based
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

**`edit` gets the same two params too (ADR 0012 D-EDIT-9).** `LORA_PARAMS` is spliced
into `EDIT.params` after `seed`, and both bundled edit workflows genuinely carry a LoRA
loader — just a different node than the checkpoint modes use. `LoraLoaderModelOnly`
outputs MODEL alone (no `strength_clip` input at all — verified against a live ComfyUI
0.33.0), so `_fragments.model_lora_chain(graph, model, params)` is a second function
rather than a flag on `_lora_chain`: two nodes, two behaviours. Both edit templates call
it at the position their OWN bundled workflow puts the node — `flux2_edit.py` straight
after its loader (nothing else wraps the model there), `qwen_edit.py` after its own
`ModelSamplingAuraFlow` → `CFGNorm` pair, in both cases as the last model step before the
guider/sampler. No LoRA selected adds no node to either graph.

A LoRA can also be a *distillation* adapter — trained to replace the guidance setting
rather than add a style. Nothing here recognizes one as such: doing that from a filename
is the forbidden guess (D-EDIT-9's own reasoning). The operator picks the adapter and
sets `steps`/`guidance` themselves; `loras`' own description says so ("some adapters are
distillation adapters — set Steps to the count the adapter names and Guidance to 1"),
naming no file and no vendor.

`models.contracts.operations.validate_params(operation, raw)` is the schema *floor* every
caller shares — the Django form built by `build_form` layers the engine's *live* choice
lists (samplers, schedulers, from `InferenceEngine.list_choices`) on top of it, but a
future caller that never builds a form (the chatbot tool below) still gets full validation
by calling `validate_params` directly, the same call `tools/vision/services.py::submit_job`
makes.

## The service layer — the seam a chatbot tool will call

`tools/vision/services.py` holds every generation behaviour; `views.py` holds none — it
only ever calls into `services`, so a future caller (a conversational agent's tool, a
management command, a test) gets byte-identical behaviour to the page:

- **`preflight() -> PreflightResult`** — resolves `vision.generate` and health-checks its
  engine. Three honest states, never a fourth: `"ready"`, `"unbound"` (nothing assigned),
  `"unreachable"` (assigned but the engine isn't answering) — mirroring
  `tools.rag.views._precheck_models`. The message names the bound engine and
  endpoint from the *resolved binding*, never a hardcoded product name, so a second engine
  adapter needs no new copy (ADR 0012's engine-agnostic-copy deviation).
  The actual health round trip is cached for 30 seconds, process-locally, keyed on the
  bound engine and endpoint (`tools/vision/probe_cache.py`) — `narrowed_generate_spec`
  runs this preflight on every chat turn that offers `vision.generate`, and without a
  cache that is one HTTP round trip to the generation server per turn, on the turn's
  critical path. A cached "unreachable" answer expires the same way a cached "ready"
  one does: neither outlives the 30-second window, so the tool never refuses honestly
  for longer than that window says it should. The cache drops on an operator's own
  per-generation model pick and at the start of actually submitting a generation, so
  neither reads a stale answer from an unrelated chat turn; short of those two events it
  relies on the TTL alone. This is a *separate* cache from the one the inference console
  keeps over its own engine probes — the two live in different columns by design (each
  column's own probe cache answers a different question and invalidates on different
  events), and combining them into one shared module is not a simplification worth
  making.
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
  `GET /vision/operations/` serves this under `"operations"` alongside a
  `"connection": {"id", "name"}` naming WHICH model the schema describes
  (final-review finding 3 -- the picked connection, or the role binding when
  nothing was picked, so a tool caller never has to guess) — the
  schema-discovery endpoint an agent caller or the UI reads before it submits,
  degrading exactly the way the catalog itself does (no bound engine, no 503 — a
  full schema with empty option lists). `?connection=<pk>` naming a pk that no
  longer resolves is refused outright (404, `{"error": str}`) rather than
  silently answering for a different model.
- **`submit_job(operation_key, raw_params, files=None) -> GenerationJob`** — preflight,
  then `validate_params`, then create the `GenerationJob` row, store any file inputs, build
  a `GenerationRequest`, and call the engine's `submit()`. An engine that refuses the
  submission (`GenerationRejected`) or fails outright does not raise back to the caller —
  the job row already exists, it's simply recorded `failed` with the engine's own words and
  a `failure_kind` naming which kind of failure it was.
  Raises `VisionUnavailable` when preflight isn't ready, and `ParamError` when the
  parameters don't fit the schema.
- **The binding travels with the job.** `submit_job` records `engine`, `model_id`,
  `endpoint`, `model_fingerprint`, and `model_config` on the row and then submits through
  *that* binding (`models.contracts.gateway.get_image_generator_for`), never through a second
  `resolve()`. `refresh_job` polls through the same recorded binding
  (`services._generator_for_job`), so rebinding `vision.generate` mid-flight never points a
  poll at another engine's queue — it would answer `lost` for a job that is running fine.
  Only `preflight()` reads the *current* binding, because that is the question it asks.
- **`refresh_job(job) -> GenerationJob`** — brings one job up to date with the engine:
  `queued`/`running` update in place; `done` fetches every output, copies it into the
  managed store, and records `GeneratedOutput` rows; `failed`/`lost` mark the job failed
  with an explaining message. Idempotent — a terminal job returns immediately with no
  engine call — and safe to call as often as a page polls. An unreachable engine during a
  poll leaves the job **untouched** and sets a transient `job.unreachable` attribute (never
  a DB column) for the caller to display; a network hiccup must never turn a running job
  into a failed one. **Safe under a concurrent caller on the same row** (2026-09-03 fix
  batch, item D: the "wrong number initially, refresh fixes it" defect; widened in
  review) — the web poll (`job_status`/`queue_job_status`) and the queued worker's own
  `wait_for` loop (`jobs.run_generate`) each call this on their OWN fetched
  `GenerationJob` object, and can be mid-flight on the same row at once. EVERY branch
  that writes the row (`running`, `failed`, `lost`, `done`) re-reads and locks it
  (`select_for_update()`) immediately before writing, and returns the row AS-IS the
  instant that fresh read is already terminal — so a stale caller, still holding an
  in-memory copy that looks `queued`/`running`, can never overwrite an already-committed
  `done`/`failed` row in EITHER direction: not by re-finalizing `done` a second time under
  a new `GeneratedOutput` id (the original defect — deleting a just-committed row and
  recreating it under a new auto-incrementing id, orphaning whatever had already rendered
  the first id, a card or a `?format=json` poll response, mid-flight), and not by stomping
  a committed `done` row with `running`/`failed` because THAT caller's own stale engine
  answer disagreed with what had already happened (a corruption the original fix missed —
  worse than the id-burning defect, since it flips a genuinely finished generation's
  status). Not covered, because nothing durable is at stake: the `queued` branch and the
  two engine-unreachable branches only ever set the two TRANSIENT attributes above, never
  a column. **Every caller must reassign the return value** (`job = services.refresh_job
  (job)`, the shape every real call site already uses) — reusing an un-reassigned
  reference for a second call still costs one extra engine poll per call (its own
  `is_terminal` check never sees the finish), never a duplicate or corrupted row, but it
  is not a substitute for the correct convention.
- **`job_json(job) -> dict`** — one job as plain data: the shape `?format=json`
  returns, the shape the queue result reports, and the shape a tool reads. Files
  are named by their serving URL, never by a path. `unreachable` is the transient
  attribute `refresh_job` sets (never a column) read with a default, so a row that
  was never polled honestly reports `False`. `started_at` (ISO, or `None` before
  the engine reports the prompt executing) and `durations` (`{"submitted",
  "queued", "processing", "total"}` in seconds, see "How long it took" below)
  are read straight off `GenerationJob.durations` — the same numbers the card
  renders, computed once.

**Failures name themselves.** A failed job carries `failure_kind` beside its
prose `error` — `engine_rejected` (the engine refused the submission),
`engine_failed` (it accepted and then failed, or the submission failed some other
way), `lost` (it no longer knows the job). The three failures that happen *before* a
row exists carry the same vocabulary on the exception instead:
`VisionUnavailable.failure_kind` is `role_unbound`, `connection_unavailable`, or
`engine_unreachable`, and a `ParamError` means `params_invalid` (named in
`FailureKind`'s own docstring). One vocabulary, two carriers — so "fix your
parameters" and "try again later" are distinguishable without reading English.
`connection_unavailable` (final-review finding 4) is what a payload's own
`connection` pk gets when it no longer resolves — distinct from
`role_unbound`, since resolving a named pk never consults the role at all,
so the role may well be bound; `jobs.run_generate`'s claim-time re-check
raises this one specifically rather than folding it into the role's own
"nothing assigned" message.
- **`wait_for(job, timeout, interval=1.0) -> GenerationJob`** — loops `refresh_job` until
  terminal or timeout, for a synchronous caller (tests, a management command, the future
  chatbot tool). The page itself never calls this — it polls from the browser instead, so
  no request thread sits blocked on a generation.
- **`delete_job(job)`** — deletes the DB rows (cascading to `JobInput`/`GeneratedOutput`)
  and the job's whole directory (`store.remove_job_files`).
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

### Picking a model for one generation

`vision.generate`'s role binding is the DEFAULT model, not the only one. A
caller may hand `submit_job` a `resolved=` binding it picked itself, and the
queue payload carries that choice as `"connection": "<ModelConnection pk>"` —
byte-identical to the field `rag.ask` already carries, resolved through the
same `models.registry.bindings.resolve_connection_named`. There is exactly
one override mechanism, and this is it.

No new column records the pick: the `GenerationJob` row already stores
`engine`, `model_id`, `endpoint`, `model_fingerprint`, and `model_config` —
the full identity of the model that ran, which is what D6 promises and what
polling a job after a rebind already depends on.

### How long it took

Every job reports four numbers, DERIVED (never stored) from the
`created_at`/`started_at`/`finished_at` columns it already carries and, when
knowable, the `InferenceJob` queue row that submitted it (owner requirement
2026-08-25, [ADR 0012](../../docs/adr/0012-image-generation-engine-adapter.md)
D-EDIT-11, amended by the 2026-08-25 fix round):
`GenerationJob.durations` (seconds) and `.durations_display` (rendered with
`foundation.format.format_timecode`, the codebase's one duration renderer).

- **`submitted`** — the PLATFORM's own queue wait, enqueue to claim
  (`InferenceJob.created_at` to this row's own `created_at`). A FIXED,
  historical number the instant a `GenerationJob` row exists, never live —
  both ends are already stamped by then. `None` when it cannot be known: a
  job submitted directly with no queue job at all (a management command, a
  test, a future tool calling `services.submit_job` itself), a queue row
  that has aged out of the queue's own retention limit, or the queue being
  unreachable right now. Read ONLY through the sanctioned
  `models.contracts.queue.get_job` seam (`GenerationJob._submitted_at`) — never
  a `models.queue` import, and never a duplicate of the queue's own clock
  (`models/queue/models.py::InferenceJob` times the queue slot; this times
  the generation itself).
- **`queued`** — the ENGINE's OWN wait, and ONLY that: `created_at` on this
  row is stamped inside `services.submit_job`, which the worker calls only
  AFTER it has already claimed the platform's queue job, so the platform
  wait never lands inside it. (Corrected 2026-08-25 — an earlier version of
  this doc claimed `queued` covered both queues; it never did.)
- **`processing`** — the run, which INCLUDES loading the weights:
  `started_at` is stamped the moment ComfyUI reports the prompt executing,
  and no finer split (load vs. sampling) is available from its HTTP
  surface. `None`, never `0`, for a job that never started — a submission
  the engine refused waited and then failed, and "it ran for no time" is a
  different claim from "it never ran".
- **`total`** — end-to-end. Starts from `submitted` when it is known (the
  full, honest figure the owner asked for) and falls back to this row's own
  `created_at` when it is not.

A non-terminal job's `queued`/`processing`/`total` are LIVE (computed
against `now`), which is what makes the job card's clock move on every poll
with no new endpoint; `submitted` never moves once a row exists. These
numbers show on the job card (all four states, plus the pre-row queued
placeholder's own `In queue <mm:ss>` line — the only clock available before
a `GenerationJob` row exists at all), the gallery caption (`· ran
<processing>`, finished work only), and `job_json`'s `started_at`/`durations`
keys — read by `?format=json`, the queue result (`run_generate`'s own
`durations` key), and any future tool.

## Queued generation

**Every generation goes through the queue, including the page's own.**
`POST /vision/generate/` validates (form + `validate_params`), preflights, turns
each file into a reference, and calls
`models.contracts.queue.enqueue("vision.generate", payload)` — the same call an
agent makes. It never calls `submit_job` itself. That is what lets the
scheduler's memory admission see every generation there is: while the page
submitted directly, the scheduler could admit a language model into VRAM with a
generation already running, because it had never heard of it.

A payload is `{"operation": str, "params": dict, "inputs": dict}`. `params` are
the VALIDATED params minus every file key — so the seed is pinned at submit time
and a file is named once, in `inputs`, as `"output:<id>"` (a gallery result),
`"input:<id>"` (another job's input) or the `"input:<id>"` of an upload the page
staged for exactly this submission.

`tools/vision/jobs.py` enrolls `vision.generate` as a job kind against the execution
queue's registry (`models.contracts.jobkinds`, [ADR 0013](../../docs/adr/0013-inference-execution-queue.md)) — the same enrollment shape
`tools/rag/jobs.py` established for `rag.ask`, registered in `VisionConfig.ready()`
alongside the role and `TXT2IMG` above, inside the same feature gate. The planner resolves the current `vision.generate` binding to one `ModelRef`
(`footprint_bytes` left `None`, per `models/queue/scheduler.py`'s provenance contract);
the handler calls `services.submit_job` then `services.wait_for` and reports
`{"job_id", "status", "timed_out", "output_ids", "output_urls", "durations"}`.
`durations` is `job_json`'s own field (see "How long it took" above), read off
the same `GenerationJob.durations`, so a caller driving the queue sees the cost
of what it just ran without a second lookup. An engine-side
generation failure is a normal, honestly-reported outcome (`status: "failed"`),
not a raised exception. `timed_out` is `True` when the handler's wall-clock
budget elapsed with the generation still running — the queue job still succeeds,
because waiting is what timed out, not generating: the engine keeps working and
the job's own card polls it to completion. The queue page shows nothing about
results, so nothing there changes; the flag is for the caller reading the result.

`run_generate` stamps the queue row's own id (`ctx.job_id`, from the
`JobContext` every handler receives — ADR 0013 §8) onto the `GenerationJob` as
soon as `submit_job` returns, so a running generation can be found from the queue
job that submitted it: that is what `/vision/queue/<id>/` looks up. The context's
other seam, `ctx.report_progress`, is deliberately not wired — `wait_for` blocks
inside its own polling loop with no callback seam, and threading one through is a
separate change.

- **Priority 200.** Deliberately coarser and lower-urgency than `rag.ask`'s (queue-wide
  default), reflecting that a generation is a stand-alone, self-contained request an
  operator explicitly queued — not a response someone is waiting on synchronously the way
  an Ask answer is.
- **Exclusive, always — still, and deliberately.** The ComfyUI adapter DOES now report a
  measured footprint (`models/contracts/engines/comfyui.py`'s `loaded_footprint`: the memory
  that disappeared across its own run of a checkpoint, over-counting on purpose, `None`
  when the reading is not credible) and can be asked to release it (`unload`, ComfyUI's
  `/free`) — see ADR 0012's "Memory-governance seams". `plan_generate` nonetheless still
  declares `exclusive=True` unconditionally: whether a measured footprint should relax that
  is a scheduler decision, not an adapter one, and it belongs to the execution queue's own
  budget work. What the measurement buys today is real regardless — it is what `/inference/`
  shows as the connection's **Last measured** footprint, and it is what
  `models/queue/worker.py`'s eviction pass sums when deciding whether the machine is over
  its configured budget.
- **File parameters travel as references.** A payload is JSON, so it carries
  `{"inputs": {"init_image": "output:12"}}` — `output:<GeneratedOutput id>` for a
  gallery result or `input:<JobInput id>` for another job's input — and
  `services.stored_input` re-reads the platform's own copy of those bytes and hands
  `submit_job` the same upload-shaped object (`store.StoredFile`) a browser post
  produces. One submission path, no second store-and-record branch, and nothing but
  strings in the payload. A reference naming a param the operation does not declare
  is refused, not dropped.
- **Describes its own output, once generation is done** (vision-describes-
  its-own-output task). `plan_generate` also declares `rag.extract` (the
  same extraction role `tools/rag`'s own image-ingestion path resolves)
  ALONGSIDE `vision.generate`, TOLERANTLY — an unbound `rag.extract`
  simply leaves it out; the image job is planned exactly as before. When
  it resolves, `run_generate` asks the extraction role to describe the
  job's first output STRICTLY AFTER the image generation has fully
  finished — sequential, never overlapped, never started eagerly — and
  stores the result on `GenerationJob.description`: one extra model
  call, never fatal to the generation. An unbound role, a failed call,
  or a blank answer leaves the generation exactly as it already stands,
  plus a short, honest sentence on that field instead of the real
  description. Only for a `done` job with at least one output — never
  for a failed one.
  **The image model is deliberately NOT released before describing.**
  An earlier version of this step did release it (the engine's own
  `unload` seam) so the two models would never be resident at once — but
  ComfyUI's `/free` has no per-model form: `POST /free` with
  `unload_models` set frees EVERY model at that endpoint
  (`ComfyUIEngine.unload`'s own docstring), so releasing would make the
  NEXT generation at that endpoint pay a full cold load — "up to ~25
  minutes" on the reference hardware per this job kind's own
  `GENERATE_WAIT_TIMEOUT_SECONDS` comment — to avoid a few seconds of
  double residency. Removed for exactly that reason; do not add it back
  without a genuinely per-model free to release against. See "Tools"
  below for how a
  caller reads the field back.
  **`rag.extract`'s own `ModelRef` is `synchronous=False`** (fix round
  item 4) — deliberately, and for a narrower reason than "the other
  planner already does it": the flag only ever drops the BARRIER's
  pre-claim wait at that endpoint, never protection, sweeping or budget
  accounting (`models.contracts.jobkinds.ModelRef.synchronous`'s own
  docstring). Paying that wait unconditionally, on EVERY claimed job,
  for a describing call that only sometimes runs (never on a failed or
  output-less generation) would be a cost with no matching benefit on
  every job that never reaches it. The accepted trade: a possibly-slow
  FIRST describing call at a freshly-touched endpoint (never a
  correctness problem — releasing MOVES weights rather than freeing
  them, per this task's own memory diagnosis), bounded by
  `describe_output`'s own `request_timeout` rather than an unbounded
  stall, in exchange for never charging an unconditional wait for a
  conditional call. **`request_timeout` is a required parameter of the
  describing call, supplied by each caller honestly** (fix round item 3,
  then tightened by a second review) — never a bare constant living
  inside it. `services.DESCRIBE_REQUEST_TIMEOUT_SECONDS` (60s —
  explicitly well below the platform's own default agent/chat response
  timeout, 1800s) lives in `services.py`, not `jobs.py`, precisely
  because BOTH callers need it: the QUEUED job kind passes it straight
  through as its own `request_timeout` (no turn budget to derive one
  from), and the CHAT tool derives its own from what remains of the
  turn's own budget but CAPS that derivation at this same constant — a
  generation that finishes early in a turn leaves most of the response
  timeout still remaining, and handing all of it to a stalled describer
  would let a forty-word sentence hold a live chat turn for roughly half
  an hour, which a second review caught as a regression dressed as a
  fix. The turn's remaining budget is a ceiling on what is worth waiting
  for, never a target to spend: past the shared cap, the image already
  arrived and the sentence only aids judging it, so nothing is lost by
  giving up on it. **The actual
  model call is the shared gateway mechanism**
  (`models.contracts.gateway.describe_image`, fix round item 5) — the
  same "ask a vision-capable model about an image file" shape `tools/
  rag`'s own extraction path hand-built independently, now held in ONE
  place both columns may call. The PROMPT stays this column's own
  (`DESCRIBE_OUTPUT_PROMPT`) and is never shared with rag's retrieval
  caption prompt — a reviewer ruled those two purposes must not
  converge — the gateway function takes the prompt as a parameter and
  has no opinion on it. `tools/rag` does not call this seam yet;
  converging its own `_ask_vision` onto it is a named follow-up, not
  part of this task.

## Gallery select mode and bulk delete

The gallery's normal view offers the same per-figure two-step delete
(`_delete_control.html`) every job card carries. Select mode (owner ask,
2026-09-02) is a second GET state of the SAME page, not a second page,
so no JS is required anywhere in it:

- `?select=1` (composes with `page=`, e.g. `?select=1&page=2`) turns
  every figure's thumbnail into a checkbox-carrying label (clicking the
  image toggles the box rather than opening the file) and drops the
  per-figure actions row (`_output_actions.html`, `_delete_control.html`)
  — the whole grid moves inside ONE bulk form instead, since nested
  `<form>`s are invalid HTML. A "Done" link returns to the normal view;
  a "Select" link in the normal view enters select mode, both preserving
  the current page.
- `?select=all` is the identical page with every checkbox on it
  pre-checked. **This is per-page, not global** — it selects everything
  the current page is showing, not every generation across every page;
  paging away and back re-renders unchecked (`?select=1`'s own default)
  unless `?select=all` is followed again.
- The action bar renders as a row at the top of the form, directly
  under the Gallery header and above the grid (owner ask, 2026-09-02:
  not a bar stuck to the bottom of the viewport). It carries the same
  dialog-free two-step confirm every delete control on this page uses
  (a `<details>` disclosure, no `confirm()`/`alert()`), submitting the
  one form wrapping the grid to `POST /vision/jobs/delete-selected/`
  (`vision-jobs-delete-selected`, `views.jobs_delete_selected`). Its
  disclosure opens downward in normal document flow, pushing the grid
  down rather than overlaying it.

`jobs_delete_selected` dedupes `request.POST.getlist("jobs")` (a batch
job's several outputs render several checkboxes carrying the SAME job
id) and resolves what is left through `visible_jobs(principal)` — the
identical seam `job_delete` uses. It diverges from `job_delete`'s
per-row 404 on exactly one point: an id that is malformed, or that does
not resolve inside `visible_jobs(principal)`, is silently DROPPED from
the batch rather than refusing or 404ing the whole submit. A bulk
action addresses many rows behind one submit, so there is no single
"which one" a 404 could confirm or deny the existence of the way a
row-addressed URL can — the id just doesn't count toward the "Deleted N
generations." total. An empty selection is not an error either:
"Nothing selected.", same redirect, nothing touched.

Deleting a job through either route deletes the WHOLE `GenerationJob` —
a batch generation's sibling `GeneratedOutput` rows (and the job's
whole directory) go with it, exactly as selecting only one figure of a
multi-image batch already does via the per-figure control today. Bulk
delete changes no existing single-job deletion semantics; it only lets
many be chosen in one submit.

### The `?job=` filter (2026-09-03 fix batch, item B)

`GET /vision/gallery/?job=<uuid>` narrows the listing to one
`GenerationJob`'s own outputs — the contract a chat card's link uses to
jump here at the exact image a generation produced:
`{% url 'vision-gallery' %}?job=<uuid>#job-<uuid>`. Every gallery
`<figure>` carries a matching `id="job-{{ output.job.id }}"` as the
anchor half; a batch job's several outputs repeat that id across their
figures (acceptable — only the first is ever the anchor a browser
scrolls to).

The filter resolves through `visible_jobs(principal)`, the same IA-1
manager the rest of this page already reads, so a job belonging to
someone else is simply invisible, not a 404 that would confirm it
exists. A malformed uuid string and an unknown or foreign one land on
the exact same branch: the ordinary empty-gallery state, `200`, "Nothing
generated yet." — no distinguishing oracle either way, and no 500 for a
caller's typo. The filter composes with select mode (`?job=<uuid>
&select=1`) and the pager preserves it across `page=`, exactly as it
already preserves `select=1`.

## Visibility (Identity & Auth, Task 13)

See [`identity/README.md`](../../identity/README.md) for the column
`sees_all_content`/`is_admin` and the four seams below reach.

`tools/vision/visibility.py` is the ONE place this column reaches
`GenerationJob.objects` for a read (`services.py` is the other file —
its OWN two legitimate uses are the create, `submit_job`, and the
locked re-read `refresh_job`'s finalize step takes immediately before
writing the row (2026-09-03 fix batch, item D: `select_for_update()`,
never a query this column's own visibility rule should narrow, since
it is reading back the SAME row this same call is about to write, not
answering "what may this principal see"); `foundation/ops/tests/
test_column_boundaries.py` pins the file set, not the call count, as
closed at two). It gives two answers:

- `visible_jobs(principal)` — a `GenerationJob` queryset: `principal`'s
  own generations, or every generation for a principal that
  `sees_all_content`. A generated image is CONTENT, not an operational
  row, so this is `sees_all_content`, never `is_admin` — an
  administrator with the content setting off sees none of somebody
  else's pictures, and deleting one is not on the operator's administer
  list either.
- `may_read_job(principal, job)` — the same rule for one already-loaded
  row: `job_status`, `job_delete`, `output_file`, and `input_file`
  resolve through it (`output_file`/`input_file` through `output.job`/
  `job_input.job` — one owner per generation, never a per-output one)
  and answer 404, not 403, when it says no — a 403 on a row-addressed
  URL would confirm the row exists.

`is_admin` is read in exactly one place inside this module, inside
`may_read_job`: a row owned by `("service", "local")` — one a shell
path produced — is visible to an administrator regardless of the
content setting, the one carve-out `identity.access.owned_rows_q`
states in full at its canonical home (identity/README.md §5b).
`visible_jobs` calls it rather than restating it.

The create page's Recent list (`_recent_jobs`) and the Gallery
(`gallery`) both read `visible_jobs`, so the two listings can never
drift on who may see what. `queue_job_status` (the queued placeholder's
poll target) additionally reaches `models.queue.visibility` — the
rule-2 seam `models/README.md` names — for `may_see_job_id`/
`may_read_job_content`, since before a `GenerationJob` row exists at
all the only record of a submission is the execution queue's own row.

A referenced image (`output:<id>`/`input:<id>`, carried by the
"use this image" gallery link and by a queued job's own `inputs`) is
resolved through `services._visible_referenced_row`, never a bare
manager lookup — an invisible reference behaves exactly like a
nonexistent one, so a member cannot derive from another principal's
generated image or job input by naming its id in
`input_<param>=output:<id>` (or in a hand-crafted queue payload; the
worker re-resolves references at claim time against the payload's own
recorded actor, exactly as it re-resolves the picked model). Threaded
through `services.stored_input`/`stored_input_exists`/`resolve_inputs`,
each of which now takes the acting `principal` as a required argument.

**IA-1 LIMITATION: staged-upload preview is admin-only, and IA-2 does
not close it.** A STAGED `JobInput` (one recorded before the generation
that will consume it exists — `job_id is None`) carries no owner column:
`tools.vision.models.JobInput` has none, and adding one is a migration,
out of this task's scope. On a box with accounts, `input_file` and
`_visible_referenced_row` serve a staged upload's bytes to `is_admin`
only; a member gets the same 404 an invisible row gets everywhere else.
On an OPEN box `is_admin` is always True, so nothing changes there.
**This is a LATER follow-up, not IA-2's** — spec §17's IA-2 migration
table names five migrations (identity, rag, two in agents, one in the
model registry) and none of them touches `JobInput`, and §18.2's
done-when content list does not mention it either. Closing it
needs the same shape every other blank-owner gap in this codebase was
closed with: an `owner_kind`/`owner_key` column on `JobInput` (a
migration of its own), a sixth `OwnedRows` registration
(`identity.contracts.ownership.register_owned_rows`, alongside the five
this codebase already carries for `Agent`/`Flow`/`Conversation`/
`AskRecord`/`GenerationJob`), and `manage.py adopt_open_rows` extended to
claim the pre-phase blanks the same way it already does for those five.

## Storage layout

`store.py` is the single source of truth for where a job's files live, mirroring the
document store's shape ([ADR 0009](../../docs/adr/0009-document-store-and-categories.md)):

```
<GENERATED_DIR>/<job-uuid>/
  inputs/<param_key>-<original-basename>     # file params (none for txt2img today)
  <index>-<basename>                         # each fetched output, in order
```

A **staged** upload lives at `data/generated/uploads/<uuid>/<filename>` and is
recorded as a `JobInput` with no job — the page writes it there before it
enqueues, because a JSON queue payload cannot carry a file. When the queued job
runs, `submit_job` copies those bytes into the job's own directory exactly as it
does for a browser upload, so every job still owns its inputs and deleting a job
still deletes them. Staged rows and their directories are swept after
`VISION_STAGED_UPLOAD_TTL` (default 24 hours).

An operation may declare more than one file param — inpaint takes an image and a mask —
and `submit_job` stores each under its own `inputs/<param_key>-<basename>` name while
`ImageGenerator.submit` transfers all of them before the graph is built. Nothing about
two files differs from one at any layer.

`GENERATED_DIR` is `<FARABUNKER_DATA_DIR>/generated` (`config/settings.py`), host-mounted
outside the container lifecycle exactly like `DOCUMENTS_DIR`. Outputs are fetched from the
engine and **copied** into this store; the engine's own output folder is never relied on
afterwards — it may be on another machine, or wiped. Filenames coming back from an engine
are untrusted: only the basename is ever used to build a path, so a tampered or unusual
engine response can never write outside a job's own directory. Deleting a job deletes its
directory tree; there is no retention policy yet (spec §9).

Both halves of that directory are served the same way and only by primary key:
`GET /vision/outputs/<id>/file/` for a result, `GET /vision/inputs/<id>/file/` for the
image a job was given (`?download=1` on either forces an attachment). The job card
renders a job's inputs above its outputs, and `?format=json` reports them as URLs —
a filesystem path never leaves this module.

Every stored row's `media_type` is derived from the file's own extension
(`store.media_type_for_upload`) — never from the posting client's `Content-Type`
header, which is whatever the browser chose to send. Serving is equally
defensive on the way out: `_serve_stored_file` names only a closed allowlist of
raster types in the response `Content-Type`; anything else — an unrecognized
extension at store time, or a row written before this behaviour existed —
downloads (`application/octet-stream`) instead of rendering. A type outside the
allowlist is also always sent `Content-Disposition: attachment`, whatever
`?download=` says — `application/octet-stream` already makes every browser
this platform targets download rather than render, so the disposition header
is a second, independent lock on the same door rather than the only one. The
response also carries its own `X-Content-Type-Options: nosniff`, set on this
route directly rather than left to the site-wide middleware, since a stored
row's media type reaching a browser unsniffed is exactly what this route
exists to guarantee. This does not narrow what an upload field accepts; it
only changes what gets recorded and re-served, so a file with an extension
outside the allowlist still uploads, it is just never named as its own type by
this store.

The same response (B-8, round-3 hardening) also carries `Cache-Control: private,
no-store, max-age=0` with `Cookie` added to `Vary`, set by one call to
`foundation.http.mark_private` right beside the `nosniff` line above — the same
shared helper `tools/rag` calls for its own file and transcript routes. A
generated output and a stored input are entitlement-gated content exactly like
a document's original file; see `docs/OPERATIONS.md` §"Private content never
gets cached" for the finding this closes.

## Engine files (2026-09-02) — no phantom images

Everything above is farabunker's own **managed** copy. The image engine keeps
copies of its own, in host folders this module never manages and never reads
back from (`store.py`'s own module docstring: outputs are copied out of the
engine and never relied on afterwards). Gallery delete and the per-job delete
control only ever remove the managed copy under `<GENERATED_DIR>/<job-uuid>/`
— so without this page, the engine's own output/input folders accumulate
"phantom" files invisibly, forever, with no UI that ever sees or removes them.

**Reachability.** `compose.yaml` bind-mounts the engine's real output/input
directories into the `web` container, read-write, at the fixed in-container
paths `/engine/output` and `/engine/input`:

```yaml
- ${COMFYUI_OUTPUT_DIR:-./data/engine/output}:/engine/output
- ${COMFYUI_INPUT_DIR:-./data/engine/input}:/engine/input
```

`COMFYUI_OUTPUT_DIR`/`COMFYUI_INPUT_DIR` are operator-set in `.env`, pointed at
wherever the engine really keeps its folders (e.g. ComfyUI's own `output`/
`input` directories) — this repo ships no assumption about that path, since it
is public and every operator's install lives somewhere different; left unset,
the mount defaults to a harmless empty local folder and the page below simply
shows nothing rather than failing. `tools/vision/store.py::ENGINE_OUTPUT_DIR`/
`ENGINE_INPUT_DIR` are the module-level `Path`s this app reads inside the
container, defaulted to those same two fixed mount points and pinned there
again in `compose.yaml`'s own `environment:` block for `web` — deliberately a
**different pair of env var names** than the compose-side ones above, because
`env_file: .env` also hands every `.env` variable straight to the container's
own process, and reusing one name for both the host path and the in-container
path would let an operator's host-side value silently override the mount
target. `config/settings.py` is a no-touch zone for this change, which is why
these two live in `tools/vision/store.py` instead of beside `GENERATED_DIR`.

**The page.** `GET /vision/engine-files/` (`vision-engine-files`,
`tools/vision/maintenance.py`) lists both folders — files only, one level
deep (`os.scandir`, no recursion into any subdirectory the engine keeps) —
sorted newest first, each tagged **accounted** or **orphaned**.

Accounting is a **prefix match, not a join**: a file is accounted for when its
basename *starts with* a UUID this platform already knows — a `GenerationJob`
primary key in ANY status (a failed job can still have left engine-side bytes
behind) for the output folder, or that same set unioned with every staged
upload's own UUID (`JobInput` rows with no job yet — the directory name
`store.stage_input` writes) for the input folder. Two files that happen to
share a prefix are indistinguishable, and a filename the engine invented with
no UUID in it at all always reads as orphaned; this is documented as a
deliberate simplification, not a real relationship.

An unreachable directory (the bind mount is absent — a preview stack, most dev
boxes, or the folder was simply never created) renders an honest empty state
with a muted note, never a 500.

**Thumbnails are content.** Route class `S` (admin-only, `identity/routes.py`)
gets an administrator to the page and to the delete action; that alone does
**not** show anybody a pixel. An `<img>` preview only renders when
`identity.access.sees_all_content(principal)` is true — the same predicate
`models/queue`'s job cards withhold another user's payload behind — because an
engine-side file belongs to no row this platform's tables track ownership of,
but its bytes can still depict somebody's private generation. Without the
flag: name, size, mtime, and the accounted/orphaned tag only, never the bytes.
Filenames here are UUID-shaped (engine-produced or entirely engine-invented)
and therefore safe to render as plain text. The thumbnail-serving route itself
(`GET /vision/engine-files/thumb/<output|input>/<name>/`,
`vision-engine-file-thumbnail`) is *also* gated on `sees_all_content`, on top
of its own `S` class, because serving those bytes is the one place on this
page content actually leaves the server — resolved strictly by basename under
the two configured directories (no path-traversal surface, the same shape
`_serve_stored_file` gives a request that never supplies a filesystem path).

**Delete.** The same select-mode grammar the gallery's bulk delete uses
(`?select=1`/`?select=all`, checkbox tiles, a `<details>` two-step confirm,
zero JS) posts to `POST /vision/engine-files/delete/`
(`vision-engine-files-delete`). Each checkbox names `"<output|input>:<name>"`;
delete is a plain `Path.unlink(missing_ok=True)` of that basename resolved
under the matching configured directory — never a directory removal, and never
touching a farabunker row, because none of these files are tracked by one. An
unsafe name (a traversal attempt, an absolute path, an unrecognized folder
key) is silently dropped, the same shape the gallery's own bulk delete drops a
foreign job id in. `?select=orphans` is a cheap preselect link that checks
only the orphaned rows. One `identity.audit.record` line is written per
delete *request* (not per file), naming how many files actually went.

**The delete-hook.** `services.delete_job` — called by both the per-job
delete control and the gallery's bulk delete — now also best-effort-unlinks
any engine-side file whose name starts with that job's UUID, in both
directories, after the managed directory is removed. This is what stops NEW
phantoms from accumulating: from this point on, deleting a generation from
farabunker also asks the engine's own folders to let it go. The sweep never
raises — a missing mount, a permission error, or a race with another deleter
is logged and swallowed, because a failed best-effort cleanup must never fail
the job delete already in progress.

**Settings registration.** "Engine files" sits in the Settings area's Setup
group, admin-gated exactly like Models/Library, and additionally
flag-guarded on `"vision"` in `FARABUNKER_FEATURES` — with the feature off
there is no `/vision/` route at all (`config/urls.py`), so the sidebar must
not try to `reverse()` it either (`foundation/settings_area.py::Entry.
feature`, `foundation/templates/_settings.html`).

## The poll-driven page and its no-JS fallback

`CreatePageView` (`GET /vision/`) renders the create form plus recent jobs as cards
(`_job_card.html`). Each non-terminal card's inline script (`create.html`) polls
`GET /vision/jobs/<uuid>/` every couple of seconds and swaps the card fragment in place
until the job reaches a terminal state; a submission itself goes out as `fetch()` to
`POST /vision/generate/` and swaps in the returned card, or — for a 400 — renders just the
form's errors into a dedicated `#form-errors` slot, never into the jobs list, so a rejected
submission can never be mistaken for a new job.

None of this is required to use the page. With JavaScript off, the create form posts and
redirects normally, and a card that would otherwise be polling instead shows a plain
"refresh to update" note (`_job_card.html`) — reloading the page calls `job_status` the
same way the script would have, just manually. `?format=json` on the job-status route
returns the same facts as plain data (`_job_json`), for a future tool or a test that
shouldn't have to parse HTML.

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

**The Recent strip discovers jobs it did not submit itself** (T1, 2026-09-16). Every poll
described above only refreshes a card already on the page; nothing watched for a NEW job
appearing at all, so a generation queued from a chat turn, or from the same account in
another tab, never showed up on an already-open create page until it was reloaded by hand.
The page's script now also polls `GET /vision/jobs/strip/` — the exact same Recent region
(`_jobs_strip.html`, the one include both the page's own render and this endpoint use, so
the two can never disagree on which jobs count as recent) — on a fixed few-second cadence,
and compares a **fingerprint**, not the response body, to decide whether anything changed:
each answer carries an `X-Strip-Fingerprint` header (`views._strip_fingerprint`, a cheap hash
of every listed job's id/status/output-count), and the page's own render stamps the identical
value onto the region as a `data-strip-fp` attribute at load — including on the very first
poll tick. Comparing header to attribute instead of fetched text to rendered markup is
deliberate: the same include renders with different incidental whitespace depending on
whether it's included inline or fetched standalone, so a text compare would report a
"change" on every single tick regardless of whether a job actually had. Only a real
fingerprint change replaces the region's contents; every fresh non-terminal card that swap
brings in picks up the same live poll every other card gets, and any watcher still polling a
card the swap just detached notices on its own next tick and stops rather than running to its
ceiling. If this poll eventually gives up (the same transport-retry and duration ceilings as
every other poll on this page), a plain note says so — "Stopped
watching for new generations — reload to catch up." — rather than silently going quiet. The
Gallery is deliberately NOT wired into this: it has no live region to refresh into, and the
owner asked for the create page only.

**The page's URL.** `/vision/?operation=<key>&connection=<pk>` is the
canonical form: both choices live in the query string, never a session, so
a link is a complete description of what the page will show — and the
Operation select can produce it, which a path segment could not (it is a
control in a GET form). `/vision/op/<key>/` is kept as a rendering alias:
same view, same page, no redirect, so links issued before this change keep
working. A path segment wins over `?operation=` when both are present.

### The model picker and the operation select

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

**Carrying typed values across a Model/Mode switch.** Reloading the page
above used to throw away everything the operator had already typed — a
different problem from what `?reuse=<job-id>` solves (that names a
finished job whose params are read back from the database; it is not a
per-field mechanism). Before the picker form submits, its own inline
script mirrors every enabled, non-file value currently in the generate
form into a hidden field of the same name on the picker form, so the
reload's query string carries it; the create page reads a query key
matching a union param's own name back into the form's `initial`, at the
LOWEST precedence — a live default or a reused job's own value still wins
on the same key. A stored gallery hand-off (`input_<key>`, "Feeding an
image back in" below) survives a switch too, but through its own
server-rendered hidden field on the picker form, not this script, so it
works with JavaScript unavailable; the typed-value carry itself needs
JavaScript, since there is no query string to read from until the picker
form actually submits — without it, a switch clears the form exactly as
before. **Attached files are never carried this way** — a query string
cannot hold a file's bytes — so the operator re-attaches after switching,
same as today.

A submission naming a model that no longer resolves (deleted, or its
capability removed, since the page rendered) is refused rather than silently
run against a different model: a 503 fragment for the page's own `fetch()`
call, a whole re-rendered page (with the same message, as a non-field form
error) for a plain POST. A GET naming the same stale pk is treated more
gently — it falls back to the role binding in silence, because a stale link
or bookmark should leave a normal page behind, never an error.

Beside the Model select sits an **Operation select**, in the same little
GET form, listing EVERY registered mode. One this model's engine has no
graph for is a `disabled` option carrying its reason ("No inpaint graph
for the `flux2` family."), never an entry that quietly disappears. Because
a GET form can only produce a query parameter, `?operation=<key>` is the
canonical URL and `op/<key>/` is kept as a rendering alias.

**One constant form.** The page renders the UNION of every registered
operation's params — the same 20 fields, in the same order, whichever mode
is picked (`services.union_params` → `forms.build_constant_form`). A field
is `enabled`, `unused` (this mode does not declare it: "Not used by
Upscale."), or `ignored` (this model's graph cannot honour it, in the
engine's own words via `services.live_ignored`). Both disabled states
carry HTML `disabled`, so the browser never submits them, and
`required=False`, so nothing an operator was not allowed to answer can
block the form — but they render differently (2026-08-29): `unused`
renders its `.field` wrapper `hidden` (the mode does not want it, so it
is not shown, not merely explained), while `ignored` stays visible with
its reason inline (the mode DOES want it; the picked model's graph just
can't honour it). Both keep the identical label/widget/reason markup in
the DOM either way — the hidden branch is not a smaller render, only a
`hidden`-attributed one — so a screen reader or a stylesheet that
overrides `hidden` still finds the reason there.
`services.fill_engine_blanks` supplies an ignored
`"choice"` param's value before `validate_params` sees it. Narrowing the
form to the picked mode is what R2 removed: it read as lost function, and
a control that vanishes explains nothing. `img2img` and `edit` are two
operations again, under their own schema labels — the merged "Image to
image" entry is retired (ADR 0012 D-EDIT-13). A job's card headline is
unchanged: an `edit` job's heading is its instruction.

**Kind-aware layout.** Every field also carries its param's `kind` (and
whether it is `multiple`) as `field--kind-<kind>`/`field--multi` classes on
the `.field` wrapper (stamped by `forms._field_for`, the path `build_form`
and `build_constant_form` both call, so the two builders' fields agree).
`create.html`'s CSS gives a file input, and any field marked `multiple`,
the full grid row instead of one narrow auto-fit column — both truncate
illegibly at a normal column width (a file input reading "Choose File
I…3", a raw multi-select clipping every option name past the edge). A
single-choice asset select stays in the grid alongside everything else;
only a field whose content genuinely needs the width gets a full row.

**LoRAs are a chip group, not a raw multi-select.** A `multiple=True`
asset param (`loras`) renders with `forms.CheckboxSelectMultiple`, and
`forms._base_field_for` stamps `field.chip_group = True` on it so
`_field.html` renders the platform's chip-picker markup instead of the
default widget — the same `<label class="chip-check"><input
type="checkbox">…<span class="chip">…</span></label>` pattern used
elsewhere on the platform for "pick any number", styled entirely by the
shared shell CSS and never duplicated locally. The template picks this
branch off the stamped flag, not by inspecting the widget class. The
submitted shape does not change: still one field name, still read with
`getlist`. The three field states (enabled/unused/ignored) apply to a chip
group exactly as they do to any other field — Django's `CheckboxSelectMultiple`
merges the field's `disabled`/`aria-describedby` widget attrs onto every
rendered checkbox, so a disabled chip group needed no extra wiring beyond
what every other disabled field already had.

**Chosen-file preview.** `create.html` carries one small inline script,
scoped to the generate form's own file inputs, that shows a preview of
whatever image was just attached — `URL.createObjectURL` on `change`,
revoking the previous object URL when the input's value is replaced or
cleared, only for a file whose `type` starts with `image/`. It is pure
progressive enhancement: nothing about submitting the form depends on it
running, and with JavaScript unavailable the operator attaches a file and
submits exactly as before, simply without the preview. The same script
also adds a Remove control beside the preview so a chosen file can be
unselected outright rather than only ever replaced, and — like the
preview itself — that control simply never exists without JavaScript,
leaving the native replace-only behaviour standing.

**Cancel a stored hand-off.** A gallery "Use in ..." link's carried
reference (below, "Feeding an image back in") used to have no way to undo
short of editing the URL. Each stored-input block now carries its own
"Remove" link, whose `href` is the create page's own URL with just that
one `input_<key>` gone and everything else — the picked operation, the
picked model, any OTHER stored reference — intact; clicking it with no
JavaScript reloads without that hand-off, the fallback. The same script
that builds the chosen-file preview above intercepts the click instead
and removes the block from the page in place, no reload.

**An honest banner when a model can run nothing here.** When the selected
model's engine reports no operations for the family it declares
(`services.operations_for_model` returns `[]` — a REAL fact, not "no
opinion"; see "The chooser is not the registry" above), the page shows a
banner saying so by name (the model id and engine) and disables the generate
form's fields and submit button (an HTML `<fieldset disabled>`, so this
needs no JavaScript) rather than letting the operator fill in a form that
can only fail once submitted.

## Feeding an image back in

The gallery renders one "Use in <mode>" link per registered operation that declares a
file param — driven by `views.input_targets()`, so a mode appears there the day it
registers and the template names none. The link is
`/vision/?operation=<key>&input_<param>=output:<id>`; the create page carries that reference
in a hidden field, shows a thumbnail of it inline under the matching file field
(`vision/_field.html`, not a separate block at the top of the form), and makes that field optional
(`forms.build_constant_form(stored_keys=...)` — the create page's one form; `build_form`
is the POST-path form that actually validates the submission). On submit, `generate`
resolves the reference with
`services.stored_input` into the same upload-shaped object an attached file produces —
an attached file always wins — so nothing about the submission path changes. It is the
same reference a queued payload carries (`{"inputs": {"init_image": "output:12"}}`),
with one resolver behind both.

Both halves of the flow resolve a reference through `services.resolve_inputs
(operation, {param: reference})` — the page's carried `input_<key>` field and a
queue payload's `"inputs"` map alike. A reference naming a param the operation
does not declare as a file param is refused, not dropped, on both paths. The
"an attached file wins over a carried reference" rule stays in the view: it is a
page rule about an operator changing their mind, and the resolver never sees
both candidates.

**A third reference kind: `document:<id>` (chat image artifacts, 2026-09-16).**
An image attached to a chat conversation is a generation input like any other
— the thing to edit, or a reference while editing another. `services.stored_
input` resolves it through `agents.contracts.artifacts.file_resolver_for
("document")`: a **dotted path** the column that owns documents registers at
app start, resolved here at call time. This column never imports that one —
they are peers, private to each other, and `foundation/ops/tests/
test_import_law.py::test_neither_tool_column_imports_its_peer` fails the build
on it. The resolver hands back an `ArtifactFile(path, name, media_type)`, which
becomes the same `store.StoredFile` a browser upload becomes, and `submit_job`
copies the bytes into the job's own `inputs/` directory exactly as it does for
every other kind — so detaching or deleting the document afterwards never
affects a queued, running or finished job. A hand-crafted POST carrying
`input_<param>=document:<id>` still resolves through this same
principal-gated resolver and enqueues exactly as a genuine one would; only
the create page's own carried-reference *preview* skips the kind (it shows
vision's own two kinds only — a chat turn, not this page, is the door an
attached document comes in through).

Four refusals, and each one is a sentence a model can act on. A reference the
registry has no resolver for (that column is not installed), and one whose
resolver raises `LookupError` (missing row, no file on disk, **or** not visible
to this principal — one exception for all three, so an invisible row cannot be
told from a missing one), both get the *same* sentence a dead `output:`
reference gets: `document:12 does not name a stored image.` A resolved file
that is not an image gets `document:12 is application/pdf, not an image.`
(or `… is a file of unknown type, not an image.` when the owning column does
not know). A file that vanished between the lookup and the read gets
`The file for document:12 is no longer on disk.` All four echo the **bare**
`document:<id>` form — a reference may carry a percent-encoded display title,
which is somebody's uploaded filename, and no refusal ever repeats it. The
`output:`/`input:` branches are unchanged, byte for byte.

## Tools

`tools/vision/tools.py` registers two `agents.contracts.tools.ToolSpec`s in
`VisionConfig.ready()`, after the feature-flag early exit and after the five
`register_operation` calls — with the feature off there is no vision role,
no operations, no job kind, and now no tools either. Each runner is a thin
wrapper: it calls the *same* service function the page calls, never a
parallel copy of that logic.

| Tool key           | `roles`             | `mutates` | Calls |
|---|---|---|---|
| `vision.operations` | `()`                 | `False`   | `services.operation_catalog(resolved=None)` — one preflight for the whole catalog, exactly what `GET /vision/operations/` serves |
| `vision.generate`   | `("vision.generate",)` | `False`   | `services.submit_job` + `services.wait_for` — exactly what the queued `vision.generate` job handler (`jobs.run_generate`) already does |

`vision.operations`'s catalog now lists every registered operation, some
marked `"supported": false` for the selected model (Task 2's reversal of
"never tell a tool about a mode it cannot perform"). `run_operations`
passes the catalog through to `ToolResult.data` verbatim, but an LLM
reads `.text`: for an unsupported entry, that operation's own line gets
`" — not runnable here: {unsupported_reason}"` appended, so the cue is
visible without a caller having to parse `data`.

**A successful generation describes its own output** (vision-describes-
its-own-output task) so a caller that just made an image can judge or
retry it without a second turn. Both callers of `services.submit_job`/
`wait_for` — the queued `vision.generate` job kind's own handler
(`jobs.run_generate`) and `run_generate` above, the chat tool's own
synchronous submit/wait — call the SAME shared gate,
`services.describe_if_ready`, right after `wait_for` returns: one
implementation, two callers. It costs one extra call through the
extraction role's own model, made only after the image generation has
fully finished (never overlapped with it, and the image model is never
released first — see "Queued generation" above for why), and an unbound
extraction role simply means no description — the generation itself is
unaffected either way, for either caller.

For the CHAT path specifically (ruling 3): `agents.runtime.jobs.
plan_turn` — the `agent.turn` job kind's own planner, which is what
actually admits a chat turn to the execution queue — now declares
`rag.extract` alongside `vision.generate` whenever this tool is granted,
tolerantly (unbound → declares nothing) and `synchronous=False` (the
same flag `vision.generate`'s own ref there already carries, for the
same reason: neither model loads in-process inside the turn's own
handler). `rag.extract` is deliberately NOT added to this tool's own
`ToolSpec.roles` — `agents.runtime.loop._roles_resolve` drops a tool
from the turn ENTIRELY when any of its declared roles fails to resolve,
so doing that would make image generation itself vanish whenever
`rag.extract` is unbound, a far worse regression than the one this task
fixes.

`vision.generate`'s params are **computed from `all_operations()` at
registration time** (a ruling superseding this task's original literal
param list, which did not match any real operation's own keys — `cfg`
where every operation declares `cfg_scale`, among other mismatches, so no
registered operation could actually be submitted through it). This is why
`tools.vision.tools.build_generate_spec()` is a function rather than a
module constant: a constant would be evaluated at import time, before
`ready()` has registered anything, and would ship an empty param list.

Every file param the union exposes — the shared `image` key and each further
file key under its own name (`mask_image`, `reference_image`) — describes the
same three-kind vocabulary: `output:<id>`, `input:<id>` or `document:<id>` (an
image attached to this conversation). The per-turn narrowed spec rebuilds
`image`'s description from the surviving operations and carries the same three
kinds.

**The union.** Every operation's params, deduplicated by key —
registration order is txt2img, img2img, inpaint, upscale, edit, and a key
is placed at its *first* operation's declaration — translated onto
`agents.contracts.tools.TOOL_PARAM_KINDS` (which has no `"file"`):

- A `"file"` param becomes a TEXT artifact-reference param. Every
  operation's *first* file param (`Operation.file_params()`'s own
  declaration-order rule) collapses onto ONE shared key, `image` — it
  always means "the picture this generation starts from or edits",
  whichever operation is picked. Every *other* file param — `mask_image`
  on `inpaint`, `reference_image` on `edit` — keeps its own key, because a
  caller supplies it *in addition to* `image`, not instead of it.
- A `"choice"` param (`sampler`, `scheduler` — ENGINE-owned, no fixed
  `choices`) becomes `"text"`: `validate_params` requires a non-blank
  value for ANY `"choice"` param, required or not, so a `"choice"` tool
  param would reject a call that simply omits it — the identical trade
  `tools/rag/tools.py`'s own `_CATEGORY` makes for the identical reason
  (see that module's docstring).
- `"asset"` (`loras`, `upscale_model`) passes through exactly as declared
  — it is already in `TOOL_PARAM_KINDS`.
- Every other param (`prompt`, `cfg_scale`, `denoise`, `guidance`,
  `instruction`, `mask_grow`, `batch_size`, `width`, `height`, `steps`,
  `seed`, `negative_prompt`, `lora_strength`) keeps its real key, kind,
  description, default, and bounds verbatim.
- Only `"operation"` is `required=True` at this schema's level. Every
  other param's `required` is real but *local* to one operation — `prompt`
  is required for `txt2img`, meaningless for `upscale` — and a union
  schema cannot honestly project one operation's requirement onto every
  call. The real enforcement is `submit_job`'s own `validate_params`,
  downstream of narrowing, where the picked operation is known.

`submit_job` validates against the *one* operation the caller picked, and
rejects a key that operation does not declare. So `run_generate` narrows
the caller's own arguments down to what the picked operation declares
*before* calling `submit_job`, and it **announces** every argument it drops
in `ToolResult.text` — never silently, and only for what the caller
actually supplied (a union param nothing ever touched was never dropped,
it simply never applied).

`image` and any supplied second file key the picked operation declares are
resolved *together*, in one `services.resolve_inputs` call, and its
return value is handed to `submit_job(files=...)` unchanged — no
remapping: `resolve_inputs` is asked for exactly the keys the picked
operation names, so it always answers under those same keys. An `image`
argument given to an operation with no file param at all is a dropped
argument under the same narrowing rule, not an error.

An ENGINE-owned param (`sampler`, `scheduler`) the caller leaves blank is
filled by `services.fill_engine_blanks` — the *same* function the page
uses for the params a model *ignores* — with this module's own, wider key
set (every blank engine-owned choice, not only the ignored ones: a tool
caller has no form and no disabled widget, so it may simply have omitted
`sampler`). The value order is unchanged: `services.live_defaults`'s
value for the selected model, else the first entry `services.live_options`
reports — never left blank, which `validate_params` would refuse for
*any* `"choice"`-kind param regardless of `required`.

A numeric (`"int"`/`"float"`) *or* `"choice"`-with-a-fixed-vocabulary
param the caller *omitted entirely* — `steps`, `cfg_scale` today; the
brief's own wording was "numeric/choice", and the `"choice"` half is
real even though every operation registered today only declares the
engine-owned kind above — gets the same `services.live_defaults` value
the page's own GET already spreads into its form's `initial`
(`build_form_for`'s caller). Before this, only the engine-owned
`"choice"` params above read live values on the tool path; an omitted
`steps`/`cfg_scale` fell straight through to `submit_job`'s
`validate_params`, which fills the *static schema* default
(`operations.py`'s `SAMPLING_PARAMS`) — correct for the checkpoint
family the schema was written against, badly wrong (and multiple times
slower) for a family whose graph is tuned for a handful of steps and
near-unity guidance. A key the caller *explicitly supplied* — even a
value equal to the schema's own default — is never touched by this
fill; only a key genuinely absent from the caller's own arguments
(`ctx.supplied_keys`, threaded through `_narrow_for_operation`) is
filled, and only when the model reports a value for it — one it does
not report is left for `validate_params`'s own schema fallback, exactly
as before. Never the engine-owned keys above: those already ran
through `services.fill_engine_blanks`'s own blank check, off the
identical `live_defaults` source, so this second pass has nothing left
to add for them.

`run_generate` preflights *once*, itself — after `resolve_inputs` (a dead
or malformed reference is a repairable `ValueError`, and it must surface
before this pays a health round trip for a generation that was never
going to run anyway) and before the engine-param fill and the submit —
and threads its `resolved` binding to both `_fill_engine_params` (which
no longer preflights on its own) and `submit_job(resolved=...)`. This
used to run twice for any operation with a blank engine-owned param:
`_fill_engine_params` preflighted for itself, and `submit_job` preflighted
again internally, resolving the role from the database a second time for
the same call. `submit_job` still preflights internally regardless — its
own contract, shared with the page's submission path, unchanged here —
but passing `resolved` degrades that internal call to a health check on
the SAME binding rather than a second full role resolution. An operation
with no engine-owned params (`upscale`, `edit`) used to pay only
`submit_job`'s one preflight; it now also pays `run_generate`'s own
role-resolving preflight up front, so it costs one role lookup plus two
health checks instead of one role lookup plus one health check — the
trade for a single call site the whole runner shares.

`run_generate` waits synchronously, via `services.wait_for` — the same
function "The service layer" section above describes — polling the *image
engine* about a generation already submitted, never
`models.contracts.queue.get_job`; the never-block-on-a-queue-job rule is
about the queue, and this seam never touches it. The wait is **clamped to
whatever remains of the turn's own step budget**
(`ctx.budget.deadline_monotonic`), never past it and never below one
second — `tools.vision.jobs.GENERATE_WAIT_TIMEOUT_SECONDS` (the same
constant the queued job handler uses, never a second one) is only the
*upper* bound.

Every finished output's ref (`output:<id>`, the same string
`.artifacts` carries) is also named IN the text (2026-09-03 fix batch,
item C) — `"Outputs: output:36 — reference this to edit."`, every
output listed for a batch. `.artifacts` alone is not enough: a model
never sees that field when a tool turn is replayed, only `.text`, so a
follow-up edit step had nothing in-turn to name the image by. Prose
only, never a bracketed `[artifacts: ...]` block — the chat runtime
already appends its own such line to a replayed tool turn, and this
module adding a second one would read as duplicate noise. The line is
skipped entirely when there is no output yet (a non-terminal or failed
job).

`services.VisionUnavailable` (a `RuntimeError`, raised for an unbound role
or an unreachable engine) is translated to `agents.contracts.tools.
ToolRefused` — nothing the model can say fixes either, so it gets no retry
— preserving the platform's own operator-facing message
(`services.role_unbound_message()` for an unbound role) verbatim, never a
second sentence. `services.InputReferenceError` (a `ValueError`, for a dead
or malformed image reference) and `models.contracts.operations.ParamError`
(from `submit_job`'s own parameter validation) both propagate unchanged —
repairable failures the model gets one retry for.

Neither tool declares a `describer`: Ruling R3 struck the vision describer
from P1 entirely.

### Per-turn narrowing, pre-row refusal, and error-speaking text

`vision.generate`'s registered schema (above) is the UNION across every
registered operation, built once at `ready()` time — it has no opinion
about which model is bound, because `ready()` may touch no database. Left
alone, that union steers a model toward operations and numbers the BOUND
model cannot actually honour: an `operation` enum offering all five modes
when the bound model implements two, an `image` param whose static prose
names "the image-to-image family" regardless of whether this model has one,
and numeric defaults (`steps`, `cfg_scale`) written for an arbitrary
checkpoint rather than the one actually bound. A model that reads the
static schema alone — it need never have called `vision.operations` first —
has no way to tell.

`tools.vision.tools.narrowed_generate_spec(spec)` is the per-turn fix:
`agents.runtime.loop.available_tools` calls it every turn, beside
`agents.runtime.flowtool.narrowed_flow_spec`, keyed to `vision.generate`
and reached only through a dotted-path string (import-law rule 3 forbids
`agents/` importing `tools/` at all, even lazily). It reads the bound
model's own `services.operations_for_model` / `services.live_defaults` and
returns a spec whose `operation` enum is narrowed to what the model
actually supports, whose union params are dropped when no surviving
operation declares them, whose `image` description names only the
surviving operations, whose numeric defaults and prose are overwritten with
the model's own numbers, and whose description gains the supported list.
Unlike `narrowed_flow_spec`, this **never returns `None`**: an engine with
no opinion (or one that happens to support every registered operation)
leaves the union spec exactly as registered rather than a same-shaped copy,
and a role that is unbound does the same — there is always a legal call to
offer, or the caller is left to the refusal below to explain why not.

Narrowing is a per-turn courtesy, not the enforcement. `run_generate` itself
refuses an unsupported operation *before* any row is written: once preflight
resolves the binding, it checks the requested operation against
`services.operation_states` and raises a plain, **retryable** `ValueError`
(never `ToolRefused` — a *different* operation can still succeed) naming why
and what the model runs instead. Without this, `submit_job` would create a
real row, stamp the bound model onto it, and only the engine's own template
lookup would refuse — after the fact, and the refusal is defence in depth
behind the narrowing above for whatever narrowing does not cover (a queued
job, a caller that never re-fetched its tool list).

And when a generation *does* reach `failed`, the tool's result text now
speaks the job's own `error` — `"Generation … finished as failed: <error>."`
— rather than the bare status word. The error already names the actionable
thing; it sat one dict key away in `job_json`'s own payload and was never
read.

No model or family name appears in any of this prose — every sentence is
built from what the CONNECTION declared (`operations_for_model`,
`operation_states`, `live_defaults`), never a name this module ships.

## Running the tests

```bash
.venv/bin/pytest tools/vision -q
```

HTTP is mocked at the `httpx` layer only (`models.contracts.engines.comfyui.httpx.get`/
`.post`), never by patching the adapter's own methods away, so the adapter's real URL
building, JSON parsing, and error handling all run under test —
`tools/vision/tests/_helpers.py::FakeComfyUI` is the shared double, dispatching on path
(`/system_stats`, `/object_info/<node>`, `/prompt`, `/history/<id>`, `/queue`, `/view`).
There is no `conftest.py` (repo convention); each test module imports what it needs from
`_helpers.py`, including a `clear_bindings()` helper every role-touching test uses to undo
whatever the seed migration bound.

## Deferred (spec §9) — not a gap, a scope line

- **A background worker or batch runs.** Completion is poll-driven by the page; a worker
  can be added later without changing the `services` API. WebSocket progress (ComfyUI
  offers `/ws`) is likewise deferred — polling is sufficient at this scale.
- **Retention or quota** for `data/generated/`.
- **The chatbot tool wrapper itself.** `services` is written to be that seam, but no tool
  calls it yet; the feature-flag pattern (`FARABUNKER_FEATURES`) is the hook a future
  per-tool toggle reuses.
- **A second engine adapter** (A1111/Forge, SwarmUI, InvokeAI, a hand-rolled diffusers
  service) mapping the same operations.
- **ControlNet.** It needs a preprocessor story this module doesn't open yet — a
  conditioning image alone isn't enough; deciding which preprocessor runs on it is a
  separate piece of scope.
- **Embedding assets.** ComfyUI has no embedding loader node
  (`comfyui.py::_ASSET_NODES` says so), so an `"embedding"`-kind asset param would
  honestly report nothing to select from; it stays deferred until an engine can list one.
- **Authentication for `/vision/`'s mutation endpoints** (`generate/`,
  `jobs/<uuid>/delete/`) — unauthenticated today, the same Phase-1 gap `/inference/`
  already carries.

## Known limits (operational, raised on the live preview)

Not scope gaps in the code above — real constraints the owner hit running the
EDIT operation on the reference hardware, recorded here rather than fixed
silently, and carried into the plan's "Open questions for the owner":

- **The generation wait timeout (`GENERATE_WAIT_TIMEOUT_SECONDS`, now 4
  hours) is a module constant, not operator-tunable.** Raised 2026-08-25 from
  an original 600s (10 minute) bound that was an order of magnitude too
  small -- it fired mid-run (queue job 32, at sampler step 13/20) and, per
  ADR 0012's known gap, released the exclusive model slot while ComfyUI was
  still sampling. 4 hours covers the slowest measured real run with margin
  (the edit-only family needed on the order of ten-odd minutes of sampling;
  the ordinary, non-distilled build of the other family needed several tens
  of minutes of sampling plus a comparable cold-load delay) and is a runaway
  backstop only -- worker liveness is independently covered by
  `models/queue/worker.py`'s own heartbeat writer, not by this bound. The
  handler still reports honestly if the bound is ever hit
  (`timed_out: True`, generation continues under memory-guarded polling),
  and the residual risk is now only that 4-hour backstop firing on a
  genuinely wedged engine, not an ordinary slow run; an operator with slower
  hardware still has no way to raise the budget without editing
  `tools/vision/jobs.py`.
- **EDIT's default `steps` (20) is slow on this hardware -- for the
  ordinary (non-distilled) `flux2` graph.** Sampling alone runs to the order
  of tens of minutes for a roughly 1 MP edit, on top of any cold load. A
  lower default (8-12, matching the distilled-LoRA-tuned step counts the
  bundled ComfyUI workflows assume) or surfacing an expected-time estimate
  next to the Steps field would both help; neither is built. The distilled
  variant of the same family (its own 4-step default) MEASURES far faster on
  the same hardware -- single-digit minutes end to end across two governed
  runs (the first interrupted by the worker token race described next, and
  recovered by `refresh_job`) -- making it the better fit for iteration than
  the ordinary build.
- **A worker token race could orphan a queue job whose generation kept
  running and succeeded (fixed in main via PR #50, `bed3ca1`).** The
  distilled variant's own long cold load starved the worker process past
  its 120 s heartbeat staleness sweep, causing a same-job re-claim whose
  cleanup deleted the
  live attempt's token unconditionally (`models/queue/worker.py::_execute`'s
  `finally`) -- the generation itself was unaffected, but the queue job
  reported failure/loss until `refresh_job` recovered the row. Fixed by
  making that cleanup token-conditional, matching every other
  token-conditional writeback already in the module; ADR 0013 carries a
  dated amendment. A longer-term fix (a dedicated heartbeat thread, and/or a
  cold-load-aware staleness threshold) is out of this module's scope and
  handed off to the `models/queue` maintainer
  (`.superpowers/owner-requirements/peer-handoff-worker-token-race.md`).
- **The ordinary (non-distilled) family's memory headroom is tight, not
  comfortable.** Observed system free memory bottomed out at a low
  single-digit number of GB during a governed edit run with nothing
  else loaded (LIVE RUN #3); there is no margin today for a second model, a
  larger batch, or a busier host machine, and the queue's own
  exclusive-by-default posture for this job kind is NOT fully intact across
  a timed-out wait -- see the next item.
- **A timed-out wait releases the exclusive slot while the model is still
  resident (final-review finding 5).** `run_generate` returns
  `{"timed_out": true, ...}` and the QUEUE job succeeds the instant
  `GENERATE_WAIT_TIMEOUT_SECONDS` elapses -- `plan_generate`'s `exclusive=True`
  hold is scoped to the queue job's own lifetime, not the generation's, so it
  is released while ComfyUI is still sampling and the checkpoint still
  resident. A second exclusive job can then be admitted concurrently with a
  generation that has not actually finished. See ADR 0012's "Two status
  vocabularies" section for the full mechanism; closing it is a scheduler
  decision (whether the exclusive hold needs to outlive the queue job that
  requested it) that belongs to the execution queue's own admission logic.
- **FIXED: a model already resident at submit now reports no footprint,
  instead of a growth-only delta.** A prior version of this measurement
  under-counted a distilled connection that was already loaded going into
  two governed runs: the delta-based strategy only saw each run's
  incremental growth, landing on a footprint a full order of magnitude below
  the connection's actual resident size. A sibling ordinary connection,
  measured while genuinely NOT already resident, stayed plausible. That
  under-measurement went uncaught into the memory plan (job 24,
  2026-08-25): the plan admitted the ordinary connection alongside the
  already-loaded distilled one because the distilled connection's recorded
  footprint looked small enough, free memory fell to a low single-digit
  number of GB, and the operator interrupted the run.
  `models/contracts/engines/comfyui.py`'s `_remember_run` now stamps each run
  memo `resident_at_submit=True` whenever the memo it replaces already names
  the same model, and `loaded_footprint` returns `None` unconditionally when
  that flag is set -- BEFORE computing any delta -- rather than trusting a
  delta that can itself clear the 256 MiB credible floor (the under-counted
  connection's own growth-only reading did). `models/queue/worker.py`
  already skips a `None` footprint
  (`if not size: continue`), so a prior cold-load measurement on the
  connection stands untouched. A file-size floor (refusing a delta smaller
  than the checkpoint's size on disk) was considered and NOT implemented:
  ComfyUI's `/object_info` never reports a file's size, so `list_installed`
  already reports `size=None` for every model here and there is nothing
  cheap to compare a delta against.
- **The edit-only family is live-verified with a GGUF text encoder.**
  Originally registered with an fp8 encoder
  (the vision model's fp8-scaled checkpoint) that upcasts on MPS to a
  double-digit number of GB resident -- the same OOM-shaped combination
  described above -- the connection now uses its quantized GGUF build plus
  the required projector (mmproj) file: ComfyUI-GGUF locates the mmproj by
  matching it as a filename substring of the encoder's base name, and logs
  `Qwen-Image-Edit will be broken!` when it can't find one.
  A plain 20-step run took several times longer, wall-clock, than the
  distilled 4-step LoRA run against the same connection, with resident
  memory in the low tens of GB (`mps`-resident weights, a `cpu`-resident
  text encoder, and the VAE) and no memory pressure observed. Both the
  4-step and 8-step distilled LoRAs are installed and offered in the edit
  form's LoRA field; the 4-step LoRA has now been run against this
  connection.
- **FIXED: `unload()` now waits for ComfyUI to actually free memory before
  returning (2026-08-25, job 28).** `POST /free` only sets a flag ComfyUI's
  prompt-worker thread consumes on its NEXT loop tick, not synchronously with
  the 2xx it returns -- so `unload()` used to report success the instant
  ComfyUI accepted the request, and the worker's next `/prompt` could land
  before the free actually happened. It did: an ordinary checkpoint loaded
  on top of an already-resident distilled one, free memory falling by
  double digits of GiB instead of rising. `unload()` is now a barrier --
  after a 2xx it polls
  `/system_stats` (and, once that looks sufficient, `/queue`, to confirm
  nothing is still executing) until the pre-POST free-memory reading has
  risen by at least HALF the endpoint's known footprint (or the bare 256 MiB
  floor when none is known -- see the `/free` caveat below for why half, not
  the whole footprint), or `UNLOAD_TIMEOUT` (30s, covering the POST, the
  baseline read, and the poll together, deadline-bounded throughout) elapses.
  When there is no baseline to measure a rise against at all (a transient
  `/system_stats` failure right at the top of the call), this degrades to a
  single immediate `/queue` check instead of polling out to the full
  timeout for a delta that could never be computed anyway. `True` now means
  the free was observed (or, on that no-baseline path, that the POST was
  accepted and nothing looked still running); `False` covers a refused POST,
  an unreachable engine, and a settle-poll timeout alike. Read the worker's
  "eviction unload refused" log line as "memory might still be held", not as
  a guarantee -- it is a genuine false-negative-shaped signal (a settle
  timeout can still mean the free actually happened), and `unload`'s bare
  256 MiB floor can itself produce a false-POSITIVE `True` off unrelated
  machine activity (a browser tab or container freeing memory at the same
  moment) when this endpoint has no recorded footprint yet to compare
  against. See ADR 0012's memory-governance seams section for the full
  mechanism.
- **The residency memo cannot survive a worker restart, and nothing
  persists it across one.** `_RUN_MEMO` is a bare module-level `dict` in
  `models/contracts/engines/comfyui.py` -- gone the instant the worker process
  that wrote it exits. Two sanctioned persistence seams were checked
  (2026-08-25) and both ruled out: Django's cache (`config/settings.py`
  declares no `CACHES`, so it defaults to `LocMemCache` -- itself a
  per-process dict, no more durable than `_RUN_MEMO` already is) and a small
  model owned by `models/contracts` (that package is deliberately not a Django
  app -- no `models.py`, absent from `INSTALLED_APPS` -- its purity as a
  rule-1 pure leaf means it holds no persistence of its own, and giving it
  one would be a much larger change than this fix). Nothing was faked in
  either direction: on a cold memo, `list_installed` honestly reports
  `loaded=False` for every model at an endpoint, which is conservative
  about claiming residency it has no evidence for but is NOT safe in the
  OOM sense -- a restarted worker that
  believes nothing is loaded will not evict a checkpoint ComfyUI may still be
  holding from before the restart. The memo is also ONE MODEL PER ENDPOINT by
  construction (a new run replaces whatever the endpoint's entry held), so it
  can never represent two checkpoints resident on the same endpoint at once
  even within a single process's uptime. Since every vision job is
  `exclusive=True` unconditionally (`tools/vision/jobs.py::plan_generate`),
  the safe mitigation belongs to the worker: call `unload()` unconditionally
  on an exclusive job's own endpoints ahead of launch, rather than gating on
  `list_installed`'s `loaded` flag, so a cold-memo restart is never
  load-bearing for eviction safety. Left as a peer/worker-track item --
  `models/queue` is untouched here.
- **On this machine, ComfyUI's `/free` has been observed to release only
  `mps`-device models -- a CPU-resident component may survive it, and this
  is WHY `unload`'s settle threshold is a majority of the known footprint,
  not the whole thing.** A text encoder ComfyUI chooses to keep on `cpu`
  rather than `mps` is not necessarily among what `unload_all_models()`
  releases. Because `ram_free`/`vram_free`/`torch_vram_free` are all the
  same `psutil.virtual_memory().available` figure on this unified-memory
  host, a CPU-resident encoder `/free` left behind still reads as occupied
  on the next `/system_stats` poll -- so a checkpoint whose weights sit on
  `mps` but whose text encoder sits on `cpu` can never give back its FULL
  recorded footprint even on a genuinely successful free. Demanding the
  whole amount back would make every correct eviction stall the entire
  settle timeout and then falsely report `False`; demanding only half is
  still comfortably above the noise the bare 256 MiB floor guards against,
  and still a real, checkpoint-sized rise. Not a bug in the settle poll;
  documented because there is no cheap way to tell "GPU model, freed" from
  "CPU model, still there" over `/system_stats` alone.
- **`narrowed_generate_spec` (the "Per-turn narrowing" section above) used to pay
  one `services.preflight` health round trip every turn a chat agent carries
  `vision.generate`, uncached** (review round 1, peer finding Q1 — the fix
  batch this section documents narrowed a schema, and deliberately left
  preflight's own caching story alone). Since fixed: `preflight`/
  `_health_check`'s round trip is now cached for 30 seconds, process-locally,
  per bound engine and endpoint — see "The service layer" section above and
  `tools/vision/probe_cache.py`.
