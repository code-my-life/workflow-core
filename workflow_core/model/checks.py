"""The state of the machine: one check per service, as the former probe of the factory reported them (ADR 0008).

The runtime's own checks (its pause, its gateway, its scheduled jobs, its local model router, its disk) are read by
its adapter. The other checks of the machine are declared in the configuration (`[[check]]`), never written in the
code: an address that answers, a port that listens, the video memory, the public funnel.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

CheckStatus = Literal["ok", "degraded", "down", "unknown"]
CheckKind = Literal["http", "tcp", "vram", "funnel"]

# Checks run every 30 seconds, unless declared slower; each is bounded to 5 seconds.
CHECK_EVERY_SECONDS = 30
CHECK_TIMEOUT_SECONDS = 5


class MachineCheck(BaseModel):
    """One tile of the machine's state."""

    id: str
    label: str
    # The group of the tile on the screen: the runtime, models, tools or intake.
    group: str
    status: CheckStatus
    detail: str
    # Readings shown with the tile, as text (free memory, loaded models, use of the GPU...).
    metrics: dict[str, str] = Field(default_factory=dict)
    # ISO 8601, UTC.
    checked_at: str


class DeclaredCheck(BaseModel):
    """One check of the machine, declared in the configuration."""

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    group: str = Field(min_length=1)
    kind: CheckKind
    # `http`: the address; `tcp`: the port, or host:port; `vram` and `funnel`: the command, when it is not on the PATH.
    target: str | None = None
    every_seconds: int = Field(default=CHECK_EVERY_SECONDS, ge=CHECK_EVERY_SECONDS)


class RuntimeChecks(Protocol):
    """The runtime's own checks, given by its adapter: local files and bases only, each one quick."""

    def checks(self, now: float) -> list[MachineCheck]: ...
