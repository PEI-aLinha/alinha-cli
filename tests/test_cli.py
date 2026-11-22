from pathlib import Path

import pytest
from typer.testing import CliRunner

from alinha_cli.cli import app

runner = CliRunner()


def test_outside_checkout_shows_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["plugins", "status"])
    assert result.exit_code == 2
    assert "aLinha checkout" in result.output


def test_incompatible_version_blocks_commands(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / ".alinha-cli-version").write_text(">=9.0.0\n")
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["plugins", "status"])
    assert result.exit_code == 2
    assert "uv tool install" in result.output


def test_plugins_status_prints_each_plugin(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(project / "plugins" / "feature" / "demo")
    result = runner.invoke(app, ["plugins", "status"])
    assert result.exit_code == 0
    assert "feature/demo" in result.output


def test_plugins_status_shows_the_table_when_a_fetch_fails(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from conftest import git

    git(project / "plugins/feature/demo", "remote", "set-url", "origin", str(project / "nope"))
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["plugins", "status", "--fetch"])
    assert result.exit_code == 1
    assert "feature/demo" in result.output
    assert "fetch failed" in result.output
    assert "Could not fetch feature/demo" in result.output


def test_doctor_fails_when_a_check_fails(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from alinha_cli import cli
    from alinha_cli.doctor import FAIL, OK, Check

    monkeypatch.chdir(project)
    monkeypatch.setattr(
        cli, "run_doctor", lambda _root: [Check("java", OK, ""), Check("node", FAIL, "missing")]
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "node: missing" in result.output


def test_dev_reports_dependency_errors(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from alinha_cli import cli

    def broken(_root: Path) -> None:
        raise cli.DependencyError("compose failed")

    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "start_dependencies", broken)
    result = runner.invoke(app, ["dev"])
    assert result.exit_code == 1
    assert "compose failed" in result.output


def test_dev_without_deps_returns_the_supervisor_code(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alinha_cli import cli

    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "start_dependencies", lambda _root: pytest.fail("deps started"))
    monkeypatch.setattr(cli, "supervise", lambda _procs, _emit: 3)
    assert runner.invoke(app, ["dev", "--no-deps"]).exit_code == 3


def test_down_stops_dependencies(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from alinha_cli import cli

    stopped: list[Path] = []
    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "stop_dependencies", stopped.append)
    assert runner.invoke(app, ["down"]).exit_code == 0
    assert stopped == [project]


def test_dev_with_a_role_starts_only_the_frontend(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alinha_cli import cli

    started: list[list[str]] = []
    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "start_dependencies", lambda _root: pytest.fail("deps started"))
    monkeypatch.setattr(
        cli, "supervise", lambda procs, _emit: started.append([p.name for p in procs]) or 0
    )
    assert runner.invoke(app, ["dev", "--role", "analyst"]).exit_code == 0
    assert started == [["web"]]


@pytest.mark.parametrize("args", [["--role", "admin"], ["--role", "analyst", "--demo"]])
def test_dev_rejects_bad_role_options(
    project: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    from alinha_cli import cli

    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "supervise", lambda _procs, _emit: pytest.fail("apps started"))
    assert runner.invoke(app, ["dev", *args]).exit_code == 2


def test_dev_passes_demo_to_the_backend(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from alinha_cli import cli

    envs: list[dict] = []
    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "supervise", lambda procs, _emit: envs.append(dict(procs[0].env)) or 0)
    assert runner.invoke(app, ["dev", "--no-deps", "--demo"]).exit_code == 0
    assert envs[0]["SPRING_PROFILES_ACTIVE"] == "demo"
