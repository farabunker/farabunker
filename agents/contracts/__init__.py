"""The TOOL CONTRACT -- pure, Django-free, universally importable (rule 1).

Three modules, no more:

- `tools.py`      -- ToolSpec, ToolResult, ToolContext, StepBudget,
                     ToolRefused, `validate_tool_args`, the
                     module-level registry, `granted_tools`, and
                     `describe_tool`. `Principal` is IMPORTED here
                     (from `identity.contracts.principals`, IA-1),
                     never defined -- that is `ToolContext`'s type,
                     not this package's.
- `toolschema.py` -- ToolSpec -> the tool-calling JSON an LLM reads,
                     ToolSpec -> an MCP `tools/list` entry
                     (`mcp_tool_dict`, sharing one schema builder with
                     the first), and the wire-name round trip back.
- `artifacts.py`  -- the artifact-reference vocabulary.

NOTHING HERE IMPORTS DJANGO -- not transitively either. At runtime it
imports the standard library and `models.contracts.operations`, and
nothing else. `models.contracts.jobkinds` is reached ONLY under
`if TYPE_CHECKING:` for one annotation, because that module does
`from django.utils.module_loading import import_string` at module scope
(jobkinds.py:34) and a real import would drag Django in.
`agents/contracts/tests/test_purity.py` pins this in a subprocess with no
DJANGO_SETTINGS_MODULE set at all -- because "pure" that is only asserted
in a docstring is a hope, not a property.
"""
