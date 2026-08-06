"""Agentic layer for MzansiMesh.

The agent narrates; it never decides.  Readiness scores, recommendations and
case status remain fully deterministic in ``claims_case.run_case_agent``.  This
package only turns an already-completed decision trace into readable prose, so
a language model failure can degrade the wording but never the outcome.
"""

from .narrator import narrate_case_run

__all__ = ["narrate_case_run"]
