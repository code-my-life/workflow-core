"""Build the core's service: check the contract with the runtime, then serve, whatever the verdict (ADR 0019)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import uvicorn
from fastapi import FastAPI

from workflow_core.api.app import create_app
from workflow_core.clock import utc_now
from workflow_core.config import Workflow
from workflow_core.engine import Engine, EngineRunner
from workflow_core.engine.answers import ENGINE_DB, AnswerBox
from workflow_core.engine.engine import PASS_SECONDS
from workflow_core.events import JOURNAL_FILE, state_dir
from workflow_core.intake import Intake, IntakeRegister, IntakeRunner
from workflow_core.model.board import BoardReader
from workflow_core.model.checks import CHECK_EVERY_SECONDS, DeclaredCheck
from workflow_core.model.contract import ContractVerdict
from workflow_core.model.state import CoreState
from workflow_core.notify import Notifier, NotifierRunner, NotifySettings
from workflow_core.notify.contract_issue import ContractIssueTask, report_contract
from workflow_core.notify.notifier import journal_line
from workflow_core.notify.register import REGISTER_FILE
from workflow_core.plugin.manifest import ManifestRefusedError, PluggedWorkflow, plug
from workflow_core.version_watch import VersionWatch, VersionWatchRunner

# journald reads a leading "<n>" on a service's output as the syslog priority of the line.
ERROR = "<3>"
INFO = "<6>"


def journal(priority: str, message: str) -> None:
    journal_line(f"{priority}workflow-core: {message}")


def build_service(
    *,
    secret: str,
    workflows: dict[str, Workflow],
    adapter: ModuleType,
    clock: Callable[[], float] = time.time,
    spy: Callable[[str], Any] | None = None,
    checks: list[DeclaredCheck] | None = None,
    checks_every: float = CHECK_EVERY_SECONDS,
    environ: dict[str, str] | None = None,
    post_comment: Callable[[str, int, str], str] | None = None,
    read_comments: Callable[[str, int], list] | None = None,
    github: Any = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    engine_every: float = PASS_SECONDS,
    version_clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    """The API, with the core's state after the contract check. A broken contract keeps the observer mode, is written
    to the journal and to its issue in code-my-life/workflow-core (the one write of the observer mode); the service
    still answers. `github` replaces the core's GitHub client (tests).

    The workflows with a sheet are served by the API and the notifier, as before; those plugged by their manifest are
    served by the engine only. The contract covers the served boards and the manifests' boards that already exist: a
    board the engine has not created yet would otherwise break it, and the engine would never create it.

    The runtime's version read at the start is read again every 5 minutes (`version_clock` replaces the clock of those
    reads in the tests): a change puts the core in observer mode until a restart. The engine's writer refuses every
    write until a first read confirms that version, which the engine's first pass makes.

    The reading of GitHub (`workflow_core.intake`) runs only with a workflow plugged by its manifest: at the start, then
    every 10 minutes, it hands the new issues of the manifests' repositories to their intake commands, through the
    engine's writer."""
    served = {key: workflow for key, workflow in workflows.items() if not workflow.plugged_by_manifest}
    reader = adapter.make_board_reader()
    plugged = plug_manifests(
        [workflow for workflow in workflows.values() if workflow.plugged_by_manifest], reader=reader
    )
    writer = adapter.make_board_writer()
    boards = sorted(
        {workflow.board for workflow in served.values()}
        | {workflow.board for workflow in plugged if writer.has_board(workflow.board)}
    )
    try:
        verdict = adapter.check_contract(boards)
    except Exception as error:  # a check that cannot run is a broken contract, never a crash at startup
        verdict = ContractVerdict(ok=False, checked_at=utc_now(), problems=[f"vérification : {error}"])
    version, _ = adapter.read_runtime_version()
    state = CoreState(contract=verdict, runtime_version=version)
    if verdict.ok:
        journal(INFO, f"contrat tenu pour {', '.join(boards) or 'aucun board'} ; mode {state.mode}")
    else:
        journal(ERROR, f"contrat cassé, mode {state.mode} : " + " ; ".join(verdict.problems))

    settings = NotifySettings.from_environ(environ)
    for line in settings.describe():
        journal(INFO if settings.on or settings.problem is None else ERROR, line)
    if github is None:
        from workflow_core.adapters.github import AppTokens, GitHubClient

        github = GitHubClient(AppTokens(environ=environ))
    if post_comment is None or read_comments is None:
        post_comment = post_comment or github.post_comment
        read_comments = read_comments or github.list_comments
    notifier = Notifier(
        workflows=served,
        reader=reader,
        state=state,
        settings=settings,
        post_comment=post_comment,
        read_comments=read_comments,
        register_path=state_dir(environ) / REGISTER_FILE,
        clock=clock,
    )

    def report() -> None:
        report_contract(
            verdict,
            runtime=getattr(adapter, "RUNTIME_NAME", "runtime"),
            version=version,
            client=github,
            state_dir=state_dir(environ),
            now=now(),
            log=journal_line,
        )

    watch = VersionWatch(
        state, lambda: adapter.version_change(version), every=adapter.VERSION_CHECK_SECONDS, clock=version_clock
    )
    # One writer for the engine and the reading of GitHub: both refuse to write until the version is confirmed.
    engine_writer = adapter.make_board_writer(gate=watch.write_refusal)
    engine = Engine(
        workflows=plugged,
        reader=reader,
        writer=engine_writer,
        state=state,
        confirm_version=watch.ensure_confirmed,
        answers=AnswerBox(state_dir(environ) / ENGINE_DB),
    )
    intake = Intake(
        workflows=plugged,
        github=github,
        writer=engine_writer,
        state=state,
        register=IntakeRegister(state_dir(environ) / ENGINE_DB),
        confirm_version=watch.ensure_confirmed,
        ensure_board=engine.ensure_board,
        live_url=settings.live_url,
    )

    app = create_app(
        secret=secret,
        workflows=served,
        boards=reader,
        state=state,
        watcher=adapter.make_board_watcher(),
        live=adapter.make_live_reader(),
        live_watcher=adapter.make_live_watcher(),
        journal_path=state_dir(environ) / JOURNAL_FILE,
        clock=clock,
        spy=spy,
        runtime_checks=adapter.make_machine_checks(),
        checks=checks,
        checks_every=checks_every,
        background=[
            ContractIssueTask(report),
            NotifierRunner(notifier),
            EngineRunner(engine, engine_every),
            IntakeRunner(intake),
            VersionWatchRunner(watch, adapter.VERSION_CHECK_SECONDS),
        ],
    )
    app.state.notifier = notifier
    app.state.engine = engine
    app.state.intake = intake
    app.state.version_watch = watch
    return app


def plug_manifests(workflows: list[Workflow], *, reader: BoardReader | None = None) -> list[PluggedWorkflow]:
    """The workflows plugged by their judged manifest, the lanes in use read by `reader`; each one left unplugged is
    written to the journal with its reason, and the others run."""
    plugged = []
    for workflow in workflows:
        try:
            plugged.append(plug(workflow, reader=reader))
        except ManifestRefusedError as error:
            journal(ERROR, str(error))
            continue
        journal(INFO, f"workflow {workflow.key} branché par son manifeste, board {workflow.board}")
    return plugged


# Seconds uvicorn waits for open connections at a stop: an SSE client never closes its own, so the core ends them.
GRACEFUL_STOP_SECONDS = 2


def server_config(app: FastAPI, host: str, port: int, log_level: str = "info") -> uvicorn.Config:
    """How the core is served, by `python -m workflow_core` and by the tests alike."""
    return uvicorn.Config(
        app, host=host, port=port, log_level=log_level, timeout_graceful_shutdown=GRACEFUL_STOP_SECONDS
    )
