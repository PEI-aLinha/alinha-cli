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

from alinha_cli.project import COMPOSE_FILE

BAO_DEV_TOKEN = "dev-app-token"
POLL_SECONDS = 0.1
STOP_TIMEOUT_SECONDS = 10
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


def partition_services(root: Path) -> tuple[list[str], list[str]]:
    """Splits compose services into long-running ones and one-shot initialisers."""
    services = yaml.safe_load((root / COMPOSE_FILE).read_text())["services"]
    one_shot = [name for name, spec in services.items() if spec.get("restart") in ("no", False)]
    return [name for name in services if name not in one_shot], one_shot


def _compose(root: Path, *args: str) -> None:
    command = ["docker", "compose", "-f", str(root / COMPOSE_FILE), *args]
    try:
        subprocess.run(command, cwd=root, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise DependencyError(f"`{' '.join(command)}` failed: {error}") from error


def start_dependencies(root: Path) -> None:
    long_running, one_shot = partition_services(root)
    _compose(root, "up", "-d", "--wait", *long_running)
    for service in one_shot:
        _compose(root, "run", "--rm", "--no-deps", service)


def stop_dependencies(root: Path) -> None:
    _compose(root, "down")


def app_processes(root: Path) -> list[Proc]:
    return [
        Proc("api", ["./gradlew", ":app:bootRun"], root, {"BAO_TOKEN": BAO_DEV_TOKEN}),
        Proc("web", ["pnpm", "dev"], root),
    ]


def _pump(name: str, stream, emit: Emit) -> None:
    for line in stream:
        emit(f"[{name}] {line.rstrip()}")


def _signal_group(pid: int, signum: signal.Signals) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass


def _stop(running: list[subprocess.Popen[str]]) -> None:
    """Stops whole process groups, so children such as the Spring JVM do not survive."""
    for process in running:
        _signal_group(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    for process in running:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
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
        bufsize=1,
        start_new_session=True,
    )


def _wait_for_first_exit(running: list[subprocess.Popen[str]]) -> int:
    while True:
        for process in running:
            if process.poll() is not None:
                return process.returncode
        time.sleep(POLL_SECONDS)


def supervise(procs: list[Proc], emit: Emit) -> int:
    """Runs all processes until one exits or Ctrl-C, then stops the rest."""
    lock = threading.Lock()

    def locked_emit(line: str) -> None:
        with lock:
            emit(line)

    running: list[subprocess.Popen[str]] = []
    try:
        for proc in procs:
            process = _start(proc)
            running.append(process)
            threading.Thread(
                target=_pump, args=(proc.name, process.stdout, locked_emit), daemon=True
            ).start()
        return _wait_for_first_exit(running)
    except FileNotFoundError as error:
        emit(f"Could not start process: {error}")
        return EXIT_NOT_FOUND
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    finally:
        _stop(running)
