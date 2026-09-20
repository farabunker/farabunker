# P4 — ADR 0015 and the doc sweep

**Date:** 2026-08-29
**Branch base:** `main` at `08cd21f` ("Merge pull request #64: vision hygiene batch")
**Phase:** the fifth and closing PR of the agents + tools programme (spec §12.5)
**Status:** plan, not executed

## Goal

Close the agents + tools programme with the record it is missing. P0–P3 shipped four
columns, a tool contract, a turn runtime, `/chat/`, and flows-as-rows; **no ADR describes
any of it**, and six documents still describe the pre-regroup tree. P4 writes ADR 0015,
appends the two cross-ADR amendments the new decisions require, and brings
`ROADMAP` / `ARCHITECTURE` / `DEV` / `README` / the column READMEs back into agreement
with the tree — so a reader who opens `docs/` learns what the platform actually is.

**Documentation only.** No behaviour change, no migration, no new test. The one place P4
could touch a settings file is `STATIC_ROOT` (T6), and T6's own ruling is *not to*.

## Architecture

The tree P4 documents, as it exists at `08cd21f`:

```
tools/       rag/  vision/  home/                        feature apps that register tools
models/      contracts/  registry/  queue/               engines, bindings, gateway, the queue
agents/      contracts/  runtime/  chat/                 the tool contract, the turn, /chat/
             + models.py defaults.py resident.py limits.py visibility.py
foundation/  format.py files.py  ops/  setup/  templates/   shared leaves + two feature-less apps
```

Four columns, Django app labels preserved (`rag`, `vision`, `home`, `inference`, `jobs`,
`ops`, `setup`, `agents`, `chat`). Import law in three rules: pure leaves are universally
importable; Django apps are column-private with two named exceptions; cross-column *work*
goes through a seam (the queue, the gateway, the tool registry) and never an import.

Nine tools are registered today, in four `AppConfig.ready()` bodies:
`tools/rag/apps.py:114-116` (`rag.search`, `rag.ask`, `rag.ingest`),
`models/registry/apps.py:60` (`models.status`),
`tools/vision/apps.py:75-76` (`vision.operations`, `vision.generate`, flag-gated),
`agents/apps.py:85,95` (`agent.library`, `agent.illustrator`, `flow.run`).

## Tech Stack

- Markdown under `docs/`, and the ADR house shape verified across `docs/adr/0010`,
  `0012`, `0013`, `0014`.
- `pytest` + `pytest-django` for the gate matrix (`pytest.ini:4` —
  `testpaths = tools models foundation agents scripts`).
- `manage.py check` for the one settings decision.
- `git` + `gh` for the PR; the live stack fast-forwards a `main` SHA
  (`docs/DEV.md` Rung 3, `:339-356`).

## Spec

- **Primary:** `docs/superpowers/specs/2026-08-25-agents-and-tools-design.md` — §12.5
  (`:2267-2304`) is this phase's content list; §1.2's honesty note (`:104-111`), §2.1
  (`:128-158`), §2.3 (`:173-182`), §3.3 (`:286-337`), §6.3 (`:1391-1430`), §9.3
  (`:1963-1984`), §14 (`:2385-2443`), the "Corrections from plan authoring" addendum
  (`:2517-2580`), and the "Long-term requirement: enterprise control and MCP interop"
  addendum (`:2582-2678`) are the sources ADR 0015 draws its decisions from.
- **The record of what shipped:** `docs/superpowers/plans/2026-08-25-agents-p0-regroup.md`,
  `…-p1-tool-contract.md`, `2026-08-27-agents-p2-runtime.md`,
  `2026-08-28-agents-p3-chat-and-flows.md` — specifically P3's "The deviations from the
  spec" table (`:62-84`), "Named gaps this phase opens" (`:85-90`), "What P3 does NOT
  touch" (`:91-100`), "Open mode, stated once and explicitly" (`:101-111`), and the
  `## Amendment (2026-08-28)` rulings (`:5008-5056`).
- **House shape:** `docs/adr/0013-inference-execution-queue.md` (602 lines) and
  `docs/adr/0014-media-ingestion.md` (1070 lines) in full; `docs/adr/0010`'s and `0012`'s
  amendment sections.

## Global Constraints

- **Docs-only.** No behaviour change, no migration, no new test. Two `.py` files are
  touched and both edits are *comments*: `tools/rag/tests/test_tools.py`'s module
  docstring (T5) and — only if T6 is overruled — `config/settings.py`. Any step that
  wants to change executable code is out of scope and comes out.
- **No model names or versions, anywhere, in anything P4 writes.** The repository is going
  public. Say "the chat model", "the vision model", "a distilled variant"; engine names
  (Ollama, ComfyUI, whisper.cpp) are fine because they are software, not weights. ADR
  0010's third amendment (`docs/adr/0010-model-management-framework.md:303-393`) already
  forbids the platform naming a model for the operator; this is the documentation half of
  the same rule. **Pre-existing model names elsewhere in `docs/` are not P4's to scrub,
  except inside ROADMAP Phase 1.55, which IS scrubbed in T3** — see amended D11.
- **Every citation verified against the tree at `08cd21f` before it is written.** The spec
  cites pre-regroup paths throughout (`modules/rag/…`, `console/jobs/…`,
  `core/inference/…`); every one of them has moved, and P4 names the post-regroup path. A
  citation that does not resolve is a signal to re-read, never to guess.
- **The gate matrix — every task, no exceptions.** Two `FARABUNKER_FEATURES` states × two
  collection orders:

  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q                                            # configured collection order
  .venv/bin/pytest -q scripts agents foundation models tools     # reversed
  ```

  **One run at a time** — parallel runs collide on the test database. For a task whose
  diff is `.md` only the four runs are still required before its commit, because doc text
  is quoted inside test docstrings and a doc-link script can be wrong about a path a test
  proves exists. `pytest.ini` is **not** edited this phase.
- **If T6 is overruled and `STATIC_ROOT` is added**, that is the one settings edit and it
  additionally needs `python manage.py check` clean plus the full four-run gate before the
  commit. Nothing else in P4 may touch `config/`.
- **`foundation/ops/tests/test_docs_sync.py` polices `docs/OPERATIONS.md` only** — it
  asserts four Python constants appear verbatim in that file (`test_docs_sync.py:16`,
  `:23-38`). P4 edits no constant it names and no line of `OPERATIONS.md`, so no P4 doc
  claim is under an existing guard, and **P4 adds no test.** See Deviations D5.
- **`docs/adr/` is excluded from every path sweep, except the two files T2 appends to.**
  An ADR records what was decided *at its date*; rewriting `console/jobs/` to
  `models/queue/` inside ADR 0013's body would falsify the record it exists to keep. The
  same exclusion covers `docs/superpowers/` (specs and prior plans are dated artifacts).
  Where a stale path inside an ADR would actively mislead, the fix is an **amendment**
  saying so — which is exactly what T2 does for ADR 0013's `## See also` (M8).
- **One commit per task**, message shaped `docs(p4): …`, each independently reviewable.
  Nothing is pushed until T7's ladder; nothing merges into the live `/app` checkout except
  a `main` SHA (`docs/DEV.md:347-356`).

---

## Coverage against the spec

Every §12.5 bullet, and every "ADR 0015 must…" sentence in the spec, mapped to a task.

| Spec | Requirement | Task |
|---|---|---|
| §12.5 bullet 1 | `docs/adr/0015-<slug>.md` in the house shape: `# ADR NNNN — Title`, `**Status:** Accepted`, `**Date:**`, `## Context`, `## Decision` in numbered assertive-claim subsections, `## Consequences`, a named-gaps section, `## See also` | **T1** |
| §12.5 bullet 2 | Amendment appended to ADR 0010: `session_id` retirement (§2.3), import-law restatement (§3.3), `platform/` naming re-confirmed rather than reversed | **T2** (session_id part: see D2) |
| §12.5 bullet 3 | Amendment appended to ADR 0013 stating the inline-tools rule (§6.3), following the `0013:402-407` both-sides precedent | **T2** |
| §12.5 bullet 4 | `docs/ROADMAP.md` Phase 1.6 rewritten: `modules/chat` → `agents/`, ChatSession-reuse bullet replaced | **T3** (mostly already landed — see D4) |
| §12.5 bullet 5 | `docs/ROADMAP.md` Phase 1.55 gains R1 and R2 | **T3** (reconciled to `[x]`, not added as open — see D1) |
| §12.5 bullet 6 | `docs/ARCHITECTURE.md` §1/§5 rewritten for four columns; "Modules today" → "Tools today"; Core services table gains an "Agent runtime" row | **T4** |
| §12.5 bullet 7 | `docs/DEV.md` §7/§8 updated for the new `testpaths` and the `/chat/` smoke path | **T5** |
| §12.5 bullet 8 | `README.md`, `tools/rag/README.md`, `tools/vision/README.md`, `models/registry/README.md`, `foundation/setup/README.md` path updates; new `agents/README.md` | **T4** (root), **T5** (column READMEs — see D3) |
| §12.5 closing line | "check what `test_docs_sync.py` asserts before writing" | **T5** step 1 (see D5) |
| spec `:108-111` (§1.2) | "ADR 0015 must therefore **state this as a new decision**, not cite it as an existing one" — ADR 0013 never forbids nested enqueue; the deadlock is implied, not stated | **T1** Decision 5, **T2** the 0013 amendment |
| spec `:179-182` (§2.3) | "ADR 0015 must therefore amend ADR 0010 on this point, not merely cite it" | **T2** (already partly landed — D2) |
| spec `:1393` (§6.3) | "This is the new decision ADR 0015 must state" — tools run inline; a tool may enqueue but never wait | **T1** Decision 5 |
| spec `:1791` (§7.6) | The `ChatSession`/`ChatMessage` drop is "recorded in ADR 0015 and in `docs/OPERATIONS.md`" | **T1** Decision 13; `OPERATIONS.md` — see D12 |
| spec `:1965` (§9.3) | "Hosted API engines are **unbroken ground with a strong prior against them**, and ADR 0015 must say so rather than cite ADR 0010 as precedent" | **T1** Decision 14 |
| spec §14 (`:2385-2443`) | Seventeen named gaps read as scoped-out, not forgotten | **T1** named-gaps section |
| spec addendum (`:2604-2649`) | The six design consequences P2 honoured; the post-P4 order Identity & Auth → MCP edge → tenancy | **T1** Decisions 9/10/11, **T3** step 4 |
| P2/P3 ledger deferrals | `STATIC_ROOT`; `docs/DEV.md`'s stale snippet; `plan_turn` over-declaration; the vision-side helper duplication | **T6**, **T5**, **T1** named gaps |
| *(no §12.5 bullet — doc-sweep scope found against the tree)* | Three stale claims §12.5 does not name, found by the repo-wide sweep: `docs/BUSINESS.md:173` ("the `modules/` split"), `tools/README.md:20` ("once P1 lands them"), `foundation/README.md:27-33` (rule 2 contradicting itself in two sentences) — plus `foundation/README.md:41`'s unqualified collectstatic claim (M5) | **T5** |
| *(no §12.5 bullet — owner amendment, 2026-08-29)* | **`docs/EXTENDING.md` — "Adding a tool in three steps"**, a worked walkthrough for someone new to the repo, using the real `models.status` tool as the example. ADR 0015 records *what* the contract is; this records *how a person uses it*, which no document does today | **T5** |

