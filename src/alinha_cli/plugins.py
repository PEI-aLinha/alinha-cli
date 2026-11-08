"""Reports how each plugin checkout relates to the main repo's pointer."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from alinha_cli.project import Plugin, read_plugins


@dataclass(frozen=True)
class PluginStatus:
    name: str
    initialized: bool
    branch: str = "-"
    dirty: bool = False
    ahead: int | None = None
    behind: int | None = None
    on_main: bool = False


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, check=False, capture_output=True, text=True)


def _drift(plugin: Plugin, pointer: str) -> tuple[int | None, int | None]:
    """Returns (ahead, behind) relative to the pointer, or unknowns if it is not available."""
    counts = _git(plugin.path, "rev-list", "--left-right", "--count", f"{pointer}...HEAD")
    if counts.returncode != 0:
        return None, None
    behind, ahead = counts.stdout.split()
    return int(ahead), int(behind)


def _status(root: Path, plugin: Plugin, fetch: bool) -> PluginStatus:
    if not (plugin.path / ".git").exists():
        return PluginStatus(plugin.name, initialized=False)
    if fetch:
        fetched = _git(plugin.path, "fetch", "-q", "origin", "main")
        if fetched.returncode != 0:
            raise RuntimeError(f"Could not fetch {plugin.name}: {fetched.stderr.strip()}")
    relative = plugin.path.relative_to(root).as_posix()
    pointer = _git(root, "rev-parse", f"HEAD:{relative}").stdout.strip()
    ahead, behind = _drift(plugin, pointer)
    on_main = _git(plugin.path, "merge-base", "--is-ancestor", "HEAD", "origin/main")
    return PluginStatus(
        name=plugin.name,
        initialized=True,
        branch=_git(plugin.path, "branch", "--show-current").stdout.strip() or "(detached)",
        dirty=bool(_git(plugin.path, "status", "--porcelain").stdout.strip()),
        ahead=ahead,
        behind=behind,
        on_main=on_main.returncode == 0,
    )


def plugin_statuses(root: Path, fetch: bool = False) -> list[PluginStatus]:
    return [_status(root, plugin, fetch) for plugin in read_plugins(root)]
