"""The `models/` column: model handling AND execution.

`models/contracts/` is pure, Django-free platform contract code (the
role/operation/job-kind/engine registries, the queue seam, the gateway).
`models/registry/` and `models/queue/` are the Django apps that store and
drive them.

CONVENTION, and there is no exception to it anywhere in this codebase:
never write a bare `import models`. Always `from models.<sub> import ...`.
Every Django `models.py` in the repo opens `from django.db import models`;
the two forms never conflict because `from X.Y import Z` resolves through
`sys.modules` and never through a module-level name -- but a bare
`import models` followed by `models.contracts....` WOULD be shadowed by
that local name. See the spec's section 3.7.
"""
