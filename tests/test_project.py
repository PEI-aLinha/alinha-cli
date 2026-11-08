from pathlib import Path

import pytest

from alinha_cli.project import (
    IncompatibleVersion,
    ProjectNotFound,
    check_cli_version,
    find_root,
    read_plugins,
)


def test_find_root_works_from_inside_a_plugin(project: Path) -> None:
    assert find_root(project / "plugins" / "feature" / "demo") == project


def test_find_root_fails_outside_a_checkout(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotFound):
        find_root(tmp_path)


def test_read_plugins_lists_submodules_from_gitmodules(project: Path) -> None:
    plugins = read_plugins(project)
    assert [(p.name, p.path) for p in plugins] == [
        ("feature/demo", project / "plugins" / "feature" / "demo")
    ]


def test_version_check_passes_when_pin_file_is_absent(project: Path) -> None:
    check_cli_version(project, "0.1.0")


def test_version_check_passes_inside_range(project: Path) -> None:
    (project / ".alinha-cli-version").write_text(">=0.1.0,<0.2.0\n")
    check_cli_version(project, "0.1.5")


def test_version_check_rejects_version_outside_range(project: Path) -> None:
    (project / ".alinha-cli-version").write_text(">=0.2.0,<0.3.0\n")
    with pytest.raises(IncompatibleVersion, match="uv tool install"):
        check_cli_version(project, "0.1.0")
