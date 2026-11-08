"""Locates the aLinha checkout and reads what it declares."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

COMPOSE_FILE = Path("deploy/docker-compose.dev.yml")
VERSION_FILE = ".alinha-cli-version"
INSTALL_COMMAND = "uv tool install --force git+ssh://git@github.com/PEI-aLinha/alinha-cli.git@<tag>"


class ProjectNotFound(Exception):
    pass


class IncompatibleVersion(Exception):
    pass


@dataclass(frozen=True)
class Plugin:
    name: str
    path: Path


def find_root(start: Path) -> Path:
    for candidate in (start.resolve(), *start.resolve().parents):
        if (candidate / ".gitmodules").is_file() and (candidate / COMPOSE_FILE).is_file():
            return candidate
    raise ProjectNotFound(f"{start} is not inside an aLinha checkout.")


def read_plugins(root: Path) -> list[Plugin]:
    result = subprocess.run(
        ["git", "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    paths = [line.split(maxsplit=1)[1] for line in result.stdout.splitlines()]
    return [Plugin(path.removeprefix("plugins/"), root / path) for path in sorted(paths)]


def check_cli_version(root: Path, version: str) -> None:
    """Raises when this CLI is outside the range pinned by the checkout."""
    pin_file = root / VERSION_FILE
    if not pin_file.is_file():
        return
    raw = pin_file.read_text().strip()
    try:
        supported = SpecifierSet(raw)
    except InvalidSpecifier as error:
        raise IncompatibleVersion(f"{VERSION_FILE} is not a valid range: {raw!r}") from error
    if Version(version) not in supported:
        raise IncompatibleVersion(
            f"alinha-cli {version} does not support this checkout, which requires {raw}.\n"
            f"Update with: {INSTALL_COMMAND}"
        )
