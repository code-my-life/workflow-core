"""The interventions of a board, as the core publishes them (ADR 0012, ADR 0018): each trigger of the diagnosis with
the title, question, severity, notification and actions factory-live's `interventionsOf` gave it at commit d47f235
(packages/domain/src/interventions.ts).

A text is a code of factory-live's catalogue with its parameters, `{"code": "intervention.<...>", "params": {...}}`,
or a quote of the agent's reason as written, `{"quote": "<text>"}`: the sentences stay with factory-live. The links
to an issue or a pull request stay with factory-live too, which knows to which issue a card belongs.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from workflow_core.model.triggers import TriggerKind

Param = str | int | float | bool | None


class CodedText(BaseModel):
    """A text of factory-live's catalogue: its code and its parameters."""

    code: str
    params: dict[str, Param] = Field(default_factory=dict)


class QuotedText(BaseModel):
    """The agent's own words, as written."""

    quote: str


InterventionText = CodedText | QuotedText


class GestureAction(BaseModel):
    """A gesture the workflow declares, that applies to the card (`unblock`): the client posts it."""

    kind: Literal["gesture"] = "gesture"
    gesture: str


class CopyAction(BaseModel):
    """A command to copy, written by the runtime's adapter, and its label."""

    kind: Literal["copy"] = "copy"
    label: CodedText
    command: str


class LinkAction(BaseModel):
    kind: Literal["link"] = "link"
    label: CodedText
    url: str


Action = Annotated[GestureAction | CopyAction | LinkAction, Field(discriminator="kind")]


class Intervention(BaseModel):
    """A point where the workflow waits for its owner."""

    # Stable from one computation to the next: `<kind>:<card>:<event id, or opening time>`.
    id: str
    kind: TriggerKind
    # The card.
    item: str
    # The event told (the block, the give-up, the loop); `None` when none is.
    event: int | None
    # ISO 8601 UTC, to the millisecond.
    opened_at: str
    # The agent the diagnosis examines.
    agent: str | None
    severity: Literal["critical", "warning", "info"]
    # Whether the owner is notified.
    push: bool
    title: InterventionText
    question: InterventionText
    actions: list[Action]


class InterventionClosed(BaseModel):
    """The state of an `intervention_closed` event: the intervention gone."""

    id: str
