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


def test_stop_signal_stops_the_apps(project: Path, tmp_path: Path) -> None:
    """SIGTERM/SIGHUP to `alinha dev` (e.g. closing the terminal) must not leave apps running."""
    import signal
    import subprocess

    pid_file = tmp_path / "pid"
    child = f"import os,time,pathlib;pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()));time.sleep(60)"
    runner = (
        "import sys\n"
        "from pathlib import Path\n"
        "from alinha_cli.dev import Proc, supervise\n"
        f"sys.exit(supervise([Proc('web', [sys.executable, '-c', {child!r}], Path({str(tmp_path)!r}))], print))"
    )
    for signum in (signal.SIGTERM, signal.SIGHUP):
        pid_file.unlink(missing_ok=True)
        src = str(Path(__file__).parents[1] / "src")
        supervisor = subprocess.Popen(
            [sys.executable, "-c", runner], env={**os.environ, "PYTHONPATH": src}
        )
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        supervisor.send_signal(signum)
        assert supervisor.wait(timeout=15) == 128 + signum
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not alive(pid)


def test_non_utf8_output_does_not_block(project: Path) -> None:
    lines: list[str] = []
    code = supervise(
        [Proc("api", py("import sys; sys.stdout.buffer.write(b'bad \\xff byte\\n')"), project)],
        lines.append,
    )
    assert code == 0
    assert any(line.startswith("[api] bad") for line in lines)


def test_signal_group_ignores_permission_error(monkeypatch) -> None:
    from alinha_cli import dev

    def deny(_pid: int, _sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(dev.os, "killpg", deny)
    dev._signal_group(1, dev.signal.SIGTERM)


class _ExitedProcess:
    pid = 4242

    def wait(self, timeout: float | None = None) -> int:
        return 0


def _record_killpg(monkeypatch, group_exists: bool) -> list[int]:
    from alinha_cli import dev

    sent: list[int] = []

    def killpg(_pid: int, sig: int) -> None:
        if sig == 0 and not group_exists:
            raise ProcessLookupError
        sent.append(sig)

    monkeypatch.setattr(dev.os, "killpg", killpg)
    return sent


def test_stop_skips_sigkill_when_group_is_gone(monkeypatch) -> None:
    from alinha_cli import dev

    sent = _record_killpg(monkeypatch, group_exists=False)
    dev._stop([_ExitedProcess()])
    assert dev.signal.SIGKILL not in sent
    assert dev.signal.SIGTERM in sent


def test_stop_kills_leftover_group_members(monkeypatch) -> None:
    from alinha_cli import dev

    sent = _record_killpg(monkeypatch, group_exists=True)
    dev._stop([_ExitedProcess()])
    assert sent[-1] == dev.signal.SIGKILL


def test_start_dependencies_runs_one_shot_services_after_the_rest(
    project: Path, monkeypatch
) -> None:
    from alinha_cli import dev

    calls: list[list[str]] = []
    monkeypatch.setattr(dev.subprocess, "run", lambda command, **_kw: calls.append(command))
    dev.start_dependencies(project)
    dev.stop_dependencies(project)
    assert [c[4:] for c in calls] == [
        ["up", "-d", "--wait", "postgres", "openbao"],
        ["run", "--rm", "--no-deps", "openbao-init"],
        ["down"],
    ]


def test_compose_failure_raises_dependency_error(project: Path, monkeypatch) -> None:
    import pytest

    from alinha_cli import dev

    def missing(*_a, **_kw):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(dev.subprocess, "run", missing)
    with pytest.raises(dev.DependencyError):
        dev.stop_dependencies(project)