---

## Deviations from §12.5

Recorded here rather than argued in each task. Where §12.5 (written 2026-08-25, before any
phase executed) disagrees with the tree at `08cd21f`, **the tree wins**.

**D1 — R1 and R2 are RECONCILED, not added as open.** §12.5 says add two new `[ ]` items
to ROADMAP Phase 1.55. They are already there and already `[x]` (`docs/ROADMAP.md:141-158`
for R1, `:159-182` for R2), shipped by the vision track on 2026-08-27 and live-verified.
P4 does not re-open them. It replaces their bodies with the owner-supplied closure text
(T3 step 2), which also removes several model names from those two bullets as a side
effect of the rewrite.

**D2 — ADR 0010's `session_id` amendment already landed in P2.** §12.5 asks P4 to append
it. `docs/adr/0010-model-management-framework.md:282-293` already carries
"**Amendment (2026-08-27, agents P2).**" inline inside the RAG-as-tool bullet, correcting
both the moved path and the deleted parameter. P4's 0010 amendment therefore covers the
import law and the naming re-confirmation, and **points at** the existing inline note
rather than restating it — two records of one decision that can drift apart is exactly
what an amendment is supposed to prevent.

**D3 — most of §12.5's README list is already correct.** `agents/README.md`,
`agents/contracts/README.md`, `agents/runtime/README.md`, `agents/chat/README.md`,
`models/README.md`, `models/registry/README.md`, `models/contracts/README.md`,
`tools/README.md`, `tools/rag/README.md`, `tools/vision/README.md`,
`tools/home/README.md`, `foundation/README.md`, `foundation/setup/README.md` all exist and
were written or updated by P0–P3. P4 **verifies** them rather than rewriting them, and
edits exactly two: `tools/README.md:20`'s "(once P1 lands them)" and
`foundation/README.md:27-33`'s self-contradicting rule-2 sentence.

**D4 — ROADMAP Phase 1.6 was rewritten by P2 and P3, not by P4.** §12.5's
"`modules/chat` → `agents/`" and "the ChatSession reuse bullet (`:245-250`) replaced" both
already happened (`docs/ROADMAP.md:277-311`). P4's Phase 1.6 work is narrower: the
`v1 — grounded conversation` bullet's honest status (T3 step 3), the ADR 0015 link, and
the three-phase tail's cross-reference.

**D5 — `test_docs_sync.py` polices nothing P4 writes.** §12.5 says it "will police some of
this automatically; check what it asserts before writing". Checked:
`foundation/ops/tests/test_docs_sync.py:16` reads only `docs/OPERATIONS.md`, and its four
assertions (`:23-38`) are that `DOCUMENTED_BACKUP_SEQUENCE`, `RESTORE_DB_SEQUENCE`,
`RECOVERY_SEQUENCE`, and `LIVE_POSTGRES_WARNING` appear verbatim in it. No README count,
no ADR text, no path list is under any guard. **P4 therefore adds no test**, and says so
rather than inventing one to satisfy a sentence that turned out to be wrong.

