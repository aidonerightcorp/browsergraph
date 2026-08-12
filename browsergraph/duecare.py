"""Moved to `assay.obligations`. Re-exported here so nothing breaks.

Renamed on the way out. "Due Care" is also an unrelated product — an offline
legal companion for people who cannot afford to be wrong about their rights —
and one name for two things is how a search for either finds neither.

    from assay import obligations        # the new home
    from browsergraph import duecare     # still works, and will keep working

What the module does is unchanged: nine obligations, three verdict states, and
a refusal to compare two numbers held to different bars.
"""
from __future__ import annotations

from assay.obligations import *  # noqa: F401,F403
from assay.obligations import (  # noqa: F401
    SEVERITIES,
    STANDARD,
    STATES,
    Discharge,
    Ledger,
    Loop,
    Obligation,
    Round,
    Verdict,
    check_coverage,
    check_grader_agreement,
    check_negative_control,
    check_positive_control,
    check_provenance,
    check_replication,
    check_sample_size,
    check_slices,
    cohen_kappa,
    compare,
    from_scores,
    interval,
)
