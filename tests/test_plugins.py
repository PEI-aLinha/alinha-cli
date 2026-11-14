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


def test_submodule_without_committed_pointer_has_unknown_drift(
    project: Path, tmp_path: Path
) -> None:
    other = tmp_path / "origin-other"
    other.mkdir()
    git(other, "init")
    commit(other, "first")
    git(project, "submodule", "add", str(other), "plugins/feature/other")  # staged, not committed
    status = next(s for s in plugin_statuses(project) if s.name == "feature/other")
    assert (status.ahead, status.behind) == (None, None)


def test_failed_fetch_is_reported_per_plugin_and_keeps_the_rest(project: Path) -> None:
    path = project / "plugins/feature/demo"
    git(path, "remote", "set-url", "origin", str(project / "does-not-exist"))
    status = plugin_statuses(project, fetch=True)[0]
    assert status.fetch_error
    assert (status.ahead, status.behind) == (0, 0)
    assert status.on_main  # from the last fetched origin/main