**D6 — the ComfyUI memory-seam gap as briefed is false.** The brief for this plan records
"ComfyUI engine implements neither `loaded_footprint` nor `unload`". Both are implemented:
`models/contracts/engines/comfyui.py:731` and `:827`, landed by the 2026-08-25
comfyui-memory-seams work. What is *actually* true, and what ADR 0015 records instead:
`unload` frees **every** model at the endpoint, not one (`comfyui.py:827-846` — there is
no per-model free in ComfyUI's API), and `loaded_footprint` is a deliberate over-count
measured as a memory *delta* across the adapter's own run (`comfyui.py:731-760`). So the
queue **can** evict a ComfyUI resident, but never selectively, and its footprint number is
an admission-shaped answer rather than a checkpoint size.

**D7 — the `agents/runtime/delegate.py` "wrong exception" item is not reproducible.**
`resolve_chat` documents "Raises `ValueError` for an unbound role or an unknown/non-chat
pk" (`agents/runtime/bindings.py:41`), the gateway raises `ValueError`
(`models/contracts/gateway.py:134`, `:169`), and `delegate.py:116` catches `ValueError`.
The comment at `delegate.py:99-112` names the right one. **No edit.**

**D8 — `snapshot_tools`/`restore_tools` are NOT ported.** `tools/rag/tests/_helpers.py:116`
and `:128` do duplicate what `tools/vision/tests/_helpers.py:27`'s `isolated_tool_registry`
fixture does. Porting them into one shared helper would mean `tools/rag` importing from
`tools/vision` — a **cross-column** test-helper import, which P3's Global Constraints
forbid outright ("the cross-COLUMN rule is unchanged and still pinned") and which P2's
rule ("`_helpers.py` modules are duplicated per app, never imported across apps") already
covered. The duplication is the rule working, not the rule failing. **Recorded as a named
gap in ADR 0015 and left in place.**

**D9 — the `tools/rag/tests/test_tools.py` note is stale and gets corrected, not ported.**
Its module docstring (`test_tools.py:1-29`) describes a real leak: `tools/vision/services.py`
doing `from models.contracts.bindings import resolve` at module scope, binding the name
once so a later `patch("models.contracts.bindings.resolve")` could not reach it. **PR #64
fixed that on the vision side** — `services.py:26` now imports the *module*
(`from models.contracts import bindings`) and calls `bindings.resolve(...)` at
`services.py:171`, looked up per call. Cite `:26` only: `:27` is still a by-name import
(`from models.contracts.bindings import ResolvedModel, config_family`), and those two names
are a dataclass and a helper nobody patches, which is why they were left alone. The
docstring's premise no longer holds. T5
rewrites the note to say what is true now. The `import tools.vision.services` pin at
`test_tools.py:36` **stays**: removing it would be a change to test behaviour, which a
docs-only phase does not make.

**D10 — `STATIC_ROOT` is deliberately NOT added.** Nothing in the repository runs
`collectstatic`: `Dockerfile:32`'s `CMD` is `migrate` + `uvicorn`, no `RUN` line collects,
and `compose*.yaml`, `deploy/`, `scripts/`, and `config/` contain no occurrence of the
word. Adding a value nothing collects into documents an operation the platform does not
perform. T6 records the ruling and its trigger condition instead. This also narrows spec
§2.1's third `platform/`-shadowing proof, which asserts "`manage.py collectstatic`, which
the Docker image runs" — the image does not run it; the proof stands on §2.1's other two
legs, and **the narrowing is recorded in ADR 0015 Decision 1 only** (M5). It is *not* put
into the ADR 0010 amendment: ADR 0010 §2's own "Why not `platform/`" (`0010:128-133`)
argues stdlib shadowing and import ambiguity and never mentions `collectstatic`, so there
is nothing there to narrow — that amendment re-confirms `0010:128-133` cleanly. The repo's
own two flat "breaks `collectstatic`" claims get the qualifier instead: `foundation/README.md:41`
(T5, in budget) and `foundation/__init__.py:10` (a `.py` comment, **out of a docs-only
phase's budget — named, not edited**).

**D11 (amended by controller ruling, 2026-08-29) — the scrub is performed inside ROADMAP
Phase 1.55, and nowhere else.** This plan originally deferred the whole question. The
ruling narrows it: **`docs/ROADMAP.md` Phase 1.55's model names, weight names, and GGUF
filenames are scrubbed as part of T3's rewrite** — same file, same commit — covering the
lines the D1 closure text does not already replace (`:109`, `:112-114`, `:125-126`,
`:136-139`). Everything outside Phase 1.55 — ADR 0012's headings and body, ADR 0014, the
vision plans, the spec — **stays a separate owner-visible decision** and is not touched by
P4. Recorded in T7's read-through as the remaining question, scoped to "outside Phase
1.55" rather than to the whole repository.

**D12 — `docs/OPERATIONS.md` is not edited.** Spec `:1791` asks for the `ChatSession` drop
to be recorded there as well as in ADR 0015. Checked: `OPERATIONS.md` documents the
backup/restore procedure and names no table list that the drop invalidates, and it is the
one file under an automated verbatim guard (`test_docs_sync.py`). ADR 0015 Decision 13
carries the record; touching the guarded file to add a sentence it does not need is not
worth the risk. **Recorded here as a knowing deviation.**

---

## Task 1: `docs/adr/0015-agent-layer-and-tool-contract.md`

**Deliverable:** one new ADR, in the house shape, describing the system P0–P3 actually
built. Reviewable on its own: a reader who knows the tree should find no claim in it they
can falsify.

### Files

- **New:** `docs/adr/0015-agent-layer-and-tool-contract.md`
- **Read, not edited:** the spec sections in `## Spec` above; `docs/adr/0013` and `0014`
  in full for shape; the P0–P3 plans' deviation/ruling sections.

### Interfaces (the ADR's full outline, for review before it is written)

```
# ADR 0015 — The agent layer: one tool contract, one turn, one door

**Status:** Accepted
**Date:** 2026-08-29

## Context
## Decision
### 1.  The repository is four columns, and the fourth is `foundation/`
### 2.  One tool contract, in one pure leaf, is what every feature registers against
### 3.  A tool's wire schema is rendered by adapters, from one builder
### 4.  An artifact is a reference, never bytes
### 5.  One chat turn is one queue job — and tools run INLINE inside it
### 6.  A delegate spends the ROOT turn's budget, and depth is capped by omission
### 7.  A flow is a row, and it runs through the one registered `flow.run`
### 8.  Shipped defaults are a catalogue an operator adopts, never a deploy step
### 9.  Grants attach to a PRINCIPAL, and one function decides availability
### 10. The open box has a named principal, an owner column, and one filter per kind
### 11. Every tool call writes its own row, whoever the caller is
### 12. `/chat/` answers 202-and-poll, refuses before it writes, and works with no JS
### 13. `ChatSession`/`ChatMessage` are retired: the seam survives, the parameter does not
### 14. Hosted-API engines are designed-for, and the prior runs against them
### 15. A tool and its page ask the engine the same question, through the same seam
## Named gaps and deferred work
## Consequences
## See also
```

The assertive claim each numbered subsection must land, and the evidence it must cite:

1. **Four columns; `foundation/`, not `platform/`; the import law is three rules.**
   `platform` is a stdlib module name, `manage.py` puts the repo root on `sys.path[0]`, and
   ADR 0010 §2 (`0010:128-133`) already ruled once against it — this re-confirms that
   ruling rather than reversing it. State the three rules verbatim in substance from spec
   §3.3 (`:286-337`), with the **two** named cross-column exceptions the tree actually has:
   `models.registry.bindings` (for `tools/*` and `agents/*`) and `foundation.ops` →
   `models.queue.models` for the active-work refusal. Narrow §2.1's collectstatic proof
   per D10.
2. **`agents/contracts/` is a pure leaf and the single registration point.**
   `ToolSpec`/`ToolResult`/`ToolContext`/`Principal`/`StepBudget`/`ToolRefused`; the
   module-level registry (`agents/contracts/tools.py:302`'s `register_tool`); nine tools
   registered from four `ready()` bodies (cite all four `apps.py` lines). **The count needs
   the flag caveat, word for word in substance with `agents/README.md:11`:** the two vision
   tools are registered only while the `vision` feature flag is on
   (`tools/vision/apps.py:75-76`), so "nine" is the count on a vision-enabled install and
   the sentence must say so rather than implying nine unconditionally. Purity is pinned by
   a subprocess with no Django configured (`agents/contracts/tests/test_purity.py`), not
   asserted in a docstring. `rag.ingest` mutates, so it is registered but not grantable —
   the mechanism is `grantable_tools()`, a one-line filter on `spec.mutates`
   (`agents/contracts/tools.py:336-344`), whose own docstring cites ADR 0010:266-276; cite
   the code first and the ADR second, in that order.
3. **Two adapters, one schema builder.** `openai_tool_dict` and `mcp_tool_dict` in
   `agents/contracts/toolschema.py`, pinned equal by a drift test. The MCP adapter shipped
   in P2 *before* any edge exists, deliberately — while there is one builder to share
   rather than after an edge exists to drift from (spec `:2650-2678`).
4. **An artifact is a reference.** `output:<id>` / `input:<id>` / `document:<id>`. A tool
   call is JSON (ADR 0012:140), so bytes cannot travel in one; an agent references an
   existing artifact and never receives an upload.
5. **One turn = one `agent.turn` job, and a tool runner must never block on a queue job.**
   This is the **new** decision, stated as new (spec §1.2's honesty note, `:104-111`): ADR
   0013 nowhere forbids nested enqueue; the deadlock is *implied* by sequential mode and by
   the no-backfill proof. Give the one-sentence rule verbatim — *a tool runner must never
   block on a queue job; it may enqueue one and return its id, but it must never call
   `get_job` in a loop and never enqueue work whose result it needs* — and name
   `rag.ingest` as the fire-and-forget case that is allowed. Note that memory rotation is
   unweakened: the turn is exclusive and declares its models to the planner.
6. **Delegation is bounded by a SHARED budget, not a nested one.** `agents/limits.py`'s
   `MAX_AGENT_DEPTH`; the cap is enforced by **omission** (the `agent.*` specs are left out
   of the delegate's tool list at the last legal depth) with `ToolRefused` as
   belt-and-braces; a delegate gets a fresh message list, never the parent's history
   (`agents/runtime/delegate.py:1-24`).
7. **A flow is a ROW.** Owner ruling 1 (2026-08-28). One registered `flow.run`
   (`agents/runtime/flowtool.py`), whose `flow` param is a choice filled **per turn** from
   `visible_flows(principal)`; the runner (`agents/runtime/flow.py`) loads the row by slug
   at call time. This is what makes N flows possible without N registered specs, because
   `ready()` may not read the database. Step grammar: `$input.<key>`,
   `$steps.<N>.text|data.<path>|artifacts.<i>`.
8. **Nothing auto-installs.** Owner ruling 2. `agents/defaults.py` declares a catalogue of
   what the platform *offers*; `manage.py install_defaults [--reset <slug>]` is the
   explicit, idempotent adoption; `sync_agents` is gone
   (`agents/management/commands/` holds `agent_turn.py` and `install_defaults.py` only).
   And ruling 3: an adopted row is the operator's — `resident=True` is an **origin
   marker**, the edit-lock is gone, `slug` stays immutable, `--reset` is the one way back.
9. **`granted_tools(principal, tool_keys)` is the only place that answers "may this caller
   call this tool".** It drops an unregistered key with a log, and drops a `mutates=True`
   key outright. Retrieval keeps exactly **one** filter point,
   `tools/rag/retrieval.py::retrieve_nodes`, for both callers — a visibility scope is one
   more argument *there*, never a second copy in a runner.
10. **Accounts are off, and that is a posture with a name.** Ruling 4.
    `agents/chat/principal.py::principal_for_request` is the only request→principal point
    in the codebase; open mode returns `OPEN_PRINCIPAL` = `Principal("open", "box")`
    (`agents/contracts/tools.py:126`), a real kind rather than a `None` special case;
    `settings.ACCOUNTS_REQUIRED` (`config/settings.py:36`) defaults False and raising
    `NotImplementedError` naming Identity & Auth is what `True` does today
    (`agents/chat/principal.py:36-40`); `Agent`/`Flow`/`Conversation` all carry
    `owner_kind`/`owner_key`; `agents/visibility.py` holds one `visible_*` per kind, asked
    by the page **and** by the runtime.
11. **`ToolInvocation` is its own row, not a column on `Turn`.** Written by `invoke_tool`
    for every call; an open row means RUNNING and a dead turn closes its rows
    (`agents/runtime/audit.py`); P3 added `queue_job_id`. An external MCP `tools/call` has
    no conversation and no turn and reuses the row unchanged — which is only possible
    because the conversation table does not own it.
12. **`/chat/` is a product surface with a working no-JS path.** POST → 202 + poll, or a
    plain redirect; `turn_status` reports **turn** states (`queued`/`running`/`done`/
    `failed`/`cancelled`), never queue states, and the totality of the body builders over
    `Turn.State` is guarded server-side. Refusals are preflighted before anything is
    written — `agents/runtime/preflight.py::preflight_turn` (`:104`) is the one rule,
    shared by `manage.py agent_turn` and by both `/chat/` call sites
    (`agents/chat/service.py:123`, `agents/chat/views/thread.py:62`).
13. **The `session_id` seam survives; the parameter does not.** Both tables were
    write-only; they were dropped in P2 (`agents/0001` + `rag/0013`) along with
    `answer_question`'s `session_id`. Conversation memory is `agents.models.Turn`, which
    records the tool call, its arguments, its data, its artifacts, and its delegation
    depth. Cross-reference the amendment already standing at `0010:282-293` (D2).
14. **Hosted-API engines are unbroken ground with a strong prior against them.** Say so;
    do **not** cite ADR 0010 as precedent — there is none. The standing rules point the
    other way: no implicit phone-home and default-deny egress
    (`docs/ARCHITECTURE.md:59-65`), "the platform itself never pulls a model"
    (`0010:182-185`). A future adapter is forbidden in `airgap` and `isolated-lan` and
    possible only inside a `gated-sync` window; a credential is a **pointer** in
    `ModelConnection.config`, never a secret, because backup dumps that table.
15. **A tool and its page ask the engine the same question, through the same seam.** The
    R1/R2 data contract, built by the vision track: `ignored_params(operation_key, config)`
    is an optional engine member beside `loaded_footprint`/`unload`
    (`models/contracts/engines/base.py:402`, `:426`, `:435`; the header comment at
    `:383` and `:418-421` states the `getattr` degradation rule);
    `services.fill_engine_blanks(operation, params, resolved, keys=None)`
    (`tools/vision/services.py:293`) is the **one** filler for engine-owned blanks, used by
    the page (`tools/vision/views.py:762-766`) and by the tool
    (`tools/vision/tools.py:371`); `services.operation_catalog`
    (`tools/vision/services.py:465`) is the single truth for operation support, and
    `vision.operations` renders its cue verbatim — `" — not runnable here: "` +
    `unsupported_reason` (`tools/vision/tools.py:254`). Name PR #64's hygiene: `run_generate`
    preflights **once** and threads `resolved` into both `_fill_engine_params` and
    `submit_job` (`tools/vision/tools.py:447-453`); an ignored FILE param is neither staged
    nor forwarded, with a second line of defence against a hand-crafted POST
    (`tools/vision/views.py:735-752`); `isolated_tool_registry`
    (`tools/vision/tests/_helpers.py:27`).

**Named gaps and deferred work** — the section must carry, each in one honest paragraph:

- **G1 (P3-G1)** `agent.<slug>` stays catalogue-registered while `flow.run` is row-driven.
  The symmetry (`agent.run` with an `agent` choice filled from `visible_agents`) is
  available and deliberately not taken: delegation is a **grant** decision in a way running
  a flow is not. It lands with Identity & Auth, and `agent.<slug>` retires then.
- **G2 (P3-D4)** Flow-as-a-turn is not built. `agent.turn`'s payload keeps `"mode"` with
  its one legal value `"chat"`; a key with one possible value and no caller is not forward
  compatibility.
- **G3** `/chat/` offers agents, not flows. A flow is reachable only *through* an agent's
  `flow.run` call; the conversation-start surface has no flow picker.
- **G4** `agents/chat/rendering.py` computes a `files` card key (`:123`, `:147`) that no
  template under `agents/chat/templates/chat/` reads. Non-image artifacts are recorded and
  not rendered.
- **G5** `thread_context` runs `preflight_turn` on **every** thread GET, which for an agent
  with granted tools and a bound connection can mean a real, bounded (~5 s) call to the
  engine's `supports_tool_calling` probe, degrading to `None` on any failure. A per-request
  TTL cache in front of it is backlog (`agents/chat/README.md:435-440`).
- **G6** Hosted-API engines: designed-for only (Decision 14). The secrets vault, the
  posture gate, credential CRUD, quota, and streaming are all out of scope.
- **G7** `plan_turn` may over-declare a role for a multi-role tool that `_roles_resolve`
  later drops — the same tolerant shape `plan_ingest` uses. Narrowing it needs an
  enqueue-time/run-time reconciliation nothing has yet had a reason to design.
- **G8 (vision)** `services.submit_job` still performs its own health check after the
  tool's single preflight; passing `resolved` makes it a health round trip rather than a
  full re-resolution, and it is `services`' own contract, shared with the page
  (`tools/vision/tools.py:400-411`).
- **G9 (vision)** `build_generate_spec()` is a function rebuilt per call, by design: its
  params are the union of whatever operations are registered at the moment it runs
  (`tools/vision/tools.py:183`, called from `tools/vision/apps.py:76`).
- **G10 (vision)** ComfyUI's `unload` is endpoint-wide, not per-model — there is no
  per-model free in its API — and `loaded_footprint` is a deliberate over-count measured as
  a delta across the adapter's own run. The queue can evict a ComfyUI resident, never
  selectively (D6; `models/contracts/engines/comfyui.py:731`, `:827`). Record alongside it
  that `models/contracts/engines/base.py:424`'s seam comment still reads "`OllamaEngine`
  implements both", which was true when it was written and is now incomplete — a `.py`
  comment, **out of a docs-only phase's budget, so named here and not fixed here.**
- **G11** `tools/rag/tests/_helpers.py:116-131` duplicates `tools/vision`'s
  `isolated_tool_registry`. Kept duplicated on purpose: a shared helper would be a
  cross-column test import, which the helper rule forbids (D8).
- **G12** The **ten** §14 standing gaps (spec `:2389-2416`, gaps 1–10; gap 11 starts at
  `:2417`) are unchanged and must be listed as inherited, not re-litigated: no agent-builder UI, no flow-builder UI,
  no grantable settings-mutating tool until Identity & Auth, `/chat/` unauthenticated like
  every other surface on the box, no streaming, no cooperative cancel of a *running* turn,
  no turn resume across a worker restart, no token-budget management (`HISTORY_TURNS` is a
  fixed cap), `rag.ingest` accepts no path, and no file uploads into a turn.
- **G12b — one §14 gap is DISCHARGED, and the ADR says so.** Gap 12 ("whether `/api/tags`
  reports `capabilities` is an open question, resolvable only by a live probe") was closed
  by P1: the live probe was run (spec's corrections addendum, `:2544-2554` — the rows do
  carry `capabilities`, and `/api/show` was kept as the source by choice rather than by
  necessity), `supports_tool_calling` was implemented on the Ollama adapter, and
  `preflight_turn` consumes it today on every `/chat/` GET. A gap that got answered is
  worth one sentence saying it was answered; leaving it in the open list would
  misdescribe the tree.
- **G13** The order after this ADR, each depending on the one before: **Identity & Auth**
  (principals, grants in their own table, `/inference/`'s mutation surface closed) → **MCP
  edge** (`tools/list` + `tools/call` over HTTP on the existing app, over the same
  registry — never a separate server, never a protocol in the middle of an internal call;
  and the import direction, this platform as an MCP client with a per-server allowlist) →
  **tenancy** (visibility scopes applied at Decision 9's single filter point).

**Consequences** must state, at minimum: there is now exactly one way a feature exposes
capability to an agent, and it is the same object an external caller will one day read;
`ready()` stays DB-free and light because every runner imports its services lazily; a
deploy changes what the platform *offers* and never what is *installed*; and the agent
layer consumes the queue's admission and eviction exactly as every other job kind does.

**See also** must link `agents/README.md`, `agents/contracts/README.md`,
`agents/runtime/README.md`, `agents/chat/README.md`, ADR 0010 (amended below by T2), ADR
0013 (amended below by T2), ADR 0012 (D-EDIT-12/13, Decision 15), and the design spec.

### Steps

1. Re-read `docs/adr/0013-inference-execution-queue.md` and `docs/adr/0014-media-ingestion.md`
   end to end and write down the house conventions actually observed: header block order,
   `### N. <assertive claim>` phrasing, the "recorded here rather than only there" idiom for
   cross-ADR notes, and the fact that 0014's named-gaps live inside a numbered decision
   (`0014:827`) while 0013's are their own `### 8` (`0013:283`). Choose 0013's shape — a
   dedicated section — and record the choice in one sentence at the top of the section.
2. Create `docs/adr/0015-agent-layer-and-tool-contract.md` with the header block and the
   section skeleton from **Interfaces** above, headings only, nothing else. Confirm the
   heading list matches the outline exactly before writing prose.
3. Write `## Context`: what existed before P0 (three top-level packages, `chat.converse`
   an unbuilt role, RAG and vision each their own island), what the owner asked for
   (agents with tools across the platform, one contract, a permanent chat product), and
   the two facts that constrained the design — the one-door rule and sequential mode.
   State plainly that this ADR records **what was built in five PRs**, not a plan.
4. Write Decisions 1–15, one at a time, in the order given. **For every `file:line` in the
   text, open the file at that line first.** Any claim that cannot be pinned to a line
   comes out or is rewritten as the weaker thing that is true.
5. Write the named-gaps section, G1–G13 (G12b included, in place), in the order given.
6. Write `## Consequences` and `## See also` per **Interfaces**.
7. Grep the finished file for model names and versions —
   `grep -niE 'flux|qwen|klein|lightning|sdxl|llama|mistral|gemma|phi|qwen2|[0-9]+b\b' docs/adr/0015-*.md`
   — and confirm every hit is a false positive or remove it.
8. Grep the finished file for pre-regroup paths —
   `grep -nE '(^|[^a-z])(modules|console|core)/' docs/adr/0015-*.md` — expecting hits only
   inside deliberate historical sentences ("`modules/rag/` is now `tools/rag/`"); rewrite
   any other.
9. Verify every `file:line` citation mechanically: extract them with a one-off shell loop
   and `sed -n '<n>p' <file>` each, eyeballing that the line is what the ADR says it is.
10. Run the gate matrix. Commit: `docs(p4): ADR 0015 — the agent layer, one tool contract, one turn`.

---

## Task 2: the two cross-ADR amendments

**Deliverable:** ADR 0010 and ADR 0013 each carry an appended amendment recording the
constraint ADR 0015 places on them, so a reader who arrives at either from the outside is
not left with a stale rule. The precedent is `0013:402-407` ("recorded here rather than
only there") and `0010:594-622`, which is ADR 0013's own both-sides entry.

### Files

- `docs/adr/0010-model-management-framework.md` (append after `:622`)
- `docs/adr/0013-inference-execution-queue.md` (append after `:602`)

### Interfaces

**ADR 0010, new section:**

```
## Amendment (2026-08-29) — Amended by ADR 0015: the import law is three rules, and `platform/` stays rejected
```

Three paragraphs, no more:

1. **The import law is restated.** §2's `core/` / `console/` / `modules/` vocabulary
   (`0010:108-133`) and the dependency-direction amendment (`0010:520-526`, the amendment
   that opened the seam) described a tree that no longer exists. The three rules replacing
   them are stated in ADR 0015 Decision 1; this paragraph gives the mapping (`core/` split
   into `models/contracts` + `agents/contracts` + `foundation/format.py`,`files.py`;
   `console/inference` → `models/registry`; `console/jobs` → `models/queue`;
   `console/ops`,`console/setup` → `foundation/`; `modules/*` → `tools/*`) and the two
   named cross-column exceptions. **The sanctioned-seam sentence is the blockquote at
   `0010:534-537`** — "A §5 module MAY import from `console.inference.bindings` — and ONLY
   that one module…" — and it is that blockquote the amendment carries over verbatim in
   substance, now naming `models.registry.bindings` and admitting `agents/*` as a second
   importer family. `0010:520-526` is cited only as the amendment that opened it, never as
   the sentence itself.
2. **`platform/` is re-confirmed as rejected, not reversed.** §2's "Why not `platform/`"
   (`0010:128-133`) was re-verified three ways before P0 and holds; the fourth column is
   `foundation/`. **Re-confirm it cleanly — no `collectstatic` qualifier here.**
   `0010:128-133` argues stdlib shadowing and import ambiguity and never mentions
   `collectstatic`, so there is nothing in it to narrow; the narrowing belongs to spec
   §2.1's third proof and lives in ADR 0015 Decision 1 alone (D10, M5).
3. **`session_id` — pointer, not restatement.** One sentence directing the reader to the
   amendment already standing inside the RAG-as-tool bullet at `0010:282-293`, and stating
   that ADR 0015 Decision 13 is the fuller account.

**ADR 0013, new section:**

```
## Amendment (2026-08-29) — Amended by ADR 0015: an agent turn is a job, and a tool runner may never wait on one
```

Four paragraphs:

1. **`agent.turn` is registered, and it is exclusive.** It declares every model it may
   touch to the planner, so `_evict_to_match_plan` evicts to match before it launches —
   §1's one-door rule is consumed, not weakened.
2. **The new constraint, stated as new.** This ADR does not say "nested enqueue is
   forbidden" anywhere, and ADR 0015 does not pretend it does (spec §1.2's honesty note).
   The rule, in one sentence: *a tool runner must never block on a queue job; it may
   enqueue one and return its id, but it must never call `get_job` in a loop and never
   enqueue work whose result it needs.* Give the derivation from this ADR's own §3–§4:
   sequential mode is the shipped default, `plan_admissions` returns `[]` whenever anything
   is running, so a job waiting on a job holds the machine's one slot against a job that
   can never be admitted; the orphan sweep then makes it a permanent failure. Deadlock,
   then data loss — certain, not probable.
3. **Why inline is not a new seam.** Every tool calls a function the platform already calls
   synchronously inside a job handler; `rag.ingest` is the one fire-and-forget enqueue, and
   it returns an id rather than waiting.
4. **What is unchanged, and one path correction.** §8's named gaps and the 2026-08-25
   amendment's three follow-ups are untouched by this amendment; say so explicitly, in the
   same closing shape those sections already use. Close with one sentence in ADR 0010's own
   idiom, because the `docs/adr/` sweep exclusion (Global Constraints) means nobody else
   will: *the `## See also` paths above predate the P0 regroup —* `console/jobs/scheduler.py`,
   `console/jobs/models.py`, `console/jobs/claim.py`, `console/jobs/worker.py`
   (`0013:386-389`) *are now `models/queue/`.* An amendment saying so is the honest fix; a
   silent rewrite of a dated record is not.

### Steps

1. Read `0013:402-483` and `0010:594-622` again and match their register: a bold lead-in
   sentence per paragraph, second person absent, no bullet lists where prose will do.
2. Append the ADR 0010 amendment after `0010:622`, following **Interfaces**. Verify
   `0010:108-133` and the `0010:534-537` blockquote say what the amendment claims before
   citing them (`0010:520-526` is the amendment heading and its opening, not the seam
   sentence — M4).
3. Append the ADR 0013 amendment after `0013:602`. Verify the derivation's citations
   against the tree, **not against the spec** — the spec cites `console/jobs/scheduler.py`
   and `console/jobs/worker.py`, which are now `models/queue/scheduler.py` and
   `models/queue/worker.py`. Confirm the rule-1 sequential-mode branch and
   `STALE_AFTER_SECONDS` at their current lines and cite those.
4. Add the reciprocal link in ADR 0015's `## See also` (T1 wrote the entry; confirm both
   directions now resolve).
5. Grep both files for model names as in T1 step 7.
6. Run the gate matrix. Commit: `docs(p4): amend ADR 0010 and ADR 0013 for the agent layer`.

---

## Task 3: `docs/ROADMAP.md`

**Deliverable:** the roadmap tells the truth about Phase 1.55 and Phase 1.6, names ADR
0015, and carries no pre-regroup path outside a deliberate historical sentence.

### Files

- `docs/ROADMAP.md`

### Interfaces

Path rewrites (verified present at `08cd21f`), one per line:

| Line | Now | Becomes |
|---|---|---|
| `:27` | `core/inference/gateway.py` | `models/contracts/gateway.py` |
| `:28` | `modules/rag/index.py` | `tools/rag/index.py` |
| `:34` | `console/jobs/` | `models/queue/` |
| `:57` | `console/inference/` | `models/registry/` |
| `:61` | `core/inference/bindings.py` | `models/registry/bindings.py` |
| `:63` | `core/inference/catalog.py` | `models/contracts/catalog.py` |
| `:67` | `console/inference/drift.py` | `models/registry/drift.py` |
| `:81` | `core/inference/engines/comfyui.py` | `models/contracts/engines/comfyui.py` |
| `:86` | `modules/vision/` | `tools/vision/` |
| `:95` | `core/inference/operations.py` | `models/contracts/operations.py` |
| `:193` | `modules.vision.services` | see step 3 — the bullet is retired, not rewritten |

`:279` and `:300` keep their `modules/chat` and `modules/rag/models.py` references: both
are deliberate historical sentences ("that shape predates the P0 regroup", "since renamed
`tools/rag/models.py`") and rewriting them would erase the correction they exist to make.

**R1 closure text (verbatim, replaces the body of `:141-158`):**

> Text-to-image on the second graph family: a template declares the params its graph cannot
> honour (`IGNORES`), the engine exposes them (`ignored_params`), and the page/tool never
> trust a submitted value for them. Family-level defaults; shared graph fragments promoted
> to `_fragments.py`. (ADR 0012 D-EDIT-12)

**R2 closure text (verbatim, replaces the body of `:159-182`):**

> One constant generation form: the union of every registered operation's params, each
> field stamped enabled/unused/ignored with a reason and a tooltip; Model + Mode are GET
> selectors; blanks the operator was never allowed to answer are back-filled from the
> engine before validation; the catalog reports `supported`/`unsupported_reason` per
> operation so tools can say "not runnable here". Tab/merge machinery deleted.
> (ADR 0012 D-EDIT-13)

Both keep their `- [x] **… (R1)** — ` / `- [x] **… (R2)** — ` lead-ins and their `[x]`.

**Phase 1.55 name scrub (controller ruling, amended D11).** Four spots outside the R1/R2
bodies still name weights, product names, or GGUF filenames, and they are scrubbed in this
same commit:

| Line | Carries | Becomes |
|---|---|---|
| `:109` | "two multi-file GGUF model families (`flux2`, `qwen_image`)" | "two multi-file model families" — the family keys are implementation identifiers, documented in ADR 0012 where they belong, not in a public roadmap |
| `:112-114` | a named distilled variant, twice | "the distilled variant"; keep the ADR 0012 cross-reference and the live-verification pointer |
| `:125-126` | a named distilled connection and its per-step timings against a named ordinary graph | "the distilled variant" / "the ordinary graph of the same family"; **timings stay** — they are the platform's own measurements, not a model's identity |
| `:136-139` | two GGUF filenames with sizes, a family name, and — on `:139` — a run named after a distilled-variant product | "a quantized text encoder plus its multimodal projector"; the run becomes "the distilled run"; **sizes, step counts, and job timings stay** |

Nothing outside Phase 1.55 is touched — ADR 0012's headings and body, ADR 0014, the vision
plans, and the spec keep their names and are a separate owner decision (amended D11).

### Steps

1. Apply the ten path rewrites in the table. After each, `grep -n` the new path against
   the tree to confirm the file exists.
2. Replace the R1 body (`:141-158`) and the R2 body (`:159-182`) with the verbatim closure
   text above. Do **not** change `[x]` to `[ ]` (Deviation D1). The live-verification
   detail that made those bullets long is preserved in the vision plans and in ADR 0012;
   the roadmap's job is to say what shipped. Then apply the four Phase 1.55 name-scrub
   rows above (`:109`, `:112-114`, `:125-126`, `:136-139`), and re-grep the whole phase —
   `sed -n '76,199p' docs/ROADMAP.md | grep -niE 'flux|qwen|klein|lightning|sdxl|gguf|[0-9]+b\b'` —
   expecting zero hits that name a model, a weight, or a file.
3. Retire the "Deferred" bullet at `:193` — "A chatbot tool wrapper over
   `modules.vision.services` (the seam already exists; nothing calls it yet)". It shipped:
   `vision.operations` and `vision.generate` are registered
   (`tools/vision/apps.py:75-76`). Move it out of Deferred and into the delivered list as
   a one-line `[x]` naming both tools and ADR 0015, or delete it and let ADR 0015 carry the
   record — **choose deletion only if the delivered list already names both tools**; check
   before deciding.
4. Phase 1.6 spans `:269-361` (the "Deliberate boundary" paragraph at `:355-358` closes
   it). Confirm the three-phase tail (`:330-353`) still matches ADR 0015 G13 word for word
   in substance; fix the tail, not the ADR, if they differ.
5. **The Phase 1.6 preamble (`:271-276`) and the `v1` bullet (`:298-308`) are rewritten in
   this same step** (controller ruling, reviewer M2 — they are one claim split across two
   places and fixing only one leaves the page self-contradicting).
   - **Preamble (`:271-276`).** Delete "None of this is implemented yet; it's recorded here
     so development keeps it in mind." — it is false: P2 and P3 shipped `chat.converse`,
     `/chat/`, tools, flows, and the defaults catalogue, and four of this phase's own
     bullets are already `[x]`. Replace with one sentence saying the phase is **partly
     shipped** and naming what remains (per-role model split, grounded-by-default
     conversation, and the three phases that follow). Keep the existing ADR 0010 link and
     its "2026-08-22 amendment" wording, and **add the ADR 0015 link** beside it as the
     record of what was built.
   - **`v1 — grounded conversation` (`:298-308`).** Stays `[ ]`. Rewrite the opening
     sentence — "Replies are grounded by the existing retrieval pipeline on **every
     turn**" — which is not what shipped. What is true: grounding today is **by tool
     choice** — an agent granted `rag.ask`/`rag.search` retrieves when it decides to, and
     `/chat/` renders the citations it returns — and the open item is narrowed to an agent
     mode that retrieves on **every** turn regardless of the model's choice. The historical
     paragraph about `ChatSession`/`ChatMessage` inside the same bullet stays as written;
     it is a correction, not a status. Do not touch `:309`, the shipped `v2 — tool use`
     bullet.
6. `grep -nE '(^|[^a-z])(modules|console|core)[/.]' docs/ROADMAP.md`. Expected hits, and
   nothing else: `:279` and `:300` (deliberate historical sentences, kept — see
   **Interfaces**) and `:387` ("…the same inference and auth core — added without
   redesigning the core."), a **known false positive** of the `[/.]` character class
   matching an English sentence's final period. `:27` must no longer appear.
7. Run the gate matrix. Commit: `docs(p4): ROADMAP — reconcile Phase 1.55, scrub its model names, close Phase 1.6`.

---

## Task 4: `docs/ARCHITECTURE.md` and the root `README.md`

**Deliverable:** the two documents a newcomer opens first describe four columns and an
agent layer, with no pre-regroup path.

### Files

- `docs/ARCHITECTURE.md`
- `README.md`

### Interfaces

**`docs/ARCHITECTURE.md`:**

- **§1's layer diagram — fences at `:22` and `:38`, content `:23-37`.** The `MODULES` band becomes `TOOLS` and lists
  `RAG · Image generation · Home automation (planned)`. A band for the agent layer is added
  **above** it — `AGENTS  Tool contract · Turn runtime · /chat/` — because an agent is a
  consumer of tools, not one of them. The closing sentence — "**The line we own** is the
  CORE… everything above it is 'modules built against our contracts'" — keeps its meaning
  with "tools" in place of "modules". It sits at **`:40-41`**; round-1 review cited
  `:42-43`, which is the blank line and the `---` rule beneath it (`grep -n 'The line we
  own' docs/ARCHITECTURE.md` → `40`). Verified line numbers win over cited ones, per
  Global Constraints.
- **§3 (`:94`, `:119`, `:122`, `:132`)** — four pre-regroup path citations:
  `core/inference/bindings.py` → `models/registry/bindings.py`;
  `core/inference/engines/comfyui.py` → `models/contracts/engines/comfyui.py`;
  `core/inference/operations.py` → `models/contracts/operations.py` (and its "four
  operations" count is now five: `txt2img`, `img2img`, `inpaint`, `upscale`, `edit` —
  verify against `models/contracts/operations.py` before writing a number);
  `core/inference/engines/base.py::Asset` → `models/contracts/engines/base.py::Asset`.
- **§4's Core services table (`:146-158`).** Two rows repointed —
  Config & Secrets `console/inference/` → `models/registry/`, Execution Queue
  `console/jobs/` → `models/queue/`. **One row added**, per §12.5:

  | **Agent runtime** | One tool contract every feature registers against; a bounded turn that drives tools by model decision or by a fixed flow | `agents/` — the registry in `agents/contracts/`, the turn in `agents/runtime/`, the product surface at `/chat/` ([ADR 0015](adr/0015-agent-layer-and-tool-contract.md)) |

  Place it after Execution Queue, so the table reads bottom-up in the order a request
  travels.
- **§5's preamble (`:162-171`)** — `console/inference/`, `console/jobs`, `console/setup/`
  become `models/registry/`, `models/queue/`, `foundation/setup/`.
- **§5's "Modules today" (`:173-190`)** becomes **"Tools today"**, listing all three
  `tools/*` folders with their mount points, the roles they register, and their READMEs.
  The feature-flag paragraph's three stale references —
  `modules/vision/apps.py::VisionConfig.ready()`, `modules.vision.context_processors.features`,
  and `templates/_shell.html` — become `tools/vision/apps.py`,
  `tools.vision.context_processors.features`, and `foundation/templates/_shell.html`.
  **Verify each before writing**, particularly the context-processor path.
- **§8's "Identity model" open question (`:280`)** gains one clause noting that the agent
  layer's grant seams already exist and are waiting on it (`granted_tools`,
  `principal_for_request`, the owner columns, the three `visible_*` functions) — so this
  question now has a named consumer.

**`README.md`:**

- **Repository layout, `:123`** — "`agents/  # The central brain (empty in P0;
  contracts/runtime/chat arrive P1-P3)`" is stale. Replace with a three-line block matching
  the other columns' style: `contracts/` the tool contract and registry, `runtime/` the
  turn and the flow runner, `chat/` the `/chat/` surface.
- **Status section (`:72-92`)** — the "What works today" list opens at `:72` and its last
  bullet ends at `:92`. Add one bullet after Media ingestion:

  > **A conversational agent with tools** — a permanent chat surface at `/chat/` where an
  > agent answers by *using* the platform: searching and asking the document library,
  > generating an image, reporting what models are bound, delegating to another agent, or
  > running a fixed multi-step flow. Every capability is registered once, as a tool, and the
  > same registry will one day serve external callers ([ADR 0015](docs/adr/0015-agent-layer-and-tool-contract.md)).

- **"Next up" paragraph (`:94-98`)** — "Then Phase 1.6's conversational agent" is stale.
  Replace the tail with: retrieval quality, then Identity & Auth, then the MCP edge, then
  Phase 2's posture and isolation work.
- **Design principle 3 (`:34-35`)** — "RAG and home automation are the first two *modules*"
  becomes the honest three (`tools/rag`, `tools/vision`, `tools/home` planned) and keeps
  its point that the durable value is the core.

### Steps

1. Rewrite ARCHITECTURE §1's diagram. Keep the box widths aligned — the block is
   monospaced ASCII and a ragged right edge is a visible regression.
2. Apply the §3 path rewrites; count the registered operations from
   `models/contracts/operations.py` and write that number, not "four".
3. Repoint the two §4 table rows and insert the Agent runtime row.
4. Rewrite §5's preamble and the "Modules today" → "Tools today" section, verifying each
   path and each registered role name against the app's `apps.py`.
5. Add the §8 clause.
6. Apply the four `README.md` edits.
7. `grep -nE '(^|[^a-z])(modules|console|core)/' docs/ARCHITECTURE.md README.md` returns
   empty. `grep -n 'modules\.' docs/ARCHITECTURE.md` returns empty.
8. Confirm every markdown link in both files resolves:
   for each `](…)` target that is a repo path, `test -e` it.
9. Run the gate matrix. Commit: `docs(p4): ARCHITECTURE and README — four columns and an agent layer`.

---

## Task 5: `docs/DEV.md`, `docs/EXTENDING.md`, the column READMEs, `docs/BUSINESS.md`, and the two test-module notes

**Deliverable:** the day-to-day developer documents match the tree, the two test-file notes
that describe a fixed or misdescribed situation are corrected, and a newcomer can add a
tool to this platform by following one page.

### Files

- `docs/DEV.md`
- **New:** `docs/EXTENDING.md` (owner amendment, 2026-08-29)
- `README.md` (one link line — see Interfaces), `agents/contracts/README.md` (one link line)
- `tools/README.md`, `foundation/README.md`
- `docs/BUSINESS.md`
- `tools/rag/tests/test_tools.py` (module docstring only)
- **Read and verify, do not rewrite:** `agents/README.md`,
  `agents/runtime/README.md`, `agents/chat/README.md`, `models/README.md`,
  `models/registry/README.md`, `models/contracts/README.md`, `tools/rag/README.md`,
  `tools/vision/README.md`, `tools/home/README.md`, `foundation/setup/README.md`

### Interfaces

#### `docs/EXTENDING.md` — "Adding a tool in three steps" (owner amendment)

**Why it exists.** ADR 0015 records *what* the tool contract is. Nothing records *how a
person uses it*: today, adding a tool means reading `agents/contracts/README.md`, an
existing `tools.py`, an `apps.py`, and two guard tests, and inferring the sequence. This
page is that sequence, written once, against a real tool.

**The worked example is `models.status`** (`models/registry/tools.py`) — the smallest real
tool in the repository: no params, no roles, one lazy import, one `ToolResult`. Every code
block on the page is **quoted from that file**, never invented, so a reader who diffs the
page against the tree finds them identical. **No model names appear anywhere on the page**;
`models.status` reports role assignments and names none itself, which is part of why it is
the right example.

**Exact outline, in order:**

```
# Extending farabunker — adding a tool in three steps

(one-paragraph frame: a tool is how a feature offers itself to an agent;
 three steps, three files, no plugin system)

## Step 1 — the spec and the runner, in your column's `tools.py`
### The spec
### The runner
### Which exception to raise
## Step 2 — one line in `AppConfig.ready()`
## Step 3 — two guard lists, in the same commit
## What you get for free
## Granting it to an agent
## Testing it
## What is not possible yet
## See also
```

**Step 1 — the spec and the runner.** Show the complete `MODELS_STATUS` spec as it stands
at `models/registry/tools.py:37-49`, then annotate every field: `key` (the wire key, dotted,
`<column>.<verb>`); `label` (what a card shows); `description` (**written for the model, not
for a human** — it is what the LLM reads to decide whether to call the tool); `params`
(empty here — show the grammar with a second, two-line example built from
`models/contracts/operations.py:41`'s `Param`, e.g.
`Param("query", "text", "Query", required=True)` and
`Param("top_k", "int", "Results", default=None, min=1, max=50)`, which are the two shapes
`agents/contracts/tests/test_tools.py:169-176` already exercises; name the kinds a tool may
declare, citing `agents/contracts/tools.py:47` (`TOOL_PARAM_KINDS`), and say why `"file"` is
excluded, citing its rationale comment at `agents/contracts/tools.py:43-46` — a tool call is
JSON, so an image input is an artifact reference); `roles` (empty here — a tool that needs a model declares the role so
the turn's planner can declare it to the queue); `runner` (a **dotted-path string**, not a
function object, because `agents/runtime` reaches tools through a string and never imports
`tools/*` — import-law rule 3); `mutates` (defaults `False`; `True` means registered but
**not grantable** until Identity & Auth).

Then the runner, quoted from `models/registry/tools.py:52-59` (signature and docstring) and
its body's opening: the signature is fixed at `(args: dict, ctx: ToolContext) -> ToolResult`;
**the service imports are INSIDE the function body** (`models/registry/tools.py:60-61` —
`from models.contracts.roles import all_roles`, `from models.registry.bindings import
role_primary`), and `validate_tool_args(SPEC, args)` is the first real statement after them
(`:63`) — say it in that order, because that is the order the file has. The page must say why
in one sentence: `AppConfig.ready()` imports this module to reach the spec, so a
module-scope service import would put database and heavy-import work into every
`manage.py` invocation. A guard enforces it
(`foundation/ops/tests/test_column_boundaries.py:486`).

**"Which exception to raise"** — a three-row table, no prose:

| Situation | Raise | What the agent sees |
|---|---|---|
| The caller's arguments are wrong | `ParamError` (`models/contracts/operations.py:228`) — `validate_tool_args` raises it for you | `param_error`; the one failure class worth handing back for a retry |
| Nothing the model can say would fix it (a role is unbound, the caller is not allowed) | `ToolRefused` (`agents/contracts/tools.py:50`) | `refused`; no retry |
| Anything else went wrong | let it raise, or raise `ValueError` | `error`; logged, classified, never a traceback in the UI |

**Step 2 — registration.** One line in the app's `AppConfig.ready()`, quoted from
`models/registry/apps.py:60` — `register_tool(MODELS_STATUS)`. Say the two rules `ready()`
lives under: **no database access** and **no heavy imports**, which is exactly why step 1's
imports are lazy. Note that a flag-gated feature registers inside its flag check, as
`tools/vision/apps.py:75-76` does.

**Step 3 — the two guard lists.** Both live in
`foundation/ops/tests/test_column_boundaries.py`, and both are edited **in the same commit
that creates the module** — the file's own comment at `:182-184` says so:

- `TOOL_MODULES` (`:185-189`) — every module that declares tool runners. Swept for the
  never-block-on-a-queue-job rule (`test_no_tool_runner_blocks_on_a_queue_job`, `:346`).
- `_REGISTRATION_MODULES` (`:217-228`) — the modules `AppConfig.ready()` imports to
  register specs. It **overlaps** `TOOL_MODULES` rather than being a subset of it:
  `agents/runtime/flowtool.py` is in this list only. Swept for the
  no-service-import-at-module-scope rule (`:486`). It is an explicit positive list, not a
  derived one, and `:510`'s `test_the_registration_modules_list_is_not_silently_empty` is
  what keeps it honest.

**On "bump the floor":** there is **no per-tool registration floor to bump**, and the page
must say so rather than inventing a step. What exists are two anti-vacuous floors, both
`>=`, both deliberately loose: `models/registry/tests/test_registry_paths.py:80`
(`assert len(paths) >= 26`, whose docstring at `:59-78` states the rule — "a phase that adds
a registration should not have to edit this line, but it must move up when a phase adds
six"), and `agents/contracts/tests/test_toolschema.py:203`
(`assert len(all_tools()) >= 4`, the flag-independent tools). One new tool moves neither.
The test that *will* fail if step 3 is skipped is
`test_every_registered_runner_lives_in_a_swept_module`
(`foundation/ops/tests/test_column_boundaries.py:443`), and the page quotes what its
docstring says so the reader recognises the failure when they see it.
(`agents/contracts/tests/test_tools.py:169`'s
`test_it_delegates_to_the_platforms_one_floor` is **not** a registration floor — it pins
that `validate_tool_args` delegates to the platform's one validation floor. Named here so
nobody mistakes it for a counter.)

**"What you get for free"** — a table, each row with the file:line of the mechanism, so the
claim is checkable:

| You get | Mechanism |
|---|---|
| Grants — an agent row naming your key in `tool_keys` may call it | `agents/models.py:74` (`Agent.tool_keys`), decided by `agents/contracts/tools.py:347` (`granted_tools`) |
| Not grantable if it writes | `agents/contracts/tools.py:336` (`grantable_tools`, a filter on `spec.mutates`) |
| Both wire schemas, from one builder | `agents/contracts/toolschema.py:89` (`openai_tool_dict`) and `:105` (`mcp_tool_dict`), sharing `:53` (`_input_schema`) |
| Argument validation, one floor | `agents/contracts/tools.py:283` (`validate_tool_args`) delegating to the platform's `validate_params` |
| An audit row per call, whoever called | `agents/runtime/invoke.py:95` (`invoke_tool`) writing `agents/models.py:311-316`'s five outcome classes (`ok`, `refused`, `param_error`, `error`, `degraded`) |
| A card in `/chat/` with its arguments, thumbnails, and citations | `agents/chat/rendering.py:127` (`tool_card`), rendered by `agents/chat/templates/chat/_tool_card.html` |
| Usable as a flow step, with `$input.<key>` / `$steps.<N>.…` references | `agents/runtime/flow.py:67` (`resolve_ref`), `:149` (`resolve_args`), `:258` (`_run_steps`) |
| Budget and depth bounds, enforced around you | `agents/limits.py:20` (`MAX_STEPS_DEFAULT`), `:29` (`TURN_DEADLINE_SECONDS`), `:43` (`MAX_AGENT_DEPTH`) |
| An honest ending on every failure path | `agents/runtime/invoke.py:95-157` — it never raises for a tool failure; it classifies |

**"Granting it to an agent"** — three sentences and one command. A tool is not callable
because it is registered; it is callable because an `Agent` row names it in `tool_keys`
(`agents/models.py:74`). Edit the row (`manage.py shell`, or the row itself), or add the key
to the shipped catalogue in `agents/defaults.py` and adopt it with `manage.py
install_defaults`. **`--reset <slug>` restores the shipped text of one default and discards
local edits to that row** (`agents/management/commands/install_defaults.py:40`,`:48-49`) —
say that plainly, because it is the one destructive flag on the page.

**"Testing it"** — the four-run gate verbatim from this plan's Global Constraints, then the
three guards that fail loudly if a step was missed, each with what its docstring says:
`test_every_registered_runner_lives_in_a_swept_module` (`:443`, step 3 forgotten),
`test_no_tool_module_imports_its_service_layer_at_module_scope` (`:486`, step 1's lazy
import forgotten), `test_no_tool_runner_blocks_on_a_queue_job` (`:346`, a runner that waits
on a queue job — the rule ADR 0015 Decision 5 states). Add one line: write the runner's own
test the way every existing one does — patch the service function and assert the call.

**"What is not possible yet"** — four bullets, each pointing at ADR 0015's named-gaps
section rather than restating it: **no plugin system or entry points** (a tool is code in
this repository, registered by an app you install); **no MCP import** (this platform is not
yet a client of other MCP servers — that is the MCP-edge phase, and the export half is not
built either); **no row-driven tools** (a `Flow` is a row, an `Agent` is a row, a *tool* is
not — `ready()` may not read the database, which is why `flow.run` is one tool with
per-turn choices rather than one tool per row); **no grants UI** (`tool_keys` is edited out
of band until Identity & Auth). Close with: *the shape these gaps close in is recorded in
ADR 0015's named-gaps section; none of them changes the three steps above.*

**"See also"** — `agents/contracts/README.md`, ADR 0015, `docs/DEV.md`'s gate section,
`models/registry/tools.py` itself.

**The two link lines:**

- `README.md` has no "Extending" section; the nearest existing spot is the documentation
  list at `:100-104`. Add a bullet after `docs/ROADMAP.md`'s line (`:101`):
  `- [docs/EXTENDING.md](docs/EXTENDING.md) — add a tool to the platform in three steps`.
- `agents/contracts/README.md` — add one line under its opening (before `## The three
  modules`, `:8`) pointing at `docs/EXTENDING.md` as the walkthrough this README is the
  reference for. One line, not a section: this README is the contract's reference and must
  not grow a second, drifting tutorial.

**`docs/DEV.md`, the eleven stale spots:**

| Line | Now | Action |
|---|---|---|
| `:159-162` | sanity-check snippet imports `core.inference.gateway` and `modules.rag` | rewrite to `from models.contracts import gateway; from tools.rag import index, models` — **and run it** before committing |
| `:172` | "tests for the RAG module live under `modules/rag/tests/`" | `tools/rag/tests/`; add that every column has its own `tests/` and `pytest.ini:4` names all five roots |
| `:184-186` | already carries a correction ("`core/` has not existed since the P0 regroup; the seam is `models.contracts.gateway`") | leave as-is — it is a deliberate correction, not a stale path |
| `:261-267` | "**`core/tests/` is outside `testpaths`**" plus a `pytest -q core` recipe | **delete the whole block.** `core/` does not exist (`ls core` → No such file), and `testpaths` covers every directory that holds a test — verified by `find . -name 'test_*.py'`, whose every hit is under `tools/`, `models/`, `foundation/`, `agents/`, or `scripts/` |
| `:366` | `modules/rag/ingest.py::ingest_path` | `tools/rag/ingest.py::ingest_path` |
| `:426`, `:434` | `console.inference.bindings.chat_connections_for_picker` | `models.registry.bindings.…` |
| `:454`, `:459` | `modules.rag.jobs.run_ask`, `console.inference.models.ModelConnection` | `tools.rag.jobs.run_ask`, `models.registry.models.ModelConnection` |
| `:472` | `modules.rag.services.record_ask` | `tools.rag.services.record_ask` |
| `:580` | `core/inference/engines/ollama.py` | `models/contracts/engines/ollama.py` |
| `:595-600` | `console.inference.bindings.resolve_connection`, `core.inference.gateway.get_llm_for`, `modules.rag.retrieval.answer_question` | `models.registry.bindings.…`, `models.contracts.gateway.…`, `tools.rag.retrieval.…` |
| `:772` | `modules/rag/retrieval.py` | `tools/rag/retrieval.py` |
| `:994` | `modules/vision/templates/vision/_output_actions.html` | `tools/vision/templates/vision/_output_actions.html` |

§8's ladder and the `/chat/` smoke path: `docs/DEV.md:619-630` ("Using `/chat/`") and
`:631-669` ("Driving an agent turn from the CLI") were added by P3 and are current.
§12.5's ask is satisfied by **verifying** them plus adding one line to Rung 4
(`:357-363`) naming a `/chat/` conversation as an acceptable fresh-pixels subject.

**`tools/README.md:20`** — "…and `agents/contracts/` (once P1 lands them)" — drop the
parenthetical; P1 landed.

**`foundation/README.md:27-33`** — rule 2 currently says "The one sanctioned cross-column
import in the whole repo is `models.registry.bindings`… The second named exception IS in
this column", which contradicts itself in two sentences. Rewrite to: **two** named
exceptions in the whole repo — `models.registry.bindings`, importable by `tools/*` and
`agents/*` and by nothing else in this column; and `foundation.ops` → `models.queue.models`,
only for the active-work refusal. `foundation/ops/tests/test_import_law.py:10-11` names
this README's rule-2 text in its own docstring; **re-read that docstring after the edit**
and confirm it still describes what the README says (docstring only — the test asserts
against imports, not against prose).

**`foundation/README.md:41`** (M5) — "a `platform/` package here breaks `manage.py
collectstatic`" states flatly what is only conditionally true: nothing in this repository
runs `collectstatic` (T6). Qualify it — *would break `manage.py collectstatic` if it were
ever run, and does break a bare `import platform` today, which is the argument that
actually decides the name.* The identical unqualified claim at **`foundation/__init__.py:10`**
is a `.py` comment and therefore **out of a docs-only phase's budget: named here, not
edited**, and recorded in the commit message so the next `.py`-touching change can carry it.

**`docs/BUSINESS.md:173`** — "See the `modules/` split." → "See the `tools/` split."

**`tools/rag/tests/test_tools.py:1-29`** — the module docstring's premise is fixed (D9).
Rewrite the second paragraph to say: the cross-app leak it guards against was real until
PR #64, which changed `tools/vision/services.py` to import the *module*
(`services.py:26` — `:27`'s by-name import of `ResolvedModel`/`config_family` is
unchanged and irrelevant, since nothing patches either) and call `bindings.resolve(...)`
per call (`services.py:171`), so a
`patch("models.contracts.bindings.resolve")` now reaches vision's call site correctly. The
`import tools.vision.services` pin at `:36` is kept as belt-and-braces — a future
`from … import resolve` anywhere in the URLconf's import cascade would reintroduce the same
class of leak, and the pin costs one import. **Do not remove the import**; the docstring
must say why it stays, so the next reader does not delete it as dead.

### Steps

1. Read `foundation/ops/tests/test_docs_sync.py` in full (38 lines; the four assertions are
   `:23-38`) and confirm it reads only `docs/OPERATIONS.md` — the §12.5 instruction,
   discharged and recorded (D5).
2. Apply the `docs/DEV.md` rewrites in the table, verifying each new path with `test -e`
   or `grep -n` against the named symbol. Delete the `:261-267` block entirely.
3. Run the rewritten `:159-162` snippet verbatim and confirm it exits clean (a Postgres
   connection error is expected and acceptable; an `ImportError` is not).
4. Add the Rung 4 `/chat/` line; verify `:619-669` against the current `/chat/` surface by
   reading `agents/chat/README.md`, not by memory.
5. Apply the `tools/README.md:20`, `foundation/README.md:27-33`, `foundation/README.md:41`,
   and `docs/BUSINESS.md:173` edits. Re-read `foundation/ops/tests/test_import_law.py:1-20`
   afterwards. Record `foundation/__init__.py:10` in the commit message as named-not-edited.
5a. **Write `docs/EXTENDING.md`** to the outline in **Interfaces**, section by section.
   Before writing each code block, `sed -n` the lines it quotes out of
   `models/registry/tools.py` and paste them — **quote, never retype**, because a
   walkthrough whose example does not compile against the tree is worse than no
   walkthrough. The three ranges, re-verified 2026-08-29: the spec `:37-49`
   (`sed -n '37,49p'` — `:49` is the closing paren), the runner's signature and docstring
   `:52-59`, the lazy service imports `:60-61`, with `validate_tool_args` at `:63`. Then verify, in order: the two guard lists (`TOOL_MODULES` at
   `foundation/ops/tests/test_column_boundaries.py:185-189`, `_REGISTRATION_MODULES` at
   `:217-228`) still carry those names; the three guard tests are at `:443`, `:486`, `:346`;
   the two `>=` floors are at `models/registry/tests/test_registry_paths.py:80` and
   `agents/contracts/tests/test_toolschema.py:203`; and every file:line in the
   "what you get for free" table resolves to what the row claims.
5b. Add the two link lines — `README.md` after `:101`, `agents/contracts/README.md` before
   `:8` — and confirm both new links resolve.
5c. **Read `docs/EXTENDING.md` once as the newcomer it is written for**, with only that
   page open, and check the smoke-checklist claim: steps 1–3 are followable without opening
   another file. Anything that forces a detour is a missing sentence, not an acceptable
   cross-reference.
6. Rewrite the `tools/rag/tests/test_tools.py` docstring per **Interfaces**. Change nothing
   below the docstring.
7. Read the eleven verify-only READMEs. For each, record either "current" or a one-line
   note of what is stale. **If a stale claim is found, fix it here** — that is what this
   task is for — but do not restructure a README that is merely terse.
8. Repo-wide check that the sweep is complete:
   `grep -rnE '(^|[^a-z/])(modules|console|core)[/.]' --include='*.md' . | grep -v docs/adr/ | grep -v docs/superpowers/`
   — every remaining hit must be a deliberate historical sentence, and each is listed in
   the commit message.
9. Run the gate matrix — this task touches a `.py` file, so all four runs are mandatory and
   the reversed-order run matters most (the docstring sits in a module whose import order
   is the thing it documents).
10. Commit: `docs(p4): DEV, EXTENDING, column READMEs, and the two stale test-module notes`.

---

## Task 6: the `STATIC_ROOT` ruling

**Deliverable:** the P2-deferred `STATIC_ROOT` question is closed with a recorded reason,
and the condition that would reopen it is written down. Reviewable as a decision, not a
diff.

### Files

- `docs/adr/0015-agent-layer-and-tool-contract.md` (one paragraph, in Decision 1)
- `docs/DEV.md` (one line, in the deployment/status area)
- `config/settings.py` — **only if the ruling is overruled**

### Interfaces

The ruling, as it goes into ADR 0015 Decision 1:

> **`STATIC_ROOT` is deliberately unset.** `django.contrib.staticfiles` is installed
> (`config/settings.py:241`) and `STATIC_URL` is set (`:337`), but nothing in this
> repository runs `collectstatic`: the image's `CMD` is `migrate` + `uvicorn`
> (`Dockerfile:32`), no `RUN` layer collects, and no compose file, deploy script, or
> management command mentions it. `STATIC_ROOT` is required by `collectstatic` alone;
> setting it would name a directory nothing writes to and nothing serves from, which is a
> setting that documents an operation the platform does not perform. It is added by
> whoever first puts a real static-serving story in front of the app — a web server, a
> collected tree, a cache header policy — in that same change, not before.

### Steps

1. Re-verify the ruling's four factual claims. `grep -rn 'collectstatic' .` (excluding
   `.git`, `__pycache__`, and `docs/superpowers/`) returns **exactly two hits, and neither
   is an invocation**: `foundation/README.md:41` and `foundation/__init__.py:10`, both
   restating the `platform/`-shadowing argument. State that expected result here, so a
   third hit — a `RUN` layer, a compose command, a deploy script — is what makes the
   executor stop and re-open the ruling rather than something they have to notice. Then:
   `grep -n 'STATIC' config/settings.py` returns `:337` only; `sed -n '32p' Dockerfile`
   shows `migrate` + `uvicorn`; `grep -n 'staticfiles' config/settings.py` returns `:241`.
2. Write the paragraph into ADR 0015 Decision 1 and one line into `docs/DEV.md` recording
   the same ruling where a developer would look for it.
3. Run `python manage.py check` and record its output in the commit message. It is expected
   clean; this is the evidence that the ruling costs nothing.
4. **Contingency, if a reviewer or the owner overrules the ruling:** add
   `STATIC_ROOT = BASE_DIR / "staticfiles"` beside `STATIC_URL` at `config/settings.py:337`,
   add `staticfiles/` to `.gitignore`, run `python manage.py collectstatic --noinput --dry-run`
   and `python manage.py check`, run the **full four-run gate**, and replace the ADR
   paragraph with the value's rationale. That is the one settings edit P4 is allowed and it
   does not travel with any other change.
5. Run the gate matrix. Commit: `docs(p4): record the STATIC_ROOT ruling — unset, and why`.

---

## Task 7: the verification ladder

**Deliverable:** a merged PR, a live checkout that fast-forwards to it, and an owner's-eye
read-through that says the documentation is now true. No success language before the last
rung (`docs/DEV.md:192-363`).

### Files

- None edited by default. Fixes found on the ladder land as follow-up commits on the same
  branch, each named for the rung that found it.

### Steps

1. **Link check.** Give `docs/EXTENDING.md` its own pass first — it is the page with the
   most outbound file references and the only one whose value depends on every one of them
   resolving: check its `## See also` block, its two inbound links (`README.md`,
   `agents/contracts/README.md`), and **every `file:line` in its tables**, the same way step
   2 samples the ADR's. Then, for every markdown file P4 touched, extract each `](target)` whose
   target is a repo-relative path and `test -e` it, resolving relative to the file's own
   directory. Zero misses. Do the same for `docs/adr/0015-*.md`'s `## See also` block
   specifically — a new ADR's outbound links are the most likely to be wrong.
2. **Citation spot-check.** Sample twenty `file:line` citations across ADR 0015 and the two
   amendments — chosen to cover every file cited at least once — and `sed -n '<n>p'` each.
   Any miss means re-verifying every citation in that file, not just the miss.
3. **Cross-document consistency.** Three claims appear in more than one place and must
   agree word for word in substance: the nine registered tools (ADR 0015 Decision 2,
   `agents/README.md:11`); the inline-tools sentence (ADR 0015 Decision 5, the ADR 0013
   amendment, `agents/runtime/loop.py`'s module docstring); and the post-P4 phase order
   (ADR 0015 G13, `docs/ROADMAP.md:312-337`). Read all three sets side by side.
4. **No model names.** `grep -rniE 'flux|qwen|klein|lightning|sdxl|gguf|llama[- ]?[0-9]|mistral|gemma|phi-?[0-9]|deepseek|[0-9]+b\b'`
   over every file P4 touched. Engine and framework names (Ollama, ComfyUI, whisper.cpp,
   LlamaIndex, pgvector) are allowed and expected — `docs/ROADMAP.md:14-15`, `:27`, `:63`,
   `:207` are exactly those. Every *other* hit is a false positive or it comes out. **Scope
   note:** for `docs/ROADMAP.md` the scrub obligation is Phase 1.55 (`:76-199`) plus any
   text P4 itself wrote; a hit elsewhere is logged for step 10, not fixed here (amended
   D11).
5. **The gate matrix**, all four runs, one at a time, from a clean tree.
6. **`python manage.py check`** — clean.
7. **`git diff --stat main`** — confirm the diff is `.md` plus exactly one `.py` docstring
   (plus `config/settings.py` only if T6 was overruled). Any other file is scope creep and
   comes out before the PR.
8. **PR.** Open it with `gh`, body summarising the fifteen decisions, the thirteen named
   gaps, and the twelve deviations, and linking the spec. Request review.
9. **Live.** After merge, fast-forward the primary `/app` checkout to the merged `main` SHA
   (`docs/DEV.md:347-356` — main SHAs only, container git operations serialized). **No
   restart is required**: the diff touches no Python that any service imports, and the one
   `.py` change is a test docstring. If T6 was overruled, `web` **must** be restarted for
   the settings change and the restart is part of this step.
10. **Owner's-eye read-through, the last rung.** Read, in this order, as someone who has
    not seen the code: `README.md` → `docs/ARCHITECTURE.md` → `docs/ROADMAP.md` →
    `docs/adr/0015-*.md` → `agents/README.md`. Three questions to answer out loud: does the
    reader learn what the platform is; does any sentence claim something that is not built;
    is any built thing invisible. Surface to the owner: the ROADMAP Phase 1.6 preamble and
    `v1` rewrite (T3 step 5, controller-ruled but worth a second pair of eyes), and the
    model names still standing **outside** ROADMAP Phase 1.55 — ADR 0012's headings and
    body, ADR 0014, the vision plans, the spec — which amended D11 leaves as a separate
    owner decision with its own diff.

---

## Smoke checklist

- [ ] `docs/adr/0015-agent-layer-and-tool-contract.md` exists, opens with
      `# ADR 0015 — `, and carries `## Context`, `## Decision`, fifteen `### N.` subsections,
      a named-gaps section, `## Consequences`, `## See also`.
- [ ] ADR 0010 and ADR 0013 each end with a `## Amendment (2026-08-29) — Amended by ADR 0015`
      section, and ADR 0015's `## See also` links back to both.
- [ ] `grep -rnE '(^|[^a-z/])(modules|console|core)[/.]' --include='*.md' .` outside
      `docs/adr/` and `docs/superpowers/` returns only deliberate historical sentences
      (`docs/ROADMAP.md:279`, `:300`) and the `:387` English-period false positive, all
      enumerated in T5's commit message.
- [ ] `ls core` fails, and `docs/DEV.md` no longer tells anyone to run `pytest -q core`.
- [ ] ROADMAP Phase 1.55's R1 and R2 carry the verbatim closure text and remain `[x]`, and
      `sed -n '76,199p' docs/ROADMAP.md | grep -niE 'flux|qwen|klein|lightning|sdxl|gguf'` is empty.
- [ ] ROADMAP Phase 1.6's preamble no longer says "None of this is implemented yet", links
      both ADR 0010 and ADR 0015, and `v1` is still `[ ]` with a grounding-by-tool-choice
      opening sentence.
- [ ] `docs/ARCHITECTURE.md` §4 has an **Agent runtime** row; §5's list is titled
      "Tools today".
- [ ] `README.md`'s repository layout describes `agents/` as shipped, not as empty.
- [ ] **A reader can follow `docs/EXTENDING.md` against the tree without opening another
      file for steps 1–3** — the spec, the runner, the `register_tool` line, and both guard
      lists are all quoted on the page — and every code block in it matches
      `models/registry/tools.py` byte for byte. It is linked from `README.md` and from
      `agents/contracts/README.md`, and names no model.
- [ ] `python manage.py check` clean; the four-run gate green; `git diff --stat main` is
      `.md` + one `.py` docstring.
- [ ] No model name or version anywhere in the diff.

---

## Self-review

**Spec coverage.** Every §12.5 bullet and every "ADR 0015 must…" sentence in the spec is in
the coverage table with a task. The five "must" sentences (`:108-111`, `:179-182`, `:1393`,
`:1791`, `:1965`) map to T1 Decisions 5, 13, 5, 13, and 14 respectively, with `:179-182`
also driving T2 and `:1791` carrying deviation D12. §14's seventeen gaps are folded into
T1's fourteen entries (G1–G13 plus G12b): **G12 collects ten of them** (§14 gaps 1–10,
inherited unchanged and not re-litigated), **G12b discharges one** (gap 12, answered by
P1's live probe), and the remaining six (11, 13–17) are carried by G6, G13, Decision 14,
and the phase-order paragraph. The collection is explicit, not an omission.

**Placeholders.** No step says "update as needed", "etc.", or names a file without saying
what changes in it. Every path rewrite is in a table with a from and a to. The two verbatim
blocks (R1, R2 closure text) are reproduced in full so the executor never has to go looking
for them. The ADR outline is written out to the level of each subsection's claim and its
evidence, so a reviewer can reject a decision before it costs a page of prose.

**Consistency.** The gate matrix appears once, in Global Constraints, and is referenced by
name thereafter. `08cd21f` is the only base SHA named. The ADR slug
(`0015-agent-layer-and-tool-contract.md`) is identical in T1, T2, T4, T6, T7, and the smoke
checklist. Deviations D1–D12 are each referenced from the task that acts on them, and no
task contradicts one.

**Right-sizing.** Seven tasks, each ending in one commit and one reviewable artifact: an
ADR (T1), two amendments (T2), a roadmap (T3), the two front-door documents (T4), the
developer documents (T5), one recorded ruling (T6), the ladder (T7). T6 is the smallest and
stays separate because its deliverable is a *decision* with a contingency, and burying a
settings decision inside a README sweep is how a settings change gets made by accident.

**The risk this plan carries.** A documentation phase's failure mode is confident prose
about code nobody re-read. Six of the brief's own "binding facts" turned out to need
correction against the tree (D2, D3, D4, D5, D6, D7, D9, D10), which is the measured rate at
which second-hand facts about this repository go stale. Hence the rule stated three times
and mechanically checked twice: **open the file at the line before writing the sentence.**

---

## Plan review

### Round 1 — AMEND (8 MAJOR / 12 minor). Author applied all findings, plus two controller rulings.

Controller rulings folded in: Phase 1.6's preamble is rewritten alongside the `v1` bullet
(M2/Q1), and D11 is narrowed rather than deferred — the model-name scrub is performed
inside ROADMAP Phase 1.55 and nowhere else (Q2). One reviewer citation was corrected
against the tree rather than adopted: ARCHITECTURE §1's closing sentence is at `:40-41`,
not `:42-43` (`grep -n 'The line we own'` → `40`); the discrepancy is recorded in T4's
Interfaces so a later reader sees it was checked, not missed. Round 2 withdrew that
finding.

### Round 2 — AMEND (1 MAJOR / 6 minor). Author applied all findings.

The MAJOR was a real miss in this plan's own scrub table: `docs/ROADMAP.md:139` carries a
distilled-variant product name one line past where the table stopped, so that row now runs
`:136-139` and `lightning` joins both grep patterns. The six minor items were citation
corrections (`0010:534-537` in T2's step 2; spec `:2389-2416` with gap 11 at `:2417` for
G12; spec `:2544-2554` for G12b; ARCHITECTURE's diagram fences at `:22`/`:38`, so `:40` is
no longer double-assigned), one count (ten path rewrites, not nine), and one
Global-Constraints clause naming the Phase 1.55 scrub as the exception to the no-scrub
rule.

### Round 3 — AMEND (0 MAJOR / 1 minor) → orchestrator ruling: ACCEPTED and applied
All round-2 edits verified against the real files. The one residual — the two remaining
model-name greps (T1 step 7, T7 step 4) lacked `lightning` — was applied by the
orchestrator directly, so all four model-name greps in this plan share one vocabulary.
The plan is executable.

### Round 4 (owner amendment, 2026-08-29) — one new deliverable added to T5

`docs/EXTENDING.md`, "Adding a tool in three steps": a worked walkthrough for someone new
to the repository, using the real `models.status` tool (`models/registry/tools.py`) as the
example because it is the smallest one that exercises the whole contract. Specified in T5's
Interfaces to the level of each section's content, each quoted code block's source lines,
and every file:line in its two tables — no placeholders. Added to the coverage table as
doc-sweep scope, to T5's Files and Steps (5a–5c), to T7's link check as its own first pass,
and to the smoke checklist. **One verification changed a specified step:** the amendment
asked for "bump the registration floor if one exists" — there is no per-tool registration
floor. The two `>=` floors that exist (`models/registry/tests/test_registry_paths.py:80`,
`agents/contracts/tests/test_toolschema.py:203`) are anti-vacuous and deliberately loose,
and one new tool moves neither; `agents/contracts/tests/test_tools.py:169`'s
`test_it_delegates_to_the_platforms_one_floor` is a *validation* floor, not a counter. The
page says so rather than teaching a step that does not exist.

Scoped re-check: 1 major (off-by-one quote ranges) + 6 minor citation fixes applied;
orchestrator adjudicated CLEAN. One correction went the other way against the tree, as
round 1's ARCHITECTURE case did: the file-param rationale is cited **`:43-46`**, not
`:44-46` — `:43` is the line that actually states `"file"` is excluded, and dropping it
would cite the parenthetical without the claim (`sed -n '43,47p' agents/contracts/tools.py`).
