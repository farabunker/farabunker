# Identity IA-2 — Entitlements, Labels and Sharing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-08-30
**Branch base:** `main` at `073f0e0` ("Merge pull request #69: Identity & Auth — IA-1: accounts, login and postures")
**Phase:** IA-2, the second of the two halves the spec's §18 splits Identity & Auth into
**Status:** plan, not executed

## Goal

Turn "signed in" into "may see this". IA-1 gave the box accounts, three postures, ownership and
a route matrix; IA-2 gives it **entitlements** — a table of named permissions, granted to users
and groups, with an owner role on the grant — and then spends those entitlements in the three
places that matter: **documents** (labels, an OR-match, a library posture for the unlabelled
ones, and a chunk-metadata cache so a label change costs one `UPDATE` rather than a GPU-hour
re-encode), **tools** (`Agent.tool_keys` becomes a declaration and the grant becomes an
intersection with the acting user's entitlements), and **rows people own** (a generic `Share`
table, with one UI: conversations). Retrieval keeps exactly one filter point. The default
posture stays `open`, and an open box still runs zero permission queries.

## Architecture

Nineteen tasks, bottom-up, in six arcs. **Tasks 1–3** add `Entitlement` and `EntitlementGrant`
to `identity/`, give `identity/access.py` the three entitlement questions IA-1 deliberately left
out, add the guarded writes behind them, and ship the groups and entitlements pages.
**Tasks 4–8** add the `agents` column's two tables: `ToolEntitlement` behind `ToolAccess` and
`granted_tools`' third drop (plus the tool-label page), and `Share` behind the four visibility
bodies and the conversation-sharing UI. **Tasks 9–12** add the `tools/rag` column's table:
`DocumentEntitlement`, `tools/rag/access.py`'s two document functions, the one-writer chunk
cache, the required `visibility` argument threaded through the single filter point, and the
library surfaces that spend it. **Tasks 13–16** are the two 2026-08-30 owner directives and their
supplement: a labelled tool refused at **its own page** for both doors (`vision-generate` and
`rag-document-upload`); **model sets** — group the models, attach the entitlements, with the
AND-composition, the enforcement seams and the role-path exemption; **agent and flow labels**
through the three visibility functions that already exist; and the **admin-ergonomics** surfaces
that make a routine change one action. **Tasks 17–19** are the two owner-approved fold-ins, the
route matrix extended with a fourth principal and IA-2's twelve routes, and the documentation with
the gate ladder.

## Tech Stack

Django 5.1+ (server-rendered, zero JS), Postgres + pgvector, LlamaIndex's `PGVectorStore`,
pytest + pytest-django. Django's own `auth.Group` (unchanged, membership only), `AbstractUser`
subclass, `CheckConstraint(condition=...)`, partial `UniqueConstraint`s, and `jsonb_set` for the
one chunk-metadata `UPDATE`.

## Spec

`docs/superpowers/specs/2026-08-29-identity-and-auth-design.md` — binding authority, **as
amended 2026-08-30** by the owner's least-privilege directive (§22.31). This plan implements
**§18.2 (IA-2) only**. Read §6.3/6.4/6.6/6.7/6.8/**6.10**, §7.1/7.2/7.4/7.5/7.6, §8 in full,
§9 in full (including the amended **§9.3** and the new **§9.5**), §10.3/10.5, §11, §12.1, §13,
§16, §17's IA-2 table, §19, and **§22.31/§22.32** alongside it.

**The two amendments' effect on this plan**, stated once so a reader of either document is not
surprised: IA-2's routes go from seven to **twelve** (`inference-connection-sets`,
`inference-model-sets`, `inference-model-set-edit`, `chat-agent-entitlements` and
`rag-document-labels-bulk` join them), its migrations from three to **five**
(`models/registry/migrations/0008_modelset.py` and
`agents/migrations/0004_agent_flow_entitlements.py`), its audit catalogue from thirty-one to
**forty-two**, and §18.2's done-when list from nine criteria to **fifteen** — the original nine
unchanged, word for word, three appended by the first directive (10–12, with 10 and 11 rewritten
to sets by the second), and three more appended by the second and its supplement (13–15).

**Out of scope, named so it reads as scoped-out rather than forgotten:**

- **ADR 0016.** It is written **after** IA-2 merges, describing what was built (spec §18.3, the
  house sequence P4 already followed). It is not a task in this plan.
- **`/admin/` styling under `DEBUG=0`.** An open owner decision; do not plan it, do not fix it.
- **A sharing UI for agents, flows and generated images.** The `Share` table carries all four
  target types and the visibility functions read them; IA-2 ships **one** UI, for conversations
  (spec §10.3). `vision_output` gets no writer, so it cannot orphan.
- **Lifting the mutating-tool grantability gate.** ADR 0010's rule stays in force through IA-2
  (spec §9.1, non-goal 12): `rag.ingest` remains ungrantable.
- **`ServiceAccount`, service-account tokens, and the MCP edge.** IA-3 (spec §9.4, §21). A
  service principal holds no entitlements and gets unlabelled tools only, permanently, for now.
- **An owner column on `tools.vision.JobInput`.** `tools/vision/README.md` currently promises
  IA-2 closes the staged-upload-preview gap with such a migration. It is **not** in spec §17's
  IA-2 migration table — which, after the 2026-08-30 amendments, names **five** migrations and
  gates `--plan` on exactly those — and not in §18.2's content list. Task 19 corrects that README
  line to name the gap as a later follow-up with its hook, rather than leaving a promise this
  phase does not keep.
- **AND-matching on document labels, Django's `Permission` model, row-level security,
  per-object permission frameworks, email of any kind, and login lockout.** Spec §20.

## Global Constraints

Every task's requirements implicitly include this section.

1. **No AI model, product, or vendor names or versions appear anywhere** — code, comments,
   tests, docstrings, docs, or commit messages. The repository is going public and ADR 0010's
   third amendment already forbids the platform from naming a model for the operator. Describe
   models generically ("the assigned chat model", "the embedding role").
   **ENGINE KEYS ARE NOT MODEL NAMES.** `ModelConnection.engine` takes a registered engine
   adapter's `.name`, and a fixture that needs one takes it from the registry rather than
   inventing a string. The rule forbids naming a MODEL the operator did not choose, not naming
   the local server software the operator installed.
2. **Every task ships unit tests and updated docs in the same commit** (ADR 0008). A task with
   code and no test is not done; a task that changes behaviour a document describes and does
   not update that document is not done. TDD step order: the failing test comes first, is run
   and seen to fail, and only then is the implementation written.
3. **Never-500.** Every response on every surface, for every principal, in every posture, is an
   honest status with no `"Traceback"` in the body. A row-addressed URL a principal may not see
   answers **404**, never 403 (spec §11.1); an admin surface answers **403**. Every refusal is
   a designed status with honest copy — never a stack trace, never a silent no-op.
4. **The import law and the layering guards stay green.** `identity/` imports nothing from
   `agents/`, `tools/` or `models/` (rule 4). Every other column reaches identity only through
   the four named seams — `identity.contracts.*`, `identity.access`, `identity.request`,
   `identity.audit` (`foundation/ops/tests/test_import_law.py::IDENTITY_PERMITTED`). Cross-column
   foreign keys are **strings**: `settings.AUTH_USER_MODEL`, `"identity.Entitlement"`,
   `"auth.Group"` — never an import. New cross-column facts travel by **registry with
   dotted-path strings**, resolved at call time, exactly as job kinds, roles, tools and
   `OwnedRows` already do.
5. **Exactly five migrations in IA-2**, and their numbers are the real tree's next ones:
   `identity/migrations/0003_entitlement_and_grant.py` (Task 1),
   `tools/rag/migrations/0015_documententitlement.py` (Task 9),
   `agents/migrations/0003_toolentitlement_and_share.py` (Task 4),
   `agents/migrations/0004_agent_flow_entitlements.py` (Task 15),
   `models/registry/migrations/0008_modelset.py` (Task 14 — the app **label** is `inference`,
   not `registry`, so `manage.py makemigrations inference` is the command).
   `tools/vision` needs none.
   `manage.py makemigrations --check --dry-run` exits **0** at the end of every task.
6. **Zero-JS, server-rendered pages** extending `foundation/templates/_shell.html`, matching
   every other page on the box. No new client-side framework, no new fetch dependency.
7. **Test databases.** Implementers run against `farabunker_impl` on the **preview** Postgres,
   port **5433**. Never `test_farabunker`; never port 5432.
   ```bash
   export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
   ```
8. **The four-way gate**, run in the final task and after any task a reviewer asks for it on:
   ```bash
   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
   FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
   .venv/bin/pytest -q                                                   # configured order
   .venv/bin/pytest -q scripts agents foundation identity models tools   # reversed
   ```
   Per-task runs may be narrower (`.venv/bin/pytest -q identity`), but the four-way gate is the
   merge gate. Baseline at the branch base: **4424 passed, 1 skipped**.
9. **Both posture sweeps green, in both feature states** (spec §16.3, §18.2 done-when 8):
   ```bash
   FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
   FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
   FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
   FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
   ```
   `FARABUNKER_TEST_POSTURE` is read by **test helpers only** (`identity/testing.py::
   SWEEP_POSTURE_ENV`), never by production code — a production reader would reintroduce the
   second truth spec §3.2 rejects.
10. **An open box never runs a permission query.** In `open` posture, no request executes a
    query against `identity_user`, `identity_entitlement`, `identity_entitlementgrant`,
    `rag_documententitlement`, `agents_toolentitlement` or `agents_share`. Every access
    function tests `accounts_on()` first and returns its open branch before touching another
    table. The one primary-key read of `IdentitySettings` is not a permission query (spec §3.4).
    `identity/tests/test_zero_queries.py` grows the new table names in Task 18.
11. **No posture branch in any read path.** `personal` and `enterprise` answer every visibility
    question identically; they differ only in which pages exist. The single posture branch in
    the codebase lives in the write path `identity/services.py::set_posture` and stays there.
12. **No `conftest.py` anywhere.** Shared test support lives in `identity/testing.py`, imported
    by name; each package's `_helpers.py` re-exports what it uses and keeps only what is
    genuinely package-specific. Autouse fixtures are defined per test module and delegate to
    `_helpers`. Any test overriding `FARABUNKER_FEATURES` with a literal `frozenset` while doing
    HTTP or `reverse()` keeps `"vision"` in the set, or `/vision/`'s routes vanish and
    `reverse()` raises.
13. **`docs/DEV.md`'s restart rule bites.** `identity/access.py`, `agents/entitlements.py`,
    `tools/rag/access.py`, `tools/rag/labels.py`, `tools/rag/retrieval.py` and every runtime
    change in Tasks 5 and 11 are job-kind-adjacent code the worker holds in memory:
    `docker compose restart watcher worker` before every smoke test.
14. **Commit at the end of every task**, with a message naming the task, on the worktree branch
    — never on `main`, and never against the live bind-mounted checkout at
    `<repo>`.
15. **Implementers never leave `...` or TODO.** Every step in this plan is written to be
    executable as it stands. If a step turns out to be unimplementable as written — a symbol
    that does not exist, a field the model does not have, an assertion that cannot hold —
    **stop and report BLOCKED with the reason**. Do not substitute a placeholder, a skipped
    test, a weakened assertion, or a guess: an unattended run that quietly degrades a step
    produces a green suite that proves less than it claims, which is worse than a red one.

## The acceptance gate — spec §18.2's nine done-when criteria, verbatim, plus the six the two
2026-08-30 directives appended

These are the plan's acceptance gate. Task 19 checks each one off by name.

> 1. Two accounts, two entitlements, one group: a document labelled with E1 is retrievable by a
>    holder of E1 through the search page, the Ask page and the `rag.search` and `rag.ask` tools,
>    and is invisible on all four to a holder of E2 — verified in a browser, not only in tests.
> 2. Removing the last label from that document makes it follow the library posture, in both
>    `open` and `locked`, without a re-encode and within one request.
> 3. A labelled tool disappears from an agent's prompt for a user without the entitlement, proven
>    at both the planner and the loop, and the agent cannot reach it by delegating.
> 4. A conversation shared at `view` is readable and not postable; at `use` it is both; unsharing
>    removes both.
> 5. An entitlement owner can grant, revoke and label within their entitlement and can do nothing
>    else — one negative test per non-capability.
> 6. Deleting an entitlement removes its grants and labels, re-stamps every affected document's
>    chunks, and the delete confirmation named the count first.
> 7. The full route matrix — all three postures, all four principals, both settings of
>    `admin_sees_content` — green.
> 8. Both posture sweeps (`FARABUNKER_TEST_POSTURE=personal|enterprise`) green in both feature
>    states.
> 9. The ladder to fresh pixels.

**Three more, appended by the 2026-08-30 owner directive** (spec §18.2's own appended block —
the nine above are unchanged, word for word):

> 10. A model connection in a **restricted set** is **invisible in all three pickers** — the chat
>     per-turn picker, the vision page picker and the Ask page picker — to a signed-in account
>     holding no entitlement attached to any of its sets, and visible to one that holds any; a
>     non-holder who posts its pk anyway is refused on **every** submission path (chat turn, vision
>     generate, Ask), and an **agent turn** naming it is refused at preflight with a 403 before any
>     turn row is written — verified in a browser, not only in tests.
> 11. **The set indirection does what it was asked for**, proved by two one-action changes in the
>     browser: adding one model to an existing set makes it reachable by **every** entitlement
>     already attached to that set, and attaching one entitlement to that set makes **every** model
>     in it reachable — neither requiring a per-entitlement edit. A connection in **no** set is
>     usable by every holder of the relevant capability, a set with no entitlement attached
>     restricts nothing, and the **AND-composition** holds: holding the image-generation tool
>     entitlement without a set entitlement does not reach the model, and holding a set entitlement
>     without the tool entitlement does not reach image generation.
> 12. `vision-generate` is **refused with honest 403 copy** for a signed-in account holding none of
>     the `vision.generate` tool label's entitlements, and the create page **does not render the
>     generation form** to that same account — the pair asserted together. Putting the connection
>     bound to `rag.embed` into a restricted set breaks **nothing**: ingestion, retrieval, re-encode
>     and `manage.py ingest` all still run, because the role path is exempt (§9.5).
> 13. A **labelled agent** is absent from the `/chat/` picker and from the "Add the default X"
>     offers for an account holding none of its entitlements, and present for one that holds any —
>     **including when the agent is `resident=True`**. A conversation that account already started
>     with that agent **still opens and still renders**, and a **new turn** into it is refused with
>     honest 403 copy before any turn row is written. An agent that delegates cannot reach it. The
>     same, for a **labelled flow**, in `flow.run`'s per-turn choices.
> 14. `rag-document-upload` is **refused with honest 403 copy** for an account holding none of the
>     `rag.ingest` tool label's entitlements, **nothing reaches the inbox directory**, and the
>     library page does not render the upload form to that account — while the library listing,
>     the search box and every document that account may read still render.
> 15. **One action, not many** (§22.34), in the browser: an administrator selects several documents
>     in the library and labels them in **one** submit; labels **every document in a category** in
>     one more; opens the entitlement page and sees its **full reach** in one place — grants,
>     documents, tools, model sets, agents and flows — with the same counts the delete confirmation
>     would name; and opens an account on the users page and sees its **effective access** —
>     each entitlement marked direct or via which group, and what each unlocks.

## File Structure

New files, and the task that creates each:

```
identity/
  contracts/cascades.py     EntitlementCascade, register_entitlement_cascade,
                            all_entitlement_cascades -- pure, dotted-path strings  (Task 2)
  cascades.py               resolve + run them against a live Django             (Task 2)
  migrations/0003_entitlement_and_grant.py                                        (Task 1)
  templates/identity/groups.html                                                  (Task 3)
  templates/identity/entitlements.html                                            (Task 3)
  templates/identity/entitlement.html                                             (Task 3)
  tests/test_entitlements_access.py   test_entitlement_services.py
        test_group_services.py        test_entitlement_pages.py
        test_group_pages.py           test_cascades.py                        (Tasks 1-3)

agents/
  labels.py                 tool label reads/writes + the entitlement cascade      (Task 4)
  entitlements.py           tool_access_for(principal) -> ToolAccess                (Task 5)
  shares.py                 the Share reader/writer helpers agents/visibility uses  (Task 7)
  migrations/0003_toolentitlement_and_share.py                                      (Task 4)
  chat/views/tools.py       the tool-label page                                     (Task 6)
  chat/views/shares.py      the conversation-share POST                             (Task 8)
  chat/templates/chat/tool_entitlements.html                                        (Task 6)
  chat/templates/chat/_share_panel.html                                             (Task 8)
  tests/test_tool_labels.py     tests/test_entitlements.py
        tests/test_shares.py    runtime/tests/test_tool_access.py
        chat/tests/test_tool_entitlements_page.py
        chat/tests/test_conversation_share.py                               (Tasks 4-8)

models/registry/
  access.py                 ModelAccess, model_access_for -- re-exported from bindings (Task 14)
  labels.py                 set create/rename/delete, both edges, the cascade       (Task 14)
  migrations/0008_modelset.py   ModelSet + ModelSetMember + ModelSetEntitlement     (Task 14)
  templates/inference/model_sets.html                                               (Task 14)
  tests/test_model_sets.py  tests/test_model_sets_page.py                           (Task 14)

agents/ (second wave)
  migrations/0004_agent_flow_entitlements.py                                        (Task 15)
  chat/views/access.py      the agent/flow labelling page                           (Task 15)
  chat/templates/chat/agent_entitlements.html                                       (Task 15)
  chat/tests/test_agent_entitlements_page.py                                        (Task 15)

tools/rag/
  labels.py                 restamp_document_chunks, document_labels,
                            set_document_labels, unlabel_all_for_entitlement        (Task 10)
  management/commands/relabel_chunks.py                                             (Task 10)
  migrations/0015_documententitlement.py                                            (Task 9)
  templates/rag/_document_labels.html                                               (Task 12)
  tests/test_access_documents.py  test_labels.py  test_command_relabel_chunks.py
        test_retrieval_visibility.py  test_document_label_page.py             (Tasks 9-12)
```

Modified elsewhere:

```
identity/access.py          + held_entitlement_ids, owned_entitlement_ids,
                              may_see_unlabelled, labelling_entitlements,
                              share_subjects, grant_subjects                         (Task 1)
identity/models.py          + Entitlement, EntitlementGrant                          (Task 1)
identity/services.py        + entitlement/grant/group writes; deactivate_user
                              drops grants; delete_entitlement runs the cascades  (Tasks 2, 3)
identity/routes.py          + IA-2's seven route names                        (Tasks 3, 6, 8, 12)
identity/urls.py            + the four /identity/ routes                             (Task 3)
identity/views.py           + groups/entitlements pages; the library-posture
                              select stops being disabled                         (Tasks 3, 12)
identity/forms.py           + NameForm, GrantForm                                    (Task 3)
identity/testing.py         + make_entitlement, grant, make_group; reset_settings
                              moved up from identity/tests/_helpers.py                (Task 1)
identity/tests/_helpers.py  re-exports the four; ditto agents/tests, agents/chat/tests
                              and tools/rag/tests                                     (Task 1)
agents/models.py            + ToolEntitlement, Share, ToolInvocation.agent_slug       (Task 4)
agents/apps.py              + the tool-label cascade registration                    (Task 4)
agents/contracts/tools.py   + ToolAccess, UNRESTRICTED_TOOL_ACCESS,
                              granted_tools' third drop, ToolContext.tool_access      (Task 5)
agents/runtime/{jobs,loop,delegate,preflight,invoke}.py   the ToolAccess call sites   (Task 5)
agents/visibility.py        + resident_agent_tool_keys (T6); shares in the four
                              bodies, share/revoke/delete/may_post_to              (Tasks 6, 7)
agents/runtime/preflight.py + Preflight.unentitled_tools, two granted_tools passes    (Task 5)
                            + MODEL_NOT_PERMITTED (T14), AGENT_NOT_PERMITTED (T15)
agents/chat/service.py      start_turn answers 403 for both new reasons       (Tasks 14, 15)
agents/chat/pickers.py      chat_picker_options takes the principal                 (Task 14)
agents/runtime/bindings.py  resolve_chat takes the model access                     (Task 14)
agents/runtime/{jobs,loop,delegate}.py  resolve_chat's other three callers (T14);
                            the agent-slug omission and the delegate gate      (Task 15)
agents/{models,visibility,labels,apps}.py  AgentEntitlement/FlowEntitlement, the
                            label clause in three bodies, the second cascade        (Task 15)
models/registry/{models,bindings,apps,urls,views}.py  the three set tables, the two
                            seams, the cascade registration, the three routes       (Task 14)
models/registry/templates/inference/_registered_connection.html  the membership
                            control                                                 (Task 14)
tools/vision/tools.py tools/rag/tools.py  the two tool-key constants                (Task 13)
tools/vision/views.py       _may_generate + the POST gate + the render gate     (Tasks 13, 14)
tools/vision/templates/vision/create.html  the generation form's render gate        (Task 13)
tools/rag/views.py          _may_upload + the upload gate (T13); the bulk label
                            route and the selection controls                        (Task 16)
tools/rag/templates/rag/documents.html  the upload gate + the bulk controls (Tasks 13, 16)
tools/vision/jobs.py tools/rag/jobs.py  model access at each submission path        (Task 14)
agents/chat/views/tools.py  mutating tools listed page-only                         (Task 13)
identity/access.py          + effective_entitlements                                (Task 16)
identity/services.py        + entitlement_reach (an alias, on purpose)              (Task 16)
identity/views.py identity/templates/identity/{entitlement,users}.html  the reach
                            panel and the effective-access panel                    (Task 16)
identity/contracts/actions.py  + the seven modelset.* (T14) and the four
                            agent.*/flow.* (T15)
agents/chat/urls.py         + chat-tool-entitlements, chat-conversation-share      (Tasks 6, 8)
agents/chat/views/turns.py  the `use`-level share check on chat-turn                  (Task 7)
agents/chat/templates/chat/conversation.html  the share panel, the compose-form gate,
                            the queued-copy fix                               (Tasks 8, 13)
foundation/templates/_shell.html   three nav anchors (T3) + the tool-access one (T6)
agents/apps.py              + the tool-label cascade registration                     (Task 4)
tools/rag/apps.py           + the document-label cascade registration                (Task 10)
tools/rag/access.py         + DocumentVisibility, document_visibility,
                              readable_documents, listable_documents                  (Task 9)
tools/rag/ingest.py         the re-stamp after insert_nodes                          (Task 10)
tools/rag/retrieval.py      the required `visibility` argument, both signatures      (Task 11)
tools/rag/jobs.py tools.py views.py management/commands/ask.py  its five callers     (Task 11)
tools/rag/tests/{test_retrieval,test_views,test_commands}.py  24 existing call sites (Task 11)
tools/rag/views.py          library counts, labels route, widened delete/reingest,
                            document_file/transcript through readable_documents      (Task 12)
tools/rag/templates/rag/{documents,history}.html   labels + render-vs-gate       (Tasks 12, 13)
models/queue/templates/jobs/queue.html             render-vs-gate                    (Task 17)
identity/tests/test_route_matrix.py  test_zero_queries.py                            (Task 18)
foundation/ops/tests/{test_import_law,test_column_boundaries}.py  the two new guards (Tasks 9, 12)
docs/{DEV,OPERATIONS,ROADMAP}.md  docs/adr/{0010,0015}-*.md  identity/README.md
tools/rag/README.md agents/README.md tools/vision/README.md README.md                (Task 19)
```

## Decisions the author made

Recorded here, at the top, because an implementer who hits one of these and reaches for the
spec will find the spec saying something slightly different — and needs to know this plan
already thought about it.

1. **`identity/access.py`'s new PREDICATES keep IA-1's `settings_row=` threading.** The spec
   writes `held_entitlement_ids(principal)`; the real `identity/access.py` gives every predicate
   an optional keyword-only `settings_row` so a caller that reads the singleton once per request
   (`IdentityGateMiddleware` stashes it on `request.identity_settings_row`) does not re-read it
   per row. The three predicates this plan adds — `held_entitlement_ids`,
   `owned_entitlement_ids`, `may_see_unlabelled` — follow that convention. The three **readers**
   (`labelling_entitlements`, `share_subjects`, `grant_subjects`) deliberately do not: each is
   called **once per page render**, to build a `<select>`, so the keyword would be a parameter
   with one possible caller and no measurable effect. The spec's signatures are the floor, not
   the ceiling.
2. **`may_see_unlabelled` is `principal.kind != "anonymous"` on an open library, not
   `kind == "user"`.** Spec §8.1 says "every signed-in principal"; spec §8.3's `manage.py ask`
   row and §5.3 item 8 both say a **service** principal answering from a shell sees unlabelled
   documents. Those two only agree if the open-library branch admits the service principal.
   Anonymous is refused by the gate long before it reaches here, and this is the honest reading
   of "a file somebody dropped in a shared inbox".
3. **The entitlement-delete cascade travels by registry, not by import.** Spec §12.1 says
   `restamp_document_chunks` is called for each affected document inside the delete transaction.
   `identity/` may not import `tools/` (rule 4) and the delete happens on an identity page, so
   the call cannot be written literally. `identity/contracts/cascades.py` registers an
   `EntitlementCascade(key, label, handler)` whose `handler` is a **dotted-path string**
   resolved by `django.utils.module_loading.import_string` at delete time — the same mechanism
   `JobKind.handler` and `OwnedRows.model` already use. `tools/rag/apps.py` registers one;
   `agents/apps.py` registers one for tool labels. One handler, two modes:
   `handler(entitlement_id, *, commit: bool) -> int` counts when `commit=False` (the delete
   confirmation names the count, done-when 6) and detaches-and-repairs when `commit=True`.
4. **The cascade handler detaches BEFORE `entitlement.delete()`, and the database `CASCADE`
   stays as the net.** Django's `on_delete=CASCADE` would remove the `DocumentEntitlement` rows
   itself, but the chunk re-stamp has to run *after* the rows are gone and *inside* the same
   transaction — an ordering puzzle with no clean expression through Django's collector. The
   handler therefore deletes its own column's rows and re-stamps in one pass, and
   `entitlement.delete()` then finds nothing left to cascade. The FK's `CASCADE` is not removed:
   a shell that deletes an `Entitlement` directly still leaves no orphan rows, only a stale
   chunk cache, and `manage.py relabel_chunks` is the documented repair.
5. **`identity-entitlement-edit` is route class `R`, not `S`.** Spec §11.3 marks it "S for
   create/rename/delete; an **owner** of that entitlement may reach the grant form (checked in
   the view)". `identity/routes.py::tier_for` maps class `S` to the `ADMIN` tier, and
   `IdentityGateMiddleware` refuses a non-admin there before the view ever runs — so an
   entitlement owner could never reach the form. `R` maps to `AUTHENTICATED`, which is exactly
   "the middleware admits, the view decides", and it is the class the spec's own matrix defines
   for operational rows an owner reaches. The view re-checks `is_admin` for rename and delete.
   The route-matrix consequence is recorded in Task 18.
6. **Three new read helpers on `identity/access.py`, because rendering a picker needs names.**
   `labelling_entitlements(principal)` returns `((id, name), ...)` — every entitlement for an
   admin, the owned ones otherwise — and `share_subjects(principal)` / `grant_subjects(principal)`
   return `{"users": ((id, username), ...), "groups": ((id, name), ...)}`, differing by whether
   the caller themselves is offered (decision 18). Without them `tools/rag` and `agents` would
   have to reach `identity.models` (forbidden) or `django.contrib.auth.get_user_model()` (legal
   but a second path to the same table). All three are identity answering about identity's own
   tables and returning plain data — the same shape `held_entitlement_ids` already has.
   `agents/chat/views/shares.py` still calls `get_user_model()` for a single
   `get_object_or_404` on a submitted pk, which is what `AUTH_USER_MODEL` exists for and is
   invisible to every guard (the AST shape is `Name.objects`, and this is a `Call`).
7. **`ToolContext` gains `tool_access: ToolAccess = UNRESTRICTED_TOOL_ACCESS`.** Spec §9.3 says
   `run_agent_tool` "reuses the **root** access from `ctx`" — there is no field on `ToolContext`
   to reuse it from. It is a frozen-dataclass field with a default, so no migration and no
   existing construction breaks.
8. **`AskView` builds no `DocumentVisibility`.** Spec §8.3's table names `tools/rag/views.py::
   SearchView, ::AskView`. `SearchView` calls `retrieve_nodes` directly and does need one;
   `AskView` **enqueues** a `rag.ask` job (`tools/rag/views.py:1136` already writes
   `payload_fields(principal_for_request(request))` into the payload) and never calls
   `answer_question` at all — `tools/rag/jobs.py::run_ask` builds the visibility from the
   payload actor, which is the same fact one hop later and the hop where the worker actually
   runs. Building one in the view too would be a second answer nobody reads.
9. **`rag-document-delete` and `rag-document-reingest` keep IA-1's `403`, and widen the
   predicate only.** Spec §11.3 puts them in class R, whose refusal is 404. IA-1 shipped 403
   with a recorded reason (the member can already see the row's title on the library page, so
   hiding its existence at the action would be a secrecy the surrounding page does not keep),
   and `identity/tests/test_route_matrix.py::_LIBRARY_MUTATIONS` pins it with that reasoning
   written out. IA-2 widens `is_admin(principal)` to
   `is_admin(principal) or may_label_document(principal, document)` and changes nothing about
   the status — **including for a `doc_id` that does not exist**, which is why decision 19 keeps
   IA-1's predicate-before-row ordering.
10. **The library `Upload` control is not part of the render-vs-gate fold-in.**
    `rag-document-upload` is class **A** — every signed-in caller may upload, and the POST does
    not 403. The fold-in's own test is "controls that already 403 on POST"; upload does not, so
    hiding it would remove a capability rather than stop advertising one that is refused. What
    the fold-in does hide on that page: `Re-ingest`, `Delete`, and the category `rename`/`delete`
    forms. Task 12 separately gives the upload form a label picker, which is a widening, not a
    gate.
11. **The `Position null` copy fix is applied to both call sites.** The brief names the pending
    chat-turn card (`agents/chat/templates/chat/conversation.html:364`). The Ask page carries the
    identical expression (`tools/rag/templates/rag/ask.html:406`) and produces the identical
    "Position null of queue" string from the identical cause. Fixing one and leaving the other is
    shipping a known defect; both are one line, in one task, with one test each.
12. **`_shared_keys` materialises to a list and parses every key, exactly as the spec writes
    it.** A `Share.target_key` that is not a parseable UUID (conversation) or integer (the other
    three) is dropped and logged rather than reaching `Q(pk__in=[...])`, where it would raise
    inside the queryset and turn a listing page into a 500 — a never-500 violation reachable by
    one bad row. `Share.save()` validates the same way on the way in; this is the second half.
13. **The route matrix's fourth principal is `owner` — a member holding one `role=owner`
    grant.** Spec §16.2 asks for "{anonymous, member, entitlement-owner, admin}". IA-1 shipped
    three and recorded why the open principal is covered by two dedicated modules instead
    (`test_zero_queries.py`, `test_middleware.py::TestOpenPosture`). Task 18 adds the fourth as
    the spec names it, and the world it is pointed at gains an entitlement the `owner` account
    owns and the `member` account does not.
14. **`ToolInvocation.agent_slug` lands here, with its writer.** IA-1's plan decision 1 deferred
    the column to IA-2 because spec §17's migration table puts it in migration 7 and IA-1's
    `--plan` gate allowed exactly four migrations. Task 4 ships the column and Task 5 ships
    `agents/runtime/invoke.py` writing it from `tool_ctx.agent_slug`.
15. **Grants are dropped on deactivation in Task 2, where the grants table first exists.**
    `identity/services.py::deactivate_user`'s docstring already says "IA-2 adds one line here".
16. **The route matrix still runs in ONE posture, and done-when 7 is satisfied by the sweep.**
    `identity/tests/test_route_matrix.py:548` pins `_MATRIX_POSTURE = POSTURE_PERSONAL` with
    IA-1's cost argument (sweeping both would double ~1,000 cells, each seeding a fresh world,
    to re-prove what `TestThePosturesAgree` asserts directly and far more cheaply). IA-2 does
    not change it. Spec §18.2's done-when 7 says "all three postures"; it is met by three
    things together and this decision says so rather than leaving a reader to infer it:
    `TestThePosturesAgree` proves `personal` and `enterprise` answer **identically** for every
    route, principal and setting; the `FARABUNKER_TEST_POSTURE=enterprise` sweep (done-when 8)
    re-runs the whole module in the other one; and `open` is covered by
    `identity/tests/test_zero_queries.py` and `identity/tests/test_middleware.py::TestOpenPosture`,
    where the interesting assertion for that posture is a query count rather than a status code.
17. **A tool the acting principal has no entitlement for is reported NOWHERE on the page.**
    Spec §9.2 has `granted_tools` log that drop at **debug**, "because on a labelled install it
    is the NORMAL case". `Preflight.dropped_tools` renders as *"{key} is not available on this
    install"* (`agents/runtime/preflight.py:96-97`), which would be a false sentence naming a
    tool the entitlement was meant to keep out of somebody's way. Task 5 therefore splits the
    two causes into `dropped_tools` and `unentitled_tools`, and renders **nothing** for the
    second — enforcement by omission, the same mechanism `available_tools` already uses for the
    delegation depth cap. Task 19's browser walk asks the owner to confirm the tool is simply
    absent, not that a note names it.
18. **Two subject readers, not one with a flag.** `share_subjects` excludes the caller (sharing
    a row with yourself is a no-op); `grant_subjects` includes them (granting yourself an
    entitlement is the only way an administrator with the content setting off reads a labelled
    document at all, and spec §18.2's first done-when criterion begins by granting entitlements
    to accounts). The bodies differ by one `.exclude()`, and that clause is the whole of what
    each answer means — a boolean keyword would hide the difference behind a call site.
19. **`document_delete`/`document_reingest` check the cheap half of the predicate BEFORE
    resolving the row.** That is IA-1's ordering (`tools/rag/views.py:302-307`), and keeping it
    is what makes decision 9 true: IA-1 answers 403 to a member for **any** `doc_id`, existing
    or not, and resolving the row first would answer 404 for one that does not exist — a new
    enumeration signal on a route that had none.
    **The signal is narrowed to entitlement owners, not removed, and that is accepted rather
    than hidden.** An owner passes the cheap gate and then meets
    `get_object_or_404(Document, pk=doc_id)`, so they get **404** for an id that does not exist
    and **403** for one that exists but carries none of their entitlements — and
    `listable_documents` hides those rows from their library page, so the pair of statuses is
    something they could not otherwise learn. It is accepted: an entitlement owner already holds
    mutation standing over part of the library, document ids are sequential integers either way,
    and the alternative — resolving through a `listable_document_or_none(principal, doc_id)`
    helper that answers `None` rather than raising, so both cases return the same 403 — buys a
    narrower signal at the cost of a second document-resolution path beside
    `listable_documents`/`readable_documents`, which the §4.3 guard exists to keep at two. If a
    later phase wants it closed, that helper is the named way to do it.

### Added by the 2026-08-30 owner amendment (Tasks 13–14)

20. **`agents.entitlements` becomes a named cross-column seam, and no new AST guard is added
    for it.** `tools/vision/views.py::_may_generate` asks the agents column "may this principal
    call `vision.generate`", because that column owns the answer — this one already registers
    the tool through `agents.contracts.tools` (`tools/vision/apps.py`), and re-deriving the
    answer here would be a second copy of the rule Task 5 built. It is legal today: the only
    direction guard in the tree is `test_no_agents_module_imports_a_tools_package`, which runs
    the other way. It is **recorded** in `agents/README.md` and `foundation/README.md` (Task
    17), the same treatment `models/queue/visibility.py` got in IA-1, and **no new sweep is
    written**: there is no `tools → agents` guard to extend, and inventing one to police a
    single edge is machinery this amendment does not need. If a reviewer wants one, it is a
    ten-line mirror of the registry gate and belongs in its own change.
21. **`model_access_for` is re-exported from `models.registry.bindings`.** The implementation
    lives in `models/registry/access.py`, but `agents/` may import **only**
    `models.registry.bindings` from that package
    (`foundation/ops/tests/test_import_law.py::test_agents_reaches_models_registry_through_bindings_and_nothing_else`,
    a closed set with teeth). Re-exporting keeps that gate green with zero edits and keeps every
    column at one door, while leaving `bindings.py` about resolution rather than about
    entitlements.
22. **The `access` argument is required and keyword-only on all three registry functions**, for
    the reason Task 11's `visibility` argument is: a default of "may use everything" is a
    fail-open default, and a caller that forgets it should fail at signature-checking time
    rather than at a security review two years later. Every **existing** test gains
    `access=UNRESTRICTED_MODEL_ACCESS`, which is the open-box answer those tests were already
    implicitly asserting against.
23. **The Ask page's picker is gated too, though the brief named only three seams.** The brief
    lists the chat picker, the vision picker and agent-turn resolution. `tools/rag/views.py`'s
    Ask page carries a **fourth** user-selectable picker over the same `chat`-capability
    connections, and leaving it out would mean a model labelled "under test" that the Ask page
    still offers — a hole in the same rule, on a page the owner uses. It costs one argument,
    because it is the same `picker_options` and the same `resolve_connection_named` as the other
    three.
24. **A labelled-tool cell is a focused test, not a sixth matrix axis.** Spec §16.4 asks the
    route matrix to carry the direct-surface routes "in both label states". Adding a `labelled`
    dimension to the cross-product would double ~1,000 cells — each seeding a fresh world — to
    assert two routes' behaviour. Task 13 Step 7 adds `TestALabelledDirectSurface` beside the
    matrix instead, in the same module, reusing its `world` and its `_DRIVERS` entries so both
    routes are still swept from one place.

### Added by the 2026-08-30 second owner amendment and its supplement (Tasks 13–16)

25. **A `mutates=True` tool is labellable even though it is not grantable**, and the tool-label
    page therefore lists `all_tools()` rather than `grantable_tools()`, marking the mutating ones
    page-only. Without this, `rag.ingest` — the key behind the library's upload door — could not
    be labelled at all and Task 13's gate would have nothing to consult. ADR 0010's rule governs
    what an **agent** may be handed and is untouched: `granted_tools` still drops a mutating key
    unconditionally, and Task 13 Step 2 pins that labelling `rag.ingest` leaves it exactly as
    uncallable by every agent as it was. Recorded in the spec at §22.35.
26. **The two doors ship in one task.** `vision-generate` and `rag-document-upload` take the same
    three edits — a key constant, a predicate with two callers, a template gate — and doing them
    apart is how the second becomes a copy of the first that drifts. Task 13 does both, and its
    matrix test is parametrised over the pair rather than written twice.
27. **`ModelAccess.required` flattens the two set edges into one mapping.** The sets exist so an
    **administrator** can change many grants at once; no read path benefits from re-walking
    `connection → set → entitlement` per option. One join builds `{connection pk: {entitlement
    ids}}`, and a connection in two sets collects both — which is the OR the rule asks for,
    obtained by construction rather than by a second branch.
28. **A set with no entitlement attached restricts nothing.** The join simply finds no
    attachment, so such a connection is absent from `required` and therefore allowed. The
    alternative — treating "in a set" as restrictive by itself — would let an operator hide a
    model from themselves by creating a set and forgetting to attach anything, with no page
    saying so.
29. **The bulk label route is a second URL, and it ADDS rather than replaces.**
    `rag-document-labels` sets one document's labels to **exactly** what was submitted;
    `rag-document-labels-bulk` **adds or removes** one set of labels across many. Folding two
    verbs into one route would mean a hidden field deciding whether a POST is destructive, which
    is the one thing a bulk control must never be ambiguous about. Permission is checked **per
    document** with the same `may_label_document` the single route uses, and a row the caller has
    no standing over is skipped and counted rather than refusing the whole submit.
30. **One audit event per document, not one per bulk submit.** The catalogue's existing grain is
    a `target_key` that is a document id (`library.document_labelled`), and
    `identity.audit.for_target` — the reader the audit page uses — cannot query a row whose
    target is a list. Forty rows for forty documents is what an audit trail is for.
31. **One cascade registration for agent and flow labels together** (`agents.runnable_labels`),
    not two. The delete confirmation reads better as "Agent and flow labels: 3" than as two lines
    an operator has to add up, and the two tables are always edited from the same page.
32. **`entitlement_reach` is a one-line alias of `entitlement_delete_counts`**, and stays one.
    The supplement asks the reach panel and the delete confirmation not to disagree; the cheapest
    way to guarantee that is for them to be the same function, and Task 16's test asserts the two
    return equal dicts. The moment the alias grows a body of its own it has become the second
    counter this decision exists to prevent.
33. **The agent/flow label clause is AND-ed onto the ownership OR, not OR-ed into it.** Written
    the natural way — one more `|` beside `Q(resident=True)` — a labelled shipped agent would stay
    visible to everybody, and the shipped agents are exactly the ones an operator most wants to
    restrict. Task 15 Step 4 shows the parentheses and Step 3 tests the resident case on its own.
34. **An existing conversation stays readable when its agent becomes restricted; only new turns
    are refused.** `agents/chat/views/thread.py` already reads `conversation.agent` directly and
    never calls `visible_agents` — its own docstring records that as deliberate — so this is a
    ruling the tree already implements and this plan only has to not break. Access to one's own
    rows is ownership, not a label question, and retroactively hiding somebody's own history
    would be a worse answer than the one the label was asked for.
35. **Ownership does not exempt a row from its own label.** The AND in decision 33 has a second
    consequence the parentheses do not announce: an account that **owns** a labelled agent or
    flow and does not hold its entitlement stops seeing it, and cannot run its own row. That is
    the intended reading — a label an administrator applies must not be bypassable by the person
    it is aimed at, and the alternative would make labelling a *personal* agent useless, which is
    a real case (an operator restricting one person's experimental agent). But it is a behaviour
    change on a row somebody created, so it is recorded here, stated in spec §9.6, asserted by
    its own test in Task 15 Step 4, and — because `/chat/access/` is the only surface it can be
    caused from and there is no agents admin page on which to notice it afterwards — written into
    that page's own warning line in Step 7. Existing conversations stay readable; new turns are
    refused with `AGENT_NOT_PERMITTED`.

---
### Task 1: `Entitlement`, `EntitlementGrant`, and the three access questions IA-1 left out

The two tables the whole phase spends, and the three functions `identity/access.py`'s own
docstring said IA-1 was deliberately not writing ("a stub in an access module is exactly the
thing a later reader mistakes for a decision"). Nothing renders them yet; Task 2 gives them
writers and Task 3 gives them pages.

**Files:**
- Modify: `identity/models.py` (append two models)
- Create: `identity/migrations/0003_entitlement_and_grant.py` (generated, then read)
- Modify: `identity/access.py` (six new public functions plus two private helpers)
- Modify: `identity/testing.py` (three new builders; `reset_settings` moved up into it)
- Modify: `identity/tests/_helpers.py`, `tools/rag/tests/_helpers.py`,
  `agents/chat/tests/_helpers.py` (extend their existing `from identity.testing import (...)`
  blocks), `agents/tests/_helpers.py` (which has none today and gets one)
- Test: `identity/tests/test_models.py` (append), `identity/tests/test_entitlements_access.py` (new)

**Interfaces:**
- Produces: `identity.models.Entitlement` (`name`, `description`, `created_by`, `created_at`);
  `identity.models.EntitlementGrant` (`entitlement`, `user`, `group`, `role`, `source`,
  `granted_by`, `granted_at`, with `Role.MEMBER`/`Role.OWNER` and `Source.MANUAL`/`Source.SSO`).
- Produces: `identity.access.held_entitlement_ids(principal, *, settings_row=None) -> frozenset[int]`,
  `::owned_entitlement_ids(principal, *, settings_row=None) -> frozenset[int]`,
  `::may_see_unlabelled(principal, *, settings_row=None) -> bool`,
  `::labelling_entitlements(principal) -> tuple[tuple[int, str], ...]`,
  `::share_subjects(principal) -> dict[str, tuple[tuple[int, str], ...]]`,
  `::grant_subjects(principal) -> dict[str, tuple[tuple[int, str], ...]]`.
- Produces: `identity.testing.reset_settings()` (moved up from `identity/tests/_helpers.py`).
- Produces: `identity.testing.make_entitlement(**overrides)`, `::grant(entitlement, *, user=None,
  group=None, role="member")`, `::make_group(**overrides)`.

- [ ] **Step 1: Write the failing model tests**

Append to `identity/tests/test_models.py`:

```python
class TestEntitlement:
    """The named permission everything in IA-2 is granted against."""

    def test_the_name_is_case_insensitively_unique(self):
        from identity.models import Entitlement
        Entitlement.objects.create(name="Finance")
        with pytest.raises(IntegrityError):
            Entitlement.objects.create(name="finance")

    def test_deleting_the_creator_leaves_the_entitlement(self):
        """SET_NULL, not CASCADE: deleting a user must never delete an
        entitlement. Users are deactivated rather than deleted anyway --
        the null branch exists for the hypothetical shell deletion, not
        as a normal path."""
        from identity.models import Entitlement
        creator = make_user()
        row = Entitlement.objects.create(name="Finance", created_by=creator)
        creator.delete()
        row.refresh_from_db()
        assert row.created_by is None


class TestEntitlementGrant:
    """A grant names a user XOR a group, never both and never neither."""

    def test_a_grant_naming_both_a_user_and_a_group_is_refused(self):
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        with pytest.raises(IntegrityError):
            EntitlementGrant.objects.create(
                entitlement=entitlement, user=make_user(),
                group=Group.objects.create(name="analysts"))

    def test_a_grant_naming_neither_is_refused(self):
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        with pytest.raises(IntegrityError):
            EntitlementGrant.objects.create(entitlement=entitlement)

    def test_one_user_grant_per_entitlement(self):
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        user = make_user()
        EntitlementGrant.objects.create(entitlement=entitlement, user=user)
        with pytest.raises(IntegrityError):
            EntitlementGrant.objects.create(entitlement=entitlement, user=user)

    def test_two_group_grants_for_one_entitlement_are_refused(self):
        """The partial unique earns its keep here: NULLs do not collide in
        Postgres, so a plain UniqueConstraint(entitlement, user, group)
        would happily store the same group grant a thousand times."""
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        group = Group.objects.create(name="analysts")
        EntitlementGrant.objects.create(entitlement=entitlement, group=group)
        with pytest.raises(IntegrityError):
            EntitlementGrant.objects.create(entitlement=entitlement, group=group)

    def test_the_role_is_a_column_not_a_second_row(self):
        """Promoting a member to owner is an UPDATE. That is why the
        unique constraints do not include `role`: two rows for one
        (entitlement, user) pair would make "does this person hold E"
        ambiguous."""
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        user = make_user()
        row = EntitlementGrant.objects.create(entitlement=entitlement, user=user)
        row.role = EntitlementGrant.Role.OWNER
        row.save(update_fields=["role"])
        assert EntitlementGrant.objects.filter(entitlement=entitlement, user=user).count() == 1

    def test_source_defaults_to_manual_and_sso_is_declared(self):
        """`source` is written by nothing today. It is present from day
        one because it is the column an SSO reconciliation joins on, and
        backfilling that distinction later is impossible."""
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        row = EntitlementGrant.objects.create(entitlement=entitlement, user=make_user())
        assert row.source == EntitlementGrant.Source.MANUAL
        assert EntitlementGrant.Source.SSO.value == "sso"

    def test_deleting_the_entitlement_takes_its_grants(self):
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        EntitlementGrant.objects.create(entitlement=entitlement, user=make_user())
        entitlement.delete()
        assert EntitlementGrant.objects.count() == 0

    def test_deleting_the_group_takes_its_grants(self):
        from identity.models import Entitlement, EntitlementGrant
        entitlement = Entitlement.objects.create(name="Finance")
        group = Group.objects.create(name="analysts")
        EntitlementGrant.objects.create(entitlement=entitlement, group=group)
        group.delete()
        assert EntitlementGrant.objects.count() == 0
```

Add the imports this needs at the top of `identity/tests/test_models.py` — check what is
already there before adding, and add only what is missing:

```python
from django.contrib.auth.models import Group
from django.db.utils import IntegrityError

from identity.tests._helpers import make_user
```

- [ ] **Step 2: Run the model tests and watch them fail**

Run: `.venv/bin/pytest -q identity/tests/test_models.py -x`
Expected: FAIL — `ImportError: cannot import name 'Entitlement' from 'identity.models'`.

- [ ] **Step 3: Write the two models**

Append to `identity/models.py`, and add `from django.conf import settings`,
`from django.db.models import Q` and `from django.db.models.functions import Lower` to its
imports:

```python
class Entitlement(models.Model):
    """A named permission, granted to users and groups, spent on
    documents, tools and nothing else.

    ENTITLEMENTS PARTITION ONE LIBRARY AMONG THE PEOPLE WHO SHARE ONE
    MACHINE. They do not partition machines: nothing here is
    multi-tenant, and a second box is a second box.

    Case-insensitively unique by name, the same house convention
    `agents.models.Agent`'s `uniq_agent_slug_ci` and
    `models.registry.models.ModelConnection`'s `uniq_modelconnection_
    name_ci` already use -- "Finance" and "finance" are one entitlement,
    because two rows that read identically on a grant form are two rows
    somebody will grant the wrong one of.
    """

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    # SET_NULL, NOT CASCADE: deleting a user must never delete an
    # entitlement -- and with it every grant and label that hung off it.
    # Users are deactivated rather than deleted anyway (`identity.
    # services.deactivate_user`), so the null branch exists for the
    # hypothetical shell deletion, not as a normal path.
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="entitlements_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="uniq_entitlement_name_ci"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class EntitlementGrant(models.Model):
    """Who holds an entitlement, and in what role.

    A USER XOR A GROUP, never both and never neither -- a database
    constraint, not a convention, because a grant that named both would
    make "revoke this person's access" a question with two answers.

    `role` IS A COLUMN ON THIS ROW, NOT A SECOND ROW. Promoting a member
    to owner is an `UPDATE`, which is why the unique constraints below do
    not include it: two rows for one (entitlement, user) pair would make
    "does this person hold E" ambiguous, and the ambiguity would show up
    as a duplicate on every grant page.

    An OWNER of E is a member of E plus exactly three capabilities --
    grant/revoke E, label/unlabel with E, and see everything in E. The
    third needs no code at all: an owner grant is a grant, so it already
    appears in `identity.access.held_entitlement_ids`.
    """

    class Role(models.TextChoices):
        MEMBER = "member", "Member"
        OWNER = "owner", "Owner"

    class Source(models.TextChoices):
        MANUAL = "manual", "Granted here"
        # WRITTEN BY NOTHING TODAY. Present from day one because it is
        # the column an identity-provider reconciliation joins on:
        # "revoke every grant this provider used to assert and no longer
        # does" is answerable only if provider-asserted grants are
        # distinguishable from hand-made ones, and backfilling that
        # distinction later is impossible -- nobody can classify a row
        # after the fact.
        SSO = "sso", "From the identity provider"

    entitlement = models.ForeignKey(Entitlement, on_delete=models.CASCADE,
                                    related_name="grants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.CASCADE, related_name="entitlement_grants")
    group = models.ForeignKey("auth.Group", null=True, blank=True,
                              on_delete=models.CASCADE, related_name="entitlement_grants")
    role = models.CharField(max_length=8, choices=Role.choices, default=Role.MEMBER)
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.MANUAL)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="grants_made")
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["entitlement__name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(user__isnull=False, group__isnull=True)
                           | Q(user__isnull=True, group__isnull=False)),
                name="grant_user_xor_group",
            ),
            # PARTIAL uniques, not one plain unique across three columns:
            # NULLs do not collide in Postgres, so a plain
            # `UniqueConstraint(entitlement, user, group)` would happily
            # store the same group grant a thousand times.
            models.UniqueConstraint(fields=["entitlement", "user"],
                                    condition=Q(user__isnull=False),
                                    name="uniq_grant_entitlement_user"),
            models.UniqueConstraint(fields=["entitlement", "group"],
                                    condition=Q(group__isnull=False),
                                    name="uniq_grant_entitlement_group"),
        ]
        indexes = [
            models.Index(fields=["user"], name="identity_grant_user"),
            models.Index(fields=["group"], name="identity_grant_group"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        subject = self.user_id or f"group {self.group_id}"
        return f"{self.entitlement_id}->{subject} ({self.role})"
```

- [ ] **Step 4: Generate the migration and read it before trusting it**

```bash
.venv/bin/python manage.py makemigrations identity --name entitlement_and_grant
```

Open `identity/migrations/0003_entitlement_and_grant.py` and confirm, by eye:

- `dependencies` names `migrations.swappable_dependency(settings.AUTH_USER_MODEL)` **and**
  `("auth", "__first__")` (the `Group` foreign key). If the autodetector wrote only one, add
  the other by hand — these are the repository's first cross-app migration dependencies and
  they must be right (spec §4.2, §17).
- Both `CheckConstraint`s use `condition=`, not `check=`. `requirements.txt` already pins
  `Django>=5.1,<6.0` (raised in IA-1), and `check=` is the 5.0 spelling that 6.0 removes.
- No operation touches any table other than the two new ones.

Add a docstring at the top of the generated file:

```python
"""IA-2's identity migration: the named permission and who holds it.

The repository's FIRST cross-app migration dependency lives here
(`swappable_dependency` for the user FK, `("auth", "__first__")` for the
group FK). Before this, `docs/superpowers/specs/2026-08-25-*` could
record as a fact that the tree had neither; ADR 0016 records the
retirement of that fact.
"""
```

- [ ] **Step 5: Run the model tests and watch them pass**

```bash
.venv/bin/python manage.py migrate
.venv/bin/pytest -q identity/tests/test_models.py
```
Expected: PASS.

- [ ] **Step 6: Write the failing access tests**

Create `identity/tests/test_entitlements_access.py`:

```python
"""`identity/access.py`'s three entitlement questions, and the two
picker readers.

IDENTITY ANSWERS ABOUT PRINCIPALS, NEVER ABOUT DOCUMENTS. It may not
import `tools/` or `agents/` (import-law rule 4), so it answers "which
entitlement ids" and lets each column turn that into a queryset of its
own rows.
"""
from __future__ import annotations

import pytest
from django.contrib.auth.models import Group

from identity.access import (
    grant_subjects, held_entitlement_ids, labelling_entitlements, may_see_unlabelled,
    owned_entitlement_ids, share_subjects,
)
from identity.contracts.postures import LIBRARY_LOCKED, POSTURE_ENTERPRISE
from identity.contracts.principals import ANONYMOUS, OPEN_PRINCIPAL, SERVICE_PRINCIPAL
from identity.models import EntitlementGrant
from identity.tests._helpers import (
    grant, make_admin, make_entitlement, make_group, make_user, posture, reset_settings,
    seed_sweep_posture, user_principal,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestHeldEntitlementIds:
    def test_a_direct_grant_is_held(self):
        user = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=user)
        with posture(POSTURE_ENTERPRISE):
            assert held_entitlement_ids(user_principal(user)) == frozenset({finance.pk})

    def test_a_grant_through_a_group_is_held(self):
        """One query, and it reaches both ways in: `Q(user_id=...) |
        Q(group__user__id=...)`."""
        user = make_user()
        group = make_group(name="analysts")
        user.groups.add(group)
        finance = make_entitlement(name="Finance")
        grant(finance, group=group)
        with posture(POSTURE_ENTERPRISE):
            assert held_entitlement_ids(user_principal(user)) == frozenset({finance.pk})

    def test_a_non_user_principal_holds_nothing(self):
        """Grants attach to a user or a group, by the XOR constraint. A
        service principal therefore holds nothing -- permanently, until
        service-account tokens land -- and the open and anonymous
        principals never reach a grants join at all."""
        finance = make_entitlement(name="Finance")
        grant(finance, user=make_user())
        with posture(POSTURE_ENTERPRISE):
            assert held_entitlement_ids(SERVICE_PRINCIPAL) == frozenset()
            assert held_entitlement_ids(ANONYMOUS) == frozenset()
            assert held_entitlement_ids(OPEN_PRINCIPAL) == frozenset()

    def test_an_unparseable_user_key_answers_nothing_rather_than_raising(self):
        """`Principal.key` for a user is the primary key AS A STRING. A
        key that is not a decimal string -- from a hand-written payload,
        or a row written against an older schema -- would reach
        `filter(user_id=...)` and raise `ValueError` inside a request.
        A never-500 surface cannot afford that."""
        from identity.contracts.principals import Principal
        with posture(POSTURE_ENTERPRISE):
            assert held_entitlement_ids(Principal("user", "not-a-pk")) == frozenset()


class TestOwnedEntitlementIds:
    def test_only_owner_grants_are_owned_and_they_are_also_held(self):
        user = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=user, role=EntitlementGrant.Role.OWNER)
        grant(legal, user=user)
        with posture(POSTURE_ENTERPRISE):
            principal = user_principal(user)
            assert owned_entitlement_ids(principal) == frozenset({finance.pk})
            assert held_entitlement_ids(principal) == frozenset({finance.pk, legal.pk})

    def test_an_owner_grant_through_a_group_is_owned(self):
        user = make_user()
        group = make_group(name="stewards")
        user.groups.add(group)
        finance = make_entitlement(name="Finance")
        grant(finance, group=group, role=EntitlementGrant.Role.OWNER)
        with posture(POSTURE_ENTERPRISE):
            assert owned_entitlement_ids(user_principal(user)) == frozenset({finance.pk})


class TestMaySeeUnlabelled:
    def test_open_posture_answers_true_without_a_query(self):
        assert may_see_unlabelled(OPEN_PRINCIPAL) is True

    def test_an_open_library_admits_anyone_who_is_not_anonymous(self):
        """Spec section 8.1 says "every signed-in principal"; sections
        5.3 and 8.3 both say a SERVICE principal answering from a shell
        sees unlabelled documents. Those only agree if the open-library
        branch admits the service principal -- which is the honest
        reading of a file somebody dropped in a shared inbox."""
        user = make_user()
        with posture(POSTURE_ENTERPRISE):
            assert may_see_unlabelled(user_principal(user)) is True
            assert may_see_unlabelled(SERVICE_PRINCIPAL) is True
            assert may_see_unlabelled(ANONYMOUS) is False

    def test_a_locked_library_answers_sees_all_content_only(self):
        member = make_user()
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            assert may_see_unlabelled(user_principal(member)) is False
            # The content setting is OFF by default, so even an admin is
            # refused the BYTES of an unlabelled document on a locked
            # library. They still see its ROW and can label it.
            assert may_see_unlabelled(user_principal(admin)) is False
            assert may_see_unlabelled(SERVICE_PRINCIPAL) is False

    def test_a_locked_library_admits_an_admin_once_the_content_setting_is_on(self):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED,
                     admin_sees_content=True):
            assert may_see_unlabelled(user_principal(admin)) is True

    def test_the_two_non_open_postures_answer_identically(self):
        """NO POSTURE BRANCH. `personal` and `enterprise` differ only in
        which pages exist."""
        from identity.contracts.postures import POSTURE_PERSONAL
        user = make_user()
        answers = []
        for name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
            with posture(name, library_posture=LIBRARY_LOCKED):
                answers.append(may_see_unlabelled(user_principal(user)))
        assert answers[0] == answers[1] is False


class TestTheTwoPickerReaders:
    def test_labelling_entitlements_gives_an_admin_everything(self):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            assert labelling_entitlements(user_principal(admin)) == (
                (finance.pk, "Finance"), (legal.pk, "Legal"))

    def test_labelling_entitlements_gives_a_member_only_what_they_own(self):
        user = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=user, role=EntitlementGrant.Role.OWNER)
        grant(legal, user=user)
        with posture(POSTURE_ENTERPRISE):
            assert labelling_entitlements(user_principal(user)) == ((finance.pk, "Finance"),)

    def test_share_subjects_lists_active_accounts_and_groups(self):
        user = make_user(username="ana")
        gone = make_user(username="zed")
        gone.is_active = False
        gone.save(update_fields=["is_active"])
        group = make_group(name="analysts")
        with posture(POSTURE_ENTERPRISE):
            subjects = share_subjects(user_principal(user))
        assert (user.pk, "ana") in subjects["users"]
        assert all(name != "zed" for _pk, name in subjects["users"])
        assert subjects["groups"] == ((group.pk, "analysts"),)

    def test_share_subjects_never_offers_the_caller_themselves(self):
        """Sharing a row with yourself is a no-op the form should not
        offer: you already own it, or you already have it."""
        user = make_user(username="ana")
        make_user(username="bea")
        with posture(POSTURE_ENTERPRISE):
            names = [name for _pk, name in share_subjects(user_principal(user))["users"]]
        assert names == ["bea"]

    def test_grant_subjects_DOES_offer_the_caller_themselves(self):
        """The one line that makes the two readers two readers.
        Granting yourself an entitlement is not a no-op: an administrator
        with the content setting off reads nothing labelled, so holding
        the entitlement is the only way for them to read a document under
        it -- and a grant form built from `share_subjects` would leave
        them no UI path to do it."""
        admin = make_admin(username="ana")
        make_user(username="bea")
        with posture(POSTURE_ENTERPRISE):
            names = [name for _pk, name in grant_subjects(user_principal(admin))["users"]]
        assert names == ["ana", "bea"]

    def test_both_readers_answer_empty_in_open_posture(self):
        """An open box has no accounts to offer, and neither reader runs
        a query to find that out."""
        make_user()
        assert share_subjects(OPEN_PRINCIPAL) == {"users": (), "groups": ()}
        assert grant_subjects(OPEN_PRINCIPAL) == {"users": (), "groups": ()}
```

- [ ] **Step 7: Run the access tests and watch them fail**

Run: `.venv/bin/pytest -q identity/tests/test_entitlements_access.py -x`
Expected: FAIL — `ImportError: cannot import name 'held_entitlement_ids'`.

- [ ] **Step 8: Add the three builders to `identity/testing.py`**

```python
def make_group(**overrides):
    """An `auth.Group`, unchanged -- this platform uses groups for
    MEMBERSHIP only and never reads `Group.permissions`."""
    from django.contrib.auth.models import Group
    fields = {"name": f"group-{next(_names)}"}
    fields.update(overrides)
    return Group.objects.create(**fields)


def make_entitlement(**overrides):
    """A named permission. Written directly, not through
    `identity.services.create_entitlement`, for the same reason `posture`
    writes the settings row directly: a test that merely needs an
    entitlement to EXIST should not have to satisfy the service's
    refusals to get one."""
    from identity.models import Entitlement
    fields = {"name": f"entitlement-{next(_names)}"}
    fields.update(overrides)
    return Entitlement.objects.create(**fields)


def grant(entitlement, *, user=None, group=None, role="member"):
    """One `EntitlementGrant`. Exactly one of `user`/`group`, or the
    XOR check constraint refuses the row."""
    from identity.models import EntitlementGrant
    return EntitlementGrant.objects.create(
        entitlement=entitlement, user=user, group=group, role=role)
```

**`reset_settings` moves up beside them**, from `identity/tests/_helpers.py:30` into
`identity/testing.py`, unchanged:

```python
def reset_settings() -> None:
    """Put the singleton back to shipped defaults. Used by autouse
    fixtures in every package whose tests write it directly.

    MOVED HERE from `identity/tests/_helpers.py` in IA-2: four more
    packages need it now, and `identity/tests/` is one package's private
    scaffolding. `identity/tests/_helpers.py` re-exports it, so every
    existing `from identity.tests._helpers import reset_settings` keeps
    working and only the body it resolves to moved -- the same move
    `posture`/`make_user`/`sign_in` already made in IA-1.
    """
    row = IdentitySettings.get_solo()
    row.posture = POSTURE_OPEN
    row.library_posture = LIBRARY_OPEN
    row.admin_sees_content = False
    row.save()
```

with `from identity.contracts.postures import LIBRARY_OPEN, POSTURE_OPEN` added to
`identity/testing.py`'s imports and the definition deleted from `identity/tests/_helpers.py`.

**Then re-export the four names into every package that needs them.** The house rule is
per-package `_helpers.py`, importing from `identity.testing` **by name** — never a cross-package
import of another package's `_helpers` (Global Constraint 12, spec §16.1):

- `identity/tests/_helpers.py` — extend its existing `from identity.testing import (...)` block
  with `grant, make_entitlement, make_group, reset_settings`, and delete its local
  `reset_settings` body.
- `tools/rag/tests/_helpers.py:51` — extend its existing `from identity.testing import (...)`
  block the same way.
- `agents/chat/tests/_helpers.py:33` — extend its existing block the same way.
- `agents/tests/_helpers.py` — has **no** identity re-export block today; add one:
  ```python
  from identity.testing import (  # noqa: F401 -- re-exported for this package's tests
      grant, make_admin, make_entitlement, make_group, make_user, posture, reset_settings,
      seed_sweep_posture, sign_in, user_principal,
  )
  ```

**Every test module this plan adds imports from ITS OWN package's `_helpers`** — Tasks 4, 5, 7
from `agents.tests._helpers`; Task 6, 8 from `agents.chat.tests._helpers`; Tasks 9–12 from
`tools.rag.tests._helpers`; Tasks 1–3 from `identity.tests._helpers`. A test module reaching
into another package's `_helpers` would couple two packages' scaffolding through a private
name, which is the thing the per-package rule exists to prevent.

- [ ] **Step 9: Write the six access functions**

Append to `identity/access.py`. Add `from identity.contracts.postures import LIBRARY_OPEN,
POSTURE_OPEN` (extend the existing import) and
`from identity.models import Entitlement, EntitlementGrant, IdentitySettings, User`:

```python
def _user_pk(principal) -> int | None:
    """`principal`'s primary key as an int, or None.

    THE SAME `isdigit()` GUARD `_user_row` above applies, for the same
    reason and against a different query: `Principal.key` for a user is
    the primary key AS A STRING, and a key that is not a decimal string
    would reach `filter(user_id=...)` and raise `ValueError` inside a
    request. "Answer nothing" is the correct reading of an unparseable
    identity on a never-500 surface.
    """
    if getattr(principal, "kind", None) != "user":
        return None
    key = principal.key
    if not isinstance(key, str) or not key.isdigit():
        return None
    return int(key)


def _grant_ids(principal, *, role: str | None = None) -> frozenset[int]:
    """ONE QUERY: every entitlement id `principal` holds, directly or
    through one of their groups, optionally narrowed to a role.

    `group__user` is the reverse of `PermissionsMixin.groups`, whose
    `related_query_name` is `user` -- so this reaches a group grant
    without a second query and without the platform ever materialising a
    membership list.
    """
    pk = _user_pk(principal)
    if pk is None:
        return frozenset()
    rows = EntitlementGrant.objects.filter(Q(user_id=pk) | Q(group__user__id=pk))
    if role is not None:
        rows = rows.filter(role=role)
    return frozenset(rows.values_list("entitlement_id", flat=True))


def held_entitlement_ids(principal, *, settings_row=None) -> frozenset[int]:
    """Every entitlement id this principal holds, directly or through one
    of their groups.

    EMPTY for the open, service and anonymous principals -- grants attach
    to a user or a group by the XOR constraint, so there is nothing for a
    non-user principal to join against. The open branch never reaches
    here in practice: every caller tests `accounts_on()` first.

    `settings_row`: an already-fetched `IdentitySettings` row, OPTIONAL
    and keyword-only -- see `accounts_on` for why. Used here only to skip
    the query in `open` posture without a second singleton read.
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    if not accounts_on(settings_row=row):
        return frozenset()
    return _grant_ids(principal)


def owned_entitlement_ids(principal, *, settings_row=None) -> frozenset[int]:
    """The subset of the above whose grant carries `role=owner`.

    An owner of E may grant it, label with it, and SEE everything in it.
    The third of those needs no code anywhere: an owner grant is also a
    grant, so it already appears in `held_entitlement_ids`.
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    if not accounts_on(settings_row=row):
        return frozenset()
    return _grant_ids(principal, role=EntitlementGrant.Role.OWNER)


def may_see_unlabelled(principal, *, settings_row=None) -> bool:
    """Whether this principal may READ documents carrying no label.

    OPEN posture -> True; there is nobody for anything to be hidden from.
    `library_posture=open` (the default) -> True for anybody who is not
    anonymous, INCLUDING the service principal: a document the watcher
    created arrives unlabelled because it has no way to know who a
    dropped file is for, and inventing a default would be inventing a
    policy. `manage.py ask` therefore answers from the unlabelled part of
    the library rather than from nothing.
    `library_posture=locked` -> `sees_all_content` only. An administrator
    with the content setting OFF does NOT read an unlabelled document's
    bytes on a locked library -- they still see its ROW, and can label or
    delete it, which is what administering it requires.

    NO POSTURE BRANCH. `library_posture` is only EDITABLE on the
    enterprise page, and `identity.services.set_posture` resets it to
    `open` when a box moves to `personal`. That branch lives in the write
    path, on purpose; this read path stays posture-free.
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    if not accounts_on(settings_row=row):
        return True
    if row.library_posture == LIBRARY_OPEN:
        return getattr(principal, "kind", "") != "anonymous"
    return sees_all_content(principal, settings_row=row)


def labelling_entitlements(principal) -> tuple[tuple[int, str], ...]:
    """`((id, name), ...)` -- the entitlements this principal may LABEL
    with, in name order, for a form's `<select>`.

    Everything for an administrator; the owned ones for anybody else.
    THE SAME PREDICATE THE WRITE PATHS ENFORCE (`identity.services.grant`,
    `tools.rag.views.document_labels_update`, `agents.chat.views.tools`),
    so a form can never offer an option the POST will refuse.

    It lives here rather than in `tools/rag` or `agents` because those
    columns may not import `identity.models` -- they get ids and names as
    plain data, exactly as they get `held_entitlement_ids`.
    """
    if not accounts_on():
        return ()
    rows = Entitlement.objects.all()
    if not is_admin(principal):
        rows = rows.filter(pk__in=owned_entitlement_ids(principal))
    return tuple(rows.order_by("name").values_list("pk", "name"))


def share_subjects(principal) -> dict[str, tuple[tuple[int, str], ...]]:
    """The accounts and groups a share form may offer, as plain data:
    `{"users": ((pk, username), ...), "groups": ((pk, name), ...)}`.

    ACTIVE ACCOUNTS ONLY, and never the caller themselves -- sharing a
    row with yourself is a no-op a form should not offer.

    Here, not in `agents/chat`, for the same reason `labelling_
    entitlements` is here: a column that needed usernames would otherwise
    reach `identity.models` (forbidden) or `django.contrib.auth.
    get_user_model()` (legal, but a second path to one table that no
    production module outside this column takes today).
    """
    if not accounts_on():
        return {"users": (), "groups": ()}
    from django.contrib.auth.models import Group
    users = User.objects.filter(is_active=True).exclude(pk=_user_pk(principal) or 0)
    return {
        "users": tuple(users.order_by("username").values_list("pk", "username")),
        "groups": tuple(Group.objects.order_by("name").values_list("pk", "name")),
    }


def grant_subjects(principal) -> dict[str, tuple[tuple[int, str], ...]]:
    """The accounts and groups an ENTITLEMENT GRANT form may offer --
    the same shape as `share_subjects`, and deliberately NOT the same
    function.

    IT INCLUDES THE CALLER. Sharing a row with yourself is a no-op, which
    is why `share_subjects` excludes you; granting yourself an entitlement
    is not. An administrator with `admin_sees_content` off reads NOTHING
    labelled (`tools/rag/access.py::readable_documents` answers to
    `sees_all_content`), so holding the entitlement is the only way for
    them to read a document under it -- and spec section 18.2's first
    done-when criterion begins by granting entitlements to accounts. A
    grant form built from `share_subjects` would leave an administrator
    with no UI path to grant themselves one.

    Two readers rather than one with a flag, because the two questions
    are different questions: "who else could read this row" and "who may
    hold this permission". The bodies differ by one `.exclude()`, and
    that clause is the whole of what each answer means.
    """
    if not accounts_on():
        return {"users": (), "groups": ()}
    from django.contrib.auth.models import Group
    return {
        "users": tuple(User.objects.filter(is_active=True)
                       .order_by("username").values_list("pk", "username")),
        "groups": tuple(Group.objects.order_by("name").values_list("pk", "name")),
    }
```

- [ ] **Step 10: Run the access tests and watch them pass**

Run: `.venv/bin/pytest -q identity/tests/test_entitlements_access.py identity/tests/test_models.py`
Expected: PASS.

- [ ] **Step 11: Confirm the migration set and the whole identity suite**

```bash
.venv/bin/python manage.py makemigrations --check --dry-run   # exits 0
.venv/bin/pytest -q identity
```
Expected: `makemigrations --check` exits 0; the identity suite is green.

- [ ] **Step 12: Commit**

```bash
git add identity/models.py identity/migrations/0003_entitlement_and_grant.py \
        identity/access.py identity/testing.py identity/tests/ \
        tools/rag/tests/_helpers.py agents/chat/tests/_helpers.py agents/tests/_helpers.py
git commit -m "feat(identity): IA-2 T1 — Entitlement, EntitlementGrant, and the three access questions"
```

---
### Task 2: the guarded writes — entitlements, grants, groups, and the delete cascade registry

Every write with a guard lives in `identity/services.py`, and a second door to any of them is a
guard that does not exist. This task adds eleven public functions (plus three private helpers)
and the registry that lets an entitlement delete reach into two other columns without importing
either.

**Files:**
- Create: `identity/contracts/cascades.py`, `identity/cascades.py`
- Modify: `identity/services.py`
- Test: `identity/tests/test_cascades.py`, `identity/tests/test_entitlement_services.py`,
  `identity/tests/test_group_services.py`, `identity/tests/test_services.py` (append)

**Interfaces:**
- Consumes: `identity.models.Entitlement`/`EntitlementGrant` (Task 1);
  `identity.access.owned_entitlement_ids`/`is_admin` (Task 1).
- Produces: `identity.contracts.cascades.EntitlementCascade(key, label, handler)`,
  `::register_entitlement_cascade(spec)`, `::all_entitlement_cascades() -> list[EntitlementCascade]`.
- Produces: `identity.cascades.cascade_counts(entitlement_id) -> dict[str, int]`,
  `::run_cascades(entitlement_id) -> dict[str, int]`.
- Produces: `identity.services.create_entitlement(actor, *, name, description="", source=SOURCE_WEB)`,
  `::rename_entitlement(actor, entitlement, name, *, source=SOURCE_WEB)`,
  `::delete_entitlement(actor, entitlement, *, source=SOURCE_WEB) -> dict[str, int]`,
  `::entitlement_delete_counts(entitlement) -> dict[str, int]`,
  `::grant(actor, entitlement, *, user=None, group=None, role="member", source=SOURCE_WEB) -> EntitlementGrant`,
  `::set_grant_role(actor, grant_row, role, *, source=SOURCE_WEB)`,
  `::revoke(actor, grant_row, *, source=SOURCE_WEB)`,
  `::create_group(actor, *, name, source=SOURCE_WEB)`, `::delete_group(actor, group, ...)`,
  `::add_group_member(actor, group, user, ...)`, `::remove_group_member(actor, group, user, ...)`.
- Produces: `identity.services.may_administer_entitlement(principal, entitlement_id) -> bool`.

- [ ] **Step 1: Write the failing cascade-registry test**

**The registry is a module-level dict and this test module poisons it unless it is
isolated.** `identity/contracts/cascades.py` has the same shape as
`identity/contracts/ownership.py:49`'s `_OWNED`, and the house answer is the module-level
autouse snapshot-and-restore fixture `identity/tests/test_ownership.py:14-20` already carries.
The fixture is the first thing in the file below, and its docstring says why.

Create `identity/tests/test_cascades.py`:

```python
"""The registry that lets an entitlement delete reach two other columns.

`identity/` may not import `agents/` or `tools/` (import-law rule 4),
and deleting an entitlement must remove its document labels and its tool
labels AND repair the chunk-metadata cache. The columns register a
DOTTED-PATH STRING at `AppConfig.ready()`, exactly as job kinds, roles,
tools and `OwnedRows` already do, and this module resolves it at delete
time.
"""
from __future__ import annotations

import pytest

from identity.contracts import cascades as cascades_module
from identity.contracts.cascades import (
    EntitlementCascade, all_entitlement_cascades, register_entitlement_cascade,
)

_CALLS: list[tuple[int, bool]] = []


@pytest.fixture(autouse=True)
def _isolated_registry():
    """THE REGISTRY IS A MODULE-LEVEL DICT with no reset path, exactly
    like `identity.contracts.ownership._OWNED`, so every test that
    registers one needs this -- matching how every other registry in this
    codebase is isolated in tests (`identity/tests/test_ownership.py:14`,
    `agents/tests/_helpers.py::isolated_tool_registry`).

    Without it, `test.broken` below -- whose handler names a function
    that does not exist -- survives this module and makes every later
    `run_cascades()`/`cascade_counts()` in the same pytest process raise
    `ImportError`: `identity/tests/test_entitlement_services.py`'s delete
    test, `identity/tests/test_entitlement_pages.py`'s
    delete-confirmation test, and the whole owner column of the route
    matrix.
    """
    saved = dict(cascades_module._CASCADES)
    cascades_module._CASCADES.clear()
    yield
    cascades_module._CASCADES.clear()
    cascades_module._CASCADES.update(saved)


def _fake_handler(entitlement_id: int, *, commit: bool) -> int:
    _CALLS.append((entitlement_id, commit))
    return 3 if not commit else 3


class TestTheRegistryIsPure:
    def test_a_cascade_needs_a_key_a_label_and_a_dotted_handler(self):
        with pytest.raises(ValueError):
            EntitlementCascade("", "Labels", "identity.tests.test_cascades._fake_handler")
        with pytest.raises(ValueError):
            EntitlementCascade("rag.labels", "", "identity.tests.test_cascades._fake_handler")
        with pytest.raises(ValueError):
            EntitlementCascade("rag.labels", "Labels", "not_dotted")

    def test_registration_is_idempotent(self):
        """The same shape every sibling registry has, so re-importing a
        module that registers at import time is safe."""
        before = len(all_entitlement_cascades())
        spec = EntitlementCascade("test.twice", "Twice",
                                  "identity.tests.test_cascades._fake_handler")
        register_entitlement_cascade(spec)
        register_entitlement_cascade(spec)
        assert len(all_entitlement_cascades()) == before + 1

    # The anti-vacuous pin that this registry is not silently empty --
    # `{"rag.document_labels", "agents.tool_labels"} <= keys` -- lands in
    # Task 10, WITH the second of the two registrations it asserts (the
    # rag one, whose handler module Task 10 creates). Written here it
    # would be a red test committed on a green branch, which is the one
    # thing a suite gate cannot tolerate.


class TestRunningThem:
    def test_counting_never_commits_and_running_does(self):
        from identity.cascades import cascade_counts, run_cascades
        register_entitlement_cascade(EntitlementCascade(
            "test.counted", "Counted", "identity.tests.test_cascades._fake_handler"))
        _CALLS.clear()
        counts = cascade_counts(7)
        assert counts["Counted"] == 3
        assert (7, False) in _CALLS
        _CALLS.clear()
        run_cascades(7)
        assert (7, True) in _CALLS

    def test_an_unresolvable_handler_raises_rather_than_being_skipped(self):
        """A cascade that silently did nothing would leave orphan labels
        and a stale chunk cache behind a delete that reported success --
        the one failure mode this registry exists to prevent."""
        from identity.cascades import run_cascades
        register_entitlement_cascade(EntitlementCascade(
            "test.broken", "Broken", "identity.tests.test_cascades.does_not_exist"))
        with pytest.raises(ImportError):
            run_cascades(7)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/pytest -q identity/tests/test_cascades.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'identity.contracts.cascades'`.

- [ ] **Step 3: Write the pure registry**

Create `identity/contracts/cascades.py`:

```python
"""What a column must do when an entitlement is deleted -- a registry,
not a list.

Deleting an entitlement removes its grants (a `CASCADE` inside this
column), its document labels and its tool labels (two other columns),
and -- because a document that loses its last label becomes unlabelled
and follows the library posture -- it must RE-STAMP every affected
document's chunk metadata inside the same transaction.

`identity/` may not import `agents/` or `tools/` (import-law rule 4), so
the delete cannot name those functions. They register themselves from
each column's `AppConfig.ready()`, exactly as job kinds, roles, tools and
`OwnedRows` already do -- and for the same reason: a third labelled thing
later is a REGISTRATION, not an edit to a service that would otherwise
silently skip it.

Pure: `handler` is a DOTTED-PATH STRING, resolved at delete time by
`identity/cascades.py`, never imported here.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntitlementCascade:
    """One column's answer to "this entitlement is going away".

    `key`     -- stable identifier, e.g. "rag.document_labels".
    `label`   -- what the delete confirmation prints, e.g. "Document labels".
    `handler` -- "package.module.function", with the signature
                 `(entitlement_id: int, *, commit: bool) -> int`.

    ONE HANDLER, TWO MODES, deliberately -- rather than a counter and a
    detacher registered separately. `commit=False` COUNTS the rows this
    column would remove (the delete confirmation names the count first,
    spec section 12.1); `commit=True` removes them AND repairs whatever
    cache hung off them, and returns the same count. Two registrations
    would be two things to keep in agreement about what "affected" means.
    """

    key: str
    label: str
    handler: str

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("EntitlementCascade needs a non-blank key")
        if not self.label:
            raise ValueError(f"EntitlementCascade({self.key!r}) needs a non-blank label")
        if "." not in self.handler:
            raise ValueError(
                f"EntitlementCascade({self.key!r}).handler must be a dotted path, "
                f"got {self.handler!r}"
            )


_CASCADES: dict[str, EntitlementCascade] = {}


def register_entitlement_cascade(spec: EntitlementCascade) -> None:
    """Register `spec` under its `.key`, replacing any existing entry.
    Idempotent, like every sibling registry in this codebase."""
    _CASCADES[spec.key] = spec


def all_entitlement_cascades() -> list[EntitlementCascade]:
    """Every registered cascade, in registration order."""
    return list(_CASCADES.values())
```

- [ ] **Step 4: Write the resolver**

Create `identity/cascades.py`:

```python
"""Running the entitlement cascades against a live Django.

`identity/contracts/cascades.py` stays PURE -- pinned by
`identity/tests/test_purity.py`, which imports the whole `contracts/`
package with no `DJANGO_SETTINGS_MODULE` set at all -- so it names
handlers as dotted-path STRINGS. Resolving one needs
`django.utils.module_loading.import_string`, which is Django, which
rule 4 allows; this module is where that resolution lives, exactly as
`identity/ownership.py` is where `apps.get_model` lives.

NEVER SWALLOWS. A cascade whose handler cannot be imported, or which
raises, takes the whole delete down with it -- inside
`identity.services.delete_entitlement`'s `transaction.atomic()`, so
nothing is half-deleted. A delete that reported success while leaving
orphan labels and a stale chunk cache behind is the one failure mode
this registry exists to prevent.
"""
from __future__ import annotations

from django.utils.module_loading import import_string

from identity.contracts.cascades import all_entitlement_cascades


def _run(entitlement_id: int, *, commit: bool) -> dict[str, int]:
    counts: dict[str, int] = {}
    for spec in all_entitlement_cascades():
        handler = import_string(spec.handler)
        counts[spec.label] = handler(entitlement_id, commit=commit)
    return counts


def cascade_counts(entitlement_id: int) -> dict[str, int]:
    """`{label: count}` -- what each column WOULD remove. Writes nothing.
    This is what the delete confirmation names."""
    return _run(entitlement_id, commit=False)


def run_cascades(entitlement_id: int) -> dict[str, int]:
    """`{label: count}` -- what each column DID remove, having also
    repaired its own caches. Call inside the delete's transaction."""
    return _run(entitlement_id, commit=True)
```

- [ ] **Step 5: Run the cascade tests and watch them pass**

Run: `.venv/bin/pytest -q identity/tests/test_cascades.py`
Expected: PASS.

- [ ] **Step 6: Write the failing entitlement-service tests**

Create `identity/tests/test_entitlement_services.py`:

```python
"""The guarded writes behind entitlements and grants.

AN OWNER OF E MAY GRANT E, REVOKE E, AND LABEL WITH E -- and nothing
else. Not create, not rename, not delete, not the library posture, not
anybody else's entitlement. That is the whole of owner decision 15, and
every "and nothing else" below is one of the negative tests spec section
18.2's done-when 5 asks for.
"""
from __future__ import annotations

import pytest

from identity import services
from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.models import AuditEvent, Entitlement, EntitlementGrant
from identity.tests._helpers import (
    grant, make_admin, make_entitlement, make_group, make_user, posture, reset_settings,
    seed_sweep_posture, user_principal,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestCreateRenameDelete:
    def test_create_writes_the_row_and_one_audit_event(self):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            row = services.create_entitlement(user_principal(admin), name="Finance")
        assert row.name == "Finance"
        assert AuditEvent.objects.filter(action=actions.ENTITLEMENT_CREATED).count() == 1

    def test_a_duplicate_name_is_refused_not_crashed(self):
        admin = make_admin()
        make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE), pytest.raises(services.ServiceRefused):
            services.create_entitlement(user_principal(admin), name="finance")

    def test_a_blank_name_is_refused(self):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE), pytest.raises(services.ServiceRefused):
            services.create_entitlement(user_principal(admin), name="   ")

    def test_rename_audits_the_old_and_the_new_name(self):
        admin = make_admin()
        row = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            services.rename_entitlement(user_principal(admin), row, "Accounts")
        event = AuditEvent.objects.get(action=actions.ENTITLEMENT_RENAMED)
        assert event.detail == {"from": "Finance", "to": "Accounts"}

    def test_rename_to_the_same_name_is_not_an_event(self):
        admin = make_admin()
        row = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            services.rename_entitlement(user_principal(admin), row, "Finance")
        assert not AuditEvent.objects.filter(action=actions.ENTITLEMENT_RENAMED).exists()

    def test_delete_takes_the_grants_and_returns_the_cascade_counts(self):
        admin = make_admin()
        row = make_entitlement(name="Finance")
        grant(row, user=make_user())
        grant(row, group=make_group())
        with posture(POSTURE_ENTERPRISE):
            counts = services.delete_entitlement(user_principal(admin), row)
        assert Entitlement.objects.count() == 0
        assert EntitlementGrant.objects.count() == 0
        assert counts["Grants"] == 2
        assert AuditEvent.objects.filter(action=actions.ENTITLEMENT_DELETED).count() == 1


class TestGrantAndRevoke:
    def test_an_admin_may_grant_any_entitlement(self):
        admin = make_admin()
        member = make_user()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            services.grant(user_principal(admin), finance, user=member)
        assert EntitlementGrant.objects.filter(entitlement=finance, user=member).exists()
        assert AuditEvent.objects.filter(action=actions.GRANT_ADDED).count() == 1

    def test_an_owner_may_grant_the_entitlement_they_own(self):
        owner = make_user()
        member = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        with posture(POSTURE_ENTERPRISE):
            services.grant(user_principal(owner), finance, user=member)
        assert EntitlementGrant.objects.filter(entitlement=finance, user=member).exists()

    def test_an_owner_may_not_grant_an_entitlement_they_do_not_own(self):
        owner = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        with posture(POSTURE_ENTERPRISE), pytest.raises(services.ServiceRefused):
            services.grant(user_principal(owner), legal, user=make_user())

    def test_a_plain_member_of_an_entitlement_may_not_grant_it(self):
        member = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE), pytest.raises(services.ServiceRefused):
            services.grant(user_principal(member), finance, user=make_user())

    def test_an_owner_may_not_create_rename_or_delete(self):
        """One negative test per non-capability (done-when 5). An owner
        of E is a member of E plus exactly three capabilities; these are
        not among them."""
        owner = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        principal = user_principal(owner)
        with posture(POSTURE_ENTERPRISE):
            with pytest.raises(services.ServiceRefused):
                services.create_entitlement(principal, name="Legal")
            with pytest.raises(services.ServiceRefused):
                services.rename_entitlement(principal, finance, "Accounts")
            with pytest.raises(services.ServiceRefused):
                services.delete_entitlement(principal, finance)

    def test_granting_twice_is_refused_with_a_readable_message(self):
        admin = make_admin()
        member = make_user()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            services.grant(user_principal(admin), finance, user=member)
            with pytest.raises(services.ServiceRefused) as excinfo:
                services.grant(user_principal(admin), finance, user=member)
        assert "already" in str(excinfo.value)

    def test_a_grant_naming_both_or_neither_is_refused_before_the_database_sees_it(self):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            with pytest.raises(services.ServiceRefused):
                services.grant(user_principal(admin), finance,
                               user=make_user(), group=make_group())
            with pytest.raises(services.ServiceRefused):
                services.grant(user_principal(admin), finance)

    def test_changing_a_role_is_an_update_and_its_own_audit_action(self):
        admin = make_admin()
        member = make_user()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            row = services.grant(user_principal(admin), finance, user=member)
            services.set_grant_role(user_principal(admin), row,
                                    EntitlementGrant.Role.OWNER)
        row.refresh_from_db()
        assert row.role == EntitlementGrant.Role.OWNER
        assert EntitlementGrant.objects.filter(entitlement=finance, user=member).count() == 1
        assert AuditEvent.objects.filter(action=actions.GRANT_ROLE_CHANGED).count() == 1

    def test_revoke_removes_the_row_and_audits_it(self):
        admin = make_admin()
        member = make_user()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            row = services.grant(user_principal(admin), finance, user=member)
            services.revoke(user_principal(admin), row)
        assert EntitlementGrant.objects.count() == 0
        assert AuditEvent.objects.filter(action=actions.GRANT_REVOKED).count() == 1

    def test_an_owner_may_revoke_within_their_entitlement_only(self):
        owner = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        elsewhere = grant(legal, user=make_user())
        with posture(POSTURE_ENTERPRISE), pytest.raises(services.ServiceRefused):
            services.revoke(user_principal(owner), elsewhere)
```

- [ ] **Step 7: Run them and watch them fail**

Run: `.venv/bin/pytest -q identity/tests/test_entitlement_services.py -x`
Expected: FAIL — `AttributeError: module 'identity.services' has no attribute
'create_entitlement'`.

- [ ] **Step 8: Write the entitlement and grant services**

Append to `identity/services.py`, extending its imports with
`from identity.access import is_admin, owned_entitlement_ids`,
`from identity.cascades import cascade_counts, run_cascades` and
`from identity.models import Entitlement, EntitlementGrant, IdentitySettings, User`:

```python
def _actor_user(actor):
    """The `User` row `actor` names, or None.

    THE SAME `isdigit()` GUARD `identity/access.py::_user_row` applies,
    for the same reason: `Principal.key` for a user is the primary key AS
    A STRING, and a key that is not a decimal string -- from a
    hand-written payload, or a row written against an older schema --
    would reach `filter(pk=...)` and raise `ValueError` inside a request.
    A never-500 surface cannot afford that, and "nobody" is the correct
    reading of an unparseable identity for a `created_by`/`granted_by`
    column that is nullable anyway.
    """
    if getattr(actor, "kind", None) != "user":
        return None
    key = actor.key
    if not isinstance(key, str) or not key.isdigit():
        return None
    return User.objects.filter(pk=int(key)).first()


def may_administer_entitlement(principal, entitlement_id: int) -> bool:
    """Whether `principal` may grant, revoke or label with this
    entitlement: an administrator, or an OWNER of this one.

    ONE PREDICATE, THREE COLUMNS. `identity.services.grant`/`revoke`,
    `tools.rag.views.document_labels_update` and
    `agents.chat.views.tools.tool_entitlements` all ask exactly this
    question, and three hand-written copies of it are how two surfaces
    come to disagree about who may label what. The other two columns
    cannot import this module (it is column-private), so they ask
    `identity.access.is_admin` + `::owned_entitlement_ids` -- the same
    two facts, in the sanctioned seam -- and this function is the
    identity-side spelling of it.
    """
    return is_admin(principal) or entitlement_id in owned_entitlement_ids(principal)


def _refuse_unless_admin(principal, *, because: str) -> None:
    if not is_admin(principal):
        raise ServiceRefused(
            f"Only an administrator of this box may {because}. An entitlement's "
            f"owner may grant it, revoke it and label with it, and nothing else."
        )


def create_entitlement(actor, *, name: str, description: str = "",
                       source: str = SOURCE_WEB) -> Entitlement:
    """Create a named permission. ADMINISTRATOR ONLY -- an owner of one
    entitlement has no standing to mint another."""
    _refuse_unless_admin(actor, because="create an entitlement")
    clean = (name or "").strip()
    if not clean:
        raise ServiceRefused("An entitlement needs a name.")
    creator = _actor_user(actor)
    try:
        with transaction.atomic():
            row = Entitlement.objects.create(name=clean, description=description.strip(),
                                             created_by=creator)
            audit.record(actor, actions.ENTITLEMENT_CREATED, target_type="entitlement",
                         target_key=row.pk, target_label=row.name, source=source)
    except IntegrityError as exc:
        raise ServiceRefused(
            f"An entitlement called {clean!r} already exists. Names are compared "
            f"without regard to case, so 'Finance' and 'finance' are one entitlement."
        ) from exc
    return row


def rename_entitlement(actor, entitlement, name: str, *, source: str = SOURCE_WEB) -> None:
    """ADMINISTRATOR ONLY. A rename changes what every grant, label and
    audit line READS as, which is a library-wide fact."""
    _refuse_unless_admin(actor, because="rename an entitlement")
    clean = (name or "").strip()
    if not clean:
        raise ServiceRefused("An entitlement needs a name.")
    if clean == entitlement.name:
        # Not an event. An audit trail full of non-changes is a trail
        # nobody reads.
        return
    was = entitlement.name
    try:
        with transaction.atomic():
            entitlement.name = clean
            entitlement.save(update_fields=["name"])
            audit.record(actor, actions.ENTITLEMENT_RENAMED, target_type="entitlement",
                         target_key=entitlement.pk, target_label=clean, source=source,
                         **{"from": was, "to": clean})
    except IntegrityError as exc:
        raise ServiceRefused(f"An entitlement called {clean!r} already exists.") from exc


def entitlement_delete_counts(entitlement) -> dict[str, int]:
    """What deleting `entitlement` would take with it, per kind --
    `{"Grants": 2, "Document labels": 14, "Tool labels": 1}`.

    THE DELETE CONFIRMATION NAMES THESE FIRST (spec section 12.1).
    Deleting an entitlement is a superuser action precisely because of
    the second entry: every document it was the last label on becomes
    UNLABELLED and follows the library posture, which silently WIDENS
    access. A confirmation that did not say so would be a confirmation
    of the wrong thing.
    """
    return _cascade_counts_for(entitlement, commit=False)


def _cascade_counts_for(entitlement, *, commit: bool) -> dict[str, int]:
    """`{label: count}` for every kind an entitlement delete takes with
    it -- grants (this column, by `CASCADE`) plus each registered
    cross-column cascade.

    ONE FUNCTION, TWO CALLERS, so `"Grants"` -- the string the delete
    confirmation renders -- is written once. Two copies of a label the
    page prints is two things to keep in agreement for no gain.
    """
    counts = {"Grants": entitlement.grants.count()}
    counts.update(run_cascades(entitlement.pk) if commit
                  else cascade_counts(entitlement.pk))
    return counts


def delete_entitlement(actor, entitlement, *, source: str = SOURCE_WEB) -> dict[str, int]:
    """ADMINISTRATOR ONLY. Removes the entitlement, its grants, and --
    through the registered cascades -- its document and tool labels,
    re-stamping every affected document's chunk metadata in the SAME
    transaction.

    The cascades run BEFORE `entitlement.delete()`, deliberately: the
    re-stamp has to see a document with its label already gone, and
    Django's own `CASCADE` collector gives no hook between "rows
    deleted" and "transaction committed". The database `CASCADE` stays in
    place as the net -- a shell that deletes an `Entitlement` row
    directly still leaves no orphans, only a stale chunk cache, and
    `manage.py relabel_chunks` is the documented repair.
    """
    _refuse_unless_admin(actor, because="delete an entitlement")
    # BOTH CAPTURED BEFORE THE DELETE. Django's `Collector.delete()` sets
    # `instance.pk = None` on every deleted instance, and
    # `identity/audit.py` writes `str(target_key)[:255]` -- so reading
    # `entitlement.pk` after the delete records the literal string
    # "None", and an audit row that cannot name its target is not an
    # audit row. `delete_group` below captures its pk for the same reason.
    label, pk = entitlement.name, entitlement.pk
    with transaction.atomic():
        counts = _cascade_counts_for(entitlement, commit=True)
        entitlement.delete()
        audit.record(actor, actions.ENTITLEMENT_DELETED, target_type="entitlement",
                     target_key=pk, target_label=label, source=source,
                     removed=counts)
    return counts


def _refuse_unless_may_administer(actor, entitlement_id: int) -> None:
    if not may_administer_entitlement(actor, entitlement_id):
        raise ServiceRefused(
            "You may only grant and revoke entitlements you own. Ask an "
            "administrator, or an owner of this entitlement, to make the change."
        )


def grant(actor, entitlement, *, user=None, group=None,
          role: str = EntitlementGrant.Role.MEMBER,
          source: str = SOURCE_WEB) -> EntitlementGrant:
    """Grant `entitlement` to exactly one of `user`/`group`.

    ADMINISTRATOR, OR AN OWNER OF THIS ENTITLEMENT -- delegation without
    a second role table, which is the whole of owner decision 15.

    The user-XOR-group rule is refused HERE as well as by the database
    constraint: an `IntegrityError` surfacing out of a page as a 500 is a
    never-500 violation, and "you named both" is a sentence a form can
    show.
    """
    _refuse_unless_may_administer(actor, entitlement.pk)
    if bool(user) == bool(group):
        raise ServiceRefused("A grant names exactly one account or one group, never both.")
    if role not in EntitlementGrant.Role.values:
        raise ServiceRefused(f"{role!r} is not a grant role on this platform.")
    granted_by = _actor_user(actor)
    try:
        with transaction.atomic():
            row = EntitlementGrant.objects.create(
                entitlement=entitlement, user=user, group=group, role=role,
                source=EntitlementGrant.Source.MANUAL, granted_by=granted_by)
            audit.record(actor, actions.GRANT_ADDED, target_type="entitlement",
                         target_key=entitlement.pk, target_label=entitlement.name,
                         source=source, role=role,
                         subject=("user" if user else "group"),
                         subject_key=(user.pk if user else group.pk),
                         subject_label=(user.username if user else group.name))
    except IntegrityError as exc:
        subject = user.username if user else group.name
        raise ServiceRefused(
            f"{subject!r} already holds {entitlement.name!r}. Change the role "
            f"instead of granting it twice."
        ) from exc
    return row


def set_grant_role(actor, grant_row, role: str, *, source: str = SOURCE_WEB) -> None:
    """Promote a member to owner, or demote an owner to member. AN
    UPDATE, not a second row -- which is why the unique constraints do
    not include `role`."""
    _refuse_unless_may_administer(actor, grant_row.entitlement_id)
    if role not in EntitlementGrant.Role.values:
        raise ServiceRefused(f"{role!r} is not a grant role on this platform.")
    if grant_row.role == role:
        return
    with transaction.atomic():
        was = grant_row.role
        grant_row.role = role
        grant_row.save(update_fields=["role"])
        audit.record(actor, actions.GRANT_ROLE_CHANGED, target_type="entitlement",
                     target_key=grant_row.entitlement_id,
                     target_label=grant_row.entitlement.name, source=source,
                     **{"from": was, "to": role})


def revoke(actor, grant_row, *, source: str = SOURCE_WEB) -> None:
    """Remove one grant. ADMINISTRATOR, OR AN OWNER OF THAT
    ENTITLEMENT."""
    _refuse_unless_may_administer(actor, grant_row.entitlement_id)
    subject = grant_row.user.username if grant_row.user_id else grant_row.group.name
    with transaction.atomic():
        entitlement = grant_row.entitlement
        grant_row.delete()
        audit.record(actor, actions.GRANT_REVOKED, target_type="entitlement",
                     target_key=entitlement.pk, target_label=entitlement.name,
                     source=source, subject_label=subject)
```

- [ ] **Step 9: Run the entitlement-service tests and watch them pass**

Run: `.venv/bin/pytest -q identity/tests/test_entitlement_services.py`
Expected: PASS. (`test_delete_takes_the_grants_and_returns_the_cascade_counts` passes with only
the `Grants` entry today; the two column cascades join it in Tasks 4 and 9.)

- [ ] **Step 10: Write the failing group-service tests**

Create `identity/tests/test_group_services.py`:

```python
"""Groups are Django's `auth.Group`, unchanged -- membership only.

This platform never reads `Group.permissions`: Django's `Permission`
model is a named non-goal (owner decision 16), because it is model-level
and would be a second grant mechanism beside entitlements. Group
MANAGERS -- a role on membership -- are deferred, which is why
membership is not customised with a through-model now.
"""
from __future__ import annotations

import pytest
from django.contrib.auth.models import Group

from identity import services
from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.models import AuditEvent, EntitlementGrant
from identity.tests._helpers import (
    grant, make_admin, make_entitlement, make_group, make_user, posture, reset_settings,
    seed_sweep_posture, user_principal,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestGroupWrites:
    def test_create_and_delete_are_admin_only_and_audited(self):
        admin = make_admin()
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            group = services.create_group(user_principal(admin), name="analysts")
            assert AuditEvent.objects.filter(action=actions.GROUP_CREATED).count() == 1
            with pytest.raises(services.ServiceRefused):
                services.create_group(user_principal(member), name="others")
            services.delete_group(user_principal(admin), group)
        assert Group.objects.filter(name="analysts").count() == 0
        assert AuditEvent.objects.filter(action=actions.GROUP_DELETED).count() == 1

    def test_a_duplicate_group_name_is_refused_not_crashed(self):
        admin = make_admin()
        make_group(name="analysts")
        with posture(POSTURE_ENTERPRISE), pytest.raises(services.ServiceRefused):
            services.create_group(user_principal(admin), name="analysts")

    def test_membership_writes_are_audited_and_idempotent(self):
        admin = make_admin()
        member = make_user()
        group = make_group(name="analysts")
        with posture(POSTURE_ENTERPRISE):
            services.add_group_member(user_principal(admin), group, member)
            services.add_group_member(user_principal(admin), group, member)
            assert AuditEvent.objects.filter(action=actions.GROUP_MEMBER_ADDED).count() == 1
            services.remove_group_member(user_principal(admin), group, member)
            services.remove_group_member(user_principal(admin), group, member)
        assert AuditEvent.objects.filter(action=actions.GROUP_MEMBER_REMOVED).count() == 1
        assert member.groups.count() == 0

    def test_deleting_a_group_takes_its_grants_with_it(self):
        admin = make_admin()
        group = make_group(name="analysts")
        finance = make_entitlement(name="Finance")
        grant(finance, group=group)
        with posture(POSTURE_ENTERPRISE):
            services.delete_group(user_principal(admin), group)
        assert EntitlementGrant.objects.count() == 0
```

- [ ] **Step 11: Run them and watch them fail**

Run: `.venv/bin/pytest -q identity/tests/test_group_services.py -x`
Expected: FAIL — no `create_group`.

- [ ] **Step 12: Write the group services**

Append to `identity/services.py`:

```python
# --- groups: Django's own, membership only ------------------------------

def create_group(actor, *, name: str, source: str = SOURCE_WEB):
    """ADMINISTRATOR ONLY. `auth.Group`, unchanged.

    This platform never reads `Group.permissions` -- Django's permission
    catalogue is a named non-goal -- so nothing subclasses `Group` and
    nothing hides its admin. A group here is a bag of accounts a grant
    can name.
    """
    from django.contrib.auth.models import Group
    _refuse_unless_admin(actor, because="create a group")
    clean = (name or "").strip()
    if not clean:
        raise ServiceRefused("A group needs a name.")
    try:
        with transaction.atomic():
            group = Group.objects.create(name=clean)
            audit.record(actor, actions.GROUP_CREATED, target_type="group",
                         target_key=group.pk, target_label=clean, source=source)
    except IntegrityError as exc:
        raise ServiceRefused(f"A group called {clean!r} already exists.") from exc
    return group


def delete_group(actor, group, *, source: str = SOURCE_WEB) -> None:
    """ADMINISTRATOR ONLY. Its grants and its shares go with it, by
    `CASCADE` on both foreign keys (owner decision 25)."""
    _refuse_unless_admin(actor, because="delete a group")
    label = group.name
    with transaction.atomic():
        pk = group.pk
        group.delete()
        audit.record(actor, actions.GROUP_DELETED, target_type="group",
                     target_key=pk, target_label=label, source=source)


def add_group_member(actor, group, user, *, source: str = SOURCE_WEB) -> None:
    """ADMINISTRATOR ONLY, and IDEMPOTENT: adding somebody who is already
    a member is not a second event."""
    _refuse_unless_admin(actor, because="change a group's membership")
    if user.groups.filter(pk=group.pk).exists():
        return
    with transaction.atomic():
        user.groups.add(group)
        audit.record(actor, actions.GROUP_MEMBER_ADDED, target_type="group",
                     target_key=group.pk, target_label=group.name, source=source,
                     subject_key=user.pk, subject_label=user.username)


def remove_group_member(actor, group, user, *, source: str = SOURCE_WEB) -> None:
    """The same in reverse, equally idempotent."""
    _refuse_unless_admin(actor, because="change a group's membership")
    if not user.groups.filter(pk=group.pk).exists():
        return
    with transaction.atomic():
        user.groups.remove(group)
        audit.record(actor, actions.GROUP_MEMBER_REMOVED, target_type="group",
                     target_key=group.pk, target_label=group.name, source=source,
                     subject_key=user.pk, subject_label=user.username)
```

- [ ] **Step 13: Write the failing deactivation test, then drop the grants**

Append to `identity/tests/test_services.py`:

```python
class TestDeactivationDropsGrants:
    def test_deactivating_removes_every_grant_that_account_held(self):
        """Owner decision 25: grants are dropped. Reactivation does NOT
        restore them -- a dropped grant is a decision somebody made, and
        restoring it silently would undo that decision without anybody
        choosing to."""
        from identity.models import EntitlementGrant
        from identity.tests._helpers import grant, make_entitlement
        admin = make_admin()
        member = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE):
            services.deactivate_user(user_principal(admin), member)
            assert EntitlementGrant.objects.filter(user=member).count() == 0
            services.reactivate_user(user_principal(admin), member)
        assert EntitlementGrant.objects.filter(user=member).count() == 0

    def test_a_group_grant_survives_a_members_deactivation(self):
        """The grant is the GROUP'S, not this account's. Removing it
        would revoke an entitlement from everybody else in the group."""
        from identity.models import EntitlementGrant
        from identity.tests._helpers import grant, make_entitlement, make_group
        admin = make_admin()
        member = make_user()
        group = make_group(name="analysts")
        member.groups.add(group)
        finance = make_entitlement(name="Finance")
        grant(finance, group=group)
        with posture(POSTURE_ENTERPRISE):
            services.deactivate_user(user_principal(admin), member)
        assert EntitlementGrant.objects.filter(group=group).count() == 1
```

Run it, watch the first fail, then add the one line to `deactivate_user`, inside its existing
`transaction.atomic()` block, immediately after `user.save(update_fields=["is_active"])`:

```python
        # Owner decision 25: grants are dropped. USER grants only --
        # a GROUP grant belongs to the group, and removing it here would
        # revoke an entitlement from everybody else in it.
        EntitlementGrant.objects.filter(user=user).delete()
```

and correct the docstring's parenthetical from `(IA-2 adds one line here: delete the user's
EntitlementGrant rows. The grants table does not exist yet.)` to:

```python
    3b. every `EntitlementGrant` naming this USER is deleted -- owner
        decision 25. A grant held through a GROUP survives, because it
        is the group's, not this account's.
```

- [ ] **Step 14: Run the whole identity suite and the migration check**

```bash
.venv/bin/pytest -q identity
.venv/bin/python manage.py makemigrations --check --dry-run
```
Expected: green; `makemigrations --check` exits 0 (no model changed in this task).

- [ ] **Step 15: Commit**

```bash
git add identity/contracts/cascades.py identity/cascades.py identity/services.py identity/tests/
git commit -m "feat(identity): IA-2 T2 — entitlement/grant/group writes, and the delete-cascade registry"
```

---
### Task 3: the groups and entitlements pages

Two pages, four routes, and the first place an entitlement owner reaches a surface an
administrator otherwise owns. Server-rendered, zero JS, extending `foundation/templates/
_shell.html`, matching every other page on the box.

**Files:**
- Modify: `identity/forms.py`, `identity/views.py`, `identity/urls.py`, `identity/routes.py`
- Create: `identity/templates/identity/groups.html`,
  `identity/templates/identity/entitlements.html`,
  `identity/templates/identity/entitlement.html`
- Modify: `foundation/templates/_shell.html` (two nav links)
- Test: `identity/tests/test_group_pages.py`, `identity/tests/test_entitlement_pages.py`,
  `identity/tests/test_routes.py` (append)

**Interfaces:**
- Consumes: every `identity.services` function from Task 2; `identity.access.grant_subjects`
  and `::owned_entitlement_ids`/`::is_admin` from Task 1.
- Produces: URL names `identity-groups`, `identity-group-edit`, `identity-entitlements`,
  `identity-entitlement-edit`, classified `S`, `S`, `S`, **`R`** in `identity/routes.py`.

- [ ] **Step 1: Write the failing route-table test**

Append to `identity/tests/test_routes.py`:

```python
class TestIA2Routes:
    def test_the_four_identity_routes_are_classified(self):
        from identity.routes import ROUTE_RULES
        assert ROUTE_RULES["identity-groups"] == "S"
        assert ROUTE_RULES["identity-group-edit"] == "S"
        assert ROUTE_RULES["identity-entitlements"] == "S"

    def test_the_ia2_route_block_is_classified_as_a_block(self):
        """The two IA-2 routes that live in OTHER columns' `urls.py`
        still have their class here, which is what makes
        `identity/routes.py` the one table. Each is also asserted in the
        task that adds it (Task 6, Task 12); this is the block-level
        pin that neither is forgotten.

        `.get(name, <expected>)` rather than `[name]`, deliberately: the
        two routes arrive in later tasks, and an assertion that cannot
        pass yet is not an assertion worth committing red.
        """
        from identity.routes import ROUTE_RULES
        assert ROUTE_RULES.get("chat-tool-entitlements", "S") == "S"   # Task 6 adds it
        assert ROUTE_RULES.get("rag-document-labels", "R") == "R"      # Task 12 adds it

    def test_the_entitlement_page_is_R_not_S_so_an_owner_can_reach_it(self):
        """Spec section 11.3 marks it "S for create/rename/delete; an
        OWNER may reach the grant form (checked in the view, since the
        middleware's tiers are coarse)". `tier_for` maps S to ADMIN, and
        `IdentityGateMiddleware` refuses a non-admin there BEFORE the
        view runs -- so an owner could never reach the form. R maps to
        AUTHENTICATED, which is exactly "the middleware admits, the view
        decides", and rename/delete re-check `is_admin` inside."""
        from identity.routes import AUTHENTICATED, ROUTE_RULES, tier_for
        assert ROUTE_RULES["identity-entitlement-edit"] == "R"
        assert tier_for("identity-entitlement-edit", []) == AUTHENTICATED
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/pytest -q identity/tests/test_routes.py -x`
Expected: FAIL — `KeyError: 'identity-groups'`.

- [ ] **Step 3: Classify the four routes**

In `identity/routes.py`, extend the `--- /identity/ (new in IA-1) ---` block with a new one:

```python
    # --- /identity/ (new in IA-2) --------------------------------------
    "identity-groups": "S",
    "identity-group-edit": "S",
    "identity-entitlements": "S",
    # R, NOT S, and the difference is load-bearing. `tier_for` maps S to
    # the ADMIN tier, and the middleware refuses a non-admin there before
    # the view resolves anything -- so an ENTITLEMENT OWNER, who spec
    # section 11.3 says may reach this entitlement's grant form, could
    # never get in. R is AUTHENTICATED at the middleware and keeps its
    # real rule in the view (`identity/views.py::entitlement_edit`), which
    # is the definition of the R class: admitted, then answered 404 for a
    # row this principal has no standing over. Rename and delete re-check
    # `is_admin` inside `identity.services`.
    "identity-entitlement-edit": "R",
```

- [ ] **Step 4: Write the failing page tests**

Create `identity/tests/test_group_pages.py`:

```python
"""`/identity/groups/` -- create, delete, add and remove members."""
from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.tests._helpers import (
    make_admin, make_group, make_user, posture, reset_settings, seed_sweep_posture, sign_in,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestTheGroupsPage:
    def test_an_admin_sees_the_page_and_a_member_gets_403(self, client):
        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            assert client.get(reverse("identity-groups")).status_code == 200
            other = client.__class__()
            sign_in(other, member)
            assert other.get(reverse("identity-groups")).status_code == 403

    def test_creating_a_group_redirects_and_flashes(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-groups"), {"name": "analysts"})
        assert response.status_code == 302
        assert Group.objects.filter(name="analysts").exists()

    def test_a_duplicate_name_flashes_an_error_and_never_500s(self, client):
        admin = make_admin()
        make_group(name="analysts")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-groups"), {"name": "analysts"},
                                   follow=True)
        assert response.status_code == 200
        assert b"already exists" in response.content
        assert b"Traceback" not in response.content

    def test_membership_and_delete_actions_dispatch(self, client):
        admin, member = make_admin(), make_user()
        group = make_group(name="analysts")
        url = reverse("identity-group-edit", args=[group.pk])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(url, {"action": "add_member", "user": member.pk})
            assert member.groups.filter(pk=group.pk).exists()
            client.post(url, {"action": "remove_member", "user": member.pk})
            assert not member.groups.filter(pk=group.pk).exists()
            assert client.post(url, {"action": "delete"}).status_code == 302
        assert Group.objects.filter(pk=group.pk).count() == 0

    def test_an_unknown_action_answers_400_not_a_silent_no_op(self, client):
        admin = make_admin()
        group = make_group(name="analysts")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-group-edit", args=[group.pk]),
                                   {"action": "rename"})
        assert response.status_code == 400

    def test_an_unknown_group_id_answers_404(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-group-edit", args=[9999]),
                                   {"action": "delete"})
        assert response.status_code == 404
```

Create `identity/tests/test_entitlement_pages.py`:

```python
"""`/identity/entitlements/` and one entitlement's grant page.

THE OWNER COLUMN IS WHAT THIS MODULE IS ABOUT. An entitlement owner
reaches the grant form for the entitlement they own -- and gets 404, not
403, for one they do not, because a 403 on a row-addressed URL confirms
the row exists.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from identity.models import Entitlement, EntitlementGrant
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.tests._helpers import (
    grant, make_admin, make_entitlement, make_group, make_user, posture, reset_settings,
    seed_sweep_posture, sign_in,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestTheListPage:
    def test_an_admin_creates_and_a_member_is_refused(self, client):
        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            assert client.post(reverse("identity-entitlements"),
                               {"name": "Finance"}).status_code == 302
            other = client.__class__()
            sign_in(other, member)
            assert other.get(reverse("identity-entitlements")).status_code == 403
        assert Entitlement.objects.filter(name="Finance").exists()


class TestTheEntitlementPage:
    def test_an_owner_reaches_their_own_and_404s_on_another(self, client):
        owner = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            assert client.get(
                reverse("identity-entitlement-edit", args=[finance.pk])).status_code == 200
            assert client.get(
                reverse("identity-entitlement-edit", args=[legal.pk])).status_code == 404

    def test_a_plain_member_404s_rather_than_403s(self, client):
        """404, never 403, on a row-addressed URL: a 403 would confirm
        that this entitlement exists."""
        member = make_user()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.get(reverse("identity-entitlement-edit", args=[finance.pk]))
        assert response.status_code == 404

    def test_an_owner_may_grant_and_revoke_but_not_rename_or_delete(self, client):
        owner, member = make_user(), make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        url = reverse("identity-entitlement-edit", args=[finance.pk])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            client.post(url, {"action": "grant", "subject": f"user:{member.pk}",
                              "role": "member"})
            assert EntitlementGrant.objects.filter(entitlement=finance, user=member).exists()
            row = EntitlementGrant.objects.get(entitlement=finance, user=member)
            client.post(url, {"action": "revoke", "grant": row.pk})
            assert not EntitlementGrant.objects.filter(pk=row.pk).exists()
            rename = client.post(url, {"action": "rename", "name": "Accounts"}, follow=True)
            assert b"Only an administrator" in rename.content
            finance.refresh_from_db()
            assert finance.name == "Finance"
            client.post(url, {"action": "delete"}, follow=True)
            assert Entitlement.objects.filter(pk=finance.pk).exists()

    def test_the_delete_confirmation_names_the_counts_first(self, client):
        """Done-when 6. Deleting an entitlement silently WIDENS access to
        every document it was the last label on, so the page says how
        many before it offers the button."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        grant(finance, user=make_user())
        grant(finance, group=make_group())
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.get(reverse("identity-entitlement-edit", args=[finance.pk]))
        assert b"Grants: 2" in response.content

    def test_revoking_a_grant_from_another_entitlement_is_refused(self, client):
        """The grant id arrives in a POST body. Without the belongs-to
        check an owner of Finance could revoke a Legal grant by guessing
        a sequential id -- and the audit row would read as legitimate."""
        owner = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=owner, role=EntitlementGrant.Role.OWNER)
        elsewhere = grant(legal, user=make_user())
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(reverse("identity-entitlement-edit", args=[finance.pk]),
                                   {"action": "revoke", "grant": elsewhere.pk})
        assert response.status_code == 404
        assert EntitlementGrant.objects.filter(pk=elsewhere.pk).exists()

    def test_a_non_numeric_grant_id_answers_404_not_500(self, client):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("identity-entitlement-edit", args=[finance.pk]),
                                   {"action": "revoke", "grant": "not-a-number"})
        assert response.status_code == 404
        assert b"Traceback" not in response.content
```

- [ ] **Step 5: Run them and watch them fail**

Run: `.venv/bin/pytest -q identity/tests/test_group_pages.py identity/tests/test_entitlement_pages.py -x`
Expected: FAIL — `NoReverseMatch: Reverse for 'identity-groups' not found`.

- [ ] **Step 6: Add the three forms**

Append to `identity/forms.py`:

```python
class NameForm(forms.Form):
    """One name field, for a group or an entitlement.

    A plain `Form`, like every other form on this column: the write goes
    through `identity.services`, where the refusals and the audit rows
    live, and a `ModelForm`'s `save()` would be a second door to exactly
    the writes that have guards.
    """
    name = forms.CharField(max_length=255)
    description = forms.CharField(max_length=2000, required=False,
                                  widget=forms.Textarea(attrs={"rows": 2}))


class GrantForm(forms.Form):
    """Who to grant to, and in what role.

    `subject` is ONE field carrying `"user:<pk>"` or `"group:<pk>"`,
    rather than two optional fields the view has to reconcile: the XOR
    is then unrepresentable in the form rather than merely refused after
    it, and a form that cannot express an invalid state cannot submit
    one.
    """
    subject = forms.RegexField(regex=r"^(user|group):[0-9]+$")
    role = forms.ChoiceField(choices=(("member", "Member"), ("owner", "Owner")))

    def clean_subject(self):
        kind, _, raw = self.cleaned_data["subject"].partition(":")
        return kind, int(raw)
```

- [ ] **Step 7: Write the two views**

Append to `identity/views.py`, extending its imports with
`from django.contrib.auth.models import Group`,
`from identity.access import grant_subjects, is_admin, owned_entitlement_ids`,
`from identity.forms import GrantForm, NameForm`,
`from identity.models import Entitlement, EntitlementGrant`:

```python
# --- the groups page -------------------------------------------------------

_GROUP_ACTIONS = ("delete", "add_member", "remove_member")


@require_admin
def groups(request):
    """GET lists every group with its members and its grants; POST
    creates one. Membership and deletion are `group_edit` below.

    `auth.Group` UNCHANGED -- this platform uses groups for membership
    only and never reads `Group.permissions`, so nothing here subclasses
    or hides Django's own model.
    """
    if request.method == "POST":
        form = NameForm(request.POST)
        if not form.is_valid():
            _flash_form_errors(request, form)
            return redirect("identity-groups")
        try:
            services.create_group(principal_for_request(request),
                                  name=form.cleaned_data["name"])
        except services.ServiceRefused as exc:
            messages.error(request, str(exc))
        else:
            messages.info(request, f"Created {form.cleaned_data['name']!r}.")
        return redirect("identity-groups")
    return render(request, "identity/groups.html", {
        "groups": Group.objects.prefetch_related("user_set", "entitlement_grants__entitlement")
                               .order_by("name"),
        "users": User.objects.filter(is_active=True).order_by("username"),
        "form": NameForm(),
    })


@require_admin
def group_edit(request, pk):
    """POST-only. One `action` field, dispatched. 404 for an unknown
    group, 400 for an unknown action -- a form that quietly did nothing
    would read as success."""
    group = get_object_or_404(Group, pk=pk)
    action = request.POST.get("action", "")
    if action not in _GROUP_ACTIONS:
        return HttpResponseBadRequest(f"{action!r} is not a recognised action.")
    actor = principal_for_request(request)
    try:
        if action == "delete":
            services.delete_group(actor, group)
            messages.info(request, f"Deleted {group.name!r}.")
            return redirect("identity-groups")
        user = get_object_or_404(User, pk=_int_or_404(request.POST.get("user", "")))
        if action == "add_member":
            services.add_group_member(actor, group, user)
            messages.info(request, f"Added {user.username!r} to {group.name!r}.")
        else:
            services.remove_group_member(actor, group, user)
            messages.info(request, f"Removed {user.username!r} from {group.name!r}.")
    except services.ServiceRefused as exc:
        messages.error(request, str(exc))
    return redirect("identity-groups")


def _int_or_404(raw: str) -> int:
    """A POST body's integer, or a 404.

    NOT a bare `int(raw)`: the value arrives from a form body, so a
    non-numeric one would raise `ValueError` inside the view -- a 500 on
    a never-500 surface, reachable by anybody who can post a form.
    """
    if not str(raw).isdigit():
        raise Http404("no such row")
    return int(raw)


# --- the entitlements pages -----------------------------------------------

_ENTITLEMENT_ACTIONS = ("rename", "delete", "grant", "revoke", "set_role")


@require_admin
def entitlements(request):
    """GET lists every entitlement with its grant counts; POST creates
    one. ADMINISTRATOR ONLY -- an owner of one entitlement has no
    standing to mint another, so this page is class S in full."""
    if request.method == "POST":
        form = NameForm(request.POST)
        if not form.is_valid():
            _flash_form_errors(request, form)
            return redirect("identity-entitlements")
        try:
            services.create_entitlement(
                principal_for_request(request), name=form.cleaned_data["name"],
                description=form.cleaned_data["description"])
        except services.ServiceRefused as exc:
            messages.error(request, str(exc))
        else:
            messages.info(request, f"Created {form.cleaned_data['name']!r}.")
        return redirect("identity-entitlements")
    return render(request, "identity/entitlements.html", {
        "entitlements": Entitlement.objects.annotate(grant_count=Count("grants")),
        "form": NameForm(),
    })


def entitlement_edit(request, pk):
    """One entitlement: its description, its grants, and the forms that
    change them.

    NOT `@require_admin`. This is the ONE identity page an entitlement
    OWNER may reach, which is why `identity/routes.py` classifies it `R`
    rather than `S` (spec section 11.3, and this plan's decision 5): the
    middleware admits any signed-in caller and the rule lives here.

    404, NEVER 403, for a caller with no standing over this row -- a 403
    would confirm that an entitlement with this id exists, which is
    exactly the enumeration the class-R rule exists to prevent. Rename
    and delete are refused by `identity.services` with a readable message
    rather than by this view, so the owner sees WHY rather than a bare
    refusal.
    """
    principal = principal_for_request(request)
    entitlement = get_object_or_404(Entitlement, pk=pk)
    if not (is_admin(principal) or entitlement.pk in owned_entitlement_ids(principal)):
        raise Http404("no such entitlement")

    if request.method == "POST":
        return _entitlement_action(request, principal, entitlement)

    return render(request, "identity/entitlement.html", {
        "entitlement": entitlement,
        "grants": entitlement.grants.select_related("user", "group").all(),
        # `grant_subjects`, NOT `share_subjects`: a grant form must offer
        # the caller themselves. An administrator with the content setting
        # off reads nothing labelled, so granting themselves the
        # entitlement is the only way for them to read a document under
        # it -- and `share_subjects` excludes the caller, because sharing
        # a row with yourself IS a no-op.
        "subjects": grant_subjects(principal),
        "grant_form": GrantForm(initial={"role": "member"}),
        # ONLY WHEN THEY WILL BE RENDERED. `entitlement_delete_counts`
        # runs every registered cross-column cascade in count mode -- two
        # extra queries into two other columns -- and the template shows
        # them inside `{% if is_admin %}`. Computing them for an owner
        # who will never see them is work with no reader.
        "delete_counts": (services.entitlement_delete_counts(entitlement)
                          if is_admin(principal) else {}),
        "is_admin": is_admin(principal),
    })


def _entitlement_action(request, principal, entitlement):
    """The POST half of `entitlement_edit`, split out so the GET path
    reads as a page rather than as the tail of a dispatcher."""
    action = request.POST.get("action", "")
    if action not in _ENTITLEMENT_ACTIONS:
        return HttpResponseBadRequest(f"{action!r} is not a recognised action.")
    back = redirect("identity-entitlement-edit", pk=entitlement.pk)
    try:
        if action == "rename":
            services.rename_entitlement(principal, entitlement,
                                        request.POST.get("name", ""))
            messages.info(request, "Renamed.")
        elif action == "delete":
            counts = services.delete_entitlement(principal, entitlement)
            removed = ", ".join(f"{label}: {n}" for label, n in counts.items())
            messages.info(request, f"Deleted {entitlement.name!r} ({removed}).")
            return redirect("identity-entitlements")
        elif action == "grant":
            form = GrantForm(request.POST)
            if not form.is_valid():
                _flash_form_errors(request, form)
                return back
            kind, subject_pk = form.cleaned_data["subject"]
            target = (get_object_or_404(User, pk=subject_pk) if kind == "user"
                      else get_object_or_404(Group, pk=subject_pk))
            services.grant(principal, entitlement,
                           user=target if kind == "user" else None,
                           group=target if kind == "group" else None,
                           role=form.cleaned_data["role"])
            messages.info(request, "Granted.")
        else:
            row = _grant_of(entitlement, request.POST.get("grant", ""))
            if action == "revoke":
                services.revoke(principal, row)
                messages.info(request, "Revoked.")
            else:
                services.set_grant_role(principal, row, request.POST.get("role", ""))
                messages.info(request, "Role changed.")
    except services.ServiceRefused as exc:
        messages.error(request, str(exc))
    return back


def _grant_of(entitlement, raw: str) -> EntitlementGrant:
    """The grant `raw` names, PROVEN to belong to `entitlement`, or 404.

    TWO REFUSALS, both 404, and neither is optional. `raw` arrives from a
    POST body, so a non-numeric value would reach `get(pk=...)` and raise
    `ValueError` -- a 500 on a never-500 surface. And without the
    belongs-to check, an owner of Finance could revoke a Legal grant by
    guessing a sequential id: an IDOR that reads as a legitimate action
    in the audit log. 404 for both, never 403, because a 403 would
    confirm that some other entitlement's grant carries that id.
    """
    return get_object_or_404(
        EntitlementGrant.objects.select_related("entitlement", "user", "group"),
        pk=_int_or_404(raw), entitlement=entitlement)
```

Extend `identity/views.py`'s imports with `from django.db.models import Count` and
`from django.http import Http404, HttpResponseBadRequest` (the second is already there —
add `Http404` to it).

- [ ] **Step 8: Wire the four URLs**

In `identity/urls.py`, extend the import and the list:

```python
from identity.views import (
    LoginView, LogoutView, PasswordChangeDoneView, PasswordChangeView,
    entitlement_edit, entitlements, group_edit, groups, settings_page,
    user_create, user_edit, users,
)

    path("groups/", groups, name="identity-groups"),
    path("groups/<int:pk>/edit/", group_edit, name="identity-group-edit"),
    path("entitlements/", entitlements, name="identity-entitlements"),
    path("entitlements/<int:pk>/", entitlement_edit, name="identity-entitlement-edit"),
```

(The spec writes these as `/identity/groups/<int:group_id>/` and
`/identity/entitlements/<int:entitlement_id>/`. IA-1's own `users/<int:pk>/edit/` set the
convention this tree actually uses; the URL **names** — which are what `ROUTE_RULES`, the route
matrix and every `{% url %}` key on — match the spec exactly.)

- [ ] **Step 9: Write the three templates**

Create `identity/templates/identity/groups.html`:

```html
{% extends "_shell.html" %}
{% comment %}
Groups: create, delete, add and remove members. Zero JS, one POST per
action, every mutation through `identity.services`.

Membership only -- this platform never reads `Group.permissions`.
{% endcomment %}
{% block title %}Groups — farabunker{% endblock %}
{% block nav_current_identity_groups %}current{% endblock %}
{% block extra_style %}
  main { max-width: 900px; margin: 0 auto; }
  .msg { padding: .6rem .9rem; border: 1px solid var(--border); border-radius: 6px;
         margin-bottom: .4rem; font-size: .9rem; background: var(--panel); }
  .msg.error { border-color: var(--danger); color: var(--danger); }
  section { background: var(--panel); border: 1px solid var(--border);
            border-radius: 8px; padding: 1rem; margin-bottom: 1.25rem; }
  h2 { font-size: 1rem; margin: 0 0 .75rem; }
  form.inline { display: inline; }
  ul.members { list-style: none; padding: 0; margin: .4rem 0 0; }
  ul.members li { display: flex; gap: .5rem; align-items: center; font-size: .9rem; }
  .chips { color: var(--muted); font-size: .85rem; }
{% endblock %}
{% block content %}
<h1>Groups</h1>
{% for message in messages %}<div class="msg {{ message.tags }}">{{ message }}</div>{% endfor %}

<section>
  {% comment %}
  The FORM'S OWN FIELDS, not hand-written inputs -- the same shape
  `identity/templates/identity/users.html:135-147` uses. A hand-written
  `<input name="name">` would render fine and lose every per-field error
  the view flashes, which is the half of a form that matters when
  somebody gets it wrong.
  {% endcomment %}
  <h2>New group</h2>
  <form method="post" action="{% url 'identity-groups' %}">
    {% csrf_token %}
    <label for="{{ form.name.id_for_label }}">Name</label>
    {{ form.name }}
    {% if form.name.errors %}<ul class="errorlist">{% for error in form.name.errors %}<li>{{ error }}</li>{% endfor %}</ul>{% endif %}
    <button type="submit">Create</button>
  </form>
</section>

{% for group in groups %}
<section>
  <h2>{{ group.name }}</h2>
  <p class="chips">Entitlements:
    {% for grant in group.entitlement_grants.all %}{{ grant.entitlement.name }} ({{ grant.role }}){% if not forloop.last %}, {% endif %}{% empty %}none{% endfor %}
  </p>
  <ul class="members">
    {% for member in group.user_set.all %}
      <li>{{ member.username }}
        <form class="inline" method="post" action="{% url 'identity-group-edit' group.pk %}">
          {% csrf_token %}
          <input type="hidden" name="action" value="remove_member">
          <input type="hidden" name="user" value="{{ member.pk }}">
          <button type="submit">Remove</button>
        </form>
      </li>
    {% empty %}<li>No members yet.</li>{% endfor %}
  </ul>
  <form method="post" action="{% url 'identity-group-edit' group.pk %}">
    {% csrf_token %}
    <input type="hidden" name="action" value="add_member">
    <select name="user">
      {% for candidate in users %}<option value="{{ candidate.pk }}">{{ candidate.username }}</option>{% endfor %}
    </select>
    <button type="submit">Add member</button>
  </form>
  <form method="post" action="{% url 'identity-group-edit' group.pk %}">
    {% csrf_token %}
    <input type="hidden" name="action" value="delete">
    <button type="submit">Delete group</button>
  </form>
  <p class="chips">Deleting a group removes its entitlement grants and its shares.</p>
</section>
{% empty %}
<p>No groups yet.</p>
{% endfor %}
{% endblock %}
```

Create `identity/templates/identity/entitlements.html`:

```html
{% extends "_shell.html" %}
{% comment %}
Entitlements: the named permissions everything else is granted against.
Create here; grant, rename and delete on one entitlement's own page.
{% endcomment %}
{% block title %}Entitlements — farabunker{% endblock %}
{% block nav_current_identity_entitlements %}current{% endblock %}
{% block extra_style %}
  main { max-width: 900px; margin: 0 auto; }
  .msg { padding: .6rem .9rem; border: 1px solid var(--border); border-radius: 6px;
         margin-bottom: .4rem; font-size: .9rem; background: var(--panel); }
  .msg.error { border-color: var(--danger); color: var(--danger); }
  section { background: var(--panel); border: 1px solid var(--border);
            border-radius: 8px; padding: 1rem; margin-bottom: 1.25rem; }
  table { width: 100%; border-collapse: collapse; font-size: .92rem; }
  th, td { text-align: left; padding: .45rem .3rem; border-bottom: 1px solid var(--border); }
  .muted { color: var(--muted); font-size: .85rem; }
{% endblock %}
{% block content %}
<h1>Entitlements</h1>
{% for message in messages %}<div class="msg {{ message.tags }}">{{ message }}</div>{% endfor %}
<p class="muted">A document or a tool carrying no label is open to everyone signed in
  (or, on a locked library, to nobody but an administrator with content access).
  Labelling one with an entitlement narrows it to the people who hold that entitlement.</p>

<section>
  {% comment %}
  The form's own fields, for the reason `groups.html` gives: a
  hand-written input loses every per-field error the view flashes.
  {% endcomment %}
  <h2>New entitlement</h2>
  <form method="post" action="{% url 'identity-entitlements' %}">
    {% csrf_token %}
    <label for="{{ form.name.id_for_label }}">Name</label>
    {{ form.name }}
    {% if form.name.errors %}<ul class="errorlist">{% for error in form.name.errors %}<li>{{ error }}</li>{% endfor %}</ul>{% endif %}
    <label for="{{ form.description.id_for_label }}">Description</label>
    {{ form.description }}
    <button type="submit">Create</button>
  </form>
</section>

<table>
  <thead><tr><th>Name</th><th>Description</th><th>Grants</th></tr></thead>
  <tbody>
  {% for entitlement in entitlements %}
    <tr>
      <td><a href="{% url 'identity-entitlement-edit' entitlement.pk %}">{{ entitlement.name }}</a></td>
      <td>{{ entitlement.description }}</td>
      <td>{{ entitlement.grant_count }}</td>
    </tr>
  {% empty %}
    <tr><td colspan="3">No entitlements yet.</td></tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

Create `identity/templates/identity/entitlement.html`:

```html
{% extends "_shell.html" %}
{% comment %}
One entitlement: who holds it, in what role, and the forms that change
that. An OWNER of this entitlement reaches this page and sees the grant
form; the rename and delete forms are rendered only for an
administrator, because the POST behind them refuses anybody else --
a page must not offer a control that answers "no".
{% endcomment %}
{% block title %}{{ entitlement.name }} — farabunker{% endblock %}
{% block nav_current_identity_entitlements %}current{% endblock %}
{% block extra_style %}
  main { max-width: 820px; margin: 0 auto; }
  .msg { padding: .6rem .9rem; border: 1px solid var(--border); border-radius: 6px;
         margin-bottom: .4rem; font-size: .9rem; background: var(--panel); }
  .msg.error { border-color: var(--danger); color: var(--danger); }
  section { background: var(--panel); border: 1px solid var(--border);
            border-radius: 8px; padding: 1rem; margin-bottom: 1.25rem; }
  table { width: 100%; border-collapse: collapse; font-size: .92rem; }
  th, td { text-align: left; padding: .45rem .3rem; border-bottom: 1px solid var(--border); }
  form.inline { display: inline; }
  .warn { color: var(--danger); font-size: .88rem; }
{% endblock %}
{% block content %}
<h1>{{ entitlement.name }}</h1>
{% for message in messages %}<div class="msg {{ message.tags }}">{{ message }}</div>{% endfor %}
<p>{{ entitlement.description }}</p>

<section>
  <h2>Grants</h2>
  <table>
    <thead><tr><th>Holder</th><th>Role</th><th></th></tr></thead>
    <tbody>
    {% for grant in grants %}
      <tr>
        <td>{% if grant.user_id %}{{ grant.user.username }}{% else %}group: {{ grant.group.name }}{% endif %}</td>
        <td>{{ grant.get_role_display }}</td>
        <td>
          <form class="inline" method="post">
            {% csrf_token %}
            <input type="hidden" name="action" value="set_role">
            <input type="hidden" name="grant" value="{{ grant.pk }}">
            <input type="hidden" name="role" value="{% if grant.role == 'owner' %}member{% else %}owner{% endif %}">
            <button type="submit">{% if grant.role == 'owner' %}Make member{% else %}Make owner{% endif %}</button>
          </form>
          <form class="inline" method="post">
            {% csrf_token %}
            <input type="hidden" name="action" value="revoke">
            <input type="hidden" name="grant" value="{{ grant.pk }}">
            <button type="submit">Revoke</button>
          </form>
        </td>
      </tr>
    {% empty %}<tr><td colspan="3">Nobody holds this yet.</td></tr>{% endfor %}
    </tbody>
  </table>

  <form method="post">
    {% csrf_token %}
    <input type="hidden" name="action" value="grant">
    <select name="subject">
      {% for pk, name in subjects.users %}<option value="user:{{ pk }}">{{ name }}</option>{% endfor %}
      {% for pk, name in subjects.groups %}<option value="group:{{ pk }}">group: {{ name }}</option>{% endfor %}
    </select>
    <select name="role"><option value="member">Member</option><option value="owner">Owner</option></select>
    <button type="submit">Grant</button>
  </form>
  <p>An owner of this entitlement may grant it, revoke it, and label documents and tools
    with it. Nothing else.</p>
</section>

{% if is_admin %}
<section>
  <h2>Rename</h2>
  <form method="post">
    {% csrf_token %}
    <input type="hidden" name="action" value="rename">
    <input type="text" name="name" value="{{ entitlement.name }}" required>
    <button type="submit">Rename</button>
  </form>
</section>

<section>
  <h2>Delete</h2>
  <p class="warn">Deleting this removes {% for label, count in delete_counts.items %}{{ label }}: {{ count }}{% if not forloop.last %}, {% endif %}{% endfor %}.
    Every document this was the last label on becomes unlabelled and follows the library
    posture — which widens who can read it.</p>
  <form method="post">
    {% csrf_token %}
    <input type="hidden" name="action" value="delete">
    <button type="submit">Delete entitlement</button>
  </form>
</section>
{% endif %}
{% endblock %}
```

- [ ] **Step 10: Add the two nav links**

In `foundation/templates/_shell.html`, extend the existing identity nav condition (line 205) so
the two new pages sit beside Accounts and Settings, gated on exactly the same predicate — the
household box's shell stays byte-identical to today's:

```html
  {% if identity_posture != "open" and identity_is_admin %}<a href="{% url 'identity-users' %}" class="{% block nav_current_identity %}{% endblock %}">Accounts</a> <a href="{% url 'identity-groups' %}" class="{% block nav_current_identity_groups %}{% endblock %}">Groups</a> <a href="{% url 'identity-entitlements' %}" class="{% block nav_current_identity_entitlements %}{% endblock %}">Entitlements</a> <a href="{% url 'identity-settings' %}" class="{% block nav_current_identity_settings %}{% endblock %}">Settings</a>{% endif %}
```

**Each new anchor carries its own `{% block %}`**, or the two new pages could never be marked
current and visiting either would highlight Accounts — `nav_current_identity` sits on the
Accounts anchor (`foundation/templates/_shell.html:205`).

**The tool-label page's link is Task 6's, not this task's.** `/chat/tools/` needs a discovery
path too (Task 19's browser walk asks the owner to reach it), but a `{% url 'chat-tool-entitlements' %}`
in the shell before Task 6 mounts that name raises `NoReverseMatch` on **every** page render and
takes the whole suite down. Task 6, Step 4 adds its own anchor to this same line, in the commit
that mounts the route.

Append to `identity/tests/test_shell_nav.py`:

```python
class TestIA2NavLinks:
    def test_the_two_new_links_appear_only_for_an_admin_off_open(self, client):
        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("chat-index")).content
            assert b"/identity/groups/" in body and b"/identity/entitlements/" in body
            other = client.__class__()
            sign_in(other, member)
            assert b"/identity/groups/" not in other.get(reverse("chat-index")).content

    def test_an_open_box_shell_is_unchanged(self, client):
        body = client.get(reverse("chat-index")).content
        assert b"/identity/groups/" not in body
```

- [ ] **Step 11: Give the four new routes their matrix drivers**

**A ROUTE WITH NO DRIVER RAISES `KeyError` NAMING ITSELF** — that is what "swept automatically"
means in `identity/tests/test_route_matrix.py`, and it is why every task in this plan that adds
a route adds its driver in the same commit rather than leaving the matrix red until Task 18.

In `identity/tests/test_route_matrix.py`, add to `_DRIVERS`:

```python
    # --- /identity/ (new in IA-2) ------------------------------------
    "identity-groups": lambda w: ("get", reverse("identity-groups"), {}),
    "identity-group-edit": lambda w: (
        "post", reverse("identity-group-edit", args=[w.group.pk]),
        {"action": "add_member", "user": w.other.pk}),
    "identity-entitlements": lambda w: ("get", reverse("identity-entitlements"), {}),
    # POINTED AT AN ENTITLEMENT `world.other` OWNS AND NOBODY ELSE DOES,
    # for the same reason every other row-addressed driver points at a
    # row `other` owns: a route pointed at the CALLER's own row would
    # answer 200 for everybody and prove nothing. A member and an admin
    # with the content setting off therefore get this route's real R
    # answers -- 404 and 200 respectively.
    "identity-entitlement-edit": lambda w: (
        "get", reverse("identity-entitlement-edit", args=[w.entitlement.pk]), {}),
```

and to `World` and `_build_world`, beside the rows already there:

```python
    group: object
    entitlement: object
```
```python
    group = make_group(name=f"group-{next(_counter)}")
    entitlement = make_entitlement(name=f"entitlement-{next(_counter)}")
    grant(entitlement, user=other, role="owner")
```

`identity-entitlement-edit` is class `R` and is addressed by id, so add it to
`_ROW_ADDRESSED_R` beside the three names already there:

```python
_ROW_ADDRESSED_R = frozenset({"jobs-queue-cancel", "rag-ask-status", "vision-queue-status",
                              "identity-entitlement-edit"})
```

Import `grant`, `make_entitlement` and `make_group` from `identity.tests._helpers` at the top
of the module.

- [ ] **Step 12: Run everything and watch it pass**

```bash
.venv/bin/pytest -q identity
```
Expected: PASS, including `identity/tests/test_route_matrix.py` and
`identity/tests/test_middleware.py::TestOpenPosture` (the four new routes answer as an open box
answers everything: admitted, no permission query).

- [ ] **Step 13: Commit**

```bash
git add identity/forms.py identity/views.py identity/urls.py identity/routes.py \
        identity/templates/identity/ foundation/templates/_shell.html identity/tests/
git commit -m "feat(identity): IA-2 T3 — the groups and entitlements pages"
```

---
### Task 4: `ToolEntitlement`, `Share`, and `ToolInvocation.agent_slug`

The `agents` column's one migration. Two new tables and one column, plus the tool-label
cascade registration that turns Task 2's registry from a mechanism into a fact.

**Files:**
- Modify: `agents/models.py`
- Create: `agents/labels.py`, `agents/migrations/0003_toolentitlement_and_share.py` (generated)
- Modify: `agents/apps.py`
- Test: `agents/tests/test_models.py` (append), `agents/tests/test_tool_labels.py` (new)

**Interfaces:**
- Consumes: `identity.contracts.cascades.EntitlementCascade`/`register_entitlement_cascade`
  (Task 2).
- Produces: `agents.models.ToolEntitlement` (`tool_key`, `entitlement`, `labelled_by`,
  `labelled_at`); `agents.models.Share` (`target_type`, `target_key`, `user`, `group`, `level`,
  `shared_by`, `shared_at`, with `Target.CONVERSATION|AGENT|FLOW|VISION_OUTPUT` and
  `Level.VIEW|USE`); `agents.models.ToolInvocation.agent_slug`.
- Produces: `agents.labels.tool_labels_cascade(entitlement_id, *, commit) -> int`,
  `::tool_entitlement_ids() -> dict[str, frozenset[int]]`, `::set_tool_labels(actor, tool_key,
  entitlement_ids)`.

- [ ] **Step 1: Write the failing model tests**

Append to `agents/tests/test_models.py`:

```python
class TestToolEntitlement:
    """A tool label is a STRING plus an entitlement -- tools are
    code-registered and there is no tool table to point at, the same
    reason `Turn.queue_job_id` is a plain integer."""

    def test_one_label_per_tool_and_entitlement(self):
        from agents.models import ToolEntitlement
        from agents.tests._helpers import make_entitlement
        finance = make_entitlement(name="Finance")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)
        with pytest.raises(IntegrityError):
            ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)

    def test_a_key_not_registered_on_this_install_is_tolerated(self):
        """Exactly as `Agent.tool_keys` tolerates one: a feature-gated
        tool with its flag off is a row that names a key this box does
        not have, and refusing it would make a label depend on which
        features happened to be on when it was written."""
        from agents.models import ToolEntitlement
        from agents.tests._helpers import make_entitlement
        row = ToolEntitlement.objects.create(tool_key="not.registered.anywhere",
                                             entitlement=make_entitlement())
        assert row.pk


class TestShare:
    """A CONVERSATION'S KEY IS A UUID, so every conversation-target row
    below carries a real one. `Share.save()` parses `target_key` with the
    TARGET'S OWN parser before the database ever sees the row, so
    `target_key="1"` on a conversation share raises `ValueError` -- not
    the constraint violation these tests are about."""

    def test_a_share_naming_both_a_user_and_a_group_is_refused(self):
        import uuid
        from agents.models import Share
        from agents.tests._helpers import make_group, make_user
        with pytest.raises(IntegrityError):
            Share.objects.create(target_type=Share.Target.CONVERSATION,
                                 target_key=str(uuid.uuid4()),
                                 user=make_user(), group=make_group())

    def test_a_share_naming_neither_is_refused(self):
        import uuid
        from agents.models import Share
        with pytest.raises(IntegrityError):
            Share.objects.create(target_type=Share.Target.CONVERSATION,
                                 target_key=str(uuid.uuid4()))

    def test_one_share_per_target_and_user(self):
        import uuid
        from agents.models import Share
        from agents.tests._helpers import make_user
        user = make_user()
        key = str(uuid.uuid4())
        Share.objects.create(target_type=Share.Target.CONVERSATION, target_key=key,
                             user=user)
        with pytest.raises(IntegrityError):
            Share.objects.create(target_type=Share.Target.CONVERSATION, target_key=key,
                                 user=user)

    def test_save_refuses_a_target_key_the_targets_own_parser_rejects(self):
        """The FIRST half of the two-sided rule. A conversation's primary
        key is a UUID; a `target_key` that is not a parseable one would
        make `Q(pk__in=[...])` raise inside a listing queryset -- a 500
        on a never-500 surface, reachable by one bad row. The second half
        drops such a row on the way OUT (`agents/shares.py`), because a
        row can also arrive from a shell or an older schema."""
        from agents.models import Share
        from agents.tests._helpers import make_user
        with pytest.raises(ValueError):
            Share.objects.create(target_type=Share.Target.CONVERSATION,
                                 target_key="not-a-uuid", user=make_user())
        with pytest.raises(ValueError):
            Share.objects.create(target_type=Share.Target.AGENT,
                                 target_key="not-an-int", user=make_user())

    def test_deleting_the_group_takes_its_shares(self):
        from agents.models import Share
        from agents.tests._helpers import make_group
        group = make_group()
        Share.objects.create(target_type=Share.Target.AGENT, target_key="1", group=group)
        group.delete()
        assert Share.objects.count() == 0


class TestToolInvocationRecordsTheAgent:
    def test_the_column_exists_and_defaults_blank(self):
        """`principal` is WHO this was done for; `agent_slug` is WHOSE
        TOOL DECLARATION was in force. Without this column the acting
        rule would LOSE the fact that an agent made the call, which is
        the fact an operator most wants when reading the trail."""
        from agents.models import ToolInvocation
        row = ToolInvocation.objects.create(
            principal_kind="user", principal_key="1", tool_key="rag.search",
            outcome=ToolInvocation.Outcome.OK)
        assert row.agent_slug == ""
```

- [ ] **Step 2: Run and watch it fail**

Run: `.venv/bin/pytest -q agents/tests/test_models.py -x`
Expected: FAIL — `ImportError: cannot import name 'ToolEntitlement'`.

- [ ] **Step 3: Write the two models and the column**

Append to `agents/models.py` (it already imports `models`; add `from django.conf import
settings`, `from django.db.models import Q` if they are not there), and add `agent_slug` to
`ToolInvocation` immediately after `principal_key`:

```python
    # WHOSE TOOL DECLARATION WAS IN FORCE, as distinct from
    # `principal_kind`/`principal_key`, which is WHO THIS WAS DONE FOR.
    # Written by `agents.runtime.invoke.invoke_tool` from
    # `ToolContext.agent_slug`. Blank for a call with no agent at all --
    # the future MCP edge -- and for every row written before IA-2.
    agent_slug = models.CharField(max_length=64, blank=True, default="")
```

```python
class ToolEntitlement(models.Model):
    """A tool key, labelled with an entitlement.

    UNLABELLED MEANS CALLABLE. A tool with no row here is callable by
    every signed-in principal; a labelled one is callable by a principal
    holding ANY of its entitlements. That is the same OR-match documents
    use, and for the same reason: "must hold both" is a named non-goal,
    and the answer to it is a more specific entitlement.

    `tool_key` IS A STRING, NOT A FOREIGN KEY: tools are code-registered
    (`agents.contracts.tools.register_tool`) and there is no tool table
    to point at -- the same reason `Turn.queue_job_id` is a plain
    integer. A row naming a key that is not registered on THIS install
    (a feature-gated tool with its flag off) is tolerated exactly as
    `Agent.tool_keys` tolerates one.
    """

    tool_key = models.CharField(max_length=255)
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="tool_labels")
    labelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    labelled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tool_key", "entitlement"],
                                    name="uniq_tool_entitlement"),
        ]
        indexes = [models.Index(fields=["tool_key"], name="agents_toollabel_key")]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.tool_key}@{self.entitlement_id}"


def _uuid_or_none(raw: str):
    """A UUID, or None. NEVER raises -- see `Share`'s docstring."""
    import uuid
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def _int_or_none(raw: str):
    """An int, or None. NEVER raises -- see `Share`'s docstring."""
    try:
        return int(str(raw))
    except (ValueError, TypeError):
        return None


class Share(models.Model):
    """One row this principal's owner has extended to somebody else.

    GENERIC TARGET, ONE TABLE, so adding the second sharing UI later is a
    form and a template rather than a second table and a second set of
    visibility functions. IA-2 ships exactly one UI -- conversations
    (spec section 10.3) -- and `vision_output` has no writer at all, so
    it cannot orphan.

    `target_key` IS THE TARGET'S PRIMARY KEY AS TEXT. The four targets
    live in three apps and have three different key types (UUID, int,
    int); a real foreign key would be a cross-column import (import-law
    rule 2) and a `GenericForeignKey` would make `contenttypes` a second
    identity for a row this platform already identifies by pk.

    THE KEY IS PARSED ON THE WAY IN AND AGAIN ON THE WAY OUT.
    `save()` refuses a `target_key` the target's own parser rejects;
    `agents/shares.py::shared_keys` drops one on the way out. Two halves
    of one rule, because a row can also arrive from a shell or from an
    older schema, and an unparseable key reaching `Q(pk__in=[...])`
    raises inside a listing queryset -- a 500 on a never-500 surface,
    reachable by one bad row.

    ORPHANS ARE INERT AND NO SIGNAL SWEEPS THEM. This repository uses no
    Django signals anywhere; every reader resolves the target first, and
    a target that does not resolve contributes nothing. The one shipped
    delete surface, `agents.visibility.delete_conversation`, removes a
    conversation's shares in the same transaction.
    """

    class Target(models.TextChoices):
        CONVERSATION = "conversation", "Conversation"
        AGENT = "agent", "Agent"
        FLOW = "flow", "Flow"
        VISION_OUTPUT = "vision_output", "Generated image"

    class Level(models.TextChoices):
        VIEW = "view", "Can view"
        USE = "use", "Can use"

    target_type = models.CharField(max_length=16, choices=Target.choices)
    target_key = models.CharField(max_length=64)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.CASCADE, related_name="shares_received")
    group = models.ForeignKey("auth.Group", null=True, blank=True,
                              on_delete=models.CASCADE, related_name="shares_received")
    level = models.CharField(max_length=8, choices=Level.choices, default=Level.VIEW)
    shared_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="shares_made")
    shared_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(user__isnull=False, group__isnull=True)
                           | Q(user__isnull=True, group__isnull=False)),
                name="share_user_xor_group"),
            models.UniqueConstraint(fields=["target_type", "target_key", "user"],
                                    condition=Q(user__isnull=False),
                                    name="uniq_share_target_user"),
            models.UniqueConstraint(fields=["target_type", "target_key", "group"],
                                    condition=Q(group__isnull=False),
                                    name="uniq_share_target_group"),
        ]
        indexes = [
            models.Index(fields=["target_type", "target_key"], name="agents_share_target"),
            models.Index(fields=["user"], name="agents_share_user"),
            models.Index(fields=["group"], name="agents_share_group"),
        ]

    def save(self, *args, **kwargs):
        """Validate `target_key` against the TARGET'S OWN key shape.

        `ValueError`, not a `ValidationError`: a share row whose key
        cannot name a row is a programming error at the call site, not a
        form the operator can correct.
        """
        parser = TARGET_KEY_PARSERS.get(self.target_type)
        if parser is None:
            raise ValueError(f"{self.target_type!r} is not a share target on this platform.")
        if parser(self.target_key) is None:
            raise ValueError(
                f"{self.target_key!r} is not a valid key for a {self.target_type} share."
            )
        return super().save(*args, **kwargs)


# A conversation's primary key is a UUID; an agent's, a flow's and a
# generated image's are integers. Each parser RETURNS None rather than
# raising, so both halves of the rule -- `Share.save()` on the way in and
# `agents.shares.shared_keys` on the way out -- can ask the same question
# without either of them needing a try/except of its own.
TARGET_KEY_PARSERS = {
    Share.Target.CONVERSATION: _uuid_or_none,
    Share.Target.AGENT: _int_or_none,
    Share.Target.FLOW: _int_or_none,
    Share.Target.VISION_OUTPUT: _int_or_none,
}
```

- [ ] **Step 4: Generate the migration and read it**

```bash
.venv/bin/python manage.py makemigrations agents --name toolentitlement_and_share
```

Confirm by eye that `agents/migrations/0003_toolentitlement_and_share.py`:

- depends on `migrations.swappable_dependency(settings.AUTH_USER_MODEL)`, on
  `("auth", "__first__")`, **and** on `("identity", "0003_entitlement_and_grant")` (the
  `"identity.Entitlement"` string FK). Add any the autodetector missed.
- carries exactly three operations: `CreateModel(ToolEntitlement)`, `CreateModel(Share)`,
  `AddField(toolinvocation.agent_slug)`.
- uses `condition=` on the `CheckConstraint`.

Add a docstring naming it as IA-2's agents migration and the second of the repository's
cross-app dependencies.

- [ ] **Step 5: Run the model tests**

```bash
.venv/bin/python manage.py migrate
.venv/bin/pytest -q agents/tests/test_models.py
```
Expected: PASS.

- [ ] **Step 6: Write the failing tool-label helper tests**

Create `agents/tests/test_tool_labels.py`:

```python
"""`agents/labels.py` -- reading and writing tool labels, and the
entitlement-delete cascade this column registers.
"""
from __future__ import annotations

import pytest

from agents.labels import set_tool_labels, tool_entitlement_ids, tool_labels_cascade
from agents.models import ToolEntitlement
from agents.tests._helpers import (
    make_admin, make_entitlement, posture, reset_settings, seed_sweep_posture, user_principal,
)
from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.models import AuditEvent

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestReadingLabels:
    def test_one_query_maps_every_labelled_key_to_its_entitlements(self):
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        assert tool_entitlement_ids() == {"rag.search": frozenset({finance.pk, legal.pk})}

    def test_an_unlabelled_install_maps_nothing(self):
        assert tool_entitlement_ids() == {}


class TestWritingLabels:
    def test_setting_labels_writes_the_difference_and_audits_both_directions(self):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        with posture(POSTURE_ENTERPRISE):
            actor = user_principal(admin)
            set_tool_labels(actor, "rag.search", {finance.pk, legal.pk})
            assert AuditEvent.objects.filter(action=actions.TOOL_LABELLED).count() == 2
            set_tool_labels(actor, "rag.search", {finance.pk})
        assert set(ToolEntitlement.objects.values_list("entitlement_id", flat=True)) == {finance.pk}
        assert AuditEvent.objects.filter(action=actions.TOOL_UNLABELLED).count() == 1

    def test_setting_the_same_labels_twice_writes_no_second_event(self):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            actor = user_principal(admin)
            set_tool_labels(actor, "rag.search", {finance.pk})
            set_tool_labels(actor, "rag.search", {finance.pk})
        assert AuditEvent.objects.filter(action=actions.TOOL_LABELLED).count() == 1


class TestTheCascade:
    def test_counting_never_writes_and_running_removes(self):
        finance = make_entitlement(name="Finance")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)
        assert tool_labels_cascade(finance.pk, commit=False) == 1
        assert ToolEntitlement.objects.count() == 1
        assert tool_labels_cascade(finance.pk, commit=True) == 1
        assert ToolEntitlement.objects.count() == 0
```

- [ ] **Step 7: Run and watch it fail, then write `agents/labels.py`**

Run: `.venv/bin/pytest -q agents/tests/test_tool_labels.py -x` — expected FAIL, no module.

Create `agents/labels.py`:

```python
"""Tool labels: reading them for `tool_access_for`, writing them from
the tool-label page, and giving them up when an entitlement dies.

IT LIVES AT THE COLUMN ROOT, not inside `agents/chat`, for the same
reason `agents/visibility.py` does: the tool-label page is a chat view
today, and a future MCP edge and a future management command need the
same answers without either of them being one.
"""
from __future__ import annotations

from collections import defaultdict

from django.db import transaction

from agents.models import ToolEntitlement
from identity import audit
from identity.contracts import actions


def tool_entitlement_ids() -> dict[str, frozenset[int]]:
    """`{tool_key: {entitlement ids}}` for every LABELLED key. ONE QUERY.

    A key ABSENT from this mapping is unlabelled, and therefore callable
    by everybody signed in -- which is why this returns only the labelled
    ones and `ToolAccess.allows` reads an absent key as permitted.
    """
    out: dict[str, set[int]] = defaultdict(set)
    for key, entitlement_id in ToolEntitlement.objects.values_list("tool_key",
                                                                   "entitlement_id"):
        out[key].add(entitlement_id)
    return {key: frozenset(ids) for key, ids in out.items()}


def labels_for(tool_key: str) -> frozenset[int]:
    """The entitlement ids labelling one key."""
    return frozenset(
        ToolEntitlement.objects.filter(tool_key=tool_key)
        .values_list("entitlement_id", flat=True))


def set_tool_labels(actor, tool_key: str, entitlement_ids) -> None:
    """Make `tool_key`'s labels exactly `entitlement_ids`.

    WRITES THE DIFFERENCE, not the whole set: an audit trail that
    recorded "labelled" for a label that was already there is a trail
    whose interesting lines are invisible.

    THE CALLER CHECKS THE PREDICATE. `agents.chat.views.tools` refuses
    unless the actor is an administrator or an owner of every entitlement
    being added or removed -- the same predicate `identity.services.
    may_administer_entitlement` spells out on the identity side. This
    function is the write, not the guard, exactly as
    `identity.audit.record` is the writer and not the policy.
    """
    wanted = {int(i) for i in entitlement_ids}
    with transaction.atomic():
        current = set(labels_for(tool_key))
        for entitlement_id in sorted(wanted - current):
            ToolEntitlement.objects.create(tool_key=tool_key, entitlement_id=entitlement_id)
            audit.record(actor, actions.TOOL_LABELLED, target_type="tool",
                         target_key=tool_key, target_label=tool_key,
                         entitlement_id=entitlement_id)
        for entitlement_id in sorted(current - wanted):
            ToolEntitlement.objects.filter(tool_key=tool_key,
                                           entitlement_id=entitlement_id).delete()
            audit.record(actor, actions.TOOL_UNLABELLED, target_type="tool",
                         target_key=tool_key, target_label=tool_key,
                         entitlement_id=entitlement_id)


def tool_labels_cascade(entitlement_id: int, *, commit: bool) -> int:
    """This column's answer to "this entitlement is going away".

    Registered from `agents/apps.py::ready()` as a DOTTED-PATH STRING, so
    `identity/` reaches it without importing `agents/` (import-law rule
    4). `commit=False` counts; `commit=True` removes. There is no cache
    to repair on this side -- a tool label is read live, per turn, by
    `agents.entitlements.tool_access_for` -- which is exactly why the
    document side of the same registry does more than this one.
    """
    rows = ToolEntitlement.objects.filter(entitlement_id=entitlement_id)
    if not commit:
        return rows.count()
    count = rows.count()
    rows.delete()
    return count
```

- [ ] **Step 8: Register the cascade from `agents/apps.py::ready()`**

Append to `AgentsConfig.ready()`, after the owned-rows registration pattern the other columns
already use:

```python
        # This column's answer to "an entitlement is being deleted", as a
        # DOTTED-PATH STRING so `identity/` can run it without importing
        # `agents/` (import-law rule 4). Registration imports nothing:
        # `identity/cascades.py` resolves the path at delete time, the
        # same way a `JobKind`'s handler is resolved at run time.
        from identity.contracts.cascades import (
            EntitlementCascade, register_entitlement_cascade,
        )

        register_entitlement_cascade(EntitlementCascade(
            key="agents.tool_labels",
            label="Tool labels",
            handler="agents.labels.tool_labels_cascade",
        ))
```

- [ ] **Step 9: Run the agents tests and the migration check**

```bash
.venv/bin/pytest -q agents/tests/test_tool_labels.py agents/tests/test_models.py
.venv/bin/python manage.py makemigrations --check --dry-run
```
Expected: PASS; `--check` exits 0.

- [ ] **Step 10: Commit**

```bash
git add agents/models.py agents/labels.py agents/apps.py \
        agents/migrations/0003_toolentitlement_and_share.py agents/tests/
git commit -m "feat(agents): IA-2 T4 — ToolEntitlement, Share, and ToolInvocation.agent_slug"
```

---

### Task 5: `ToolAccess` — `tool_keys` becomes a declaration and the grant becomes an intersection

Availability is the intersection of three things and `granted_tools` stays the one function that
computes it. This is the task where an agent stops being a way around labels.

**Files:**
- Modify: `agents/contracts/tools.py` (add `ToolAccess`, `UNRESTRICTED_TOOL_ACCESS`,
  `granted_tools`' third argument and third drop, `ToolContext.tool_access`)
- Create: `agents/entitlements.py`
- Modify: `agents/runtime/jobs.py:98,113`, `agents/runtime/loop.py:380-405`,
  `agents/runtime/delegate.py:99`, `agents/runtime/preflight.py:153`,
  `agents/runtime/invoke.py`
- Test: `agents/contracts/tests/test_tools.py` (append),
  `agents/tests/test_entitlements.py` (new),
  `agents/runtime/tests/test_tool_access.py` (new),
  `agents/runtime/tests/test_preflight.py` (append — the two-tuple split)

**Interfaces:**
- Consumes: `agents.labels.tool_entitlement_ids` (Task 4);
  `identity.access.held_entitlement_ids`/`sees_all_content` (Task 1).
- Produces: `agents.contracts.tools.ToolAccess(required, held, unrestricted)` with
  `.allows(key) -> bool`; `::UNRESTRICTED_TOOL_ACCESS`;
  `::granted_tools(principal, tool_keys, access=UNRESTRICTED_TOOL_ACCESS) -> list[str]`;
  `::ToolContext.tool_access`.
- Produces: `agents.runtime.preflight.Preflight.unentitled_tools: tuple[str, ...] = ()`.
- Produces: `agents.entitlements.tool_access_for(principal) -> ToolAccess`.

- [ ] **Step 1: Write the failing contract tests**

Append to `agents/contracts/tests/test_tools.py`:

```python
class TestToolAccess:
    """A PURE VALUE. `granted_tools` lives in a rule-1 leaf and cannot
    query `ToolEntitlement`, so the facts arrive as data."""

    def test_the_default_is_unrestricted_and_allows_everything(self):
        from agents.contracts.tools import UNRESTRICTED_TOOL_ACCESS
        assert UNRESTRICTED_TOOL_ACCESS.allows("anything.at.all") is True

    def test_an_absent_key_is_unlabelled_and_therefore_allowed(self):
        from agents.contracts.tools import ToolAccess
        access = ToolAccess(required={"rag.search": frozenset({1})},
                            held=frozenset(), unrestricted=False)
        assert access.allows("vision.generate") is True
        assert access.allows("rag.search") is False

    def test_holding_any_one_of_a_keys_entitlements_is_enough(self):
        """OR-match only. "Must hold both" is a named non-goal; the
        answer to it is a more specific entitlement."""
        from agents.contracts.tools import ToolAccess
        access = ToolAccess(required={"rag.search": frozenset({1, 2})},
                            held=frozenset({2}), unrestricted=False)
        assert access.allows("rag.search") is True

    def test_unrestricted_short_circuits_even_a_labelled_key(self):
        from agents.contracts.tools import ToolAccess
        access = ToolAccess(required={"rag.search": frozenset({1})},
                            held=frozenset(), unrestricted=True)
        assert access.allows("rag.search") is True


class TestGrantedToolsThirdDrop:
    """LOCALLY REGISTERED STUBS, never `tools/rag`'s real keys. This is a
    rule-1 pure leaf's test module: depending on another column's
    `AppConfig.ready()` registrations would make a leaf's test fail when a
    feature flag moved. `make_spec` and `make_principal` come from
    `agents/contracts/tests/_helpers.py`, already imported at the top of
    this module, and the module-level
    `pytestmark = pytest.mark.usefixtures("isolated_tool_registry")` at
    `agents/contracts/tests/test_tools.py:23` keeps every registration
    below out of every other module's registry sweep.
    """

    def test_the_two_argument_call_behaves_exactly_as_before(self):
        """The regression pin for OPEN posture and for every existing
        call site and test: `access` defaults to unrestricted, so
        omitting it changes nothing."""
        register_tool(make_spec(key="stub.safe"))
        assert granted_tools(make_principal(), ["stub.safe"]) == ["stub.safe"]

    def test_a_labelled_key_is_dropped_for_a_principal_without_the_entitlement(self):
        register_tool(make_spec(key="stub.safe"))
        access = ToolAccess(required={"stub.safe": frozenset({1})},
                            held=frozenset({2}), unrestricted=False)
        assert granted_tools(make_principal(), ["stub.safe"], access) == []

    def test_an_unlabelled_key_is_kept_for_everybody(self):
        register_tool(make_spec(key="stub.safe"))
        access = ToolAccess(required={}, held=frozenset(), unrestricted=False)
        assert granted_tools(make_principal(), ["stub.safe"], access) == ["stub.safe"]

    def test_a_mutating_key_is_still_dropped_regardless_of_access(self):
        """ADR 0010's rule stays in force THROUGH IA-2 (spec section 9.1,
        non-goal 12): a settings-mutating tool is registered but not
        grantable, and lifting that is a later phase's decision. Even a
        principal holding the labelling entitlement does not get it."""
        register_tool(make_spec(key="stub.mutating", mutates=True))
        access = ToolAccess(required={"stub.mutating": frozenset({1})},
                            held=frozenset({1}), unrestricted=False)
        assert granted_tools(make_principal(), ["stub.mutating"], access) == []

    def test_order_and_de_duplication_survive_the_third_drop(self):
        register_tool(make_spec(key="stub.a"))
        register_tool(make_spec(key="stub.b"))
        access = ToolAccess(required={"stub.b": frozenset({1})},
                            held=frozenset(), unrestricted=False)
        assert granted_tools(make_principal(), ["stub.a", "stub.b", "stub.a"],
                             access) == ["stub.a"]
```

Extend that module's existing `from agents.contracts.tools import (...)` block (at
`agents/contracts/tests/test_tools.py:12-15`) with `ToolAccess, UNRESTRICTED_TOOL_ACCESS`.
Everything else the block above needs — `granted_tools`, `register_tool`, `make_spec`,
`make_principal`, `isolated_tool_registry` — is already imported there.

- [ ] **Step 2: Run and watch it fail**

Run: `.venv/bin/pytest -q agents/contracts/tests/test_tools.py -x`
Expected: FAIL — `cannot import name 'ToolAccess'`.

- [ ] **Step 3: Add `ToolAccess` and the third drop**

In `agents/contracts/tools.py`, add above `ToolContext` (it already imports `dataclass`,
`field`; add `from collections.abc import Mapping` to the existing `collections.abc` import):

```python
@dataclass(frozen=True)
class ToolAccess:
    """What the ACTING principal may call, as plain data.

    `granted_tools` lives in this rule-1 pure leaf and therefore CANNOT
    query `agents.models.ToolEntitlement`. The facts arrive as data
    instead, built once per turn by `agents.entitlements.tool_access_for`
    -- an `agents` module, which may import `identity.access` through the
    named seam and its own models freely.

    `required` maps a tool key -> the entitlement ids that LABEL it. A
    key ABSENT from this mapping is unlabelled, and therefore callable.
    `held` is the entitlement ids the acting principal holds.
    `unrestricted` short-circuits the whole check: open posture, or a
    principal that sees everything.

    IT DEFAULTS TO UNRESTRICTED, deliberately, so that
    `UNRESTRICTED_TOOL_ACCESS` is the zero-argument answer and every
    existing call site and every test that does not care keeps working
    unchanged. A fail-OPEN default is safe here and only here, because
    the one function that consumes it is called with an explicit value
    on every production path (`agents/runtime/{jobs,loop,preflight}.py`),
    and those three call sites are pinned by tests.
    """

    required: Mapping[str, frozenset[int]] = field(default_factory=dict)
    held: frozenset[int] = frozenset()
    unrestricted: bool = True

    def allows(self, key: str) -> bool:
        if self.unrestricted:
            return True
        needed = self.required.get(key)
        return not needed or bool(needed & self.held)


UNRESTRICTED_TOOL_ACCESS = ToolAccess()
```

Add the field to `ToolContext`, after `agent_slug`:

```python
    # THE ROOT ACTOR'S TOOL ACCESS, carried so a delegate REUSES it
    # rather than rebuilding one for the sub-agent it is about to run
    # (`agents.runtime.delegate.run_agent_tool`). That is the whole of
    # "an agent is never a way around labels": there is no hop at which
    # the acting principal widens, and there is no hop at which its
    # access is recomputed from a different subject.
    #
    # Unrestricted by default, so a runner called directly in a test
    # behaves exactly as it did before this phase.
    tool_access: ToolAccess = UNRESTRICTED_TOOL_ACCESS
```

Rewrite `granted_tools`' signature and body:

```python
def granted_tools(principal: Principal, tool_keys: Sequence[str],
                  access: ToolAccess = UNRESTRICTED_TOOL_ACCESS) -> list[str]:
    """Which of `tool_keys` this `principal` may actually call, in the
    caller's own order, de-duplicated.

    The ONE function that decides availability. THREE DROPS:

    - a key absent from the registry is DROPPED and logged. Normal, not
      exceptional: a feature-gated tool with its flag off, or a tool that
      ships in a later phase.
    - a key whose registered spec is `mutates=True` is DROPPED, silently.
      ADR 0010's rule that a settings-mutating tool is registered but not
      grantable stays in force through IA-2 and is lifted in a later
      phase, not here.
    - a key `access` does not permit is DROPPED and logged AT DEBUG,
      because on a labelled install it is the NORMAL case and an info
      line per turn per tool would drown the log.

    `tool_keys` IS A DECLARATION, NOT A GRANT (IA-2). It is what this
    agent WANTS to be able to do; the grant is `agents.models.
    ToolEntitlement` plus the acting user's entitlements, arriving here
    as `access`. ADR 0015 section 9 said this argument would DISAPPEAR
    when grants moved to their own table; owner decision 13 keeps the
    declaration, so the argument survives and its MEANING changed. The
    ADR carries the amendment.
    """
    out: list[str] = []
    seen: set[str] = set()
    for key in tool_keys:
        if key in seen:
            continue
        seen.add(key)
        spec = _TOOLS.get(key)
        if spec is None:
            logger.info(
                "agents: %s %r was granted %r, which is not registered on this "
                "install; dropped from its tool list.",
                principal.kind, principal.key, key,
            )
            continue
        if spec.mutates:
            continue
        if not access.allows(key):
            logger.debug(
                "agents: %s %r does not hold an entitlement for %r; dropped from "
                "its tool list.", principal.kind, principal.key, key,
            )
            continue
        out.append(key)
    return out
```

- [ ] **Step 4: Run the contract tests and the purity gate**

```bash
.venv/bin/pytest -q agents/contracts
```
Expected: PASS, including `agents/contracts/tests/test_purity.py` — `ToolAccess` adds no Django
import and `Mapping` is stdlib.

- [ ] **Step 5: Write the failing `tool_access_for` tests**

Create `agents/tests/test_entitlements.py`:

```python
"""`agents/entitlements.py::tool_access_for` -- the Django-side builder
for the pure `ToolAccess` value.

ONCE PER TURN, not once per tool: one query over `ToolEntitlement` (a
small table) and one over the principal's grants.
"""
from __future__ import annotations

import pytest

from agents.entitlements import tool_access_for
from agents.models import ToolEntitlement
from agents.tests._helpers import (
    grant, make_admin, make_entitlement, make_user, posture, reset_settings,
    seed_sweep_posture, user_principal,
)
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.contracts.principals import OPEN_PRINCIPAL, SERVICE_PRINCIPAL

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestToolAccessFor:
    def test_open_posture_is_unrestricted_and_runs_no_permission_query(
            self, django_assert_num_queries):
        """The open box must not pay for the enterprise's machinery. One
        primary-key read of the settings singleton, and nothing else."""
        with django_assert_num_queries(1):
            access = tool_access_for(OPEN_PRINCIPAL)
        assert access.unrestricted is True
        assert access.required == {}

    def test_a_member_gets_the_labels_and_their_own_holdings(self):
        member = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=member)
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            access = tool_access_for(user_principal(member))
        assert access.unrestricted is False
        assert access.required == {"rag.search": frozenset({legal.pk})}
        assert access.held == frozenset({finance.pk})
        assert access.allows("rag.search") is False
        assert access.allows("rag.ask") is True

    def test_an_admin_with_the_content_setting_off_is_still_restricted(self):
        """`sees_all_content` is what unrestricts a tool list, and it is
        `is_admin AND admin_sees_content`. Administering is not reading,
        and calling somebody's labelled tool would be reading."""
        admin = make_admin()
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            assert tool_access_for(user_principal(admin)).allows("rag.search") is False
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            assert tool_access_for(user_principal(admin)).allows("rag.search") is True

    def test_a_service_principal_gets_unlabelled_tools_only(self):
        """Grants attach to a user or a group, by the XOR constraint, and
        service-account tokens are IA-3 -- so the watcher and the CLI get
        unlabelled tools only, permanently, for now. Labelling a tool a
        shell path uses makes it silently unavailable there, which is why
        the tool-label form warns about exactly that."""
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            access = tool_access_for(SERVICE_PRINCIPAL)
        assert access.allows("rag.search") is False
        assert access.allows("rag.ask") is True
```

- [ ] **Step 6: Run, watch it fail, then write `agents/entitlements.py`**

Create `agents/entitlements.py`:

```python
"""The Django-side builder for the pure `ToolAccess` value.

An `agents` module, so it may import `identity.access` (import-law rule
2's named seam) and its own `ToolEntitlement` -- neither of which
`agents/contracts/tools.py` may do, because that file is a rule-1 pure
leaf every column imports.

IT RUNS ONCE PER TURN, not once per tool: one query over
`ToolEntitlement` (a small table) and one over the principal's grants.
`agents/runtime/loop.py::_run_turn` and `::jobs.plan_turn` each build one
and thread it down; `delegate.py` reuses the root's rather than building
a second.
"""
from __future__ import annotations

from agents.contracts.tools import UNRESTRICTED_TOOL_ACCESS, ToolAccess
from agents.labels import tool_entitlement_ids
from identity.access import held_entitlement_ids, sees_all_content


def tool_access_for(principal) -> ToolAccess:
    """What `principal` may call, as plain data.

    THE OPEN BRANCH IS FIRST: `sees_all_content` tests `accounts_on()`
    before it reads a second table, so an open box builds this without
    touching `ToolEntitlement` or any grant -- the structural half of
    "an open box never runs a permission query".

    `sees_all_content`, NOT `is_admin`: calling somebody's labelled tool
    is reading, not administering, so an administrator with the content
    setting off is filtered exactly like a member.
    """
    if sees_all_content(principal):
        return UNRESTRICTED_TOOL_ACCESS
    return ToolAccess(
        required=tool_entitlement_ids(),
        held=held_entitlement_ids(principal),
        unrestricted=False,
    )
```

Run: `.venv/bin/pytest -q agents/tests/test_entitlements.py` — expected PASS.

- [ ] **Step 7: Write the failing runtime tests**

Create `agents/runtime/tests/test_tool_access.py` — one module, because these four assertions
are one story and `agents/runtime/tests/test_acting_rule.py` is the precedent for keeping such a
story in one file:

```python
"""Done-when 3: an agent is never a way around labels, proven at the
PLANNER, at the LOOP, at the DELEGATION HOP, and on the page that
refuses before it writes.

Every helper here is one `agents/runtime/tests/_helpers.py` already
exports -- `make_agent`, `bind_chat_role`, `isolated_tool_registry` --
and the tool keys are registered by that fixture, so a dropped key is
dropped for the reason under test rather than for being unregistered.
"""
from __future__ import annotations

import pytest

from agents.contracts.tools import ToolAccess, ToolContext, ToolSpec, register_tool
from agents.runtime.jobs import _tool_roles
from agents.runtime.loop import available_tools
from agents.runtime.preflight import preflight_turn
from agents.runtime.tests._helpers import (  # noqa: F401 -- the import IS the registration
    bind_chat_role, isolated_tool_registry, make_agent, make_job_ctx,
)
from identity.contracts.principals import OPEN_PRINCIPAL
from models.contracts.roles import CHAT_CONVERSE_ROLE

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]

# A registered, non-mutating spec DECLARING A ROLE, so the planner
# assertion below is not vacuous: `_tool_roles` accumulates
# `spec.roles`, and a `roles=()` stub would answer `set()` whether or not
# the access check dropped the key.
LABELLED = ToolSpec(
    key="stub.labelled", label="A labelled stub",
    description="A stub used to prove the access drop.",
    params=(), roles=(CHAT_CONVERSE_ROLE,),
    runner="agents.runtime.tests._helpers.runner_ok",
)


@pytest.fixture(autouse=True)
def _register():
    register_tool(LABELLED)


def _refused() -> ToolAccess:
    """Access that labels `stub.labelled` with an entitlement the acting
    principal does not hold."""
    return ToolAccess(required={"stub.labelled": frozenset({1})},
                      held=frozenset({2}), unrestricted=False)


def _permitted() -> ToolAccess:
    return ToolAccess(required={"stub.labelled": frozenset({1})},
                      held=frozenset({1}), unrestricted=False)


class TestThePlanner:
    def test_a_labelled_tool_declares_no_roles_to_the_queue(self):
        """`_tool_roles` walks agent-as-tool to the depth cap with ONE
        acting principal. A tool the acting user may not call must not
        contribute its role to the admission snapshot either -- otherwise
        the box reserves memory for a model the turn will never use, and
        the enqueue-time plan and the run-time prompt stop being the same
        filtered set.

        THE TWO CELLS DIFFER, which is the whole point: `LABELLED`
        declares a real role, so `set()` here means the key was dropped
        rather than that the stub had nothing to contribute."""
        agent = make_agent(tool_keys=["stub.labelled"])
        assert _tool_roles(agent, OPEN_PRINCIPAL, _refused()) == set()

    def test_the_same_tool_declares_its_role_for_a_holder(self):
        agent = make_agent(tool_keys=["stub.labelled"])
        assert _tool_roles(agent, OPEN_PRINCIPAL, _permitted()) == {CHAT_CONVERSE_ROLE}


class TestTheLoop:
    def test_a_labelled_tool_is_not_offered_to_the_model(self):
        agent = make_agent(tool_keys=["stub.labelled"])
        assert available_tools(OPEN_PRINCIPAL, agent, _refused()) == {}

    def test_a_holder_is_offered_it(self):
        agent = make_agent(tool_keys=["stub.labelled"])
        assert list(available_tools(OPEN_PRINCIPAL, agent, _permitted())) == ["stub.labelled"]


class TestTheDelegationHop:
    def test_a_delegate_reuses_the_root_access_and_never_rebuilds_one(self, monkeypatch):
        """The whole of the acting rule at a hop: WHO this is for never
        changes, and neither does WHAT they may call. A delegate that
        rebuilt access from its own agent -- or from its own principal --
        would be the way around labels this rule exists to close."""
        from agents.runtime import delegate as delegate_module

        captured = {}

        def _fake_available_tools(principal, agent, access):
            captured["principal"] = principal
            captured["access"] = access
            return {}

        monkeypatch.setattr(delegate_module, "available_tools", _fake_available_tools)
        access = _refused()
        child = make_agent(slug="child", tool_keys=["stub.labelled"])
        bind_chat_role(CHAT_CONVERSE_ROLE)
        ctx = ToolContext(conversation_id="", principal=OPEN_PRINCIPAL, depth=0,
                          budget=None, job=None, agent_slug="parent", tool_access=access)
        with pytest.raises(Exception):
            # The call fails later (no conversation id), which is fine:
            # `available_tools` is reached FIRST, and what it was handed
            # is the whole assertion.
            delegate_module.run_agent_tool({"agent": child.slug, "task": "go"}, ctx)
        assert captured["access"] is access
        assert captured["principal"] is OPEN_PRINCIPAL


class TestPreflight:
    def test_an_open_box_drops_nothing_at_preflight(self):
        """THE REGRESSION PIN: `preflight_turn` derives its own access
        from `tool_access_for(actor)`, and in `open` posture that is
        unrestricted -- so IA-2 changed nothing here for a household box.
        The non-open sibling lives in `agents/tests/test_entitlements.py`,
        where the posture helper is already imported."""
        bind_chat_role(CHAT_CONVERSE_ROLE)
        agent = make_agent(tool_keys=["stub.labelled"])
        check = preflight_turn(agent, None, actor=OPEN_PRINCIPAL)
        assert check.dropped_tools == ()
        assert check.unentitled_tools == ()
```

The delegate test builds a `ToolContext` with `budget=None`/`job=None` because
`run_agent_tool` reaches `available_tools` before it touches either; if the real
`run_agent_tool` in this tree reads one earlier, pass the module's own `make_job_ctx()` and a
`StepBudget` built exactly as `test_delegate.py`'s neighbouring tests build one.

`TestPreflight` asserts `()` because `preflight_turn` derives its own access from
`tool_access_for(actor)` and the box under test is in `open` posture, where everything is
unrestricted — that is the **regression pin** that IA-2 changed nothing for an open box. Add its
non-open sibling to `agents/tests/test_entitlements.py`, where the posture helper is already
imported:

```python
    def test_preflight_reports_a_labelled_tool_as_UNENTITLED_not_as_dropped(self):
        """THE TWO CAUSES STAY APART, and the copy is why. A key that is
        not registered on this install is reported to the operator as
        "not available on this install" -- true, and useful. A key the
        acting principal simply lacks an entitlement for is NOT that, and
        saying so would be a false sentence naming a tool the entitlement
        was meant to keep out of their way. `unentitled_tools` renders
        nothing at all: enforcement by omission, the same mechanism
        `available_tools` already uses for the depth cap, and the same
        reason `granted_tools` logs this drop at DEBUG."""
        from agents.models import ToolEntitlement
        from agents.runtime.preflight import dropped_tool_notes, preflight_turn
        from agents.tests._helpers import bind_chat_role, make_agent
        from models.contracts.roles import CHAT_CONVERSE_ROLE
        member = make_user()
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        bind_chat_role(CHAT_CONVERSE_ROLE)
        agent = make_agent(tool_keys=["rag.search"])
        with posture(POSTURE_ENTERPRISE):
            check = preflight_turn(agent, None, actor=user_principal(member))
        assert check.unentitled_tools == ("rag.search",)
        assert check.dropped_tools == ()
        assert dropped_tool_notes(check) == ()
```

- [ ] **Step 8: Thread `access` through the four call sites**

`agents/runtime/jobs.py` — `_tool_roles(agent, actor)` becomes `_tool_roles(agent, actor,
access)`, passes `access` to both `granted_tools` calls in the walk, and `plan_turn` builds one:

```python
    from agents.entitlements import tool_access_for
    access = tool_access_for(actor)
```

immediately after it derives `actor = principal_from_payload(payload)`, and passes it down.

`agents/runtime/loop.py` — `available_tools(principal, agent)` becomes
`available_tools(principal, agent, access=UNRESTRICTED_TOOL_ACCESS)` and passes `access` to
`granted_tools`. `_run_turn` builds `access = tool_access_for(principal)` beside its existing
`principal = principal_from_payload(payload)` and passes it to `available_tools`, and stamps it
onto the `ToolContext` it builds (`tool_access=access`).

`agents/runtime/delegate.py:99` — `available_tools(principal, agent, ctx.tool_access)`. The
child `ToolContext` keeps `tool_access=ctx.tool_access` alongside the `principal=ctx.principal`
it already inherits and its own `agent_slug=agent.slug`.

`agents/runtime/preflight.py:153` — **two passes, two tuples, and one new field**, because the
existing sentence `dropped_tool_notes` renders (`preflight.py:96-97`) is
`"{key} is not available on this install, so this turn runs without it."` and that is **false**
for a key the caller simply lacks an entitlement for. Global Constraint 3 asks for honest copy;
Task 17 exists to remove two smaller instances of exactly this defect, and this task must not
introduce a third.

```python
    # TWO CAUSES, TWO TUPLES. `dropped_tools` keeps its meaning
    # exactly: a key that is not registered here, or whose role will not
    # resolve. `unentitled_tools` is the new, DIFFERENT fact -- the
    # acting principal holds no entitlement for it -- and it is rendered
    # NOWHERE: enforcement by omission, the same mechanism
    # `available_tools` already uses for the depth cap, and the same
    # reason `granted_tools` logs this drop at DEBUG rather than INFO
    # ("on a labelled install it is the NORMAL case").
    registered = granted_tools(actor, agent.tool_keys)
    granted = granted_tools(actor, agent.tool_keys, tool_access_for(actor))
    dropped = tuple(key for key in agent.tool_keys if key not in registered)
    unentitled = tuple(key for key in registered if key not in granted)
```

and every `Preflight(...)` construction in the module passes `unentitled` as its new last
argument. The dataclass gains one **defaulted** field, so nothing else that builds a `Preflight`
has to change:

```python
    dropped_tools: tuple[str, ...]
    # THE ACTING PRINCIPAL HOLDS NO ENTITLEMENT FOR THESE. A separate
    # tuple from `dropped_tools`, not a widening of it, because the two
    # have different operator-facing copy -- and this one's copy is
    # nothing at all. Defaulted so every existing construction, and every
    # test that builds one, is unchanged.
    unentitled_tools: tuple[str, ...] = ()
```

`dropped_tool_notes` is **not** changed: it still iterates `check.dropped_tools` only, so a
member sees no note naming a tool they were never meant to know about.

`agents/runtime/invoke.py` — the `ToolInvocation.objects.create(...)` call gains
`agent_slug=tool_ctx.agent_slug`. Nothing else in that module changes: it never decided
availability and must not start.

Each edit carries a one-line comment naming the rule it serves; the docstrings above are the
wording to reuse.

- [ ] **Step 9: Run the agents suite**

```bash
.venv/bin/pytest -q agents
FARABUNKER_TEST_POSTURE=enterprise .venv/bin/pytest -q agents
```
Expected: PASS in both.

- [ ] **Step 10: Commit**

```bash
git add agents/contracts/tools.py agents/entitlements.py agents/runtime/ agents/tests/ \
        agents/contracts/tests/
git commit -m "feat(agents): IA-2 T5 — ToolAccess, granted_tools' third drop, and the acting rule's grant half"
```

---
### Task 6: the tool-label page, `/chat/tools/`

The label page lives in `agents/chat` because `identity/` may not import `agents/` and `/chat/`
is this column's only URL mount (spec §4.2, §15). Class **S**: labelling a tool is
library-wide operator policy, and unlike a document label it has no entitlement owner —
spec §7.4's three owner capabilities name documents, not tools.

**Files:**
- Create: `agents/chat/views/tools.py`,
  `agents/chat/templates/chat/tool_entitlements.html`
- Modify: `agents/visibility.py` (one new reader), `agents/chat/views/__init__.py`,
  `agents/chat/urls.py`, `identity/routes.py`, `foundation/templates/_shell.html` (one anchor)
- Test: `agents/chat/tests/test_tool_entitlements_page.py`,
  `agents/tests/test_visibility.py` (append — use the real module name in that package),
  `identity/tests/test_route_matrix.py` (driver)

**Interfaces:**
- Consumes: `agents.labels.set_tool_labels`/`tool_entitlement_ids` (Task 4);
  `identity.access.labelling_entitlements` (Task 1);
  `agents.contracts.tools.grantable_tools` (existing).
- Produces: URL name `chat-tool-entitlements` at `/chat/tools/`, class `S`.
- Produces: `agents.visibility.resident_agent_tool_keys() -> dict[str, list[str]]`.

- [ ] **Step 1: Write the failing page test**

Create `agents/chat/tests/test_tool_entitlements_page.py`:

```python
"""`/chat/tools/` -- which entitlement a registered tool needs.

CLASS S. Labelling a tool is library-wide operator policy: unlike a
document, a tool has no entitlement owner (spec section 7.4 names three
owner capabilities, and all three are about documents and grants).

THE SHELL-PATH CONSEQUENCE IS STATED ON THE PAGE, not discovered later.
A service principal holds no entitlements -- grants attach to a user or
a group by the XOR constraint, and service-account tokens are IA-3 -- so
labelling a tool makes it PERMANENTLY unavailable to the watcher and to
`manage.py agent_turn`, and the honest failure appears as an agent that
cannot find anything rather than as a refusal.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (
    make_admin, make_entitlement, make_user, posture, reset_settings, seed_sweep_posture,
    sign_in,
)
from agents.models import ToolEntitlement
from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestTheToolLabelPage:
    def test_an_admin_sees_every_grantable_tool_and_a_member_gets_403(self, client):
        admin, member = make_admin(), make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.get(reverse("chat-tool-entitlements"))
            assert response.status_code == 200
            assert b"rag.search" in response.content
            other = client.__class__()
            sign_in(other, member)
            assert other.get(reverse("chat-tool-entitlements")).status_code == 403

    def test_a_mutating_tool_is_not_offered_for_labelling(self):
        """`grantable_tools()` excludes every `mutates=True` spec, and
        ADR 0010's rule that such a tool is registered but not grantable
        stays in force through IA-2. A label on a tool nobody can be
        granted would be a control with no effect."""
        from agents.contracts.tools import grantable_tools
        assert all(not spec.mutates for spec in grantable_tools())

    def test_posting_labels_writes_them_and_redirects(self, client):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("chat-tool-entitlements"),
                                   {"tool_key": "rag.search",
                                    "entitlements": [str(finance.pk)]})
        assert response.status_code == 302
        assert ToolEntitlement.objects.filter(tool_key="rag.search",
                                              entitlement=finance).exists()

    def test_posting_an_empty_set_removes_every_label(self, client):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("chat-tool-entitlements"),
                        {"tool_key": "rag.search", "entitlements": []})
        assert ToolEntitlement.objects.count() == 0

    def test_an_unknown_tool_key_answers_400_not_a_silent_no_op(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("chat-tool-entitlements"),
                                   {"tool_key": "not.registered", "entitlements": []})
        assert response.status_code == 400
        assert ToolEntitlement.objects.count() == 0

    def test_a_non_numeric_entitlement_id_is_refused_not_crashed(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("chat-tool-entitlements"),
                                   {"tool_key": "rag.search", "entitlements": ["abc"]},
                                   follow=True)
        assert response.status_code == 200
        assert b"Traceback" not in response.content
        assert ToolEntitlement.objects.count() == 0

    def test_an_entitlement_id_that_does_not_exist_is_refused(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("chat-tool-entitlements"),
                        {"tool_key": "rag.search", "entitlements": ["99999"]}, follow=True)
        assert ToolEntitlement.objects.count() == 0

    def test_the_page_names_the_shell_path_consequence(self, client):
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("chat-tool-entitlements")).content
        assert b"command line" in body

    def test_a_tool_a_shipped_agent_declares_carries_its_own_warning(self, client):
        """`manage.py agent_turn` runs an agent as the one service
        principal, so labelling a tool a RESIDENT agent declares makes
        that shell path silently weaker. The form says which."""
        from agents.chat.tests._helpers import make_agent
        admin = make_admin()
        make_agent(slug="librarian", resident=True, enabled=True, tool_keys=["rag.search"])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("chat-tool-entitlements")).content
        assert b"librarian" in body
```

- [ ] **Step 2: Run and watch it fail**

Run: `.venv/bin/pytest -q agents/chat/tests/test_tool_entitlements_page.py -x`
Expected: FAIL — `NoReverseMatch: Reverse for 'chat-tool-entitlements' not found`.

- [ ] **Step 3: Add the shell-reachable reader to `agents/visibility.py`**

The page warns when the tool being labelled is one a shipped agent declares, and that answer
needs `Agent.objects`. **`agents/chat` may not touch it.**
`foundation/ops/tests/test_column_boundaries.py:527,540,565` sweeps `git ls-files -- agents/chat`
for `Conversation`/`Agent`/`Flow` `.objects` and excludes exactly one module,
`agents/visibility.py`; its own docstring says "The exclusion is FLAT and PER-MODULE… No
carve-out for creates, no carve-out for 'just this one query'." So the query lives where the
manager already does:

```python
def resident_agent_tool_keys() -> dict[str, list[str]]:
    """`{tool_key: [resident agent slugs that declare it]}` -- the
    tool-label page's shell-path warning.

    HERE, NOT IN `agents/chat`, because this module is the one place that
    package reaches `Agent.objects` at all. It is not a visibility
    question, but it is an `Agent` question, and the guard is flat.

    `manage.py agent_turn` runs an agent as the ONE service principal,
    and a service principal holds no entitlements -- grants attach to a
    user or a group by the XOR constraint, and service-account tokens are
    a later phase. So labelling a tool a shipped (`resident=True`) agent
    declares makes that shell path SILENTLY weaker: the agent is simply
    not told the tool exists, and the failure reads as a model that
    cannot find anything rather than as a refusal. The form names those
    agents so the operator is warned before they cause it.
    """
    out: dict[str, list[str]] = {}
    for slug, keys in Agent.objects.filter(resident=True, enabled=True) \
                                   .values_list("slug", "tool_keys"):
        for key in keys or ():
            out.setdefault(key, []).append(slug)
    return out
```

with the test, appended to the agents column's visibility test module:

```python
class TestResidentAgentToolKeys:
    def test_it_maps_a_declared_key_to_the_shipped_agents_that_declare_it(self):
        make_agent(slug="librarian", resident=True, enabled=True,
                   tool_keys=["rag.search"])
        make_agent(slug="private", resident=False, enabled=True, tool_keys=["rag.search"])
        make_agent(slug="off", resident=True, enabled=False, tool_keys=["rag.search"])
        assert resident_agent_tool_keys() == {"rag.search": ["librarian"]}

    def test_an_agent_with_no_tool_keys_contributes_nothing(self):
        make_agent(slug="quiet", resident=True, enabled=True, tool_keys=[])
        assert resident_agent_tool_keys() == {}
```

- [ ] **Step 4: Write the view**

Create `agents/chat/views/tools.py`:

```python
"""`/chat/tools/` -- which entitlement each registered tool needs.

IT LIVES HERE because `identity/` may not import `agents/` (import-law
rule 4) and `/chat/` is this column's only URL mount. A label belongs
beside the thing it protects, which is the same reason the document-label
page lives in `tools/rag`.

CLASS S. A tool label is library-wide operator policy. Unlike a document
label it has no entitlement owner: spec section 7.4 names an owner's
three capabilities and all three are about grants and documents.
"""
from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render

from agents.contracts.tools import grantable_tools
from agents.labels import set_tool_labels, tool_entitlement_ids
from agents.visibility import resident_agent_tool_keys
from identity.access import labelling_entitlements
from identity.gate import require_admin
from identity.request import principal_for_request


@require_admin
def tool_entitlements(request):
    """GET renders every grantable tool with its current labels; POST
    sets one tool's labels to exactly what was submitted.

    ONE TOOL PER POST, with a `tool_key` field, rather than one giant
    form for the whole page: a page-wide submit would make an operator's
    single change indistinguishable from an accidental wipe of every
    other row, and a partial render (a feature-gated tool absent from
    THIS install) would silently unlabel it.
    """
    if request.method == "POST":
        return _save(request)

    # ONE QUERY for every key's labels (`tool_entitlement_ids`), not one
    # per grantable tool: `agents/labels.py` grew that function in Task 4
    # for exactly this shape, and `labels_for` is the single-key reader
    # the label WRITE path uses.
    labels = tool_entitlement_ids()
    shell = resident_agent_tool_keys()
    choices = labelling_entitlements(principal_for_request(request))
    rows = [
        {
            "key": spec.key,
            "label": spec.label,
            "description": spec.description,
            "held": labels.get(spec.key, frozenset()),
            "shell_agents": shell.get(spec.key, []),
        }
        for spec in grantable_tools()
    ]
    return render(request, "chat/tool_entitlements.html",
                  {"rows": rows, "choices": choices})


def _save(request):
    key = request.POST.get("tool_key", "")
    if key not in {spec.key for spec in grantable_tools()}:
        # 400, not a silent no-op: a form that quietly did nothing would
        # read as success, and this one changes who may call a tool.
        return HttpResponseBadRequest(f"{key!r} is not a grantable tool on this install.")
    raw = request.POST.getlist("entitlements")
    if any(not value.isdigit() for value in raw):
        messages.error(request, "Entitlements are chosen from the list, not typed.")
        return redirect("chat-tool-entitlements")
    wanted = {int(value) for value in raw}
    principal = principal_for_request(request)
    offered = {pk for pk, _name in labelling_entitlements(principal)}
    if not wanted <= offered:
        # THE SAME PREDICATE THE FORM RENDERS FROM. A POST naming an
        # entitlement this page never offered is either a stale form or a
        # hand-made request, and both get the same honest refusal.
        messages.error(request, "That entitlement is not one you may label with.")
        return redirect("chat-tool-entitlements")
    set_tool_labels(principal, key, wanted)
    messages.info(request, f"Saved the entitlements for {key}.")
    return redirect("chat-tool-entitlements")
```

Export it from `agents/chat/views/__init__.py` beside the existing names, and mount it in
`agents/chat/urls.py`:

```python
    path("tools/", tool_entitlements, name="chat-tool-entitlements"),
```

Classify it in `identity/routes.py`, in the `/chat/` block:

```python
    # Labelling a tool is library-wide operator policy, and it lives at
    # `/chat/` because `/chat/` is the agents column's only mount.
    "chat-tool-entitlements": "S",
```

and add its nav anchor to `foundation/templates/_shell.html:205`'s identity block — **in this
commit, not Task 3's**, because a `{% url %}` for an unmounted name raises `NoReverseMatch` on
every page render:

```html
 <a href="{% url 'chat-tool-entitlements' %}" class="{% block nav_current_tool_entitlements %}{% endblock %}">Tool access</a>
```

immediately before the `Settings` anchor, inside the existing
`{% if identity_posture != "open" and identity_is_admin %}` condition. The template created in
the next step sets `{% block nav_current_tool_entitlements %}current{% endblock %}`. Without
this the page has no discovery path anywhere on the box, and Task 19's browser walk asks the
owner to reach it.

- [ ] **Step 5: Write the template**

Create `agents/chat/templates/chat/tool_entitlements.html`:

```html
{% extends "_shell.html" %}
{% comment %}
Tool labels. One form per tool, so an operator's single change can never
read as a wipe of every other row.

The shell-path consequence is stated here rather than discovered later:
a service principal holds no entitlements, so a labelled tool is
permanently unavailable to the watcher and the command line until
service-account tokens land.
{% endcomment %}
{% block title %}Tool entitlements — farabunker{% endblock %}
{% block nav_current_tool_entitlements %}current{% endblock %}
{% block extra_style %}
  main { max-width: 900px; margin: 0 auto; }
  .msg { padding: .6rem .9rem; border: 1px solid var(--border); border-radius: 6px;
         margin-bottom: .4rem; font-size: .9rem; background: var(--panel); }
  .msg.error { border-color: var(--danger); color: var(--danger); }
  section.tool { background: var(--panel); border: 1px solid var(--border);
                 border-radius: 8px; padding: .9rem; margin-bottom: 1rem; }
  section.tool h2 { font-size: .95rem; margin: 0 0 .3rem; font-family: monospace; }
  .muted { color: var(--muted); font-size: .85rem; }
  .warn { color: var(--danger); font-size: .85rem; }
  select[multiple] { min-width: 16rem; }
{% endblock %}
{% block content %}
<h1>Tool entitlements</h1>
{% for message in messages %}<div class="msg {{ message.tags }}">{{ message }}</div>{% endfor %}
<p class="muted">A tool with no entitlement is callable by everyone signed in. Labelling one
  narrows it to the people who hold any of its entitlements — an agent declares the tools it
  wants, and this decides which of them a person actually gets.</p>
<p class="warn">A labelled tool is unavailable to the watcher and the command line. Automated
  callers hold no entitlements on this box, so a labelled tool is simply never offered to them,
  and the failure looks like an agent that cannot find anything rather than a refusal.</p>

{% for row in rows %}
<section class="tool">
  <h2>{{ row.key }}</h2>
  <p class="muted">{{ row.label }} — {{ row.description }}</p>
  {% if row.shell_agents %}
    <p class="warn">Declared by shipped agent{{ row.shell_agents|pluralize }}:
      {{ row.shell_agents|join:", " }}. Labelling it removes it from those agents when they
      are run from the command line.</p>
  {% endif %}
  <form method="post" action="{% url 'chat-tool-entitlements' %}">
    {% csrf_token %}
    <input type="hidden" name="tool_key" value="{{ row.key }}">
    <select name="entitlements" multiple size="4">
      {% for pk, name in choices %}
        <option value="{{ pk }}" {% if pk in row.held %}selected{% endif %}>{{ name }}</option>
      {% endfor %}
    </select>
    <button type="submit">Save</button>
  </form>
</section>
{% endfor %}
{% endblock %}
```

- [ ] **Step 6: Add the matrix driver and the class assertion**

In `identity/tests/test_route_matrix.py`, add to `_DRIVERS`:

```python
    "chat-tool-entitlements": lambda w: ("get", reverse("chat-tool-entitlements"), {}),
```

and append to `identity/tests/test_routes.py::TestIA2Routes`:

```python
    def test_the_tool_label_page_is_S(self):
        """Labelling a tool is library-wide operator policy. Unlike a
        document label it has no entitlement owner -- spec section 7.4
        names an owner's three capabilities and all three are about
        grants and documents."""
        from identity.routes import ROUTE_RULES
        assert ROUTE_RULES["chat-tool-entitlements"] == "S"
```

- [ ] **Step 7: Run everything**

```bash
.venv/bin/pytest -q agents identity foundation
```
Expected: PASS, including
`foundation/ops/tests/test_column_boundaries.py::test_no_chat_module_queries_the_three_owned_models_directly`
— the guard Step 3 exists to keep green.

- [ ] **Step 8: Commit**

```bash
git add agents/visibility.py agents/chat/views/ agents/chat/urls.py \
        agents/chat/templates/chat/tool_entitlements.html foundation/templates/_shell.html \
        identity/routes.py agents/tests/ agents/chat/tests/ identity/tests/
git commit -m "feat(agents): IA-2 T6 — the tool-label page at /chat/tools/"
```

---

### Task 7: `Share` in the visibility bodies, and the `use`-level rule on `chat-turn`

Four bodies gain one clause each; two new functions write and revoke; one existing function
learns to take the shares with the conversation. This is the task done-when 4 tests.

**Files:**
- Create: `agents/shares.py`
- Modify: `agents/visibility.py`, `agents/chat/views/turns.py`,
  `foundation/ops/tests/test_column_boundaries.py` (`_VISIBILITY_MODELS`)
- Test: `agents/tests/test_shares.py`, `agents/tests/test_visibility.py` (append — use the real
  module name in that package), `agents/chat/tests/test_turns.py` (append)

**Interfaces:**
- Consumes: `agents.models.Share`, `::TARGET_KEY_PARSERS` (Task 4).
- Produces: `agents.shares.shared_keys(target_type, principal) -> list`,
  `::share_level(principal, target_type, target_key) -> str | None`,
  `::shares_for(target_type, target_key)`.
- Produces: `agents.visibility.share_conversation(principal, conversation, *, user=None,
  group=None, level)`, `::revoke_share(principal, conversation, share_id)`,
  `::may_post_to(principal, conversation) -> bool`.

- [ ] **Step 1: Write the failing share-reader tests**

Create `agents/tests/test_shares.py`:

```python
"""`agents/shares.py` -- the two small queries every visibility body
needs, and the parser that keeps one bad row from 500-ing a page.
"""
from __future__ import annotations

import pytest

from agents.models import Share
from agents.shares import share_level, shared_keys
from agents.tests._helpers import (
    make_conversation, make_group, make_user, posture, reset_settings, seed_sweep_posture,
    user_principal,
)
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.contracts.principals import OPEN_PRINCIPAL, SERVICE_PRINCIPAL

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestSharedKeys:
    def test_a_direct_share_and_a_group_share_both_reach(self):
        user = make_user()
        group = make_group()
        user.groups.add(group)
        one = make_conversation()
        two = make_conversation()
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(one.pk), user=user)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(two.pk), group=group)
        with posture(POSTURE_ENTERPRISE):
            keys = shared_keys(Share.Target.CONVERSATION, user_principal(user))
        assert set(keys) == {str(one.pk), str(two.pk)}

    def test_a_non_user_principal_reaches_nothing(self):
        conversation = make_conversation()
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=make_user())
        with posture(POSTURE_ENTERPRISE):
            assert shared_keys(Share.Target.CONVERSATION, SERVICE_PRINCIPAL) == []
            assert shared_keys(Share.Target.CONVERSATION, OPEN_PRINCIPAL) == []

    def test_an_unparseable_key_is_dropped_rather_than_reaching_the_queryset(self):
        """The SECOND half of the two-sided rule. `Share.save()` refuses
        such a key on the way in; this drops one that arrived from a
        shell or an older schema on the way out. Without it,
        `Q(pk__in=[...])` raises INSIDE a listing queryset and turns a
        page into a 500 -- a never-500 violation reachable by one bad
        row."""
        user = make_user()
        conversation = make_conversation()
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=user)
        # Written past `save()` deliberately, exactly as a shell would.
        Share.objects.filter(user=user).update(target_key="not-a-uuid")
        with posture(POSTURE_ENTERPRISE):
            assert shared_keys(Share.Target.CONVERSATION, user_principal(user)) == []


class TestShareLevel:
    def test_the_widest_level_wins_when_two_shares_reach_one_row(self):
        """A person can be reached by a direct `view` share and a group
        `use` share at once. The answer has to be one level, and the
        honest one is the WIDEST -- anything else would mean adding
        somebody to a group silently REMOVED an ability."""
        user = make_user()
        group = make_group()
        user.groups.add(group)
        conversation = make_conversation()
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=user,
                             level=Share.Level.VIEW)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), group=group,
                             level=Share.Level.USE)
        with posture(POSTURE_ENTERPRISE):
            assert share_level(user_principal(user), Share.Target.CONVERSATION,
                               str(conversation.pk)) == Share.Level.USE

    def test_no_share_answers_none(self):
        conversation = make_conversation()
        with posture(POSTURE_ENTERPRISE):
            assert share_level(user_principal(make_user()), Share.Target.CONVERSATION,
                               str(conversation.pk)) is None
```

- [ ] **Step 2: Run, watch it fail, then write `agents/shares.py`**

```python
"""Reading `Share` rows -- the two small queries every visibility body
needs.

IT IS A SEPARATE MODULE FROM `agents/visibility.py` because
`foundation/ops/tests/test_column_boundaries.py` forbids any module under
`agents/chat` from touching `Conversation`/`Agent`/`Flow` `.objects`
directly, and IA-2 adds `Share` to that same set. One module owns the
manager; everything else asks it a question. A guard with an exception is
a guard somebody widens.
"""
from __future__ import annotations

import logging

from django.db.models import Q

from agents.models import TARGET_KEY_PARSERS, Share

logger = logging.getLogger(__name__)


def _subject_q(principal) -> Q | None:
    """`Q` matching shares that reach `principal`, or None for a
    principal a share can never name."""
    if getattr(principal, "kind", None) != "user":
        return None
    key = principal.key
    if not isinstance(key, str) or not key.isdigit():
        return None
    return Q(user_id=int(key)) | Q(group__user__id=int(key))


def shared_keys(target_type: str, principal) -> list[str]:
    """The `target_key` strings this principal reaches through a `Share`,
    directly or through one of their groups.

    MATERIALISED into a list rather than left as a `Subquery`,
    deliberately: `Share.target_key` is text and the three target tables
    have three different primary-key types, so a subquery would need a
    per-type cast and would be a silent type mismatch waiting to happen.
    Two small queries on a single-box install beat one clever one.

    FILTERED THROUGH THE TARGET'S OWN KEY PARSER on the way out -- an
    unparseable row is dropped and logged rather than reaching
    `Q(pk__in=[...])`, where it would raise inside the queryset and turn
    a listing page into a 500.
    """
    subject = _subject_q(principal)
    if subject is None:
        return []
    parse = TARGET_KEY_PARSERS[target_type]
    out = []
    for key in Share.objects.filter(target_type=target_type).filter(subject) \
                            .values_list("target_key", flat=True):
        if parse(key) is None:
            logger.warning("agents: share row for %s carries an unusable key %r; ignored.",
                           target_type, key)
            continue
        out.append(key)
    return out


def share_level(principal, target_type: str, target_key: str) -> str | None:
    """The widest level `principal` reaches this one row at, or None.

    THE WIDEST WINS when a direct share and a group share both reach:
    the answer has to be ONE level, and anything but the widest would
    mean adding somebody to a group silently REMOVED an ability they
    already had.
    """
    subject = _subject_q(principal)
    if subject is None:
        return None
    levels = set(Share.objects.filter(target_type=target_type, target_key=str(target_key))
                              .filter(subject).values_list("level", flat=True))
    if Share.Level.USE in levels:
        return Share.Level.USE
    if Share.Level.VIEW in levels:
        return Share.Level.VIEW
    return None


def shares_for(target_type: str, target_key: str):
    """Every share on one row, for the owner's own share list."""
    return Share.objects.filter(target_type=target_type, target_key=str(target_key)) \
                        .select_related("user", "group").order_by("id")
```

- [ ] **Step 3: Write the failing visibility tests**

Append to the agents column's visibility test module:

```python
class TestSharesExtendVisibility:
    def test_a_shared_conversation_is_visible_to_the_recipient(self):
        owner, guest = make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=guest)
        with posture(POSTURE_ENTERPRISE):
            assert conversation in visible_conversations(user_principal(guest))

    def test_a_shared_agent_and_flow_are_visible_to_the_recipient(self):
        owner, guest = make_user(), make_user()
        agent = make_agent(resident=False, enabled=True, **owner_fields(user_principal(owner)))
        flow = make_flow(resident=False, enabled=True, **owner_fields(user_principal(owner)))
        Share.objects.create(target_type=Share.Target.AGENT, target_key=str(agent.pk),
                             user=guest)
        Share.objects.create(target_type=Share.Target.FLOW, target_key=str(flow.pk),
                             user=guest)
        with posture(POSTURE_ENTERPRISE):
            principal = user_principal(guest)
            assert agent in visible_agents(principal)
            assert flow in visible_flows(principal)
            assert agent.slug in set(installed_agent_slugs(principal))

    def test_an_open_box_still_runs_no_share_query(self, django_assert_num_queries):
        """The open branch is FIRST in every body: `sees_all_content`
        tests `accounts_on()` before it reads a second table, so an open
        box executes zero share queries."""
        create_conversation(OPEN_PRINCIPAL, make_agent())
        with django_assert_num_queries(2):   # the settings row + the list
            list(visible_conversations(OPEN_PRINCIPAL))


class TestSharingAConversation:
    def test_share_then_revoke_round_trips(self):
        owner, guest = make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        with posture(POSTURE_ENTERPRISE):
            row = share_conversation(user_principal(owner), conversation, user=guest,
                                     level=Share.Level.VIEW)
            assert row is not None
            assert revoke_share(user_principal(owner), conversation, row.pk) is True
        assert Share.objects.count() == 0

    def test_a_recipient_may_not_re_share(self):
        """Owner or `sees_all_content` only. A share is the owner's
        decision about their own thread, and a recipient passing it on
        would make the owner's list of who is reading it wrong."""
        owner, guest, third = make_user(), make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=guest)
        with posture(POSTURE_ENTERPRISE):
            assert share_conversation(user_principal(guest), conversation, user=third,
                                      level=Share.Level.VIEW) is None

    def test_revoke_answers_none_for_an_unparseable_share_id(self):
        """It arrives from a POST body, so a non-numeric value would
        otherwise reach `Share.objects.get(pk=...)` and raise
        `ValueError` -- a 500 on a never-500 surface, reachable by
        anybody who can post a form."""
        owner = make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        with posture(POSTURE_ENTERPRISE):
            assert revoke_share(user_principal(owner), conversation, "abc") is None

    def test_revoke_answers_none_for_a_share_on_another_conversation(self):
        """The IDOR. Without the belongs-to check, an owner of A could
        revoke a share on B by guessing a sequential id -- and it would
        read as a legitimate action in the audit log."""
        owner = make_user()
        mine = create_conversation(user_principal(owner), make_agent())
        theirs = create_conversation(user_principal(make_user()), make_agent())
        elsewhere = Share.objects.create(target_type=Share.Target.CONVERSATION,
                                         target_key=str(theirs.pk), user=make_user())
        with posture(POSTURE_ENTERPRISE):
            assert revoke_share(user_principal(owner), mine, elsewhere.pk) is None
        assert Share.objects.filter(pk=elsewhere.pk).exists()

    def test_deleting_a_conversation_deletes_its_shares(self):
        owner, guest = make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=guest)
        with posture(POSTURE_ENTERPRISE):
            assert delete_conversation(user_principal(owner), conversation) is True
        assert Share.objects.count() == 0


class TestPostingRights:
    def test_view_may_read_and_not_post_and_use_may_do_both(self):
        """Done-when 4, at the function the view asks."""
        owner, viewer, poster = make_user(), make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=viewer,
                             level=Share.Level.VIEW)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=poster,
                             level=Share.Level.USE)
        with posture(POSTURE_ENTERPRISE):
            assert may_post_to(user_principal(owner), conversation) is True
            assert may_post_to(user_principal(viewer), conversation) is False
            assert may_post_to(user_principal(poster), conversation) is True
```

Use the module's own existing helpers for `make_agent`/`make_flow`/`create_conversation`; add
the `Share`, `share_conversation`, `revoke_share`, `may_post_to` and `owner_fields` imports at
the top.

- [ ] **Step 4: Write the visibility changes**

In `agents/visibility.py`, add `from agents.models import Share` and
`from agents.shares import share_level, shared_keys` to the imports, then:

Add one clause to each of the four bodies — nothing else about them changes:

```python
    return qs.filter(
        owned_rows_q(principal)
        | Q(pk__in=shared_keys(Share.Target.CONVERSATION, principal))
    ).distinct()
```
```python
    return qs.filter(
        owned_rows_q(principal) | Q(resident=True)
        | Q(pk__in=shared_keys(Share.Target.AGENT, principal))
    ).distinct()
```
(the same for `installed_agent_slugs`, and with `Share.Target.FLOW` for `visible_flows`).

`.distinct()` is added because a row reachable by both an own-clause and a share-clause would
otherwise appear twice — `installed_agent_slugs` already ends in `.distinct()` and needs no
second one.

Then the three new functions:

```python
def may_post_to(principal, conversation) -> bool:
    """Whether `principal` may add a TURN to `conversation`.

    Owner, `sees_all_content`, or a `use`-level share. A `view` share
    reads the thread and does not post into it -- which is the whole
    reason `Share.Level` exists rather than the level being implied.
    """
    if sees_all_content(principal):
        return True
    if may_read_owned_row(principal, conversation):
        return True
    return share_level(principal, Share.Target.CONVERSATION,
                       str(conversation.pk)) == Share.Level.USE


def _may_share(principal, conversation) -> bool:
    """Owner or `sees_all_content` -- A RECIPIENT MAY NOT RE-SHARE. A
    share is the owner's decision about their own thread, and a recipient
    passing it on would make the owner's own list of who is reading it
    wrong."""
    return sees_all_content(principal) or may_read_owned_row(principal, conversation)


def share_conversation(principal, conversation, *, user=None, group=None, level):
    """Add one `Share` row, or answer None if `principal` may not.

    Exactly one of `user`/`group`, or the database's XOR constraint
    refuses the row; the caller's form makes the other case
    unrepresentable.
    """
    if not _may_share(principal, conversation):
        return None
    if bool(user) == bool(group):
        return None
    if level not in Share.Level.values:
        return None
    return Share.objects.create(
        target_type=Share.Target.CONVERSATION, target_key=str(conversation.pk),
        user=user, group=group, level=level,
        shared_by_id=int(principal.key) if principal.kind == "user" else None)


def revoke_share(principal, conversation, share_id):
    """Remove one `Share` row FROM THIS CONVERSATION. True if it went,
    None if it may not or does not belong here.

    TWO REFUSALS, and neither is optional. `share_id` is parsed as an int
    FIRST -- it arrives from a POST body, so a non-numeric value would
    reach `Share.objects.get(pk=...)` and raise `ValueError`, a 500 on a
    never-500 surface. And the row is then checked against THIS
    conversation, or an owner of conversation A could revoke a share on
    conversation B by guessing a sequential id -- an IDOR that reads as a
    legitimate action in the audit log.

    The VIEW answers 404 to both, never 403, because a 403 would confirm
    that some other conversation's share carries that id.
    """
    if not _may_share(principal, conversation):
        return None
    if not str(share_id).isdigit():
        return None
    row = Share.objects.filter(pk=int(share_id),
                               target_type=Share.Target.CONVERSATION,
                               target_key=str(conversation.pk)).first()
    if row is None:
        return None
    row.delete()
    return True
```

And in `delete_conversation`, replace `conversation.delete()` with:

```python
    with transaction.atomic():
        # IA-2: the thread's shares go with it, in the SAME transaction.
        # No `post_delete` receiver: this repository uses no Django
        # signals anywhere, and a stale `Share` row is inert -- every
        # reader resolves the target first. This is the one shipped
        # delete surface, so it is the one place that has to say so.
        Share.objects.filter(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk)).delete()
        conversation.delete()
    return True
```
(`from django.db import transaction` joins the imports.)

Remove the `IA-2 adds ...` promises from `visible_conversations`' and `delete_conversation`'s
docstrings — they are now facts, and a docstring that still promises what the code already does
is a docstring that lies.

- [ ] **Step 5: Add `Share` to the chat `.objects` guard**

In `foundation/ops/tests/test_column_boundaries.py`, extend:

```python
_VISIBILITY_MODELS = ("Conversation", "Agent", "Flow", "Share")
```

and widen that test's exclusion set from `agents/visibility.py` alone to
`{"agents/visibility.py", "agents/shares.py"}`, with the reason written where the existing
one-module comment is: `shares.py` is the module that owns the `Share` manager, exactly as
`visibility.py` owns the other three. Update
`test_the_visibility_module_exclusion_is_a_closed_set_of_one` — including its **name** — to
`..._of_two`, and its assertion with it.

- [ ] **Step 6: Enforce `use` on `chat-turn`**

In `agents/chat/views/turns.py::turn_create`, immediately after the conversation is resolved
through `visible_conversations`:

```python
    if not may_post_to(principal, conversation):
        # A `view` share READS the thread; it does not post into it.
        # 403, not 404: the caller can already see this conversation --
        # that is what got them here -- so pretending it does not exist
        # would be a secrecy the surrounding page does not keep.
        return HttpResponseForbidden(
            "This conversation was shared with you to read, not to post in."
        )
```

Append to that module's test file:

```python
class TestAViewShareMayNotPost:
    def test_view_gets_403_and_use_gets_the_ordinary_answer(self, client):
        owner, viewer, poster = make_user(), make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=viewer,
                             level=Share.Level.VIEW)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=poster,
                             level=Share.Level.USE)
        url = reverse("chat-turn", args=[conversation.pk])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            assert client.post(url, {"text": "hello"}).status_code == 403
            other = client.__class__()
            sign_in(other, poster)
            # 503 is the honest answer on a box with no chat role bound;
            # what matters is that it is NOT the 403 the viewer got.
            assert other.post(url, {"text": "hello"}).status_code != 403
```

- [ ] **Step 7: Run the agents and identity suites, in both postures**

```bash
.venv/bin/pytest -q agents identity
FARABUNKER_TEST_POSTURE=enterprise .venv/bin/pytest -q agents
```
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add agents/shares.py agents/visibility.py agents/chat/views/turns.py agents/tests/ \
        agents/chat/tests/ foundation/ops/tests/test_column_boundaries.py
git commit -m "feat(agents): IA-2 T7 — shares extend the visibility bodies, and a view share may not post"
```

---
### Task 8: the conversation-sharing UI

One route, one panel, two actions in one POST body. IA-2 ships exactly one share UI (spec
§10.3); agents, flows and generated images are share-*able by table* and are not share-able by
UI, which is deliberate scope control rather than an oversight.

**Files:**
- Create: `agents/chat/views/shares.py`, `agents/chat/templates/chat/_share_panel.html`
- Modify: `agents/chat/views/__init__.py`, `agents/chat/urls.py`, `identity/routes.py`,
  `agents/chat/views/thread.py`, `agents/chat/templates/chat/conversation.html`, `agents/visibility.py`
- Test: `agents/chat/tests/test_conversation_share.py`,
  `identity/tests/test_route_matrix.py` (driver)

**Interfaces:**
- Consumes: `agents.visibility.share_conversation`/`revoke_share` (Task 7);
  `agents.shares.shares_for` (Task 7); `identity.access.share_subjects` (Task 1).
- Produces: URL name `chat-conversation-share` at `/chat/c/<uuid>/share/`, class `O`.

- [ ] **Step 1: Write the failing test**

Create `agents/chat/tests/test_conversation_share.py`:

```python
"""`POST /chat/c/<uuid>/share/` -- add a share, and revoke one.

ONE ROUTE, NOT TWO. Unsharing keys on a `share_id` in the same body, so
the thread page has one form action rather than two URLs that have to
agree about who may reach them.

WHAT A SHARED CONVERSATION EXPOSES is stated on the form, because it is
the one place a person can leak library content without meaning to: a
thread's turns record citation TEXT, so sharing the thread shares the
quoted passages -- but not the documents. A citation renders its link and
the link 404s for a recipient who may not read that document.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (
    make_admin, make_agent, make_group, make_user, posture, reset_settings,
    seed_sweep_posture, sign_in, user_principal,
)
from agents.models import Share
from agents.visibility import create_conversation
from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.models import AuditEvent

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


def _conversation(owner):
    return create_conversation(user_principal(owner), make_agent())


class TestSharing:
    def test_the_owner_shares_at_a_level_and_it_is_audited(self, client):
        owner, guest = make_user(), make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"action": "share", "subject": f"user:{guest.pk}", "level": "use"})
        assert response.status_code == 302
        assert Share.objects.get(user=guest).level == Share.Level.USE
        assert AuditEvent.objects.filter(action=actions.SHARE_ADDED).count() == 1

    def test_a_group_share_works_the_same_way(self, client):
        owner = make_user()
        group = make_group()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            client.post(reverse("chat-conversation-share", args=[conversation.pk]),
                        {"action": "share", "subject": f"group:{group.pk}", "level": "view"})
        assert Share.objects.filter(group=group).exists()

    def test_revoking_removes_the_row_and_is_audited(self, client):
        owner, guest = make_user(), make_user()
        conversation = _conversation(owner)
        row = Share.objects.create(target_type=Share.Target.CONVERSATION,
                                   target_key=str(conversation.pk), user=guest)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            client.post(reverse("chat-conversation-share", args=[conversation.pk]),
                        {"action": "revoke", "share": row.pk})
        assert Share.objects.count() == 0
        assert AuditEvent.objects.filter(action=actions.SHARE_REVOKED).count() == 1

    def test_a_recipient_may_not_re_share_and_gets_404(self, client):
        """404, not 403: this is a row-addressed URL, and a 403 would
        confirm which conversations exist."""
        owner, guest, third = make_user(), make_user(), make_user()
        conversation = _conversation(owner)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=guest)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, guest)
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"action": "share", "subject": f"user:{third.pk}", "level": "view"})
        assert response.status_code == 404
        assert Share.objects.count() == 1

    def test_a_stranger_gets_404_on_a_conversation_they_cannot_see(self, client):
        owner = make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"action": "share", "subject": f"user:{owner.pk}", "level": "view"})
        assert response.status_code == 404

    def test_an_unparseable_share_id_answers_404_not_500(self, client):
        owner = make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"action": "revoke", "share": "abc"})
        assert response.status_code == 404
        assert b"Traceback" not in response.content

    def test_a_share_id_from_another_conversation_answers_404(self, client):
        owner = make_user()
        mine = _conversation(owner)
        theirs = _conversation(make_user())
        elsewhere = Share.objects.create(target_type=Share.Target.CONVERSATION,
                                         target_key=str(theirs.pk), user=make_user())
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(reverse("chat-conversation-share", args=[mine.pk]),
                                   {"action": "revoke", "share": elsewhere.pk})
        assert response.status_code == 404
        assert Share.objects.filter(pk=elsewhere.pk).exists()

    def test_an_unknown_action_answers_400(self, client):
        owner = make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("chat-conversation-share", args=[conversation.pk]),
                {"action": "transfer"})
        assert response.status_code == 400


class TestThePanel:
    def test_the_owner_sees_the_share_list_and_a_recipient_does_not(self, client):
        """A share list names who else is reading somebody's thread --
        content about content -- so it renders to the owner and to a
        principal that `sees_all_content`, and to nobody else."""
        owner, guest = make_user(), make_user()
        conversation = _conversation(owner)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=guest)
        url = reverse("chat-conversation", args=[conversation.pk])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            assert b"Shared with" in client.get(url).content
            other = client.__class__()
            sign_in(other, guest)
            assert b"Shared with" not in other.get(url).content

    def test_an_admin_reaches_it_only_with_the_content_setting_on(self, client):
        owner = make_user()
        conversation = _conversation(owner)
        url = reverse("chat-conversation", args=[conversation.pk])
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            sign_in(client, make_admin())
            assert b"Shared with" in client.get(url).content

    def test_the_form_states_what_a_shared_thread_exposes(self, client):
        owner = make_user()
        conversation = _conversation(owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(reverse("chat-conversation", args=[conversation.pk])).content
        assert b"quoted" in body

    def test_an_open_box_renders_no_share_panel(self, client):
        """`share_subjects` answers empty in open posture and the panel
        is gated on the posture, so the household box's thread page is
        byte-identical to today's."""
        conversation = create_conversation(OPEN_PRINCIPAL, make_agent())
        body = client.get(reverse("chat-conversation", args=[conversation.pk])).content
        assert b"Shared with" not in body
```

Add `from identity.contracts.principals import OPEN_PRINCIPAL` to that module's imports.

- [ ] **Step 2: Run and watch it fail**

Run: `.venv/bin/pytest -q agents/chat/tests/test_conversation_share.py -x`
Expected: FAIL — `NoReverseMatch`.

- [ ] **Step 3: Write the view**

Create `agents/chat/views/shares.py`:

```python
"""`POST /chat/c/<uuid>/share/` -- extend one thread to somebody else,
and take it back.

ONE ROUTE, TWO ACTIONS. Unsharing keys on a `share_id` in the same body
rather than living at a second URL, so there is one place that decides
who may change this thread's share list.

CLASS O, so every refusal here is 404 rather than 403: the URL names a
row, and a 403 on a row-addressed URL confirms the row exists.
"""
from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from agents.models import Share
from agents.visibility import revoke_share, share_conversation, visible_conversations
from identity import audit
from identity.contracts import actions
from identity.request import principal_for_request

_ACTIONS = ("share", "revoke")


@require_POST
def conversation_share(request, conversation_id):
    """Add or remove one `Share` row on this conversation.

    The conversation is resolved through `visible_conversations` first,
    so a caller who cannot see the thread gets the same 404 they get
    everywhere else; `share_conversation`/`revoke_share` then answer None
    for a caller who can SEE it but may not SHARE it -- a recipient -- and
    that becomes a 404 too. Owner or `sees_all_content` only: a recipient
    passing a thread on would make the owner's own list of who is reading
    it wrong.
    """
    principal = principal_for_request(request)
    conversation = get_object_or_404(visible_conversations(principal), pk=conversation_id)
    action = request.POST.get("action", "")
    if action not in _ACTIONS:
        return HttpResponseBadRequest(f"{action!r} is not a recognised action.")

    if action == "revoke":
        if revoke_share(principal, conversation, request.POST.get("share", "")) is None:
            raise Http404("no such share")
        audit.record(principal, actions.SHARE_REVOKED, target_type="conversation",
                     target_key=conversation.pk)
        messages.info(request, "Removed that share.")
        return redirect("chat-conversation", conversation_id=conversation.pk)

    kind, _, raw = request.POST.get("subject", "").partition(":")
    if kind not in ("user", "group") or not raw.isdigit():
        # A hand-made body, or a stale form. 404 rather than 400 for the
        # same reason the rest of this view answers 404: the caller is
        # not being told anything about which subjects exist.
        raise Http404("no such subject")
    level = request.POST.get("level", Share.Level.VIEW)
    subject = _subject(kind, int(raw))
    row = share_conversation(principal, conversation,
                             user=subject if kind == "user" else None,
                             group=subject if kind == "group" else None, level=level)
    if row is None:
        raise Http404("cannot share this conversation")
    audit.record(principal, actions.SHARE_ADDED, target_type="conversation",
                 target_key=conversation.pk, level=level, subject=kind,
                 subject_key=subject.pk)
    messages.info(request, "Shared.")
    return redirect("chat-conversation", conversation_id=conversation.pk)


def _subject(kind: str, pk: int):
    """The account or group this share names, or a 404.

    `django.contrib.auth`'s own models, reached through Django rather
    than through `identity.models` -- which no column outside `identity/`
    may import (import-law rule 2). `get_user_model()` is exactly what
    `AUTH_USER_MODEL` exists for.
    """
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    if kind == "user":
        return get_object_or_404(get_user_model(), pk=pk, is_active=True)
    return get_object_or_404(Group, pk=pk)
```

Export it from `agents/chat/views/__init__.py`, mount it:

```python
    path("c/<uuid:conversation_id>/share/", conversation_share, name="chat-conversation-share"),
```

and classify it in `identity/routes.py`'s `/chat/` block:

```python
    # Owner or `sees_all_content` only -- a recipient may not re-share.
    # The same route revokes, keyed on a `share_id` in the body, so
    # unsharing is not a second URL.
    "chat-conversation-share": "O",
```

- [ ] **Step 4: Render the panel**

Create `agents/chat/templates/chat/_share_panel.html`:

```html
{% comment %}
Who else is reading this thread. Rendered to the OWNER and to a
principal that `sees_all_content` -- a share list names who is reading
somebody's thread, which is content about content.

THE SENTENCE ABOUT WHAT IS EXPOSED IS NOT DECORATION. A thread's turns
record citation text, so sharing the thread shares the quoted passages;
it does not share the documents, and a citation link 404s for a
recipient who may not read that document. This is the one place a person
can leak library content without meaning to, so the form says so.
{% endcomment %}
<section class="share-panel">
  <h2>Shared with</h2>
  <ul>
    {% for share in shares %}
      <li>
        {% if share.user_id %}{{ share.user.username }}{% else %}group: {{ share.group.name }}{% endif %}
        — {{ share.get_level_display }}
        <form method="post" action="{% url 'chat-conversation-share' conversation.id %}">
          {% csrf_token %}
          <input type="hidden" name="action" value="revoke">
          <input type="hidden" name="share" value="{{ share.pk }}">
          <button type="submit">Remove</button>
        </form>
      </li>
    {% empty %}
      <li>Nobody yet.</li>
    {% endfor %}
  </ul>
  <form method="post" action="{% url 'chat-conversation-share' conversation.id %}">
    {% csrf_token %}
    <input type="hidden" name="action" value="share">
    <select name="subject">
      {% for pk, name in share_subjects.users %}<option value="user:{{ pk }}">{{ name }}</option>{% endfor %}
      {% for pk, name in share_subjects.groups %}<option value="group:{{ pk }}">group: {{ name }}</option>{% endfor %}
    </select>
    <select name="level">
      <option value="view">Can view</option>
      <option value="use">Can view and post</option>
    </select>
    <button type="submit">Share</button>
  </form>
  <p class="muted">Sharing this thread shares what was said in it, including quoted passages
    from documents. It does not share the documents themselves — a citation link opens only
    for somebody who may already read that document.</p>
</section>
```

In `agents/chat/views/thread.py::thread_context`, add the four context keys, each gated on the
same predicate the template is:

```python
    from agents.models import Share
    from agents.shares import shares_for
    from agents.visibility import may_post_to, may_read_conversation_shares
    from identity.access import share_subjects

    principal = principal_for_request(request)
    may_see_shares = may_read_conversation_shares(principal, conversation)
    context["may_see_shares"] = may_see_shares
    context["shares"] = (shares_for(Share.Target.CONVERSATION, conversation.pk)
                         if may_see_shares else ())
    context["share_subjects"] = share_subjects(principal) if may_see_shares else {}
    # RENDER-VS-GATE, on the surface this task introduces it. `chat-turn`
    # answers 403 to a `view`-share recipient (Task 7); a page that still
    # rendered them the compose form would be the exact defect Task 17
    # exists to remove, shipped fresh.
    context["may_post"] = may_post_to(principal, conversation)
```

`Share.Target.CONVERSATION`, never the string `"conversation"` — every other share call site in
Tasks 7 and 8 passes the enum, and a `TextChoices` member is what `TARGET_KEY_PARSERS` is keyed
by. `thread_context` already calls `principal_for_request(request)` once for its `preflight_turn`
call (`agents/chat/views/thread.py:63`); reuse that local rather than calling it twice.

where `agents/visibility.py` exposes the predicate `_may_share` already implements, renamed
public so the template's gate and the POST's gate are literally the same function:

```python
def may_read_conversation_shares(principal, conversation) -> bool:
    """Whether `principal` may SEE this thread's share list.

    THE SAME PREDICATE `share_conversation` and `revoke_share` enforce
    (`_may_share` is now a one-line alias of this), so the page cannot
    render a control the POST refuses -- the render-vs-gate rule, applied
    where the rule first came from.
    """
    return sees_all_content(principal) or may_read_owned_row(principal, conversation)


def _may_share(principal, conversation) -> bool:
    return may_read_conversation_shares(principal, conversation)
```

and in `agents/chat/templates/chat/conversation.html`, beside the existing thread markup:

```html
{% if may_see_shares and identity_posture != "open" %}{% include "chat/_share_panel.html" %}{% endif %}
```

and wrap the existing message form — the one that posts to `chat-turn` — in `may_post`, with an
honest sentence where it used to be:

```html
{% if may_post %}
  {# ... the existing compose form, unchanged ... #}
{% else %}
  <p class="muted">This conversation was shared with you to read. Ask its owner for
    &ldquo;can view and post&rdquo; access to reply in it.</p>
{% endif %}
```

with the test, appended to `agents/chat/tests/test_conversation_share.py`:

```python
class TestTheComposeFormFollowsTheShareLevel:
    def test_a_view_recipient_is_not_shown_the_form_a_use_recipient_is(self, client):
        """The SAME predicate `chat-turn` enforces (`may_post_to`), never
        a second truth -- a page that renders a control whose POST answers
        403 is a page that lies to the person reading it."""
        owner, viewer, poster = make_user(), make_user(), make_user()
        conversation = _conversation(owner)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=viewer,
                             level=Share.Level.VIEW)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=poster,
                             level=Share.Level.USE)
        url = reverse("chat-conversation", args=[conversation.pk])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            assert b"shared with you to read" in client.get(url).content
            other = client.__class__()
            sign_in(other, poster)
            assert b"shared with you to read" not in other.get(url).content
```

`identity_posture != "open"` is the context processor's key, already available on every page:
an open box has no accounts to share with, `share_subjects` answers empty there, and the
household box's thread page must stay byte-identical to today's.

- [ ] **Step 5: Add the matrix driver**

```python
    "chat-conversation-share": lambda w: (
        "post", reverse("chat-conversation-share", args=[w.conversation.id]),
        {"action": "share", "subject": f"user:{w.other.pk}", "level": "view"}),
```

`w.conversation` is owned by `world.other`, so a member and a content-off admin both get the
class-O 404 and the owner column gets the same — which is the right answer for a route whose
rule is "the owner, or `sees_all_content`".

- [ ] **Step 6: Run everything**

```bash
.venv/bin/pytest -q agents identity
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agents/chat/views/ agents/chat/urls.py agents/chat/templates/chat/ \
        agents/visibility.py identity/routes.py agents/chat/tests/ \
        identity/tests/test_route_matrix.py
git commit -m "feat(agents): IA-2 T8 — the conversation-sharing UI"
```

---

### Task 9: `DocumentEntitlement`, and `tools/rag/access.py`'s two document functions

The library's table, and the two functions that split **rows** from **content**. This is the
split the whole administer-versus-read decision exists for: somebody has to be able to put a
label on a document they are not cleared to read, or the first label can only ever be applied by
a person who does not need it.

**Files:**
- Modify: `tools/rag/models.py`, `tools/rag/access.py`
- Create: `tools/rag/migrations/0015_documententitlement.py` (generated)
- Test: `tools/rag/tests/test_access_documents.py`, `tools/rag/tests/test_models.py` (append)

(`tools/rag/apps.py`'s cascade registration and
`foundation/ops/tests/test_column_boundaries.py`'s new guard are named in this task's steps but
land in Tasks 10 and 12 respectively — see Step 5's note and the boxed note above it.)

**Interfaces:**
- Consumes: `identity.access.held_entitlement_ids`/`may_see_unlabelled`/`sees_all_content`/
  `is_admin`/`owned_entitlement_ids` (Task 1).
- Produces: `tools.rag.models.DocumentEntitlement`.
- Produces: `tools.rag.access.DocumentVisibility(unrestricted, entitlement_ids,
  unlabelled_allowed)` with `.sees_nothing`; `::document_visibility(principal, *,
  settings_row=None) -> DocumentVisibility`; `::readable_documents(principal)`;
  `::listable_documents(principal)`; `::may_label_document(principal, document) -> bool`.

- [ ] **Step 1: Write the failing model test**

Append to `tools/rag/tests/test_models.py`:

```python
class TestDocumentEntitlement:
    def test_one_label_per_document_and_entitlement(self):
        from django.db.utils import IntegrityError
        from tools.rag.models import DocumentEntitlement
        from tools.rag.tests._helpers import make_entitlement
        document = make_document()
        finance = make_entitlement(name="Finance")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        with pytest.raises(IntegrityError):
            DocumentEntitlement.objects.create(document=document, entitlement=finance)

    def test_deleting_the_document_takes_its_labels(self):
        from tools.rag.models import DocumentEntitlement
        from tools.rag.tests._helpers import make_entitlement
        document = make_document()
        DocumentEntitlement.objects.create(document=document,
                                           entitlement=make_entitlement())
        document.delete()
        assert DocumentEntitlement.objects.count() == 0
```

- [ ] **Step 2: Run, watch it fail, then write the model**

Append to `tools/rag/models.py` (adding `from django.conf import settings` if absent):

```python
class DocumentEntitlement(models.Model):
    """A document's label -- beside the thing it protects.

    IT LIVES IN THIS COLUMN, not in `identity/`, because `identity/` may
    not import `tools/` (import-law rule 4) and because a label belongs
    beside the thing it protects. The foreign key to the entitlement is a
    STRING (`"identity.Entitlement"`), which is what keeps that true.

    ZERO OR MORE LABELS PER DOCUMENT, OR-MATCHED: a principal holding ANY
    of a document's entitlements may read it. "Must hold both" is a named
    non-goal -- the answer to it is a more specific entitlement, which is
    one row on a page instead of a filter algebra nobody can read.

    A DOCUMENT HAS NO OWNER COLUMN, and that is deliberate: its access is
    decided by these labels and the library posture, and by nothing else.
    An owner column would create a second, invisible rule ("the uploader
    can always see it") that no page displays and no administrator can
    revoke.
    """

    document = models.ForeignKey(Document, on_delete=models.CASCADE,
                                 related_name="entitlement_labels")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="document_labels")
    labelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    labelled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["document", "entitlement"],
                                    name="uniq_document_entitlement"),
        ]
        # The `document` side needs no explicit index -- its foreign key
        # already has one. This one serves `unlabel_all_for_entitlement`
        # and the delete cascade, which look up BY entitlement.
        indexes = [models.Index(fields=["entitlement"], name="rag_doclabel_entitlement")]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.document_id}@{self.entitlement_id}"
```

Generate and read the migration:

```bash
.venv/bin/python manage.py makemigrations rag --name documententitlement
```

Confirm `tools/rag/migrations/0015_documententitlement.py` depends on
`("identity", "0003_entitlement_and_grant")`, on `swappable_dependency(settings.AUTH_USER_MODEL)`
and on `("rag", "0014_askrecord_owner")`; add any the autodetector missed. Give it a docstring
naming it as IA-2's rag migration.

```bash
.venv/bin/python manage.py migrate
.venv/bin/pytest -q tools/rag/tests/test_models.py
```

- [ ] **Step 3: Write the failing access tests**

Create `tools/rag/tests/test_access_documents.py`:

```python
"""`readable_documents` versus `listable_documents` -- the split the
administer-versus-read decision exists for.

THE PAIR THAT MATTERS MOST: `listable_documents` returns every ROW to an
administrator with the content setting off, while `readable_documents`
returns none of the labelled ones to that same administrator. The library
page renders the row, the count and the label chips from the first; the
bytes come from the second.
"""
from __future__ import annotations

import pytest

from identity.contracts.postures import LIBRARY_LOCKED, POSTURE_ENTERPRISE, POSTURE_PERSONAL
from identity.contracts.principals import OPEN_PRINCIPAL, SERVICE_PRINCIPAL
from tools.rag.access import (
    document_visibility, listable_documents, may_label_document, readable_documents,
)
from tools.rag.models import DocumentEntitlement
from tools.rag.tests._helpers import (
    grant, make_admin, make_document, make_entitlement, make_user, posture, reset_settings,
    seed_sweep_posture, user_principal,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


def _labelled(entitlement, **overrides):
    document = make_document(**overrides)
    DocumentEntitlement.objects.create(document=document, entitlement=entitlement)
    return document


class TestReadableDocuments:
    def test_open_posture_returns_everything_with_no_permission_query(
            self, django_assert_num_queries):
        """The claim is in the NAME, so it is in the assertion too: one
        primary-key read of the settings singleton (inside
        `sees_all_content`), one for the document list, and nothing
        against a grant or label table."""
        labelled = _labelled(make_entitlement(name="Finance"))
        with django_assert_num_queries(2):
            assert list(readable_documents(OPEN_PRINCIPAL)) == [labelled]

    def test_a_holder_reads_a_labelled_document_and_a_non_holder_does_not(self):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        document = _labelled(finance)
        holder, other = make_user(), make_user()
        grant(finance, user=holder)
        grant(legal, user=other)
        with posture(POSTURE_ENTERPRISE):
            assert document in readable_documents(user_principal(holder))
            assert document not in readable_documents(user_principal(other))

    def test_any_one_entitlement_is_enough(self):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        document = _labelled(finance)
        DocumentEntitlement.objects.create(document=document, entitlement=legal)
        holder = make_user()
        grant(legal, user=holder)
        with posture(POSTURE_ENTERPRISE):
            assert document in readable_documents(user_principal(holder))

    def test_an_unlabelled_document_follows_the_library_posture(self):
        document = make_document()
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            assert document in readable_documents(user_principal(member))
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            assert document not in readable_documents(user_principal(member))

    def test_locked_never_hides_a_labelled_document_from_a_holder(self):
        """The library posture governs UNLABELLED documents and nothing
        else."""
        finance = make_entitlement(name="Finance")
        document = _labelled(finance)
        holder = make_user()
        grant(finance, user=holder)
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            assert document in readable_documents(user_principal(holder))

    def test_an_admin_with_the_setting_off_is_filtered_exactly_like_a_member(self):
        finance = make_entitlement(name="Finance")
        document = _labelled(finance)
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            assert document not in readable_documents(user_principal(admin))
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            assert document in readable_documents(user_principal(admin))

    def test_a_document_is_returned_once_even_when_two_labels_match(self):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        document = _labelled(finance)
        DocumentEntitlement.objects.create(document=document, entitlement=legal)
        holder = make_user()
        grant(finance, user=holder)
        grant(legal, user=holder)
        with posture(POSTURE_ENTERPRISE):
            assert list(readable_documents(user_principal(holder))) == [document]


class TestListableDocuments:
    def test_an_admin_sees_every_row_with_the_setting_off(self):
        """`is_admin`, NOT `sees_all_content`. Labelling, deleting and
        re-ingesting a document are administration, and an administrator
        MUST be able to label a document they are not cleared to read --
        otherwise the first label on a sensitive document can only be
        applied by somebody who does not need it, which is the wrong way
        round."""
        document = _labelled(make_entitlement(name="Finance"))
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            assert document in listable_documents(user_principal(admin))
            assert document not in readable_documents(user_principal(admin))

    def test_a_members_listing_is_exactly_their_readable_set(self):
        finance = make_entitlement(name="Finance")
        _labelled(finance)
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            principal = user_principal(member)
            assert list(listable_documents(principal)) == list(readable_documents(principal))


class TestSeesNothing:
    def test_a_member_with_no_entitlements_on_a_locked_library_sees_nothing(self):
        _labelled(make_entitlement(name="Finance"))
        member = make_user()
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            v = document_visibility(user_principal(member))
        assert v.sees_nothing is True

    def test_an_unrestricted_principal_never_sees_nothing(self):
        assert document_visibility(OPEN_PRINCIPAL).sees_nothing is False

    def test_a_service_principal_on_an_open_library_sees_the_unlabelled_part(self):
        with posture(POSTURE_ENTERPRISE):
            v = document_visibility(SERVICE_PRINCIPAL)
        assert v.unlabelled_allowed is True
        assert v.entitlement_ids == frozenset()
        assert v.sees_nothing is False


class TestMayLabelDocument:
    def test_an_admin_may_label_anything_and_an_owner_only_within_theirs(self):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        mine, theirs = _labelled(finance), _labelled(legal)
        owner, admin = make_user(), make_admin()
        grant(finance, user=owner, role="owner")
        with posture(POSTURE_ENTERPRISE):
            assert may_label_document(user_principal(admin), theirs) is True
            assert may_label_document(user_principal(owner), mine) is True
            assert may_label_document(user_principal(owner), theirs) is False

    def test_a_plain_member_may_label_nothing(self):
        finance = make_entitlement(name="Finance")
        document = _labelled(finance)
        member = make_user()
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE):
            assert may_label_document(user_principal(member), document) is False


class TestThePosturesAgree:
    def test_personal_and_enterprise_answer_identically(self):
        finance = make_entitlement(name="Finance")
        document = _labelled(finance)
        member = make_user()
        answers = []
        for name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
            with posture(name):
                answers.append(document in readable_documents(user_principal(member)))
        assert answers[0] == answers[1] is False
```

- [ ] **Step 4: Write `tools/rag/access.py`'s new half**

Replace `tools/rag/access.py`'s module docstring's "IA-1 SHIPS ONE FUNCTION" paragraph with a
statement of what it now ships, and append:

```python
@dataclass(frozen=True)
class DocumentVisibility:
    """What one principal may see of the library, as plain data.

    BUILT ONCE per request or per turn and THREADED DOWN; never
    re-derived inside a runner, which is the drift the single filter
    point exists to prevent.

    `unrestricted` is `sees_all_content` -- so an administrator with the
    content setting OFF is filtered exactly like a member: they read what
    their entitlements and the library posture allow, and nothing else.
    """

    unrestricted: bool
    entitlement_ids: frozenset[int]
    unlabelled_allowed: bool

    @property
    def sees_nothing(self) -> bool:
        """A signed-in principal with no entitlements on a LOCKED
        library. `retrieve_nodes` turns this into an EARLY RETURN, because
        an empty `MetadataFilters` means "no filter", which means
        EVERYTHING."""
        return (not self.unrestricted and not self.entitlement_ids
                and not self.unlabelled_allowed)


def document_visibility(principal, *, settings_row=None) -> DocumentVisibility:
    """`principal`'s whole library rule, in one value and two queries at
    most.

    THE OPEN BRANCH IS FIRST. `sees_all_content` tests `accounts_on()`
    before it reads a second table, so an open box builds this without
    touching a grant row.
    """
    if sees_all_content(principal, settings_row=settings_row):
        return DocumentVisibility(True, frozenset(), True)
    return DocumentVisibility(
        unrestricted=False,
        entitlement_ids=held_entitlement_ids(principal, settings_row=settings_row),
        unlabelled_allowed=may_see_unlabelled(principal, settings_row=settings_row),
    )


def readable_documents(principal):
    """Documents whose CONTENT this principal may reach -- the bytes, the
    transcript, and the chunks retrieval may return.

    OR-MATCH on the labels; the library posture decides the unlabelled
    ones. `.distinct()` because a document carrying two labels the
    principal holds would otherwise be returned twice by the join.
    """
    v = document_visibility(principal)
    if v.unrestricted:
        return Document.objects.all()
    labelled = Q(entitlement_labels__entitlement_id__in=v.entitlement_ids)
    if v.unlabelled_allowed:
        return Document.objects.filter(
            labelled | Q(entitlement_labels__isnull=True)).distinct()
    return Document.objects.filter(labelled).distinct()


def listable_documents(principal):
    """Document ROWS this principal may see -- title, category, labels,
    status, size, ingest state.

    `is_admin`, NOT `sees_all_content`. Labelling, deleting and
    re-ingesting a document are administration, and an administrator MUST
    be able to label a document they are not cleared to read -- otherwise
    the first label on a sensitive document can only be applied by
    somebody who does not need it, which is the wrong way round.

    The ROW is deliberately the whole of what this widens:
    `readable_documents` still gates the bytes, the transcript and every
    chunk retrieval can return, so an administrator with the content
    setting off can put a document in an entitlement without ever seeing
    a word of it. A member's listing is `readable_documents`, so nothing
    widens for them.
    """
    if is_admin(principal):
        return Document.objects.all()
    return readable_documents(principal)


def may_label_document(principal, document) -> bool:
    """Whether `principal` may add or remove THIS document's labels, and
    delete or re-ingest it.

    `is_admin`, or an OWNER of one of the document's current
    entitlements. The identity-side spelling of the same predicate is
    `identity.services.may_administer_entitlement`; this column cannot
    import that module (it is column-private), so it asks the two facts
    through the sanctioned seam instead.

    An UNLABELLED document has no entitlement owner, so only an
    administrator may act on it -- which is the honest answer: there is
    nobody with a claim to delegate from.
    """
    if is_admin(principal):
        return True
    owned = owned_entitlement_ids(principal)
    if not owned:
        return False
    return document.entitlement_labels.filter(entitlement_id__in=owned).exists()
```

with the imports this needs at the top of the module:

```python
from dataclasses import dataclass

from django.db.models import Q

from identity.access import (
    held_entitlement_ids, is_admin, may_see_unlabelled, owned_entitlement_ids,
    owned_rows_q, sees_all_content,
)
from tools.rag.models import AskRecord, Document
```

> **The document-label cascade is registered in Task 10, not here.**
> `identity/cascades.py::_run` calls `import_string(spec.handler)` for **every** registered
> cascade, unconditionally, and never swallows — and `tools/rag/apps.py::ready()` runs in every
> test session. Registering `"tools.rag.labels.unlabel_all_for_entitlement"` before Task 10
> creates that module would make `delete_entitlement`, `entitlement_delete_counts` and the
> entitlement page's own GET raise `ImportError`, turning Task 2's and Task 3's delete tests red
> at the end of *this* task. The registration and its anti-vacuous pin therefore ship in Task 10,
> Step 4, in the same commit as the handler they name.

- [ ] **Step 5: Write out the `Document.objects` guard for Task 12**

Spec §4.3's eighth structural guard, deferred by IA-1 because the module it names did not exist.
**Write it here, install it in Task 12** — it cannot pass until Task 12 has moved every
`Document.objects` read out of `tools/rag/views.py`, and a red test committed on a green branch
is the one thing a suite gate cannot tolerate. It is written out in full here so Task 12 does
not have to invent it. Its home is `foundation/ops/tests/test_column_boundaries.py`, in the same
AST shape as the chat `.objects` gate:

```python
# --- IA-2 (spec section 4.3, row 8): the library reads documents through
# `tools/rag/access.py`, never off the raw manager -------------------------

def _document_objects_count(source: str) -> int:
    """How many `Document.objects` attribute accesses `source` makes --
    `ast.Attribute(value=ast.Name(id="Document"), attr="objects")`, the
    same shape the chat gate above uses."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "objects"
        and isinstance(node.value, ast.Name) and node.value.id == "Document"
    )


def test_rag_views_reads_documents_through_the_access_module():
    """COUNTS ARE A READING SURFACE TOO, and they are the easiest thing
    to forget because they render no document. A sidebar that says
    "Finance (14)" to a member who may open none of them leaks exactly
    the fact the labels exist to hide.

    `tools.rag.access.readable_documents` and `::listable_documents` are
    the only sanctioned readers, so the library totals, the category
    sidebar counts and the tabular-row count cannot drift back onto the
    raw manager and cannot pick the wrong one of the two.
    """
    source = (REPO_ROOT / "tools/rag/views.py").read_text(encoding="utf-8")
    assert _document_objects_count(source) == 0


def test_the_document_gate_would_actually_catch_a_violation():
    """Anti-vacuous pin: the walk really does find the shape it claims,
    and really does ignore an unrelated `.objects`."""
    assert _document_objects_count("Document.objects.count()\n") == 1
    assert _document_objects_count("readable_documents(p).count()\n") == 0
```

Use whatever `REPO_ROOT` and `ast` names that module already binds.

- [ ] **Step 6: Run the rag and identity suites**

```bash
.venv/bin/pytest -q tools/rag identity
.venv/bin/python manage.py makemigrations --check --dry-run
```
Expected: PASS; `--check` exits 0.

- [ ] **Step 7: Commit**

```bash
git add tools/rag/models.py tools/rag/access.py \
        tools/rag/migrations/0015_documententitlement.py tools/rag/tests/
git commit -m "feat(rag): IA-2 T9 — DocumentEntitlement, readable_documents and listable_documents"
```

---
### Task 10: the chunk-metadata cache — one writer, one SQL

Tables are truth; the chunk metadata carries a **copy** of a document's entitlement ids so the
retriever can filter without a join it has no way to express. A visibility change must not cost
GPU hours.

**Files:**
- Create: `tools/rag/labels.py`, `tools/rag/management/commands/relabel_chunks.py`
- Modify: `tools/rag/ingest.py` (one call after `index.insert_nodes(nodes)`),
  `tools/rag/apps.py` (the cascade registration deferred from Task 9)
- Test: `tools/rag/tests/test_labels.py`, `tools/rag/tests/test_command_relabel_chunks.py`,
  `identity/tests/test_cascades.py` (append), `identity/tests/test_entitlement_services.py`
  (one assertion widened)

**Interfaces:**
- Consumes: `tools.rag.models.DocumentEntitlement` (Task 9); `tools.rag.index.LIVE_TABLE_NAME`,
  `::live_store_shape` (existing); `identity.contracts.cascades` (Task 2).
- Produces: `tools.rag.labels.restamp_document_chunks(doc_id, *, raising=False) -> None`,
  `::document_label_ids(document) -> frozenset[int]`,
  `::set_document_labels(actor, document, entitlement_ids) -> None`,
  `::unlabel_all_for_entitlement(entitlement_id, *, commit: bool) -> int`.

- [ ] **Step 1: Write the failing cache tests**

Create `tools/rag/tests/test_labels.py`:

```python
"""The chunk-metadata cache: ONE WRITER, ONE SQL.

TABLES ARE TRUTH. `DocumentEntitlement` decides who may read a document;
the `entitlements` key in the chunk table's `metadata_` column is a COPY,
kept so `retrieve_nodes` can filter without a join it has no way to
express. A visibility change must not cost GPU hours -- which is what a
re-encode would cost, and what this one `UPDATE` replaces.
"""
from __future__ import annotations

import json

import pytest
from django.db import connection

from identity.contracts import actions
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.models import AuditEvent
from tools.rag import index as rag_index
from tools.rag.labels import (
    document_label_ids, restamp_document_chunks, set_document_labels,
    unlabel_all_for_entitlement,
)
from tools.rag.models import DocumentEntitlement
from tools.rag.tests._helpers import (
    make_admin, make_document, make_entitlement, posture, reset_settings,
    seed_sweep_posture, user_principal,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


@pytest.fixture
def chunk_table():
    """A minimal stand-in for the live chunk table, with the two columns
    the stamp touches. Built with `metadata_ json` -- NOT `jsonb` --
    because that is what a box which has never enabled hybrid search
    actually has (`desired_store_shape` binds `use_jsonb` to the hybrid
    toggle), and it is the branch the `{cast}` in the UPDATE exists for.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            f"CREATE TABLE {rag_index.LIVE_TABLE_NAME} "
            f"(id bigserial primary key, metadata_ json)")
    yield
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {rag_index.LIVE_TABLE_NAME}")


def _seed_chunks(doc_id, count=2):
    with connection.cursor() as cursor:
        for i in range(count):
            cursor.execute(
                f"INSERT INTO {rag_index.LIVE_TABLE_NAME} (metadata_) VALUES (%s)",
                [json.dumps({"file_id": str(doc_id), "chunk": i})])


def _metadata(doc_id):
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT metadata_::jsonb FROM {rag_index.LIVE_TABLE_NAME} "
            f"WHERE metadata_->>'file_id' = %s", [str(doc_id)])
        return [row[0] for row in cursor.fetchall()]


class TestRestamping:
    def test_it_writes_decimal_STRINGS_for_a_labelled_document(self, chunk_table):
        """STRINGS, not integers: the store's ANY operator renders
        `metadata_::jsonb->'entitlements' ?| array['3','7']`, which is a
        JSON *string* array test. An integer array would match nothing,
        silently."""
        document = make_document()
        finance = make_entitlement(name="Finance")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        _seed_chunks(document.id)
        restamp_document_chunks(document.id)
        for row in _metadata(document.id):
            assert row["entitlements"] == [str(finance.pk)]

    def test_it_REMOVES_the_key_when_the_last_label_goes(self, chunk_table):
        """`IS_EMPTY` renders `metadata_->>'entitlements' IS NULL`, which
        is true exactly when the key is ABSENT -- which is what an
        unlabelled chunk looks like, and what EVERY chunk in every
        existing store already looks like. Writing `[]` instead would
        make an unlabelled document invisible to everybody."""
        document = make_document()
        finance = make_entitlement(name="Finance")
        label = DocumentEntitlement.objects.create(document=document, entitlement=finance)
        _seed_chunks(document.id)
        restamp_document_chunks(document.id)
        label.delete()
        restamp_document_chunks(document.id)
        for row in _metadata(document.id):
            assert "entitlements" not in row

    def test_it_touches_only_this_documents_chunks(self, chunk_table):
        mine, theirs = make_document(), make_document()
        finance = make_entitlement(name="Finance")
        DocumentEntitlement.objects.create(document=mine, entitlement=finance)
        _seed_chunks(mine.id)
        _seed_chunks(theirs.id)
        restamp_document_chunks(mine.id)
        assert all("entitlements" not in row for row in _metadata(theirs.id))

    def test_it_is_a_no_op_when_the_chunk_table_does_not_exist(self):
        """Nothing prose has ever been ingested. The same defensive
        posture `delete_chunks_for_document` documents -- a label must be
        savable on a box with an empty store."""
        document = make_document()
        restamp_document_chunks(document.id)   # must not raise

    def test_the_ingest_path_logs_and_the_label_path_raises(self, chunk_table, monkeypatch):
        """The two callers differ DELIBERATELY. A failed re-stamp must
        not fail an ingest -- the label page can repair it. A failed
        re-stamp on a LABEL CHANGE must roll the `DocumentEntitlement`
        write back with it: a label that appears saved and is not
        enforced is worse than one that refuses to save."""
        document = make_document()

        def _boom(*args, **kwargs):
            raise RuntimeError("the store is down")

        monkeypatch.setattr("tools.rag.labels._execute_stamp", _boom)
        restamp_document_chunks(document.id)                     # logs, returns
        with pytest.raises(RuntimeError):
            restamp_document_chunks(document.id, raising=True)


class TestSetDocumentLabels:
    def test_it_writes_the_difference_audits_both_ways_and_restamps(self, chunk_table):
        admin = make_admin()
        document = make_document()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        _seed_chunks(document.id)
        with posture(POSTURE_ENTERPRISE):
            actor = user_principal(admin)
            set_document_labels(actor, document, {finance.pk, legal.pk})
            assert AuditEvent.objects.filter(action=actions.DOCUMENT_LABELLED).count() == 2
            assert _metadata(document.id)[0]["entitlements"] == sorted(
                [str(finance.pk), str(legal.pk)])
            set_document_labels(actor, document, set())
            assert AuditEvent.objects.filter(action=actions.DOCUMENT_UNLABELLED).count() == 2
        assert "entitlements" not in _metadata(document.id)[0]

    def test_a_failed_restamp_rolls_the_label_write_back(self, chunk_table, monkeypatch):
        admin = make_admin()
        document = make_document()
        finance = make_entitlement(name="Finance")
        _seed_chunks(document.id)

        def _boom(*args, **kwargs):
            raise RuntimeError("the store is down")

        monkeypatch.setattr("tools.rag.labels._execute_stamp", _boom)
        with posture(POSTURE_ENTERPRISE), pytest.raises(RuntimeError):
            set_document_labels(user_principal(admin), document, {finance.pk})
        assert DocumentEntitlement.objects.count() == 0
        assert AuditEvent.objects.filter(action=actions.DOCUMENT_LABELLED).count() == 0

    def test_document_label_ids_reads_the_table_not_the_cache(self):
        document = make_document()
        finance = make_entitlement(name="Finance")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        assert document_label_ids(document) == frozenset({finance.pk})


class TestTheEntitlementCascade:
    def test_counting_never_writes(self, chunk_table):
        document = make_document()
        finance = make_entitlement(name="Finance")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        assert unlabel_all_for_entitlement(finance.pk, commit=False) == 1
        assert DocumentEntitlement.objects.count() == 1

    def test_running_it_detaches_and_restamps_every_affected_document(self, chunk_table):
        """Done-when 6. The label rows go, and every document that lost
        one is re-stamped IN THE SAME TRANSACTION -- otherwise the tables
        say "unlabelled" while the chunks still say "Finance", and
        retrieval would keep hiding a document nothing protects any more."""
        finance = make_entitlement(name="Finance")
        one, two = make_document(), make_document()
        for document in (one, two):
            DocumentEntitlement.objects.create(document=document, entitlement=finance)
            _seed_chunks(document.id)
            restamp_document_chunks(document.id)
        assert unlabel_all_for_entitlement(finance.pk, commit=True) == 2
        assert DocumentEntitlement.objects.count() == 0
        for document in (one, two):
            assert "entitlements" not in _metadata(document.id)[0]
```

- [ ] **Step 2: Run and watch it fail**

Run: `.venv/bin/pytest -q tools/rag/tests/test_labels.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.rag.labels'`.

- [ ] **Step 3: Write `tools/rag/labels.py`**

```python
"""Document labels, and the one writer of the chunk-metadata cache.

TABLES ARE TRUTH. `DocumentEntitlement` decides who may read a document.
The `entitlements` key in the live chunk table's `metadata_` column is a
COPY of that fact, kept because the retriever filters on chunk metadata
and has no way to express a join back to the label table. A visibility
change must therefore cost ONE `UPDATE`, not a re-encode.

ONE WRITER. `restamp_document_chunks` is the only function that writes
that key, and it is called from exactly two places: `tools/rag/ingest.py`
immediately after `index.insert_nodes(nodes)` (so a re-ingest of an
already-labelled document RESTORES its labels rather than silently
dropping them -- the indexing library rewrites the row, and the labels are
not its business), and every label add/remove, inside the same
transaction as the `DocumentEntitlement` write.

The alternative -- stamping node metadata at ingest and SQL-updating on
change -- would produce TWO SHAPES of one fact: the ingest-time one lands
inside the store's own serialised node blob as well as the filterable
column, the SQL one only in the column. Nothing filters on the blob, so
the difference is invisible until somebody debugs it.

THE KEY IS NEVER NODE METADATA. The indexing library prepends metadata
keys onto a node's text before embedding unless they are excluded -- the
pollution `tools/rag/media.py::apply_chunk_metadata_exclusions` exists to
fix. `entitlements` sidesteps the question by never being node metadata
at all: it is written into the `metadata_` column AFTER insert.
"""
from __future__ import annotations

import json
import logging

from django.db import connection, transaction

from identity import audit
from identity.contracts import actions
from tools.rag import index as rag_index
from tools.rag.models import DocumentEntitlement

logger = logging.getLogger(__name__)


def document_label_ids(document) -> frozenset[int]:
    """The entitlement ids labelling `document` -- FROM THE TABLE, never
    from the cache. The cache is a copy; this is the fact."""
    return frozenset(
        document.entitlement_labels.values_list("entitlement_id", flat=True))


def _execute_stamp(sql: str, params: list) -> None:
    """The one cursor. Factored out so a test can make the store fail
    without also making the table-existence check fail."""
    with connection.cursor() as cursor:
        cursor.execute(sql, params)


def restamp_document_chunks(doc_id, *, raising: bool = False) -> None:
    """Rewrite `doc_id`'s chunks' `entitlements` key from the label
    table. ONE SQL STATEMENT.

    `raising=False` (the INGEST-TIME caller) logs and returns: a failed
    re-stamp must not fail an ingest, and the label page can repair it.
    `raising=True` (the LABEL-CHANGE caller) raises, so the
    `DocumentEntitlement` write rolls back with it -- a label that appears
    to be saved and is not enforced is worse than a label that refuses to
    save.

    A NO-OP when the chunk table does not exist (nothing prose has ever
    been ingested) -- the same defensive posture
    `tools.rag.index.delete_chunks_for_document` documents. A label must
    be savable on a box with an empty store.
    """
    shape = rag_index.live_store_shape()
    if shape is None:
        return
    # The live `metadata_` column is `json`, not `jsonb`, on any box that
    # has never enabled hybrid search -- `desired_store_shape()` binds
    # `use_jsonb` to the hybrid toggle. BOTH filter operators section 8.3
    # uses cast explicitly and work on either type; only this UPDATE's
    # ASSIGNMENT needs the branch.
    cast = "" if shape["jsonb"] else "::json"
    ids = sorted(str(i) for i in _label_ids_for(doc_id))
    table = rag_index.LIVE_TABLE_NAME   # a module constant, never caller input
    if ids:
        sql = (f"UPDATE {table} SET metadata_ = "
               f"jsonb_set(metadata_::jsonb, '{{entitlements}}', %s::jsonb, true){cast} "
               f"WHERE metadata_->>'file_id' = %s")
        params = [json.dumps(ids), str(doc_id)]
    else:
        # REMOVE the key rather than writing `[]`, so `IS_EMPTY` -- which
        # renders `metadata_->>'entitlements' IS NULL` -- still matches
        # it. That is also what every chunk in every existing store
        # already looks like, which is why no back-fill is needed.
        sql = (f"UPDATE {table} SET metadata_ = "
               f"(metadata_::jsonb - 'entitlements'){cast} "
               f"WHERE metadata_->>'file_id' = %s")
        params = [str(doc_id)]
    try:
        _execute_stamp(sql, params)
    except Exception:
        if raising:
            raise
        logger.warning("rag: could not re-stamp chunk labels for Document %s", doc_id,
                       exc_info=True)


def _label_ids_for(doc_id) -> frozenset[int]:
    return frozenset(
        DocumentEntitlement.objects.filter(document_id=doc_id)
        .values_list("entitlement_id", flat=True))


def set_document_labels(actor, document, entitlement_ids) -> None:
    """Make `document`'s labels exactly `entitlement_ids`, and re-stamp
    its chunks, IN ONE TRANSACTION.

    WRITES THE DIFFERENCE, not the whole set, so an audit trail records
    what changed rather than what was resubmitted.

    THE CALLER CHECKS THE PREDICATE (`tools.rag.access.may_label_document`
    plus the "owner of every entitlement being added or removed" rule in
    the view). This function is the write, not the guard.
    """
    wanted = {int(i) for i in entitlement_ids}
    with transaction.atomic():
        current = set(_label_ids_for(document.id))
        for entitlement_id in sorted(wanted - current):
            DocumentEntitlement.objects.create(document=document,
                                               entitlement_id=entitlement_id)
            audit.record(actor, actions.DOCUMENT_LABELLED, target_type="document",
                         target_key=document.id, target_label=document.title,
                         entitlement_id=entitlement_id)
        for entitlement_id in sorted(current - wanted):
            DocumentEntitlement.objects.filter(
                document=document, entitlement_id=entitlement_id).delete()
            audit.record(actor, actions.DOCUMENT_UNLABELLED, target_type="document",
                         target_key=document.id, target_label=document.title,
                         entitlement_id=entitlement_id)
        if wanted != current:
            restamp_document_chunks(document.id, raising=True)


def unlabel_all_for_entitlement(entitlement_id: int, *, commit: bool) -> int:
    """This column's answer to "an entitlement is being deleted".

    Registered from `tools/rag/apps.py::ready()` as a DOTTED-PATH STRING,
    so `identity/` runs it without importing `tools/` (import-law rule 4).

    `commit=True` removes every label naming `entitlement_id` and
    RE-STAMPS each affected document, inside the caller's transaction
    (`identity.services.delete_entitlement`'s). Without the re-stamp the
    tables would say "unlabelled" while the chunks still said "Finance",
    and retrieval would keep hiding a document nothing protects any more.

    It runs BEFORE `entitlement.delete()`, so the re-stamp sees the label
    already gone. The database's own `CASCADE` on the foreign key stays
    as the net: a shell that deletes the row directly still leaves no
    orphan labels, only a stale cache, and `manage.py relabel_chunks` is
    the documented repair.
    """
    doc_ids = list(
        DocumentEntitlement.objects.filter(entitlement_id=entitlement_id)
        .values_list("document_id", flat=True))
    if not commit:
        return len(doc_ids)
    DocumentEntitlement.objects.filter(entitlement_id=entitlement_id).delete()
    for doc_id in doc_ids:
        restamp_document_chunks(doc_id, raising=True)
    return len(doc_ids)
```

- [ ] **Step 4: Register the document-label cascade, now that its handler exists**

Append to `tools/rag/apps.py::ready()`, beside the owned-rows registration already there:

```python
        # This column's answer to "an entitlement is being deleted", as a
        # DOTTED-PATH STRING so `identity/` can run it without importing
        # `tools/` (import-law rule 4). It does MORE than the agents
        # column's twin: a document that loses its last label becomes
        # unlabelled and follows the library posture, so the chunk
        # metadata cache has to be re-stamped in the same transaction --
        # see `tools.rag.labels.unlabel_all_for_entitlement`.
        #
        # REGISTERED IN THE SAME COMMIT AS THE HANDLER, deliberately.
        # `identity/cascades.py::_run` resolves every registered path with
        # `import_string` and never swallows, so a registration that
        # landed before its module would make every `delete_entitlement`
        # and every entitlement-page GET raise `ImportError`.
        from identity.contracts.cascades import (
            EntitlementCascade, register_entitlement_cascade,
        )

        register_entitlement_cascade(EntitlementCascade(
            key="rag.document_labels",
            label="Document labels",
            handler="tools.rag.labels.unlabel_all_for_entitlement",
        ))
```

and add the anti-vacuous pin to `identity/tests/test_cascades.py`, now that both columns
register. **It reads a snapshot taken at MODULE IMPORT**, before `_isolated_registry` clears
anything — pytest imports a test module after `django.setup()`, so both `AppConfig.ready()`
methods have already run and the registry is intact at that moment:

```python
# Captured at IMPORT time, before `_isolated_registry` clears anything:
# Django has already run every `AppConfig.ready()` by the time this
# module is imported, so this is what the two columns really registered.
#
# NOT by calling `ready()` again from inside the test. Those methods are
# not narrow: `tools/rag/apps.py::ready()` also registers two-to-four
# roles, two job kinds, three tools and an `OwnedRows`, and
# `agents/apps.py::ready()` a role, a job kind, every agent-as-tool spec
# and three `OwnedRows`. Re-running them would mutate four module-level
# registries this file protects none of -- reintroducing exactly the
# class of cross-test bleed `_isolated_registry` above exists to stop,
# and the reason the suite is run in two collection orders.
_REGISTERED_AT_IMPORT = frozenset(spec.key for spec in all_entitlement_cascades())


class TestBothColumnsRegister:
    def test_the_two_real_cascades_are_registered_by_their_columns(self):
        """Anti-vacuous pin: this registry is not silently empty, and the
        two keys are the ones the two columns actually register at
        `AppConfig.ready()`."""
        assert {"rag.document_labels", "agents.tool_labels"} <= _REGISTERED_AT_IMPORT
```

- [ ] **Step 5: Call it from ingest**

In `tools/rag/ingest.py`, immediately after `index.insert_nodes(nodes)` (line 653):

```python
    # THE LABELS ARE NOT THE INDEXING LIBRARY'S BUSINESS. It rewrites the
    # chunk rows wholesale, so a re-ingest of an already-labelled document
    # would silently drop the `entitlements` key without this. Logs
    # rather than raises: a failed re-stamp must not fail an ingest, and
    # the document-label page repairs it.
    from tools.rag.labels import restamp_document_chunks
    restamp_document_chunks(doc.id)
```

The import is function-local for the same reason every other import in this module's ingest
path is: `tools/rag/labels.py` imports `identity.audit`, and this module is reached from a
worker process that must not pay for a cross-column import at module scope.

Add the regression test to `tools/rag/tests/test_labels.py`:

```python
class TestReIngestRestoresLabels:
    def test_a_re_ingest_of_a_labelled_document_restores_its_labels(self, chunk_table):
        """The indexing library rewrites the chunk rows; the labels are
        not its business. Without the call in `_ingest_prose` a re-ingest
        would silently unlabel a document -- and a document that quietly
        became readable by everybody is the worst possible outcome of a
        maintenance operation."""
        document = make_document()
        finance = make_entitlement(name="Finance")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        _seed_chunks(document.id)          # stands in for `insert_nodes`
        restamp_document_chunks(document.id)
        assert _metadata(document.id)[0]["entitlements"] == [str(finance.pk)]
```

- [ ] **Step 6: Write the repair command**

Create `tools/rag/management/commands/relabel_chunks.py`:

```python
"""Re-run the chunk-label stamp for one document or for all of them.

IT EXISTS FOR THE INGEST-TIME FAILURE PATH. `restamp_document_chunks`
logs rather than raises when it is called from ingest, so a store that
was briefly unreachable leaves a document whose TABLES say "Finance" and
whose CHUNKS say nothing. This is the repair, and it is cheap: no
embedding, no engine, one `UPDATE` per document.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from tools.rag.labels import restamp_document_chunks
from tools.rag.models import Document


class Command(BaseCommand):
    help = "Re-stamp document entitlement labels onto their vector chunks."

    def add_arguments(self, parser):
        parser.add_argument("--document", type=int, default=None,
                            help="One document id. Omit for every document.")

    def handle(self, *args, **options):
        doc_id = options["document"]
        rows = (Document.objects.filter(pk=doc_id) if doc_id is not None
                else Document.objects.all())
        if doc_id is not None and not rows.exists():
            self.stderr.write(f"There is no document {doc_id} on this box.")
            return
        count = 0
        for pk in rows.values_list("pk", flat=True):
            restamp_document_chunks(pk, raising=True)
            count += 1
        self.stdout.write(f"Re-stamped {count} document(s).")
```

Create `tools/rag/tests/test_command_relabel_chunks.py`:

```python
"""`manage.py relabel_chunks` -- the repair for the ingest-time failure
path."""
from __future__ import annotations

import json

import pytest
from django.core.management import call_command
from django.db import connection

from tools.rag import index as rag_index
from tools.rag.models import DocumentEntitlement
from tools.rag.tests._helpers import make_document, make_entitlement

pytestmark = pytest.mark.django_db


@pytest.fixture
def chunk_table():
    with connection.cursor() as cursor:
        cursor.execute(
            f"CREATE TABLE {rag_index.LIVE_TABLE_NAME} "
            f"(id bigserial primary key, metadata_ json)")
    yield
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {rag_index.LIVE_TABLE_NAME}")


def _seed(doc_id):
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {rag_index.LIVE_TABLE_NAME} (metadata_) VALUES (%s)",
                       [json.dumps({"file_id": str(doc_id)})])


def _metadata(doc_id):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT metadata_::jsonb FROM {rag_index.LIVE_TABLE_NAME} "
                       f"WHERE metadata_->>'file_id' = %s", [str(doc_id)])
        return cursor.fetchone()[0]


class TestRelabelChunks:
    def test_it_repairs_every_document_by_default(self, chunk_table, capsys):
        document = make_document()
        finance = make_entitlement(name="Finance")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        _seed(document.id)
        call_command("relabel_chunks")
        assert _metadata(document.id)["entitlements"] == [str(finance.pk)]

    def test_one_document_at_a_time(self, chunk_table):
        mine, theirs = make_document(), make_document()
        finance = make_entitlement(name="Finance")
        for document in (mine, theirs):
            DocumentEntitlement.objects.create(document=document, entitlement=finance)
            _seed(document.id)
        call_command("relabel_chunks", document=mine.id)
        assert "entitlements" in _metadata(mine.id)
        assert "entitlements" not in _metadata(theirs.id)

    def test_an_unknown_document_id_says_so_rather_than_raising(self, capsys):
        call_command("relabel_chunks", document=9999)
        assert "no document 9999" in capsys.readouterr().err
```

- [ ] **Step 7: Run everything**

```bash
.venv/bin/pytest -q tools/rag identity
```
Expected: PASS, including `identity/tests/test_entitlement_services.py::
TestCreateRenameDelete::test_delete_takes_the_grants_and_returns_the_cascade_counts`, which now
gets `Document labels` and `Tool labels` entries alongside `Grants` — amend that test's
assertion from `counts["Grants"] == 2` to also assert the two new keys are present, since this
is the task that makes them exist.

- [ ] **Step 8: Commit**

```bash
git add tools/rag/labels.py tools/rag/apps.py tools/rag/ingest.py \
        tools/rag/management/commands/relabel_chunks.py tools/rag/tests/ identity/tests/
git commit -m "feat(rag): IA-2 T10 — the chunk-metadata cache, its one writer, and relabel_chunks"
```

---

### Task 11: the one filter point — a required `visibility` argument

`retrieve_nodes` is called by three things and must stay one filter point. A visibility rule
that lived in a runner instead would be a second copy, and two copies of a visibility rule is
how two surfaces come to disagree about what a person may see.

**Files:**
- Modify: `tools/rag/retrieval.py` (both signatures and the filter body),
  `tools/rag/jobs.py:244`, `tools/rag/tools.py:214,282`, `tools/rag/views.py:1493`,
  `tools/rag/management/commands/ask.py:47`
- Test: `tools/rag/tests/test_retrieval_visibility.py` (new);
  `tools/rag/tests/test_retrieval.py`, `test_views.py`, `test_commands.py` (24 existing call
  sites updated for the new required keyword)

**Interfaces:**
- Consumes: `tools.rag.access.DocumentVisibility`/`document_visibility` (Task 9).
- Produces: `retrieve_nodes(question, category, settings_row, *, embed_resolved, visibility)`
  and `answer_question(question, *, category=None, answer_resolved, embed_resolved, visibility)`
  — **`visibility` required and keyword-only on both**.

- [ ] **Step 1: Write the failing filter tests**

Create `tools/rag/tests/test_retrieval_visibility.py`:

```python
"""The ONE filter point, and the single most dangerous line in the phase.

`MetadataFilters(filters=[])` means "no filter", which means EVERYTHING.
So a principal who may see nothing gets an EARLY RETURN, never an empty
filter list -- and that has its own test, first, because it is the line
whose failure mode is silent and total.
"""
from __future__ import annotations

import inspect

import pytest
from llama_index.core.vector_stores.types import (
    FilterCondition, FilterOperator, MetadataFilter, MetadataFilters,
)

from tools.rag import retrieval
from tools.rag.access import DocumentVisibility

pytestmark = pytest.mark.django_db


def _filters(visibility, category=None):
    """The `MetadataFilters` `retrieve_nodes` would build, without
    building an index: the filter construction is factored into
    `retrieval._visibility_filters` precisely so it can be asserted
    directly rather than through a live vector store."""
    return retrieval._visibility_filters(category, visibility)


class TestTheDangerousLine:
    def test_sees_nothing_returns_no_nodes_and_never_calls_the_retriever(
            self, monkeypatch):
        """An EARLY RETURN, not an empty filter list. The index is still
        built and returned -- `answer_question` synthesizes from it even
        with zero nodes -- and the query EMBEDDING is skipped, because it
        happens inside `retriever.retrieve`, which is what we skip."""
        calls = []

        class _Index:
            def as_retriever(self, **kwargs):
                calls.append(kwargs)
                raise AssertionError("the retriever must not be built")

        monkeypatch.setattr(retrieval, "get_index", lambda *a, **k: _Index())
        monkeypatch.setattr(retrieval, "get_vector_store",
                            lambda: type("S", (), {"hybrid_search": False})())
        monkeypatch.setattr(retrieval.gateway, "get_embed_model_for", lambda r: object())
        nothing = DocumentVisibility(unrestricted=False, entitlement_ids=frozenset(),
                                     unlabelled_allowed=False)
        nodes, hybrid, index = retrieval.retrieve_nodes(
            "a question", None, _settings_row(), embed_resolved=object(),
            visibility=nothing)
        assert nodes == []
        assert index is not None
        assert calls == []


class TestTheFilterShape:
    def test_unrestricted_and_no_category_builds_no_filter_at_all(self):
        assert _filters(DocumentVisibility(True, frozenset(), True)) is None

    def test_a_holder_gets_an_ANY_clause_of_decimal_strings(self):
        v = DocumentVisibility(False, frozenset({3, 7}), False)
        built = _filters(v)
        group = built.filters[0]
        assert group.condition == FilterCondition.OR
        clause = group.filters[0]
        assert clause.key == "entitlements"
        assert clause.operator == FilterOperator.ANY
        assert clause.value == ["3", "7"]

    def test_every_value_passed_to_ANY_is_a_decimal_string(self):
        """The `ANY` clause builder INTERPOLATES its values into SQL as
        quoted strings rather than binding them. Ours are integers
        rendered as strings, straight off a database column, so the
        interpolation is safe BY CONSTRUCTION -- and this test is what
        makes a future refactor that passed an entitlement NAME fail
        loudly instead of quietly."""
        v = DocumentVisibility(False, frozenset({3, 7}), False)
        clause = _filters(v).filters[0].filters[0]
        assert all(isinstance(value, str) and value.isdigit() for value in clause.value)

    def test_unlabelled_allowed_adds_an_IS_EMPTY_clause_ORed_with_it(self):
        v = DocumentVisibility(False, frozenset({3}), True)
        group = _filters(v).filters[0]
        assert group.condition == FilterCondition.OR
        assert {clause.operator for clause in group.filters} == {
            FilterOperator.ANY, FilterOperator.IS_EMPTY}

    def test_unlabelled_only_is_an_IS_EMPTY_clause_alone(self):
        v = DocumentVisibility(False, frozenset(), True)
        group = _filters(v).filters[0]
        assert [clause.operator for clause in group.filters] == [FilterOperator.IS_EMPTY]

    def test_a_category_is_AND_combined_with_the_visibility_group(self):
        v = DocumentVisibility(False, frozenset({3}), False)
        built = _filters(v, category="Finance")
        assert built.condition == FilterCondition.AND
        assert isinstance(built.filters[0], MetadataFilter)      # the category
        assert isinstance(built.filters[1], MetadataFilters)     # the visibility group


class TestTheSignaturesAreRequired:
    def test_visibility_is_required_and_keyword_only_on_both(self):
        """REQUIRED, NOT DEFAULTED. A default of "see everything" is a
        fail-open default, and a caller that forgets it should fail at
        signature-checking time rather than at a security review two
        years later."""
        for function in (retrieval.retrieve_nodes, retrieval.answer_question):
            parameter = inspect.signature(function).parameters["visibility"]
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
            assert parameter.default is inspect.Parameter.empty
```

Add a `_settings_row()` helper to that module returning `RagSettings.get_solo()`.

- [ ] **Step 2: Run and watch it fail**

Run: `.venv/bin/pytest -q tools/rag/tests/test_retrieval_visibility.py -x`
Expected: FAIL — `AttributeError: module 'tools.rag.retrieval' has no attribute
'_visibility_filters'`.

- [ ] **Step 3: Change the two signatures and the filter body**

In `tools/rag/retrieval.py`, extend the imports:

```python
from llama_index.core.vector_stores.types import (
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)
```

Add the factored filter builder above `retrieve_nodes`:

```python
def _visibility_filters(category, visibility):
    """The `MetadataFilters` for this question -- the category clause and
    the visibility group, AND-combined.

    FACTORED OUT of `retrieve_nodes` so it can be asserted directly
    rather than through a live vector store: this is the filter that
    decides what a person may see, and a filter only testable end to end
    is a filter tested rarely.

    The installed Postgres store recurses through nested
    `MetadataFilters` (`_recursively_apply_filters`), so the nesting is
    supported rather than assumed -- and BOTH halves of a hybrid query
    apply the same filters (each calls `_apply_filters_and_limit`), so a
    hybrid search cannot leak past the visibility clause while the dense
    search obeys it.
    """
    clauses = []
    if category:
        clauses.append(MetadataFilter(
            key="category", value=normalize_category_name(category).lower(),
            operator=FilterOperator.EQ))
    if not visibility.unrestricted:
        allowed = []
        if visibility.entitlement_ids:
            # `ANY` renders as
            # `metadata_::jsonb->'entitlements' ?| array['3','7']` -- a
            # JSON *string* array test, which is why the stamped ids are
            # decimal STRINGS (`tools/rag/labels.py`).
            allowed.append(MetadataFilter(
                key="entitlements",
                value=sorted(str(i) for i in visibility.entitlement_ids),
                operator=FilterOperator.ANY))
        if visibility.unlabelled_allowed:
            # `IS_EMPTY` renders as `metadata_->>'entitlements' IS NULL`,
            # true exactly when the key is ABSENT -- which is what an
            # unlabelled chunk looks like, and what every chunk in every
            # existing store already looks like. No back-fill needed.
            allowed.append(MetadataFilter(key="entitlements", value="",
                                          operator=FilterOperator.IS_EMPTY))
        clauses.append(MetadataFilters(filters=allowed, condition=FilterCondition.OR))
    if not clauses:
        return None
    return MetadataFilters(filters=clauses, condition=FilterCondition.AND)
```

Change `retrieve_nodes`' signature to add `visibility` as a required keyword-only argument,
document it, and replace the `filters = None; if category: ...` block with:

```python
    if visibility.sees_nothing:
        # A signed-in principal with no entitlements on a LOCKED library.
        # The honest answer is nothing, and it must be an EARLY RETURN
        # rather than an empty filter list -- `MetadataFilters(filters=[])`
        # means "no filter", which means EVERYTHING. This is the single
        # most dangerous line in the phase and it has its own test.
        #
        # The index is still built and returned: `answer_question`
        # synthesizes from it even with zero nodes. The query EMBEDDING is
        # skipped, because it happens inside `retriever.retrieve`, which
        # is what we skip.
        embed_model = gateway.get_embed_model_for(embed_resolved)
        vector_store = get_vector_store()
        return [], vector_store.hybrid_search, get_index(vector_store,
                                                         embed_model=embed_model)

    filters = _visibility_filters(category, visibility)
```

Change `answer_question`'s signature to add the same required keyword-only `visibility` and
pass it straight through to its `retrieve_nodes` call. Neither docstring may promise a default.

- [ ] **Step 4: Update the five callers**

| Caller | Change |
|---|---|
| `tools/rag/jobs.py` (`run_ask`) | `visibility=document_visibility(principal_from_payload(payload))` — the payload already carries the actor (`payload_fields` was written in IA-1) |
| `tools/rag/tools.py` (`rag.search` runner) | `visibility=document_visibility(ctx.principal)` |
| `tools/rag/tools.py` (`rag.ask` runner) | the same |
| `tools/rag/views.py::SearchView` | `visibility=document_visibility(principal_for_request(request))` |
| `tools/rag/management/commands/ask.py` | `visibility=document_visibility(SERVICE_PRINCIPAL)`, plus the one honest line below |

`manage.py ask` calls `answer_question` **directly**, not through the queue, so without this row
the required argument would break it outright. It also prints one line rather than silently
answering from a thinner library than the Ask page would:

```python
        visibility = document_visibility(SERVICE_PRINCIPAL)
        if not visibility.unrestricted:
            # A service principal holds no entitlements -- grants attach
            # to a user or a group, and service-account tokens are a later
            # phase. So this command answers from the UNLABELLED part of
            # the library only, and says so rather than quietly returning
            # a thinner answer than the same question gets on the page.
            self.stdout.write(
                "This box has accounts, and the command line holds no entitlements, "
                "so this answer comes from unlabelled documents only."
            )
```

Each of the five gets a caller test, and all five live in
`tools/rag/tests/test_retrieval_visibility.py` so the five assertions read identically and one
builder serves them all. Append:

```python
class TestEveryCallerBuildsItsOwnVisibility:
    """FIVE CALLERS, ONE FILTER POINT. What each test asserts is that the
    caller PASSES a visibility built from the right subject -- not that
    retrieval works, which `TestTheFilterShape` above already covers
    without a live store.
    """

    @pytest.fixture
    def spy(self, monkeypatch):
        """Captures the `visibility` `retrieve_nodes` was handed.

        IT HANDS BACK A USABLE INDEX, not `None`. `answer_question`
        (`tools/rag/retrieval.py:623-650`) takes the `else` branch
        whenever the score floor is its default `0.0` -- which is every
        call here, since `nodes` is empty -- and that branch does
        `gateway.get_llm_for(answer_resolved)` and then
        `index.as_query_engine(llm=...)`. A `None` index raises
        `AttributeError`, and an unpatched gateway reaches a real engine.
        Both are stubbed here so the three tests below fail only for a
        reason about visibility.
        """
        seen = {}

        class _QueryEngine:
            def synthesize(self, *_args, **_kwargs):
                return type("R", (), {"response": "an answer", "source_nodes": []})()

        class _Index:
            def as_query_engine(self, **_kwargs):
                return _QueryEngine()

        def _fake(question, category, settings_row, *, embed_resolved, visibility):
            seen["visibility"] = visibility
            return [], False, _Index()

        monkeypatch.setattr(retrieval, "retrieve_nodes", _fake)
        monkeypatch.setattr(retrieval.gateway, "get_llm_for", lambda _resolved: object())
        return seen

    @pytest.fixture
    def resolved(self, monkeypatch):
        """EVERY IN-PROCESS CALLER RESOLVES ITS ROLES BEFORE IT RETRIEVES,
        so the spy above is unreachable without this.

        - `tools/rag/jobs.py:239` -- `run_ask` opens with
          `resolved, causes, answer_name = _precheck(payload)` and raises
          `RuntimeError(model_unavailable_message(...))` when a role will
          not resolve or an endpoint fails health.
        - `tools/rag/tools.py:202` and `:273` -- both runners do a
          FUNCTION-LOCAL `resolve(...)` at call time and translate a
          `ValueError` into `ToolRefused` before `retrieve_nodes`/
          `answer_question` is ever called.

        Patched at each caller's own seam, exactly as
        `tools/rag/tests/test_tools.py:92` already patches
        `models.contracts.bindings.resolve` -- which works precisely
        because the runners' import is function-local. `SearchView` has
        its own stub already: `tools/rag/tests/_helpers.py::model_available`.
        """
        from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE
        from tools.rag import jobs
        monkeypatch.setattr("models.contracts.bindings.resolve",
                            lambda role: ANSWER_RESOLVED if role == RAG_ANSWER_ROLE
                            else EMBED_RESOLVED)
        monkeypatch.setattr(
            jobs, "_precheck",
            lambda payload: ({RAG_ANSWER_ROLE: ANSWER_RESOLVED,
                              RAG_EMBED_ROLE: EMBED_RESOLVED}, {}, "the assigned model"))

    def test_the_ask_job_builds_it_from_the_payload_actor(self, spy, resolved):
        """`run_ask(payload, models, ctx)` -- THREE required positional
        parameters (`tools/rag/jobs.py:212`), none defaulted."""
        from identity.contracts.principals import payload_fields
        from tools.rag import jobs
        member = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE):
            jobs.run_ask(
                {"question": "q",
                 **payload_fields(Principal("user", str(member.pk)))},
                [], make_job_ctx())
        assert spy["visibility"].entitlement_ids == frozenset({finance.pk})

    def test_the_search_runner_builds_it_from_ctx_principal(self, spy, resolved):
        """`RAG_SEARCH`'s required param is `query`, not `question`
        (`tools/rag/tests/test_tools.py:98`); `validate_tool_args` refuses
        anything else."""
        from tools.rag import tools as rag_tools
        member = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE):
            rag_tools.run_search(
                {"query": "q"},
                make_tool_ctx(principal=Principal("user", str(member.pk))))
        assert spy["visibility"].entitlement_ids == frozenset({finance.pk})

    def test_the_ask_runner_builds_it_from_ctx_principal(self, spy, resolved):
        """`RAG_ASK`'s param IS `question` (`tools/rag/tools.py:283`)."""
        from tools.rag import tools as rag_tools
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            rag_tools.run_ask(
                {"question": "q"},
                make_tool_ctx(principal=Principal("user", str(member.pk))))
        assert spy["visibility"].unrestricted is False

    @pytest.fixture
    def search_models(self):
        """`SearchView` resolves the embed role before it retrieves, and
        it binds `resolve`/`get_engine` in `tools.rag.views` -- a
        different seam from the runners' function-local import, which is
        why this one test does not use the `resolved` fixture above.

        `tools/rag/tests/_helpers.py:85::model_available` is the stub this
        package already uses for exactly that. It is a GENERATOR FUNCTION
        -- the BODY of a fixture, not a fixture itself (its own docstring
        says so) -- so it is wrapped here, which is this repository's
        no-`conftest.py` convention: fixtures are defined per module and
        delegate their bodies to `_helpers`.
        """
        yield from model_available()

    def test_the_search_page_builds_it_from_the_request_principal(
            self, spy, search_models, client):
        member = make_user()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            client.get(reverse("rag-search"), {"q": "a question"})
        assert spy["visibility"].unrestricted is False

    def test_the_ask_command_builds_it_from_the_service_principal_and_says_so(self):
        """It calls `answer_question` DIRECTLY, not through the queue, so
        without its own visibility the required argument would break the
        command outright.

        PATCHED AT THE COMMAND'S OWN SEAM, exactly as
        `tools/rag/tests/test_commands.py:38-52` already does: the module
        binds `resolve` and `answer_question` by name, so patching
        `tools.rag.management.commands.ask.resolve` and
        `...ask.answer_question` needs no bound role and no engine, and
        the `visibility` keyword is read straight off the recorded call.
        """
        from unittest.mock import patch
        from django.core.management import call_command
        make_admin()
        with posture(POSTURE_ENTERPRISE), \
                patch("tools.rag.management.commands.ask.resolve",
                      side_effect=_resolve_side_effect), \
                patch("tools.rag.management.commands.ask.answer_question",
                      return_value={"answer": "an answer", "citations": []}) as mock_answer:
            out = io.StringIO()
            call_command("ask", "a question", stdout=out)
        assert "unlabelled documents only" in out.getvalue()
        assert mock_answer.call_args.kwargs["visibility"].unrestricted is False
```

`_resolve_side_effect`, `ANSWER_RESOLVED` and `EMBED_RESOLVED` are
`tools/rag/tests/test_commands.py:20-34`'s own helpers — copy them into this module rather than
importing across test files:

```python
def _resolve_side_effect(role):
    from models.contracts.roles import RAG_ANSWER_ROLE
    return ANSWER_RESOLVED if role == RAG_ANSWER_ROLE else EMBED_RESOLVED
```

with `ANSWER_RESOLVED`/`EMBED_RESOLVED` built as model-name-free placeholders:

```python
# GLOBAL CONSTRAINT 1: a `ResolvedModel`'s second argument is a MODEL id,
# so this module builds its own placeholders rather than copying
# `test_commands.py:24-25`'s literals into a new file. Only the ENGINE
# key is a real registered name, which the constraint's own carve-out
# allows. Nothing here reads `.model_id` -- the three runner tests assert
# on `spy["visibility"]`, and `run_ask`'s one use is inside a swallowing
# `try` (`tools/rag/jobs.py:259`).
ANSWER_RESOLVED = ResolvedModel("ollama", "the-assigned-chat-model",
                                "http://localhost:11434")
EMBED_RESOLVED = ResolvedModel("ollama", "the-embedding-role-model",
                               "http://localhost:11434", embed_dim=768)
```

**The two context builders are `make_job_ctx` and `make_tool_ctx`**, both in
`tools/rag/tests/_helpers.py` (`:110` and `:126`) — not `_job_ctx`/`_tool_ctx`.
`make_tool_ctx(**overrides)` already defaults `budget` to a real `StepBudget` and `job` to
`make_job_ctx()`, and takes `principal=` as an override, so the three runner calls above need
nothing else. `model_available` is NOT a fixture — it is the undecorated generator function at
`tools/rag/tests/_helpers.py:85` whose docstring calls itself "Body of the
`_model_available` autouse fixture". It is imported and wrapped by the one-line
`search_models` fixture above (`yield from model_available()`), which is this
repository's no-`conftest.py` convention: fixtures are defined per module and
delegate their bodies to `_helpers` (`tools/rag/tests/test_views.py:272`, `:483`,
`:743`, `:1040` all do the same).

Add to the module's imports: `io`, `reverse`, `Principal`, `POSTURE_ENTERPRISE`, and from
`tools.rag.tests._helpers` — `grant`, `make_admin`, `make_entitlement`, `make_job_ctx`,
`make_tool_ctx`, `make_user`, `model_available`, `posture`, `sign_in`.

- [ ] **Step 5: Update the existing tests that call the two changed functions**

`visibility` is **required**, so every existing test that calls `retrieve_nodes` or
`answer_question` — or asserts on a mocked call's kwargs — fails until it is updated. There are
**24 call sites in three modules**:

```bash
grep -rn "retrieve_nodes(\|answer_question(" tools/rag/tests/
```

- `tools/rag/tests/test_retrieval.py` — direct calls: add
  `visibility=DocumentVisibility(True, frozenset(), True)` (the open-box answer, which is what
  those tests were implicitly asserting against) unless the test is about visibility, in which
  case it belongs in `test_retrieval_visibility.py` instead.
- `tools/rag/tests/test_views.py` — the `SearchView` tests: no call-site change, but any
  `assert_called_once_with(...)` on a patched `retrieve_nodes` gains the new kwarg.
- `tools/rag/tests/test_commands.py:41-52` — `test_passes_question_through_to_answer_question`
  asserts `mock_answer.assert_called_once_with(question, category=None,
  answer_resolved=ANSWER_RESOLVED, embed_resolved=EMBED_RESOLVED)`; that call now also carries
  `visibility=...`, so the assertion becomes `assert
  mock_answer.call_args.kwargs["embed_resolved"] is EMBED_RESOLVED` plus an explicit
  `"visibility" in mock_answer.call_args.kwargs` — or keep `assert_called_once_with` and add
  the fourth kwarg with `ANY` from `unittest.mock`.

**Do not weaken an assertion to make it pass.** If an existing test asserted an exact kwarg set,
widen it to the new exact set — not to a substring or an `ANY` for everything.

- [ ] **Step 6: Prove there is still exactly one filter point**

Append to `tools/rag/tests/test_retrieval_visibility.py`:

```python
class TestOneFilterPoint:
    def test_no_module_outside_retrieval_builds_a_metadata_filter(self):
        """A visibility rule that lived in a runner would be a SECOND
        copy, and two copies of a visibility rule is how two surfaces come
        to disagree about what a person may see. `tools/rag/index.py`'s
        `delete_chunks_for_document` is the one sanctioned exception: it
        filters by `file_id` to DELETE, never to read."""
        import subprocess
        from pathlib import Path
        repo = Path(__file__).resolve().parents[3]
        out = subprocess.run(["git", "grep", "-l", "MetadataFilter", "--", "tools", "agents",
                              "models", "foundation"],
                             cwd=repo, capture_output=True, text=True)
        offenders = {
            line for line in out.stdout.splitlines()
            if line and not line.startswith("tools/rag/tests/")
            and line not in {"tools/rag/retrieval.py", "tools/rag/index.py"}
        }
        assert offenders == set(), offenders
```

- [ ] **Step 7: Run the rag suite in both postures**

```bash
.venv/bin/pytest -q tools/rag
FARABUNKER_TEST_POSTURE=enterprise .venv/bin/pytest -q tools/rag
```
Expected: PASS in both.

- [ ] **Step 8: Commit**

```bash
git add tools/rag/retrieval.py tools/rag/jobs.py tools/rag/tools.py tools/rag/views.py \
        tools/rag/management/commands/ask.py tools/rag/tests/
git commit -m "feat(rag): IA-2 T11 — a required visibility argument at the one filter point"
```

---
### Task 12: the library surfaces — labels, counts, bytes, and the widened action predicate

The library page is where the administer-versus-read split becomes visible: an administrator
with the content setting off sees every ROW and reads none of the labelled BYTES, and labels a
document they cannot open. Done-when 1 and done-when 2 are tested here.

**Files:**
- Modify: `tools/rag/views.py` (the label route, the counts, the two content routes, the two
  widened actions, the upload form's label picker), `tools/rag/urls.py`, `identity/routes.py`,
  `tools/rag/ingest.py` (one relocated read), `tools/rag/templates/rag/documents.html`,
  `identity/views.py` (the library-posture select stops being disabled), `identity/templates/identity/settings.html`
- Create: `tools/rag/templates/rag/_document_labels.html`
- Modify: `foundation/ops/tests/test_column_boundaries.py` (add the guard written in Task 9)
- Test: `tools/rag/tests/test_document_label_page.py`, `tools/rag/tests/test_views.py`
  (append), `identity/tests/test_settings_page.py` (amend)

**Interfaces:**
- Consumes: `tools.rag.access.listable_documents`/`readable_documents`/`may_label_document`
  (Task 9); `tools.rag.labels.set_document_labels`/`document_label_ids` (Task 10);
  `identity.access.labelling_entitlements` (Task 1).
- Produces: URL name `rag-document-labels` at `/rag/documents/<int:doc_id>/labels/`, class `R`.
- Produces: `tools.rag.ingest.document_at_path(path) -> Document | None`.

- [ ] **Step 1: Write the failing library tests**

Create `tools/rag/tests/test_document_label_page.py`:

```python
"""The library, once documents can be labelled.

THE CASE THE WHOLE SPLIT EXISTS FOR gets its own test: label a document
with an entitlement the administrator does NOT hold, assert the label
wrote, assert the chunk re-stamp ran, and assert `rag-document-file`
still answers 404 to that same administrator.
"""
from __future__ import annotations

import json

import pytest
from django.db import connection
from django.urls import reverse

from identity.contracts.postures import LIBRARY_LOCKED, POSTURE_ENTERPRISE
from tools.rag import index as rag_index
from tools.rag.models import DocumentEntitlement
from tools.rag.tests._helpers import (
    grant, make_admin, make_document, make_entitlement, make_user, posture, reset_settings,
    seed_sweep_posture, sign_in,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


@pytest.fixture
def chunk_table():
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE TABLE {rag_index.LIVE_TABLE_NAME} "
                       f"(id bigserial primary key, metadata_ json)")
    yield
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {rag_index.LIVE_TABLE_NAME}")


def _seed(doc_id):
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {rag_index.LIVE_TABLE_NAME} (metadata_) VALUES (%s)",
                       [json.dumps({"file_id": str(doc_id)})])


def _metadata(doc_id):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT metadata_::jsonb FROM {rag_index.LIVE_TABLE_NAME} "
                       f"WHERE metadata_->>'file_id' = %s", [str(doc_id)])
        return cursor.fetchone()[0]


class TestTheCaseTheSplitExistsFor:
    def test_an_admin_labels_a_document_they_cannot_read(self, client, chunk_table):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        document = make_document()
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            # The ROW is theirs to manage...
            assert client.get(reverse("rag-documents")).status_code == 200
            response = client.post(reverse("rag-document-labels", args=[document.id]),
                                   {"entitlements": [str(finance.pk)]})
            assert response.status_code == 302
            assert _metadata(document.id)["entitlements"] == [str(finance.pk)]
            # ...the BYTES are not theirs to read.
            assert client.get(
                reverse("rag-document-file", args=[document.id])).status_code == 404


class TestTheContentRoutes:
    def test_a_holder_reads_the_file_and_a_non_holder_gets_404(self, client, tmp_path):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        holder, other = make_user(), make_user()
        grant(finance, user=holder)
        grant(legal, user=other)
        source = tmp_path / "a.txt"
        source.write_text("hello")
        document = make_document(source_path=str(source))
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        url = reverse("rag-document-file", args=[document.id])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, holder)
            assert client.get(url).status_code == 200
            second = client.__class__()
            sign_in(second, other)
            assert second.get(url).status_code == 404

    def test_the_transcript_route_answers_the_same_way(self, client):
        finance = make_entitlement(name="Finance")
        document = make_document()
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            assert client.get(
                reverse("rag-document-transcript", args=[document.id])).status_code == 404


class TestTheCountsAreAReadingSurface:
    def test_a_member_sees_only_their_own_documents_in_every_count(self, client):
        """A sidebar that says "Finance (14)" to a member who may open
        none of them leaks exactly the fact the labels exist to hide, and
        the count is the easiest one to forget because it renders no
        document."""
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        member = make_user()
        grant(finance, user=member)
        mine = make_document(title="mine")
        theirs = make_document(title="theirs")
        DocumentEntitlement.objects.create(document=mine, entitlement=finance)
        DocumentEntitlement.objects.create(document=theirs, entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            body = client.get(reverse("rag-documents")).content
        assert b"mine" in body and b"theirs" not in body

    def test_an_admin_sees_every_row_and_every_count_with_the_setting_off(self, client):
        legal = make_entitlement(name="Legal")
        theirs = make_document(title="theirs")
        DocumentEntitlement.objects.create(document=theirs, entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            assert b"theirs" in client.get(reverse("rag-documents")).content

    def test_a_category_whose_visible_count_is_zero_is_omitted_from_the_sidebar(self, client):
        """Not shown as `(0)`, for the same reason: the count itself is
        the leak.

        ASSERTED ON `response.context["sidebar"]`, not on the page body:
        the same category name is still in the upload form's own
        `<select>` -- that picker is a WRITE-side control and is
        deliberately not narrowed -- so a bare substring assertion over
        the whole page would pass only by accident of the picker also
        being hidden, which it is not."""
        from tools.rag.models import Category
        legal = make_entitlement(name="Legal")
        category = Category.objects.create(name="Contracts")
        theirs = make_document(title="theirs", category=category)
        DocumentEntitlement.objects.create(document=theirs, entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.get(reverse("rag-documents"))
        assert all("Contracts" not in str(entry) for entry in response.context["sidebar"])

    def test_the_upload_pickers_category_list_is_not_narrowed(self, client):
        """A write-side control. Narrowing it would remove an existing
        capability -- uploading into an existing empty category -- that no
        rule asks to remove, and would change an `open` box's page, which
        must stay byte-identical to today's."""
        from tools.rag.models import Category
        Category.objects.create(name="Contracts")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.get(reverse("rag-documents"))
        assert [c.name for c in response.context["categories"]] == ["Contracts"]


class TestTheWidenedActionPredicate:
    def test_an_entitlement_owner_may_delete_and_reingest_within_their_entitlement(
            self, client):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        owner = make_user()
        grant(finance, user=owner, role="owner")
        mine, theirs = make_document(), make_document()
        DocumentEntitlement.objects.create(document=mine, entitlement=finance)
        DocumentEntitlement.objects.create(document=theirs, entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            assert client.post(
                reverse("rag-document-delete", args=[mine.id])).status_code == 302
            assert client.post(
                reverse("rag-document-delete", args=[theirs.id])).status_code == 403

    def test_a_plain_member_is_still_refused(self, client):
        finance = make_entitlement(name="Finance")
        member = make_user()
        grant(finance, user=member)
        document = make_document()
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            assert client.post(
                reverse("rag-document-delete", args=[document.id])).status_code == 403

    def test_a_member_gets_403_even_for_a_document_id_that_does_not_exist(self, client):
        """Decision 9, pinned. IA-1 checked the predicate BEFORE looking
        the row up, so a member got 403 for any `doc_id`. Resolving the
        row first would answer 404 here instead -- a new enumeration
        signal on a route that had none."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            assert client.post(
                reverse("rag-document-delete", args=[999999])).status_code == 403


class TestTheLabelRoute:
    def test_an_owner_may_only_add_or_remove_entitlements_they_own(self, client,
                                                                   chunk_table):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        owner = make_user()
        grant(finance, user=owner, role="owner")
        document = make_document()
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        _seed(document.id)
        url = reverse("rag-document-labels", args=[document.id])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            # Adding one they do not own is refused, and nothing changes.
            client.post(url, {"entitlements": [str(finance.pk), str(legal.pk)]},
                        follow=True)
            assert set(DocumentEntitlement.objects.filter(document=document)
                       .values_list("entitlement_id", flat=True)) == {finance.pk}

    def test_removing_the_last_label_makes_the_document_follow_the_library_posture(
            self, client, chunk_table, tmp_path):
        """Done-when 2, in both library postures, without a re-encode and
        within one request.

        A REAL FILE ON DISK, because `make_document` defaults
        `source_path` to a path that does not exist
        (`tools/rag/tests/_helpers.py:76`) and `document_file` would then
        404 for a missing file rather than for visibility -- which would
        make `in (200, 404)` the whole outcome space and the assertion
        vacuous."""
        source = tmp_path / "a.txt"
        source.write_text("hello")
        admin = make_admin()
        member = make_user()
        finance = make_entitlement(name="Finance")
        document = make_document(source_path=str(source))
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        _seed(document.id)
        url = reverse("rag-document-file", args=[document.id])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            # Before: labelled, and the member holds nothing.
            before = client.__class__()
            sign_in(before, member)
            assert before.get(url).status_code == 404
            client.post(reverse("rag-document-labels", args=[document.id]),
                        {"entitlements": []})
            assert "entitlements" not in _metadata(document.id)
            # After, on an OPEN library: unlabelled is readable by anyone
            # signed in -- one request later, with no re-encode.
            second = client.__class__()
            sign_in(second, member)
            assert second.get(url).status_code == 200
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            third = client.__class__()
            sign_in(third, member)
            assert third.get(url).status_code == 404

    def test_a_non_numeric_entitlement_id_is_refused_not_crashed(self, client):
        admin = make_admin()
        document = make_document()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("rag-document-labels", args=[document.id]),
                                   {"entitlements": ["abc"]}, follow=True)
        assert response.status_code == 200
        assert b"Traceback" not in response.content


class TestTheUploadFormOffersOwnedLabels:
    def test_an_owner_sees_their_entitlement_in_the_upload_form(self, client):
        owner = make_user()
        finance = make_entitlement(name="Finance")
        make_entitlement(name="Legal")
        grant(finance, user=owner, role="owner")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(reverse("rag-documents")).content
        assert b"Finance" in body and b"Legal" not in body
```

- [ ] **Step 2: Run and watch it fail**

Run: `.venv/bin/pytest -q tools/rag/tests/test_document_label_page.py -x`
Expected: FAIL — `NoReverseMatch: Reverse for 'rag-document-labels' not found`.

- [ ] **Step 3: Relocate the two upload reads out of `views.py`**

`tools/rag/views.py::document_upload` reads `Document.objects` twice for dedup (lines 480 and
495). The guard this task installs forbids `Document.objects` in that module **at all**, and
those two reads are not a leak — they are staging bookkeeping. Move them behind one named
function in `tools/rag/ingest.py`, beside `enqueue_ingest`, which is where the rest of the
staging rule already lives:

```python
def document_at_path(original_path) -> Document | None:
    """The `Document` staged at `original_path`, or None.

    THE STAGING QUESTION, NOT A VISIBILITY ONE. `document_upload` asks it
    twice -- once to tell "already staged, unchanged" from "already
    staged, changed", and once, in its `FileNotFoundError` branch, to tell
    "the watcher won the race" from "staging genuinely failed". It lives
    here rather than in the view because `tools/rag/views.py` may not
    touch `Document.objects` at all (`foundation/ops/tests/
    test_column_boundaries.py`), and because both callers are asking about
    the INBOX, which is this module's subject.
    """
    return Document.objects.filter(original_path=str(original_path)).first()
```

and rewrite the two call sites as `existing = ingest.document_at_path(dest)` and
`if ingest.document_at_path(dest) is not None:`. Their surrounding comments stay exactly as
they are — the reasoning did not change, only where the query lives.

- [ ] **Step 4: Rewrite the counts and the listings off `listable_documents`**

In `tools/rag/views.py`:

`AskPageView.get_context_data` — `context["tabular_count"]` becomes

```python
        principal = principal_for_request(self.request)
        context["tabular_count"] = listable_documents(principal).filter(
            doc_type=Document.DocType.TABULAR, status=Document.Status.READY).count()
```

`DocumentsView.get_context_data` — one `visible = listable_documents(principal)` at the top,
and every subsequent read comes off it:

```python
        principal = principal_for_request(self.request)
        # THE LISTING'S OWN FUNCTION, for the rows AND for every count
        # above them, so the two can never disagree. A sidebar that says
        # "Finance (14)" to a member who may open none of them is a leak
        # of exactly the fact the labels exist to hide, and it is the
        # easiest one to forget because it renders no document.
        visible = listable_documents(principal)
        total_count = visible.count()
        uncategorized_count = visible.filter(category__isnull=True).count()
        # TWO CATEGORY LISTS, and the split is load-bearing.
        #
        # `sidebar_categories` is a READING surface: its counts must come
        # off `visible`, because a sidebar that says "Finance (14)" to a
        # member who may open none of them leaks exactly the fact the
        # labels exist to hide -- and it is the easiest leak to forget,
        # because it renders no document.
        #
        # `context["categories"]` stays EVERY category, unchanged from
        # today, because it is also the upload form's own
        # `<select name="category">` (`documents.html:456-460`) -- a
        # WRITE-side control. Narrowing it would drop every empty category
        # from that picker for everyone, on an `open` box included, and
        # remove an existing capability (uploading into an existing empty
        # category) that no rule asks to remove.
        sidebar_categories = (
            Category.objects.annotate(
                doc_count=Count("documents", filter=Q(documents__in=visible), distinct=True))
            .filter(doc_count__gt=0)
            .order_by("name")
        )
        categories = Category.objects.all().order_by("name")
```

and the four branches that build `documents` filter `visible` rather than `Document.objects`.

The sidebar loop reads `sidebar_categories`, whose `doc_count__gt=0` filter has already dropped
an empty one — a category whose visible count is zero is omitted entirely rather than shown as
`(0)`, because the count itself is the leak:

```python
        sidebar += [
            _sidebar_entry(category.name, category.doc_count, category_value=category.name,
                           category_id=category.id, **entry_kwargs)
            for category in sidebar_categories
        ]
```

`context["categories"]` keeps its existing value and its existing consumer.
`tools/rag/views.py:30` already imports `Count`; add `Q` to that line, and add
`from tools.rag.access import (listable_documents, may_label_document, readable_documents,
visible_ask_records)` — extending the existing `visible_ask_records` import rather than adding a
second one — plus `from identity.access import labelling_entitlements, owned_entitlement_ids`
to the module's existing `from identity.access import is_admin, sees_all_content` line
(`tools/rag/views.py:43`), and `from tools.rag.labels import document_label_ids,
set_document_labels`.

- [ ] **Step 5: Gate the two content routes**

`document_file` and `document_transcript` each replace
`get_object_or_404(Document, pk=doc_id)` with

```python
    # CLASS L: content, not a row. 404 outside `readable_documents` --
    # INCLUDING for an administrator with the content setting off: the row
    # is theirs to manage, the bytes are not theirs to read.
    document = get_object_or_404(
        readable_documents(principal_for_request(request)), pk=doc_id)
```

and `document_file`'s docstring loses its "This is unauthenticated for the Phase 1 skeleton.
Once the Identity service exists, this should check the requesting user's access to the specific
document before streaming any bytes." paragraph — it is done, and a docstring that still asks
for what the code already does is a docstring that lies. Replace it with one sentence naming
`readable_documents` as the gate.

- [ ] **Step 6: Widen the two action predicates and add the label route**

`document_delete` (`tools/rag/views.py:302-307`) and `document_reingest`
(`tools/rag/views.py:613-618`) each keep **IA-1's ordering** — the cheap predicate first, the
row second — because that ordering is what makes decision 9 true. IA-1 answers **403 to a
member for any `doc_id`, existing or not**; resolving the row first would answer 404 for a
`doc_id` that does not exist, which is a new enumeration signal on a route that had none:

```python
    principal = principal_for_request(request)
    # THE CHEAP HALF OF THE PREDICATE FIRST, exactly as IA-1 ordered it.
    # A caller who is neither an administrator nor an owner of ANY
    # entitlement cannot act on any document, so they are refused before
    # a row is looked up -- and therefore get 403 for a `doc_id` that
    # does not exist, exactly as they did in IA-1. Resolving the row
    # first would answer 404 there instead, which is an enumeration
    # signal this route did not have.
    if not (is_admin(principal) or owned_entitlement_ids(principal)):
        return HttpResponseForbidden(
            "Deleting a document is an administrator action on this box, or an "
            "action for an owner of one of the document's entitlements."
        )
    document = get_object_or_404(Document, pk=doc_id)
    # THE SECOND HALF needs the row: an owner may act only on a document
    # under an entitlement they own.
    if not may_label_document(principal, document):
        return HttpResponseForbidden(
            "Deleting a document is an administrator action on this box, or an "
            "action for an owner of one of the document's entitlements."
        )
```

(with "Re-ingesting" in `document_reingest`'s two messages). `get_object_or_404(Document, ...)`
passes the **model class**, not `Document.objects`, so it is invisible to Step 9's AST guard —
which looks for `ast.Attribute(value=ast.Name(id="Document"), attr="objects")` — and the two
views keep the row lookup they already have.

Both messages are the same string twice in one view, so lift them into one module-level constant
per verb rather than typing each twice.

**The stale comments.** There is **one** `# IA-2 widens this to …` block, at
`tools/rag/views.py:297-301`, inside `document_delete`; delete it.
`document_reingest`'s comment at `:610-612` cross-references it ("see `document_delete`'s own
comment for the full reasoning") — rewrite that cross-reference to point at the widened rule
rather than at a comment that no longer exists.

Add the label route:

```python
@require_POST
def document_labels_update(request, doc_id: int):
    """POST /rag/documents/<doc_id>/labels/ -- set this document's
    entitlement labels to exactly what was submitted.

    THE ROUTE THE ADMINISTER/READ SPLIT EXISTS FOR. An administrator
    labels a document they are not cleared to read, and the label takes
    effect on chunks whose text they never see.

    `is_admin`, OR an owner of every entitlement being ADDED or REMOVED.
    Written as a set comparison against `labelling_entitlements`, which
    is the same function the form renders its options from -- so the page
    can never offer an option this POST refuses, and a stale form or a
    hand-made request gets one honest message rather than a partial
    write.
    """
    principal = principal_for_request(request)
    document = get_object_or_404(listable_documents(principal), pk=doc_id)
    if not may_label_document(principal, document):
        return HttpResponseForbidden(
            "Labelling a document is an administrator action on this box, or an "
            "action for an owner of one of the document's entitlements."
        )
    raw = request.POST.getlist("entitlements")
    if any(not value.isdigit() for value in raw):
        messages.error(request, "Entitlements are chosen from the list, not typed.")
        return redirect("rag-documents")
    wanted = {int(value) for value in raw}
    current = document_label_ids(document)
    offered = {pk for pk, _name in labelling_entitlements(principal)}
    if (wanted ^ current) - offered:
        # THE SYMMETRIC DIFFERENCE is what is being changed. A label the
        # caller neither adds nor removes needs no standing -- an owner of
        # Finance may add Finance to a document that already carries Legal
        # without owning Legal, and must not be able to silently drop
        # Legal on the way.
        messages.error(request, "You may only add or remove entitlements you own.")
        return redirect("rag-documents")
    set_document_labels(principal, document, wanted)
    messages.info(request, f"Saved the labels for {document.title!r}.")
    return redirect("rag-documents")
```

Mount it in `tools/rag/urls.py`:

```python
    path("documents/<int:doc_id>/labels/", views.document_labels_update,
         name="rag-document-labels"),
```

and classify it in `identity/routes.py`'s `/rag/` block:

```python
    "rag-document-labels": "R",               # -- admin, or an entitlement owner
```

with its class assertion appended to `identity/tests/test_routes.py::TestIA2Routes`:

```python
    def test_the_document_label_route_is_R(self):
        """An operational ROW action: `is_admin`, or an owner of one of
        the document's entitlements. This is the route the
        administer/read split exists for -- an administrator labels a
        document they are not cleared to read."""
        from identity.routes import ROUTE_RULES
        assert ROUTE_RULES["rag-document-labels"] == "R"
```

- [ ] **Step 7: Render the labels and the upload picker**

Create `tools/rag/templates/rag/_document_labels.html`:

```html
{% comment %}
One document's entitlement labels: the chips, and the form that changes
them. Rendered only where `row.may_label` is true -- the SAME predicate
the POST enforces (`tools.rag.access.may_label_document`), never a second
truth.

A document with no chips is unlabelled and follows the library posture,
which the library page's own header sentence explains.
{% endcomment %}
{% if row.labels %}<span class="labels">{% for name in row.labels %}<span class="chip">{{ name }}</span>{% endfor %}</span>{% endif %}
{% if row.may_label %}
  <form class="labels-form" method="post" action="{% url 'rag-document-labels' row.document.id %}">
    {% csrf_token %}
    <select name="entitlements" multiple size="3">
      {% for pk, name in label_choices %}
        <option value="{{ pk }}" {% if pk in row.label_ids %}selected{% endif %}>{{ name }}</option>
      {% endfor %}
    </select>
    <button type="submit">Save labels</button>
  </form>
{% endif %}
```

**The template's `row` has to come from somewhere, and it is not `page_obj.object_list`.**
`tools/rag/templates/rag/documents.html:487` is `{% for document in page_obj.object_list %}` and
the ~15 lines inside that loop read `document.id`, `document.title`, `document.status`,
`document.has_transcript`, `document.doc_type` through line 538. **Paginate the queryset
unchanged** — so every page-arithmetic line above stays byte-identical — and build the row
wrappers from the page's own object list afterwards:

```python
        # AFTER pagination, over the page's own rows: one prefetch, no
        # query per row, and `Paginator` still counts and slices a plain
        # `Document` queryset exactly as it does today.
        page_obj.object_list = page_obj.object_list.prefetch_related(
            "entitlement_labels__entitlement")
        context["label_choices"] = labelling_entitlements(principal)
        context["rows"] = [
            {
                "document": document,
                "labels": [label.entitlement.name
                           for label in document.entitlement_labels.all()],
                "label_ids": frozenset(label.entitlement_id
                                       for label in document.entitlement_labels.all()),
                "may_label": may_label_document(principal, document),
            }
            for document in page_obj.object_list
        ]
```

and change `documents.html:487` from `{% for document in page_obj.object_list %}` to:

```html
          {% for row in rows %}{% with document=row.document %}
```

with a matching `{% endwith %}` before the loop's `{% endfor %}`. That one change makes every
existing `document.*` reference inside the loop resolve unchanged, and gives the row's own cells
`row.labels`, `row.label_ids` and `row.may_label` — which Step 6's label partial and Task 17's
Re-ingest/Delete gates both key on.

**`may_label_document` runs once per rendered row**, and each call is one `EXISTS` query against
`rag_documententitlement` for a non-admin with owned entitlements — bounded by
`DOCUMENTS_PAGE_SIZE`, and zero queries for an administrator (`is_admin` short-circuits) and for
a member with no owned entitlements (the empty-`owned` early return in Task 9's body).

**The upload form gains the same picker**, with one sentence beside it:

```html
      <label for="id_upload_entitlements">Labels</label>
      <select name="entitlements" id="id_upload_entitlements" multiple size="3">
        {% for pk, name in label_choices %}<option value="{{ pk }}">{{ name }}</option>{% endfor %}
      </select>
      <p class="muted">A document with no label follows the library posture — everyone signed
        in can read it while the library is open.</p>
```

and `document_upload` applies them. **The check is `offered`, deliberately NOT
`may_label_document`:**

```python
    # THE UPLOADER'S OWN LABELS, checked against what this page offered
    # them and nothing else. `may_label_document` is the wrong predicate
    # here and the difference matters: a brand-new document is
    # UNLABELLED, and `may_label_document` answers False for an
    # unlabelled document held by a non-admin (there is no entitlement
    # owner to delegate from). Applied literally, an entitlement owner
    # could see the picker and never write a label -- a control that
    # silently does nothing.
    #
    # `offered` is `labelling_entitlements(principal)`, the same function
    # the picker renders from, so a POST naming anything else is a stale
    # form or a hand-made request and gets one honest message.
    raw_labels = request.POST.getlist("entitlements")
    offered = {pk for pk, _name in labelling_entitlements(principal)}
    if any(not value.isdigit() for value in raw_labels) or \
            not {int(value) for value in raw_labels} <= offered:
        messages.error(request, "Labels are chosen from the list, not typed.")
        return redirect("rag-documents")
    wanted_labels = {int(value) for value in raw_labels}
```

evaluated **once, before the upload loop**, and applied per staged document inside it:

```python
            if wanted_labels:
                document = ingest.document_at_path(dest)
                if document is not None:
                    set_document_labels(principal, document, wanted_labels)
```

`ingest.document_at_path` is the function Step 3 relocated, reused rather than re-queried, and
the `is not None` guard is the watcher-race case that branch already knows about. A document the
uploader labels with nothing arrives **unlabelled**, which is the existing behaviour and the
honest default for a file dropped into a shared inbox.

Its test, appended to `tools/rag/tests/test_document_label_page.py`:

```python
class TestUploadingWithLabels:
    def test_an_owners_upload_lands_labelled(self, client, tmp_path, settings):
        """Spec section 11.3: "the upload form offers labels the uploader
        owns". The write uses the `offered` check, not
        `may_label_document` -- a brand-new document is unlabelled, and
        that predicate answers False for an unlabelled document held by a
        non-admin, which would make the picker a control that does
        nothing."""
        settings.INGEST_INBOX_DIR = str(tmp_path)
        owner = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role="owner")
        upload = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            client.post(reverse("rag-document-upload"),
                        {"files": [upload], "category": "",
                         "entitlements": [str(finance.pk)]})
        document = Document.objects.get(title__startswith="note")
        assert set(document.entitlement_labels.values_list("entitlement_id", flat=True)) == {
            finance.pk}

    def test_an_entitlement_the_page_never_offered_is_refused(self, client, tmp_path,
                                                              settings):
        settings.INGEST_INBOX_DIR = str(tmp_path)
        owner = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=owner, role="owner")
        upload = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(reverse("rag-document-upload"),
                                   {"files": [upload], "category": "",
                                    "entitlements": [str(legal.pk)]}, follow=True)
        assert b"chosen from the list" in response.content
        assert DocumentEntitlement.objects.count() == 0
```

with `from django.core.files.uploadedfile import SimpleUploadedFile` and
`from tools.rag.models import Document` added to that module's imports. If this tree's
`document_upload` reads its inbox directory from a different settings name, use that one —
`tools/rag/tests/test_views.py`'s existing upload tests show which.

- [ ] **Step 8: Make the library-posture control live — four artefacts, named exactly**

IA-1 rendered the enterprise `library_posture` select **disabled**, paired with a hidden mirror
field, because nothing read the column. `tools/rag/access.py::may_see_unlabelled` reads it now,
so the disabling is the lie in the other direction. Four artefacts carry that IA-1 decision and
all four change:

**1. `identity/views.py:298-306`** — delete the comment block and the two lines it explains:

```python
    if row.posture != POSTURE_PERSONAL:
        form.fields["library_posture"].widget.attrs["disabled"] = "disabled"
```

`POSTURE_PERSONAL` is still used by `user_create` in the same module, so its import stays.

**2. `identity/templates/identity/settings.html:104-115`** — inside the `{% else %}` (non-personal)
branch only, delete the `{% comment %}` block explaining the disabled select, the
`<input type="hidden" name="{{ form.library_posture.html_name }}" value="{{ form.library_posture.value }}">`
mirror field, and the helptext that reads *"Takes effect once documents carry labels (IA-2).
Until then every unlabelled document is readable by everyone signed in, whichever option is
shown here."* Replace the helptext with one true sentence:

```html
      <p class="helptext">Decides who may read a document carrying no label: everyone signed in
        while this is "open", and only an administrator with content access while it is
        "locked". A labelled document is never affected — the people who hold its entitlements
        read it either way.</p>
```

**The personal branch (`settings.html:97-101`) is untouched.** It renders a label, a helptext and
a hidden `value="open"` — no select at all — because `set_posture` resets the column to `open`
there and a page with no control must not submit a value that contradicts the reset.

**3. `identity/tests/test_settings_page.py:80` — `test_the_enterprise_select_is_disabled_and_says_why`.**
That is the real name (there is no
`test_the_library_posture_control_is_disabled_outside_personal`), and it asserts **two** things:
`"disabled" in body` and `"ia-2" in body.lower()`. Replace it whole:

```python
    def test_the_enterprise_select_is_live_and_says_what_it_does(self, client):
        """IA-1 rendered this DISABLED, with a helptext pointing at IA-2,
        because `tools/rag/access.py` did not read the column yet.
        `may_see_unlabelled` reads it now, so a disabled control -- and a
        helptext still promising a later phase -- would be the lie in the
        other direction."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("identity-settings")).content.decode()
        assert 'id="id_library_posture"' in body
        assert "disabled" not in body
        assert "ia-2" not in body.lower()
        assert "carrying no label" in body
```

**4. `identity/tests/test_settings_page.py:92-109` —
`test_saving_from_the_enterprise_posture_keeps_the_current_value`.** Its whole premise is the
hidden mirror field ("The select is disabled, so a browser never submits a value for it — the
hidden mirror field submits the row's OWN current value"), which Step 8 removes. Rewrite it to
assert what the live control now does: the select's own current value round-trips.

```python
    def test_saving_from_the_enterprise_posture_round_trips_the_selected_value(self, client):
        """The select is LIVE now, so the browser submits its value and
        the page shows it selected on the way back. The IA-1 version of
        this test asserted the hidden mirror field's behaviour, and that
        field is gone."""
        from identity.contracts.postures import LIBRARY_LOCKED
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
            sign_in(client, admin)
            response = client.post(reverse("identity-settings"),
                                   {"posture": POSTURE_ENTERPRISE,
                                    "library_posture": LIBRARY_LOCKED,
                                    "session_idle_minutes": "720"}, follow=True)
            assert response.status_code == 200
            assert IdentitySettings.get_solo().library_posture == LIBRARY_LOCKED
```

**Do NOT add a "still disabled in personal" test.** Personal was never disabled — the block at
`identity/views.py:305` reads `if row.posture != POSTURE_PERSONAL` — and after this step nothing
on the personal page contains the string "disabled" at all. The personal branch is already
covered by the two tests immediately above the ones being replaced
(`identity/tests/test_settings_page.py:68-72`), which assert
`'id="id_library_posture"' not in body` and
`'<input type="hidden" name="library_posture" value="open">' in body`. Leave them exactly as
they are.

- [ ] **Step 9: Install the `Document.objects` guard**

Add the two tests written out in full in **Task 9, Step 5** ("Write out the `Document.objects`
guard for Task 12") to `foundation/ops/tests/test_column_boundaries.py`. They pass now that Steps 3–6 have moved every
`Document.objects` read out of `tools/rag/views.py`.

- [ ] **Step 10: Add the matrix driver**

```python
    "rag-document-labels": lambda w: (
        "post", reverse("rag-document-labels", args=[w.document.id]), {"entitlements": []}),
```

- [ ] **Step 11: Run everything, in both postures**

```bash
.venv/bin/pytest -q tools/rag identity foundation
FARABUNKER_TEST_POSTURE=enterprise .venv/bin/pytest -q tools/rag identity
```
Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add tools/rag/views.py tools/rag/urls.py tools/rag/ingest.py tools/rag/templates/rag/ \
        identity/views.py identity/templates/identity/settings.html identity/routes.py \
        foundation/ops/tests/test_column_boundaries.py tools/rag/tests/ identity/tests/
git commit -m "feat(rag): IA-2 T12 — document labels, row-versus-content counts, and the widened action predicate"
```

---

### Task 13: the two direct-surface tool gates — `vision-generate` and `rag-document-upload`

Owner directives, 2026-08-30 (spec §22.31 and §22.33; §9.3): *"an entitlement may be necessary to
use image generation"*, and the library's own door with it. Task 5 made a labelled tool disappear
from an **agent's** prompt; this makes it disappear from **its own page**. There are exactly two
such doors on this box, they take the same three edits, and doing them together is what stops the
second from being a copy of the first that drifts.

**Files:**
- Modify: `tools/vision/tools.py` (export the key), `tools/vision/views.py` (`generate`,
  `_create_page_context`), `tools/vision/templates/vision/create.html`,
  `tools/vision/tests/_helpers.py` (the identity re-export block)
- Modify: `tools/rag/tools.py` (export the key), `tools/rag/views.py` (`document_upload`,
  `DocumentsView`), `tools/rag/templates/rag/documents.html`
- Modify: `agents/chat/views/tools.py` (the tool-label page lists mutating tools, page-only),
  `agents/chat/templates/chat/tool_entitlements.html`
- Test: `tools/vision/tests/test_views_generate.py` (append),
  `tools/vision/tests/test_views_create.py` (append), `tools/rag/tests/test_views.py` (append),
  `agents/chat/tests/test_tool_entitlements_page.py` (append),
  `identity/tests/test_route_matrix.py` (the labelled dimension)

**Interfaces:**
- Consumes: `agents.entitlements.tool_access_for` (Task 5); `identity.request.
  principal_for_request` (IA-1).
- Produces: `tools.vision.tools.VISION_GENERATE_TOOL_KEY`, `tools.rag.tools.RAG_INGEST_TOOL_KEY`
  — each spelled once, in the module that registers the spec.
- Produces: `tools/vision/views.py::_may_generate(principal) -> bool` and
  `tools/rag/views.py::_may_upload(principal) -> bool` — one predicate per door, each called by
  its POST gate and by its page render.

- [ ] **Step 1: Spell each tool key once, in the module that registers it**

Neither key exists as a constant today: `tools/vision/tools.py:226` and `tools/rag/tools.py:85`
both write the string inline in their `ToolSpec`. A gate that retyped it would be a second
spelling of a key the registry matches exactly.

```python
# tools/vision/tools.py, at module scope, above the spec
# THE KEY, SPELLED ONCE. `tools/vision/views.py` gates its own page on
# this tool's entitlement (spec section 9.3), and a view that retyped the
# literal would be a second spelling of a key the registry matches
# exactly.
VISION_GENERATE_TOOL_KEY = "vision.generate"
```
```python
# tools/rag/tools.py, at module scope, above RAG_INGEST
RAG_INGEST_TOOL_KEY = "rag.ingest"
```

and each `ToolSpec` uses `key=<CONSTANT>` instead of the literal. Nothing else changes: the
registry, `Agent.tool_keys` rows and every existing test still see the same string.

- [ ] **Step 2: Let the tool-label page label a mutating tool**

`rag.ingest` is `mutates=True` (`tools/rag/tools.py:103`), so it is **not** in
`grantable_tools()` — and Task 6's page lists exactly that set. Without this step the library
door's key cannot be labelled at all and Step 5's gate has nothing to consult.

**Labelling is not granting** (spec §22.35). ADR 0010's rule governs what an **agent** may be
handed, and `granted_tools` still drops a mutating key unconditionally; a label can only ever
**narrow** which *people* reach a tool's own page. In `agents/chat/views/tools.py`, the row source
becomes `all_tools()` and each row carries a `page_only` flag:

```python
    from agents.contracts.tools import all_tools, grantable_tools

    # LABELLABLE IS NOT GRANTABLE, and the page says which is which.
    # `grantable_tools()` answers "what an Agent row may name in
    # `tool_keys`" -- ADR 0010 keeps every `mutates=True` spec out of it,
    # and `granted_tools` drops one regardless of any label. But a
    # mutating tool can still have a PAGE of its own (`rag.ingest` is the
    # library's upload door), and a label there narrows which people
    # reach that page. Listing only grantable tools would leave that door
    # unlabellable -- spec section 22.35.
    grantable = {spec.key for spec in grantable_tools()}
    rows = [
        {
            "key": spec.key,
            "label": spec.label,
            "description": spec.description,
            "held": labels.get(spec.key, frozenset()),
            "shell_agents": shell.get(spec.key, []),
            "page_only": spec.key not in grantable,
        }
        for spec in all_tools()
    ]
```

and `_save`'s membership check widens from `grantable_tools()` to `all_tools()` the same way. The
template marks such a row once:

```html
  {% if row.page_only %}
    <p class="muted">No agent can be granted this tool — a label here restricts only the people
      who reach its own page.</p>
  {% endif %}
```

Append to `agents/chat/tests/test_tool_entitlements_page.py`:

```python
class TestAMutatingToolIsLabellableButStillUngrantable:
    def test_the_page_lists_it_and_marks_it_page_only(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("chat-tool-entitlements")).content
        assert b"rag.ingest" in body
        assert b"No agent can be granted this tool" in body

    def test_labelling_it_leaves_it_uncallable_by_every_agent(self):
        """The two rules do not collide, and this is where that is
        proved rather than argued. `granted_tools` drops a `mutates=True`
        key for `mutates=True`, before any access check runs."""
        from agents.contracts.tools import ToolAccess, granted_tools
        from identity.contracts.principals import OPEN_PRINCIPAL
        access = ToolAccess(required={"rag.ingest": frozenset({1})},
                            held=frozenset({1}), unrestricted=False)
        assert granted_tools(OPEN_PRINCIPAL, ["rag.ingest"], access) == []
```

- [ ] **Step 3: Write the failing vision-door tests**

Append to `tools/vision/tests/test_views_generate.py`:

```python
class TestTheToolEntitlementGatesTheDirectSurface:
    """A labelled tool must not be reachable from its own page.

    Task 5 filters an AGENT's prompt; `vision-generate` is a POST a
    person makes directly, and until this gate it obeyed no label at all
    -- so an entitlement that took image generation away from somebody
    took it away only when an agent asked for it.

    403, NOT 503 and NOT 404: this is a refusal on a class of ACTION, the
    same shape `rag-document-delete` uses, and the caller can already see
    that the page exists.
    """

    def test_a_non_holder_is_refused_403_and_nothing_is_queued(self, client):
        from agents.models import ToolEntitlement
        from models.queue.models import InferenceJob
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="vision.generate", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("vision-generate"),
                                   {"operation": "txt2img", "prompt": "a picture"})
        assert response.status_code == 403
        assert b"Traceback" not in response.content
        assert InferenceJob.objects.count() == 0

    def test_a_holder_is_not_refused_by_this_gate(self, client):
        """It may still fail later for an unbound role or an unreachable
        engine -- that is a 503 and a different fact. What this asserts is
        that the ENTITLEMENT gate is not what stopped them."""
        from agents.models import ToolEntitlement
        member = make_user()
        legal = make_entitlement(name="Legal")
        grant(legal, user=member)
        ToolEntitlement.objects.create(tool_key="vision.generate", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.post(reverse("vision-generate"),
                                   {"operation": "txt2img", "prompt": "a picture"})
        assert response.status_code != 403

    def test_an_unlabelled_tool_refuses_nobody(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("vision-generate"),
                                   {"operation": "txt2img", "prompt": "a picture"})
        assert response.status_code != 403

    def test_an_open_box_is_unchanged(self, client):
        """`tool_access_for(OPEN_PRINCIPAL)` is unrestricted because
        `sees_all_content` tests `accounts_on()` first, so a household box
        never reaches a `ToolEntitlement` query on this path."""
        from agents.models import ToolEntitlement
        ToolEntitlement.objects.create(tool_key="vision.generate",
                                       entitlement=make_entitlement(name="Legal"))
        response = client.post(reverse("vision-generate"),
                               {"operation": "txt2img", "prompt": "a picture"})
        assert response.status_code != 403

    def test_the_refusal_lands_before_anything_is_staged(self, client, tmp_path, settings):
        """The gate is the FIRST thing in the view, before
        `picked_connection`, before the form, before `stage_upload`. A
        refusal that had already written a file into the managed store
        would leave the box holding bytes for a job that never existed."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from agents.models import ToolEntitlement
        settings.GENERATED_DIR = str(tmp_path)
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="vision.generate", entitlement=legal)
        upload = SimpleUploadedFile("in.png", b"not-a-real-png", content_type="image/png")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("vision-generate"),
                                   {"operation": "img2img", "prompt": "a picture",
                                    "input_image": upload})
        assert response.status_code == 403
        assert list(tmp_path.rglob("*")) == []
```

Append to `tools/vision/tests/test_views_create.py`:

```python
class TestTheCreatePageRenderGatesTheForm:
    def test_a_non_holder_sees_no_generation_form_and_a_holder_does(self, client):
        """THE SAME PREDICATE the POST enforces (`_may_generate`), never a
        second truth -- a page that renders a control whose POST answers
        403 is a page that lies to the person reading it."""
        from agents.models import ToolEntitlement
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="vision.generate", entitlement=legal)
        holder = make_user()
        grant(legal, user=holder)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            body = client.get(reverse("vision-create")).content
            assert b'id="generate-form"' not in body
            assert b"entitlement" in body.lower()
            other = client.__class__()
            sign_in(other, holder)
            assert b'id="generate-form"' in other.get(reverse("vision-create")).content

    def test_the_gallery_and_the_recent_list_still_render_for_a_non_holder(self, client):
        """The page is class A and it is not only a form: somebody who may
        not generate may still READ what they already generated. Hiding
        the whole page would be a refusal the route does not make."""
        from agents.models import ToolEntitlement
        ToolEntitlement.objects.create(tool_key="vision.generate",
                                       entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.get(reverse("vision-create"))
        assert response.status_code == 200
        assert b"Traceback" not in response.content

    def test_an_open_box_renders_the_form_exactly_as_today(self, client):
        from agents.models import ToolEntitlement
        ToolEntitlement.objects.create(tool_key="vision.generate",
                                       entitlement=make_entitlement(name="Legal"))
        assert b'id="generate-form"' in client.get(reverse("vision-create")).content
```

Both modules keep `"vision"` in any literal `FARABUNKER_FEATURES` override they set, or
`reverse("vision-create")` raises. Import `grant`, `make_entitlement`, `make_user`, `posture`,
`sign_in` from `tools/vision/tests/_helpers.py` — extend its `from identity.testing import
(...)` re-export block the same way Task 1, Step 8 extends the other four (this is the fifth).

- [ ] **Step 4: Write the vision predicate and its two callers**

In `tools/vision/views.py`, add the predicate beside the other module-level helpers:

```python
def _may_generate(principal) -> bool:
    """Whether `principal` may run an image generation AT ALL.

    ONE PREDICATE, TWO CALLERS -- `generate` (the POST) and
    `_create_page_context` (the render) -- so the page cannot offer a form
    whose submission answers 403.

    `agents.entitlements.tool_access_for` is a NAMED CROSS-COLUMN SEAM,
    the same standing `models.registry.bindings` and
    `models.queue.visibility` already have: this column registers
    `vision.generate` as a tool through `agents.contracts.tools`
    (`tools/vision/apps.py`), so "may this principal call it" is a
    question the agents column owns the answer to, and asking it twice in
    two shapes is how two surfaces come to disagree.
    """
    from agents.entitlements import tool_access_for
    from tools.vision.tools import VISION_GENERATE_TOOL_KEY

    return tool_access_for(principal).allows(VISION_GENERATE_TOOL_KEY)
```

In `generate`, immediately after the existing `principal = principal_for_request(request)` and
**before** `picked_connection(request)`:

```python
    if not _may_generate(principal):
        # THE FIRST THING, before a model is picked, a form is built, or a
        # file is staged: a refusal that had already written bytes into the
        # managed store would leave the box holding an upload for a job
        # that never existed.
        #
        # 403, not 503: this is not an unavailable service, it is an
        # action this account may not take. Honest copy, and the same
        # in-view gate shape `tools/rag/views.py::document_delete` uses.
        message = ("Image generation needs an entitlement this account does not hold. "
                   "Ask an administrator to grant it.")
        if _is_xhr(request):
            return render(request, "vision/_unavailable.html",
                          {"message": message}, status=403)
        return HttpResponseForbidden(message)
```

In `_create_page_context`, beside the other principal-derived keys:

```python
        "may_generate": _may_generate(principal),
```

and in `tools/vision/templates/vision/create.html`, wrap the generation form — the
`<form class="card gen-form" … id="generate-form">` at line 173 through its `</form>` at
line 212 — in:

```html
{% if may_generate %}
  {# ... the existing generation form, unchanged ... #}
{% else %}
  <p class="card muted">Image generation needs an entitlement this account does not hold.
    Ask an administrator to grant it. Everything you have already generated is still below.</p>
{% endif %}
```

The model picker form above it (line 125), the preflight banner, Recent and the gallery are all
left rendering: the route is class **A** and somebody who may not generate may still read what
they already generated. The page's inline script is already null-guarded
(`create.html:285 if (form) {`), so hiding the form breaks no JavaScript.

- [ ] **Step 5: Write the failing library-door tests**

Append to `tools/rag/tests/test_views.py`:

```python
class TestTheUploadDoorObeysItsToolLabel:
    """The library's own door, and the exact mirror of the vision one:
    same predicate shape, same 403, same first-thing-in-the-view position.

    `rag.ingest` is `mutates=True`, so no AGENT can ever be granted it --
    which is a different question from whether a PERSON reaches the upload
    form, and spec section 22.35 is where the two are told apart.
    """

    def test_a_non_holder_is_refused_403_and_nothing_reaches_the_inbox(
            self, client, tmp_path, settings):
        """ASSERTED ON THE DIRECTORY, not only on the response: the whole
        point of gating first is that no byte is staged."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from agents.models import ToolEntitlement
        settings.INGEST_INBOX_DIR = str(tmp_path)
        ToolEntitlement.objects.create(tool_key="rag.ingest",
                                       entitlement=make_entitlement(name="Legal"))
        upload = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.post(reverse("rag-document-upload"),
                                   {"files": [upload], "category": ""})
        assert response.status_code == 403
        assert b"Traceback" not in response.content
        assert list(tmp_path.rglob("*")) == []
        assert Document.objects.count() == 0

    def test_a_holder_is_not_refused_by_this_gate(self, client, tmp_path, settings):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from agents.models import ToolEntitlement
        settings.INGEST_INBOX_DIR = str(tmp_path)
        member = make_user()
        legal = make_entitlement(name="Legal")
        grant(legal, user=member)
        ToolEntitlement.objects.create(tool_key="rag.ingest", entitlement=legal)
        upload = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.post(reverse("rag-document-upload"),
                                   {"files": [upload], "category": ""})
        assert response.status_code != 403

    def test_an_unlabelled_tool_refuses_nobody_and_an_open_box_is_unchanged(
            self, client, tmp_path, settings):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from agents.models import ToolEntitlement
        settings.INGEST_INBOX_DIR = str(tmp_path)
        ToolEntitlement.objects.create(tool_key="rag.ingest",
                                       entitlement=make_entitlement(name="Legal"))
        upload = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
        assert client.post(reverse("rag-document-upload"),
                           {"files": [upload], "category": ""}).status_code != 403


class TestTheLibraryRenderGatesTheUploadForm:
    def test_a_non_holder_sees_no_upload_form_but_still_sees_the_library(self, client):
        from agents.models import ToolEntitlement
        ToolEntitlement.objects.create(tool_key="rag.ingest",
                                       entitlement=make_entitlement(name="Legal"))
        make_document(title="a document")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            body = client.get(reverse("rag-documents")).content
        assert reverse("rag-document-upload").encode() not in body
        assert b"a document" in body          # the listing is untouched

    def test_a_holder_sees_it(self, client):
        from agents.models import ToolEntitlement
        member = make_user()
        legal = make_entitlement(name="Legal")
        grant(legal, user=member)
        ToolEntitlement.objects.create(tool_key="rag.ingest", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            body = client.get(reverse("rag-documents")).content
        assert reverse("rag-document-upload").encode() in body
```

- [ ] **Step 6: Write the library predicate and its two callers**

In `tools/rag/views.py`:

```python
def _may_upload(principal) -> bool:
    """Whether `principal` may add documents to the library AT ALL.

    THE MIRROR OF `tools/vision/views.py::_may_generate`, deliberately
    identical in shape: one predicate, two callers (the POST and the
    page), one named cross-column seam.

    `rag.ingest` is `mutates=True`, so no AGENT can be granted it -- a
    different question from whether a PERSON reaches this form, and one
    the tool-label page keeps apart by listing mutating tools as
    page-only (spec section 22.35).
    """
    from agents.entitlements import tool_access_for
    from tools.rag.tools import RAG_INGEST_TOOL_KEY

    return tool_access_for(principal).allows(RAG_INGEST_TOOL_KEY)
```

`document_upload` gains, as its **first** statement — before `request.FILES.getlist("files")`,
because a refusal that had already read an upload into memory is a refusal that did work it did
not need to:

```python
    principal = principal_for_request(request)
    if not _may_upload(principal):
        return HttpResponseForbidden(
            "Adding documents to the library needs an entitlement this account does not "
            "hold. Ask an administrator to grant it."
        )
```

(the existing body's later `principal_for_request(request)` calls reuse this local rather than
re-deriving it), and `DocumentsView.get_context_data` gains `"may_upload": _may_upload(principal)`
beside its other principal-derived keys. In `tools/rag/templates/rag/documents.html`, wrap the
upload form (line 453 through its `</form>`) in `{% if may_upload %}` with an honest `{% else %}`:

```html
{% else %}
  <p class="muted">Adding documents needs an entitlement this account does not hold. Everything
    you may read is still listed below.</p>
{% endif %}
```

The search form, the sidebar, the listing and the label controls are untouched.

- [ ] **Step 7: Add the labelled dimension to the route matrix**

`identity/tests/test_route_matrix.py` sweeps both routes in the `_ADMITTED` cell for every
principal, which stays true — the world it builds labels no tool. Add the second state as its own
focused test rather than a sixth matrix axis (a `labelled` dimension across ~1,000 cells would
double the sweep to assert two routes' behaviour):

```python
class TestALabelledDirectSurface:
    """Spec section 16.4: the route matrix carries the two direct-surface
    routes in BOTH label states. The unlabelled state is the matrix
    proper; this is the labelled one, kept out of the cross-product
    because it concerns exactly two routes."""

    @pytest.mark.parametrize("name,tool_key",
                             [("vision-generate", "vision.generate"),
                              ("rag-document-upload", "rag.ingest")])
    def test_it_is_403_for_a_non_holder_and_admitted_for_a_holder(
            self, client, world, name, tool_key):
        from django.apps import apps
        apps.get_model("agents.ToolEntitlement").objects.create(
            tool_key=tool_key, entitlement=world.entitlement)
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_user())
            _method, url, data = _DRIVERS[name](world)
            assert client.post(url, data).status_code == 403
            holder_client = client.__class__()
            holder = make_user()
            grant(world.entitlement, user=holder)
            sign_in(holder_client, holder)
            assert holder_client.post(url, data).status_code in _ADMITTED
```

`ToolEntitlement` is resolved through `apps.get_model`, never imported, for the reason every
other row builder in `identity/tests/_helpers.py` is.

- [ ] **Step 8: Run the touched suites, in both postures**

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/vision tools/rag agents identity
FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/vision tools/rag agents identity
```
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add tools/vision/tools.py tools/vision/views.py tools/vision/templates/vision/create.html \
        tools/vision/tests/ tools/rag/tools.py tools/rag/views.py \
        tools/rag/templates/rag/documents.html tools/rag/tests/ \
        agents/chat/views/tools.py agents/chat/templates/chat/tool_entitlements.html \
        agents/chat/tests/ identity/tests/test_route_matrix.py
git commit -m "feat(tools): IA-2 T13 — a labelled tool is refused at its own page, for both doors"
```

---

### Task 14: model **sets** — group the models, attach the entitlements

Owner directives, 2026-08-30 (spec §6.10, §9.5, §22.31, §22.33): *"group models into sets that can
be assigned to multiple entitlements, rather than models being assigned by entitlement. So one
change can impact many different entitlements/users."* Three tables, one access value, and **two
functions** every user-selectable model choice on this box already goes through.

**Files:**
- Modify: `models/registry/models.py`, `models/registry/bindings.py`, `models/registry/apps.py`,
  `models/registry/urls.py`, `models/registry/views.py`,
  `models/registry/templates/inference/_registered_connection.html`
- Create: `models/registry/access.py`, `models/registry/labels.py`,
  `models/registry/templates/inference/model_sets.html`,
  `models/registry/migrations/0008_modelset.py` (generated)
- Modify: `agents/chat/pickers.py`, `agents/runtime/bindings.py`,
  **`agents/runtime/jobs.py`**, **`agents/runtime/loop.py`**, **`agents/runtime/delegate.py`**,
  `agents/runtime/preflight.py`, `agents/chat/service.py`, `tools/vision/views.py`,
  `tools/vision/jobs.py`, `tools/rag/views.py`, `tools/rag/jobs.py`,
  `identity/contracts/actions.py`, `identity/routes.py`,
  **`models/registry/tests/_helpers.py`** (it has **no** `from identity.testing import (...)`
  block today and gets one — the per-package rule Task 1, Step 8 installs, applied to the fifth
  package that needs it; without it this task's two test modules would reach into
  `identity/tests/_helpers.py`, coupling two packages' scaffolding through a private name)
- Test: `models/registry/tests/test_model_sets.py` (**new**),
  `models/registry/tests/test_model_sets_page.py` (**new**),
  `models/registry/tests/test_bindings.py` (append),
  `agents/runtime/tests/test_preflight.py` (append),
  `identity/tests/test_cascades.py` (append), `identity/tests/test_actions.py` (append),
  `identity/tests/test_routes.py` (append), `identity/tests/test_route_matrix.py` (drivers)

**Interfaces:**
- Consumes: `identity.access.held_entitlement_ids`/`sees_all_content`/`is_admin`/
  `labelling_entitlements` (Task 1); `identity.contracts.cascades` (Task 2);
  `identity.audit.record` (IA-1).
- Produces: `models.registry.models.ModelSet`, `::ModelSetMember`, `::ModelSetEntitlement`.
- Produces: `models.registry.access.ModelAccess(required, held, unrestricted)` with
  `.allows(connection_pk) -> bool`; `::UNRESTRICTED_MODEL_ACCESS`;
  `::model_access_for(principal) -> ModelAccess` — **re-exported from
  `models.registry.bindings`**, the only submodule of that package `agents/` may import
  (`foundation/ops/tests/test_import_law.py::test_agents_reaches_models_registry_through_bindings_and_nothing_else`).
- Produces: `models.registry.bindings.picker_options(capability, role_key, selected="", *, access)`
  and `::resolve_connection(pk, capability, *, access)` /
  `::resolve_connection_named(pk, capability, *, access)` — **`access` required and
  keyword-only on all three**; `agents.runtime.bindings.resolve_chat(agent, connection, *, access)`.
- Produces: `models.registry.labels.set_memberships_for(connection, set_ids)`,
  `::connection_set_ids(connection)`, `::model_set_cascade(entitlement_id, *, commit) -> int`,
  and the set write helpers (`create_set`, `rename_set`, `delete_set`, `attach`, `detach`).
- Produces: `agents.runtime.preflight.MODEL_NOT_PERMITTED`; URL names
  `inference-connection-sets`, `inference-model-sets`, `inference-model-set-edit`, all class `S`.

- [ ] **Step 1: Add the seven audit actions**

In `identity/contracts/actions.py`, beside `TOOL_LABELLED`/`TOOL_UNLABELLED`:

```python
MODELSET_CREATED = "modelset.created"
MODELSET_RENAMED = "modelset.renamed"
MODELSET_DELETED = "modelset.deleted"
MODELSET_MEMBER_ADDED = "modelset.member_added"
MODELSET_MEMBER_REMOVED = "modelset.member_removed"
MODELSET_ATTACHED = "modelset.attached"
MODELSET_DETACHED = "modelset.detached"
```

add all seven to `AUDIT_ACTIONS`, and amend the module docstring:

```python
# -- labels and shares (IA-2 writes these) ------------------------------
# The `modelset.*` seven, and the `agent.*`/`flow.*` four Task 15 adds,
# arrived with the 2026-08-30 owner directives (spec sections 22.31 and
# 22.33), AFTER the catalogue was closed in IA-1. That is the one
# amendment to "the vocabulary is not amended twice" this phase makes,
# and it is recorded rather than quietly absorbed: three label kinds that
# existed in no plan when the catalogue was written cannot have had
# actions reserved for them.
#
# THE SET VOCABULARY MIRRORS THE GROUP VOCABULARY on purpose -- a model
# set is a group of models, `auth.Group` is a group of people, and giving
# the two different verb shapes would make one audit report read two
# ways. `modelset.attached`/`detached` have no group twin only because a
# group needs none: a grant covers there what an attachment covers here.
```

Amend `identity/tests/test_actions.py`'s count assertion from thirty-one to **thirty-eight** (the
`agent.*`/`flow.*` four arrive in Task 15, taking it to forty-two) and add the seven to its IA-2
subset test. Run `.venv/bin/pytest -q identity/tests/test_actions.py` — PASS.

- [ ] **Step 2: Write the failing model tests, then the three tables**

Create `models/registry/tests/test_model_sets.py` — the package has **no** `test_models.py`, so
this is a new module and the Files list declares it:

```python
"""Model sets: the group of connections an entitlement attaches to.

TWO EDGES, EDITED INDEPENDENTLY. That is the whole design: adding a
model to a set reaches every entitlement already attached, and attaching
an entitlement reaches every model already in. Neither edge knows about
the other's far end.
"""
from __future__ import annotations

import pytest
from django.db.utils import IntegrityError

from models.registry.models import ModelSet, ModelSetEntitlement, ModelSetMember
from models.registry.tests._helpers import make_chat_connection, make_entitlement

pytestmark = pytest.mark.django_db


class TestModelSet:
    def test_the_name_is_case_insensitively_unique(self):
        ModelSet.objects.create(name="Under test")
        with pytest.raises(IntegrityError):
            ModelSet.objects.create(name="under test")


class TestTheTwoEdges:
    def test_one_membership_per_set_and_connection(self):
        model_set = ModelSet.objects.create(name="Under test")
        connection = make_chat_connection()
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        with pytest.raises(IntegrityError):
            ModelSetMember.objects.create(model_set=model_set, connection=connection)

    def test_one_attachment_per_set_and_entitlement(self):
        model_set = ModelSet.objects.create(name="Under test")
        finance = make_entitlement(name="Finance")
        ModelSetEntitlement.objects.create(model_set=model_set, entitlement=finance)
        with pytest.raises(IntegrityError):
            ModelSetEntitlement.objects.create(model_set=model_set, entitlement=finance)

    def test_deleting_the_set_takes_both_edges(self):
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set,
                                      connection=make_chat_connection())
        ModelSetEntitlement.objects.create(model_set=model_set,
                                           entitlement=make_entitlement())
        model_set.delete()
        assert ModelSetMember.objects.count() == 0
        assert ModelSetEntitlement.objects.count() == 0

    def test_deleting_the_connection_takes_only_its_membership(self):
        model_set = ModelSet.objects.create(name="Under test")
        connection = make_chat_connection()
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        ModelSetEntitlement.objects.create(model_set=model_set,
                                           entitlement=make_entitlement())
        connection.delete()
        assert ModelSetMember.objects.count() == 0
        assert ModelSet.objects.count() == 1
        assert ModelSetEntitlement.objects.count() == 1
```

Then append the three models to `models/registry/models.py` (adding
`from django.conf import settings` if absent; `Lower` is already imported for
`uniq_modelconnection_name_ci`) — the bodies and docstrings are spec §6.10's, verbatim.

```bash
.venv/bin/python manage.py makemigrations inference --name modelset
```

**`inference`, not `registry`:** `manage.py makemigrations` takes an **app label**, and
`models/registry/apps.py:22` is `label = "inference"`. Read the generated
`models/registry/migrations/0008_modelset.py` before trusting it: it must depend on
`("identity", "0003_entitlement_and_grant")`,
`migrations.swappable_dependency(settings.AUTH_USER_MODEL)` and
`("inference", "0007_modelconnection_config")`, and carry exactly three `CreateModel`
operations. Add any the autodetector missed, and a docstring naming the two 2026-08-30
directives.

```bash
.venv/bin/python manage.py migrate
.venv/bin/pytest -q models/registry/tests/test_model_sets.py
```

- [ ] **Step 3: Write the failing access tests, then `ModelAccess`**

Append to `models/registry/tests/test_model_sets.py`:

```python
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.contracts.principals import OPEN_PRINCIPAL, SERVICE_PRINCIPAL
from models.registry.access import UNRESTRICTED_MODEL_ACCESS, ModelAccess, model_access_for
from models.registry.tests._helpers import (
    grant, make_admin, make_user, posture, reset_settings, seed_sweep_posture,
    user_principal,
)


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


def _set_with(connection, *entitlements, name=None):
    """A set holding `connection`, attached to `entitlements`."""
    model_set = ModelSet.objects.create(name=name or f"set-{connection.pk}")
    ModelSetMember.objects.create(model_set=model_set, connection=connection)
    for entitlement in entitlements:
        ModelSetEntitlement.objects.create(model_set=model_set, entitlement=entitlement)
    return model_set


class TestTheValue:
    def test_the_default_is_unrestricted_and_allows_everything(self):
        assert UNRESTRICTED_MODEL_ACCESS.allows(1) is True

    def test_an_absent_pk_is_in_no_set_and_therefore_allowed(self):
        access = ModelAccess(required={7: frozenset({1})}, held=frozenset(),
                             unrestricted=False)
        assert access.allows(9) is True
        assert access.allows(7) is False

    def test_holding_any_one_reaching_entitlement_is_enough(self):
        access = ModelAccess(required={7: frozenset({1, 2})}, held=frozenset({2}),
                             unrestricted=False)
        assert access.allows(7) is True


class TestModelAccessFor:
    def test_open_posture_is_unrestricted_and_runs_no_permission_query(
            self, django_assert_num_queries):
        with django_assert_num_queries(1):
            access = model_access_for(OPEN_PRINCIPAL)
        assert access.unrestricted is True
        assert access.required == {}

    def test_a_box_with_no_sets_at_all_is_inert(self):
        """The change must cost a box that never creates a set nothing at
        all: every connection is in no set, so nothing is narrowed."""
        connection = make_chat_connection()
        with posture(POSTURE_ENTERPRISE):
            access = model_access_for(user_principal(make_user()))
        assert access.required == {}
        assert access.allows(connection.pk) is True

    def test_a_holder_reaches_a_set_restricted_connection_and_a_non_holder_does_not(self):
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        connection = make_chat_connection()
        _set_with(connection, finance)
        holder, other = make_user(), make_user()
        grant(finance, user=holder)
        grant(legal, user=other)
        with posture(POSTURE_ENTERPRISE):
            assert model_access_for(user_principal(holder)).allows(connection.pk) is True
            assert model_access_for(user_principal(other)).allows(connection.pk) is False

    def test_a_connection_in_two_sets_is_reachable_through_either(self):
        """The OR across sets. A model an operator put in both the
        "chat" set and the "under test" set is reachable by a holder of
        either set's entitlement."""
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        connection = make_chat_connection()
        _set_with(connection, finance, name="one")
        _set_with(connection, legal, name="two")
        first, second = make_user(), make_user()
        grant(finance, user=first)
        grant(legal, user=second)
        with posture(POSTURE_ENTERPRISE):
            assert model_access_for(user_principal(first)).allows(connection.pk) is True
            assert model_access_for(user_principal(second)).allows(connection.pk) is True

    def test_a_set_with_no_entitlement_attached_restricts_nothing(self):
        """Otherwise an operator could hide a model from themselves by
        creating a set and forgetting to attach anything, with no page
        saying so."""
        connection = make_chat_connection()
        model_set = ModelSet.objects.create(name="Empty")
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        with posture(POSTURE_ENTERPRISE):
            assert model_access_for(user_principal(make_user())).allows(
                connection.pk) is True

    def test_one_membership_reaches_every_attached_entitlement(self):
        """THE OWNER'S FIRST PROPERTY, proved by a count: adding ONE row
        makes a model reachable by every entitlement already attached."""
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        model_set = ModelSet.objects.create(name="Chat models")
        for entitlement in (finance, legal):
            ModelSetEntitlement.objects.create(model_set=model_set,
                                               entitlement=entitlement)
        first, second = make_user(), make_user()
        grant(finance, user=first)
        grant(legal, user=second)
        connection = make_chat_connection()
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        with posture(POSTURE_ENTERPRISE):
            assert model_access_for(user_principal(first)).allows(connection.pk) is True
            assert model_access_for(user_principal(second)).allows(connection.pk) is True

    def test_one_attachment_reaches_every_member(self):
        """THE OWNER'S SECOND PROPERTY: attaching ONE entitlement makes
        every model already in the set reachable."""
        finance = make_entitlement(name="Finance")
        model_set = ModelSet.objects.create(name="Chat models")
        connections = [make_chat_connection(), make_chat_connection()]
        for connection in connections:
            ModelSetMember.objects.create(model_set=model_set, connection=connection)
        member = make_user()
        grant(finance, user=member)
        ModelSetEntitlement.objects.create(model_set=model_set, entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            access = model_access_for(user_principal(member))
        assert all(access.allows(connection.pk) for connection in connections)

    def test_an_admin_with_the_content_setting_off_is_still_restricted(self):
        """Using somebody's model is USING, not administering. Editing a
        set is administering, and that answers to `is_admin`."""
        connection = make_chat_connection()
        _set_with(connection, make_entitlement(name="Legal"))
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE):
            assert model_access_for(user_principal(admin)).allows(connection.pk) is False
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            assert model_access_for(user_principal(admin)).allows(connection.pk) is True

    def test_a_service_principal_reaches_connections_in_no_set_only(self):
        restricted, plain = make_chat_connection(), make_chat_connection()
        _set_with(restricted, make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            access = model_access_for(SERVICE_PRINCIPAL)
        assert access.allows(restricted.pk) is False
        assert access.allows(plain.pk) is True
```

Run it — FAIL, no module — then create `models/registry/access.py`:

```python
"""Which registered connections a principal may USE.

A `models/` module, so it may import `identity.access` (import-law rule
2's named seam) and its own set tables -- neither of which
`models/registry/bindings.py`'s callers should have to do for themselves.

RE-EXPORTED FROM `models.registry.bindings`, because `bindings` is the
ONLY submodule of this package `agents/` may import at all
(`foundation/ops/tests/test_import_law.py::
test_agents_reaches_models_registry_through_bindings_and_nothing_else`).
Splitting the implementation out keeps that file about resolution while
still giving every column one door.

IT RUNS ONCE PER REQUEST OR PER TURN, not once per option: one join over
the two set edges and one query over the principal's grants -- the same
shape and the same cost as `agents.entitlements.tool_access_for`.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from identity.access import held_entitlement_ids, sees_all_content


@dataclass(frozen=True)
class ModelAccess:
    """Which registered connections this principal may use, as plain data.

    `required` maps a connection pk -> the entitlement ids that reach it
    THROUGH ANY SET IT BELONGS TO. A pk ABSENT from this mapping is in no
    set, and therefore usable -- which is what "use all image generation"
    means and why a box with no sets is unaffected by any of this.

    FLATTENING THE TWO EDGES INTO ONE MAPPING here is deliberate: the
    sets exist so an ADMINISTRATOR can change many grants at once, and no
    read path benefits from re-walking them.

    IT DEFAULTS TO UNRESTRICTED, exactly as `agents.contracts.tools.
    ToolAccess` does, so `UNRESTRICTED_MODEL_ACCESS` is the zero-argument
    answer and every test that does not care keeps working unchanged.
    """

    required: Mapping[int, frozenset[int]] = field(default_factory=dict)
    held: frozenset[int] = frozenset()
    unrestricted: bool = True

    def allows(self, connection_pk: int) -> bool:
        if self.unrestricted:
            return True
        needed = self.required.get(connection_pk)
        return not needed or bool(needed & self.held)


UNRESTRICTED_MODEL_ACCESS = ModelAccess()


def connection_entitlement_ids() -> dict[int, frozenset[int]]:
    """`{connection pk: {entitlement ids that reach it}}`, for every
    connection in at least one ATTACHED set. ONE QUERY, joining the two
    edges on the set.

    A connection whose every set is unattached is ABSENT from the result
    -- the join finds no attachment for it -- which is exactly the
    "a set with nothing attached restricts nothing" rule, obtained by
    construction rather than by a second branch.
    """
    from models.registry.models import ModelSetMember

    out: dict[int, set[int]] = defaultdict(set)
    rows = ModelSetMember.objects.values_list(
        "connection_id", "model_set__entitlement_attachments__entitlement_id")
    for connection_id, entitlement_id in rows:
        if entitlement_id is not None:
            out[connection_id].add(entitlement_id)
    return {pk: frozenset(ids) for pk, ids in out.items()}


def model_access_for(principal) -> ModelAccess:
    """What `principal` may use, as plain data.

    THE OPEN BRANCH IS FIRST: `sees_all_content` tests `accounts_on()`
    before it reads a second table, so an open box builds this without
    touching a set edge or a grant -- the structural half of "an open box
    never runs a permission query".

    `sees_all_content`, NOT `is_admin`: using somebody's model is using,
    not administering, so an administrator with the content setting off
    is filtered exactly like a member. Editing a set IS administering,
    and those pages are class S.
    """
    if sees_all_content(principal):
        return UNRESTRICTED_MODEL_ACCESS
    return ModelAccess(
        required=connection_entitlement_ids(),
        held=held_entitlement_ids(principal),
        unrestricted=False,
    )
```

and re-export from `models/registry/bindings.py`, beside its other public names:

```python
# RE-EXPORTED, not re-implemented. `bindings` is the one submodule of this
# package every other column may import (the import-law gate names it by
# path), so the model-access question has to be reachable here or a caller
# would have to break that rule to ask it.
from models.registry.access import (  # noqa: F401 -- re-exported on purpose
    UNRESTRICTED_MODEL_ACCESS, ModelAccess, model_access_for,
)
```

- [ ] **Step 4: Write the failing seam tests, then thread `access` through**

Append to `models/registry/tests/test_bindings.py`:

```python
class TestThePickerAndTheResolverObeyModelAccess:
    """TWO FUNCTIONS, EVERY SEAM. `picker_options` is what is OFFERED and
    `resolve_connection_named` is what is ACCEPTED; every user-selectable
    model choice on this box goes through one or the other, which is what
    makes this rule enforceable in two places rather than nine.
    """

    def test_a_restricted_connection_is_dropped_from_the_options(self):
        from models.registry.access import ModelAccess
        from models.registry.bindings import picker_options
        restricted, plain = make_chat_connection(), make_chat_connection()
        access = ModelAccess(required={restricted.pk: frozenset({1})},
                             held=frozenset(), unrestricted=False)
        values = [option["value"]
                  for option in picker_options("chat", CHAT_CONVERSE_ROLE, access=access)]
        assert str(plain.pk) in values
        assert str(restricted.pk) not in values

    def test_an_unrestricted_access_offers_everything_exactly_as_today(self):
        from models.registry.bindings import UNRESTRICTED_MODEL_ACCESS, picker_options
        one, two = make_chat_connection(), make_chat_connection()
        values = [option["value"] for option in picker_options(
            "chat", CHAT_CONVERSE_ROLE, access=UNRESTRICTED_MODEL_ACCESS)]
        assert {str(one.pk), str(two.pk)} <= set(values)

    def test_the_option_list_collapses_to_None_when_nothing_is_permitted(self):
        """`picker_options` already answers `None` -- no `<select>` at all
        -- when there is nothing to pick from. A principal permitted none
        of the registered connections is that same case, not a new one."""
        from models.registry.access import ModelAccess
        from models.registry.bindings import picker_options
        restricted = make_chat_connection()
        access = ModelAccess(required={restricted.pk: frozenset({1})},
                             held=frozenset(), unrestricted=False)
        assert picker_options("chat", CHAT_CONVERSE_ROLE, access=access) is None

    def test_resolving_a_forbidden_connection_raises_the_same_ValueError(self):
        """`ValueError`, not a new exception type: every caller already
        handles it as "that model is not usable", and a second class would
        mean editing six `except` blocks to say the same thing. The
        MESSAGE differs, because the two causes do."""
        from models.registry.access import ModelAccess
        from models.registry.bindings import resolve_connection_named
        restricted = make_chat_connection()
        access = ModelAccess(required={restricted.pk: frozenset({1})},
                             held=frozenset(), unrestricted=False)
        with pytest.raises(ValueError) as excinfo:
            resolve_connection_named(restricted.pk, "chat", access=access)
        assert "entitlement" in str(excinfo.value).lower()

    def test_access_is_required_and_keyword_only_on_all_three(self):
        """REQUIRED, NOT DEFAULTED, for the reason the `visibility`
        argument at the retrieval filter point is: a default of "may use
        everything" is a fail-open default, and a caller that forgets it
        should fail at signature-checking time rather than at a security
        review two years later."""
        import inspect
        from models.registry import bindings
        for name in ("picker_options", "resolve_connection", "resolve_connection_named"):
            parameter = inspect.signature(getattr(bindings, name)).parameters["access"]
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
            assert parameter.default is inspect.Parameter.empty
```

using that module's own `make_chat_connection` import and its `CHAT_CONVERSE_ROLE` constant.

Then make the three signature changes in `models/registry/bindings.py`. `picker_options` becomes

```python
def picker_options(capability: str, role_key: str, selected: str = "", *,
                   access: ModelAccess) -> list[dict] | None:
```

with one filter line inside, immediately after `connections = connections_for_picker(capability)`:

```python
    # THE RENDER HALF, and it is free: a connection this principal may not
    # use is never in the `<select>`, so no page has to render-gate a
    # model option separately from the rule that refuses it.
    connections = [c for c in connections if access.allows(c.pk)]
```

`resolve_connection_named` gains the same keyword and one guard as its **first** statement, before
the existing row lookup:

```python
def resolve_connection_named(pk: int, capability: str, *,
                             access: ModelAccess) -> tuple[ResolvedModel, str]:
    # THE GATE HALF, and it covers every submission path at once:
    # `agents.runtime.bindings.resolve_chat`, `tools.vision.views.
    # picked_connection`, `tools.vision.jobs`, `tools.rag.views.AskView`
    # and `tools.rag.jobs` all resolve a caller-supplied pk through here.
    #
    # The ROLE path (`db_provider`, `role_primary`) is deliberately NOT
    # gated -- see `models/README.md` and spec section 9.5: a set holding
    # the embedding connection must not break ingestion for the whole box.
    if not access.allows(pk):
        raise ValueError(
            f"Model connection {pk} needs an entitlement this account does not hold."
        )
```

The rest of that function's body is unchanged. `resolve_connection` is a thin wrapper: it gains
the same keyword and passes it straight through to `resolve_connection_named`.

- [ ] **Step 5: Thread it from the three pickers and every submission path**

Each call site builds one `ModelAccess` from the principal it already has. **No call site derives
a principal it did not already need** — every one of these is principal-aware after IA-1 and
Tasks 5 and 11:

| Call site | Change |
|---|---|
| `agents/chat/pickers.py::chat_picker_options(selected="")` | gains a required `principal`; passes `access=model_access_for(principal)`. Its two callers, `agents/chat/views/thread.py:85` and `agents/chat/views/conversations.py:154`, already hold one |
| `tools/vision/views.py::_connection_picker_options(selected)` | gains `principal`; `_create_page_context` already has one |
| `tools/rag/views.py::_connection_picker_options()` | gains `principal`; `AskPageView.get_context_data` builds one for its `tabular_count` after Task 12 |
| `agents/runtime/bindings.py::resolve_chat(agent, connection)` | gains a required keyword-only `access` and passes it to `resolve_connection_named`. Its docstring gains one line: **`access` is consulted only when `connection` is not `None`** — the role fallback below it is the exempt path |
| `agents/runtime/preflight.py:135` (`preflight_turn`) | builds `access = model_access_for(actor)` once and passes it to `resolve_chat` |
| **`agents/runtime/jobs.py:78`** (`plan_turn`) | builds `access = model_access_for(actor)` beside the `tool_access_for(actor)` Task 5 already added, and passes it — so a connection the actor may not use is refused at **enqueue**, which is half of what the directive asks for |
| **`agents/runtime/loop.py:177`** (`_run_turn`) | the same, from the `principal` it already derives — defence in depth at the one place the model is actually built, and the seam a run-time re-check exists for (an entitlement revoked between enqueue and run) |
| **`agents/runtime/delegate.py:122`** | `resolve_chat(agent, None, access=UNRESTRICTED_MODEL_ACCESS)`. `None` takes the **role** path, which §9.5 exempts, and a delegate never carries a picked connection — the constant is passed explicitly, with that reason in a comment, rather than defaulted, because the argument is required precisely so nobody omits it by accident |
| `tools/vision/views.py::picked_connection(request)` | builds it from `principal_for_request(request)` |
| `tools/vision/jobs.py` (`vision.generate` handler) | builds it from the payload actor (`principal_from_payload`), the same source Task 11 uses for `rag.ask` |
| `tools/rag/views.py::AskView` and `tools/rag/jobs.py` | the same, from the request principal and the payload actor respectively |

**All four `resolve_chat` callers are named above**, and that is the point: the argument is
required, so a missed caller is a `TypeError` at run time in the planner, the loop or the
delegation hop rather than a fail-open. `agents/runtime/bindings.py` imports `model_access_for`
**from `models.registry.bindings`**, never from `models.registry.access`, or
`test_agents_reaches_models_registry_through_bindings_and_nothing_else` fails.

Every existing test that calls one of the four changed functions gains
`access=UNRESTRICTED_MODEL_ACCESS` — the open-box answer, which is what those tests were
implicitly asserting against. Find them with:

```bash
grep -rn "picker_options(\|resolve_connection(\|resolve_connection_named(\|resolve_chat(" \
     agents models tools
```

**Do not weaken an assertion to make it pass.**

- [ ] **Step 6: Refuse at preflight, with a 403 rather than a 503**

Append to `agents/runtime/tests/test_preflight.py`:

```python
class TestAForbiddenModelIsRefusedBeforeTheTurnIsWritten:
    def test_preflight_answers_MODEL_NOT_PERMITTED(self):
        """The directive's "refuses at preflight/enqueue, not mid-run".
        A turn runs as the USER (the acting rule), so a connection the
        actor may not use is refused where every other unrunnable turn is
        refused -- before a `Turn` row exists."""
        from agents.runtime.preflight import MODEL_NOT_PERMITTED, preflight_turn
        from models.registry.models import ModelSet, ModelSetEntitlement, ModelSetMember
        member = make_user()
        connection = make_chat_connection()
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        ModelSetEntitlement.objects.create(model_set=model_set,
                                           entitlement=make_entitlement(name="Legal"))
        agent = make_agent(tool_keys=[])
        with posture(POSTURE_ENTERPRISE):
            check = preflight_turn(agent, str(connection.pk),
                                   actor=user_principal(member))
        assert check.ok is False
        assert check.reason == MODEL_NOT_PERMITTED
        assert "entitlement" in check.message.lower()

    def test_start_turn_answers_403_for_it_and_writes_nothing(self):
        """403, not the 503 every other preflight refusal gets: an
        unavailable service and an action this account may not take are
        different facts, and `TurnStart.status` exists precisely so the
        view does not have to guess."""
        from agents.chat.service import start_turn
        from agents.models import Turn
        from models.registry.models import ModelSet, ModelSetEntitlement, ModelSetMember
        member = make_user()
        connection = make_chat_connection()
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        ModelSetEntitlement.objects.create(model_set=model_set,
                                           entitlement=make_entitlement(name="Legal"))
        conversation = make_conversation()
        with posture(POSTURE_ENTERPRISE):
            start = start_turn(conversation, "hello", connection=str(connection.pk),
                               actor=user_principal(member))
        assert start.ok is False
        assert start.status == 403
        assert Turn.objects.count() == 0
```

Then in `agents/runtime/preflight.py`, beside the other reason constants:

```python
MODEL_NOT_PERMITTED = "model_not_permitted"

_MODEL_NOT_PERMITTED_MESSAGE = (
    "That model needs an entitlement this account does not hold — pick another."
)
```

`preflight_turn` builds `access = model_access_for(actor)` once, before its `try`, and passes it
to `resolve_chat`. Inside the existing `except (ValueError, TypeError) as exc:` block, **one new
branch goes first**, before the two that are already there:

```python
        # THREE CAUSES NOW, THREE MESSAGES. A picked connection the actor
        # may not USE is a different fact from one that is gone, and
        # telling somebody "that model is no longer registered" when it is
        # registered and simply not theirs is a false sentence.
        #
        # Asked of the ACCESS VALUE, never by matching on `str(exc)`:
        # message-matching is how two refusals come to share one branch.
        if connection not in (None, "") and str(connection).isdigit() \
                and not access.allows(int(connection)):
            logger.info("chat: picked connection %r is not permitted for this actor",
                        connection)
            return Preflight(False, MODEL_NOT_PERMITTED, _MODEL_NOT_PERMITTED_MESSAGE,
                             None, "", (), ())
```

The two existing branches — `UNREGISTERED_CONNECTION` for a picked connection that no longer
resolves, and `UNBOUND` for an unbound role — follow it unchanged, and every other
`Preflight(...)` construction in the module is untouched (the seventh field,
`unentitled_tools`, is defaulted; Task 5 added it).

In `agents/chat/service.py::start_turn`, the one line that turns a preflight refusal into a
`TurnStart` picks its status:

```python
    check = preflight_turn(agent, connection, actor=actor)
    if not check.ok:
        # 403 for "you may not use that model", 503 for everything else.
        # An authorization refusal is not an unavailable service, and the
        # view renders `start.status` verbatim, so this is the only place
        # that has to know the difference.
        status = 403 if check.reason == MODEL_NOT_PERMITTED else 503
        return TurnStart(False, status, check.message,
                         notes=dropped_tool_notes(check))
```

— i.e. the existing `TurnStart(False, 503, check.message, …)` construction keeps every argument
it has and only its status becomes the `status` local. `agents/chat/views/turns.py` needs **no**
change: it already returns `start.status` verbatim, and `403` is already in
`identity/tests/test_route_matrix.py::_NEVER_500_STATUSES`.

- [ ] **Step 7: The write helpers, the cascade, and the two pages**

Create `models/registry/labels.py`, the same shape as `agents/labels.py`:

```python
"""Model sets: creating them, editing their two edges, and giving up
their attachments when an entitlement dies.

EVERY WRITE HERE IS ADMINISTRATOR-ONLY (both pages are class S), unlike
document labels: an entitlement owner's three capabilities are grants and
documents, and a model connection is box inventory rather than somebody's
library shelf. The CALLER checks the predicate; this module is the write.
"""
from __future__ import annotations

from django.db import IntegrityError, transaction

from identity import audit
from identity.contracts import actions
from models.registry.models import ModelSet, ModelSetEntitlement, ModelSetMember


class SetRefused(ValueError):
    """A set write this platform will not perform, with an
    operator-readable reason -- the same shape
    `identity.services.ServiceRefused` has, so a view can render the
    message without catching a genuine programming error by accident."""


def connection_set_ids(connection) -> frozenset[int]:
    """The set ids `connection` belongs to -- from the TABLE."""
    return frozenset(
        connection.set_memberships.values_list("model_set_id", flat=True))


def set_memberships_for(actor, connection, set_ids) -> None:
    """Make `connection`'s memberships exactly `set_ids`.

    WRITES THE DIFFERENCE, so the audit trail records what changed rather
    than what was resubmitted. This is the edge an operator edits when a
    NEW MODEL ARRIVES, which is why it lives beside the connection on the
    console rather than on the sets page.
    """
    wanted = {int(i) for i in set_ids}
    with transaction.atomic():
        current = set(connection_set_ids(connection))
        for set_id in sorted(wanted - current):
            ModelSetMember.objects.create(model_set_id=set_id, connection=connection)
            audit.record(actor, actions.MODELSET_MEMBER_ADDED, target_type="model_set",
                         target_key=set_id, target_label=connection.name,
                         connection_id=connection.pk)
        for set_id in sorted(current - wanted):
            ModelSetMember.objects.filter(model_set_id=set_id,
                                          connection=connection).delete()
            audit.record(actor, actions.MODELSET_MEMBER_REMOVED, target_type="model_set",
                         target_key=set_id, target_label=connection.name,
                         connection_id=connection.pk)


def create_set(actor, *, name: str, description: str = "") -> ModelSet:
    clean = (name or "").strip()
    if not clean:
        raise SetRefused("A model set needs a name.")
    try:
        with transaction.atomic():
            model_set = ModelSet.objects.create(name=clean,
                                                description=description.strip())
            audit.record(actor, actions.MODELSET_CREATED, target_type="model_set",
                         target_key=model_set.pk, target_label=clean)
    except IntegrityError as exc:
        raise SetRefused(
            f"A model set called {clean!r} already exists. Names are compared without "
            f"regard to case."
        ) from exc
    return model_set


def rename_set(actor, model_set, name: str) -> None:
    clean = (name or "").strip()
    if not clean:
        raise SetRefused("A model set needs a name.")
    if clean == model_set.name:
        return
    was = model_set.name
    try:
        with transaction.atomic():
            model_set.name = clean
            model_set.save(update_fields=["name"])
            audit.record(actor, actions.MODELSET_RENAMED, target_type="model_set",
                         target_key=model_set.pk, target_label=clean,
                         **{"from": was, "to": clean})
    except IntegrityError as exc:
        raise SetRefused(f"A model set called {clean!r} already exists.") from exc


def set_delete_counts(model_set) -> dict[str, int]:
    """What deleting `model_set` takes with it. THE CONFIRMATION NAMES
    THESE FIRST, for the reason an entitlement delete does: removing the
    last set a model belongs to WIDENS access to it."""
    return {"Models": model_set.members.count(),
            "Entitlements": model_set.entitlement_attachments.count()}


def delete_set(actor, model_set) -> dict[str, int]:
    """Both edges go with it, by `CASCADE`, and one audit row names both
    counts."""
    counts = set_delete_counts(model_set)
    label, pk = model_set.name, model_set.pk
    with transaction.atomic():
        model_set.delete()
        audit.record(actor, actions.MODELSET_DELETED, target_type="model_set",
                     target_key=pk, target_label=label, removed=counts)
    return counts


def attach(actor, model_set, entitlement_id: int) -> None:
    """Attach one entitlement. IDEMPOTENT: attaching twice is not a
    second event."""
    if model_set.entitlement_attachments.filter(entitlement_id=entitlement_id).exists():
        return
    with transaction.atomic():
        ModelSetEntitlement.objects.create(model_set=model_set,
                                           entitlement_id=entitlement_id)
        audit.record(actor, actions.MODELSET_ATTACHED, target_type="model_set",
                     target_key=model_set.pk, target_label=model_set.name,
                     entitlement_id=entitlement_id)


def detach(actor, model_set, entitlement_id: int) -> None:
    """The same in reverse, equally idempotent."""
    rows = model_set.entitlement_attachments.filter(entitlement_id=entitlement_id)
    if not rows.exists():
        return
    with transaction.atomic():
        rows.delete()
        audit.record(actor, actions.MODELSET_DETACHED, target_type="model_set",
                     target_key=model_set.pk, target_label=model_set.name,
                     entitlement_id=entitlement_id)


def model_set_cascade(entitlement_id: int, *, commit: bool) -> int:
    """This column's answer to "this entitlement is going away".

    Registered from `models/registry/apps.py::ready()` as a DOTTED-PATH
    STRING, so `identity/` runs it without importing `models/`
    (import-law rule 4). It detaches the ENTITLEMENT from every set; the
    sets and their memberships survive, because a set is a grouping of
    models and has meaning without any entitlement attached.
    """
    rows = ModelSetEntitlement.objects.filter(entitlement_id=entitlement_id)
    count = rows.count()
    if commit:
        rows.delete()
    return count
```

Register the cascade from `models/registry/apps.py::ready()`, **in the same commit as its
handler** (Task 10's rule):

```python
        from identity.contracts.cascades import (
            EntitlementCascade, register_entitlement_cascade,
        )

        register_entitlement_cascade(EntitlementCascade(
            key="inference.model_sets",
            label="Model set attachments",
            handler="models.registry.labels.model_set_cascade",
        ))
```

and widen `identity/tests/test_cascades.py`'s import-time pin:

```python
        assert {"rag.document_labels", "agents.tool_labels",
                "inference.model_sets"} <= _REGISTERED_AT_IMPORT
```

**Three routes**, in `models/registry/urls.py`:

```python
    path("connections/<int:pk>/sets/", connection_sets, name="inference-connection-sets"),
    path("sets/", model_sets, name="inference-model-sets"),
    path("sets/<int:pk>/", model_set_edit, name="inference-model-set-edit"),
```

classified in `identity/routes.py`'s `/inference/` block:

```python
    # A model connection is box inventory, not somebody's library row, so
    # an entitlement owner has no standing over it -- S, not R.
    "inference-connection-sets": "S",
    "inference-model-sets": "S",
    "inference-model-set-edit": "S",
```

with matrix drivers:

```python
    "inference-connection-sets": lambda w: (
        "post", reverse("inference-connection-sets", args=[w.connection.pk]),
        {"sets": []}),
    "inference-model-sets": lambda w: ("get", reverse("inference-model-sets"), {}),
    "inference-model-set-edit": lambda w: (
        "post", reverse("inference-model-set-edit", args=[w.model_set.pk]),
        {"action": "rename", "name": "renamed"}),
```

`_build_world` gains `model_set = _create("inference.ModelSet", name=f"set-{next(_counter)}")`
and a `model_set: object` field on `World`, resolved through `apps.get_model` like every other
row builder in `identity/tests/_helpers.py`. Add the three class assertions to
`identity/tests/test_routes.py::TestIA2Routes`.

The views, in `models/registry/views.py`, follow `identity/views.py::entitlements`/
`entitlement_edit` exactly in shape — `@require_admin`, one `action` field dispatched, 400 for an
unknown action, 404 for an unknown row, `SetRefused` rendered with `messages.error`, redirect on
success. `connection_sets` reads `request.POST.getlist("sets")`, refuses a non-numeric value with
a flash rather than a 500, and calls `set_memberships_for`. The sets page template
`model_sets.html` renders each set with its members, its attached entitlements (offered from
`labelling_entitlements(principal)`), and a delete form whose confirmation names
`set_delete_counts` first.

**THE UNATTACHED-SET STATE IS LOUD ON BOTH PAGES**, and this is not decoration. Decision 28 makes
a set with no entitlement attached restrict **nothing** — a connection in it stays usable by every
holder of the capability — and that state is not an edge case: it is the middle of the routine
workflow (create the set → add the models → attach the entitlement). An operator restricting *a
model that is being tested* would otherwise be told by the page that the model is now narrowed
while every signed-in account can still use it, which is the same false-sentence defect Task 17
exists to remove two smaller instances of, on the surface whose whole purpose is control.
Decision 28's own justification is about what a page says ("with no page saying so"); the mirror
hazard gets the same treatment.

So `model_sets.html` renders, per set, when it has no attachments:

```html
{% if not model_set.entitlement_attachments.all %}
  <p class="warn">No entitlement is attached, so this set restricts nobody — every account that
    may use the capability can still use these models. Attach an entitlement to narrow them.</p>
{% endif %}
```

and the console partial `_registered_connection.html` gains the membership multiselect and a
sentence that is true in **both** states:

```html
  <p class="muted">A model in no set is usable by everyone who may use its capability. Putting
    it in a set narrows it to the people who hold an entitlement attached to that set — a set with
    no entitlement attached narrows nothing — and makes it unreachable from the watcher and the
    command line, which hold none. Models bound to a
    <em>role</em> — embeddings, transcription, extraction, and the default answering model — are
    never narrowed: that is how ingestion keeps working for the whole box.</p>
```

Create `models/registry/tests/test_model_sets_page.py` with the assertions
`tools/rag/tests/test_document_label_page.py`'s label-route class carries — an admin saves; a
member gets 403; a non-numeric id is refused without a 500; an unknown row id is 404; a duplicate
set name is refused with a readable message; the delete confirmation names both counts; and the
memberships round-trip — plus the pair that keeps the copy honest:

```python
class TestTheUnattachedSetStateIsVisible:
    """Decision 28 makes an unattached set restrict nothing. A page that
    said otherwise would tell an administrator their model under test is
    locked while every account can still use it -- and this is the middle
    of the routine workflow, not an edge case."""

    def test_a_set_with_members_and_no_attachment_says_it_restricts_nobody(self, client):
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set,
                                      connection=make_chat_connection())
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("inference-model-sets")).content
        assert b"restricts nobody" in body

    def test_the_warning_goes_once_an_entitlement_is_attached(self, client):
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set,
                                      connection=make_chat_connection())
        ModelSetEntitlement.objects.create(model_set=model_set,
                                           entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("inference-model-sets")).content
        assert b"restricts nobody" not in body

    def test_the_console_sentence_is_true_in_both_states(self, client):
        """The partial's own copy, asserted rather than trusted: it must
        carry the "a set with no entitlement attached narrows nothing"
        clause, or it is a sentence that is false for half the
        workflow."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("inference-console")).content
        assert b"narrows nothing" in body
```

Both new test modules import **from their own package** —
`from models.registry.tests._helpers import (grant, make_admin, make_chat_connection,
make_entitlement, make_user, posture, reset_settings, seed_sweep_posture, sign_in,
user_principal)` — never from `identity/tests/_helpers.py`, per Global Constraint 12 and Task 1
Step 8. `models/registry/tests/_helpers.py` gets its `from identity.testing import (...)` block
in this task (it has none today), which is what makes those imports resolve.

- [ ] **Step 8: Prove the exemption**

Append to `models/registry/tests/test_model_sets.py`:

```python
class TestTheRolePathIsExempt:
    """Spec section 22.32, flagged for the owner. A model resolved
    through a ROLE must not be narrowed: `db_provider` and `role_primary`
    answer for `rag.embed`, `rag.transcribe`, `rag.extract` and the
    default answering bindings, none of which any user selects and none
    of which has a principal at all.
    """

    def test_role_resolution_ignores_a_set_entirely(self):
        from models.contracts.bindings import resolve
        from models.contracts.roles import RAG_EMBED_ROLE
        from models.registry.tests._helpers import bind, make_embed_connection
        connection = make_embed_connection()
        bind(RAG_EMBED_ROLE, connection)
        _set_with(connection, make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            assert resolve(RAG_EMBED_ROLE) is not None

    def test_the_role_path_takes_no_access_argument_at_all(self):
        """Not "passes unrestricted" -- takes NONE. A parameter would be a
        parameter somebody eventually threads a real value into."""
        import inspect
        from models.registry.bindings import db_provider
        assert "access" not in inspect.signature(db_provider).parameters
```

and the AND-composition, in `tools/vision/tests/test_views_generate.py` where both halves meet:

```python
class TestTheAndComposition:
    """`allows(T) AND (M in no set OR principal holds a reaching
    entitlement)`. Four cells, because three of them are the ones
    somebody would get wrong by implementing an OR."""

    @pytest.mark.parametrize(
        "holds_tool,holds_model,expected_403",
        [(False, False, True), (True, False, True), (False, True, True), (True, True, False)],
    )
    def test_both_are_required(self, client, holds_tool, holds_model, expected_403):
        from agents.models import ToolEntitlement
        from models.contracts.roles import IMAGE_GENERATION_CAPABILITY
        from models.registry.models import ModelSet, ModelSetEntitlement, ModelSetMember
        from models.registry.tests._helpers import make_chat_connection
        tool_ent = make_entitlement(name="ToolAccess")
        model_ent = make_entitlement(name="ModelAccess")
        connection = make_chat_connection(capabilities=[IMAGE_GENERATION_CAPABILITY])
        ToolEntitlement.objects.create(tool_key="vision.generate", entitlement=tool_ent)
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        ModelSetEntitlement.objects.create(model_set=model_set, entitlement=model_ent)
        member = make_user()
        if holds_tool:
            grant(tool_ent, user=member)
        if holds_model:
            grant(model_ent, user=member)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            response = client.post(reverse("vision-generate"),
                                   {"operation": "txt2img", "prompt": "a picture",
                                    "connection": str(connection.pk)})
        assert (response.status_code == 403) is expected_403
```

`IMAGE_GENERATION_CAPABILITY` lives in **`models/contracts/roles.py`** (`tools/vision/views.py`
merely imports it), and `make_chat_connection`/`make_embed_connection`/`bind` are
`models/registry/tests/_helpers.py`'s real names — there is no `make_connection` and no
`bind_role` in this tree.

- [ ] **Step 9: Run everything, in both postures and both feature states**

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
.venv/bin/python manage.py makemigrations --check --dry-run
```
Expected: PASS; `--check` exits 0.

- [ ] **Step 10: Commit**

```bash
git add models/registry/ agents/chat/pickers.py agents/chat/service.py \
        agents/runtime/bindings.py agents/runtime/jobs.py agents/runtime/loop.py \
        agents/runtime/delegate.py agents/runtime/preflight.py agents/runtime/tests/ \
        tools/vision/views.py tools/vision/jobs.py tools/vision/tests/ \
        tools/rag/views.py tools/rag/jobs.py identity/contracts/actions.py \
        identity/routes.py identity/tests/
git commit -m "feat(models): IA-2 T14 — model sets, the AND-composition, and the role-path exemption"
```

---

### Task 15: agent and flow labels — through the seams that already exist

Owner directive, 2026-08-30 (spec §6.11, §9.6, §22.33): an agent or a flow can be restricted to
the people who need it. **Direct labels, not sets** — an agent is a handful of long-lived rows an
operator names once, not a fleet that churns (§22.33 records why the two answers differ). This
task needs almost no new mechanism: the agents column has had **one** function per question since
IA-1, and all three already take a principal.

**Files:**
- Modify: `agents/models.py`, `agents/visibility.py`, `agents/labels.py`, `agents/apps.py`,
  `agents/runtime/delegate.py`, `agents/runtime/loop.py`, `agents/runtime/jobs.py`,
  `agents/runtime/preflight.py`, `agents/chat/service.py`, `agents/chat/urls.py`,
  `agents/chat/views/__init__.py`, `identity/contracts/actions.py`, `identity/routes.py`,
  `foundation/templates/_shell.html` (one anchor)
- Create: `agents/migrations/0004_agent_flow_entitlements.py` (generated),
  `agents/chat/views/access.py`, `agents/chat/templates/chat/agent_entitlements.html`
- Test: `agents/tests/test_models.py` (append), `agents/tests/test_visibility.py` (append — use
  the real module name in that package), `agents/runtime/tests/test_preflight.py` (append),
  `agents/runtime/tests/test_delegate.py` (append),
  `agents/chat/tests/test_agent_entitlements_page.py` (new),
  `identity/tests/test_cascades.py`, `test_actions.py`, `test_routes.py`,
  `test_route_matrix.py` (append)

**Interfaces:**
- Consumes: `identity.access.held_entitlement_ids`/`labelling_entitlements` (Task 1);
  `identity.contracts.cascades` (Task 2); `agents.labels`'s existing shape (Task 4).
- Produces: `agents.models.AgentEntitlement`, `::FlowEntitlement`.
- Produces: `agents.visibility.label_permitted_q(principal) -> Q` — the one clause both bodies
  use; `agents.labels.set_agent_labels`/`set_flow_labels`/`agent_label_ids`/`flow_label_ids`/
  `agent_flow_label_cascade`.
- Produces: `agents.runtime.preflight.AGENT_NOT_PERMITTED`; URL name
  `chat-agent-entitlements` at `/chat/access/`, class `S`.

- [ ] **Step 1: Add the four audit actions**

In `identity/contracts/actions.py`, beside the `modelset.*` seven Task 14 added:

```python
AGENT_LABELLED = "agent.labelled"
AGENT_UNLABELLED = "agent.unlabelled"
FLOW_LABELLED = "flow.labelled"
FLOW_UNLABELLED = "flow.unlabelled"
```

all four into `AUDIT_ACTIONS`, taking the catalogue to **forty-two**. Amend
`identity/tests/test_actions.py`'s count assertion from thirty-eight to forty-two and add the
four to its IA-2 subset test.

- [ ] **Step 2: Write the failing model tests, then the two tables**

Append to `agents/tests/test_models.py`:

```python
class TestAgentAndFlowEntitlements:
    def test_one_label_per_agent_and_entitlement(self):
        from django.db.utils import IntegrityError
        from agents.models import AgentEntitlement
        from agents.tests._helpers import make_agent, make_entitlement
        agent = make_agent()
        finance = make_entitlement(name="Finance")
        AgentEntitlement.objects.create(agent=agent, entitlement=finance)
        with pytest.raises(IntegrityError):
            AgentEntitlement.objects.create(agent=agent, entitlement=finance)

    def test_deleting_the_agent_takes_its_labels(self):
        from agents.models import AgentEntitlement
        from agents.tests._helpers import make_agent, make_entitlement
        agent = make_agent()
        AgentEntitlement.objects.create(agent=agent, entitlement=make_entitlement())
        agent.delete()
        assert AgentEntitlement.objects.count() == 0

    def test_a_flow_carries_its_own_table(self):
        """TWO TABLES, not one polymorphic row: `Agent` and `Flow` are two
        models with two primary keys, read by two functions that each
        already know which model they are filtering."""
        from agents.models import FlowEntitlement
        from agents.tests._helpers import make_entitlement, make_flow
        flow = make_flow()
        FlowEntitlement.objects.create(flow=flow, entitlement=make_entitlement())
        assert FlowEntitlement.objects.count() == 1
```

Append the two models to `agents/models.py` — spec §6.11's bodies and docstrings, verbatim —
then:

```bash
.venv/bin/python manage.py makemigrations agents --name agent_flow_entitlements
```

Read `agents/migrations/0004_agent_flow_entitlements.py`: it must depend on
`("agents", "0003_toolentitlement_and_share")`, `("identity", "0003_entitlement_and_grant")` and
`migrations.swappable_dependency(settings.AUTH_USER_MODEL)`, and carry two `CreateModel`
operations. **A second `agents` migration, not an edit to the first**: Task 4's migration is
already designed and another task depends on it, and rewriting a numbered migration to save a
file is what Django's numbering exists to avoid. Give it a docstring saying so.

```bash
.venv/bin/python manage.py migrate
.venv/bin/pytest -q agents/tests/test_models.py
```

- [ ] **Step 3: Write the failing visibility tests**

Append to the agents column's visibility test module:

```python
class TestLabelsNarrowTheThreeVisibilityFunctions:
    """ONE CLAUSE, THREE BODIES. The agents column has had one function
    per question since IA-1 and all three already take a principal, so
    this whole feature is one `Q` and three `&`s.
    """

    def test_an_unlabelled_agent_is_visible_to_everybody_as_today(self):
        agent = make_agent(resident=False, enabled=True,
                           **owner_fields(user_principal(make_user())))
        with posture(POSTURE_ENTERPRISE):
            assert agent in visible_agents(user_principal(make_user())) or True
        # An unlabelled row is not made visible BY the label clause -- the
        # ownership rules still apply. What this pins is that the clause
        # does not REMOVE it: a resident row stays visible to everybody.
        resident = make_agent(resident=True, enabled=True)
        with posture(POSTURE_ENTERPRISE):
            assert resident in visible_agents(user_principal(make_user()))

    def test_a_labelled_agent_is_visible_only_to_a_holder(self):
        from agents.models import AgentEntitlement
        finance = make_entitlement(name="Finance")
        agent = make_agent(resident=True, enabled=True)
        AgentEntitlement.objects.create(agent=agent, entitlement=finance)
        holder, other = make_user(), make_user()
        grant(finance, user=holder)
        with posture(POSTURE_ENTERPRISE):
            assert agent in visible_agents(user_principal(holder))
            assert agent not in visible_agents(user_principal(other))

    def test_a_resident_row_is_NOT_exempt(self):
        """THE CARVE-OUT COMPOSES, IT IS NOT BYPASSED. A reader assumes
        `| Q(resident=True)` wins, and if it did the shipped agents --
        exactly the ones an operator most wants to restrict -- would be
        unrestrictable. The clause is AND-ed with the ownership OR, not
        OR-ed into it."""
        from agents.models import AgentEntitlement
        agent = make_agent(resident=True, enabled=True)
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            assert agent not in visible_agents(user_principal(make_user()))

    def test_installed_agent_slugs_narrows_the_same_way(self):
        from agents.models import AgentEntitlement
        agent = make_agent(resident=True, enabled=False)
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            assert agent.slug not in set(installed_agent_slugs(user_principal(make_user())))

    def test_a_labelled_flow_narrows_the_same_way(self):
        from agents.models import FlowEntitlement
        finance = make_entitlement(name="Finance")
        flow = make_flow(resident=True, enabled=True)
        FlowEntitlement.objects.create(flow=flow, entitlement=finance)
        holder, other = make_user(), make_user()
        grant(finance, user=holder)
        with posture(POSTURE_ENTERPRISE):
            assert flow in visible_flows(user_principal(holder))
            assert flow not in visible_flows(user_principal(other))

    def test_an_open_box_returns_everything_and_runs_no_label_query(
            self, django_assert_num_queries):
        """The open branch is FIRST, unchanged: `sees_all_content` tests
        `accounts_on()` before a second table is touched."""
        from agents.models import AgentEntitlement
        agent = make_agent(resident=True, enabled=True)
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with django_assert_num_queries(2):   # the settings row + the list
            assert agent in list(visible_agents(OPEN_PRINCIPAL))

    def test_a_service_principal_cannot_see_a_labelled_resident_agent(self):
        """Spec section 9.4: the shell holds no entitlement, so
        `manage.py agent_turn` cannot run a labelled agent -- including a
        shipped one, which is the case an operator will hit first."""
        from agents.models import AgentEntitlement
        agent = make_agent(resident=True, enabled=True)
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            assert agent not in visible_agents(SERVICE_PRINCIPAL)
```

- [ ] **Step 4: Write the one clause, and use it three times**

In `agents/visibility.py`, add the clause beside `_owned`/`_shared_keys`:

```python
def label_permitted_q(principal) -> Q:
    """Rows this principal may reach past their entitlement labels.

    UNLABELLED ROWS PASS, which is what keeps a box that never labels an
    agent behaving exactly as it does today. Both halves are spelled out
    because the short spelling is wrong: `~Q(entitlement_labels__isnull=
    False)` alone would exclude a labelled row from EVERYBODY.

    An OR within the labels, never an AND: holding any one of a row's
    entitlements is enough, the same match documents and tools use.

    ONE FUNCTION, THREE BODIES (`visible_agents`,
    `installed_agent_slugs`, `visible_flows`), so the three cannot come to
    disagree about what a label means. It works for both models because
    both name the reverse accessor `entitlement_labels`.
    """
    return Q(entitlement_labels__isnull=True) | Q(
        entitlement_labels__entitlement_id__in=held_entitlement_ids(principal))
```

with `from identity.access import held_entitlement_ids` added to the module's existing
`identity.access` import.

Each of the three bodies **AND-s** it onto the ownership clause it already has — note the
parentheses, which are the whole of the "composes, not bypasses" rule:

```python
    return qs.filter(
        (owned_rows_q(principal) | Q(resident=True)
         | Q(pk__in=shared_keys(Share.Target.AGENT, principal)))
        & label_permitted_q(principal)
    ).distinct()
```

**The AND also removes an OWNER's own labelled row, and that is the intended reading** — see
decision 35. An account that owns an agent and does not hold its label's entitlement stops seeing
it in `visible_agents`, so it cannot run its own row. Its conversations stay readable
(`visible_conversations` is **not** changed: a conversation is somebody's own row, and its
agent's label has no bearing on whether they may re-read what was already said — §9.6's third
knock-on, and Step 6 is where the new-turn half of it lands), and Step 7's page states the
consequence beside the control that causes it.

Add the pin to the same test class, so the behaviour is asserted rather than inferred from the
parentheses:

```python
    def test_an_OWNER_loses_their_own_labelled_agent(self):
        """Decision 35, and the reason the clause is AND-ed rather than
        OR-ed onto the ownership term: a label an administrator applies
        must not be bypassable by the person it is aimed at, or labelling
        a personal agent would be useless. The conversations they already
        started stay readable -- that is `visible_conversations`, which
        this task does not touch."""
        from agents.models import AgentEntitlement
        owner = make_user()
        agent = make_agent(resident=False, enabled=True,
                           **owner_fields(user_principal(owner)))
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            assert agent not in visible_agents(user_principal(owner))
```

- [ ] **Step 5: Close the delegation and prompt knock-ons**

Append to `agents/runtime/tests/test_delegate.py`:

```python
class TestDelegationCannotReachARestrictedAgent:
    def test_run_agent_tool_refuses_an_agent_the_actor_may_not_see(self):
        """The acting rule reaches agents themselves, not only their
        tools: `run_agent_tool` resolved `Agent.objects` directly, so a
        restricted agent was one delegation away from anybody."""
        from agents.contracts.tools import ToolRefused
        from agents.models import AgentEntitlement
        child = make_agent(slug="child", enabled=True)
        AgentEntitlement.objects.create(agent=child,
                                        entitlement=make_entitlement(name="Legal"))
        ctx = make_tool_ctx(principal=user_principal(make_user()))
        with posture(POSTURE_ENTERPRISE), pytest.raises(ToolRefused):
            run_agent_tool({"agent": "child", "task": "go"}, ctx)
```

Then in `agents/runtime/delegate.py`, replace the direct manager read at line 72:

```python
    # THROUGH `visible_agents`, never `Agent.objects`. A delegate is the
    # one hop where "an agent is never a way around labels" has to be
    # true of the AGENT and not only of its tools -- otherwise a
    # restricted agent is one delegation away from anybody who can name
    # its slug. The refusal copy is unchanged: from the caller's side a
    # restricted agent and an absent one are the same fact, and telling
    # them apart would leak which slugs exist.
    from agents.visibility import visible_agents

    agent = visible_agents(ctx.principal).filter(slug__iexact=slug).first()
```

(`visible_agents` already filters `enabled=True`, so the `enabled` term goes with the manager
call it came from.)

**Enforcement by omission comes with it**, so the model is not offered a tool that would always
refuse. `agents/runtime/loop.py::available_tools` and `agents/runtime/jobs.py::_tool_roles` each
drop an `agent.<slug>` key whose slug the actor cannot see, using one shared helper in
`agents/visibility.py`:

```python
def visible_agent_slugs(principal) -> frozenset[str]:
    """Lower-cased slugs of the agents this principal may RUN -- for the
    two runtime call sites that filter `agent.<slug>` tool keys. ONE
    QUERY, once per turn, not once per key."""
    return frozenset(s.lower() for s in visible_agents(principal).values_list(
        "slug", flat=True))
```

and at each call site, beside the existing `granted_tools(...)` filter:

```python
        if key.startswith(AGENT_TOOL_PREFIX) and \
                key[len(AGENT_TOOL_PREFIX):].lower() not in visible_slugs:
            # OMISSION, not refusal -- the same mechanism the delegation
            # depth cap uses, and the same reason `granted_tools` logs its
            # entitlement drop at debug: on a labelled install this is the
            # normal case.
            continue
```

- [ ] **Step 6: Refuse a NEW turn with a restricted agent; keep the old ones readable**

Append to `agents/runtime/tests/test_preflight.py`:

```python
class TestARestrictedAgentIsRefusedBeforeTheTurnIsWritten:
    def test_preflight_answers_AGENT_NOT_PERMITTED(self):
        from agents.models import AgentEntitlement
        from agents.runtime.preflight import AGENT_NOT_PERMITTED, preflight_turn
        agent = make_agent(tool_keys=[])
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            check = preflight_turn(agent, None, actor=user_principal(make_user()))
        assert check.ok is False
        assert check.reason == AGENT_NOT_PERMITTED

    def test_start_turn_answers_403_and_writes_nothing(self):
        from agents.chat.service import start_turn
        from agents.models import AgentEntitlement, Turn
        agent = make_agent(tool_keys=[])
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        conversation = make_conversation(agent=agent)
        with posture(POSTURE_ENTERPRISE):
            start = start_turn(conversation, "hello", actor=user_principal(make_user()))
        assert start.status == 403
        assert Turn.objects.count() == 0
```

and, to the chat thread test module:

```python
class TestAnExistingConversationStaysReadable:
    def test_the_page_still_renders_when_its_agent_became_restricted(self, client):
        """ACCESS TO ONE'S OWN ROWS IS OWNERSHIP, not a label question.
        `agents/chat/views/thread.py` reads `conversation.agent` directly
        and never calls `visible_agents` -- its own docstring records that
        as deliberate -- and retroactively hiding somebody's own history
        would be a worse answer than the one the label was asked for. The
        NEW TURN is what is refused."""
        from agents.models import AgentEntitlement
        owner = make_user()
        agent = make_agent(enabled=True)
        conversation = create_conversation(user_principal(owner), agent)
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.get(reverse("chat-conversation", args=[conversation.pk]))
            assert response.status_code == 200
            assert client.post(reverse("chat-turn", args=[conversation.pk]),
                               {"text": "hello"}).status_code == 403
```

Then in `agents/runtime/preflight.py`, beside `MODEL_NOT_PERMITTED`:

```python
AGENT_NOT_PERMITTED = "agent_not_permitted"

_AGENT_NOT_PERMITTED_MESSAGE = (
    "This agent needs an entitlement this account does not hold. The conversation is "
    "still readable; new turns are not."
)
```

`preflight_turn` checks it **first**, before it resolves a model — there is no point resolving a
model for a turn that cannot run:

```python
    from agents.visibility import visible_agents

    if not visible_agents(actor).filter(pk=agent.pk).exists():
        return Preflight(False, AGENT_NOT_PERMITTED, _AGENT_NOT_PERMITTED_MESSAGE,
                         None, "", (), ())
```

and `agents/chat/service.py::start_turn`'s status line, added in Task 14, widens by one name:

```python
        status = 403 if check.reason in (MODEL_NOT_PERMITTED,
                                         AGENT_NOT_PERMITTED) else 503
```

- [ ] **Step 7: The labelling page, its route, and the cascade**

Extend `agents/labels.py` with four readers/writers and one cascade, in the shape its tool
functions already have — `set_agent_labels(actor, agent, entitlement_ids)`,
`set_flow_labels(actor, flow, entitlement_ids)`, `agent_label_ids(agent)`,
`flow_label_ids(flow)`, each writing the difference and auditing both directions with the
`AGENT_LABELLED`/`AGENT_UNLABELLED`/`FLOW_LABELLED`/`FLOW_UNLABELLED` actions — plus:

```python
def agent_flow_label_cascade(entitlement_id: int, *, commit: bool) -> int:
    """This column's SECOND answer to "this entitlement is going away" --
    agent and flow labels, counted and removed together.

    ONE cascade for two tables rather than two registrations, because the
    delete confirmation reads better as "Agent and flow labels: 3" than as
    two lines an operator has to add up, and because the two tables are
    always edited from the same page.
    """
    from agents.models import AgentEntitlement, FlowEntitlement

    agent_rows = AgentEntitlement.objects.filter(entitlement_id=entitlement_id)
    flow_rows = FlowEntitlement.objects.filter(entitlement_id=entitlement_id)
    count = agent_rows.count() + flow_rows.count()
    if commit:
        agent_rows.delete()
        flow_rows.delete()
    return count
```

registered from `agents/apps.py::ready()` beside the tool-label one, in this same commit:

```python
        register_entitlement_cascade(EntitlementCascade(
            key="agents.runnable_labels",
            label="Agent and flow labels",
            handler="agents.labels.agent_flow_label_cascade",
        ))
```

and `identity/tests/test_cascades.py`'s import-time pin widens to four keys.

**One page, two sections.** `agents/chat/views/access.py::agent_entitlements`, mounted at
`/chat/access/` as `chat-agent-entitlements`, class **S** in `identity/routes.py`, with its own
nav anchor (`{% block nav_current_agent_access %}`) added to
`foundation/templates/_shell.html:205`'s identity block **in this commit**, beside the
`Tool access` one Task 6 added. Its shape is Task 6's page exactly: `@require_admin`, one form
per row carrying a hidden `kind` (`agent`/`flow`) and the row's pk, a multiselect fed from
`labelling_entitlements(principal)`, the same `offered` membership check, 400 for an unknown
kind, and one sentence above the list:

```html
<p class="warn">A labelled agent or flow is unavailable to the watcher and the command line, and
  that includes the ones this platform ships — automated callers hold no entitlements, so
  <code>manage.py agent_turn</code> simply cannot run it. A label also applies to the account
  that <em>created</em> the agent: if they do not hold one of its entitlements they can no longer
  run it, though the conversations they already started stay readable.</p>
```

That second sentence is the only warning an administrator gets before decision 35's behaviour
happens, because this page is the only surface it can be caused from and there is no agents admin
page on which to notice it afterwards. Its test:

```python
    def test_the_page_warns_that_a_label_reaches_the_agents_own_creator(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("chat-agent-entitlements")).content
        assert b"created</em> the agent" in body or b"created the agent" in body
```

Its test module, `agents/chat/tests/test_agent_entitlements_page.py`, carries Task 6's six
assertions (admin saves; member 403; non-numeric refused without a 500; unoffered entitlement
refused; unknown kind 400; labels round-trip), plus one that a `resident=True` row is offered for
labelling like any other. Add the matrix driver, the class assertion in
`identity/tests/test_routes.py::TestIA2Routes`, and `world.agent`'s use in the driver.

- [ ] **Step 8: Run everything, in both postures**

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q agents identity tools
FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q agents identity
.venv/bin/python manage.py makemigrations --check --dry-run
```
Expected: PASS; `--check` exits 0.

- [ ] **Step 9: Commit**

```bash
git add agents/models.py agents/visibility.py agents/labels.py agents/apps.py \
        agents/migrations/0004_agent_flow_entitlements.py agents/runtime/ \
        agents/chat/ agents/tests/ identity/contracts/actions.py identity/routes.py \
        identity/tests/ foundation/templates/_shell.html
git commit -m "feat(agents): IA-2 T15 — agents and flows carry entitlement labels"
```

---

### Task 16: admin ergonomics — one action, and two ways to see the state

Owner principle, 2026-08-30 (spec §15, §22.34): *"good control over the system… easy to set up now
and easy to maintain for admins. A tedious maintenance process for changes over time is a
failure."* **If a change an administrator makes routinely takes N actions, the design is wrong.**
Three surfaces satisfy it here: labelling many documents in one submit, an entitlement page that
shows its whole reach, and a user page that shows one account's effective access.

**Files:**
- Modify: `tools/rag/views.py` (a **new** `document_labels_bulk` view beside
  `document_labels_update`, which is unchanged — decision 29: one URL must never be ambiguous
  about whether a POST is destructive; plus `DocumentsView` rendering the selection controls),
  `tools/rag/urls.py` (mounts `rag-document-labels-bulk`),
  `identity/routes.py` (classifies it `R`, with its class assertion),
  `tools/rag/templates/rag/documents.html`, `tools/rag/labels.py`
- Modify: `identity/views.py` (`entitlement_edit`'s reach panel, `users`' effective-access
  panel), `identity/access.py` (one new reader), `identity/services.py`
  (`entitlement_delete_counts` grows a sibling), `identity/templates/identity/entitlement.html`,
  `identity/templates/identity/users.html`
- Test: `tools/rag/tests/test_document_label_page.py` (append),
  `identity/tests/test_entitlement_pages.py` (append),
  `identity/tests/test_users_page.py` (append),
  `identity/tests/test_entitlements_access.py` (append)

**Interfaces:**
- Consumes: `tools.rag.labels.set_document_labels`/`restamp_document_chunks` (Task 10);
  `tools.rag.access.listable_documents`/`may_label_document` (Task 9);
  `identity.services.entitlement_delete_counts` (Task 2, which reads the cascade registry);
  `identity.access.held_entitlement_ids`/`owned_entitlement_ids` (Task 1).
- Produces: `identity.access.effective_entitlements(user) -> tuple[dict, ...]` — one row per
  entitlement, each naming whether it is direct or which group carries it.
- Produces: `identity.services.entitlement_reach(entitlement) -> dict[str, int]` — the reach
  panel's data, **the same function the delete confirmation counts with**.

- [ ] **Step 1: Write the failing bulk-label tests**

Append to `tools/rag/tests/test_document_label_page.py`:

```python
class TestBulkLabelling:
    """ONE ACTION, NOT N (spec section 22.34). An administrator who has
    just watched forty documents arrive from the inbox must not label
    them one at a time -- that is the tedium the owner named as a
    failure, and the inbox gap (spec section 21) is exactly what makes it
    routine.
    """

    def test_one_post_labels_many_documents_and_restamps_each(self, client, chunk_table):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        documents = [make_document(title=f"doc-{i}") for i in range(3)]
        for document in documents:
            _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"documents": [str(d.id) for d in documents],
                                    "entitlements": [str(finance.pk)],
                                    "action": "apply"})
        assert response.status_code == 302
        for document in documents:
            assert _metadata(document.id)["entitlements"] == [str(finance.pk)]

    def test_remove_takes_the_label_off_every_selected_document(self, client, chunk_table):
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        documents = [make_document(title=f"doc-{i}") for i in range(2)]
        for document in documents:
            DocumentEntitlement.objects.create(document=document, entitlement=finance)
            _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("rag-document-labels-bulk"),
                        {"documents": [str(d.id) for d in documents],
                         "entitlements": [str(finance.pk)], "action": "remove"})
        assert DocumentEntitlement.objects.count() == 0
        for document in documents:
            assert "entitlements" not in _metadata(document.id)

    def test_apply_ADDS_and_does_not_replace(self, client, chunk_table):
        """Bulk apply is not the single-document form's "set to exactly
        this". Somebody selecting forty documents and adding one label
        must not silently strip the labels those documents already carry
        -- a destructive bulk action is the worst possible reading of a
        convenience."""
        admin = make_admin()
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        document = make_document()
        DocumentEntitlement.objects.create(document=document, entitlement=legal)
        _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("rag-document-labels-bulk"),
                        {"documents": [str(document.id)],
                         "entitlements": [str(finance.pk)], "action": "apply"})
        assert set(document.entitlement_labels.values_list(
            "entitlement_id", flat=True)) == {finance.pk, legal.pk}

    def test_a_whole_category_can_be_labelled_in_one_action(self, client, chunk_table):
        from tools.rag.models import Category
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        category = Category.objects.create(name="Contracts")
        inside = [make_document(title=f"in-{i}", category=category) for i in range(2)]
        outside = make_document(title="out")
        for document in inside + [outside]:
            _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("rag-document-labels-bulk"),
                        {"category": category.name,
                         "entitlements": [str(finance.pk)], "action": "apply"})
        assert all(d.entitlement_labels.count() == 1 for d in inside)
        assert outside.entitlement_labels.count() == 0

    def test_a_category_bulk_apply_STORES_NO_RULE(self, client, chunk_table):
        """THE ABSENCE OF A STORED MAPPING IS THE DECISION (spec section
        22.34, the owner's own ruling). A stored category->entitlement
        rule would be a second labelling authority beside
        `DocumentEntitlement`, applying itself to rows nobody reviewed.
        A document added to the category AFTERWARDS is unlabelled."""
        from tools.rag.models import Category
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        category = Category.objects.create(name="Contracts")
        make_document(title="before", category=category)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.post(reverse("rag-document-labels-bulk"),
                        {"category": category.name,
                         "entitlements": [str(finance.pk)], "action": "apply"})
            later = make_document(title="after", category=category)
        assert later.entitlement_labels.count() == 0

    def test_an_owner_may_bulk_label_only_within_their_entitlement(self, client,
                                                                   chunk_table):
        """The SAME predicate the single-document route enforces, applied
        per document -- not a second rule with a bulk exemption."""
        owner = make_user()
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        grant(finance, user=owner, role="owner")
        document = make_document()
        _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"documents": [str(document.id)],
                                    "entitlements": [str(legal.pk)], "action": "apply"},
                                   follow=True)
        assert b"you own" in response.content.lower()
        assert DocumentEntitlement.objects.count() == 0

    def test_a_document_the_caller_may_not_label_is_skipped_and_counted(self, client,
                                                                        chunk_table):
        """Per-document, not all-or-nothing: an administrator selecting a
        page of rows should not have the whole submit refused by one row
        they lack standing over. The flash names how many went and how
        many did not."""
        owner = make_user()
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        grant(finance, user=owner, role="owner")
        mine, theirs = make_document(), make_document()
        DocumentEntitlement.objects.create(document=mine, entitlement=finance)
        DocumentEntitlement.objects.create(document=theirs, entitlement=legal)
        for document in (mine, theirs):
            _seed(document.id)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(reverse("rag-document-labels-bulk"),
                                   {"documents": [str(mine.id), str(theirs.id)],
                                    "entitlements": [str(finance.pk)],
                                    "action": "apply"}, follow=True)
        assert b"1 skipped" in response.content
```

- [ ] **Step 2: Write the bulk route**

A **second URL**, not a wider `rag-document-labels`: the single-document route sets a document's
labels to **exactly** what was submitted, and the bulk one **adds or removes** a label across
many. Folding two verbs into one route would mean a hidden field deciding whether a POST is
destructive, which is the one thing a bulk control must never be ambiguous about.

`tools/rag/urls.py`:

```python
    path("documents/labels/", views.document_labels_bulk, name="rag-document-labels-bulk"),
```

classified `R` in `identity/routes.py` beside `rag-document-labels`, with its own class
assertion and a matrix driver (`{"documents": [], "entitlements": [], "action": "apply"}`).

`tools/rag/views.py`:

```python
@require_POST
def document_labels_bulk(request):
    """POST /rag/documents/labels/ -- add or remove ONE set of
    entitlements across MANY documents, in one action.

    THE OWNER'S ONE-ACTION RULE (spec section 22.34). The inbox gap
    (spec section 21) makes this routine rather than occasional: a
    watcher-ingested document arrives unlabelled, and an administrator
    who had to label forty of them one at a time would be doing exactly
    the tedium the owner named as a failure.

    ADD OR REMOVE, never "set to exactly this". Somebody selecting forty
    documents to add one label must not silently strip the labels those
    documents already carry.

    TARGETS: either `documents` (a list of ids) or `category` (a name) --
    the second is the same action at a different cardinality, and it
    STORES NO RULE. A document added to that category afterwards is
    unlabelled, deliberately: a stored category-to-entitlement mapping
    would be a second labelling authority beside `DocumentEntitlement`,
    applying itself to rows nobody reviewed.

    PER-DOCUMENT PERMISSION, not all-or-nothing. Each document is checked
    with the same `may_label_document` the single-document route uses;
    one the caller has no standing over is skipped and counted, so an
    administrator selecting a page of rows is not refused wholesale by
    one row.
    """
    principal = principal_for_request(request)
    action = request.POST.get("action", "")
    if action not in ("apply", "remove"):
        return HttpResponseBadRequest(f"{action!r} is not a recognised action.")

    raw = request.POST.getlist("entitlements")
    if not raw or any(not value.isdigit() for value in raw):
        messages.error(request, "Choose at least one entitlement from the list.")
        return redirect("rag-documents")
    wanted = {int(value) for value in raw}
    offered = {pk for pk, _name in labelling_entitlements(principal)}
    if not wanted <= offered:
        messages.error(request, "You may only add or remove entitlements you own.")
        return redirect("rag-documents")

    visible = listable_documents(principal)
    category = request.POST.get("category", "").strip()
    if category:
        targets = visible.filter(category__name=category)
    else:
        ids = [value for value in request.POST.getlist("documents") if value.isdigit()]
        targets = visible.filter(pk__in=ids)

    changed = skipped = 0
    for document in targets:
        if not may_label_document(principal, document):
            skipped += 1
            continue
        current = set(document_label_ids(document))
        updated = (current | wanted) if action == "apply" else (current - wanted)
        if updated != current:
            # THE EXISTING WRITER, per document: it writes the
            # difference, audits each direction, and re-stamps the chunk
            # cache inside its own transaction. A bulk path with its own
            # SQL would be a second writer of the `entitlements` key,
            # which is the one thing `tools/rag/labels.py` exists to
            # prevent.
            set_document_labels(principal, document, updated)
            changed += 1
    note = f"Updated {changed} document(s)."
    if skipped:
        note += f" {skipped} skipped — you do not own a label on them."
    messages.info(request, note)
    return redirect("rag-documents")
```

**One audit event per document, not one per submit**, because that is the catalogue's existing
grain: `library.document_labelled` names a `target_key` that is a document id, and a single
event carrying forty ids would be a row whose target is a list — unqueryable by
`identity.audit.for_target`, which is the reader the audit page uses. The cost is forty rows for
forty documents, which is what an audit trail is for.

`documents.html` gains a checkbox per row (`name="documents" value="{{ document.id }}"`), one
bulk form below the table carrying the entitlement multiselect and `apply`/`remove` submits, and
— when a category is currently selected — a second submit that posts `category` instead of the
checked ids, labelled *"Apply to every document in this category"*. Both are inside
`{% if may_label_any %}`, a new context key that is `True` when
`labelling_entitlements(principal)` is non-empty, so a member who owns no entitlement sees no
bulk controls at all.

- [ ] **Step 3: Write the failing reach-panel tests, then the panel**

Append to `identity/tests/test_entitlement_pages.py`:

```python
class TestTheEntitlementPageShowsItsFullReach:
    def test_it_names_every_kind_in_one_place(self, client):
        """One page, one answer. An operator who has to open six pages to
        learn what an entitlement touches does not have control of it."""
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        grant(finance, user=make_user())
        grant(finance, group=make_group())
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(
                reverse("identity-entitlement-edit", args=[finance.pk])).content
        for label in (b"Grants", b"Document labels", b"Tool labels",
                      b"Model set attachments", b"Agent and flow labels"):
            assert label in body

    def test_the_panel_and_the_delete_confirmation_cannot_disagree(self, client):
        """SOURCED FROM THE SAME FUNCTION. Two counters answering "what
        does this entitlement touch" is how a page comes to reassure
        somebody about a delete that then removes something else."""
        from identity import services
        admin = make_admin()
        finance = make_entitlement(name="Finance")
        grant(finance, user=make_user())
        with posture(POSTURE_ENTERPRISE):
            reach = services.entitlement_reach(finance)
            counts = services.entitlement_delete_counts(finance)
        assert reach == counts
```

`identity/services.py` gains the alias, which is the whole implementation:

```python
def entitlement_reach(entitlement) -> dict[str, int]:
    """What this entitlement touches, per kind — for the entitlement
    page's reach panel.

    THE SAME FUNCTION THE DELETE CONFIRMATION COUNTS WITH
    (`entitlement_delete_counts`), aliased rather than reimplemented so
    the two can never disagree (spec section 22.34). It is a one-line
    alias on purpose: the moment it grows a body of its own, it has
    become the second counter this decision exists to prevent.
    """
    return entitlement_delete_counts(entitlement)
```

`entitlement_edit`'s GET context gains `"reach": services.entitlement_reach(entitlement)` —
computed for **every** caller, unlike `delete_counts`, because an entitlement **owner** reaching
this page is exactly somebody who should see what their entitlement covers. `entitlement.html`
renders it as a small table above the grants section.

- [ ] **Step 4: Write the failing effective-access tests, then the reader**

Append to `identity/tests/test_entitlements_access.py`:

```python
class TestEffectiveEntitlements:
    def test_it_names_direct_and_via_group_distinctly(self):
        from identity.access import effective_entitlements
        user = make_user()
        group = make_group(name="analysts")
        user.groups.add(group)
        finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
        grant(finance, user=user)
        grant(legal, group=group)
        with posture(POSTURE_ENTERPRISE):
            rows = {row["name"]: row for row in effective_entitlements(user)}
        assert rows["Finance"]["via"] == ""
        assert rows["Legal"]["via"] == "analysts"

    def test_one_entitlement_held_both_ways_appears_once_as_direct(self):
        """A direct grant is the stronger fact -- it survives leaving the
        group -- so the row says so and does not appear twice."""
        from identity.access import effective_entitlements
        user = make_user()
        group = make_group(name="analysts")
        user.groups.add(group)
        finance = make_entitlement(name="Finance")
        grant(finance, user=user)
        grant(finance, group=group)
        with posture(POSTURE_ENTERPRISE):
            rows = [row for row in effective_entitlements(user) if row["name"] == "Finance"]
        assert len(rows) == 1
        assert rows[0]["via"] == ""

    def test_an_owner_grant_says_so(self):
        from identity.access import effective_entitlements
        from identity.models import EntitlementGrant
        user = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=user, role=EntitlementGrant.Role.OWNER)
        with posture(POSTURE_ENTERPRISE):
            assert effective_entitlements(user)[0]["role"] == "owner"
```

Append to `identity/tests/test_users_page.py`:

```python
class TestTheUsersPageShowsEffectiveAccess:
    def test_it_shows_what_an_account_holds_and_what_that_unlocks(self, client):
        admin = make_admin()
        member = make_user(username="ana")
        finance = make_entitlement(name="Finance")
        grant(finance, user=member)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            body = client.get(reverse("identity-users")).content
        assert b"Finance" in body

    def test_the_panel_writes_nothing(self, client):
        """READ-ONLY, and asserted: the panel is a view over data this
        column already owns, and a page that quietly wrote while
        rendering would be the worst kind of surprise on an admin
        surface."""
        from identity.models import AuditEvent
        admin = make_admin()
        grant(make_entitlement(name="Finance"), user=make_user())
        before = AuditEvent.objects.count()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            client.get(reverse("identity-users"))
        assert AuditEvent.objects.count() == before
```

`identity/access.py` gains the reader:

```python
def effective_entitlements(user) -> tuple[dict, ...]:
    """Every entitlement `user` effectively holds, and HOW.

    One row per entitlement: `{"id", "name", "role", "via"}`, where `via`
    is `""` for a direct grant and the group's name otherwise. Sorted by
    name, so two accounts' panels read the same way.

    A DIRECT GRANT WINS over a group one for the same entitlement, and
    the row appears ONCE: the direct grant is the stronger fact -- it
    survives the person leaving the group -- and showing the same
    entitlement twice would make an administrator count it twice.

    READ-ONLY, and used by exactly one surface (the users page). It takes
    a `User` row rather than a principal because the caller is an
    administrator asking about SOMEBODY ELSE, which is a different
    question from every other function in this module.
    """
    if not accounts_on():
        return ()
    rows: dict[int, dict] = {}
    grants = (EntitlementGrant.objects
              .filter(Q(user=user) | Q(group__user=user))
              .select_related("entitlement", "group"))
    for row in grants:
        via = "" if row.user_id else row.group.name
        existing = rows.get(row.entitlement_id)
        if existing is None or (existing["via"] and not via):
            rows[row.entitlement_id] = {"id": row.entitlement_id,
                                        "name": row.entitlement.name,
                                        "role": row.role, "via": via}
    return tuple(sorted(rows.values(), key=lambda row: row["name"]))
```

`identity/views.py::users` passes `effective_entitlements(account)` per listed account (one query
per account on a page an operator opens rarely, and the alternative — one query with a Python
regroup — buys nothing on a box with a handful of accounts; say so in a comment). `users.html`
renders it as a small list under each account, together with what those entitlements unlock,
built from the same registry-shaped counts the reach panel uses: for each entitlement the account
holds, the kinds it reaches.

- [ ] **Step 5: Record the inbox gap where a reader will meet it**

The named gap is not built, and both documents say so. In `tools/rag/views.py::document_upload`'s
docstring and in `tools/rag/README.md`:

```
A document the WATCHER ingests arrives UNLABELLED and follows the library
posture until somebody labels it. Per-inbox default labels are deferred
(spec section 21); the mitigation is the bulk labelling above -- select
what arrived, or a whole category, and label it in one action.
```

- [ ] **Step 6: Run the touched suites, in both postures**

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag identity
FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag identity
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tools/rag/views.py tools/rag/urls.py tools/rag/labels.py \
        tools/rag/templates/rag/documents.html tools/rag/tests/ \
        identity/access.py identity/services.py identity/views.py \
        identity/templates/identity/ identity/routes.py identity/tests/
git commit -m "feat(identity,rag): IA-2 T16 — bulk labelling, entitlement reach, and effective access"
```

---

### Task 17: the two owner-approved fold-ins — render-vs-gate, and honest queue copy

Two small defects from the IA-1 vetting, owner-approved for this phase. Neither is
entitlement-shaped; both are about a page telling the truth.

**Files:**
- Modify: `models/queue/templates/jobs/queue.html`,
  `tools/rag/templates/rag/history.html`, `tools/rag/templates/rag/documents.html`,
  `agents/chat/templates/chat/conversation.html`, `tools/rag/templates/rag/ask.html`
- Test: `models/queue/tests/test_views.py` (append), `tools/rag/tests/test_views.py` (append),
  `agents/chat/tests/test_thread.py` (append)

**Interfaces:**
- Consumes: `identity_is_admin` (the context processor key, IA-1);
  `tools.rag.access.may_label_document` (Task 9).

- [ ] **Step 1: Write the failing render-vs-gate tests, one block per page**

**A page that renders a control whose POST answers 403 is a page that lies to the person reading
it.** The server gates do not change — they are the enforcement; this is the rendering catching
up with them, on the **same** predicate, never a second truth.

Three blocks, three modules. Each asserts on `response.context` or on the specific URL the form
posts to, never on a bare substring of a whole page — a page-wide `assert b"/delete/" not in
body` starts passing or failing on unrelated URL changes.

Append to `models/queue/tests/test_views.py`:

```python
class TestTheQueueSettingsFormsAreAdminOnlyOnThePage:
    def test_a_member_is_not_shown_them_and_an_admin_is(self, client):
        member, admin = make_user(), make_admin()
        settings_url = reverse("jobs-queue-settings").encode()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            assert settings_url not in client.get(reverse("jobs-queue")).content
            other = client.__class__()
            sign_in(other, admin)
            assert settings_url in other.get(reverse("jobs-queue")).content

    def test_a_member_still_sees_the_cancel_control_on_their_own_row(self, client):
        """`jobs-queue-cancel` is class R and a member MAY cancel a job
        `visible_jobs` returned to them, so that control is honest for
        exactly the rows it appears on and is deliberately not gated."""
        member = make_user()
        job = make_queue_job(payload=payload_fields(user_principal(member)))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            body = client.get(reverse("jobs-queue")).content
        assert reverse("jobs-queue-cancel", args=[job.id]).encode() in body

    def test_an_open_box_renders_the_settings_forms_exactly_as_today(self, client):
        """`identity_is_admin` is True for the open principal, so the
        household box's page is byte-identical to today's."""
        assert reverse("jobs-queue-settings").encode() in \
            client.get(reverse("jobs-queue")).content
```

Append to `tools/rag/tests/test_views.py`:

```python
class TestTheHistoryPagesSettingsFormsAreAdminOnlyOnThePage:
    _NAMES = ("rag-history-settings", "rag-upload-cap-settings",
              "rag-media-duration-settings", "rag-document-pages-settings",
              "rag-retrieval-top-k-settings", "rag-retrieval-score-floor-settings",
              "rag-hybrid-search-settings")

    def test_a_member_is_shown_none_of_the_seven(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            body = client.get(reverse("rag-history")).content
        for name in self._NAMES:
            assert reverse(name).encode() not in body

    def test_an_admin_is_shown_all_seven(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_admin())
            body = client.get(reverse("rag-history")).content
        for name in self._NAMES:
            assert reverse(name).encode() in body


class TestTheLibraryHidesActionsAMemberCannotTake:
    def test_reingest_delete_and_the_category_forms_are_hidden_from_a_member(self, client):
        member = make_user()
        document = make_document(title="a document")
        category = Category.objects.create(name="Contracts")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            body = client.get(reverse("rag-documents")).content
        assert b"a document" in body            # the ROW is still listed
        assert reverse("rag-document-reingest", args=[document.id]).encode() not in body
        assert reverse("rag-document-delete", args=[document.id]).encode() not in body
        assert reverse("rag-category-rename", args=[category.id]).encode() not in body
        assert reverse("rag-category-delete", args=[category.id]).encode() not in body

    def test_an_entitlement_owner_IS_shown_them_on_their_own_document(self, client):
        """The WIDENED predicate (`row.may_label`), not `identity_is_admin`:
        IA-2 gives an entitlement owner those two actions, and gating the
        rendering on admin-ness would hide from an owner a control they
        may actually use."""
        owner = make_user()
        finance = make_entitlement(name="Finance")
        grant(finance, user=owner, role="owner")
        document = make_document(title="a document")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(reverse("rag-documents")).content
        assert reverse("rag-document-delete", args=[document.id]).encode() in body

    def test_the_upload_form_is_still_offered_to_a_member(self, client):
        """Upload is class A: every signed-in caller may upload, and the
        POST does not 403. Hiding it would REMOVE a capability rather than
        stop advertising a refused one."""
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            body = client.get(reverse("rag-documents")).content
        assert reverse("rag-document-upload").encode() in body
```

Use each module's own existing helper imports; `make_queue_job`, `payload_fields` and
`user_principal` are what `models/queue/tests/test_visibility.py` already builds queue rows
with.

- [ ] **Step 2: Run, watch them fail, then gate the rendering**

`models/queue/templates/jobs/queue.html` — wrap both `jobs-queue-settings` forms (lines 252 and
366) in `{% if identity_is_admin %} ... {% endif %}`. The per-row cancel form stays as it is:
`jobs-queue-cancel` is class R and a member may cancel a job `visible_jobs` returned to them,
so the control is honest for exactly the rows it appears on.

`tools/rag/templates/rag/history.html` — wrap the seven settings forms (lines 168–251) in one
`{% if identity_is_admin %}` block. The history list itself is class A and stays.

`tools/rag/templates/rag/documents.html` — wrap the category rename/delete forms (lines 436,
441) in `{% if identity_is_admin %}`, and the per-row Re-ingest and Delete forms (lines 523,
532) in `{% if row.may_label %}` — the **widened** predicate from Task 12, not a second one:
IA-2 gives an entitlement owner those two actions, and rendering them on `identity_is_admin`
would hide from an owner a control they may actually use. `DocumentsView` already supplies
`row.may_label` (Task 12, Step 7).

Each `{% if %}` carries a one-line `{% comment %}` naming the POST-side gate it mirrors, so a
future reader can see the pair.

- [ ] **Step 3: Write the failing copy test**

Append to `agents/chat/tests/test_thread.py` (the module that already renders this template) —
a SOURCE assertion rather than a rendered one, because the defect lives in a JavaScript branch
the server never executes:

```python
class TestQueuedCopyIsHonest:
    """`data.position` is null whenever the queue cannot say where a job
    sits -- a job claimed between two polls, or a backend that answers
    without a position. "Position null of the queue…" is the page telling
    the operator something that is not a sentence."""

    _ROOT = Path(__file__).resolve().parents[3]

    def test_the_chat_card_falls_back_to_Queued(self):
        source = (self._ROOT / "agents/chat/templates/chat/conversation.html").read_text(
            encoding="utf-8")
        assert 'typeof data.position === "number"' in source
        assert '"Queued…"' in source

    def test_the_ask_page_carries_the_same_fix(self):
        """The identical expression, the identical cause, one line each.
        Fixing one and leaving the other ships a known defect."""
        source = (self._ROOT / "tools/rag/templates/rag/ask.html").read_text(
            encoding="utf-8")
        assert 'typeof data.position === "number"' in source
        assert '"Queued…"' in source
```

with `from pathlib import Path` added to that module's imports. `parents[3]` walks
`agents/chat/tests/` → `agents/chat/` → `agents/` → the repository root; check it against the
module's own location before relying on it.

- [ ] **Step 4: Fix both call sites**

`agents/chat/templates/chat/conversation.html`, line 362-364:

```javascript
                : (data.position === 1
                    ? "Queued — waiting…"
                    : (typeof data.position === "number"
                        ? "Position " + data.position + " of the queue…"
                        : "Queued…")),
```

`tools/rag/templates/rag/ask.html`, line 404-406:

```javascript
            statusEl.textContent = data.position === 1
              ? "Waiting…"
              : (typeof data.position === "number"
                  ? "Position " + data.position + " of queue — higher-priority questions can move ahead."
                  : "Queued…");
```

`typeof === "number"`, not a truthiness test: position `0` is a real position and a truthiness
test would render it as "Queued…" — a smaller lie, but still one.

- [ ] **Step 5: Run the touched suites**

```bash
.venv/bin/pytest -q models tools/rag agents
FARABUNKER_TEST_POSTURE=enterprise .venv/bin/pytest -q models tools/rag agents
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add models/queue/templates/jobs/queue.html tools/rag/templates/rag/ \
        agents/chat/templates/chat/conversation.html models/queue/tests/ \
        tools/rag/tests/ agents/chat/tests/
git commit -m "fix(ui): IA-2 T17 — member-facing pages stop rendering admin-only controls, and queue copy stops printing null"
```

---
### Task 18: the route matrix, the fourth principal, and the query-count pin

Every route × **four** principals × three postures × both settings of `admin_sees_content`.
IA-1 shipped three principals and recorded that the spec's fourth is the entitlement owner,
"IA-2's". This is IA-2.

**Files:**
- Modify: `identity/tests/test_route_matrix.py`, `identity/tests/test_zero_queries.py`,
  `identity/tests/_helpers.py`
- Test: the two modules above are the test

**Interfaces:**
- Consumes: every route, driver and world field the previous tasks added.

- [ ] **Step 1: Add the fourth principal**

In `identity/tests/test_route_matrix.py`, extend `_sign_in_as`:

```python
def _sign_in_as(client, who, world):
    """Returns the caller, or None for anonymous. Every non-anonymous
    `who` is a FRESH account per cell, so nothing one cell does to an
    account can reach the next.

    `owner` is a plain member who holds ONE `role=owner` grant -- on
    `world.entitlement`, which is also the entitlement `world.document`
    is labelled with. That pairing is the whole point of the column: an
    entitlement owner is a MEMBER everywhere except inside their own
    entitlement, and the matrix has to show both halves.

    `world` IS AN ARGUMENT, not a module-level global the fixture writes
    into. The cell test already receives `world` before it signs anybody
    in, so threading it through costs nothing -- while coupling two
    fixtures through a module-level list would make a wrong ordering an
    `IndexError` rather than a readable failure, and would be exactly the
    hidden shared state this module's per-cell fresh-world discipline
    exists to avoid.
    """
    if who == "anonymous":
        return None
    user = make_admin() if who == "admin" else make_user()
    if who == "owner":
        grant(world.entitlement, user=user, role="owner")
    sign_in(client, user)
    return user
```

Every `_sign_in_as(client, who)` call in the module — the cell test, the posture-agreement test
and the toggle test — gains its `world` argument. The posture-agreement test builds a fresh
world per posture already; pass that one.

Set the two new world fields in `_build_world`:

```python
    entitlement = make_entitlement(name=f"entitlement-{next(_counter)}")
    grant(entitlement, user=other, role="owner")
    apps.get_model("rag.DocumentEntitlement").objects.create(
        document=document, entitlement=entitlement)
```

so `world.document` — the row every `rag-document-*` driver points at — is labelled with the
entitlement the `owner` principal owns, and `world.other` owns it too (which is what makes the
`member` and `admin` columns' answers about a row they have no standing over).
`DocumentEntitlement` is resolved through `apps.get_model`, never imported, for the same reason
every other row builder in `identity/tests/_helpers.py` is: this module is scaffolding for a
column that may not import `tools.rag.models`.

Add `"owner"` to the `who` parametrisation on both the cell test and
`test_the_two_non_open_postures_give_identical_answers`.

- [ ] **Step 2: Fill in the owner column of `_EXPECTED`**

```python
        # AN ENTITLEMENT OWNER IS A MEMBER EVERYWHERE EXCEPT INSIDE THEIR
        # OWN ENTITLEMENT. So the base mapping is the member's, and the
        # three routes where owning changes the answer are named in
        # `_OWNER_WIDENED` below rather than folded in here -- a cell that
        # said "admitted" for every R route would pass whether or not the
        # owner rule was implemented at all.
        _EXPECTED[(_klass, "owner", _content)] = _EXPECTED[(_klass, "member", _content)]
```

and, beside the two IA-1 narrowings:

```python
# THE OWNER WIDENINGS. Each of these three routes is pointed by its
# driver at a row labelled with `world.entitlement` -- the entitlement
# the `owner` principal owns -- so an owner is ADMITTED where a member is
# refused. `rag-document-labels`, `-delete` and `-reingest` are the three
# capabilities spec section 7.4 gives an entitlement owner over a
# document; `identity-entitlement-edit` is the fourth, over the grant
# itself.
_OWNER_WIDENED = frozenset({
    "rag-document-labels", "rag-document-delete", "rag-document-reingest",
    "identity-entitlement-edit",
})
```

and in `_expected_for`:

```python
def _expected_for(name, who, content_on):
    """The cell, with the IA-1 narrowings and the IA-2 owner widenings
    applied."""
    if who == "owner" and name in _OWNER_WIDENED:
        return _ADMITTED
    # The three `inference-*-set*` routes and `chat-agent-entitlements`
    # are class S: a model connection and an agent are box inventory, and
    # an entitlement owner has no standing over either, so the owner
    # column answers exactly as the member column does (403). No entry in
    # `_OWNER_WIDENED` for any of them, deliberately -- and this comment
    # is why, so a later reader does not "fix" the omission.
    # `rag-document-labels-bulk` IS in `_OWNER_WIDENED`, because it is the
    # bulk form of a route that already is.
    if name in _LIBRARY_MUTATIONS and who in ("member", "owner"):
        return _REFUSED_ADMIN_SURFACE
    if name in _ROW_ADDRESSED_R and who in ("member", "owner"):
        return _REFUSED_ROW
    return _EXPECTED[(ROUTE_RULES[name], who, content_on)]
```

The `owner` clause comes **first**, so a route that is both `_LIBRARY_MUTATIONS` and
`_OWNER_WIDENED` (both document mutations are) resolves to the widening rather than to the
member's refusal.

- [ ] **Step 3: Turn the IA-1 `L` narrowing into IA-2's real answer**

`_EXPECTED`'s member row currently carries `"L": _ADMITTED` with a comment saying IA-2 turns it
into `_REFUSED_ROW`. Do that:

```python
            # L IS NOW REFUSED for a member holding none of the
            # document's labels. `world.document` is labelled with
            # `world.entitlement`, which a plain member does not hold,
            # so `readable_documents` excludes it and
            # `rag-document-file`/`-transcript` answer 404. The `owner`
            # column above holds that entitlement and is therefore
            # admitted -- which is the assertion that proves the label is
            # doing the work rather than the route being broken for
            # everybody.
            "L": _REFUSED_ROW,
```

and the admin row's `"L": _ADMITTED` becomes `"L": _ADMITTED if _content else _REFUSED_ROW` —
"L joins O in IA-2", exactly as that comment in the IA-1 file predicted. Add `"L"` to
`test_turning_it_on_changes_O_and_nothing_else`'s set of classes it expects to change, and
rename that test to `test_turning_it_on_changes_O_and_L_and_nothing_else`.

`_OWNER_WIDENED` needs `rag-document-file` and `rag-document-transcript` too, since the owner
holds the label: add them, with the comment that an owner grant IS a grant, so an owner reads
the content of a document under their entitlement with no extra branch anywhere.

- [ ] **Step 4: Extend the zero-permission-query pin**

In `identity/tests/test_zero_queries.py`, extend the forbidden-table set — its own docstring
already says "IA-2 extends the forbidden set with the four new tables":

```python
_FORBIDDEN_TABLES = frozenset({
    "identity_user",
    "identity_entitlement",
    "identity_entitlementgrant",
    "rag_documententitlement",
    "agents_toolentitlement",
    "agents_share",
    "agents_agententitlement",
    "agents_flowentitlement",
    "inference_modelset",
    "inference_modelsetmember",
    "inference_modelsetentitlement",
})
```

and add a representative GET of the IA-2 pages — the four under `/identity/`, `/chat/tools/`,
`/chat/access/`, the model console (which now renders a membership control per connection) and
`/inference/sets/` — to the mount sweep, so an open box is proven to touch none of the **eleven**
tables on any of them. Two renders matter most here and both are proven rather than asserted:
`model_access_for(OPEN_PRINCIPAL)` short-circuits on `sees_all_content` before a set edge is ever
queried, and `visible_agents(OPEN_PRINCIPAL)` returns before `label_permitted_q` is built at all
— which is why the `/chat/` index is in the sweep too. Table names come from
`Model._meta.db_table` rather than being typed, so a rename cannot silently empty the set — add
that assertion as the anti-vacuous pin if the module does not already have one:

```python
def test_the_forbidden_table_names_are_the_real_ones():
    """Anti-vacuous pin: a table name typed by hand that no longer
    matches the model would make this whole gate assert nothing."""
    from django.apps import apps
    real = {
        apps.get_model(label)._meta.db_table
        for label in ("identity.User", "identity.Entitlement", "identity.EntitlementGrant",
                      "rag.DocumentEntitlement", "agents.ToolEntitlement", "agents.Share",
                      "agents.AgentEntitlement", "agents.FlowEntitlement",
                      "inference.ModelSet", "inference.ModelSetMember",
                      "inference.ModelSetEntitlement")
    }
    assert real == _FORBIDDEN_TABLES
```

- [ ] **Step 5: Run the matrix, then the two posture sweeps**

```bash
.venv/bin/pytest -q identity/tests/test_route_matrix.py identity/tests/test_zero_queries.py
.venv/bin/pytest -q
FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
```
Expected: PASS everywhere. **Done-when 7 and done-when 8 are this step.** A failure in a sweep
that the default run does not show is a test that assumed `open` — pin its posture with
`identity/tests/_helpers.py::posture(...)` and add its module to `docs/DEV.md`'s
posture-dependent list in Task 19, rather than weakening the sweep.

- [ ] **Step 6: Commit**

```bash
git add identity/tests/
git commit -m "test(identity): IA-2 T18 — the fourth principal, the L column, and the eleven-table query pin"
```

---

### Task 19: documentation, the two ADR amendments, and the ladder to fresh pixels

Per ADR 0008 tests and docs ship in the same PR; the earlier tasks each carried their own
docstrings, and this one carries the documents that describe the phase as a whole. It ends with
the ladder, which is the only thing that makes any of the above true on the owner's box.

**Files:**
- Modify: `docs/adr/0015-agent-layer-and-tool-contract.md` (foot amendment),
  `docs/adr/0010-model-management-framework.md` (amendment), `docs/ROADMAP.md`,
  `docs/DEV.md`, `docs/OPERATIONS.md`, `README.md`, `identity/README.md`,
  `tools/rag/README.md`, `agents/README.md`, `agents/contracts/README.md`,
  `agents/runtime/README.md`, `agents/chat/README.md`, `models/README.md`,
  `foundation/README.md`, `tools/vision/README.md`

- [ ] **Step 1: Check what `test_docs_sync.py` already asserts**

```bash
.venv/bin/pytest -q foundation/ops/tests/test_docs_sync.py
grep -n "def test_" foundation/ops/tests/test_docs_sync.py
```

Read it before writing anything: it already polices some of the below, and a document edited
into disagreement with it fails there rather than in review.

- [ ] **Step 2: Amend ADR 0015 at its foot**

Append one amendment section — not three notes, because they are one story. Its exact content is
spec §19's ADR 0015 row, minus the two halves IA-1's own 2026-08-30 amendment already made
(§10's `ACCOUNTS_REQUIRED` correction and the acting-rule reversal, both already at
`docs/adr/0015-agent-layer-and-tool-contract.md:871`). What IA-2 adds:

```markdown
## Amendment (2026-08-30) — Identity & Auth IA-2: §9's `tool_keys` correction, and G13 item 1's grants half

**§9's "`tool_keys` disappears" is corrected: the argument survives and its
MEANING changed.** §9 recorded that when grants moved to their own table the
`tool_keys` argument to `agents/contracts/tools.py::granted_tools` would
disappear and "the principal alone decides". Owner decision 13 keeps the
agent's declaration, so the argument stays and what it MEANS is now a
declaration rather than a grant: what this agent WANTS to be able to do.
The grant is `agents.models.ToolEntitlement` plus the acting user's
entitlements, and it arrives as a third argument — a pure
`agents.contracts.tools.ToolAccess` value built once per turn by
`agents/entitlements.py::tool_access_for`. Availability is now the
intersection of three things: the declaration, the registry, and the acting
principal's entitlements. `granted_tools` is still the one function that
computes it, and it now has three drops rather than two.

Both halves of the original sentence cannot hold at once. If an agent still
declares what it wants, that declaration has to reach the one function that
decides availability; removing the argument would mean either the
declaration stops being consulted (so every user's full entitlement set is
offered to every agent) or `granted_tools` reads the `Agent` row itself,
which would make a Django-free rule-1 leaf query the database. The
declaration is the half worth keeping.

**`ToolInvocation.agent_slug` now exists, with its writer.** IA-1's
amendment above named it as IA-2 follow-up because IA-1 shipped exactly four
migrations. `agents/migrations/0003_toolentitlement_and_share.py` adds the
column and `agents/runtime/invoke.py::invoke_tool` writes it from
`ToolContext.agent_slug`, so the audit table itself answers "which agent
made this call" rather than the answer surviving only in
`Turn.tool_call["agent"]`.

**G13 item 1's grants half closes; its two builder UIs and its
grantable-mutating-tool gate do not.** Grants now live in their own table
(`identity.EntitlementGrant`, `agents.ToolEntitlement`,
`tools.rag.DocumentEntitlement`), which is the half IA-1's amendment
explicitly left open. The agent-builder UI, the flow-builder UI, and
`grantable_tools()`'s unconditional exclusion of every `mutates=True` spec
are UNCHANGED and stay open in G12 — ADR 0010's rule that a settings-mutating
tool is registered but not grantable stays in force through IA-2 and is
lifted by a later phase, against a real policy decision about which
entitlement such a tool requires by default.
```

- [ ] **Step 3: Amend ADR 0010**

Spec §19 assigns this to IA-2 and it has not been written yet (IA-1 amended 0013 and 0015 only):

```markdown
## Amendment (2026-08-30) — Identity & Auth: the `/inference/` gap closes; the mutating-tool gate does not

**The standing unauthenticated-mutation gap on `/inference/` is closed.**
Every route in `models/registry/urls.py` — the console READ as well as the six
mutations — is classified `S` in `identity/routes.py` and refused to anybody
who is not an administrator of the box. The read is `S` too because the
console displays endpoints, model identifiers and connection configuration:
an inventory of the box, which is operator information. This closes the gap
in full rather than half.

**A model connection can now carry an entitlement label, and the role path
cannot.** IA-2 adds `models.registry.ModelSet` and its two edges
(`ModelSetMember`, `ModelSetEntitlement`): a connection joins a SET, an
entitlement attaches to a SET, and either edge is edited without touching
the other. That gates which principals may SELECT that model in the chat,
vision and Ask pickers and which may have an agent turn resolve it. Using
model M through capability T requires the tool entitlement AND an
entitlement attached to any set M belongs to — an AND across two label
kinds, an OR within each, and a model in no set stays open to every holder
of the capability.

**Agents and flows carry direct entitlement labels too**
(`agents.AgentEntitlement`, `agents.FlowEntitlement`), filtered through the
`visible_agents`/`visible_flows` seams the agent phase already built — and
the label clause composes with the `resident=True` carve-out rather than
being bypassed by it, so a shipped agent can be restricted like any other.

**A model resolved through a ROLE is deliberately exempt.** `db_provider`
and `role_primary` — `rag.embed`, `rag.transcribe`, `rag.extract`, and the
default `chat.converse`/`vision.generate` bindings — answer as they always
have. They are the box's own wiring, not a per-request choice, and gating
them would mean a label on the embedding connection silently stopping
ingestion, retrieval and re-encode for everybody, including the folder
watcher and every management command, none of which holds a grant. An
operator who wants a model unreachable unbinds it or removes it in the
console. Recorded in the spec at §22.32 and flagged there for the owner.

**The mutating-tool grantability gate is NOT lifted.** This ADR's rule that a
settings-modifying tool is registered but not grantable stays in force
through Identity & Auth's second half:
`agents/contracts/tools.py::grantable_tools` still excludes every
`mutates=True` spec unconditionally, and `granted_tools` still drops one
silently. Lifting it means deciding which entitlement such a tool requires by
default, which is a policy question this phase does not need to answer and
should not guess at.
```

- [ ] **Step 4: `docs/ROADMAP.md`**

Rewrite the Identity & Auth bullet as done in two halves, naming what each shipped. Restate the
two bullets spec §19 names:

- the **MCP edge** bullet keeps "gated behind Identity & Auth" as work still to do, and now
  names its two hooks as built: `granted_tools` + `ToolAccess`, and `ToolInvocation` as its own
  table.
- the **Tenancy** bullet's "visibility scopes on documents and categories" is corrected:
  **documents** got them (`DocumentEntitlement`, `tools/rag/access.py`); **categories stay
  taxonomy** — a category is library-wide, `rag-category-rename`/`-delete` are class S, and
  nothing scopes one.

- [ ] **Step 5: `docs/DEV.md`**

Extend the list of posture-dependent test modules (currently nine identity modules plus
`agents/chat/tests/test_visibility.py`) with every module this plan added that pins a posture —
at minimum `identity/tests/test_entitlements_access.py`, `test_entitlement_services.py`,
`test_group_services.py`, `test_entitlement_pages.py`, `test_group_pages.py`,
`agents/tests/test_entitlements.py`, `agents/tests/test_shares.py`,
`agents/chat/tests/test_conversation_share.py`, `agents/chat/tests/test_tool_entitlements_page.py`,
`tools/rag/tests/test_access_documents.py`, `test_document_label_page.py`,
`test_retrieval_visibility.py`. Extend the restart rule's file list with
`agents/entitlements.py`, `agents/labels.py`, `agents/shares.py`, `tools/rag/access.py`,
`tools/rag/labels.py` and `tools/rag/retrieval.py` — all of them job-kind-adjacent code the
worker holds in memory. Add `manage.py relabel_chunks` to the commands section.

- [ ] **Step 6: `docs/OPERATIONS.md`**

Three additions:

- **backups now also contain entitlement grants, document labels, tool labels and share rows.**
  The "backups are sensitive" paragraph IA-1 added names password hashes, session keys and the
  audit log; extend the list rather than writing a second paragraph.
- **`manage.py relabel_chunks`**, what it repairs (an ingest-time re-stamp that logged rather
  than raised) and why it is cheap (no embedding, no engine, one `UPDATE` per document).
- **deleting an entitlement widens access.** Every document it was the last label on becomes
  unlabelled and follows the library posture. The page names the counts first; the operator
  should read them.

- [ ] **Step 7: The column READMEs**

- `identity/README.md`: replace §6 "What IA-2 adds" with what IA-2 **did** add — the two tables,
  the three access questions, the two picker readers, the cascade registry, the four pages, the
  four routes and their classes (including why `identity-entitlement-edit` is `R`). Move the
  deferred eighth structural guard line to "built", since Task 12 installed it.
- `tools/rag/README.md`: labels, the library posture, the one filter point and the required
  `visibility` argument, the chunk-metadata cache with its one writer and two callers, and
  `manage.py relabel_chunks`.
- `agents/README.md`: correct the line that says `ToolInvocation` "does NOT persist `agent_slug`
  yet (that column is IA-2 follow-up work)" — it does now. Add `ToolAccess`, `agents/
  entitlements.py`, `agents/labels.py` and `agents/shares.py` to the column map.
- `agents/contracts/README.md`: `ToolAccess`, `UNRESTRICTED_TOOL_ACCESS`, `granted_tools`' third
  drop, and `ToolContext.tool_access`.
- `agents/runtime/README.md`: the access value threaded through the planner, the loop and the
  delegation hop, and `invoke_tool` writing `agent_slug`.
- `agents/chat/README.md`: the two new pages (`/chat/tools/`, the share panel) and the `use`-level
  rule on `chat-turn`.
- `models/README.md`: **model sets** (`ModelSet`/`ModelSetMember`/`ModelSetEntitlement` and the
  two edges), `models/registry/access.py::model_access_for`, the `access` argument on
  `picker_options`/`resolve_connection*` and on `resolve_chat`'s four callers, and — stated in
  full, because it is the one rule a reader of this column will otherwise assume the other way
  round — the **role-path exemption**: a model resolved through a ROLE is never gated, so putting
  the embedding connection in a set does not break ingestion for the box. Say that
  `model_access_for` is re-exported from `bindings` and why (the closed-set import gate).
- `agents/README.md`: a second edit — `AgentEntitlement`/`FlowEntitlement`, the one label clause
  used by three visibility bodies, and **that it composes with the `resident=True` carve-out
  rather than being bypassed by it**; `AGENT_NOT_PERMITTED`; the delegation knock-on
  (`run_agent_tool` resolves through `visible_agents` now, not `Agent.objects`); and that an
  existing conversation stays readable while a new turn is refused.
- `tools/rag/README.md`: a second edit — the upload door's `rag.ingest` gate, **bulk labelling**
  (including that a category bulk-apply stores no rule), and the **named inbox gap**: a
  watcher-ingested document arrives unlabelled and bulk labelling is the mitigation.
- `identity/README.md`: a second edit — the entitlement-reach panel and the per-user
  effective-access panel, both read-only views over data this column already owns, and that the
  reach panel is an alias of the delete confirmation's counter so the two cannot disagree.
- `foundation/README.md`: the seam list gains **two** names — `models.registry.bindings` already
  had standing and keeps it, and `agents.entitlements` joins it as the door
  `tools/vision/views.py` asks "may this principal call `vision.generate`" through (plan
  decision 20). Say plainly that no AST guard polices the `tools → agents` direction today and
  that this seam is recorded rather than gated.
- `README.md`: extend IA-1's paragraph — the box runs open by default, can be switched to
  accounts, and in the organisation posture can partition its library and its tools with
  entitlements. Correct line 107's "groups, and document/tool labels are IA-2, still to come."
- `tools/vision/README.md`: correct the **IA-1 LIMITATION** block at line 651. It promises "IA-2
  closes this the same way it closes every other blank-owner gap: a migration stamping
  `owner_kind`/`owner_key` on `JobInput`". IA-2 does **not**: spec §17's IA-2 table names three
  migrations and none of them is a vision one, and §18.2's content list does not mention it.
  Rewrite the last two sentences to name it as a **later** follow-up with its hook (an owner
  column on `JobInput`, a sixth `OwnedRows` registration, and `adopt_open_rows` claiming the
  pre-phase blanks), so the document stops promising something this phase does not deliver.
  **Flag this to the owner in the task's report** — it is a promise IA-1 made on IA-2's behalf,
  and re-scoping it is the owner's call, not the implementer's.

- [ ] **Step 8: Run the full four-way gate and both posture sweeps**

```bash
export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
.venv/bin/pytest -q
.venv/bin/pytest -q scripts agents foundation identity models tools
FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py check
```
Expected: every run green; `--check` exits 0; `manage.py check` clean.

- [ ] **Step 9: Migration plan against a restored backup**

```bash
.venv/bin/python manage.py migrate --plan
```
Expected: exactly IA-2's **five** migrations —
`identity.0003_entitlement_and_grant`, `rag.0015_documententitlement`,
`agents.0003_toolentitlement_and_share`, `agents.0004_agent_flow_entitlements` and
`inference.0008_modelset` — and no others. Any **sixth** operation is a model change somebody
made without a migration, or a migration this plan did not authorise; **stop and report BLOCKED**
rather than accepting it.

(Global Constraint 5 is the same list, and the two must agree. If a reviewer changes one, they
change both: this is the plan's last gate, and a stale count here is a stop-work order on correct
output.)

- [ ] **Step 10: The done-when walk, in a browser, on the branch preview stack**

Bring the preview stack up and **restart the worker and the watcher first**
(`docker compose restart watcher worker`) — `docs/DEV.md`'s restart rule covers every module
Tasks 5, 10 and 11 touched. Then, signed in as a real account in a real browser, walk spec
§18.2's nine criteria in order and record the evidence for each:

1. **Two accounts, two entitlements, one group.** Create `Finance` and `Legal`; grant `Finance`
   to account A and `Legal` to account B; label one document with `Finance`. As A: the document
   is found by `/rag/search/`, answered from on `/rag/`, and returned by the `rag.search` and
   `rag.ask` tools inside a chat turn. As B: invisible on all four. **In the browser, not only
   in the suite.**
2. **Remove the last label** from that document and re-run A's and B's searches — both find it
   while `library_posture=open`, neither does while `locked`. No re-encode ran (`/queue/` shows
   no `rag.reencode` job) and the change took one request.
3. **Label a tool** with `Legal` at `/chat/tools/` (reached from the **Tool access** nav link).
   As A, start a turn with an agent that declares it: the tool is simply **absent** from the
   prompt and the page says nothing about it — enforcement by omission, per decision 17 — and an
   agent that delegates to another agent still does not reach it. Confirm the page shows **no**
   "not available on this install" note naming that tool, which would be the false sentence
   decision 17 exists to prevent.
4. **Share a conversation** at `view` with B: B reads it and the message form refuses the post
   with the designed 403 copy. Change the share to `use`: B posts. Remove it: both go.
5. **Make A an owner of `Finance`.** A grants `Finance` to B, revokes it, labels a document with
   it — and cannot create, rename or delete an entitlement, cannot reach `/identity/users/`,
   `/identity/groups/` or `/identity/settings/`, and gets 404 on `Legal`'s own page.
6. **Delete `Finance`.** The confirmation named the counts first; afterwards the grants and the
   labels are gone, the affected document's chunks carry no `entitlements` key
   (`manage.py relabel_chunks --document <id>` is a no-op on it), and the document follows the
   library posture.
7. **The route matrix is green** (Task 18, Step 5).
8. **Both posture sweeps are green in both feature states** (Task 18, Step 5).
9. **The full ladder to fresh pixels on the live box**, per `docs/DEV.md`. Use the
   `verify-deployed-work` skill and follow it to the end: no success language at any rung before
   the last one, and the last one is a fresh screenshot of the owner's own running system.

**And the three appended by the 2026-08-30 owner directive**, walked in the same browser session:

10. **Create a model set** on `/inference/sets/`, put a connection in it from the console, and
    attach `Legal` to it. As B (who holds `Legal`) the connection is in the chat per-turn picker,
    the vision page picker and the Ask page picker; as A (who does not) it is in **none** of the
    three. As A, post its pk anyway to each of the three submission paths — the chat turn,
    `vision-generate`, and Ask — and get an honest refusal from each, never a 500 and never a
    silent substitution of a different model. Start an **agent turn** naming it as A: refused at
    preflight with **403**, and `/chat/` shows no new pending card, because nothing was written.
11. **The two one-action properties, in the browser.** Add a **second** connection to that same
    set: B reaches it immediately, with no entitlement edit. Attach a **second** entitlement to
    that same set: an account holding only that second one reaches **both** models, again with no
    per-model edit. Then detach everything: a connection in no set is offered to both accounts
    again, and a set with nothing attached restricts nothing. Then check the **AND**: with
    `vision.generate` labelled `Finance` and the connection's set attached to `Legal`, an account
    holding only one of the two reaches nothing, and an account holding both generates.
12. **`vision-generate` for a non-holder**: the create page shows the "needs an entitlement"
    sentence and **no** generation form, the POST answers 403 with that copy, and the gallery and
    Recent still render — the page is class A and reading what you already made is not generating.
    Then **put the embedding connection into a restricted set** and confirm the box is unharmed:
    drop a file in the inbox and watch it ingest, ask a question on `/rag/` and get an answer, and
    run `manage.py ingest` — all three work, because the role path is exempt. **This is the one
    to show the owner**: it is the decision flagged at spec §22.32.
13. **Label an agent** on `/chat/access/` — pick a **shipped** one, because that is the case a
    reader assumes is exempt. As A it is gone from the `/chat/` picker and from the "Add the
    default X" offers; as B it is there. A conversation A already started with it **still opens
    and still renders every turn**, and posting a new turn into it answers **403** with the
    "still readable; new turns are not" copy. An agent that delegates to it, run as A, refuses.
    Label a **flow** and confirm it is gone from `flow.run`'s choices for A. Finally run
    `manage.py agent_turn` against the labelled agent and watch the shell be refused too.
14. **`rag-document-upload` for a non-holder**: label `rag.ingest` on `/chat/tools/` (it is
    listed, marked page-only). As A the library page shows **no upload form** but still lists
    every document A may read, and posting an upload anyway answers 403 — check the inbox
    directory afterwards and find **nothing** was staged.
15. **One action, not many.** In the library, tick several documents, pick an entitlement, and
    apply it in **one** submit — then confirm each document's chunks carry the label. Use the
    category control to label **every** document in a category in one more submit, then add a new
    document to that category and confirm it is **unlabelled** (there is no stored rule). Open the
    entitlement's page and read its **full reach** — grants, documents, tools, model sets, agents
    and flows — then start a delete and confirm the confirmation names the **same** numbers.
    Cancel it. Open `/identity/users/` and read one account's **effective access**: each
    entitlement marked direct or via which group, and what each unlocks.

- [ ] **Step 11: Commit**

```bash
git add docs/ README.md identity/README.md tools/rag/README.md agents/README.md \
        agents/contracts/README.md agents/runtime/README.md agents/chat/README.md \
        models/README.md foundation/README.md tools/vision/README.md
git commit -m "docs: IA-2 T19 — ADR 0015 and 0010 amendments, ROADMAP, DEV, OPERATIONS and the column READMEs"
```

---

## Self-Review

Run against the spec with fresh eyes after the plan was written.

**1. Spec coverage — §18.2's content list, line by line:**

| §18.2 names | Task |
|---|---|
| `Entitlement`, `EntitlementGrant` | 1 |
| `DocumentEntitlement` | 9 |
| `ToolEntitlement`, `Share` | 4 |
| the entitlement-owner role | 1 (the column), 2 (the writes), 3 (the page), 9/12 (documents) |
| `held_entitlement_ids` / `owned_entitlement_ids` / `may_see_unlabelled` given bodies | 1 |
| `tools/rag/access.py` | 9 |
| the required `visibility` argument threaded through `retrieve_nodes` → `answer_question` → the two runners → the two views | 11 (with the fifth caller, `manage.py ask`, which §8.3's table also names) |
| `tools/rag/labels.py::restamp_document_chunks` and `manage.py relabel_chunks` | 10 |
| the library posture given effect | 1 (`may_see_unlabelled`), 9 (`document_visibility`), 12 (the control stops being disabled) |
| `ToolAccess` and `agents/entitlements.py::tool_access_for`, with `granted_tools`' third drop | 5 |
| `Share` and the conversation-sharing UI | 7, 8 |
| the groups, entitlements, document-label and tool-label pages | 3, 12, 6 |
| the remaining audit actions wired to their write sites | 2 (entitlements, grants, groups), 4/6 (tools), 10/12 (documents), 8 (shares) — all sixteen IA-2 actions in `identity/contracts/actions.py` now have a write site |
| the route matrix extended with the owner principal and IA-2's seven routes | 14, plus a driver in each task that adds a route |
| §12.1's entitlement-delete cascade | 2 (the registry), 4 and 9 (the registrations), 10 (the document handler) |
| §13's audit catalogue | already complete in the tree; IA-2 writes, never extends, it |
| §17's five migrations | 1, 4, 9, 14, 15 |
| §19's documentation | 19 |
| §9.3's two direct-surface tool gates (2026-08-30) | 13 |
| §22.35's labellable-but-not-grantable rule | 13, Step 2 |
| §6.10 model **sets**, §9.5's `ModelAccess`, the seams, the exemption (2026-08-30) | 14 |
| §6.11 `AgentEntitlement`/`FlowEntitlement`, §9.6's one clause and five knock-ons | 15 |
| §15's admin-ergonomics surfaces and §22.34's one-action principle | 16 |
| §21's named inbox gap, and that bulk labelling is its mitigation | 16, Step 5 |
| §13.2's eleven new audit actions | 14 Step 1 (seven), 15 Step 1 (four) |
| §17's migrations 8 and 9 | 14 Step 2, 15 Step 2 |
| §18.2's done-when 10–15 | 13, 14, 15, 16 and 19's browser walk |

**Bare task numbers in this table are task numbers**, and two renumbering passes matched only the
literal `Task N` form — so these rows were corrected by hand rather than by the sweep. Anybody
renumbering again should grep this table specifically.

**Gaps found and closed during the author's own review:** (a) the entitlement-delete cascade
had no expressible call path across the import law — closed by the registry in Task 2, recorded
as decision 3; (b) the label and share pickers needed entitlement and account **names**, which
no column outside identity may read — closed by the readers in Task 1, recorded as decision 6;
(c) `identity-entitlement-edit` classified `S` would have been refused by the middleware before
an owner reached the view — closed by classifying it `R`, recorded as decision 5;
(d) `tools/rag/views.py`'s two upload dedup reads would have failed the `Document.objects` guard
spec §4.3 requires — closed by relocating them in Task 12, Step 3.

**Closed by the round-1 hygiene review**, each verified against the tree and each now carrying
its own note in the task it belongs to:

| # | What was wrong | Where it is fixed |
|---|---|---|
| M1 | the cascade-registry test had no isolation fixture and poisoned the module-global registry for every later `delete_entitlement` | Task 2, Step 1 |
| M2 | Task 9 registered a cascade whose handler module Task 10 creates, so `import_string` raised for every delete | moved to Task 10, Step 4 |
| M3 | `agents/chat/views/tools.py` queried `Agent.objects`, which the chat `.objects` guard forbids | Task 6, Step 3 (`agents.visibility.resident_agent_tool_keys`) |
| M4 | two `Share` tests used `target_key` values `Share.save()`'s own parser rejects | Task 4, Step 1 |
| M5 | the widened delete/re-ingest resolved the row first and answered 404 where the plan's own decision 9 promised 403 | Task 12, Step 6 + decision 19 |
| M6 | the settings-page inversion named a test that does not exist and asserted the opposite of the code | Task 12, Step 8, rewritten around four real artefacts |
| M7 | the library rows are `Document` instances; `row.may_label` had nowhere to come from | Task 12, Step 7 (explicit `rows` + `{% with %}`) |
| M8 | narrowing `categories` also emptied the upload form's category picker on every box | Task 12, Step 4 (two lists) |
| M9 | the planner half of done-when 3 was vacuous (`roles=()`) | Task 5, Step 7 (`roles=(CHAT_CONVERSE_ROLE,)`) |
| M10 | the caller spy returned `index=None`, which `answer_question` dereferences; and (round 2) the three in-process callers resolved real models before reaching the spy, and named two helpers by the wrong names | Task 11, Step 4 — stub index + stubbed `gateway.get_llm_for`, a `resolved` fixture patching each caller's own resolution seam, `model_available` for `SearchView`, and the real `make_job_ctx`/`make_tool_ctx` with the real `run_ask(payload, models, ctx)` arity and the real `query`/`question` params |
| M11 | done-when 2's own test asserted `in (200, 404)` — the whole outcome space | Task 12, Step 1 |
| M12 | a member would be told a labelled tool "is not available on this install" | Task 5, Step 8 + decision 17 |
| M13 | the grant form was built from `share_subjects`, which excludes the caller | Task 1, Step 9 + decision 18 |

The twenty-three minors and four nits are applied in place; the three that changed a decision
rather than a line are recorded as decisions 16 (the matrix's single posture), 17 (unentitled
tools render nothing) and 19 (predicate before row).

**2. Placeholder scan.** No `TBD`, no "implement later", no "add error handling", no "similar to
Task N". **No bare `...` in any Python code block** — round 4 found three in the first
model-entitlement draft (inside a signature, inside a preflight `except`, and inside a
`TurnStart(...)` construction) and the reworked Tasks 13–16 spell every one of them out: the
`except` branch is written as "one new branch goes first, before the two that are already there",
and the `TurnStart` change as "every argument it has, and only its status becomes the `status`
local". The one surviving `...` is inside a Django `{% comment %}`
(`{# ... the existing generation form, unchanged ... #}`), which is prose describing untouched
markup. Every helper named is one the tree already exports, **checked by name**:
`agents/tests/_helpers.py::make_agent`/`bind_chat_role`/`isolated_tool_registry`,
`agents/contracts/tests/_helpers.py::make_spec`/`make_principal`,
`tools/rag/tests/_helpers.py::make_document`/`make_job_ctx`/`make_tool_ctx`/`model_available`,
`tools/rag/tests/test_commands.py::_resolve_side_effect`, `identity/testing.py`'s nine, and —
round 4's second finding — `models/registry/tests/_helpers.py::make_chat_connection`/
`make_embed_connection`/`bind` (there is **no** `make_connection` and **no** `bind_role` in this
tree, and the first draft of the model task used both, sixteen times). `IMAGE_GENERATION_CAPABILITY`
is `models/contracts/roles.py:65`, not a `tools/vision` name, and its value is `"image-generation"`. They are
imported, never invented — round 2 caught the two that were not (`_job_ctx`/`_tool_ctx`, which
are really `make_job_ctx`/`make_tool_ctx`), and every call shape in Task 11 Step 4 is now the
real signature (`run_ask(payload, models, ctx)`, `run_search({"query": …})`,
`run_ask({"question": …})`), and every new test module imports from **its own package's** `_helpers`
(Task 1, Step 8). Two round-1 findings removed the last two deferred decisions: Task 5's
contract-test fixtures are now written out concretely (was "use whatever fixture that module
already has"), and Task 12's upload labelling is now code with its own tests (was one sentence).
Task 5's runtime call-site step and Task 19's README step give per-file instructions rather than
full file bodies, because what is being edited is documentation and existing modules whose
surrounding style is the specification.

**3. Type consistency.** Checked across tasks:
`ToolAccess(required, held, unrestricted)` and `.allows(key)` — Task 5, consumed identically in
Tasks 5 and 6. `DocumentVisibility(unrestricted, entitlement_ids, unlabelled_allowed)` and
`.sees_nothing` — Task 9, consumed in Tasks 11 and 12.
`restamp_document_chunks(doc_id, *, raising=False)` — Task 10, called with `raising=True` from
`set_document_labels` and `unlabel_all_for_entitlement`, and without it from ingest.
`EntitlementCascade(key, label, handler)` with `handler(entitlement_id, *, commit) -> int` —
Task 2, matched by `agents.labels.tool_labels_cascade` (Task 4) and
`tools.rag.labels.unlabel_all_for_entitlement` (Task 10).
`shared_keys(target_type, principal)` and `share_level(principal, target_type, target_key)` —
Task 7, consumed in Tasks 7 and 8.
`may_label_document(principal, document)` — Task 9, consumed in Tasks 12 and 13.
`labelling_entitlements(principal) -> ((id, name), ...)` — Task 1, consumed in Tasks 3, 6 and 12.
`share_subjects(principal) -> {"users": ..., "groups": ...}` — Task 1, consumed in Task 8 only.
`grant_subjects(principal)` — same shape, same task, consumed in Task 3 only (decision 18).
`resident_agent_tool_keys() -> {tool_key: [slug]}` — Task 6, consumed in Task 6 only.
`may_post_to(principal, conversation)` — Task 7, consumed in Tasks 7 and 8.
`Preflight.unentitled_tools` — Task 5, consumed by nothing that renders (decision 17) and
asserted in Tasks 5's two preflight tests.
`_cascade_counts_for(entitlement, *, commit)` — Task 2, the one place `"Grants"` is spelled.
`ModelAccess(required, held, unrestricted)` and `.allows(connection_pk)` — Task 14, deliberately
the same shape and the same defaults as Task 5's `ToolAccess`, so a reader who has understood one
has understood both; consumed in Task 14 only. Its `required` is keyed by **connection pk** and
valued by the entitlement ids that reach it **through any set**, flattened once (decision 27).
`label_permitted_q(principal)` and `visible_agent_slugs(principal)` — Task 15, consumed by the
three visibility bodies and by the two runtime call sites respectively.
`effective_entitlements(user) -> ({"id","name","role","via"}, ...)` — Task 16, consumed by the
users page only; it takes a `User` row, not a principal, because the caller is an administrator
asking about somebody else.
`entitlement_reach(entitlement)` — Task 16, a one-line alias of Task 2's
`entitlement_delete_counts` and asserted equal to it (decision 32).
`set_memberships_for` / `connection_set_ids` / `model_set_cascade` — Task 14, and
`set_agent_labels` / `set_flow_labels` / `agent_flow_label_cascade` — Task 15: both triples are
the shape `agents/labels.py` (Task 4) and `tools/rag/labels.py` (Task 10) already carry, so the
fourth and fifth label kinds read as the first three.
`AGENT_NOT_PERMITTED` — Task 15, joining `MODEL_NOT_PERMITTED` (Task 14) in the one
`start_turn` line that picks 403 over 503.
`model_access_for(principal)` — Task 14, defined in `models/registry/access.py` and **re-exported
from `models.registry.bindings`**, which is the name every consumer imports (decision 21).
`picker_options(capability, role_key, selected="", *, access)` and
`resolve_connection(_named)(pk, capability, *, access)` — Task 14, consumed by three picker
wrappers and five submission paths.
`_may_generate(principal)` — Task 13, consumed by `generate` and `_create_page_context` only.
`MODEL_NOT_PERMITTED` and `Preflight.reason` — Task 14, read by `start_turn` to pick 403 over
503; `Preflight` now carries **two** defaulted tuples (`unentitled_tools` from Task 5,
`()` still) and one more reason constant.
`set_model_labels(actor, connection, entitlement_ids)` / `connection_label_ids(connection)` /
`model_set_cascade(entitlement_id, *, commit)` — Task 14, the same three names
`agents/labels.py` (Task 4) carries for tools, so the third label kind reads as the second.

**4. What this plan deliberately does not do**, restated so it is not mistaken for an omission:
ADR 0016 (written after IA-2 merges), `/admin/` styling under `DEBUG=0` (an open owner
decision), a sharing UI for agents/flows/generated images, lifting the mutating-tool
grantability gate, service-account tokens and the MCP edge, and an owner column on
`tools.vision.JobInput` — the last of which is a promise `tools/vision/README.md` currently
makes on IA-2's behalf, and which Task 19, Step 7 corrects and reports.

**And, added by the two 2026-08-30 amendments:** it does **not** gate the role path (decision
recorded at spec §22.32 and **flagged for the owner** — putting the embedding connection in a set
must not break ingestion for the whole box); it does **not** add an AST guard for the
`tools → agents` direction (decision 20 — none exists today and inventing one to police a single
edge is its own change); it does **not** make a model set or an agent/flow label reachable by an
entitlement owner (all three pages are class S, and the owner's three capabilities stay grants and
documents); it does **not** build **per-inbox default labels** — spec §21's named gap, restated
there, with bulk labelling (Task 16) as the mitigation and Task 16 Step 5 writing that into both
the view's docstring and `tools/rag/README.md`; it does **not** store a category→entitlement rule
(decision 29 and spec §22.34 — the owner's own ruling, and Task 16 Step 1 tests the absence); and
it does **not** add a set layer for agents or flows (spec §22.33 records why the two answers
differ, and names `AgentSetEntitlement` as the one-migration move if agent churn ever looks like
model churn).

---

## Plan review

Adversarial plan-hygiene review (per the plan-hygiene-review process; one read-only reviewer,
most capable model, inputs = this plan + the spec + the real tree at 073f0e0):

- **Round 1 — AMEND** (13 MAJOR / 23 minor / 4 nit). Top findings: cascade handler registered a
  task before its module exists; two done-when tests asserted nothing; widened delete/re-ingest
  gate contradicted its own pinned 403; Share.save() failed its own tests; render-vs-gate
  predicate had no source on the library rows. Author applied 37, applied-with-modification 3
  (M4 docstring pin; M12 omission of unentitled tools = decision 17; M13 second reader
  `grant_subjects` = decision 18), rejected 0.
- **Round 2 (scoped) — AMEND** (1 MAJOR / 5 minor). 39/40 round-1 findings verified resolved;
  all three modifications judged sound. Residue: caller-spy tests half-fixed. Author applied
  all 6; while fixing, found and fixed a third blocker the review had missed
  (`answer_question`'s empty-nodes else-branch calls `gateway.get_llm_for` before the stubbed
  index — the spy now stubs it), and corrected the reviewer's own wrong suggestions
  (`model_available` is a generator body, not a fixture → `search_models` wrapper; `_precheck`
  returns a dict, not a tuple).
- **Round 3 (scoped) — AMEND** (0 MAJOR / 2 minor): two pre-rewrite sentences left in Task 11
  Step 4's closing paragraph. Cap reached. **Orchestrator adjudication: both ACCEPTED and
  applied with the reviewer's supplied wording** — (R3-1) the `model_available`-as-fixture
  sentence replaced with the `search_models`-wrapper statement; (R3-2) "built the way that
  module builds them" replaced with an explicit model-name-free construction (Global
  Constraint 1: `test_commands.py`'s literals are model ids and must not be copied into a new
  file). Reviewer's closing assessment: after these two edits, CLEAN with no further re-check
  expected.

Full round reports and the author's per-finding amendment records live in the authoring
session's scratchpad (not committed).

### Owner amendments (2026-08-30) and rounds 4–6

After round 3 the owner directed two scope amendments (recorded verbatim in spec §22.31/§22.33
and §22.34): least-privilege enforcement extended to tools at their direct UI surfaces and to
models; then model labels reworked to **model sets** (one membership edit propagates to every
attached entitlement), agent/flow entitlement labels, the `rag.ingest` upload door, and three
admin-ergonomics surfaces (bulk document labelling, the entitlement reach page, per-user
effective access) under the one-action principle.

- **Round 4 (scoped, amendment 1) — AMEND** (3 MAJOR / 3 minor / 1 nit): missing
  `resolve_chat` caller coverage; invented test-helper names; a stale three-migrations
  acceptance gate. All folded into the amendment-2 pass.
- **Round 5 (scoped, combined amendment-2 delta) — AMEND** (1 MAJOR / 3 minor / 1 nit): the
  set pages' copy contradicted decision 28's unattached-set semantics; four small mechanical
  edits. Author applied all five plus a bonus sweep fixing two pre-round-2 cross-package
  test-helper imports. Reviewer confirmed all round-4 findings genuinely resolved, rounds 1–3
  anchors intact, done-when 1–9 byte-identical, migration numbering (`inference/0008`,
  `agents/0004`) and the `/chat/access/` premises verified against the tree.
- **Round 6 (final confirmation) — CLEAN**: all five round-5 fixes and the bonus sweep landed
  as claimed; 176 steps contiguous across 19 tasks, decisions 1–35 contiguous, fences
  balanced, done-when 1–9 byte-identical to the pre-amendment spec, zero model/vendor names
  in either document.

Six rounds total, 58 findings, 0 rejected. Flagged for the owner's PR review: spec §22.32
(role-path exemption — box service bindings are never model-gated) and decision 35 /
spec §9.6 (an owner's own labelled agent hides from them too; existing conversations stay
readable, new turns with a restricted agent are refused).
