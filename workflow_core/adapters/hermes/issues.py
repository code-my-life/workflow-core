"""The issue of each card of a Hermès board: the factory's rule (`issue_of`, scripts/factory_issue.py at 290176e).

A card's issue is the GitHub issue address quoted by its body or its comments: the one its title names
(`impl claude-cockpit#42 : ...`) first, else the first one found, else its parent cards', up to three levels.
"""

from __future__ import annotations

import re

from workflow_core.model.board import Item

ISSUE_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)")
NAMED = re.compile(r"([\w.-]+)#(\d+)")
PARENT_LEVELS = 3


def issue_of(items: dict[str, Item], card: str, depth: int = 0) -> str | None:
    """The issue address of a card among the board's items, or `None`."""
    item = items.get(card)
    if item is None:
        return None
    texts = [item.body or ""] + [comment.body or "" for comment in item.comments]
    named = NAMED.search(item.title or "")
    if named:
        for text in texts:
            for found in ISSUE_URL.finditer(text):
                if found[1].split("/")[1] == named[1] and found[2] == named[2]:
                    return found[0]
    for text in texts:
        found = ISSUE_URL.search(text)
        if found:
            return found[0]
    if depth < PARENT_LEVELS:
        for parent in item.parents:
            address = issue_of(items, parent, depth + 1)
            if address:
                return address
    return None


def issue_urls(items: list[Item]) -> dict[str, str | None]:
    """The issue address of every card of the board, by card id."""
    by_id = {item.id: item for item in items}
    return {item.id: issue_of(by_id, item.id) for item in items}
