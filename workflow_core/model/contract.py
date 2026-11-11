"""The verdict of the contract test and the mode it puts the core in (ADR 0019).

The core only sees this verdict: what was checked, and how, stays in the runtime's adapter (ADR 0001).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# "observer": the core writes nothing (contract broken, or not yet checked). "normal": the contract holds.
Mode = Literal["observer", "normal"]


class ContractVerdict(BaseModel):
    ok: bool
    # ISO 8601, UTC.
    checked_at: str
    # "<board>: <what is missing>", in the order found.
    problems: list[str]

    @property
    def mode(self) -> Mode:
        return "normal" if self.ok else "observer"


class CoreState(BaseModel):
    """What the core holds about itself; the machine state of the API serves it (story 5)."""

    contract: ContractVerdict | None = None

    @property
    def mode(self) -> Mode:
        return self.contract.mode if self.contract is not None else "observer"
