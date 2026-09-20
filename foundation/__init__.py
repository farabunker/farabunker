"""The `foundation/` column: shared, feature-agnostic platform code.

`format.py` and `files.py` are rule-1 PURE LEAVES -- no Django models, no
views, no database, no import of any non-pure module -- so any column may
import them in any direction. `ops/` and `setup/` are Django apps and are
column-private (rule 2).

Named `foundation/` rather than `platform/` because `platform` is a
stdlib module name and `manage.py` puts the repo root at `sys.path[0]`;
a `platform/` package here would shadow it and break `collectstatic`.
ADR 0010 section 2 already ruled on this once. See the spec's section 2.1.
"""
