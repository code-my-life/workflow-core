"""The data a workflow declares besides its cards, read at the declared paths and turned into generic values
(ADR 0011, issue #36).

The readers know no workflow: a JSON file whose declared fields become values, a folder of numbered texts, an agent's
session, Claude Code's sessions of a run, a card's raw log, the drift of an agent's configuration. Every path is built
here from the declaration and checked names, under its root, without a symbolic link on the way, its size bounded as
factory-live bounds it (apps/server/src/adapters/hermes/files.ts, sessions.ts, evidence.ts, application/drift.ts at
commit d47f235). Nothing is written; a secret found in a text is masked.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflow_core.adapters.hermes.paths import CARD, NAME, safe_entry
from workflow_core.adapters.hermes.profiles import MAX_CONFIG_BYTES, parse_capped
from workflow_core.adapters.hermes.sessions import open_session_base, run_session
from workflow_core.adapters.hermes.texts import TEXTS
from workflow_core.config import Workflow
from workflow_core.diagnosis.support import is_refusal
from workflow_core.model.data import (
    ID_FORMS,
    DataDeclaration,
    DataField,
    DeclaredData,
    LinkValue,
    StatusValue,
    TableValue,
    TextValue,
)
from workflow_core.model.facts import CardFacts, RunFact
from workflow_core.text import redact_secrets

# Where Claude Code keeps its sessions: one folder per working folder, one JSONL file per session.
CLAUDE_VARIABLE = "CLAUDE_PROJECTS"
DEFAULT_CLAUDE = "~/.claude/projects"

# The bounds of factory-live: a JSON read whole up to 256 KiB, a transcript entry cut at 20 000 characters, a card's
# log read by its last 512 KiB, a proof by its first and last 32 KiB past 64 KiB, a session by its 2 000 first
# messages, a Claude Code session file up to 16 MiB.
JSON_MAX_BYTES = 256 * 1024
TEXT_MAX = 20_000
LOG_TAIL_BYTES = 512 * 1024
PROOF_BYTES = 64 * 1024
PROOF_HALF = (PROOF_BYTES - 64) // 2
SESSION_MESSAGES = 2_000
CLAUDE_MAX_BYTES = 16 * 1024 * 1024

# The columns of a session's messages the transcript reads, declared for the contract test (contract.py).
TRANSCRIPT_MESSAGE_COLUMNS = (
    "id",
    "session_id",
    "role",
    "content",
    "reasoning",
    "tool_calls",
    "tool_name",
    "timestamp",
)
TRANSCRIPT_SQL = f"""
  SELECT {", ".join(TRANSCRIPT_MESSAGE_COLUMNS)} FROM messages WHERE session_id = ? ORDER BY id LIMIT ?"""
TRANSCRIPT_COLUMNS = ["Heure", "Rôle", "Texte", "Outil", "Modèle", "Statut"]
DRIFT_COLUMNS = ["Champ", "Service", "Dépôt"]

# A numbered text of a folder: « 01-format.txt », its order and its rule.
NUMBERED_TEXT = re.compile(r"^(\d{2})-([\w-]+)\.txt$", re.ASCII)
_ROOTED = re.compile(r"^\{(\w+)\}(?:/(.*))?$")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def claude_projects(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get(CLAUDE_VARIABLE) or DEFAULT_CLAUDE).expanduser()


def iso_ms(ms: float) -> str:
    """JavaScript's `toISOString`: milliseconds, UTC, ending in `Z`."""
    moment = datetime.fromtimestamp(round(ms) / 1000, UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def clip(value: object) -> str | None:
    """A transcript entry's text as factory-live clips it: secrets masked, cut at 20 000 characters, the rest
    counted."""
    if value is None:
        return None
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    clean = redact_secrets(raw)
    size = _utf16_length(clean)
    if size <= TEXT_MAX:
        return clean
    head = clean.encode("utf-16-le", "surrogatepass")[: TEXT_MAX * 2].decode("utf-16-le", "ignore")
    return f"{head}\n… ({size - TEXT_MAX} caractères de plus)"


def _json_object(text: object) -> dict:
    if not isinstance(text, str) or not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _record(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _dotted(value: Any, key: str) -> Any:
    for part in key.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _cell(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class _Run:
    """A run of a board, with its card: what the readers of a run need."""

    def __init__(self, facts: CardFacts, run: RunFact) -> None:
        self.card = facts.card
        self.run = run


class HermesDataReader:
    """Reads the data a workflow declares for a subject (`DeclaredDataReader`), under one Hermès home."""

    def __init__(self, home: Path, boards, claude: Path, clock=time.time) -> None:
        self.home = home
        self.boards = boards
        self.claude = claude
        self.clock = clock

    # --- The subject ---------------------------------------------------------------------------------------------

    def _subject(self, workflow: Workflow, subject: str, item: str) -> tuple[dict[str, str], _Run | None] | None:
        """The parameters of the subject's paths, and the run for a run; `None` when nothing can be read for it."""
        form = ID_FORMS[subject].fullmatch(item)
        if form is None:
            return None
        if subject == "pr":
            return ({"repo": form["repo"], "pr": form["pr"]}, None) if NAME.fullmatch(form["repo"]) else None
        if subject == "card":
            return ({"card": item}, None) if CARD.fullmatch(item) else None
        if subject == "agent":
            return ({"agent": item}, None) if NAME.fullmatch(item) else None
        run_id = int(item)
        for facts in self.boards.read_board_facts(workflow.board):
            for run in facts.runs:
                if run.id == run_id and run.profile and NAME.fullmatch(run.profile) and CARD.fullmatch(facts.card.id):
                    return {"run": item, "card": facts.card.id, "agent": run.profile}, _Run(facts, run)
        return None

    # --- The paths -----------------------------------------------------------------------------------------------

    def _roots(self, workflow: Workflow) -> dict[str, Path | None]:
        board = self.home / "kanban" / "boards" / workflow.board if NAME.fullmatch(workflow.board) else None
        return {"runtime": self.home, "board": board, "workflow": workflow.repo_path, "claude": self.claude}

    def _parts(self, workflow: Workflow, pattern: str, params: dict[str, str]) -> tuple[Path, list[str]] | None:
        """The root and the checked parts of a declared path, or `None` when it names an unknown root or parameter,
        or climbs out of its root."""
        rooted = _ROOTED.fullmatch(pattern)
        root = self._roots(workflow).get(rooted[1]) if rooted else None
        if rooted is None or root is None:
            return None
        rest = rooted[2] or ""
        names = _PLACEHOLDER.findall(rest)
        if any(name not in params for name in names):
            return None
        filled = _PLACEHOLDER.sub(lambda match: params[match[1]], rest)
        parts = filled.split("/") if filled else []
        if any(part in ("", ".", "..") for part in parts):
            return None
        return root, parts

    def _entry(self, workflow: Workflow, declaration: DataDeclaration, params: dict[str, str], kind) -> Path | None:
        """The first declared place found: a file or a folder under its root, without a symbolic link."""
        for pattern in declaration.paths:
            found = self._parts(workflow, pattern, params)
            if found is None:
                continue
            root, parts = found
            if not parts:
                if kind == "dir" and root.is_dir() and not root.is_symlink():
                    return root
                continue
            entry = safe_entry(root, parts, kind)
            if entry is not None:
                return entry
        return None

    # --- The readers ---------------------------------------------------------------------------------------------

    def read(self, workflow: Workflow, subject: str, item: str) -> list[DeclaredData]:
        found = self._subject(workflow, subject, item)
        data = []
        for declaration in workflow.data:
            if declaration.subject != subject:
                continue
            if found is None:
                values = []
            else:
                try:
                    values = self._values(workflow, declaration, *found)
                except Exception as error:  # one unreadable data never fails the answer
                    values = [StatusValue(label=declaration.label, status="error", text=f"illisible : {error}")]
            data.append(DeclaredData(name=declaration.name, label=declaration.label, values=values))
        return data

    def _values(self, workflow: Workflow, declaration: DataDeclaration, params: dict[str, str], run: _Run | None):
        reader = declaration.reader
        if reader == "json":
            return self._json(workflow, declaration, params)
        if reader == "texts":
            return self._texts(workflow, declaration, params)
        if reader == "log":
            return self._log(workflow, declaration, params)
        if reader == "drift":
            return self._drift(workflow, declaration, params)
        if run is None:
            return []
        if reader == "session":
            return self._session(workflow, declaration, params, run)
        return self._claude(workflow, declaration, params, run)

    def _json(self, workflow: Workflow, declaration: DataDeclaration, params: dict[str, str]):
        path = self._entry(workflow, declaration, params, "file")
        if path is None:
            return []
        label = declaration.label
        if path.stat().st_size > JSON_MAX_BYTES:
            return [StatusValue(label=label, status="error", text=f"illisible : plus de {JSON_MAX_BYTES} octets")]
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return [StatusValue(label=label, status="error", text=f"illisible : {error}")]
        return [value for field in declaration.fields if (value := self._field(document, field)) is not None]

    @staticmethod
    def _field(document: Any, field: DataField):
        value = _dotted(document, field.key)
        if value is None or value == "":
            return None
        if field.kind == "text":
            return TextValue(label=field.label, text=redact_secrets(value if isinstance(value, str) else str(value)))
        if field.kind == "link":
            return LinkValue(label=field.label, url=str(value)) if isinstance(value, str) else None
        if field.kind == "status":
            text = value if isinstance(value, str) else str(value)
            return StatusValue(label=field.label, status=field.statuses.get(text, "unknown"), text=text)
        rows = [[_cell(row.get(column)) for column in field.columns] for row in value if isinstance(row, dict)]
        headers = field.headers if len(field.headers) == len(field.columns) else list(field.columns)
        return TableValue(label=field.label, columns=headers, rows=rows) if isinstance(value, list) else None

    def _texts(self, workflow: Workflow, declaration: DataDeclaration, params: dict[str, str]):
        """A folder of numbered texts, in their order; a path ending in `*` reads its newest subfolder."""
        newest_of = any(pattern.endswith("/*") for pattern in declaration.paths)
        folder_declaration = declaration.model_copy(
            update={"path": [pattern.removesuffix("/*") for pattern in declaration.paths]}
        )
        folder = self._entry(workflow, folder_declaration, params, "dir")
        if folder is None:
            return []
        if newest_of:
            subfolders = []
            for child in folder.iterdir():
                entry = safe_entry(folder, [child.name], "dir") if NAME.fullmatch(child.name) else None
                if entry is not None:
                    subfolders.append((entry.stat().st_mtime, entry.name, entry))
            if not subfolders:
                return []
            folder = max(subfolders)[2]
        texts = []
        for child in folder.iterdir():
            numbered = NUMBERED_TEXT.fullmatch(child.name)
            path = safe_entry(folder, [child.name], "file") if numbered else None
            if numbered and path is not None:
                texts.append((int(numbered[1]), numbered[2], path))
        return [
            TextValue(label=rule, text=redact_secrets(self._cut(path)))
            for _, rule, path in sorted(texts, key=lambda text: (text[0], text[1]))
        ]

    @staticmethod
    def _cut(path: Path) -> str:
        """A proof's text, whole up to 64 KiB; past that, its start and its end, where a command says how it ended."""
        with path.open("rb") as file:
            size = path.stat().st_size
            if size <= PROOF_BYTES:
                return file.read().decode("utf-8", errors="replace")
            head = file.read(PROOF_HALF)
            file.seek(size - PROOF_HALF)
            tail = file.read(PROOF_HALF)
        return f"{head.decode('utf-8', errors='replace')}\n…\n{tail.decode('utf-8', errors='replace')}"

    def _log(self, workflow: Workflow, declaration: DataDeclaration, params: dict[str, str]):
        """A raw log by its end, ANSI sequences kept for a terminal's rendering."""
        path = self._entry(workflow, declaration, params, "file")
        if path is None:
            return []
        with path.open("rb") as file:
            size = path.stat().st_size
            file.seek(max(0, size - LOG_TAIL_BYTES))
            text = file.read().decode("utf-8", errors="replace")
        if size > LOG_TAIL_BYTES:
            text = text[text.find("\n") + 1 :]
        return [TextValue(label=declaration.label, text=redact_secrets(text))]

    def _session(self, workflow: Workflow, declaration: DataDeclaration, params: dict[str, str], run: _Run):
        """The agent's session of the run, never guessed, as factory-live transcribes it (sessions.ts)."""
        path = self._entry(workflow, declaration, params, "file")
        if path is None:
            return []
        db = open_session_base(path)
        try:
            session = run_session(db, run.card.id, run.run)
            if session is None:
                return []
            rows = db.execute(TRANSCRIPT_SQL, (session, SESSION_MESSAGES)).fetchall()
        except sqlite3.Error as error:
            return [StatusValue(label=declaration.label, status="error", text=f"illisible : {error}")]
        finally:
            db.close()
        entries: list[list] = []
        for row in rows:
            at = iso_ms(round(float(row["timestamp"] or 0) * 1000))
            role = str(row["role"])
            content = row["content"] if isinstance(row["content"], str) else None
            if role == "assistant":
                reasoning = row["reasoning"] if isinstance(row["reasoning"], str) and row["reasoning"] else None
                if reasoning:
                    entries.append([at, "reasoning", clip(reasoning), None, None, None])
                if content:
                    entries.append([at, "assistant", clip(content), None, None, None])
                calls = _json_list(row["tool_calls"])
                for call in calls:
                    function = _record(_record(call).get("function"))
                    arguments = function.get("arguments")
                    name = function.get("name")
                    entries.append(
                        [
                            at,
                            "tool_call",
                            clip(arguments if isinstance(arguments, str) else None),
                            name if isinstance(name, str) else None,
                            None,
                            None,
                        ]
                    )
            elif role == "tool":
                tool = row["tool_name"] if isinstance(row["tool_name"], str) else None
                entries.append([at, "tool_result", clip(content), tool, None, self._tool_status(workflow, content)])
            else:
                entries.append([at, "user" if role == "user" else "system", clip(content), None, None, None])
        return [TableValue(label=declaration.label, columns=TRANSCRIPT_COLUMNS, rows=entries)]

    @staticmethod
    def _tool_status(workflow: Workflow, content: str | None) -> str:
        if not content:
            return "ok"
        # A refusal by a guard or by the runtime is a refusal, not an error of the tool.
        if is_refusal(TEXTS.classify_tool_result(content, workflow.guard_suffixes, workflow.pr_acceptance)):
            return "blocked"
        parsed = _json_object(content)
        if parsed.get("blocked") is True or parsed.get("status") == "blocked":
            return "blocked"
        return "error" if parsed.get("error") else "ok"

    def _claude(self, workflow: Workflow, declaration: DataDeclaration, params: dict[str, str], run: _Run):
        """Claude Code's sessions launched from the card's working folder during the run (files.ts): one table per
        session, named by its last custom title, the oldest first."""
        projects = self._entry(workflow, declaration, params, "dir")
        workspace = run.card.workspace_path
        if projects is None or not workspace:
            return []
        prefix = re.sub(r"[^a-zA-Z0-9]", "-", workspace)
        start_ms = run.run.started_at * 1000
        end_ms = (run.run.ended_at if run.run.ended_at is not None else self.clock()) * 1000
        lanes = []
        for folder in sorted(projects.iterdir(), key=lambda entry: entry.name):
            if folder.name != prefix and not folder.name.startswith(f"{prefix}-"):
                continue
            if safe_entry(projects, [folder.name], "dir") is None:
                continue
            for file in sorted(folder.iterdir(), key=lambda entry: entry.name):
                path = safe_entry(projects, [folder.name, file.name], "file") if file.name.endswith(".jsonl") else None
                if path is None:
                    continue
                stat = path.stat()
                mtime_ms = stat.st_mtime * 1000
                if not (mtime_ms >= start_ms and mtime_ms <= end_ms) or stat.st_size > CLAUDE_MAX_BYTES:
                    continue
                lanes.append(_lane(path.read_text(encoding="utf-8", errors="replace"), path.stem))
        lanes.sort(key=lambda lane: lane[0] if lane[0] is not None else float("inf"))
        return [TableValue(label=name, columns=TRANSCRIPT_COLUMNS, rows=rows) for _, name, rows in lanes]

    def _drift(self, workflow: Workflow, declaration: DataDeclaration, params: dict[str, str]):
        """The differences, field by field, between an agent's configuration in service (`path`) and the one of its
        workflow's repository (`compare`), as factory-live's drift (drift.ts)."""
        live_path = self._entry(workflow, declaration, params, "file")
        compare = declaration.model_copy(update={"path": declaration.compare or ""})
        repo_path = self._entry(workflow, compare, params, "file") if declaration.compare else None
        if live_path is None or repo_path is None:
            return []
        live, repo = parse_capped(live_path, MAX_CONFIG_BYTES), parse_capped(repo_path, MAX_CONFIG_BYTES)
        if live is None or repo is None:
            return [StatusValue(label=declaration.label, status="error", text="illisible : configuration")]
        plugins = None
        if declaration.plugins_from and declaration.plugins_key:
            source = declaration.model_copy(update={"path": declaration.plugins_from})
            manifest_path = self._entry(workflow, source, params, "file")
            manifest = parse_capped(manifest_path, MAX_CONFIG_BYTES) if manifest_path is not None else None
            key = _PLACEHOLDER.sub(lambda match: params.get(match[1], ""), declaration.plugins_key)
            found = _dotted(manifest, key) if manifest is not None else None
            plugins = _strings(found) if isinstance(found, list) else None
        rows = config_drift(profile_config_of(live), profile_config_of(repo), plugins)
        return [TableValue(label=declaration.label, columns=DRIFT_COLUMNS, rows=rows)]


def _json_list(text: object) -> list:
    if not isinstance(text, str) or not text:
        return []
    try:
        value = json.loads(text)
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _lane(content: str, stem: str) -> tuple[float | None, str, list[list]]:
    """A Claude Code session: its start, its name (its last custom title, else its file's name) and its entries."""
    name: str | None = None
    first: float | None = None
    rows: list[list] = []
    for index, line in enumerate(content.split("\n")):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("type") == "custom-title" and isinstance(record.get("customTitle"), str):
            name = redact_secrets(record["customTitle"])
        at_text = record.get("timestamp")
        at = _parse_ms(at_text) if isinstance(at_text, str) else 0
        if at > 0 and (first is None or at < first):
            first = at
        if record.get("type") not in ("user", "assistant"):
            continue
        message = _record(record.get("message"))
        model = message.get("model") if isinstance(message.get("model"), str) else None
        content_parts = message.get("content")
        parts = (
            [{"type": "text", "text": content_parts}]
            if isinstance(content_parts, str)
            else [part for part in content_parts if isinstance(part, dict)]
            if isinstance(content_parts, list)
            else []
        )
        when = iso_ms(at)
        for part in parts:
            kind = part.get("type")
            if kind == "text":
                role = "user" if record.get("type") == "user" else "assistant"
                rows.append([when, role, clip(part.get("text")), None, model, None])
            elif kind == "thinking":
                rows.append([when, "reasoning", clip(part.get("thinking")), None, model, None])
            elif kind == "tool_use":
                tool = part.get("name") if isinstance(part.get("name"), str) else None
                rows.append([when, "tool_call", clip(part.get("input")), tool, model, None])
            elif kind == "tool_result":
                status = "error" if part.get("is_error") is True else "ok"
                rows.append([when, "tool_result", clip(part.get("content")), None, model, status])
        del index
    return first, name or stem, rows


def _parse_ms(text: str) -> float:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return 0


def profile_config_of(yaml: dict) -> dict:
    """What the drift compares of an agent's configuration (factory-live's `profileConfigOf`, profiles.ts)."""
    model = _record(yaml.get("model"))
    agent = _record(yaml.get("agent"))
    toolsets = _record(yaml.get("platform_toolsets"))
    hooks = _record(yaml.get("hooks"))
    name = _str(model.get("default"))
    fallbacks = yaml.get("fallback_providers") if isinstance(yaml.get("fallback_providers"), list) else []
    return {
        "model": (name, _str(model.get("provider")) or "?") if name else None,
        "baseUrl": _str(model.get("base_url")),
        "fallbacks": [
            (_str(_record(entry).get("model")), _str(_record(entry).get("provider")) or "?")
            for entry in fallbacks
            if _str(_record(entry).get("model"))
        ],
        "effort": _str(agent.get("reasoning_effort")),
        "toolsets": _strings(toolsets.get("cli")),
        "plugins": _strings(_record(yaml.get("plugins")).get("enabled")),
        "outboundHooks": [
            name
            for entry in (hooks.get("outbound") if isinstance(hooks.get("outbound"), list) else [])
            if (name := _str(_record(entry).get("name")))
        ],
    }


def config_drift(live: dict, repo: dict, manifest_plugins: list[str] | None = None) -> list[list[str | None]]:
    """The fields that differ, each with its value in service and in the repository (factory-live's `configDrift`)."""

    def show(link: tuple[str, str] | None) -> str | None:
        return f"{link[0]} ({link[1]})" if link else None

    def listed(values: list[str]) -> str | None:
        return ", ".join(values) if values else None

    def fallbacks(config: dict) -> str | None:
        return listed([shown for link in config["fallbacks"] if (shown := show(link))])

    # The plugins compare to the workflow's manifest when it lists them: its install enables them from it.
    repo_plugins = manifest_plugins if manifest_plugins is not None else repo["plugins"]

    fields = [
        ("model", show(live["model"]), show(repo["model"])),
        ("baseUrl", live["baseUrl"], repo["baseUrl"]),
        ("fallbacks", fallbacks(live), fallbacks(repo)),
        ("effort", live["effort"], repo["effort"]),
        ("toolsets", listed(live["toolsets"]), listed(repo["toolsets"])),
        ("plugins", listed(sorted(live["plugins"])), listed(sorted(repo_plugins))),
        ("outboundHooks", listed(live["outboundHooks"]), listed(repo["outboundHooks"])),
    ]
    return [[field, left, right] for field, left, right in fields if left != right]
