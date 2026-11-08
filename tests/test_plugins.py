from pathlib import Path

from conftest import commit, git

from alinha_cli.plugins import plugin_statuses


def demo(project: Path):
    return plugin_statuses(project)[0]


def test_clean_plugin_matches_pointer_and_main(project: Path) -> None:
    status = demo(project)
    assert status.initialized
    assert not status.dirty
    assert (status.ahead, status.behind) == (0, 0)
    assert status.on_main


def test_uncommitted_change_is_dirty(project: Path) -> None:
    (project / "plugins/feature/demo/new.txt").write_text("x")
    assert demo(project).dirty


def test_local_commit_is_ahead_of_pointer_and_off_main(project: Path) -> None:
    path = project / "plugins/feature/demo"
    git(path, "switch", "-c", "feat/x")
    commit(path, "local")
    status = demo(project)
    assert status.branch == "feat/x"
    assert (status.ahead, status.behind) == (1, 0)
    assert not status.on_main


def test_checkout_older_than_pointer_is_behind(project: Path) -> None:
    path = project / "plugins/feature/demo"
    commit(path, "second")
    git(project, "add", "plugins/feature/demo")
    git(project, "commit", "-m", "bump")
    git(path, "checkout", "-q", "HEAD~1")
    status = demo(project)
    assert status.branch == "(detached)"
    assert (status.ahead, status.behind) == (0, 1)


def test_uninitialized_submodule_is_reported(project: Path) -> None:
    git(project, "submodule", "deinit", "-f", "plugins/feature/demo")
    status = demo(project)
    assert not status.initialized
