"""Starts dependencies and supervises the backend and frontend processes."""

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from alinha_cli.project import BAO_DEV_TOKEN, COMPOSE_FILE

POLL_SECONDS = 0.1
STOP_TIMEOUT_SECONDS = 10
DRAIN_TIMEOUT_SECONDS = 2
EXIT_INTERRUPTED = 130
EXIT_NOT_FOUND = 127

Emit = Callable[[str], None]


class DependencyError(Exception):
    pass


@dataclass(frozen=True)
class Proc:
    name: str
    command: list[str]
    cwd: Path
    env: Mapping[str, str] = field(default_factory=dict)


def _read_services(root: Path) -> dict:
    try:
        data = yaml.safe_load((root / COMPOSE_FILE).read_text())
    except (OSError, yaml.YAMLError) as error:
        raise DependencyError(f"Could not read services from {COMPOSE_FILE}: {error}") from error
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        raise DependencyError(f"{COMPOSE_FILE} has no services")
    return services


def _is_one_shot(spec: object) -> bool:
    return isinstance(spec, dict) and spec.get("restart") in ("no", False)


def partition_services(root: Path) -> tuple[list[str], list[str]]:
    """Splits compose services into long-running ones and one-shot initialisers."""
    services = _read_services(root)
    # Only an explicit `restart: "no"` marks a one-shot service. A missing key must stay
    # long-running: the dev services omit it, and `run --rm` on them would never return.
    # Unquoted `no` is parsed as False by YAML 1.1.
    one_shot = [name for name, spec in services.items() if _is_one_shot(spec)]
    return [name for name in services if name not in one_shot], one_shot


def _host_port(spec: object) -> int | None:
    """The host port of one compose `ports` entry, or None when it publishes none."""
    if isinstance(spec, dict):
        published = str(spec.get("published", ""))
        return int(published) if published.isdigit() else None
    if not isinstance(spec, str):
        return None  # a bare container port is published on a random host port
    # [ip:]host:container[/protocol]; rsplit keeps an IPv6 address such as [::1] intact.
    parts = spec.split("/")[0].rsplit(":", 2)
    host = parts[-2] if len(parts) >= 2 else ""
    return int(host) if host.isdigit() else None


def published_ports(root: Path) -> list[int]:
    """Host ports the compose services publish, in order of first appearance."""
    ports: list[int] = []
    for spec in _read_services(root).values():
        entries = spec.get("ports") if isinstance(spec, dict) else None
        for entry in entries or []:
            port = _host_port(entry)
            if port is not None and port not in ports:
                ports.append(port)
    return ports


