"""Federated web compliance scanner.

Pipeline: targets.yaml -> COLLECT (browser) -> evidence/*.json
                       -> EVALUATE (pure)  -> findings/*.json
                       -> REPORT           -> reports/*.html|md

The persisted evidence bundle in the middle is the whole design. It means a run
can be re-evaluated against changed rules without touching anyone's website,
and every verdict can be traced back to what was actually on the page.
"""

from .models import SCHEMA_VERSION, Severity, Verdict

__version__ = "0.1.0"
__all__ = ["SCHEMA_VERSION", "Severity", "Verdict", "__version__"]
