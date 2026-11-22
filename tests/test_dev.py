import os
import sys
import time
from pathlib import Path

import pytest

from alinha_cli.dev import (
    DependencyError,
    Proc,
    app_processes,
    frontend_processes,
    partition_services,
    supervise,
)
from alinha_cli.project import COMPOSE_FILE


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


def test_service_without_body_is_long_running(project: Path) -> None:
    (project / COMPOSE_FILE).write_text("services:\n  postgres:\n  init:\n    restart: 'no'\n")
    assert partition_services(project) == (["postgres"], ["init"])


@pytest.mark.parametrize("content", ["name: x\n", "services: [\n", "services: []\n"])
def test_unreadable_compose_file_is_a_dependency_error(project: Path, content: str) -> None:
    (project / COMPOSE_FILE).write_text(content)
    with pytest.raises(DependencyError, match="services"):
        partition_services(project)


def test_output_printed_while_stopping_is_not_lost(project: Path, tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    lines: list[str] = []
    # Prints a burst on SIGTERM, as an app does when it logs its shutdown.
    stopping = (
        "import signal,sys,time,pathlib\n"
        "def bye(*_):\n"
        "    for i in range(2000): print(f'bye {i}')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, bye)\n"
        f"pathlib.Path({str(ready)!r}).write_text('1')\n"
        "time.sleep(60)"
    )
    failing = (
        "import pathlib,time,sys\n"
        f"while not pathlib.Path({str(ready)!r}).exists(): time.sleep(0.05)\n"
        "sys.exit(1)"
    )

    def slow_emit(line: str) -> None:  # a slow terminal: the pump lags behind the app
        time.sleep(0.0002)
        lines.append(line)

    supervise([Proc("web", py(stopping), project), Proc("api", py(failing), project)], slow_emit)
    assert "[web] bye 1999" in lines


def test_backend_receives_the_dev_token(project: Path) -> None:
    backend, frontend = app_processes(project)
    assert backend.env["BAO_TOKEN"] == "dev-app-token"
    assert frontend.command == ["pnpm", "dev"]


def test_demo_turns_on_the_demo_profile_only_for_the_backend(project: Path) -> None:
    backend, frontend = app_processes(project, demo=True)
    assert backend.env["SPRING_PROFILES_ACTIVE"] == "demo"
    assert "SPRING_PROFILES_ACTIVE" not in frontend.env
    assert "SPRING_PROFILES_ACTIVE" not in app_processes(project)[0].env


def test_role_runs_only_the_frontend_with_a_fake_session(project: Path) -> None:
    (web,) = frontend_processes(project, "consultant")
    assert web.command == ["pnpm", "dev"]
    assert web.env == {"VITE_DEV_ROLE": "consultant"}
    with pytest.raises(ValueError, match="responsible"):
        frontend_processes(project, "admin")


def test_bff_runs_with_the_dev_profile_when_the_checkout_has_one(project: Path) -> None:
    (project / "bff").mkdir()
    (project / "bff" / "build.gradle.kts").write_text("")
    names = [proc.name for proc in app_processes(project)]
    bff = app_processes(project)[1]
    assert names == ["api", "bff", "web"]
    assert bff.command == ["./gradlew", ":bff:bootRun"]
    assert bff.env["SPRING_PROFILES_ACTIVE"] == "dev"


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


def test_app_killed_by_signal_returns_128_plus_signal(project: Path) -> None:
    """A signal-killed app (e.g. OOM-killed JVM) must give the shell convention 128+N, not -N."""
    killed = "import os,signal;os.kill(os.getpid(), signal.SIGKILL)"
    code = supervise([Proc("api", py(killed), project)], lambda _l: None)
    assert code == 128 + 9


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


def test_second_signal_during_stop_does_not_orphan_the_apps(tmp_path: Path) -> None:
    """A second Ctrl-C/SIGTERM while stopping must not skip the SIGKILL of an app that ignores SIGTERM."""
    import signal
    import subprocess

    pid_file = tmp_path / "pid"
    child = (
        "import os,signal,time,pathlib;signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()));time.sleep(60)"
    )
    runner = (
        "import sys\n"
        "from pathlib import Path\n"
        "from alinha_cli import dev\n"
        "dev.STOP_TIMEOUT_SECONDS = 2\n"
        f"sys.exit(dev.supervise([dev.Proc('web', [sys.executable, '-c', {child!r}], Path({str(tmp_path)!r}))], print))"
    )
    src = str(Path(__file__).parents[1] / "src")
    supervisor = subprocess.Popen(
        [sys.executable, "-c", runner], env={**os.environ, "PYTHONPATH": src}
    )
    deadline = time.monotonic() + 10
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    pid = int(pid_file.read_text())
    supervisor.send_signal(signal.SIGTERM)
    time.sleep(0.5)  # now inside _stop, waiting out the SIGTERM timeout
    supervisor.send_signal(signal.SIGINT)
    supervisor.send_signal(signal.SIGTERM)
    supervisor.wait(timeout=15)
    deadline = time.monotonic() + 5
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    still_alive = alive(pid)
    if still_alive:
        os.kill(pid, signal.SIGKILL)
    assert not still_alive


def test_children_get_the_grace_period_when_the_leader_exits_first(
    project: Path, tmp_path: Path
) -> None:
    """Like pnpm and Vite: the leader dies on SIGTERM, its child needs a moment to shut down."""
    ready, done = tmp_path / "ready", tmp_path / "done"
    child = (
        "import signal,time,pathlib\n"
        "def bye(*_):\n"
        "    time.sleep(1)\n"
        f"    pathlib.Path({str(done)!r}).write_text('1')\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, bye)\n"
        f"pathlib.Path({str(ready)!r}).write_text('1')\n"
        "time.sleep(60)"
    )
    leader = (
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "time.sleep(60)"
    )
    failing = (
        "import pathlib,time,sys\n"
        f"while not pathlib.Path({str(ready)!r}).exists(): time.sleep(0.05)\n"
        "sys.exit(1)"
    )
    supervise(
        [Proc("web", py(leader), project), Proc("api", py(failing), project)], lambda _l: None
    )
    assert done.exists()


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

    monkeypatch.setattr(dev, "STOP_TIMEOUT_SECONDS", 0.2)  # the group never empties
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


def test_non_executable_command_is_reported_without_traceback(tmp_path: Path) -> None:
    script = tmp_path / "gradlew"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o644)
    lines: list[str] = []
    code = supervise([Proc("api", [str(script)], tmp_path)], lines.append)
    assert code == 127
    assert any("Could not start process" in line for line in lines)


def test_start_dependencies_skips_up_when_every_service_is_one_shot(
    project: Path, monkeypatch
) -> None:
    from alinha_cli import dev

    (project / COMPOSE_FILE).write_text("services:\n  init:\n    restart: 'no'\n")
    calls: list[list[str]] = []
    monkeypatch.setattr(dev.subprocess, "run", lambda command, **_kw: calls.append(command))
    dev.start_dependencies(project)
    assert [c[4:] for c in calls] == [["run", "--rm", "--no-deps", "init"]]
