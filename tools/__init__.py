"""The `tools/` column: one folder per tool-owning feature.

A future tool is one new folder here, not a new top-level directory --
and the fact that every folder here is the SAME KIND OF THING (a feature
that registers capability-scoped tools an agent may call) is the thing
this column exists to keep visible in the tree.

Each folder is a Django app and is COLUMN-PRIVATE (import law rule 2):
`tools.rag` does not import `tools.vision`, and neither is importable
from `models/`, `agents/`, or `foundation/`. The sanctioned cross-column
imports a `tools/*` app may make are exactly these nine, each a named
seam with a live call site -- nothing else in `models.*`, `agents.*` or
`identity.*` is legal from here:

- `models.registry.bindings` (rule 2's named exception; NOT `.models`/`.views`)
- `models.queue.visibility`  -- IA-1 rows-vs-content answers (`tools/rag/views.py:55`)
- `identity.contracts`       -- `tools/rag/jobs.py:76`
- `identity.access`          -- `tools/rag/views.py:48`
- `identity.request`         -- `tools/rag/views.py:50`
- `identity.audit`           -- `tools/rag/jobs.py:75`
- `agents.contracts`         -- `tools/rag/access.py:18`
- `agents.entitlements`      -- IA-2 tool-access door (`tools/rag/views.py:784`, lazy)
- `agents.workstreams`       -- the workstream seam (`tools/rag/access.py:629`, lazy)
"""
