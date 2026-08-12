"""Moved to `assay.taxonomy`. Re-exported here so nothing breaks.

The map of pipeline shapes is not a fact about browsergraph — it is a claim
about engineering work, and it imports nothing from this package. It lives in
`assay` so that a library which is not this one can report coverage against the
same forty-one shapes.

    from assay import taxonomy       # the new home
    from browsergraph import taxonomy   # still works, and will keep working

This shim stays. Twenty-three published notebooks import from here, and a
rename that breaks a page somebody bookmarked is a rename that costs more than
it saves.
"""
from __future__ import annotations

from assay.taxonomy import *  # noqa: F401,F403
from assay.taxonomy import (  # noqa: F401
    BY_FAMILY,
    BY_ID,
    CATEGORIES,
    FAMILIES,
    OUT_OF_SCOPE,
    Category,
    Coverage,
    Family,
    catalog_text,
    coverage,
    families,
    for_pack,
    for_template,
    get,
    in_family,
    search,
)
