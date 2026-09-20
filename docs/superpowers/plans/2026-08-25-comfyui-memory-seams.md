# ComfyUI Memory-Governance Seams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the ComfyUI adapter answer the execution queue's two memory-governance questions — "how many bytes does this model occupy right now?" (`loaded_footprint`) and "release it" (`unload`) — so that a vision job stops being permanently unmeasurable, its real MPS-resident size lands on `/inference/` as a measured footprint, and a warm ComfyUI checkpoint can actually be evicted. Today `core/inference/engines/comfyui.py` implements neither seam, which is why the queue's own learning and eviction machinery is a no-op for every image-generation job.

**Architecture:** Four strictly sequential tasks on the `vision-generation` branch, confined to `core/inference/engines/comfyui.py`, `modules/vision/tests/*`, and docs. ComfyUI has no per-model residency endpoint at all (verified below), so the adapter earns its own answer: `ComfyUIGenerator.submit` stamps a per-endpoint "run memo" holding the free-memory reading taken immediately before the graph is queued, and `loaded_footprint` subtracts the reading taken after the run from it. That memo is the ONLY new state in the adapter, it sits beside the `_object_info` memo that already lives there, it is bounded and TTL'd, and it doubles as the adapter's residency belief — which is what makes `unload` reachable at all, because `console/jobs/worker.py` only ever calls `unload()` for a model its `list_installed` reported as `loaded`. `unload` is one POST to ComfyUI's `/free` with both flags set. Nothing in `console/jobs/*` or `core/inference/jobkinds.py` is touched; the queue track owns those.

