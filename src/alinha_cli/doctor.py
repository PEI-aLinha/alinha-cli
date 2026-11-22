"""Checks that a machine and checkout are ready for aLinha development."""

import json
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from alinha_cli.dev import DependencyError, partition_services, published_ports
from alinha_cli.project import BAO_DEV_TOKEN, COMPOSE_FILE, read_plugins

OK, WARN, FAIL = "ok", "warn", "fail"
# Backend, BFF and Vite. The containers' ports are read from the compose file.
APP_PORTS = (8080, 8081, 5173)
BAO_SECRET_URL = "http://localhost:8200/v1/secret/data/alinha/app"
COMMAND_TIMEOUT_SECONDS = 15
# `java -version` quotes its version, and a GA release has no minor part ("21").
VERSION = re.compile(r'"(\d+)(?:\.\d+)*"|(\d+)\.\d+')

Run = Callable[[list[str]], subprocess.CompletedProcess[str]]
Fetch = Callable[[str, str], int]


@dataclass(frozen=True)
class Check:
    name: str
    level: str
    detail: str = ""


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS
    )


def check_tool(name: str, command: list[str], major: int, run: Run = _run) -> Check:
    try:
        result = run(command)
    except (OSError, subprocess.TimeoutExpired):
        return Check(name, FAIL, f"not found, need version {major}")
    match = VERSION.search(result.stdout + result.stderr)
    found = int(match.group(1) or match.group(2)) if match else None
    if found == major:
        return Check(name, OK, match.group(0).strip('"'))
    return Check(name, FAIL, f"found {found}, need version {major}")


def check_command(name: str, command: list[str], hint: str, level: str, run: Run = _run) -> Check:
    try:
        succeeded = run(command).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        succeeded = False
    return Check(name, OK) if succeeded else Check(name, level, hint)


def check_git_setting(key: str, run: Run = _run) -> Check:
    name = f"git {key}"
    try:
        result = run(["git", "config", "--get", key])
    except (OSError, subprocess.TimeoutExpired):
        return Check(name, FAIL, "could not run git")
    if result.stdout.strip() == "true":
        return Check(name, OK)
    return Check(name, WARN, f"run: git config --global {key} true")


def check_port(port: int, owned: frozenset[int]) -> Check:
    name = f"port {port}"
    if port in owned:
        return Check(name, OK, "used by the dev containers")
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return Check(name, FAIL, "already in use by another process")
    return Check(name, OK, "free")


def parse_published_ports(output: str) -> frozenset[int]:
    text = output.strip()
    if not text:
        return frozenset()
    containers = json.loads(text) if text.startswith("[") else map(json.loads, text.splitlines())
    return frozenset(
        publisher["PublishedPort"]
        for container in containers
        for publisher in container.get("Publishers") or []
        if publisher.get("PublishedPort")
    )


def _fetch(url: str, token: str) -> int:
    request = urllib.request.Request(url, headers={"X-Vault-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def check_secrets(fetch: Fetch = _fetch, declared: bool = True) -> Check:
    name = "OpenBao dev secrets"
    if not declared:
        return Check(name, OK, f"no openbao service in {COMPOSE_FILE}, nothing to check")
    try:
        status = fetch(BAO_SECRET_URL, BAO_DEV_TOKEN)
    except OSError:
        return Check(name, WARN, "OpenBao is not running, `alinha dev` starts and seeds it")
    if status == 200:
        return Check(name, OK)
    return Check(name, FAIL, f"HTTP {status}, secrets were lost: rerun `alinha dev`")


def _compose_ports(root: Path) -> frozenset[int]:
    try:
        result = _run(
            ["docker", "compose", "-f", str(root / COMPOSE_FILE), "ps", "--format", "json"]
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    return parse_published_ports(result.stdout) if result.returncode == 0 else frozenset()


def _declares_openbao(root: Path) -> bool:
    try:
        long_running, one_shot = partition_services(root)
    except DependencyError:
        return False
    return "openbao" in long_running + one_shot


THEME_DIR = Path("frontend/keycloak-theme")
THEME_BUILD = "pnpm --filter @alinha/keycloak-theme build-keycloak-theme"
# npm lockfiles are never committed (pnpm only); skip trees that are not the team's sources.
SKIPPED_DIRS = frozenset({".git", "node_modules", "build", "dist", ".gradle"})


def check_keycloak_theme(root: Path, run: Run = _run) -> Check:
    """The login theme jar that the dev Keycloak mounts. Without it Keycloak uses its own."""
    name = "Keycloak theme"
    if not (root / THEME_DIR).is_dir():
        return Check(name, OK, "this checkout has no login theme")
    if any((root / THEME_DIR / "dist_keycloak").glob("*.jar")):
        return Check(name, OK)
    maven = check_command("Maven", ["mvn", "--version"], "", WARN, run)
    if maven.level != OK:
        return Check(name, WARN, f"not built; install Maven, then run: {THEME_BUILD}")
    return Check(name, WARN, f"not built, Keycloak shows its own login pages; run: {THEME_BUILD}")


def find_npm_lockfiles(root: Path) -> list[Path]:
    found = []
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [d for d in subdirs if d not in SKIPPED_DIRS]
        if "package-lock.json" in files:
            found.append(Path(directory, "package-lock.json").relative_to(root))
    return sorted(found)


def check_npm_lockfiles(root: Path) -> Check:
    name = "npm lockfiles"
    found = find_npm_lockfiles(root)
    if not found:
        return Check(name, OK)
    paths = ", ".join(str(path) for path in found)
    return Check(name, WARN, f"{paths}: npm was used instead of pnpm, delete them")


def _submodule_checks(root: Path) -> list[Check]:
    missing = [p.name for p in read_plugins(root) if not (p.path / ".git").exists()]
    if missing:
        hint = f"{', '.join(missing)}: run git submodule update --init"
        return [Check("submodules", FAIL, hint)]
    return [Check("submodules", OK)]


def ports_to_check(root: Path) -> list[int]:
    try:
        compose = published_ports(root)
    except DependencyError:
        compose = []
    return [*compose, *(port for port in APP_PORTS if port not in compose)]


def run_doctor(root: Path) -> list[Check]:
    owned = _compose_ports(root)
    node_modules = (root / "node_modules").is_dir()
    return [
        check_tool("JDK", ["java", "-version"], 21),
        check_tool("Node", ["node", "--version"], 22),
        check_tool("pnpm", ["pnpm", "--version"], 10),
        check_command("Docker", ["docker", "info"], "start Docker", FAIL),
        check_command("GitHub CLI", ["gh", "auth", "status"], "run: gh auth login", WARN),
        check_git_setting("submodule.recurse"),
        Check("frontend deps", OK)
        if node_modules
        else Check("frontend deps", WARN, "run: pnpm install"),
        *_submodule_checks(root),
        check_keycloak_theme(root),
        check_npm_lockfiles(root),
        *[check_port(port, owned) for port in ports_to_check(root)],
        check_secrets(declared=_declares_openbao(root)),
    ]
