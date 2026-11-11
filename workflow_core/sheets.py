"""The sheet of a declared workflow, read from its path and judged at each request (ADR 0006)."""

from __future__ import annotations

import json

from workflow_core.config import Workflow
from workflow_core.model.sheet import SheetRefused, WorkflowSheet, judge_sheet


class SheetUnavailableError(Exception):
    """The sheet's file is missing or is not JSON."""


class InvalidSheetError(Exception):
    """The sheet breaks a rule; the message starts with the rule's name."""


def read_sheet(workflow: Workflow) -> WorkflowSheet:
    path = workflow.sheet_path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise SheetUnavailableError(f'sheet of "{workflow.key}" cannot be read at {path}: {error}') from error
    except json.JSONDecodeError as error:
        raise SheetUnavailableError(f'sheet of "{workflow.key}" at {path} is not JSON: {error}') from error
    verdict = judge_sheet(data)
    if isinstance(verdict, SheetRefused):
        raise InvalidSheetError(f"{verdict.rule}: {verdict.detail}")
    return verdict.sheet


def sheet_json(sheet: WorkflowSheet) -> dict:
    """The sheet as it is served: the published form, without the absent optional fields."""
    return sheet.model_dump(mode="json", by_alias=True, exclude_none=True)
