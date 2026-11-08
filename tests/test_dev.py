import os
import sys
import time
from pathlib import Path

from alinha_cli.dev import Proc, app_processes, partition_services, supervise


def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_one_shot_services_are_separated(project: Path) -> None:
    long_running, one_shot = partition_services(project)
    assert long_running == ["postgres", "openbao"]
    assert one_shot == ["openbao-init"]


def test_backend_receives_the_dev_token(project: Path) -> None:
    backend, frontend = app_processes(project)
    assert backend.env["BAO_TOKEN"] == "dev-app-token"
    assert frontend.command == ["pnpm", "dev"]


def test_output_is_prefixed_per_process(project: Path) -> None:
    lines: list[str] = []
    code = supervise([Proc("api", py("print('hello')"), project)], lines.append)
    assert code == 0
    assert "[api] hello" in lines


def test_failure_stops_siblings_and_returns_its_code(project: Path, tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    sibling = (
        "import os,time,pathlib;"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()));"
        "time.sleep(60)"
    )
    # The failing process waits for the sibling to start, so the stop path is exercised.
    failing = (
        "import pathlib,time,sys\n"
        f"while not pathlib.Path({str(pid_file)!r}).exists(): time.sleep(0.05)\n"
        "sys.exit(3)"
    )
    code = supervise(
        [Proc("web", py(sibling), project), Proc("api", py(failing), project)], lambda _l: None
    )
    assert code == 3
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(pid)