**Tech Stack:** Python 3.12, plain `httpx` with per-call timeouts and no retries (the adapter's existing transport rule), `dataclasses` for the memo record, pytest with HTTP-layer doubles (`modules/vision/tests/_helpers.FakeComfyUI`) — no new dependencies, no DB work, no migrations, no async. ComfyUI 0.33.0 on the host at `http://localhost:8188`, PyTorch 2.13.0, MPS device on 48 GB unified memory.

**Spec:** `.superpowers/owner-requirements/memory-governance.md` (owner's binding requirements after the 2026-08-25 OOM crash) and `.superpowers/owner-requirements/comfyui-memory-seams-contract.md` (the interface contract agreed with the queue track). Both are reproduced in "Spec" below because they are the authority this plan answers to.

---

## Review history

- **r1 (adversarial plan-hygiene review) — AMEND: 6 findings + citation nits. All applied.** The delta measurement strategy and the residency deviation (`list_installed` reporting the run memo, beyond the brief's "exactly two methods") both survived review. Applied: (1) Task 4 Step 2's "other order" was a tautology — `pytest.ini`'s `testpaths` already IS `modules console scripts`, so the roots are now reversed to `console modules scripts`; (2) Task 1's commit boundary said "Steps 1–3" while `clear_run_memo` is created in Step 4 — now Steps 1–4; (3) the warm-re-run case was correct but untested and undocumented — added `test_a_second_run_of_a_warm_model_reports_none` and an ADR paragraph ("learned on the cold load; a warm re-run reports `None` and the recorded value stands"); (4) the single six-hour `_RUN_MEMO_TTL` serves two consumers — ORCHESTRATOR RULING: keep one TTL (a stale residency belief costs only a tolerated no-op `/free`, confirmed with the queue track) and make the chip honest instead, in both the ADR and a DEV.md operator note; (5) the per-process divergence (governed submits and their measurement share the worker process, while `/vision/` direct submits stamp the web process that renders `/inference/`) is now NAMED in the ADR, deliberately not fixed; (6) added `test_the_device_number_wins_over_the_host_number` pinning `_free_bytes`'s device-over-host preference. Also added the reviewer's verified fact that ComfyUI returns activation memory before the post-run read (`execution.py:769`'s `cleanup_models_gc()`, `main.py:438`'s `soft_empty_cache()`), which is why the delta settles on resident weights; and corrected the citations (`catalog.py:124`, `base.py:363`/`:372`, worker unload call sites `:807`/`:851`).

- **r2 (re-check) — AMEND: 1 finding. Applied; declared FINAL by orchestrator adjudication.** N1: the F4 DEV.md chip note contradicted the F5 ADR paragraph — it implied the `/inference/` chip shows whatever checkpoint last ran (with only ComfyUI-web-UI runs invisible) and attributed the unnecessary-release worst case to the chip. Corrected: the belief is per-process, so that page reflects generations started from `/vision/` directly while a queued generation's residency is known only to the worker that ran it; the ComfyUI-web-UI wording now reads as invisible-to-farabunker-entirely rather than as the sole exception; and the harmless-release worst case is attributed to the worker's own belief, which is what issues it.

---

## Spec

### Owner requirements (`memory-governance.md`), verbatim

> 1. **Footprint learning (owner's stated model):** a job whose model footprint is unknown is assumed to take 100% (exclusive); the queue MONITORS actual peak memory during the run and records the observed footprint for that model; subsequent admissions use the observed value. Footprints must reflect MPS reality (fp8 weights count at their bf16 upcast size).
> 2. **Eviction / one-warm-model policy:** the queue must be able to evict a warm model (ComfyUI /free, Ollama keep_alive) when the next admitted job doesn't fit alongside it — never allow two large models resident by accident.
> 3. **Model-affinity batching (owner, 2026-08-25):** because load/offload is expensive, the queue should BUNDLE jobs that need the same model — run same-model jobs consecutively even if submitted at different times and even if a job for a different model was submitted in between — subject to priority/fairness limits so a different model's job isn't starved forever.
> 4. **Governed path only:** agents/tools must never run raw engine workloads outside the queue (orchestrator discipline; a platform-side guard is welcome).
>
> Scope owner: queue track (console/jobs) for 1-3 scheduler semantics; vision track for ComfyUI footprint/unload adapter seams.

### The agreed contract (`comfyui-memory-seams-contract.md`), verbatim

> Contract to implement on core/inference/engines/comfyui.py (exact base-class signatures, no new ones):
> - loaded_footprint(endpoint: str, model_id: str) -> int | None — bytes the engine reports resident for that model NOW; None = unknown/not loaded/request failed; NEVER raises. MPS-aware: report the bf16-upcast-inclusive number (fp8 weights count at ~2x file size on Apple Silicon), not file size. If ComfyUI cannot attribute per-model residency, report total resident after the run keyed to the model just used (documented over-count).
> - unload(endpoint: str, model_id: str) -> bool — True only when the engine confirmed the free (POST /free {"unload_models": true, "free_memory": true}); bounded timeout (worker charges it against heartbeat margin); False on refusal/failure.
> - Do NOT touch console/jobs/* or core/inference/jobkinds.py.

This plan answers requirement 1's vision half and requirement 2's vision half. Requirements 3 and 4, and every scheduler-semantics decision, are the queue track's.

---

## Global Constraints

- **Never raise, from either seam.** Both methods are opportunistic facts, never operations that can fail their caller — the exact shape `core/inference/engines/base.py:363,372` documents and `ollama.py:291-337` implements. Every `httpx.HTTPError` degrades to `None` / `False`.
- **No logging in `core/`.** `grep -rn "getLogger\|^import logging" core/` returns NOTHING today, and `ollama.py`'s two seams log nothing at all — they return `None`/`False` silently and let the caller narrate. `console/jobs/worker.py` already logs both failure modes on the caller side (`"worker: loaded_footprint measurement raised for %s at %s"` at ~:597, `"worker: eviction unload refused for %s at %s (%s)"` at ~:808 and ~:852). **This is a deliberate deviation from the task brief's "log once per endpoint"**: adding the first logger in `core/` to do work the caller already does would break a real, uniform convention for no new information. If a reviewer disagrees, the correct place to revisit it is `core/inference/engines/base.py`'s seam documentation, not this adapter alone.
- **`core/` imports nothing from `console/` or `modules/`.** The one new import is `core.inference.catalog.norm_tag`, which is core→core and cycle-free (`catalog.py`'s only non-stdlib import is `core.inference.roles`).
- **No baked model names.** No test, docstring, or doc line pins a real checkpoint file the operator happens to own; tests use `"sdxl.safetensors"`-style stand-ins the double supplies.
- **Never-500.** A malformed `/system_stats` body, a `devices` list that is not a list, a 200 whose body is not JSON — every one of them degrades to `None`, never an exception.
- **HTTP-layer mocking only.** Tests patch `core.inference.engines.comfyui.httpx.get` / `.post` with `FakeComfyUI` methods; the engine's own methods are NEVER mocked away. This is stated in `modules/vision/tests/_helpers.py`'s module docstring and is not negotiable.
- **No `conftest.py` anywhere.** Shared helpers live in `modules/vision/tests/_helpers.py`; autouse fixtures are defined per module and delegate their bodies there. Class-level `@pytest.mark.django_db` only where a test actually touches the DB — **none of this plan's new tests need the DB**, so none of them should carry the marker.
- **Both-order safety.** The run memo is module-level state. `_helpers.reset_engine_caches()` must clear it, and every test module that patches `comfyui.httpx` already runs that as an autouse fixture (`test_comfyui_engine.py`, `test_comfyui_generator.py`, `test_services.py`, `test_jobs.py`). Extending the existing helper is what makes the whole suite order-independent for free.
- **Test invocation (ONE at a time, foreground):**
  `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest <path> -q`
- **Baseline:** the last recorded number is `1905 passed / 1 skipped`. **Re-measure it before Task 1 and use THAT as the floor** — the branch may have moved.
- **Tests and docs ship with every task.** TDD: failing test first, run it, watch it fail for the right reason, minimal implementation, run it again, commit.
- **Commit trailers**, both, on every commit:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: <this session's URL>
  ```
- **Branch-only.** No commits to `main`, no merges, no deploy. No migrations are expected; if a step appears to need one, STOP and report it.
- **FORBIDDEN files.** `console/jobs/*` and `core/inference/jobkinds.py` are the queue track's and must not be modified — not even a docstring. `docs/adr/0013` is read-only reference. If a task appears to require a change there, STOP and report it instead.
- **Do not run a generation and do not load a model while implementing.** The host crashed from memory pressure today. Everything in this plan is verifiable with `curl http://localhost:8188/system_stats` and the test suite. The single live end-to-end run is the ORCHESTRATOR's, after the branch is green.
- **Verification doctrine:** no "done"/"works"/"fixed" language about the seams until the orchestrator has seen fresh pixels — a measured-footprint row on `/inference/` — on the owner's live system.

---

## Verified facts (probe these yourself before trusting this plan)

Everything below was read from the live host and from ComfyUI 0.33.0's own source at `~/ComfyUI`, on 2026-08-24.

**1. `GET /system_stats` on this Mac** (`curl -s http://localhost:8188/system_stats`) returns:

```json
{"system": {"os": "darwin", "ram_total": 51539607552, "ram_free": 22714040320,
            "comfyui_version": "0.33.0", "python_version": "3.12.10 …",
            "pytorch_version": "2.13.0", "argv": ["main.py", "--listen", "0.0.0.0", "--port", "8188"]},
 "devices": [{"name": "mps", "type": "mps", "index": null,
              "vram_total": 51539607552, "vram_free": 22714105856,
              "torch_vram_total": 51539607552, "torch_vram_free": 22714105856}]}
```

**2. On MPS, every one of those free numbers is the same machine-wide number.** `~/ComfyUI/comfy/model_management.py:1748-1792`:

```python
def get_free_memory(dev=None, torch_free_too=False):
    if hasattr(dev, 'type') and (dev.type == 'cpu' or dev.type == 'mps'):
        mem_free_total = psutil.virtual_memory().available
        mem_free_torch = mem_free_total
```

So `ram_free`, `vram_free`, and `torch_vram_free` are all `psutil.virtual_memory().available`. There is **no torch-isolated allocation number on MPS** — nothing distinguishes ComfyUI's bytes from Chrome's. Three consequences the design must live with, and does:
   - **A total-resident-after-run reading is worthless here.** `ram_total - ram_free` on this machine right now is ~26 GB, almost all of it Chrome and Docker. Recording that as a checkpoint's footprint would pin every vision job to "needs 26 GB" forever.
   - **A delta is the only reading that can isolate the run**, and it is polluted by whatever else the machine did meanwhile.
   - **The MPS-awareness requirement is satisfied for free**: we measure real resident bytes, never file sizes, so an fp8 tensor upcast to bf16 shows up at its upcast size because that is the memory that actually disappeared.

**3. `POST /free` sets a flag; the prompt worker acts on it.** `~/ComfyUI/server.py:1192-1201`:

```python
@routes.post("/free")
async def post_free(request):
    json_data = await request.json()
    unload_models = json_data.get("unload_models", False)
    free_memory = json_data.get("free_memory", False)
    if unload_models:
        self.prompt_queue.set_flag("unload_models", unload_models)
    if free_memory:
        self.prompt_queue.set_flag("free_memory", free_memory)
    return web.Response(status=200)
```

`set_flag` (`~/ComfyUI/execution.py:1399-1402`) calls `self.not_empty.notify()`, which wakes the idle `prompt_worker` blocked in `q.get(timeout=…)` (`execution.py:1268-1273`); the loop then reaches `flags = q.get_flags()` (`~/ComfyUI/main.py:421-431`) and calls `comfy.model_management.unload_all_models()`. So the free is real and prompt even when nothing is running — but it happens **on another thread, after the 200 has already been sent**, and `unload_all_models()` (`model_management.py:2063-2065`) frees **every** model on **every** device, not one model.

**4. `console/jobs/worker.py` calls the seams like this** (read-only; quoted so the adapter matches the real call, not an assumed one):

```python
size = loaded_footprint(endpoint, norm_tag(model_id))          # ~:598
…
if not unload(endpoint, model.model_id):                        # :807 and :851
```

- `norm_tag` (`core/inference/catalog.py:124`, returning at `:138`) is `model_id if ":" in model_id else f"{model_id}:latest"`. A ComfyUI checkpoint name has no colon, so **the worker asks this adapter about `"sdxl.safetensors:latest"`, never `"sdxl.safetensors"`.** An implementation that keys on the raw name silently never matches. This is the single most likely way to ship a seam that does nothing.
- `unload` receives the **raw** `model.model_id` — whatever `list_installed` reported.
- `record_measured_footprint` is called with the **raw** `model_id` and matches `ModelConnection.model_id` exactly (`console/inference/bindings.py:336-343`), which is the checkpoint filename the operator registered from `list_installed`. One comfyui connection per (endpoint, checkpoint) ⇒ the "exactly one match" condition holds for our connections. Nothing on the adapter side needs to change for that.

**5. `unload()` is unreachable unless `list_installed` reports residency.** All three eviction call sites guard on the flag (`worker.py:745`, `:802`, `:845`):

```python
for model in installed:
    if not model.loaded:
        continue
```

`comfyui.list_installed` sets `loaded=False` for every checkpoint today. So adding `unload()` alone ships a method the worker will never call. This is why Task 2 also teaches `list_installed` what this adapter knows about residency — it is not scope creep, it is the difference between a working seam and dead code. See "Design → Residency" for the honesty rules that keeps.

**6. `ComfyUIGenerator.submit` is the only place a load is initiated**, and its tests mostly patch only `httpx.post` (`modules/vision/tests/test_comfyui_generator.py`, ~9 sites). Adding a `/system_stats` read to `submit` will make those tests attempt a REAL network call to `comfy.local:8188`. They will still pass (the read degrades to `None`), but a test suite that touches the network is a defect. Task 1 fixes those patches as part of its own work.

---

## Design

### Measurement strategy: baseline-at-submit, delta-after-run

`submit()` reads ComfyUI's free-memory number immediately before it POSTs the graph and stores it, with the model id, in a per-endpoint memo. `loaded_footprint(endpoint, model_id)` — called by the worker right after the job finishes — reads the number again and returns `baseline_free - free_now`.

Why this and not the contract's fallback ("total resident after the run, keyed to the model just used"): on MPS that total is machine-wide (fact 2), so it is not an over-count of the model, it is a reading of Chrome. The delta is the only reading that isolates the run at all. The contract's fallback was written for the case where ComfyUI reports a torch-only total; this host does not have one, and the plan says so rather than shipping a number that would make every vision job look like it needs 26 GB.

**What the number honestly is, and what it is not:**

- **It is an over-count of the checkpoint,** deliberately. The delta covers everything the run allocated and has not released: checkpoint weights, VAE, text encoder, whatever ComfyUI's caches held on to. That is the right answer for admission — the queue is asking "how much memory does running this model cost me", not "how large is this one tensor collection".
- **It is also polluted, in both directions, by other processes.** If Chrome grew during the run, we over-count (conservative: the job stays exclusive longer than strictly needed — harmless). If Chrome *shrank*, we under-count, and an under-count is the direction that can cause an OOM. Two guards, and one escape hatch:
  - `delta <= 0` ⇒ `None` (an impossible reading is not a measurement).
  - `delta < _MIN_CREDIBLE_FOOTPRINT` (256 MiB) ⇒ `None`. No image checkpoint anyone runs is that small, so a sub-256-MiB delta is noise, and `None` is strictly safer than a small number: the scheduler treats `None` as "unknown ⇒ runs alone", but would treat 200 MB as "fits alongside anything".
  - A residual under-count above the floor is still possible and is **documented, not hidden**. The operator's `footprint_override_bytes` (already on `ModelConnection`, already surfaced on `/inference/` as "Memory footprint override (GB)") outranks the measured value and is the stated remedy. The queue track's peak monitoring is the structural fix, and is theirs.
- **It is a post-run snapshot, not a peak** — exactly what the contract says this seam is ("Post-run snapshot, not peak"), and exactly what `loaded_footprint`'s own base-class docstring asks for ("bytes `model_id` occupies in memory at `endpoint` right now"). Sampling during the run to catch the peak would be a different quantity and is explicitly the queue track's. **Do not add polling to `status()` for this.**

**Rejected alternative — measure in `status()` / poll for a peak.** Cheap-looking, but it (a) adds an HTTP round trip per poll to every generation, (b) produces a peak, which is not what this seam is contractually for, and (c) lands the vision track inside the queue track's design. Rejected.

**Rejected alternative — derive the footprint from the checkpoint file size.** The contract forbids it in as many words, and it would be wrong on MPS anyway: an fp8 checkpoint upcasts to bf16 in memory, so the file size understates residency by roughly 2x — which is the exact miscalculation that preceded today's crash.

### The run memo

One module-level dict, `endpoint -> _RunMemo`, sitting beside the `_object_info` memo that already lives in this adapter and is already documented as "deliberately the ONLY state in this adapter". This adds a second, of the same nature: it holds no bindings, is keyed by the endpoint the caller passes, and clears on demand.

```
_RunMemo(model_key="sdxl.safetensors:latest",   # norm_tag'd at write time, so the
                                                 # worker's norm_tag'd read matches
         free_at_submit=22714040320,             # bytes free just before /prompt
         at=<time.monotonic()>,                  # for the TTL
         footprint=None)                         # filled in by the first successful
                                                 # loaded_footprint, so list_installed
                                                 # can report a size with its loaded flag
```

- **Keyed by `endpoint.rstrip("/")`** and nothing more. `core/` may not import `console.inference.discovery.norm_endpoint`, and it does not need to: both writers and readers get the endpoint from the same `ModelConnection.endpoint` string (`modules/vision/jobs.py:134` passes `resolved.endpoint`; the worker passes `ref["endpoint"]`), so they agree. A spelling that somehow disagrees yields `None` — a missed measurement, never a wrong one.
- **Bounded** at `_RUN_MEMO_MAX = 8` endpoints, oldest-inserted evicted first. Realistically one.
- **TTL `6 * 3600`s.** Long enough that no single generation outlives its own baseline (a big image-generation run is minutes, not hours); short enough that a memo left over from a ComfyUI that was restarted hours ago stops making residency claims. A ComfyUI restart is otherwise undetectable over HTTP — `/system_stats` exposes no pid and no uptime.
- **Cleared for the endpoint by a successful `unload()`**, because `/free` frees everything there.

### `unload`: what "confirmed" means here

`POST {endpoint}/free` with `{"unload_models": true, "free_memory": true}`, at a bounded `UNLOAD_TIMEOUT = 30.0` (the same number and the same reasoning as `ollama.UNLOAD_TIMEOUT`: too long for a health probe's 5s, far short of the 300s generation ceiling, and the worker charges this call against its heartbeat margin).

**A 2xx is the confirmation. We do not poll `/system_stats` afterwards to verify.** Rationale, from fact 3: `/free` returns 200 the instant the flag is set, and `unload_all_models()` runs afterwards on the prompt-worker thread. A follow-up read would therefore be racing a thread we cannot observe, and on macOS `psutil.virtual_memory().available` lags an actual free besides. A verify-then-report implementation would return `False` for frees that genuinely happened, and the worker logs every `False` as `"eviction unload refused"` — we would be manufacturing false alarms about correct behavior. Returning `True` on the 2xx is also exactly what the base-class contract asks for: *"Returns `True` only if the engine ACCEPTED the request -- not a guarantee the memory is already free by the time this returns."*

**`unload(endpoint, model_id)` frees every model at `endpoint`, not `model_id`.** ComfyUI has no per-model free. The `model_id` argument exists to satisfy the seam's signature and is unused; the docstring must say this plainly, because a caller that assumes per-model granularity would be wrong. In practice this is fine and even desirable: the worker only calls `unload` for a model the plan does NOT need, and by the time it does, the endpoint's needed model is either already resident (and will be reloaded — a cost, not a corruption) or not yet loaded.

### Residency: what `loaded` means for ComfyUI

`list_installed` reports `loaded=True` for exactly one checkpoint per endpoint: the one this adapter last submitted a graph for and has not since freed, within the memo's TTL. Its `loaded_size` is the measured footprint once one exists.

This is a **belief, not a reading** — ComfyUI has no residency endpoint — and it must be documented as one in the method's docstring. It can be wrong in two ways, both cheap:
- **Stale-positive** (ComfyUI evicted internally under its own memory management, or was restarted): costs one unnecessary `/free`, which is idempotent and harmless, plus a `loaded` chip on `/inference/` that is optimistic for up to the TTL.
- **Stale-negative** (someone submitted to ComfyUI from its own web UI, outside the queue): we simply do not know about it, exactly as today. Owner requirement 4 ("governed path only") is what keeps that case rare.

The alternative — leaving `loaded=False` forever, as today — is not "more honest", it is a guaranteed-wrong answer that also makes `unload()` dead code. Reporting what this adapter actually knows, and labelling it as such, is the honest option.

### Non-goals (deliberate, and named so nobody "fixes" them here)

- **Vision jobs do not stop being exclusive.** `modules/vision/jobs.py:114-138`'s `plan_generate` returns `exclusive=True` unconditionally. A measured footprint does **not** change that by itself, and this plan does not touch it: when to flip it is a scheduler-semantics decision the queue track owns (their budget/affinity work). The footprint is still immediately useful — it is what `/inference/` shows the operator, and it is what feeds `worker.py`'s `resident_sizes` budget arithmetic during eviction.
- **Cross-engine eviction** (a warm ComfyUI checkpoint evicted to make room for an Ollama job, or vice versa) does not follow from this work: `worker._evict_to_match_plan` derives its endpoint set from RUNNING jobs' own refs, so an idle engine's endpoint is never visited. That is a queue-track gap, and it is worth reporting to them — it is arguably the exact shape of today's crash.
- **Peak monitoring**, the observed-source label, affinity batching, per-endpoint budgets: queue track, after their T11.
- **ADR 0013** gets no amendment from this plan; its engine-seam section is the queue track's.

---

## Files

| File | Change |
| --- | --- |
| `core/inference/engines/comfyui.py` | Constants (`UNLOAD_TIMEOUT`, `_MIN_CREDIBLE_FOOTPRINT`, `_RUN_MEMO_TTL`, `_RUN_MEMO_MAX`), `_RunMemo` + `_RUN_MEMO` + helpers + `clear_run_memo`, `_free_bytes`, baseline capture in `ComfyUIGenerator.submit`, `ComfyUIEngine.loaded_footprint`, `ComfyUIEngine.unload`, residency in `ComfyUIEngine.list_installed` |
| `modules/vision/tests/_helpers.py` | `FakeComfyUI` gains real `/system_stats` fields and a `/free` route; `reset_engine_caches()` also clears the run memo |
| `modules/vision/tests/test_comfyui_engine.py` | `TestLoadedFootprint`, `TestUnload`, `TestResidency`, `TestQueueSeamDiscovery` |
| `modules/vision/tests/test_comfyui_generator.py` | Existing submit tests patch `httpx.get` as well as `.post`; new baseline-capture tests |
| `docs/adr/0012-image-generation-engine-adapter.md` | New dated decision section: the two seams, the delta strategy, the over/under-count semantics, the residency belief |
| `modules/vision/README.md` | Correct the now-false "No engine adapter reports a `loaded_footprint`" claim |
| `docs/DEV.md` | Operator note: where the measured footprint appears, that the budget is the operator's step; correct "ComfyUI releases its weights between runs" |

## Interfaces

- **Produces** (on `ComfyUIEngine`, discovered by `console/jobs/worker.py` via `getattr`, never by importing anything):
  - `loaded_footprint(endpoint: str, model_id: str) -> int | None`
  - `unload(endpoint: str, model_id: str) -> bool`
  Exact base-class signatures (`core/inference/engines/base.py:363` and `:372`). No new seam names, no keyword arguments, no extra parameters.
- **Produces** (module level, for tests and any caller needing current truth): `clear_run_memo() -> None`, the twin of the existing `clear_object_info_cache()`.
- **Changes** `ComfyUIEngine.list_installed` to populate `InstalledModel.loaded` / `.loaded_size` (both fields already exist, `base.py:26-32`; no dataclass change).
- **Consumes** `core.inference.catalog.norm_tag(model_id: str) -> str` — core→core, no cycle.
- **Consumes** ComfyUI 0.33.0 HTTP: `GET /system_stats`, `POST /free`.

---

## Dependency Table (strictly sequential — execute in this order)

| Task | Depends on | Why this order |
| --- | --- | --- |
| 1. Run memo + `loaded_footprint` | — | Creates the memo, `_free_bytes`, the double's `/system_stats` body, and the fixture reset that everything after it relies on. |
| 2. `unload` + residency | 1 | `unload` clears the memo Task 1 creates, and residency reports the footprint Task 1 measures. Without Task 1 there is nothing to clear and nothing to report. |
| 3. ADR 0012 amendment + README + DEV.md | 1, 2 | Documents the semantics both tasks settled; writing it earlier would document a guess. |
| 4. Full both-order suite + evidence note | 1, 2, 3 | Verification is last by definition. |

---

### Task 1: The run memo and `loaded_footprint`

**Files:**
- Modify: `core/inference/engines/comfyui.py`
- Modify: `modules/vision/tests/_helpers.py`
- Modify: `modules/vision/tests/test_comfyui_engine.py`
- Modify: `modules/vision/tests/test_comfyui_generator.py`

**Interfaces:**
- Produces: `ComfyUIEngine.loaded_footprint(endpoint, model_id) -> int | None`; `comfyui.clear_run_memo()`; module-private `_RunMemo`, `_RUN_MEMO`, `_memo_key`, `_run_memo`, `_remember_run`, `_free_bytes`.
- Produces: `FakeComfyUI.ram_total` / `.ram_free` / `.vram_free` (a full, real-shaped `/system_stats` body).
- Consumes: `core.inference.catalog.norm_tag`.

- [ ] **Step 0: Re-measure the baseline.** Run the full suite once and record the number. Do not assume `1905/1`.

```bash
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' \
  <repo>/.venv/bin/python -m pytest -q
```

- [ ] **Step 1: Give the double a real `/system_stats` body and a memo reset**

In `modules/vision/tests/_helpers.py`, extend `FakeComfyUI`'s fields (add after `upload_subfolder`, before `uploads`):

```python
    # `/system_stats` memory reporting. The defaults are the live host's
    # real numbers (48 GiB unified, ~21 GiB free) so a test that does not
    # care reads like the real machine. On MPS every free number ComfyUI
    # reports is the same `psutil.virtual_memory().available` value
    # (verified in ComfyUI 0.33.0's `model_management.get_free_memory`),
    # which is why `vram_free` defaults to `ram_free` rather than to a
    # second, independent number that could not occur on this hardware.
    ram_total: int = 51539607552
    ram_free: int = 22714040320
    vram_free: int | None = None

    # `POST /free` -- `free_calls` records each body posted.
    free_status: int = 200
    free_calls: list = field(default_factory=list)
```

Replace the `system_stats` branch of `get` with the full body:

```python
        if path == "system_stats":
            if not self.healthy:
                raise httpx.ConnectError("connection refused")
            device_free = self.ram_free if self.vram_free is None else self.vram_free
            return _Response(
                payload={
                    "system": {
                        "os": "darwin",
                        "ram_total": self.ram_total,
                        "ram_free": self.ram_free,
                        "comfyui_version": "0.33.0",
                    },
                    "devices": [
                        {
                            "name": "mps",
                            "type": "mps",
                            "index": None,
                            "vram_total": self.ram_total,
                            "vram_free": device_free,
                            "torch_vram_total": self.ram_total,
                            "torch_vram_free": device_free,
                        }
                    ],
                }
            )
```

And extend the class docstring's endpoint list with:

```
    - `ram_total`/`ram_free`/`vram_free` -> `GET /system_stats` memory fields
    - `free_status`/`free_calls` -> `POST /free`
```

Extend `reset_engine_caches()`:

```python
def reset_engine_caches() -> None:
    """Body of the `_reset_engine_caches` autouse fixture every test module
    that patches `comfyui.httpx` uses.

    The adapter memoizes `/object_info` for a few seconds (B6) AND keeps a
    per-endpoint run memo (the free-memory baseline `loaded_footprint`
    measures against, and the residency `list_installed` reports). Either
    one surviving between tests is exactly the kind of state that makes a
    suite pass in one order and fail in the other. Both are cleared before
    AND after each test, so a module that forgets the fixture is the only
    thing that can leak.
    """
    from core.inference.engines.comfyui import clear_object_info_cache, clear_run_memo

    clear_object_info_cache()
    clear_run_memo()
```

Run `test_comfyui_engine.py` and `test_comfyui_generator.py` — both must still pass; `clear_run_memo` does not exist yet, so this step is expected to fail at import until Step 4 creates it. That is fine: Steps 2–3 write the tests that name the behavior, Step 4 makes them pass. Do not commit a broken import; **Steps 1–4 land in one commit.**

- [ ] **Step 2: Write the failing tests**

Add to `modules/vision/tests/test_comfyui_engine.py`'s imports:

```python
from core.inference.catalog import norm_tag
from core.inference.operations import GenerationRequest
```

Add one module-level submission helper, after the `ENDPOINT` constant (these are the same param names and shapes `test_comfyui_generator.py`'s own `PARAMS` uses — read that file and keep them in step rather than inventing a second dialect):

```python
# The seam tests submit for real rather than poking the run memo: the
# memory baseline is stamped by `ComfyUIGenerator.submit`, and a test that
# writes the memo itself would be testing the test.
_SUBMIT_PARAMS = {
    "prompt": "a lighthouse", "negative_prompt": "", "width": 1024, "height": 1024,
    "steps": 25, "cfg_scale": 7.0, "seed": 99, "sampler": "euler",
    "scheduler": "normal", "batch_size": 1,
}


def _submit_once(fake, endpoint=ENDPOINT, model_id="sdxl.safetensors"):
    request = GenerationRequest(
        operation="txt2img", model_id=model_id, params=dict(_SUBMIT_PARAMS), client_ref="test"
    )
    with patch("core.inference.engines.comfyui.httpx.get", fake.get), \
         patch("core.inference.engines.comfyui.httpx.post", fake.post):
        ComfyUIEngine().build_image_generator(model_id, endpoint).submit(request)
```

Then add the test classes (no `django_db` marker — none of this touches the DB):

```python
class TestLoadedFootprint:
    """The queue's footprint-learning seam. ComfyUI has NO per-model
    residency endpoint (verified: `/system_stats` reports machine memory
    only, and on MPS every free number in it is the same
    `psutil.virtual_memory().available`), so the adapter measures the
    memory that disappeared across its own run rather than asking."""

    def test_none_before_anything_has_run(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",))
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_the_memory_that_disappeared_across_the_run_is_the_footprint(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 32_000_000_000  # 8 GB went somewhere during the run

        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            measured = ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest")

        assert measured == 8_000_000_000

    def test_the_worker_asks_with_a_tag_normalized_id(self):
        """`console/jobs/worker.py` calls `loaded_footprint(endpoint,
        norm_tag(model_id))`, and `norm_tag` appends `:latest` to any id
        without a colon -- which every ComfyUI checkpoint filename is. An
        adapter keyed on the raw name would silently never match."""
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 32_000_000_000

        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, norm_tag("sdxl.safetensors")) == 8_000_000_000

    def test_none_for_a_model_this_endpoint_did_not_run(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 32_000_000_000

        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "other.safetensors:latest") is None

    def test_none_for_an_endpoint_this_adapter_never_submitted_to(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 32_000_000_000

        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert (
                ComfyUIEngine().loaded_footprint("http://elsewhere:8188", "sdxl.safetensors:latest")
                is None
            )

    def test_a_noise_sized_delta_is_not_a_measurement(self):
        """Under-reporting is the dangerous direction: the scheduler treats
        `None` as "unknown, runs alone" but would treat 10 MB as "fits
        alongside anything". No image checkpoint is 10 MB, so this delta is
        another process, not a model."""
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 39_990_000_000  # 10 MB

        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_memory_freed_during_the_run_is_not_a_negative_footprint(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=32_000_000_000)
        _submit_once(fake)
        fake.ram_free = 40_000_000_000  # something else quit

        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_an_unreachable_engine_yields_none_and_never_raises(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)

        def failing_get(url, params=None, timeout=None):
            raise httpx.ConnectError("connection refused")

        with patch("core.inference.engines.comfyui.httpx.get", failing_get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_a_malformed_system_stats_body_yields_none_and_never_raises(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)

        def junk_get(url, params=None, timeout=None):
            from modules.vision.tests._helpers import _Response

            return _Response(payload={"devices": "not a list", "system": None})

        with patch("core.inference.engines.comfyui.httpx.get", junk_get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_a_stale_memo_stops_measuring(self):
        """A memo older than its TTL is no longer evidence that anything is
        resident -- ComfyUI may have been restarted, and `/system_stats`
        exposes no pid or uptime to detect that with."""
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 32_000_000_000

        with patch("core.inference.engines.comfyui.httpx.get", fake.get), \
             patch.object(comfyui, "_RUN_MEMO_TTL", -1):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_the_memo_is_bounded(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",))
        for port in range(8200, 8212):
            _submit_once(fake, endpoint=f"http://comfy.local:{port}")

        assert len(comfyui._RUN_MEMO) <= comfyui._RUN_MEMO_MAX

    def test_a_second_run_of_a_warm_model_reports_none(self):
        """The footprint is learned on the COLD load. A re-run of a model
        that is already resident re-stamps the baseline at the
        already-low free level, so almost nothing further disappears and
        the delta falls under the credible floor -- `None`, which the
        worker skips (`if not size: continue`), leaving the cold-load
        value standing on the connection. Reporting the warm delta
        instead would overwrite a correct 8 GB with a wrong 40 MB, which
        is the under-count direction that ends in an OOM."""
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)                       # cold load
        fake.ram_free = 32_000_000_000

        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") == 8_000_000_000

        _submit_once(fake)                       # warm re-run, baseline now 32 GB
        fake.ram_free = 31_960_000_000           # 40 MB of activations

        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_the_device_number_wins_over_the_host_number(self):
        """On MPS the two are the same value by construction, but on a
        discrete-GPU host they are separate pools and the device's is the
        one a checkpoint lives in. Pinned so a refactor cannot quietly
        start measuring host RAM on a machine where that is the wrong
        pool."""
        fake = FakeComfyUI(
            checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000, vram_free=8_000_000_000
        )
        _submit_once(fake)
        fake.vram_free = 2_000_000_000           # 6 GB of VRAM went to the model

        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") == 6_000_000_000
```

Add to `modules/vision/tests/test_comfyui_generator.py`, in the submit section:

```python
class TestSubmitStampsTheMemoryBaseline:
    """`loaded_footprint` measures against the free memory ComfyUI reported
    just before the graph was queued, so `submit` is where that reading is
    taken -- there is nowhere later that still knows the "before"."""

    def test_the_baseline_is_read_before_the_prompt_is_posted(self):
        fake = FakeComfyUI(ram_free=40_000_000_000)
        order = []

        def ordered_get(url, params=None, timeout=None):
            order.append(("get", url))
            return fake.get(url, params=params, timeout=timeout)

        def ordered_post(url, json=None, files=None, data=None, timeout=None):
            order.append(("post", url))
            return fake.post(url, json=json, files=files, data=data, timeout=timeout)

        with patch("core.inference.engines.comfyui.httpx.get", ordered_get), \
             patch("core.inference.engines.comfyui.httpx.post", ordered_post):
            _generator().submit(_request())

        assert order[0] == ("get", f"{ENDPOINT}/system_stats")
        assert ("post", f"{ENDPOINT}/prompt") in order

    def test_a_refused_graph_stamps_no_baseline(self):
        """Nothing loaded, so there is nothing to measure against."""
        fake = FakeComfyUI(prompt_status=400)

        with patch("core.inference.engines.comfyui.httpx.get", fake.get), \
             patch("core.inference.engines.comfyui.httpx.post", fake.post):
            with pytest.raises(GenerationRejected):
                _generator().submit(_request())

        assert comfyui._RUN_MEMO == {}

    def test_an_unreadable_system_stats_never_breaks_a_submission(self):
        """The measurement is opportunistic; the generation is not."""
        fake = FakeComfyUI()

        def failing_get(url, params=None, timeout=None):
            raise httpx.ConnectError("connection refused")

        with patch("core.inference.engines.comfyui.httpx.get", failing_get), \
             patch("core.inference.engines.comfyui.httpx.post", fake.post):
            engine_ref, _payload = _generator().submit(_request())

        assert engine_ref == fake.prompt_id
        assert comfyui._RUN_MEMO == {}
```

> Adapt `_generator()` / `_request()` to whatever that module's existing submit tests already use to build a generator and a request — **do not introduce a second construction helper.** Read the file first and reuse its own names and imports (it may need `from core.inference.engines import comfyui` and `from core.inference.engines.base import GenerationRejected` added to its imports).

Run both files and confirm they fail for the RIGHT reason (`AttributeError: … has no attribute 'loaded_footprint'` / `clear_run_memo`), not on a typo.

- [ ] **Step 3: Fix the existing submit tests to patch both transports**

`submit` now reads `/system_stats`. Every existing test that patches only `httpx.post` around a `submit()` call would otherwise attempt a real network connection.

```bash
grep -n "httpx.post" modules/vision/tests/test_comfyui_generator.py
```

For each site whose `with` block calls `submit()` and patches only `.post`, add the `.get` patch alongside it:

```python
        with patch("core.inference.engines.comfyui.httpx.get", fake.get), \
             patch("core.inference.engines.comfyui.httpx.post", fake.post):
```

(For the site that patches a custom `refuse` post, keep `refuse` and add `fake.get`.) `test_services.py:592-593` and `test_jobs.py:252` already patch both — leave them alone. Then re-run both files plus `test_services.py` and `test_jobs.py` and confirm nothing regressed.

- [ ] **Step 4: Implement**

In `core/inference/engines/comfyui.py`, extend the imports:

```python
from dataclasses import dataclass, replace
```

and, with the other `core.inference` imports:

```python
from core.inference.catalog import norm_tag
```

Add after `DISCOVERY_TIMEOUT`:

```python
# Below this, a free-memory delta is another process, not a checkpoint --
# the smallest image checkpoint anyone runs is an order of magnitude
# larger than 256 MiB. Reporting a noise-sized number would be WORSE than
# reporting nothing: `console/jobs/scheduler.py` treats an unknown
# footprint as "this job runs alone" (safe), but would treat 200 MB as
# "this model fits alongside anything" (an OOM).
_MIN_CREDIBLE_FOOTPRINT = 256 * 1024 * 1024

# `unload` is neither a health probe nor a generation. ComfyUI's `/free`
# handler only sets a flag and returns, but that POST can still queue
# behind an in-flight request on a busy server: `DISCOVERY_TIMEOUT` (5s,
# sized for a bare health probe) would false-negative under exactly that
# mild contention, while `DEFAULT_REQUEST_TIMEOUT` (300s) would leave the
# worker -- which charges this call against its heartbeat margin -- hanging
# on a wedged engine. 30s, the same number and the same reasoning as
# `ollama.UNLOAD_TIMEOUT`.
UNLOAD_TIMEOUT = 30.0

# How long a run memo is still evidence of anything. Long enough that no
# single generation outlives its own baseline (minutes, not hours); short
# enough that a memo left from a ComfyUI restarted hours ago stops
# claiming residency -- a restart is otherwise undetectable over HTTP,
# since `/system_stats` reports no pid and no uptime.
_RUN_MEMO_TTL = 6 * 3600.0

# At most this many endpoints are remembered at once (realistically one).
_RUN_MEMO_MAX = 8
```

Add after `clear_object_info_cache`:

```python
@dataclass(frozen=True)
class _RunMemo:
    """What this adapter last did at ONE endpoint.

    ComfyUI has no per-model residency endpoint: `/system_stats` reports
    the machine's memory and nothing else, and on MPS every free number in
    it is the same `psutil.virtual_memory().available` (verified in
    ComfyUI 0.33.0's `comfy/model_management.get_free_memory`). So the
    only way to learn what a checkpoint costs is to measure the memory
    that disappeared across a run this adapter itself started -- which
    means remembering the "before".

    Fields:
        model_key: `norm_tag`-normalized model id, so the worker's
            `loaded_footprint(endpoint, norm_tag(model_id))` call matches
            (a checkpoint filename has no colon, so `norm_tag` always
            appends `:latest` to it).
        free_at_submit: bytes ComfyUI reported free immediately before the
            graph was queued.
        at: `time.monotonic()` at submit, for the TTL.
        footprint: the measured delta, once `loaded_footprint` has taken
            one -- so `list_installed` can report a size alongside its
            loaded flag without re-measuring.
    """

    model_key: str
    free_at_submit: int
    at: float
    footprint: int | None = None


# endpoint -> the run this adapter last started there. The SECOND piece of
# module-level state in this adapter, of the same nature as
# `_OBJECT_INFO_CACHE` above: it holds no bindings (endpoint and model are
# still passed per call, never held), it is bounded, it expires, and tests
# clear it via `clear_run_memo`.
_RUN_MEMO: dict[str, _RunMemo] = {}


def clear_run_memo() -> None:
    """Forget every run memo. For tests, and for any caller that must see
    the engine's current state immediately."""
    _RUN_MEMO.clear()


def _memo_key(endpoint: str) -> str:
    """The run memo's key: the endpoint, minus a trailing slash.

    Deliberately NOT `console.inference.discovery.norm_endpoint` -- `core/`
    imports nothing from `console/`, and it does not need to here: every
    writer and reader gets this string from the same `ModelConnection
    .endpoint` (`modules/vision/jobs.py` hands `resolved.endpoint` to the
    generator; `console/jobs/worker.py` hands `ref["endpoint"]` to the
    seams), so they already agree. A spelling that somehow disagrees yields
    `None` -- a missed measurement, never a wrong one.
    """
    return endpoint.rstrip("/")


def _run_memo(endpoint: str) -> _RunMemo | None:
    """The live run memo for `endpoint`, or `None` if there is none or it
    has aged out (an expired entry is dropped on the way past)."""
    key = _memo_key(endpoint)
    memo = _RUN_MEMO.get(key)
    if memo is None:
        return None
    if time.monotonic() - memo.at >= _RUN_MEMO_TTL:
        _RUN_MEMO.pop(key, None)
        return None
    return memo


def _remember_run(endpoint: str, model_id: str, free_at_submit: int) -> None:
    """Record the baseline `loaded_footprint` will measure against.

    One entry per endpoint -- a new run at an endpoint replaces whatever
    was there, because ComfyUI loads the new graph's checkpoint and the
    old reading no longer describes anything. Re-inserting (rather than
    mutating) also keeps insertion order meaningful for the bound below.
    """
    key = _memo_key(endpoint)
    _RUN_MEMO.pop(key, None)
    _RUN_MEMO[key] = _RunMemo(
        model_key=norm_tag(model_id),
        free_at_submit=free_at_submit,
        at=time.monotonic(),
    )
    while len(_RUN_MEMO) > _RUN_MEMO_MAX:
        _RUN_MEMO.pop(next(iter(_RUN_MEMO)))


def _free_bytes(endpoint: str, timeout: float | None = None) -> int | None:
    """Bytes of memory ComfyUI reports free at `endpoint` right now, or
    `None` if it cannot be read or the body does not carry one.

    The device's own `vram_free` is preferred when there is one, falling
    back to the host's `ram_free`: on a discrete-GPU host those are
    genuinely different pools and the device's is the one a model lives
    in. On MPS they are the same number by construction -- ComfyUI's
    `get_free_memory` returns `psutil.virtual_memory().available` for both
    the `mps` and `cpu` devices -- so unified memory needs no special case
    here.

    Every failure mode degrades to `None`: an unreachable engine, a
    non-2xx, a 200 whose body is not JSON, and a body whose shape is not
    the one documented. This function feeds a measurement, and a
    measurement never fails its caller.
    """
    try:
        response = httpx.get(
            f"{endpoint}/system_stats",
            timeout=DISCOVERY_TIMEOUT if timeout is None else timeout,
        )
        response.raise_for_status()
        body = response.json() or {}
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(body, dict):
        return None

    devices = body.get("devices")
    if isinstance(devices, list) and devices and isinstance(devices[0], dict):
        free = devices[0].get("vram_free")
        if isinstance(free, int) and free > 0:
            return free

    system = body.get("system")
    free = system.get("ram_free") if isinstance(system, dict) else None
    return free if isinstance(free, int) and free > 0 else None
```

In `ComfyUIGenerator.submit`, read the baseline just before the POST and stamp it only once ComfyUI has accepted the graph:

```python
        inputs = self._upload_inputs(request)
        graph = get_template(request.operation)(request, self.model_id, self.config, inputs)
        payload = {"prompt": graph, "client_id": request.client_ref}
        # The "before" half of the footprint measurement (T-memory-seams).
        # Taken HERE because this is the last moment that still knows what
        # the machine looked like before this checkpoint was loaded --
        # `loaded_footprint`, called by the worker after the run, has only
        # the "after". One extra `/system_stats` round trip against an
        # engine we are about to hand a minutes-long job, and an
        # unreadable one costs the measurement, never the generation.
        free_before = _free_bytes(self.endpoint)
        response = httpx.post(f"{self.endpoint}/prompt", json=payload, timeout=self.timeout)
        if response.status_code >= 400:
            raise GenerationRejected(_rejection_message(response.json()))
        prompt_id = (response.json() or {}).get("prompt_id")
        if not prompt_id:
            raise GenerationRejected("ComfyUI accepted the request but returned no prompt_id.")
        if free_before is not None:
            _remember_run(self.endpoint, self.model_id, free_before)
        return str(prompt_id), payload
```

Extend that method's docstring with a sentence naming the baseline read, so the extra HTTP call is not a surprise to the next reader.

Add to `ComfyUIEngine`, after `build_image_generator` and before `build_llm`:

```python
    def loaded_footprint(self, endpoint: str, model_id: str) -> int | None:
        """Bytes `model_id` occupies at `endpoint` right now -- measured as
        the memory that DISAPPEARED across this adapter's own run of it,
        `None` when there is nothing honest to report.

        ComfyUI has no per-model residency endpoint: `/system_stats`
        reports the machine's memory and nothing else, and on MPS every
        free number in it is the same `psutil.virtual_memory().available`
        (ComfyUI 0.33.0, `comfy/model_management.get_free_memory`). So a
        "total resident now" reading would be a reading of the whole
        machine -- browser, containers, everything -- not of a checkpoint.
        `submit` therefore records what was free just before it queued the
        graph, and this subtracts what is free now.

        What that number honestly is: an OVER-count of the checkpoint,
        deliberately. It covers everything the run allocated and has not
        released -- weights, VAE, text encoder, whatever caches held on to
        -- which is the right answer for admission, whose question is
        "what does running this model cost me", not "how large is this one
        tensor collection". It is also MPS-honest by construction: it
        measures bytes that actually went missing, so an fp8 tensor
        upcast to bf16 counts at its upcast size, never at its file size.

        What it is not: isolated from the rest of the machine. Another
        process growing during the run inflates it (harmless -- the job
        stays exclusive longer than it needed to); another process
        shrinking deflates it, which is the direction that could
        under-admit into an OOM. Guarded twice -- a non-positive delta and
        anything below `_MIN_CREDIBLE_FOOTPRINT` are reported as `None`
        rather than as a small number -- and, beyond that, documented
        rather than hidden: `ModelConnection.footprint_override_bytes`
        (the operator's "Memory footprint override" field on
        `/inference/`) outranks whatever this measures.

        It is a post-run snapshot, not a peak -- which is exactly what
        this seam is for; peak monitoring belongs to the queue.

        Never raises: an unreachable engine, a malformed body, a model
        this endpoint did not run, an endpoint this adapter never
        submitted to -- all `None`, the same opportunistic-fact shape
        `InstalledModel.loaded_size` and `ollama.loaded_footprint` have.
        The scheduler already treats `None` as "unknown footprint, job
        runs alone" regardless of why.
        """
        memo = _run_memo(endpoint)
        if memo is None or memo.model_key != norm_tag(model_id):
            return None
        free_now = _free_bytes(endpoint)
        if free_now is None:
            return None
        delta = memo.free_at_submit - free_now
        if delta < _MIN_CREDIBLE_FOOTPRINT:
            return None
        _RUN_MEMO[_memo_key(endpoint)] = replace(memo, footprint=delta)
        return delta
```

- [ ] **Step 5: Run the tests, then commit**

```bash
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' \
  <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_engine.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' \
  <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_generator.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' \
  <repo>/.venv/bin/python -m pytest modules/vision/tests/test_services.py modules/vision/tests/test_jobs.py -q
```

Commit as `feat(vision): ComfyUI reports a measured footprint for the model it just ran`, both trailers.

---

### Task 2: `unload`, and the residency that makes it reachable

**Files:**
- Modify: `core/inference/engines/comfyui.py`
- Modify: `modules/vision/tests/_helpers.py` (`/free` route on the double)
- Modify: `modules/vision/tests/test_comfyui_engine.py`

**Interfaces:**
- Produces: `ComfyUIEngine.unload(endpoint, model_id) -> bool`.
- Changes: `ComfyUIEngine.list_installed` populates `InstalledModel.loaded` / `.loaded_size` from the run memo.
- Consumes: `POST /free`, `_RUN_MEMO` / `_run_memo` / `_memo_key` from Task 1.

- [ ] **Step 1: Teach the double `/free`**

In `modules/vision/tests/_helpers.py`, add to `FakeComfyUI.post`, immediately before the `if not url.endswith("/prompt")` guard:

```python
        if url.endswith("/free"):
            self.free_calls.append(json)
            return _Response(status_code=self.free_status)
```

- [ ] **Step 2: Write the failing tests**

Add to `modules/vision/tests/test_comfyui_engine.py`:

```python
class TestUnload:
    """The queue's eviction seam. ComfyUI frees ALL models at an endpoint
    or none -- there is no per-model free -- so this is deliberately a
    blunt instrument, and its docstring says so."""

    def test_it_posts_free_with_both_flags(self):
        fake = FakeComfyUI()
        with patch("core.inference.engines.comfyui.httpx.post", fake.post):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is True

        assert fake.free_calls == [{"unload_models": True, "free_memory": True}]

    def test_a_refusing_engine_is_false_not_an_exception(self):
        fake = FakeComfyUI(free_status=500)
        with patch("core.inference.engines.comfyui.httpx.post", fake.post):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is False

    def test_an_unreachable_engine_is_false_not_an_exception(self):
        def failing_post(url, json=None, files=None, data=None, timeout=None):
            raise httpx.ConnectError("connection refused")

        with patch("core.inference.engines.comfyui.httpx.post", failing_post):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is False

    def test_it_uses_the_bounded_unload_timeout(self):
        """The worker charges this call against its heartbeat margin, so it
        may never inherit the 300s generation ceiling."""
        seen = {}
        fake = FakeComfyUI()

        def recording_post(url, json=None, files=None, data=None, timeout=None):
            seen["timeout"] = timeout
            return fake.post(url, json=json, files=files, data=data, timeout=timeout)

        with patch("core.inference.engines.comfyui.httpx.post", recording_post):
            ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors")

        assert seen["timeout"] == comfyui.UNLOAD_TIMEOUT


class TestResidency:
    """`console/jobs/worker.py` only ever calls `unload()` for a model
    `list_installed` reported as loaded (three call sites, each guarded by
    `if not model.loaded: continue`). ComfyUI has no residency endpoint, so
    what this adapter reports is its own belief -- the checkpoint it last
    submitted and has not since freed -- and it is labelled as one."""

    def test_nothing_is_resident_before_anything_has_run(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors", "other.safetensors"))
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)

        assert [model.loaded for model in installed] == [False, False]

    def test_the_checkpoint_just_run_is_reported_resident(self):
        fake = FakeComfyUI(
            checkpoints=("sdxl.safetensors", "other.safetensors"), ram_free=40_000_000_000
        )
        _submit_once(fake)

        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            installed = ComfyUIEngine().list_installed(ENDPOINT)

        assert {model.model_id: model.loaded for model in installed} == {
            "sdxl.safetensors": True,
            "other.safetensors": False,
        }

    def test_a_measured_footprint_becomes_the_resident_size(self):
        """`worker._evict_to_match_plan` sums `loaded_size` over resident
        models for its budget arithmetic; without one, a resident model
        contributes zero."""
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)
        fake.ram_free = 32_000_000_000

        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            engine = ComfyUIEngine()
            engine.loaded_footprint(ENDPOINT, "sdxl.safetensors:latest")
            installed = engine.list_installed(ENDPOINT)

        assert installed[0].loaded_size == 8_000_000_000

    def test_a_successful_unload_ends_the_residency_claim(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)

        with patch("core.inference.engines.comfyui.httpx.post", fake.post):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is True

        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            engine = ComfyUIEngine()
            assert engine.list_installed(ENDPOINT)[0].loaded is False
            assert engine.loaded_footprint(ENDPOINT, "sdxl.safetensors:latest") is None

    def test_a_failed_unload_leaves_the_claim_standing(self):
        """Believing a model went away when it did not is the one error
        that could let two large checkpoints sit resident at once."""
        fake = FakeComfyUI(
            checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000, free_status=500
        )
        _submit_once(fake)

        with patch("core.inference.engines.comfyui.httpx.post", fake.post):
            assert ComfyUIEngine().unload(ENDPOINT, "sdxl.safetensors") is False

        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert ComfyUIEngine().list_installed(ENDPOINT)[0].loaded is True

    def test_a_stale_memo_stops_claiming_residency(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        _submit_once(fake)

        with patch("core.inference.engines.comfyui.httpx.get", fake.get), \
             patch.object(comfyui, "_RUN_MEMO_TTL", -1):
            assert ComfyUIEngine().list_installed(ENDPOINT)[0].loaded is False


class TestQueueSeamDiscovery:
    """`console/jobs/worker.py` finds both seams by `getattr` on the engine
    object it got from `get_engine(...)`, and calls `loaded_footprint` with
    a `norm_tag`-normalized id and `unload` with the raw one. This test
    walks that exact path, so a signature or naming drift fails here rather
    than silently disabling memory governance for every vision job."""

    def test_the_worker_s_own_call_shape_works_end_to_end(self):
        fake = FakeComfyUI(checkpoints=("sdxl.safetensors",), ram_free=40_000_000_000)
        request = GenerationRequest(
            operation="txt2img",
            model_id="sdxl.safetensors",
            params=dict(_SUBMIT_PARAMS),
            client_ref="test",
        )
        engine = get_engine("comfyui")

        with patch("core.inference.engines.comfyui.httpx.get", fake.get), \
             patch("core.inference.engines.comfyui.httpx.post", fake.post):
            engine.build_image_generator("sdxl.safetensors", ENDPOINT).submit(request)
            fake.ram_free = 32_000_000_000

            measure = getattr(engine, "loaded_footprint", None)
            unload = getattr(engine, "unload", None)
            assert measure is not None and unload is not None

            resident = [m for m in engine.list_installed(ENDPOINT) if m.loaded]
            assert [m.model_id for m in resident] == ["sdxl.safetensors"]
            assert measure(ENDPOINT, norm_tag("sdxl.safetensors")) == 8_000_000_000
            assert unload(ENDPOINT, resident[0].model_id) is True
```

- [ ] **Step 3: Implement**

Add to `ComfyUIEngine`, directly after `loaded_footprint`:

```python
    def unload(self, endpoint: str, model_id: str) -> bool:  # noqa: ARG002 - see docstring
        """Ask ComfyUI to release its loaded models at `endpoint` now.

        `model_id` is UNUSED, and that is the honest shape of ComfyUI's
        API rather than an oversight: `POST /free` with `unload_models`
        set calls `comfy.model_management.unload_all_models()`, which frees
        EVERY model on EVERY device at that endpoint. There is no
        per-model free to call. A caller assuming this evicts one
        checkpoint and leaves another warm would be wrong -- so the
        parameter stays (the seam's signature is fixed) and this docstring
        says what actually happens. In practice this is what the caller
        wants anyway: `console/jobs/worker.py` only calls this for models
        the admission plan does NOT need, and a needed checkpoint that
        gets caught in the sweep is reloaded on its next run -- a cost,
        never a corruption.

        `free_memory` is sent alongside `unload_models` because they free
        different things: `unload_models` drops model weights,
        `free_memory` also resets the execution cache holding intermediate
        tensors from the last run (ComfyUI 0.33.0, `main.py`'s
        `prompt_worker` flag handling). Eviction wants both.

        A 2xx IS the confirmation, and this deliberately does not poll
        `/system_stats` to double-check. ComfyUI's `/free` route only sets
        a flag on its prompt queue and returns 200 immediately
        (`server.py`); the actual `unload_all_models()` runs afterwards on
        the prompt-worker thread, woken by that flag. A verify-after-write
        would therefore be racing a thread it cannot observe -- and on
        macOS, `psutil.virtual_memory().available` (the number
        `/system_stats` reports) lags a real free besides. It would return
        `False` for frees that genuinely happened, and the worker logs
        every `False` as "eviction unload refused": manufactured alarms
        about correct behaviour. `True` here means what the seam's own
        contract says it means -- the engine ACCEPTED the request, not
        that the memory is already back.

        Uses `UNLOAD_TIMEOUT` (see that constant), never
        `DEFAULT_REQUEST_TIMEOUT`: the worker charges this call against
        its heartbeat margin. Any `httpx.HTTPError` -- refused connection,
        non-2xx, or a response slower than the timeout -- returns `False`,
        never an exception; the caller degrades to "didn't unload".
        """
        try:
            response = httpx.post(
                f"{endpoint}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=UNLOAD_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return False
        # Everything at this endpoint is gone, so the run memo -- this
        # adapter's only residency evidence -- is gone with it. Dropped
        # ONLY on success: believing a model went away when it did not is
        # the one error here that could leave two large checkpoints
        # resident at once.
        _RUN_MEMO.pop(_memo_key(endpoint), None)
        return True
```

Replace `list_installed`:

```python
    def list_installed(self, endpoint: str) -> list[InstalledModel]:
        """Every checkpoint ComfyUI can load at `endpoint`.

        `model_id` is ComfyUI's exact string and is OPAQUE -- a Windows
        host reports `subdir\\file.safetensors`, which must go back
        unchanged as `ckpt_name`. ComfyUI reports no size for a checkpoint
        on disk, so `size` stays `None` rather than being guessed.

        `loaded`/`loaded_size` are this adapter's OWN belief, not a
        reading: ComfyUI exposes no residency endpoint at all, so what is
        reported is "the checkpoint this adapter last submitted a graph
        for at this endpoint, and has not since freed" (see `_RunMemo`),
        with the footprint `loaded_footprint` measured for it if it has
        measured one. Reporting it matters -- `console/jobs/worker.py`
        only ever offers a model to `unload()` if `list_installed` said it
        was loaded, so a permanently-`False` flag would leave a warm
        ComfyUI checkpoint impossible to evict. The belief expires with
        the memo's TTL and is dropped by a successful `unload`; the ways
        it can still be wrong are cheap: an unnecessary (idempotent)
        `/free` if ComfyUI already evicted internally, and no knowledge at
        all of a graph someone submitted from ComfyUI's own web UI outside
        the queue.
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

- [ ] **Step 4: Run the tests, then commit**

```bash
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' \
  <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_engine.py -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' \
  <repo>/.venv/bin/python -m pytest modules/vision/tests -q
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' \
  <repo>/.venv/bin/python -m pytest console/inference/tests -q
```

`console/inference/tests` is run here because `list_installed` now returns rows that can carry `loaded=True`, and `console/inference/discovery.py:281-317` unions that flag into the console's installed rows. Nothing should regress (no test submits a graph before listing), but this is the assumption to check rather than assert.

Commit as `feat(vision): ComfyUI models can be evicted — /free seam plus the residency that reaches it`, both trailers.

---

### Task 3: ADR 0012 amendment and the operator-facing docs

**Files:**
- Modify: `docs/adr/0012-image-generation-engine-adapter.md`
- Modify: `modules/vision/README.md`
- Modify: `docs/DEV.md`

**Interfaces:** none — documentation only. No code changes in this task.

- [ ] **Step 1: Amend ADR 0012**

Insert this section into `docs/adr/0012-image-generation-engine-adapter.md`, after `### Engine-declared setup guides (owner requirement, 2026-08-23)` and before `### The content-agnostic stance, stated plainly` — verbatim, adjusting only the surrounding blank lines:

```markdown
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
re-stamps the baseline at the already-low free level, so the new delta is a
few tens of MB of activations, falls under the credible floor, and is reported
as `None` — which `console/jobs/worker.py` skips (`if not size: continue`),
leaving the cold-load measurement on the connection. This is deliberate:
overwriting a correct 8 GB with a wrong 40 MB is the under-count direction that
ends in an OOM.

**It is a post-run snapshot, not a peak.** That is what this seam is for by
agreement with the queue track, which owns peak monitoring.

**`unload` frees everything at the endpoint.** `POST /free` with
`{"unload_models": true, "free_memory": true}` calls
`comfy.model_management.unload_all_models()`, which releases every model on
every device there; ComfyUI has no per-model free, so the seam's `model_id`
argument is unused and its docstring says so. A 2xx IS the confirmation and
the adapter deliberately does not verify afterwards: `/free` only sets a flag
on ComfyUI's prompt queue and returns immediately, with the actual unload
running afterwards on the prompt-worker thread, so a verify-after-write would
race a thread it cannot observe — and `psutil`'s available-memory figure lags
a real free on macOS besides. Verifying would return `False` for frees that
genuinely happened, and the worker logs every `False` as "eviction unload
refused": manufactured alarms about correct behaviour. `True` therefore means
what the seam's own contract says — the engine ACCEPTED the request.

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

**One divergence worth naming, not fixing: the memo is per-process.** A queued
generation submits and is measured inside the worker process, so footprint
learning is self-consistent there. A generation submitted straight from
`/vision/` stamps the memo in the WEB process — which is also the process that
renders `/inference/`. So the loaded chip on that page reflects UNGOVERNED
submissions, while the measured footprints it displays come from governed ones.
Both facts are true and neither is wrong; they simply have different origins,
and a shared store for them would be a platform decision (a queue-side residency
record), not an adapter one.

**What this does not change.** `modules/vision/jobs.py`'s `plan_generate` still
declares `exclusive=True` unconditionally: whether a measured footprint should
relax that is a scheduler decision, and the execution queue owns it. Nor does
this reach cross-engine eviction — `worker._evict_to_match_plan` derives its
endpoint set from running jobs' own model refs, so an idle engine's warm model
is never visited. Both are the queue track's, and both are named here so
neither is mistaken for an oversight in this adapter.
```

Then add one line to that ADR's `## Consequences` section:

```markdown
- The adapter now keeps a SECOND piece of module-level state beside the
  `/object_info` memo: a bounded, TTL'd per-endpoint run memo holding the
  free-memory baseline `loaded_footprint` measures against and the residency
  `list_installed` reports. It still holds no bindings — endpoint and model
  are passed per call, never retained — and `clear_run_memo()` is its
  `clear_object_info_cache()`.
```


- [ ] **Step 2: Correct `modules/vision/README.md`**

The bullet at ~:293 is now false. Replace it, in full:

```markdown
- **Exclusive, always — for now.** No engine adapter reports a `loaded_footprint` for the
  `image-generation` capability yet, so every `vision.generate` job's model is
  `footprint_bytes=None` at claim time regardless, which `console/jobs/scheduler.py`'s
  admission rule already treats as "effectively exclusive" on its own. The planner declares
  `exclusive=True` explicitly rather than leaving that implicit — the honest, ruled-safe
  default until a real footprint measurement exists for this capability.
```

with:

```markdown
- **Exclusive, always — still, and deliberately.** The ComfyUI adapter DOES now report a
  measured footprint (`core/inference/engines/comfyui.py`'s `loaded_footprint`: the memory
  that disappeared across its own run of a checkpoint, over-counting on purpose, `None`
  when the reading is not credible) and can be asked to release it (`unload`, ComfyUI's
  `/free`) — see ADR 0012's "Memory-governance seams". `plan_generate` nonetheless still
  declares `exclusive=True` unconditionally: whether a measured footprint should relax that
  is a scheduler decision, not an adapter one, and it belongs to the execution queue's own
  budget work. What the measurement buys today is real regardless — it is what `/inference/`
  shows as the connection's **Last measured** footprint, and it is what
  `console/jobs/worker.py`'s eviction pass sums when deciding whether the machine is over
  its configured budget.
```

- [ ] **Step 3: Operator notes in `docs/DEV.md`**

Two edits.

First, in the ComfyUI section (~:569), **"ComfyUI releases its weights between runs" is
wrong** — ComfyUI holds a checkpoint resident until something frees it, which is precisely
why the seams exist. Replace the paragraph:

```markdown
**One GPU, two engines.** Ollama and ComfyUI both want VRAM. On a
single-GPU box, a large LLM held in memory can starve a generation (and
vice versa) — `ollama stop <model>` frees it, and ComfyUI releases its
weights between runs. Nothing in farabunker arbitrates this; it is a
hardware fact, not a policy.
```

with:

```markdown
**One GPU, two engines.** Ollama and ComfyUI both want VRAM. On a
single-GPU box (or an Apple Silicon machine, where GPU and system memory
are the same pool), a large LLM held in memory can starve a generation and
vice versa. Neither engine gives its memory back on its own: `ollama stop
<model>` frees Ollama's, and ComfyUI holds a checkpoint resident until
something asks it to let go.

For jobs that go through the queue, farabunker now does some of that
asking. After each generation it asks the ComfyUI adapter what the run
cost and records that against the connection; when a later admission needs
the room, it POSTs ComfyUI's `/free`. This is best-effort housekeeping,
not an arbiter: it only sees work the queue itself started, which is one
more reason to run generations through `/vision/` rather than ComfyUI's
own web UI.
```

Second, add this operator note to the same ComfyUI section, immediately after the paragraph
above:

```markdown
**Where the footprint shows up.** After the first queued generation with a
given checkpoint, that connection on `/inference/` grows a **Last
measured** row naming a size and the date it was measured. It is a
measurement, not a promise — it is taken by comparing free memory before
and after the run, so another busy application on the machine can skew it.
The **Memory footprint override (GB)** field on the same connection wins
whenever you know better.

**Setting the budget is your step.** A measured footprint changes nothing
about concurrency by itself: the `/queue/` memory budget ships unset,
meaning strictly sequential, and stays that way until you set it. Decide
how much memory you want the platform to use for concurrent model runs,
set it there, and the measured footprints become the numbers admission
does its arithmetic with.

**What the "loaded" chip means for ComfyUI.** For an Ollama model, that
chip on `/inference/` is a live reading from the engine. ComfyUI has no
equivalent to read, so for a ComfyUI checkpoint it means **believed
resident — up to six hours since the last run on this endpoint**.
farabunker is reporting a checkpoint it ran and has not since freed;
ComfyUI may have evicted it internally in the meantime, and a generation
started from ComfyUI's own web UI is invisible to farabunker entirely, so
it never appears there. The belief lives in whichever process ran the
generation, so this chip reflects generations started from `/vision/`
directly; a queued generation's residency is known to the worker that ran
it, not to this page. Nothing depends on the chip being exact: the release
requests that actually matter are issued by the worker off its OWN belief,
and the worst case there is one unnecessary (and harmless) request.
```

Finally, cross-reference it from the `/queue/` **Budget** bullet (~:339) by appending one
sentence to that bullet: `Engine-measured footprints (see "Install ComfyUI" for where they
appear) are what this budget is compared against.`


- [ ] **Step 4: Commit**

Documentation-only; no tests to run beyond confirming nothing else references the corrected claims:

```bash
grep -rn "loaded_footprint" docs/ modules/ --include="*.md"
grep -rn "releases its weights" docs/
```

Commit as `docs(vision): ADR 0012 amendment for the ComfyUI memory seams`, both trailers.

---

### Task 4: Full both-order verification

**Files:** none modified (unless a failure demands a fix, which is then part of this task).

- [ ] **Step 1: Full suite, default order**

```bash
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' \
  <repo>/.venv/bin/python -m pytest -q
```

Must be exactly `Task 1 Step 0's baseline + 27`, with zero failures. **27 new tests**: 16 in Task 1 (13 in `TestLoadedFootprint`, 3 in `test_comfyui_generator.py`) and 11 in Task 2 (4 `TestUnload`, 6 `TestResidency`, 1 `TestQueueSeamDiscovery`). That is the 25 the r1 review counted plus the two it asked for (`test_a_second_run_of_a_warm_model_reports_none`, `test_the_device_number_wins_over_the_host_number`). A different number means a test was dropped or duplicated — reconcile before reporting.

- [ ] **Step 2: Full suite, the other order**

```bash
DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' \
  <repo>/.venv/bin/python -m pytest console modules scripts -q
```

**The roots are REVERSED on purpose.** `pytest.ini` sets `testpaths = modules console scripts`, so naming those three roots in that order re-runs the identical collection and proves nothing; `console modules scripts` genuinely collects `console/` before `modules/`. That is the ordering the module-level run memo can be leaked across — if any test leaves it dirty, the two runs disagree. Same counts, zero failures.

- [ ] **Step 3: Confirm the forbidden files are untouched**

```bash
git diff --name-only main...HEAD
```

`console/jobs/*` and `core/inference/jobkinds.py` must NOT appear. If they do, stop and report.

- [ ] **Step 4: Confirm no test touches the network**

```bash
grep -n "httpx.post" modules/vision/tests/test_comfyui_generator.py
```

Every `submit()`-calling block must patch `httpx.get` as well.

- [ ] **Step 5: Hand the evidence to the orchestrator**

Report the two suite numbers, the diff's file list, and the expected live evidence below. **Do not run a generation, and do not use success language about the seams** — the live run is the orchestrator's.

---

## Expected live evidence (the ORCHESTRATOR's step, not this plan's)

After the branch is green and deployed, one governed `vision.generate` job through `/queue/` — not a raw ComfyUI run — should produce, in order:

1. **`/inference/`, the ComfyUI connection for the checkpoint that ran:** a **Last measured** row appears, showing a size and today's date, sourced from the engine (`_registered_connection.html` renders it from `connection.measured_footprint_bytes` / `measured_footprint_at`). Before this work that row could never appear for any ComfyUI connection.
2. **The size is plausible for the checkpoint** — the same order of magnitude as its resident cost on MPS, which for an fp8 model means roughly twice its file size, not its file size. A number in the tens of GB for a small baseline checkpoint means the run picked up another process's growth; a `None` (no row at all) means the reading was not credible and the job stayed exclusive — the safe failure, not a bug.
3. **`docker compose logs worker`** carries no `"loaded_footprint measurement raised"` and no `"eviction unload refused"` line for `comfyui`.
4. **Eviction** is only observable with a memory budget set on `/queue/` and two different checkpoints — worth doing once, but it is a separate exercise from evidence 1–3 and needs the queue track's budget work to be meaningful.

Success language about any of this waits until those pixels are on the owner's screen.
