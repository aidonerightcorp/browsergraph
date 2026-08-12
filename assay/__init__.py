"""assay — check the measurement, not just the thing being measured.

An assay is a test for the presence, amount or purity of something. This
package is the same idea applied to evaluations: before you believe a number,
establish that the thing producing it can tell good from bad at all.

Four modules, usable together or apart, standard library only:

    taxonomy      the forty-one shapes engineering work comes in, each with
                  the way it fails *while reporting success*
    obligations   what an evaluation owes — nine obligations, and a verdict
                  that stays PROVISIONAL until they are discharged or waived
    controls      null, negative, positive and shuffle controls as values;
                  a harness with none of them cannot do better than PROVISIONAL
    judge         audit an LLM-as-judge: agreement with people above chance,
                  rubric sensitivity, length and position bias

The doctrine, in one line: **"could not check" is not "checked and passed", and
neither of them is "checked and failed".** Three states, everywhere, because
collapsing them is how an unchecked number ends up in a decision.

    from assay import judge
    print(judge.check(model=verdicts, human=labels).text())

`browsergraph` is the reference implementation of the pipeline half of this and
imports these modules; nothing here imports it.
"""
from __future__ import annotations

from assay import controls, judge, obligations, taxonomy
from assay._version import __version__

__all__ = ["__version__", "controls", "judge", "obligations", "taxonomy"]
