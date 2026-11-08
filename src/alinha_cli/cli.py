"""Command-line entry point."""

from pathlib import Path

import typer

from alinha_cli import __version__
from alinha_cli.dev import (
    DependencyError,
    app_processes,
    start_dependencies,
    stop_dependencies,
    supervise,
)
from alinha_cli.doctor import FAIL, OK, Check, run_doctor
from alinha_cli.plugins import PluginStatus, plugin_statuses
from alinha_cli.project import (
    IncompatibleVersion,
    ProjectNotFound,
    check_cli_version,
    find_root,
)

EXIT_USAGE = 2

app = typer.Typer(no_args_is_help=True, add_completion=False)
plugins_app = typer.Typer(no_args_is_help=True, help="Inspect plugin submodules.")
app.add_typer(plugins_app, name="plugins")


def _fail(message: str, code: int = 1) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return typer.Exit(code)


def _root() -> Path:
    try:
        root = find_root(Path.cwd())
        check_cli_version(root, __version__)
    except (ProjectNotFound, IncompatibleVersion) as error:
        raise _fail(str(error), EXIT_USAGE) from error
    return root


def _show_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    _version: bool = typer.Option(
        False, "--version", callback=_show_version, is_eager=True, help="Show the version."
    ),
) -> None:
    """Development tooling for the aLinha platform."""


def _format_status(status: PluginStatus) -> str:
    if not status.initialized:
        return f"{status.name:<30} not initialized"
    drift = "?" if status.ahead is None else f"+{status.ahead}/-{status.behind}"
    state = "dirty" if status.dirty else "clean"
    main_state = "on main" if status.on_main else "NOT on main"
    return f"{status.name:<30} {status.branch:<24} {state:<6} {drift:<8} {main_state}"


@plugins_app.command("status")
def plugins_status(
    fetch: bool = typer.Option(False, "--fetch", help="Fetch origin/main first."),
) -> None:
    """Show branch, local changes and drift from the main repo's pointer."""
    root = _root()
    try:
        statuses = plugin_statuses(root, fetch=fetch)
    except RuntimeError as error:
        raise _fail(str(error)) from error
    typer.echo(f"{'PLUGIN':<30} {'BRANCH':<24} {'STATE':<6} {'POINTER':<8} MAIN")
    for status in statuses:
        typer.echo(_format_status(status))


def _print_check(check: Check) -> None:
    colors = {OK: typer.colors.GREEN, FAIL: typer.colors.RED}
    label = typer.style(f"{check.level:<5}", fg=colors.get(check.level, typer.colors.YELLOW))
    typer.echo(f"{label} {check.name}{f': {check.detail}' if check.detail else ''}")


@app.command()
def doctor() -> None:
    """Check tools, git settings, submodules, ports and dev secrets."""
    checks = run_doctor(_root())
    for check in checks:
        _print_check(check)
    if any(check.level == FAIL for check in checks):
        raise typer.Exit(1)


@app.command()
def dev(
    no_deps: bool = typer.Option(False, "--no-deps", help="Do not start the containers."),
) -> None:
    """Start dependencies, then run the backend and frontend until Ctrl-C."""
    root = _root()
    if not no_deps:
        try:
            start_dependencies(root)
        except DependencyError as error:
            raise _fail(str(error)) from error
    raise typer.Exit(supervise(app_processes(root), typer.echo))


@app.command()
def down() -> None:
    """Stop the dependency containers, keeping their data."""
    root = _root()
    try:
        stop_dependencies(root)
    except DependencyError as error:
        raise _fail(str(error)) from error
