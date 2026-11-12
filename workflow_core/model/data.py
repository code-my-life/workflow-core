"""The data a workflow declares besides its cards, and the generic values they are served as (ADR 0011).

A workflow's entry of `config/workflows.toml` declares each of its data with a `[[workflow.data]]` table: its name, its
subject (what it belongs to: a pull request, a card, a run, an agent), its label, the path where it is read and the
reader that reads it. The runtime's adapter reads it and turns it into values of four generic types (text, link,
table, status), which a client shows without knowing the workflow.
"""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, Field

from workflow_core.text import js_regex

Subject = Literal["pr", "card", "run", "agent"]
SUBJECTS: tuple[str, ...] = ("pr", "card", "run", "agent")
Reader = Literal["json", "texts", "session", "claude", "log", "drift"]
Cell = str | int | float | bool | None

# The form of an id, by subject: `<repository>#<n>` for a pull request, a card's id, a run's number, an agent's name.
_NAME = r"[A-Za-z0-9_.-]{1,100}"
ID_FORMS = {
    "pr": js_regex(rf"^(?P<repo>{_NAME})#(?P<pr>[1-9][0-9]{{0,9}})$"),
    "card": js_regex(rf"^{_NAME}$"),
    "run": js_regex(r"^[1-9][0-9]{0,15}$"),
    "agent": js_regex(r"^[A-Za-z0-9_-]{1,100}$"),
}


class DataField(BaseModel):
    """A field of a JSON file read, and the generic type it is served as. `key` may be dotted (`a.b`). A status maps
    the field's values to its states (`statuses`); a table reads a list of objects, one row per object, one column per
    key of `columns`, headed by `headers` (the keys by default)."""

    key: str = Field(min_length=1)
    label: str
    kind: Literal["text", "link", "table", "status"]
    statuses: dict[str, Literal["ok", "running", "warning", "error", "unknown"]] = Field(default_factory=dict)
    columns: list[str] = Field(default_factory=list)
    headers: list[str] = Field(default_factory=list)


class DataDeclaration(BaseModel):
    """One declared data of a workflow (`[[workflow.data]]`). `path`: where to read it, or several places, the first
    found is read; a path starts with its root (`{runtime}`, `{board}`, `{workflow}`, `{claude}`) and may name the
    subject's parameters (`{repo}` and `{pr}`, `{card}`, `{run}`, `{agent}`). The drift compares the file at `path`
    with the one at `compare`, and may take the plugins from `plugins_from` at the dotted `plugins_key`."""

    name: str = Field(min_length=1)
    subject: Subject
    label: str
    path: str | list[str]
    reader: Reader
    fields: list[DataField] = Field(default_factory=list)
    compare: str | None = None
    plugins_from: str | None = None
    plugins_key: str | None = None

    @property
    def paths(self) -> list[str]:
        return [self.path] if isinstance(self.path, str) else list(self.path)


class TextValue(BaseModel):
    type: Literal["text"] = "text"
    label: str
    text: str


class LinkValue(BaseModel):
    type: Literal["link"] = "link"
    label: str
    url: str


class TableValue(BaseModel):
    type: Literal["table"] = "table"
    label: str
    columns: list[str]
    rows: list[list[Cell]]


class StatusValue(BaseModel):
    type: Literal["status"] = "status"
    label: str
    status: Literal["ok", "running", "warning", "error", "unknown"]
    text: str


Value = Annotated[TextValue | LinkValue | TableValue | StatusValue, Field(discriminator="type")]


class DeclaredData(BaseModel):
    """One declared data, read: an absent one has no value, an unreadable one a status `error` that says so."""

    name: str
    label: str
    values: list[Value]


class DeclaredDataReader(Protocol):
    """Reads the data a workflow declares for one subject. `item`: the subject's id, already checked."""

    def read(self, workflow, subject: str, item: str) -> list[DeclaredData]: ...
