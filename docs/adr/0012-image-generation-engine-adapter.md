# ADR 0012 — Image generation engine adapter

**Status:** Accepted
**Date:** 2026-08-22
**Names generalised:** 2026-09-14 — illustrative model names in this ADR's prose were
replaced with capability language ahead of publication; the decision, its date, its options
and its consequences are unchanged.

Builds on [ADR 0010](0010-model-management-framework.md) (model management
framework) and its 2026-08-22 amendments, and [ADR 0011](0011-branch-preview-stacks.md)
(branch preview stacks).

## Context

Every capability farabunker has shipped so far is RAG: one chat role, one
embeddings role, one module. ADR 0010's first amendment predicted this would
not stay true — it named "a second engine and a non-RAG role" as the
validation case for the framework it records, and reserved the `"vision"`
capability ahead of any role using it, specifically so the first feature to
register an image-consuming role would need zero framework changes.

This ADR is that validation. Image generation (diffusion-model families) is
the first non-RAG capability: a `/vision/` page where the operator types a
prompt and gets an image, integrated through the same engines-as-code /
models-as-data grammar RAG already uses — a new engine adapter, a new
capability, a new role bound in the `/inference/` console. The binding
design lives in
[`docs/superpowers/specs/2026-08-22-image-generation-design.md`](../superpowers/specs/2026-08-22-image-generation-design.md);
this ADR records the decisions it made, why, and where the shipped code
deviates from it.

Three properties carried the whole design, in priority order: **offline**
(the platform never downloads a model, checkpoint, or asset — the operator
places files, the platform lists and uses them), **portable** (the engine
runs natively on the host, reached from the `web` container the way Ollama
already is), and **content-agnostic** (no prompt or output filtering, no
model inspection — any checkpoint the operator places, including uncensored
community merges, is a first-class model).

## Decision

### D1–D12

| # | Decision | Why |
|---|---|---|
| D1 | First engine adapter targets **ComfyUI** (`core/inference/engines/comfyui.py`) | Actively maintained; Windows portable build, Linux (CUDA/ROCm), macOS (Metal); fully offline once checkpoints are on disk; a stable HTTP API (`/prompt`, `/history`, `/view`, `/object_info`, `/system_stats`); our templates use only built-in nodes, so vanilla ComfyUI with no custom nodes or Manager suffices. |
| D2 | Engine is a `core.inference.engines` adapter, registered in `ENGINES` | The registry is already engine-agnostic — health checks, server scan, and the engine dropdown all iterate `ENGINES.values()`. A second adapter is one file plus a `register()` call (`core/inference/engines/__init__.py`). |
| D3 | New capability `"image-generation"`; the existing `"vision"` keeps meaning image-*input* | Output and input capabilities are different needs — `"vision"` already enriches vision-capable catalog entries for models that *read* images. `CAPABILITIES` (`core/inference/roles.py`) now holds both. |
| D4 | One role, `vision.generate`, registered by `modules.vision` | "The first non-RAG role" ADR 0010 said would validate the framework. The role binding **is** the checkpoint's DEFAULT — switching the default means rebinding in the console, exactly like `rag.answer`. A per-generation override now exists (see "Instruction-based editing across model families", D-EDIT-4): it picks among registered connections the same way `rag.ask`'s picker does, and does not touch the binding itself. |
| D5 | **Operation-driven**, not mode-driven (`core/inference/operations.py`) | Generation modes (txt2img, img2img, inpaint, ControlNet, upscale) have wildly different parameters. A platform-level operation registry with a per-operation parameter schema lets the page render its form from the schema and lets an engine map each operation onto its own graph template. Adding a mode is one `Operation` definition plus one template — never a reshape of the page or the service layer. |
| D6 | Generic job/output records carrying `media_type` and the stored engine payload verbatim | `GenerationJob`/`GeneratedOutput` (`modules/vision/models.py`) name no image specifics — video or audio later are new `media_type` values on the same tables, not new tables. Every result is reproducible and exportable: `params` (validated, seed resolved), `model_fingerprint` + `model_config` (the binding as it was at submit time, not as it is now), and `engine_payload` (the exact body the engine received). |
| D7 | Assets (LoRA, VAE, ControlNet, upscaler, embeddings) are a second axis, not role-bound models | `Asset` (`core/inference/engines/base.py`) and `InferenceEngine.list_assets` exist as a seam now; they adorn a job, they don't answer a role. The UI for asset parameters ships with the first operation that actually uses one (`modules/vision/forms.py::_field_for` renders an asset param as a select filled from `InferenceEngine.list_assets`; an asset kind an adapter cannot list reports nothing rather than raising). |
| D8 | `ModelConnection` gains a nullable `config` JSONField | `ResolvedModel.config` already flows into engine builders; the DB row had nowhere to carry it. Needed for multi-file model families (UNet + CLIPs + VAE) without a later schema change — `ComfyUIEngine.build_image_generator` already threads it through to the graph template unchanged. |
| D9 | Feature flag (`FARABUNKER_FEATURES`) gates role registration and the URL mount, and nothing else | ADR 0010's first amendment: a feature toggle has exactly one job — gate *role registration*. `modules/vision/apps.py::VisionConfig.ready()` only registers `vision.generate` and the `txt2img`/`img2img`/`inpaint`/`upscale` operations while `"vision"` is in `FARABUNKER_FEATURES`; `config/urls.py` mounts `/vision/` under the same check. Building it into the first feature establishes the pattern the chatbot's future per-tool toggles reuse. |
| D10 | Completion is poll-driven; no worker, no task queue | ComfyUI queues natively. `modules/vision/services.py::refresh_job` is called from the page's own polling fetch and is idempotent — a terminal job returns without an engine call, so polling a finished job costs nothing. A worker can be added later for unattended/batch runs without changing the service API (`submit_job`/`refresh_job`/`wait_for`/`delete_job`). *(2026-08-24: a worker exists now — ADR 0013's execution queue — and the page submits through it. `refresh_job` is unchanged and still poll-driven; what changed is who calls `submit_job`. See "One submission path, and the payload that travels it" below.)* |
| D11 | Per-engine discovery endpoints (the one change to the shared model-management framework) | `discover()` used to poll every engine at the single Ollama endpoint — a ComfyUI checkpoint would never have appeared. `console/inference/discovery.py::discover` now takes a per-engine endpoint map, built by `console/inference/views.py::_engine_endpoints` from `settings.INFERENCE_DEFAULT_ENDPOINTS` plus each engine's registered-connection endpoints. |
| D12 | No default model anywhere; `COMFYUI_BASE_URL` is the only default, and it is a *location* | Same exception `OLLAMA_BASE_URL` already has under ADR 0010's third amendment (no-baked-defaults): a server address is deploy convention, not a model choice. `COMFYUI_BASE_URL` defaults to `http://localhost:8188` (`config/settings.py`); there is still no default checkpoint anywhere — the operator places `.safetensors` files and binds one in the console. |

