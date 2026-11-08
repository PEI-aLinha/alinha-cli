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
