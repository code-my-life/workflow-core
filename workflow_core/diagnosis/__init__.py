"""The diagnosis of blocked cards: pure rules on the facts an adapter reads, without any AI model (ADR 0012).

This package only sees the core's model (`workflow_core.model`): it never names the runtime (ADR 0001).
"""

from workflow_core.diagnosis.diagnose import diagnose

__all__ = ["diagnose"]
