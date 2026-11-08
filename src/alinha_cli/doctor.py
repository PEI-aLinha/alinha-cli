"""Checks that a machine and checkout are ready for aLinha development."""

import json
import re
import socket
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from alinha_cli.project import COMPOSE_FILE, read_plugins

OK, WARN, FAIL = "ok", "warn", "fail"
PORTS = (5432, 6379, 8180, 8200, 8080, 5173)
BAO_SECRET_URL = "http://localhost:8200/v1/secret/data/alinha/app"
BAO_DEV_TOKEN = "dev-app-token"
COMMAND_TIMEOUT_SECONDS = 15

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
    match = re.search(r"(\d+)\.\d+", result.stdout + result.stderr)
    found = int(match.group(1)) if match else None
    if found == major:
        return Check(name, OK, match.group(0))
    return Check(name, FAIL, f"found {found}, need version {major}")


def check_command(name: str, command: list[str], hint: str, level: str, run: Run = _run) -> Check:
    try:
        succeeded = run(command).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        succeeded = False
    return Check(name, OK) if succeeded else Check(name, level, hint)


def check_git_setting(key: str, run: Run = _run) -> Check:
    result = run(["git", "config", "--get", key])
    if result.stdout.strip() == "true":
        return Check(f"git {key}", OK)
    return Check(f"git {key}", WARN, f"run: git config --global {key} true")


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


def check_secrets(fetch: Fetch = _fetch) -> Check:
    name = "OpenBao dev secrets"
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


def _submodule_checks(root: Path) -> list[Check]:
    missing = [p.name for p in read_plugins(root) if not (p.path / ".git").exists()]
    if missing:
        hint = f"{', '.join(missing)}: run git submodule update --init"
        return [Check("submodules", FAIL, hint)]
    return [Check("submodules", OK)]


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
        *[check_port(port, owned) for port in PORTS],
        check_secrets(),
    ]
