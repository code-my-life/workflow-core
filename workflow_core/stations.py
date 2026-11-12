"""Where an item stands (#140): the key of the station of its workflow's sheet, or None.

A pure rule, without a model, and the same for every workflow; everything it names lives here, so that the engine of
the next epic (ADR 0004, ADR 0013) can take it over in one place. At this stage each card is its own item (ADR 0016),
and a card stands, in this order:
1. at the sheet's first station of kind `end`, once the card is finished;
2. at the station whose lane name is the card's agent, `<workflow key>-<station key>` (the name ADR 0004 gives the
   lanes the engine will start);
3. at the station whose `agents` names the card's agent;
4. nowhere (None). A missing or refused sheet places no item.
"""

from __future__ import annotations

from workflow_core.model.board import Item
from workflow_core.model.sheet import WorkflowSheet

# The statuses of a finished card.
FINISHED = frozenset({"done", "archived"})
# The kind of the station a finished item stands at.
END_KIND = "end"


def lane_name(workflow: str, station: str) -> str:
    """The name of the agent that works at a station, by convention (ADR 0004)."""
    return f"{workflow}-{station}"


def station_of(item: Item, sheet: WorkflowSheet | None, workflow: str) -> str | None:
    """The key of the station where the item stands, or None."""
    if sheet is None:
        return None
    if item.status in FINISHED:
        return next((station.key for station in sheet.stations if station.kind == END_KIND), None)
    agent = item.assignee
    if not agent:
        return None
    for station in sheet.stations:
        if agent == lane_name(workflow, station.key):
            return station.key
    for station in sheet.stations:
        if agent in (station.agents or []):
            return station.key
    return None


def place(items: list[Item], sheet: WorkflowSheet | None, workflow: str) -> list[Item]:
    """The items, each with the station where it stands."""
    return [item.model_copy(update={"station": station_of(item, sheet, workflow)}) for item in items]
