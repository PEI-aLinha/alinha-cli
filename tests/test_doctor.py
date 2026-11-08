import socket
from pathlib import Path
from subprocess import CompletedProcess

from alinha_cli.doctor import (
    FAIL,
    OK,
    WARN,
    check_git_setting,
    check_port,
    check_secrets,
    check_tool,
    parse_published_ports,
)


def runner(stdout: str = "", stderr: str = "", code: int = 0):
    def run(_cmd: list[str]) -> CompletedProcess[str]:
        return CompletedProcess([], code, stdout, stderr)

    return run


def test_java_version_is_read_from_stderr() -> None:
    run = runner(stderr='openjdk version "21.0.2" 2024-01-16')
    assert check_tool("JDK", ["java", "-version"], 21, run).level == OK


def test_wrong_major_version_fails_with_found_version() -> None:
    result = check_tool("Node", ["node", "--version"], 22, runner(stdout="v20.1.0"))
    assert result.level == FAIL
    assert "20" in result.detail


def test_missing_tool_fails() -> None:
    def run(_cmd: list[str]) -> CompletedProcess[str]:
        raise FileNotFoundError

    assert check_tool("pnpm", ["pnpm", "--version"], 10, run).level == FAIL


def test_git_setting_must_be_true(project: Path) -> None:
    assert check_git_setting("x", runner(stdout="true\n")).level == OK
    assert check_git_setting("x", runner(stdout="", code=1)).level == WARN


def test_busy_port_fails_unless_owned_by_compose() -> None:
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert check_port(port, frozenset()).level == FAIL
        assert check_port(port, frozenset({port})).level == OK


def test_free_port_is_ok() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert check_port(port, frozenset()).level == OK


def test_published_ports_parse_ndjson_and_array() -> None:
    line = '{"Publishers":[{"PublishedPort":5432},{"PublishedPort":0}]}'
    assert parse_published_ports(f"{line}\n{line}") == frozenset({5432})
    assert parse_published_ports(f"[{line}]") == frozenset({5432})
    assert parse_published_ports("") == frozenset()


def test_secrets_warn_when_openbao_is_down() -> None:
    def fetch(_url: str, _token: str) -> int:
        raise OSError("refused")

    assert check_secrets(fetch).level == WARN


def test_secrets_ok_on_200_and_fail_when_missing() -> None:
    assert check_secrets(lambda _u, _t: 200).level == OK
    assert check_secrets(lambda _u, _t: 404).level == FAIL