**D6 addendum (2026-08-24): `GenerationJob.seed` is nullable.** D6 says the
job record is media-generic and reproducible; a mode with no randomness has
nothing to reproduce. `upscale` declares no `"seed"` param, so its jobs
record `seed = NULL` and their cards show no seed fact, rather than a
random number the generation never used. Every other operation still
resolves and records one (`validate_params` turns a blank seed into an
integer), which is what keeps a txt2img result reproducible.

### `GenerationRejected` — the engine's outright-refusal exception

`core/inference/engines/base.py::GenerationRejected` is recorded here as a
decision, not an implementation detail. An engine raises it when it refuses
a submission outright — an unknown checkpoint, an invalid graph — and it is
what implements the design spec's "job created then immediately failed with
the engine's own text" behaviour: `modules/vision/services.py::submit_job`
creates the `GenerationJob` row first, then calls the engine; a
`GenerationRejected` catches into an immediate `failed` status carrying the
engine's own words (`ComfyUIGenerator.submit` builds that message from
ComfyUI's `/prompt` error body — its `error.message` plus every
`node_errors` detail, never a rewritten guess).

This is deliberately distinct from a **transport failure** (the engine is
unreachable, times out, or the connection drops mid-call) — but that
distinction only forgives a transport failure on the POLL side, not the
submit side. `submit_job`'s generic `except Exception` branch does not
distinguish a transport failure from any other submission failure it
doesn't otherwise recognize: the job row already exists (it's created
before the engine call), so `submit_job` fails it immediately with the
exception text, the same as it would for an unrecognized rejection — there
is no "untouched" state to leave a job that was never accepted anywhere,
and silently retrying a submit whose outcome is unknown risks the engine
having accepted it once already. `refresh_job`'s own transient-failure
branch is the one place a transport failure is genuinely forgiven: polling
an already-submitted job leaves its status alone and tries again on the
next poll, because the job has one durable handle (`engine_ref`) a later
poll can always re-check. The two failure modes read differently to the
operator for a reason: "ComfyUI told us no" is final and explains itself;
"we couldn't reach ComfyUI just now" while polling a job that IS running
somewhere is not, and marking it failed would tell the operator to give up
on a job that might still complete once the engine comes back. A submit
that never reached the engine has no such job to poll for, so it fails and
the operator resubmits explicitly.

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
  engine's own words), a transport failure fails the job immediately on
  submit, the same as any other submission failure (see above).

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

### One submission path, and the payload that travels it (2026-08-24)

D10 recorded that "a worker can be added later for unattended/batch runs
without changing the service API". The worker arrived (ADR 0013), and this
records what happened when it did: **the `/vision/` page no longer calls
`services.submit_job` itself.** `POST /vision/generate/` validates, preflights,
and calls `core.inference.queue.enqueue("vision.generate", payload)` — the same
call any other caller makes — exactly as `modules/rag/views.py::AskView`
enqueues `rag.ask`.

The reason is not tidiness. While the page submitted directly, the execution
queue's scheduler had never heard of any generation the UI started, so its
memory admission — which `modules.vision.jobs.plan_generate` declares
`exclusive=True` against — could admit a language model into VRAM with ComfyUI
mid-generation. A second submission path is also a second behaviour: an agent
arriving on the queue path would get retention, priority and cancellation that
the page did not.

