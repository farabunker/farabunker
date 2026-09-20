"""This column's entitlement AXIS -- document labels -- COUNTED from
the entitlement's own side, not yet edited from it.

`tools/rag/labels.py` answers the resource question ("which entitlements
label this document"), which is what the library's own per-document
control asks. This module answers the same table's OTHER question ("how
many documents carry this entitlement"), so the entitlements list can
show a Documents column beside its Tools/Agents/Flows/Model sets ones
without `identity/` importing anything here.

COUNTED ONLY, DELIBERATELY. `identity.contracts.axes.EntitlementAxis`
lets an axis register the editing trio as well, and this one does not:
a document label is the ONE labelled kind an entitlement OWNER may write
(spec section 7.4), the library page already owns that write with its own
posture warnings, and a transfer panel listing every document in the box
is the wrong control for a library that scales to thousands. The
entitlement page's reach panel and this column keep the count honest
meanwhile; making it editable from this direction is a later round's
decision, not a gap this one left open.
"""
from __future__ import annotations

from urllib.parse import urlencode

from django.db.models import Count
from django.urls import reverse

# THE DOOR'S OWN SENTENCE, declared here rather than in the entitlement
# page's template: this column owns the words about its own surface, the
# same way `agents/axes.py::TOOLS_HINT` does for the tools panel. The
# entitlement page renders the heading, this sentence and the link, and
# knows nothing else about what is behind it.
DOCUMENTS_HINT = ("Document labels are written in the library, on each document's "
                  "own row.")


def document_counts() -> dict[int, int]:
    """`{entitlement_id: labelled documents}` for EVERY entitlement, in
    ONE aggregate -- see `agents/axes.py::_counts` for why the
    whole-table shape rather than a per-entitlement count."""
    from tools.rag.models import DocumentEntitlement

    return {
        row["entitlement_id"]: row["n"]
        for row in DocumentEntitlement.objects.values("entitlement_id")
                                              .annotate(n=Count("id"))
    }


def document_link(entitlement_id: int) -> str:
    """The library, narrowed to the documents this entitlement labels.

    THE ROUTE NAME LIVES HERE, in the column that owns the route
    (fix round 2, P2-I1). `identity/` registered this as a dotted path
    and resolves it at render time, exactly as it resolves the counts
    above -- so the entitlement page offers a door into this library
    without `identity/` naming `rag-documents`, which import-law rule 4
    forbids it to import and which its own registry exists to make
    unnecessary.

    `urlencode` rather than an f-string, so the one place this query is
    spelled is the one place it is escaped.
    """
    return f"{reverse('rag-documents')}?{urlencode({'entitlement': int(entitlement_id)})}"
