# Attachments Integration Land (PR #84) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Land PR #84 per the owner's ruling — as an INTEGRATION: main's message-bound attachment system stays the one attachment system; this branch folds main `f4e69f3` forward, retires every piece of the original chat-attachments design that main's later rounds independently rebuilt, and lands the one capability main still lacks — **catalog-gated native image input to the chat model** — seated on main's own seams.

**Architecture:** A fold-forward merge with a TREE-LEVEL resolution policy (the merged tree is byte-identical to main's except five named paths), then two small tasks: a citation repoint in the catalog (whose `accepts` field rides the merge already), and a gated native-image branch in the prompt's carrying-attachments machinery, consuming the artifact-file resolver registry main already provides.

**Spec:** `docs/superpowers/specs/2026-09-03-chat-attachments-design.md` — historical; this plan's Supersession Ledger is the current truth. Binding authorities: the owner's "land #84" ruling, AGENTS.md, and main's own attachment-system docstrings (`agents/attachments.py`, `agents/contracts/attachments.py`, `agents/contracts/artifacts.py`, `agents/runtime/prompt.py::_carrying_attachments_block`).

## Supersession Ledger (what the merge retires, and why)

Every row verified against `f4e69f3` by the adversarial plan review (2026-09-20):

| #84 piece | Superseded by (on main) |
|---|---|
| `ConversationFile` model + `agents/store.py` + migration | Document-backed staging: `stage_turn_attachments` seam, managed store on the rag side |
| Multipart form + `files=` on `start_turn` | Main's `start_turn(files=..., placement=...)` + the shared `_attach_files.html` composer fragment |
| `chat-file` endpoint + serving clamp | `rag-document-file` serving discipline (`readable_document` gate, text-like inline set, Range/206). The branch's SVG inline clamp loses nothing: no rag-supported extension admits an SVG, so `document_file` can never serve one — verified, not assumed |
| `file:<id>` artifact kind | Attachments are `document:` rows; `document:` is a first-class vision ref |
| Extraction module + sidecar + excerpt cap | `inline_attachment_text` seam + per-file/total budgets + async ingest |
| `file:`→`input:` vision rewrite in the loop | Vision's `image` param accepts `document:<id>` via `_stored_document_input` |
| `plan_turn` extraction roles (`_attachment_roles`) | Extraction is model-free inline + async ingest; no in-turn model roles |
| A/V transcription in-turn | The ingest pipeline owns A/V; the prompt renders `_INLINE_STILL_PROCESSING_LINE` — "attached, still processing — content will be retrievable shortly" |
| The proposed image-path contract slot | ALREADY EXISTS: `agents/contracts/artifacts.py::register_artifact_file_resolver` / `file_resolver_for` with `ArtifactFile(path, name, media_type)`; rag registers `("document", "tools.rag.access.artifact_file_for")`, gated on `readable_document`; `tools/vision/services.py::_stored_document_input` is the consumption template |

**Survives:** `CatalogEntry.accepts` + its tests (ride the merge untouched), and the native-image prompt path (Task 3).

## Global Constraints

- Branch `chat-attachments`, worktree `<worktree>` = `.claude/worktrees/chat-attachments`. NEVER the repository root checkout (it is production).
- AGENTS.md binds everything: no absolute local paths or personal data in committed prose; no AI model/vendor names in prose (code identifiers are fine); WHY-comments cite by anchor or grep pattern, not line number; tests + docs in the same commit; conventional commits; a deliberate test re-pin is named in its commit message.
- Import law: `agents/` never imports `tools/`; every cross-column reach uses the existing dotted-path registries.
- Gates: the FOUR runs exactly as AGENTS.md's Quick reference writes them (`FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q`, `FARABUNKER_FEATURES='vision' .venv/bin/pytest -q`, and both again with `scripts identity agents foundation models tools`). `DATABASE_URL` points at this branch's preview Postgres (port 5436), db name unique to this session; one pytest process at a time.
- The agents/chat steward receives a per-file hunks packet BEFORE any merge request goes to the owner; the owner's merge word must arrive in the driving session's own conversation.

---

### Task 1: The fold-forward merge (tree-level policy)

**The policy (binding):** the merge commit's tree MUST be byte-identical to `origin/main`'s except for exactly FIVE paths:
1. `docs/superpowers/specs/2026-09-03-chat-attachments-design.md` (branch-only; kept for Task 4's truth pass)
2. `docs/superpowers/plans/2026-09-03-chat-attachments.md` (branch-only; ditto)
3. `docs/superpowers/plans/2026-09-20-attachments-integration-land.md` (this plan; branch-only)
4. `models/contracts/catalog.py` (the branch's `accepts` field + stamps — auto-merges; keep the branch side)
5. `models/registry/tests/test_catalog.py` (the field's pin + default test — auto-merges; keep the branch side)

Everything else — the 19 conflicted files AND the ~18 clean-merging branch-touched files (`agents/runtime/jobs.py`'s `_attachment_roles`, `agents/chat/rendering.py`'s `file`-kind branch, `identity/routes.py`'s `chat-file` row, `identity/contracts/ownership.py`'s count prose, `config/settings.py`'s attachment block, the README sections, every branch-added test in shared modules, `identity/tests/_helpers.py`, `identity/tests/test_owned_rows_registry.py`, `agents/tests/test_models.py`, `agents/tests/test_apps.py`, `agents/chat/tests/{test_turn_create,test_thread,test_conversation_actions,test_rendering}.py`, `agents/runtime/tests/test_jobs.py`) — resolves to MAIN'S content. Two of the clean-merging keepers would otherwise be fatal (`jobs.py` imports the deleted extraction module; `routes.py` names a deleted route), which is why the policy is tree-level, not conflict-level.

- [ ] Step 1: `git merge --no-commit origin/main`. Expect 19 conflicts; do NOT hand-resolve them — Step 3 covers them (`git checkout --theirs` would leave paths unmerged and block the commit; probed). The three `docs/superpowers` files are branch-only additions with no conflict stages — they need NO action (never `git checkout --ours`, which would error). The conversation.html poll-copy residuals are re-added in Task 4 on main's shape.
- [ ] Step 2: `git rm` the seven branch-only superseded files: `agents/store.py`, `agents/tests/test_store.py`, `agents/chat/views/files.py`, `agents/chat/tests/test_files_view.py`, `agents/runtime/attachments.py`, `agents/runtime/tests/test_attachments.py`, `agents/migrations/0006_conversationfile.py`. The migration `git rm` is LOAD-BEARING: main's chain ends at `0011_turn_author` and holds its own `0006_workstream` — the branch's `0006_conversationfile` collides by NUMBER as a no-conflict add/add, and only this removal resolves it.
- [ ] Step 3: `git checkout origin/main -- <path>` for EVERY path in `git diff --name-only 4abef8a chat-attachments` except the five kept and the seven deleted — 35 paths, the 19 conflicts INCLUDED (this form both resolves and stages each one; the branch has no deletions against the merge base, so this set is complete).
- [ ] Step 4: Pre-commit gate: `git diff --stat origin/main` (worktree form — valid with the merge in progress) must list EXACTLY the five kept paths; any sixth line = a missed resolution, fix before committing. Re-confirm with `git diff --stat origin/main HEAD` after Step 6's commit.
- [ ] Step 5: Focused suite `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest agents identity foundation models -q` — green proves main's system arrived intact and the catalog additions hold.
- [ ] Step 6: Commit: `merge: fold main into chat-attachments — integration land; superseded machinery retired per the plan's Supersession Ledger`.

### Task 2: Catalog citation repoint

The merge already carries `CatalogEntry.accepts`, its two stamps, and both tests. The only work: the WHY-comment in `models/contracts/catalog.py` and the docstring in `models/registry/tests/test_catalog.py` cite the deleted `agents/runtime/attachments.py::native_media_types` — repoint both to `agents/runtime/prompt.py::native_media_types` (Task 3's home).
- [ ] Edit the two citations; run `.venv/bin/pytest models/registry/tests/test_catalog.py -q`; commit `docs(models): repoint the accepts citation at its surviving seam`.

### Task 3: Native image input, gated, with still-processing fallback

**Files:** `agents/runtime/prompt.py`, `agents/runtime/loop.py`, `agents/attachments.py`, + `agents/runtime/tests/test_prompt.py`, `agents/runtime/tests/test_loop.py`, and `agents/tests/test_attachment_seam.py` — the existing home for `agents/attachments.py`'s never-raise seams — for the helper.

**Interfaces (binding):**
- `agents.attachments.attachment_image_path(document_id, principal) -> str | None` (~15 lines): resolves `file_resolver_for("document")` by dotted path, returns `artifact.path` ONLY when `artifact.media_type.startswith("image/")` (the `_stored_document_input` rule — the row's `is_image` is the pre-filter, media type is the decision) and the path is a file; `None` on `LookupError` or any exception — the module's standing never-raise posture. WHY-comment cites `_stored_document_input` as precedent.
- `agents/runtime/prompt.py::native_media_types(resolved) -> frozenset[str]`: `catalog.find(resolved.model_id)` → `accepts`; unknown model or `None` → empty set. (models/contracts import is legal from agents.)
- `build_messages` gains keyword-only `resolved=None`; `agents/runtime/loop.py::_run_turn` passes the `resolved` it already holds from `resolve_chat` (one-line change at its `build_messages` call). `agents/runtime/delegate.py` has its own prompt path and is deliberately out of scope.
- `_carrying_attachments_block(attachments, carrying_turn_id, *, resolved=None, principal=None)` returns `list[ContentBlock]` instead of `str`; `build_messages` passes BOTH new keywords — it already holds `principal` for its `attached_documents` call. `principal is None` disables the native branch entirely (every existing caller and test keeps today's behaviour). The principal is NOT optional to the gate: `artifact_file_for`'s `readable_document` check is what keeps another uploader's chat-scoped bytes out of this message. `build_messages` emits `ChatMessage(role=USER, blocks=...)` for the carrying message; with NO image blocks the list is `[TextBlock(text=<today's exact string>)]`, whose `.content` is byte-identical to today's — the gate-closed path must not change one byte of prompt text. Imports: `ContentBlock` comes from `llama_index.core.base.llms.types` — the same import line `ToolCallBlock` already uses; `TextBlock`/`ImageBlock` from `llama_index.core.llms` beside `ChatMessage`.
- The native branch, CARRYING TURN ONLY (replay stays reference-only, main's own ruling): for each carried image row where `"image" in native_media_types(resolved)` and `attachment_image_path(...)` returns a path — construct the `ImageBlock` AND force `resolve_image()` inside the try AT BUILD TIME (a readable-but-broken file must fall back before the message list leaves the function; construction alone does not validate bytes), that file's line in this block — today `_INLINE_STILL_PROCESSING_LINE` (an image never has inline text: `inline_text_for` handles `PROSE_EXTS` only) — becomes the one-line "attached image provided to you directly" marker. `_attachments_block` and `_attachment_line_tail` are UNTOUCHED: line-tail branch (f) already drops the description clause for a carrying-turn row, so nothing there duplicates the image. ANY failure → that file keeps today's `_INLINE_STILL_PROCESSING_LINE` sentence; never fails the turn; logged.
- **Bounds:** at most `_NATIVE_IMAGE_COUNT_CAP` images per turn and `_NATIVE_IMAGE_BYTE_CAP` bytes each (stat the path first); over-cap falls back to that file's `_INLINE_STILL_PROCESSING_LINE` like any other failure. Both constants are concrete literals, reasoned in-comment against base64's ~4/3 expansion inside the worker process — the same unified-memory hazard `tools/rag/access.py::_INLINE_READ_SIZE_CAP_BYTES` was added for; read that constant's docstring and pick values in the same spirit (count cap in the low single digits, byte cap in the low tens of MB).
- **KNOWN LIMIT (state in code comment + ADR):** the gate reads the catalog, which recognises a fixed set of model ids; a multimodal connection outside it gets the still-processing fallback even though discovery reports its vision capability. Widening the gate means putting the capability set on `ResolvedModel` — a models-column change, deliberately not this plan's business.

**Tests:** gate open + path loads → image block present and the still-processing line replaced by the marker; gate closed → the exact `_INLINE_STILL_PROCESSING_LINE` sentence, byte-identical; a row whose `readable` is False contributes no block; path missing/unloadable/oversized/over-count → fallback line, no raise; an OLDER turn's image contributes no block; the helper returns `None` for a non-image and for `LookupError`; loop threading pin (`resolved` reaches `build_messages`). Monkeypatch the seams; one test drives the real catalog gate via a temporary entry (try/finally-restored).
- [ ] TDD; commit `feat(agents): native image input to capable chat models, still-processing fallback`.

### Task 4: Docs truth pass + residuals + scrub

- [ ] The two 2026-09-03 branch docs: STATUS header ("historical: superseded in part; landed as the integration in `2026-09-20-attachments-integration-land.md`"), absolute-path scrub (`<repo>`/`<worktree>`/`<home>`), model-name-in-prose scrub — the AGENTS.md gate module must pass over them.
- [ ] ADR 0015: ONE dated amendment (2026-09-20) — native-image capability gate, the artifact-file-resolver consumption, the supersession of the 2026-09-03 design by the message-bound rounds, and the KNOWN LIMIT from Task 3. No model names.
- [ ] Vision's two parked conversation.html residuals, FOLDED on main's shape: (a) the timeout admin-hint on the poll path becomes a real Settings link where the failed-card pattern already links Models; (b) the stale "10 minutes" poll copy updated to describe the configured ceiling. Each named as a deliberate re-pin in the commit if a test pins the copy.
- [ ] `agents/README.md`: one seam-cited sentence for the new capability in the attachments section, only if the section is otherwise accurate post-merge.
- [ ] Commit `docs(agents): integration-land truth pass — supersession status, scrubs, ADR amendment, poll-copy residuals`.

### Task 5: Gates + smoke checklist

- [ ] The FOUR runs, green, counts recorded vs main's own baseline (establish the baseline first by running the same four on a clean `origin/main` checkout if unknown).
- [ ] `## Smoke Checklist` (by hand, in a browser, on this branch's preview stack — restart it against the folded branch; the worktree `.env` needs the box's `POSTGRES_PASSWORD` value before `scripts/preview` renders): (1) attach an image + a text file via the composer, see chips, submit; (2) the answer references the text file's content (inline path); (3) with a connection whose model id tag-matches a catalog entry carrying `accepts`, reporting chat among its capabilities, bound as the chat model: ask what is in the attached image — a real answer proves native bytes arrived, since with the gate closed the prompt only ever says "attached, still processing" for an image, whatever its extraction state; (4) with a text-only model bound, the same question gets the still-processing line with no error; (5) pass the image's `document:` ref to the image tool from chat and get a generation.
- [ ] Whole-branch review (most capable tier) over the full integration diff (merge + tasks), pointed at this plan; then the per-file hunks packet to the agents/chat steward; then the owner's merge word in the driving session.

## Execution notes
- Tasks strictly sequential. The old SDD workspace (2026-09-03 plan) stays as history; this plan gets its own workspace/ledger.
- If the public-prep scrub branch lands on main mid-execution, fold main forward again (same tree-level policy) before Task 5.


## Plan review

Adversarial hygiene review (most-capable tier, read-only, verified against origin/main f4e69f3 and the branch), 2026-09-20:

- Round 1: AMEND, 9 findings — most severe: the conflict-level merge policy missed 18 clean-merging files keeping superseded code (two fatally importing deleted modules); the proposed contract slot duplicated main's existing artifact-file-resolver registry; Task 2 already rode the merge; loop threading and build-time byte validation gaps. Applied as a full rewrite (8c64678): tree-level five-path policy, registry reuse, definitive diff-stat gate.
- Round 2 (scoped): AMEND, 7 findings — principal threading for the readable_document gate; "caption" language named text that does not exist on the carrying path (_INLINE_STILL_PROCESSING_LINE is the real target); probed merge mechanics (checkout-from-ref resolves AND stages; --theirs stalls the commit); ContentBlock import home; concrete bound literals; helper test home; ledger quote. Applied in 5368edc.
- Round 3 (scoped, final): AMEND, 1 mechanical residue — five surviving "caption" nouns + a strictly stronger smoke discriminator. **Adjudication at the 3-round cap: ACCEPTED and applied in this commit.** All seven round-2 amendments and the merge-mechanics arithmetic (35 paths, all present on origin/main) were independently verified by the reviewer. Plan cleared for execution.