def _compose(root: Path, *args: str) -> None:
    command = ["docker", "compose", "-f", str(root / COMPOSE_FILE), *args]
    try:
        subprocess.run(command, cwd=root, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise DependencyError(f"`{' '.join(command)}` failed: {error}") from error


def start_dependencies(root: Path) -> None:
    long_running, one_shot = partition_services(root)
    # `up` with no service names would start every service, the one-shots included.
    if long_running:
        _compose(root, "up", "-d", "--wait", *long_running)
    for service in one_shot:
        _compose(root, "run", "--rm", "--no-deps", service)


def stop_dependencies(root: Path) -> None:
    _compose(root, "down")


BFF_BUILD_FILE = Path("bff/build.gradle.kts")
# Fake sessions the shell offers under `pnpm dev` (VITE_DEV_ROLE), without the BFF.
DEV_ROLES = ("consultant", "responsible", "analyst", "anonymous")


def app_processes(root: Path, demo: bool = False) -> list[Proc]:
    """The backend, the BFF (when the checkout has one) and the frontend.

    In dev, Vite proxies `/api`, `/oauth2` and `/auth` to the BFF, so without it the
    login and every API call fail. `demo` turns on the backend's demo organizations.
    """
    api_env = {"BAO_TOKEN": BAO_DEV_TOKEN}
    if demo:
        api_env["SPRING_PROFILES_ACTIVE"] = "demo"
    procs = [Proc("api", ["./gradlew", ":app:bootRun"], root, api_env)]
    if (root / BFF_BUILD_FILE).is_file():
        procs.append(
            Proc("bff", ["./gradlew", ":bff:bootRun"], root, {"SPRING_PROFILES_ACTIVE": "dev"})
        )
    procs.append(Proc("web", ["pnpm", "dev"], root))
    return procs


def frontend_processes(root: Path, role: str) -> list[Proc]:
    """Only the frontend, with a fake session for `role`. Nothing else is needed."""
    if role not in DEV_ROLES:
        raise ValueError(f"unknown role {role!r}, expected one of {', '.join(DEV_ROLES)}")
    return [Proc("web", ["pnpm", "dev"], root, {"VITE_DEV_ROLE": role})]


def _pump(name: str, stream, emit: Emit) -> None:
    for line in stream:
        emit(f"[{name}] {line.rstrip()}")


def _signal_group(pid: int, signum: signal.Signals) -> None:
    try:
        os.killpg(pid, signum)
    except (ProcessLookupError, PermissionError):
        # PermissionError: on macOS a group left with only an unreaped zombie gives EPERM.
        pass


def _group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except (ProcessLookupError, PermissionError):
        # PermissionError: macOS gives EPERM for a group left with only an unreaped zombie.
        return False
    return True


def _stop(running: list[subprocess.Popen[str]]) -> None:
    """Stops each app's process group (e.g. `pnpm` and Vite).

    The `bootRun` JVM is not in the `gradlew` group: it runs under the Gradle daemon,
    which cancels the build when the `gradlew` client goes away.

    The timeout covers the whole group, not just the leader: `pnpm` can exit at once
    while Vite is still shutting down. SIGKILL goes only to groups that still have
    members after the timeout. An empty group's id can be reused by an unrelated
    process group, which must not be killed.
    """
    for process in running:
        _signal_group(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    for process in running:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
        while _group_exists(process.pid) and time.monotonic() < deadline:
            time.sleep(POLL_SECONDS)
        if _group_exists(process.pid):
            _signal_group(process.pid, signal.SIGKILL)
        process.wait()


def _start(proc: Proc) -> subprocess.Popen[str]:
    return subprocess.Popen(
        proc.command,
        cwd=proc.cwd,
        env={**os.environ, **proc.env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",  # one non-UTF-8 byte must not kill the pump and block the app
        bufsize=1,
        start_new_session=True,
    )


def _exit_status(returncode: int) -> int:
    """Popen gives -N for a process killed by signal N; the shell convention is 128+N."""
    return 128 - returncode if returncode < 0 else returncode


def _wait_for_first_exit(running: list[subprocess.Popen[str]]) -> int:
    while True:
        for process in running:
            if process.poll() is not None:
                return _exit_status(process.returncode)
        time.sleep(POLL_SECONDS)


def _drain(pumps: list[threading.Thread]) -> None:
    """Lets the pumps print what the apps wrote while stopping, within a bound."""
    deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
    for pump in pumps:
        pump.join(timeout=max(0, deadline - time.monotonic()))


class _Terminated(Exception):
    pass


def _raise_terminated(signum: int, _frame: object) -> None:
    raise _Terminated(signum)


STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


def supervise(procs: list[Proc], emit: Emit) -> int:
    """Runs all processes until one exits or Ctrl-C, then stops the rest."""
    lock = threading.Lock()

    def locked_emit(line: str) -> None:
        with lock:
            emit(line)

    running: list[subprocess.Popen[str]] = []
    pumps: list[threading.Thread] = []
    # The apps run in their own sessions, so they do not get the terminal's signals.
    # Turn SIGTERM/SIGHUP into an exception so that `finally` stops them.
    previous = {signum: signal.signal(signum, _raise_terminated) for signum in STOP_SIGNALS}
    try:
        for proc in procs:
            process = _start(proc)
            running.append(process)
            pump = threading.Thread(
                target=_pump, args=(proc.name, process.stdout, locked_emit), daemon=True
            )
            pump.start()
            pumps.append(pump)
        return _wait_for_first_exit(running)
    except OSError as error:
        # A missing or non-executable command (e.g. `./gradlew` without +x).
        emit(f"Could not start process: {error}")
        return EXIT_NOT_FOUND
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except _Terminated as terminated:
        return 128 + terminated.args[0]
    finally:
        # Ignore further signals while stopping: a second Ctrl-C or SIGTERM would otherwise
        # raise out of `_stop`, skip the SIGKILL and leave the apps running in their sessions.
        restore = {**previous, signal.SIGINT: signal.signal(signal.SIGINT, signal.SIG_IGN)}
        for signum in STOP_SIGNALS:
            signal.signal(signum, signal.SIG_IGN)
        try:
            _stop(running)
            _drain(pumps)
        finally:
            for signum, handler in restore.items():
                signal.signal(signum, handler)
