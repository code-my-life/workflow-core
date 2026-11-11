"""What the core holds about itself: GET /api/machine serves it."""

from __future__ import annotations

from pydantic import BaseModel

from workflow_core.model.contract import ContractVerdict, Mode


class CoreState(BaseModel):
    """What the core holds about itself; GET /api/machine serves it."""

    contract: ContractVerdict | None = None
    # The installed runtime's version, None when its output could not be read.
    runtime_version: str | None = None
    # Per declared board, the time of its last successful read (ISO 8601 UTC), None before the first.
    last_read_at: dict[str, str | None] = {}

    @property
    def mode(self) -> Mode:
        return self.contract.mode if self.contract is not None else "observer"