**The payload is a contract, and `GET /vision/operations/` (`services.operation_catalog()`
as JSON) is how a caller discovers its shape rather than hardcoding it.**
`{"operation": str, "params": dict, "inputs": dict}`:

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
"status": str, "timed_out": bool, "output_ids": [int], "output_urls": [str],
"durations": dict[str, float | None]}` (`durations` added 2026-08-25, D-EDIT-11).
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

**The `timed_out: true` row above is a known gap, not a footnote.** The
queue job's own `exclusive=True` slot (D4/D5, `plan_generate`) is held for
the LIFETIME of the queue job, not the generation: `run_generate` returns
`{"timed_out": true, ...}` and the queue job SUCCEEDS the instant
`GENERATE_WAIT_TIMEOUT_SECONDS` elapses, releasing the exclusive hold —
while ComfyUI is still sampling, the model still resident. A second
`vision.generate` (or any other exclusive job) can then be admitted
concurrently with a generation that has not actually finished, which is
exactly the memory-contention scenario the exclusive posture exists to
prevent. Recorded here (final-review finding 5, 2026-08-25) rather than
fixed silently: closing it structurally is a scheduler decision (does the
queue's own exclusive bookkeeping need to outlive the JOB that requested
it, or does `vision.generate` need its own longer-lived hold independent
of the wait loop's timeout) that belongs to whoever owns the execution
queue's admission logic, not this adapter. `GENERATE_WAIT_TIMEOUT_SECONDS`
was raised 2026-08-25 from an original 600 s bound — which fired mid-run on
real hardware (queue job 32, sampler step 13/20) — to 4 hours, sized to
exceed the slowest measured real run (the ordinary, non-distilled build of
the edit-and-text-to-image family: several tens of minutes of sampling plus
a comparable cold-load delay) with margin; the bound is
a runaway backstop, not a liveness mechanism — worker liveness is covered
independently by `console/jobs/worker.py`'s own heartbeat writer. That
change does not close this gap structurally — the exclusive posture is
still not architecturally intact across a timed-out wait — but it reduces
the gap's residual risk to a genuinely wedged engine hitting the 4-hour
backstop, not an ordinary slow generation outrunning a bound sized for a
different class of run. See also modules/vision/README.md's "Known
limits" and the plan's Open Question 6, which this same finding
corrects — the exclusive posture is NOT intact across a timed-out wait.

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
`params_invalid` / `role_unbound` / `connection_unavailable` /
`engine_unreachable` on the exceptions that are raised before any row exists.
One vocabulary (`GenerationJob.FailureKind`), two carriers, so a caller can
tell "fix your parameters" from "try again later" without pattern-matching
English. `connection_unavailable` (final review, 2026-08-25) is distinct from
`role_unbound`: a payload naming a per-generation `connection` pk
(D-EDIT-4) is resolved directly, never through the role, so a pk that no
longer names a usable connection is never honestly "no model assigned" —
the role may well be bound.

### Engine-declared setup guides (owner requirement, 2026-08-23)

Every adapter owns a `SetupGuide` (`core/inference/engines/base.py`)
describing how its server is installed on macOS, Windows, and Linux, how it
must listen to be reachable from the `web` container, where its model files
go, and the path to verify it (`verify_url_path` — the same path the
adapter's own `is_healthy` checks) — and declares `serves_capabilities`, the
platform capabilities it can serve at all. `console/setup/` renders those
declarations plus the role registry at `/setup/`: it contains no engine
name, port, path, or install command of its own, so a newly registered
adapter documents itself with no template change.

The guide lives on the adapter, not in a docs page, because the install
steps and the health path **are** engine knowledge — only
`core/inference/engines/comfyui.py` knows that ComfyUI needs
`--listen 0.0.0.0`, that its checkpoints go in
`ComfyUI/models/checkpoints/`, or that `/system_stats` is the right thing
to poll. A second copy of that knowledge in prose would rot the first time
the adapter changed a flag or a path, exactly the failure mode `docs/DEV.md`
already avoids by linking to `/setup/` as the living version of its own
ComfyUI install section rather than duplicating it a third time.

`/setup/` is a reading surface only. It never registers a connection,
binds a role, installs an engine, or downloads anything — every write still
happens at `/inference/`, the one place a `ModelConnection`/`RoleBinding`
is created or changed.

### Memory-governance seams (2026-08-25)

The adapter implements the execution queue's two OPTIONAL engine seams,
`loaded_footprint` and `unload` (`core/inference/engines/base.py`). Until it
did, every `vision.generate` job was permanently unmeasurable — and therefore
permanently exclusive — and a warm ComfyUI checkpoint could not be evicted by
anything the platform owns. The trigger was an owner-machine OOM crash on
2026-08-25.

**The footprint is a delta across the run, not a total, because ComfyUI has no
other honest number.** There is no per-model residency endpoint;
`GET /system_stats` reports the machine's memory. On MPS it does not even
report a torch-only figure: `ram_free`, `vram_free`, and `torch_vram_free` are
all `psutil.virtual_memory().available` (ComfyUI 0.33.0,
`comfy/model_management.get_free_memory`, the `cpu or mps` branch). A
"total resident now" reading on the reference machine is roughly 26 GB, nearly
all of it browser and containers — recording that as a checkpoint's footprint
would pin every vision job to "needs 26 GB" forever. So `ComfyUIGenerator
.submit` records what ComfyUI reported free immediately before it queued the
graph, in a bounded, TTL'd per-endpoint run memo, and `loaded_footprint`
subtracts what is free after the run.

**That number over-counts the checkpoint on purpose, and can under-count the
machine.** It covers everything the run allocated and has not released —
weights, VAE, text encoder, whatever caches held on to — which is the right
quantity for admission, whose question is "what does running this model cost
me", not "how large is this one tensor collection". It is not isolated from
other processes: one growing during the run inflates the reading (harmless —
the job stays exclusive longer than it needed to), one shrinking deflates it,
which is the direction that could under-admit into an OOM. Guarded twice: a
non-positive delta, and any delta below 256 MiB, are reported as `None` rather
than as a small number, because the scheduler treats `None` as "unknown, runs
alone" (safe) but would treat 200 MB as "fits alongside anything". A residual
under-count above that floor remains possible and is documented rather than
hidden — `ModelConnection.footprint_override_bytes`, the operator's "Memory
footprint override" field on `/inference/`, outranks whatever is measured.

**It is MPS-honest by construction.** The reading is bytes that actually went
missing, so an fp8 checkpoint upcast to bf16 counts at its upcast size. File
sizes are never consulted — understating residency by ~2x that way is the
miscalculation that preceded the crash.

**What the delta settles on is roughly the resident weights, not the run's
high-water mark, and that is load-bearing.** ComfyUI returns activation memory
before the queue's post-run read happens: the execution path calls
`comfy.model_management.cleanup_models_gc()` (`execution.py:769`) and the
prompt worker follows with `gc.collect()` / `soft_empty_cache()`
(`main.py:438`). So by the time `loaded_footprint` reads `/system_stats`, the
intermediate tensors are gone and the checkpoint's weights are what is still
missing — which is exactly the quantity `loaded_footprint` is defined to
report.

**The footprint is learned on the cold load; a warm re-run reports `None` and
the recorded value stands.** A second run of an already-resident model
re-stamps the baseline while that model is already loaded, so any delta
measured against it can only ever be the run's incremental growth, never the
checkpoint's real footprint. That growth is not reliably small enough for the
credible-floor guard alone to catch: an owner-machine incident (2026-08-25,
job 24) measured a growth-only delta for a distilled connection that was
already resident at submit — comfortably above the 256 MiB floor — against
a resident size a full order of magnitude larger, and the worker recorded
that smaller number as the connection's footprint, which then let the
memory plan admit a second large model alongside it and drove free memory
down to a low single-digit number of GiB before the operator interrupted
the run. The fix: `_remember_run` compares
the memo it is about to replace against the model it is stamping a baseline
for, and marks the new memo `resident_at_submit=True` whenever they name the
same model. `loaded_footprint` checks that flag BEFORE computing any delta and
returns `None` unconditionally when it is set — an honest "unknown", never a
growth-only number — which `console/jobs/worker.py` skips (`if not size:
continue`), leaving the cold-load measurement standing on the connection.
This is deliberate: overwriting a correct 8 GB with a wrong, growth-only
number (whether 40 MB or 2.4 GB) is the under-count direction that ends in an
OOM. A file-size floor (refusing a delta below the checkpoint's size on disk)
is NOT implemented alongside this: ComfyUI's `/object_info` never reports a
file's size, so `list_installed` already reports `size=None` for every model
here and there is nothing cheap to compare against.

**It is a post-run snapshot, not a peak.** That is what this seam is for by
agreement with the queue track, which owns peak monitoring.

**`unload` frees everything at the endpoint.** `POST /free` with
`{"unload_models": true, "free_memory": true}` calls
`comfy.model_management.unload_all_models()`, which releases every model on
every device there; ComfyUI has no per-model free, so the seam's `model_id`
argument is unused and its docstring says so.

**`unload` is a BARRIER, not a fire-and-forget POST (fixed 2026-08-25, the
job-28 incident).** The adapter originally treated a 2xx from `/free` as the
confirmation and returned immediately — reasoning that `/free` only sets a
flag on ComfyUI's prompt queue, with the actual `unload_all_models()` running
afterwards on the prompt-worker thread, so a verify-after-write would be
racing a thread it cannot observe. That reasoning was correct about the race
and wrong about the conclusion: because there is no ordering guarantee
between "the POST returned" and "the prompt-worker thread consumed the flag,"
`console/jobs/worker.py` could — and on 2026-08-25 did — launch its next
job's `/prompt` before the free actually happened, loading the new graph's
checkpoint ON TOP OF the one `unload` was meant to evict rather than after
it. Job 28 (an ordinary checkpoint, with the distilled variant warm): the
worker called `unload`, got `True`, and immediately submitted; free memory
fell by double digits of GiB instead of
rising, because ComfyUI's prompt-worker had not yet consumed the `/free` flag
when the new `/prompt` landed. The fix: after a 2xx, `unload` now polls
`GET /system_stats` (and, once that already looks sufficient, `GET /queue` —
to confirm nothing is still executing that could mean the free has not truly
settled) every `UNLOAD_POLL_INTERVAL` (0.5s) until the free-memory reading
taken right before the POST has risen by at least `max(
_MIN_CREDIBLE_FOOTPRINT, HALF this endpoint's known footprint from its run
memo if any)` — the same device-over-host preference `_free_bytes` always
applies — or `UNLOAD_TIMEOUT` (30s) elapses, whichever comes first. HALF, not
the whole footprint: `/free` has been observed on this host to release only
`mps`-device models (see the caveat below), so a checkpoint whose text
encoder sits on `cpu` never gives back its full recorded footprint even on a
genuinely successful free — demanding the whole amount would make every
correct eviction stall the full timeout and then falsely report `False`.

The one shared deadline is started at the TOP of the call — before the
baseline `/system_stats` read, before the POST, and enforced on every pass of
the poll loop BEFORE that pass issues a read — and covers all three phases
TOGETHER: 30s total, not 30s per phase. Each read inside the loop is itself
capped at `min(DISCOVERY_TIMEOUT, whatever of the deadline remains)`, and the
inter-poll sleep is likewise capped at `min(UNLOAD_POLL_INTERVAL, remaining)`,
so neither a slow read nor a full sleep can carry the call past
`UNLOAD_TIMEOUT`. This is deliberate: `console/jobs/worker.py`'s
`MAX_UNLOADS_PER_TICK` comment already prices a single `unload()` call at "up
to `UNLOAD_TIMEOUT` (30s)" when it reasons about `STALE_AFTER_SECONDS`
margin; a settle poll that could itself run to a fresh 30s AFTER an already
slow POST would quietly invalidate arithmetic this adapter has no business
touching. When there is no baseline to measure a rise against at all — the
one `/system_stats` read taken before the POST itself failed — the poll is
skipped entirely rather than run out to the deadline for a delta that could
never be computed: `unload` instead degrades to the PRE-BARRIER contract,
trusting the POST's own 2xx and gating on one immediate `GET /queue` check
(not a poll), so a `/free` that raced an actively-running prompt is still not
mistaken for a settled eviction.

`True` now means the free was actually OBSERVED (or, on the no-baseline
path, that the POST was accepted and nothing looked still running); `False`
covers a refused POST, an unreachable engine, AND a settle-poll timeout. Read
the worker's existing "eviction unload refused" log line as "memory really
might still be held", not as a guarantee in either direction: a settle
timeout can still mean the free genuinely happened just after the deadline,
and — the opposite failure — the bare 256 MiB floor this uses when an
endpoint has no recorded footprint yet is machine-wide, not ComfyUI-specific,
so an unrelated process (a browser tab, a container) freeing a few hundred
MB during the same poll window can make `unload` report `True` and drop the
run memo even though ComfyUI itself never actually freed anything. A prompt
still `queue_running` withholds `True` until the queue looks idle too, but
never earns time beyond the shared deadline.

**Residency is a belief, and is labelled as one.** `list_installed` reports
`loaded=True` for exactly one checkpoint per endpoint: the one this adapter
last submitted a graph for and has not since freed, within the memo's TTL,
carrying the measured footprint as `loaded_size`. Reporting it is not
decoration — `console/jobs/worker.py` offers a model to `unload()` only if
`list_installed` said it was loaded, at all three of its eviction call sites,
so a permanently-`False` flag would make `unload` dead code. The belief can be
wrong in two cheap ways: ComfyUI may have evicted internally or been restarted
(costing one unnecessary, idempotent `/free`, and an optimistic chip on
`/inference/` until the memo expires), and a graph submitted from ComfyUI's own
web UI outside the queue is invisible to it — which is what the owner's
"governed path only" requirement exists to keep rare. The precise reading of
that flag is therefore **"believed resident — up to six hours since the last
run on this endpoint; ComfyUI may have evicted it internally"**, and the
operator-facing docs say so rather than letting the chip imply a live reading.
The single six-hour TTL is shared by both consumers of the memo on purpose: a
stale residency belief costs only a no-op `/free`, which the execution queue
tolerates by design, so splitting it into two lifetimes would buy nothing.

**One divergence worth naming, not fixing: the memo is per-process.** Every
generation is submitted by the worker process, never the web process:
`modules.vision.views.generate` only enqueues `vision.generate` (its own
docstring says so, and `test_a_valid_submission_is_queued_never_submitted_
directly` pins it) rather than calling `services.submit_job` itself, and
`submit_job`'s only non-test caller is `modules.vision.jobs.run_generate`,
which runs on the worker. So the residency belief (`_RunMemo`, stamped by
`ComfyUIGenerator.submit`) lives ONLY in the worker process that ran the
generation, and footprint learning is self-consistent there. `/inference/`
renders in the WEB process, which reads its own, separate in-memory
`_RUN_MEMO` — one that has never submitted anything for ComfyUI — so a
ComfyUI checkpoint's loaded chip on `/inference/` shows nothing today, no
matter how recently a generation ran. The belief's real consumer is
`console/jobs/worker.py`'s `_evict_to_match_plan`, which runs in the SAME
worker process that wrote the memo, so it stays self-consistent there;
`list_installed`'s `loaded` flag remains load-bearing for that eviction
path even though it never surfaces on `/inference/`. A shared store across
processes (a queue-side residency record) would be a platform decision, not
an adapter one.

**The memo also cannot survive a WORKER RESTART, and nothing here fakes
that it can (investigated 2026-08-25).** `_RUN_MEMO` is a bare module-level
`dict`, gone the instant the worker process that wrote it exits. Two
sanctioned persistence seams were checked and both ruled out rather than
reached for — Django's cache (defaults to per-process `LocMemCache`, no more
durable than `_RUN_MEMO` already is) and a small model owned by
`core/inference` (deliberately not a Django app; core purity means `core/`
holds no persistence of its own) — because faking durability with either
would be worse than the honest gap: a belief that LOOKS like it survived a
restart but silently didn't is a harder bug to notice than one that plainly
can't. So nothing was built here; on a cold memo, `list_installed` still
reports `loaded=False` for EVERY model at an endpoint, unchanged. The full
investigation, what that cold-memo behavior means for eviction safety, and
the recommended worker-side mitigation are written up in
`modules/vision/README.md`'s "Known limits" rather than repeated here —
this ADR is the engine adapter's own decision record, and the mitigation is
a `console/jobs` scheduling policy this branch does not own or touch.

**The `mps`-only reach of `/free`, in one line: see the barrier paragraph
above.** It is the reason `unload`'s settle threshold demands only half a
checkpoint's known footprint back, not the whole thing — full detail lives
there rather than a second time here.

**What this does not change.** `modules/vision/jobs.py`'s `plan_generate` still
declares `exclusive=True` unconditionally: whether a measured footprint should
relax that is a scheduler decision, and the execution queue owns it. Nor does
this reach cross-engine eviction — `worker._evict_to_match_plan` derives its
endpoint set from running jobs' own model refs, so an idle engine's warm model
is never visited. Both are the queue track's, and both are named here so
neither is mistaken for an oversight in this adapter.

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
and a megapixel budget are each honoured by at most one of the two graphs, so
none of them is offered. A control that silently does nothing on half the
models it is shown for is a lie about the platform, not a convenience. LoRAs
are the one apparent exception that turned out not to be one — see D-EDIT-9.

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

This also closes ADR 0013 §8's "per-job progress" seam for this module in
the same spirit ADR 0013's 2026-08-24 amendment records for the queue
generally: `vision.generate`'s wait loop is the first `JobKind` handler to
report through `JobContext`, threaded here because the EDIT graphs' first
cold load of the ordinary (non-distilled) build ran long enough (tens of
minutes, see the Live verification log) for a bare "still running" line to
read as a hang. It carries no governance
claim of its own — ADR 0014's media-ingestion governance items (transcription
exclusivity, the footprint-override escape hatch) are a parallel track, not
one this amendment revisits.

**D-EDIT-6 — a distilled build is a `variant` on the connection, not a
second family.** The operator declares `ModelConnection.config["variant"]`
alongside `family`/`text_encoder`/`vae`; the template registry stays keyed
by `(family, operation)`, and `flux2_edit.build` reads `config.get("variant")`
and builds the distilled wiring for the one value the module implements
(`"distilled"`). An absent, blank, or unrecognized variant builds the
undistilled graph — the behaviour every existing connection already has. Not
a second family key: `list_families` is the intersection of the engine's own
`CLIPLoader.type` combo with `comfyui_workflows.families()` (D-EDIT-2), so a
made-up family string could never be offered, and the distilled build
genuinely IS `flux2` to the engine (same loaders, same `type`, same latent
format, same VAE). The vocabulary comes from the template package, not the
engine — `comfyui_workflows.variants()` IS the whole list, republished
unchanged by `ComfyUIEngine.list_variants()` — because a variant names which
of a family's graphs a module has code for, not something ComfyUI reports.
Not validated on save, exactly like `family`: a declaration the adapter has
no graph for degrades quietly to the undistilled graph.

**D-EDIT-7 — `guidance` stays declared and stays wired; only its DEFAULT is
per-model.** `edit` keeps `guidance` and `steps` for every family and
variant — hiding one for a distilled model would still let a caller that
posts nothing get the SCHEMA's own default from `validate_params`, which
knows nothing about a model, so hiding would be less honest, not more.
Instead `ComfyUIEngine.param_defaults(operation_key, config)` reports the
connection's own opening values, and `services.live_defaults(operation,
resolved)` — the exact twin of `services.live_options`, following the same
precedent `list_choices`/`live_options` already set — merges them into
`operation_catalog`'s `param["default"]` and the create page's unbound form.
**The one real edge:** the catalog's `default` is where a FORM opens; a
caller that omits the param still gets the SCHEMA's own default from
`validate_params`, unchanged by this seam — a tool driving this operation
must SEND what it read from the catalog, never assume an omission reproduces
the catalog's own opening value.

**D-EDIT-8 — the registration surfaces learn `variant`.** `"variant"` joins
`_family_config`'s posted-key tuple (blank clears, absent keeps, same rule
the other three companions already follow); one `<select name="variant">` on
each form that already carries the family fieldset; `context["variant_options"]`
comes from the engine. The family/companion fields and the absent-vs-blank
config rule themselves were already landed work (the parent plan's
final-review fix round) — this only extends it by one field.

**D-EDIT-9 — a speed adapter is an ordinary LoRA, picked by the operator.**
`edit` splices in the existing `core.inference.operations.LORA_PARAMS`
(`loras` + `lora_strength`), and BOTH edit templates apply the selection
through one new shared fragment, `_fragments.model_lora_chain`, built on
`LoraLoaderModelOnly`. There is no "speed" toggle, no
few-step-distillation-shaped switch, and no automatic step/cfg change when
a particular adapter is chosen. This reverses D-EDIT-1's original `loras`
exclusion, honestly: that exclusion was scoped to the checkpoint-shaped
fragment (`_fragments._lora_chain`, which rewires CLIP as well as MODEL)
that genuinely could not serve `edit`'s graphs — not to the control itself.
Verified against a live ComfyUI 0.33.0:
BOTH bundled edit workflows contain a `LoraLoaderModelOnly` (MODEL in, MODEL
out, no `strength_clip` input) in the same relative position their family's
template already builds up to, so a model-only chain is exactly as honest
for `edit` as `_lora_chain` is for the checkpoint modes — one fragment, two
call sites, the same two params every other operation already offers.
**Why no automatic steps/cfg:** the platform would have to recognize which
adapter is a distillation adapter from its filename — the forbidden guess,
in the one place it would be most tempting. The operator picks the adapter
and sets the two numbers; `loras`' own description names the fact without
naming a file or a vendor. **Position in each graph follows each bundled
workflow:** the last model-wrapping step before the guider/sampler — right
after `components()` for `flux2` (which wraps nothing else before it), and
after `ModelSamplingAuraFlow` → `CFGNorm` for `qwen_image`. One strength for
the whole selection, unchanged from the checkpoint modes. The distilled
variant's own bundle itself ships no `LoraLoaderModelOnly` of its own — the
position chosen for it (straight after the loader) mirrors the ordinary `flux2`
workflow's `image_flux2.json`, since the two graphs otherwise share every
model-wrapping step up to that point.

**D-EDIT-11 — durations are derived, never stored; vision's clocks, not the
queue's.** Owner requirement 2026-08-25: every generation says how long it
waited and how long it ran, on the card, in the gallery, and in the tool API.
`GenerationJob.durations`/`.durations_display` compute `queued`/`processing`/
`total` from the three timestamps the row already carries
(`created_at`/`started_at`/`finished_at`) — no new column, no migration, and
no fourth number that could drift from the three it is derived from. This is
a *second* clock, deliberately: `console/jobs/models.py::InferenceJob` already
times the QUEUE job's own lifecycle (claimed/finished), and that measures
something different — how long the exclusive slot was held, which `run_generate`
already reports as `timed_out` when waiting (not generating) ran out of budget.
`GenerationJob`'s clock measures the generation itself, and is right whether or
not a queue job is involved at all (a management command, a test, a future tool
calling `services.submit_job` directly). `processing` is `None`, never `0`, for
a job that never started — an engine rejection waited and then failed, and
"ran for no time" is a different claim from "never ran". The run figure
INCLUDES loading the weights: `started_at` is stamped when ComfyUI reports the
prompt executing (`services.refresh_job`'s `running` transition, written
exactly once), and no HTTP surface it serves separates the load from the first
sampler step — a finer split is not available, not omitted.

**D-EDIT-11 amended (fix round, 2026-08-25) — a third clock the first cut got
wrong, and a fourth number to say it honestly.** The original text above
claimed `queued` covered "the ENGINE's queue plus this platform's" — false:
`created_at` on a `GenerationJob` row is stamped inside `services.submit_job`,
which the WORKER calls only after it has already claimed the platform's own
queue job, so the platform-side wait (enqueue to claim) never lands inside
`created_at` at all, and `queued` was always, only, the engine's own wait.
That platform wait was genuinely invisible — including on the pre-row queued
placeholder, which had no elapsed clock of its own. The fix adds `submitted`:
`InferenceJob.created_at` (the row this generation's own `queue_job_id`
names) to this row's own `created_at`. It is read, ONLY, through the
sanctioned `core.inference.queue.get_job` seam (`GenerationJob._submitted_at`,
a `cached_property`) — the same seam `modules.vision.views` already calls for
the identical queue job, never a `console.jobs` import, and no change to
`console/jobs/*` at all. `submitted` is `None` for three honest reasons this
does not distinguish further: no queue job at all, a queue row aged out of
the queue's own retention limit, or the queue being unreachable this instant
(`QueueUnavailable`, caught — a transient read failure here must never turn a
job card into a 500). Once knowable it is FIXED, never live: both ends are
already stamped by the time a `GenerationJob` row exists at all. `total` now
starts from `submitted` when it is known — the full, honest, end-to-end
figure the owner actually asked for — and falls back to `created_at`,
unchanged, when it is not. The pre-row queued placeholder
(`_queued_card.html`) gained its own elapsed clock from the same
`InferenceJob.created_at`, computed directly in `views._queue_card_context`
(no `GenerationJob` exists yet at that point, so there is nothing to attach
a `cached_property` to) — LIVE while the queue job is non-terminal, exactly
like the real card's own non-terminal numbers.

**D-EDIT-12 (R1, 2026-08-27) — `flux2` gets a `txt2img` template; a template
may declare `IGNORES`; a variant's defaults now have a family-level home
too.** Three related decisions, landed together for the R1 `flux2`
`txt2img` graph:

- **`comfyui_workflows/flux2_txt2img.py`, registered under
  `("flux2", "txt2img")`.** Its ORDINARY graph is derived node-for-node
  from this family's own ordinary Text to Image workflow, in ComfyUI's own
  `image_flux2_text_to_image.json` bundle, whose `FluxGuidance` widget (`4`)
  is exactly what `flux2_txt2img.DEFAULTS` transcribes. Its DISTILLED graph
  is derived node-for-node from the distilled Text to Image subgraph in the
  SEPARATE `image_flux2_klein_text_to_image.json` bundle — a genuine second
  subgraph in that file, sibling to that file's own ordinary Text to Image
  subgraph. That sibling
  (`CFGGuider` at `cfg=5` over an empty-text `CLIPTextEncode` negative, 20
  steps) is NOT what the ordinary graph above is drawn from, and this
  template does not build its shape at all — the same node-for-node
  derivation discipline `flux2_edit`'s own distilled graph already follows
  from ITS family's own distilled bundle (D-EDIT-6). Because a declared
  `variant` names one guidance-distilled BUILD of the family, and both
  templates' ordinary/distilled subgraph PAIRS turn out to build the
  identical shapes (`FluxGuidance`+`BasicGuider` ordinary,
  `ConditioningZeroOut`+`CFGGuider` distilled, the same `steps=4`/`cfg=1`
  widget values on the distilled side), the guider/negative wiring itself
  was PROMOTED into `_fragments.py`
  (`flux_guidance`/`basic_guider`/`zeroed_conditioning`/`cfg_guider`/
  `flux2_sigmas`/`flux2_sample`) and both templates now call the shared
  functions, rather than each deriving and writing out its own copy of the
  same graph shape. What genuinely differs from `edit`:
  `width`/`height`/`batch_size`/`sampler` are `TXT2IMG`'s own params, wired
  as plain values rather than the `GetImageSize` links `edit` reads off the
  picture being edited — there is no picture here, so `edit`'s
  size-from-input and pinned batch/sampler (`edit` declares neither
  `batch_size` nor `sampler`) do not apply.
- **A template may declare a module-level `IGNORES: dict[str, str]`,
  listed in `comfyui_workflows._IGNORES` and read through the new
  `ignored_params(operation_key, family)`.** `flux2_txt2img.IGNORES` names
  two `TXT2IMG` params neither `flux2` graph can honour: `negative_prompt`
  (no negative-prompt input on either graph — the distilled graph's
  "negative" is a fixed zeroed copy of the positive, not operator text)
  and `scheduler` (`Flux2Scheduler` has no scheduler input at all, same as
  `edit`, D-EDIT-1). This ANNOTATES a param the schema still declares —
  D-EDIT-7 still stands: nothing here narrows, adds, or removes a param,
  and `build` still never reads either key, the same silent-ignore
  behaviour `edit` already has for params it does not declare at all.
  `ignored_params` is a pure lookup (never raises, `{}` for an
  unregistered or clean pairing), the reader half of a fact nothing
  consumes yet — the console disabling those two fields, rather than
  silently accepting and dropping them, is deferred to the constant-form
  work.
- **`variant_defaults` gained a family-level home: `(family, "")` is now a
  real key in `_VARIANTS`, not a guaranteed `{}`.** Before this cut, `variant
  =""` short-circuited to `{}` unconditionally — "no variant declared, no
  defaults." `flux2`'s ordinary (non-distilled) graph has a widget fact of
  its own (`FluxGuidance.guidance = 4.0`, transcribed in
  `flux2_txt2img.DEFAULTS`) that is true regardless of which variant a
  connection later declares, so
  `_VARIANTS[("flux2", "")] = {"txt2img": {"cfg_scale": 4.0}}` now holds it,
  and `variant_defaults("txt2img", "flux2", "")` answers
  `{"cfg_scale": 4.0}` rather than `{}`.
  `comfyui_workflows.variants()` still excludes the empty key from what it
  reports — it is not a variant an operator picks, it is `_VARIANTS`' own
  place to hold a family's ordinary-build facts — so `ComfyUIEngine.
  list_variants()` and the registration form's variant options are
  unchanged. The distilled graph's own txt2img defaults
  (`flux2_txt2img.DISTILLED_DEFAULTS = {"steps": 4, "cfg_scale": 1.0}`) are
  the SAME numbers `flux2_edit.DISTILLED_DEFAULTS` already reports
  (`steps=4`, `guidance=1.0`) under `TXT2IMG`'s own key name — one
  guidance-distilled build, one set of honest numbers, spelled twice
  because the two operations name their CFG control differently.

**D-EDIT-13 (R2, 2026-08-27) — one constant input form.** `/vision/`
narrowed itself three ways — only the picked operation's params were
rendered, a mode the model could not run vanished from the nav with no
trace, and `img2img`/`edit` wore one merged name — and the owner read the
narrowing as lost function. Five decisions, landed together:

- **The page renders the UNION of every registered operation's params,
  always.** `services.union_params` de-duplicates by key, first
  declaration wins, in registration order — 20 fields, the same 20
  whichever mode is picked. Each is `enabled`, `unused` (this mode does
  not declare it), or `ignored` (this model's graph cannot honour it);
  the two disabled states carry HTML `disabled`, so the browser never
  submits them, and `required=False`, so nothing an operator was not
  allowed to answer can block the form. The union defines the SET and the
  ORDER; the PICKED operation's own `Param` defines each enabled field's
  rendering rule, which is why `steps` opens at 20 under `edit` and 25
  under `txt2img`. `_MERGED_OPERATION_KEYS`, `_merge_image_to_image`,
  `_display_label`, and `page_tabs` are retired, and the `<h1>` becomes
  the constant "Image generation" — owner ruling 2026-08-24(a)'s merged
  "Image to image" entry is superseded: two operations wearing one name
  was itself a narrowing, and the Operation select names each one.
- **The "this model ignores that param" fact travels engine → services →
  page, never a `comfyui_workflows` import.** D-EDIT-12 shipped
  `ignored_params` as a reader with no consumer; the new optional engine
  member `InferenceEngine.ignored_params(operation_key, config)` and its
  `getattr`-and-never-raise reader `services.live_ignored` are that
  consumer. `tools/vision` still learns nothing about which engine is
  bound. `services.fill_engine_blanks` supplies an ignored `"choice"`
  param's value between the bound form and `validate_params` — from the
  model's reported default, else the engine's first option — because a
  disabled widget submits nothing and the schema floor refuses a blank
  choice. An ignored `"text"` param (`negative_prompt`) is deliberately
  NOT filled: writing a value into the job record for text the graph
  never read would be a lie about what ran.
- **D-EDIT-7 still stands.** Nothing here narrows, adds, or removes a
  `Param`; `validate_params` is still the floor every caller shares, and
  the POST path still validates against the PICKED operation's own
  `build_form`. The union form is a RENDERING object and never validates
  a submission.
- **The catalog now lists unsupported operations, with a reason** —
  `GET /vision/operations/` gains `"supported"`/`"unsupported_reason"`
  per operation and `"ignored"` per param, and lists every registered
  mode rather than only the runnable ones. A deliberate reversal of
  "never tell a tool about a mode it cannot perform": saying why is more
  useful than silence, and a caller that read the array as "modes I can
  run" now filters on `"supported"`. The in-repo consumers are the page
  and `tools/vision/tools.py`.
- **`?operation=<key>` is the canonical URL; `op/<key>/` is a rendering
  alias.** The Operation select had to be a control inside the picker's
  existing GET form so that changing it reloads with no JavaScript — and
  a GET form can only produce a query parameter. The path route still
  serves the identical page with no redirect, so previously issued links
  keep working, and a path segment wins over the query key when both are
  present.

`Param.description` moved with the same reasoning: one copy of the text,
shown as an ⓘ tooltip on hover and keyboard focus rather than an
always-visible line under every field, and always kept in the DOM. The
CONTROL carries `aria-describedby` pointing at it (stamped on the widget
by `build_constant_form`), so a screen-reader user tabbing into a field
hears the description without ever landing on the ⓘ; the ⓘ points at it
too, for a pointer user. Situational copy
(a seed's "leave blank for a random seed", an asset field's "nothing
installed") still wins over the schema's sentence, exactly as before.

## Amendment (2026-08-29) — Unused fields render hidden; ignored fields stay visible with their reason

D-EDIT-13 gave every disabled field the same treatment: visible, dimmed,
with its reason inline. For a mode with few params of its own (Text to
image, one of the operations with the fewest), most of the constant
form's fields were `unused` — disabled controls the operator would never
touch, each explained anyway, crowding the page. The owner wanted them
gone from view, not merely explained.

`unused` and `ignored` now render differently. `unused` (the picked mode
does not declare the field) renders its `.field` wrapper with the HTML
`hidden` attribute, so the browser draws nothing. `ignored` (the mode
does want the field; the picked model's own graph can't honour it) keeps
rendering visible, dimmed, with its reason — that fact is worth surfacing
because switching models can change it, and it is not the mode's
decision. Both states still carry `disabled`, both are still
`required=False`, and — this is the part that does not change — the
label, widget, reason text, and `disabled` attribute are IDENTICAL markup
in both branches; `hidden` only stops the browser painting it. The form
remains the union (D-EDIT-13's first bullet still stands): a `hidden`
field's name still appears in the DOM exactly once, and a screen reader
or a stylesheet that overrides `hidden` still finds its reason.

### The content-agnostic stance, stated plainly

Any checkpoint the operator places in `ComfyUI/models/checkpoints/` (or the
equivalent directory for a future adapter) is a first-class model. The
platform performs no prompt or output filtering and no model inspection —
`list_installed` reports the checkpoint filename ComfyUI itself reports,
verbatim, and nothing downstream judges it. What to run is the operator's
decision. This is a design invariant, recorded here so a future contributor
does not read the absence of filtering as an oversight and "fix" it — it is
the same posture ADR 0010 §5 already states for models generally, restated
here because image generation is the capability where an operator is most
likely to run an uncensored community checkpoint on purpose.

## Deviations from the design spec

The implementation departs from the design spec in three places. Each was a
deliberate ruling made during execution, recorded here so the next reader
treats it as a decision rather than drift.

- **`discover(endpoints: dict[str, list[str]], connections)` takes a
  *list* per engine, not the spec's `set`.** Polling order must be
  deterministic — the default endpoint, then registered connections, then
  the endpoint currently being viewed — so a model seen at two addresses
  always attributes to the same one (the FIRST endpoint it was seen at,
  not whichever a set's iteration order happened to yield last), and so
  the console's rendered rows don't reshuffle between page loads for no
  reason a set's unordered iteration could explain. De-duplication is
  handled by `norm_endpoint` (`console/inference/discovery.py::_unique_endpoints`),
  which a Python `set` could not do anyway (`"http://x:8188"` and
  `"http://x:8188/"` are distinct set members despite meaning the same
  endpoint).
- **Engine-agnostic error copy replaces the spec's ComfyUI-named
  strings.** `modules/vision/services.py::preflight` reports
  `"The image engine ({resolved.engine}) at {resolved.endpoint} is not
  reachable."`, and the page's honest-state banners never say "ComfyUI";
  the lost-job message (`services.LOST_MESSAGE`, "The image engine no
  longer has this job") likewise names no product; the stale-job hint
  (`modules/vision/templates/vision/_job_card.html`, "Still queued — check
  that the image engine is running.") follows the same rule a third time.
  The design spec's §3 forbids the page from knowing which engine is
  bound, so the engine's own name is read out of the resolved binding
  (`resolved.engine`) rather than hardcoded into copy — the same
  information the spec's ComfyUI-named strings carried, but a second
  adapter needs no new strings to say it.
- **Validation has two layers.** `core.inference.operations.validate_params`
  is the schema *floor* every caller shares, including a future chatbot
  tool that builds no Django form at all — it coerces types, enforces
  min/max, resolves a blank seed, and rejects unknown keys.
  `modules/vision/forms.py::build_form` adds the engine's *live* option
  lists (samplers, schedulers, read from `list_choices`) on top, because
  the schema has no way to enumerate what a specific ComfyUI install
  actually reports. A `"choice"` param with no fixed `choices` therefore
  accepts any non-blank value at the schema layer; an option the engine
  doesn't recognize surfaces as an honestly failed job (ComfyUI's own
  rejection text via `GenerationRejected`) rather than the platform
  pretending to know every engine's sampler list ahead of time.

## Consequences

- img2img, inpaint, and upscale shipped exactly this way, each as one new
  `Operation` registration plus one new graph template — never a reshape of
  `modules/vision/` or the operation registry itself. Adding the next mode
  (ControlNet, say) touches the same three files:
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

  One caveat stands, and it is the honest limit of that promise: a
  `"choice"` param whose options an engine reports live needs a mapping in
  that engine's adapter (`comfyui.py::_CHOICE_INPUTS`) before the form can
  offer them. The `"asset"`-kind caveat is retired — D7's deferred widget
  shipped with the LoRA/upscale work: `modules/vision/forms.py::_field_for`
  renders an asset param as a select filled from
  `InferenceEngine.list_assets`, and an asset KIND an adapter cannot list
  reports nothing rather than raising (`comfyui.py::_ASSET_NODES`, where
  `"embedding"` is deliberately absent).
- **Card presentation, amended 2026-08-25.** The job card is four bands
  (head / body / notes / foot) with a two-column body — media left,
  `GenerationJob.facts` as a `<dl>` right — a `.chip` status pill from
  `templates/_shell.html`, and the SAME output actions the gallery
  offers, from the shared `vision/_output_actions.html`. What the card
  renders is still schema-driven; only its shape changed. The gallery
  caption keeps the compact dot-separated facts line: same partial,
  `layout` variable, one source.
- **`Param.description` reaches the form, amended 2026-08-25.** Every
  described param renders its sentence as the field's help text; a field
  that already carries SITUATIONAL copy (the seed's "leave blank", an
  asset kind with nothing installed) keeps it. Applied once in
  `forms._field_for`, so a new param kind inherits the behaviour.
- The adapter now keeps a SECOND piece of module-level state beside the
  `/object_info` memo: a bounded, TTL'd per-endpoint run memo holding the
  free-memory baseline `loaded_footprint` measures against and the residency
  `list_installed` reports. It still holds no bindings — endpoint and model
  are passed per call, never retained — and `clear_run_memo()` is its
  `clear_object_info_cache()`.
- A second image-generation engine (A1111/Forge, SwarmUI, InvokeAI, a
  hand-rolled diffusers service) is one new adapter implementing
  `InferenceEngine`/`ImageGenerator` and its own `SetupGuide` — `ENGINES`,
  `/inference/`, and `/setup/` all pick it up with no further change.
- A chatbot tool is simply a caller of `modules.vision.services`
  (`preflight`/`submit_job`/`refresh_job`/`wait_for`/`delete_job`) — the
  same seam the `/vision/` page itself calls, so the tool gets identical
  behaviour for free and the page holds no generation logic a tool would
  have to duplicate.
- **A dedicated `ModelConnection` column always wins over a same-named key
  in `config`.** D8's `config` JSONField and a per-connection column like
  `context_window` can both claim to answer the same setting; they are not
  merged. `console.inference.bindings.resolved_from_connection` seeds
  `ResolvedModel.config` from the JSON blob first, then applies the
  dedicated column's value *after*, so a blob key sharing a dedicated
  field's name is shadowed outright — it is silently overwritten, never
  combined, never an error. A multi-file model family's `config` (say, a
  multi-file checkpoint naming its companion CLIP/VAE files under D8) is
  safe from this only because none of those keys collide with a dedicated
  column's name today; a future dedicated column must not reuse a name an
  operator might already have written into `config`. This is pinned by
  `console/inference/tests/test_db_bindings.py::test_context_window_wins_over_a_same_named_config_key`
  and is the same rule the model-management track's ADR 0013 (queue)
  documents from the bindings side.
- Deferred from this cut, listed explicitly so they read as scoped-out
  rather than forgotten:
  - ControlNet — it needs a preprocessor story this cut doesn't open — and
    an embedding parameter UI: ComfyUI has no embedding loader node
    (`comfyui.py::_ASSET_NODES`) for an `"embedding"` asset param to bind
    to, so it would honestly report nothing.
  - Model families needing multi-file loaders — delivered 2026-08-25 for
    the two instruction-edit families this adapter has graphs for (`flux2`,
    `qwen_image`); see "Instruction-based editing across model families".
    A checkpoint-based family reusing D8's `config` column for a non-edit
    operation remains future work.
  - Per-job checkpoint override — delivered 2026-08-25; see "Instruction-based
    editing across model families", D-EDIT-4.
  - A background worker or batch runs, and WebSocket-driven progress
    (ComfyUI offers `/ws`; polling is sufficient at this scale).
  - Retention or quota for `data/generated/`.
  - The chatbot tool wrapper itself and per-tool toggles (the feature-flag
    pattern from D9 is the hook a future tool reuses).
  - A second engine adapter, as above.
  - **`/vision/`'s mutation endpoints (`generate/`, `jobs/<uuid>/delete/`)
    are unauthenticated.** This is the same Phase-1 gap `/inference/`
    already carries (ADR 0010's Consequences) — there is no Identity &
    Auth layer yet. It is explicitly in scope for Phase 2, not a new gap
    introduced by this feature.
