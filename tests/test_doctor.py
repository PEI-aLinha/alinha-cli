import socket
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

from alinha_cli.doctor import (
    FAIL,
    OK,
    WARN,
    check_git_setting,
    check_port,
    check_secrets,
    check_tool,
    parse_published_ports,
    ports_to_check,
)


def runner(stdout: str = "", stderr: str = "", code: int = 0):
    def run(_cmd: list[str]) -> CompletedProcess[str]:
        return CompletedProcess([], code, stdout, stderr)

    return run


def test_java_version_is_read_from_stderr() -> None:
    run = runner(stderr='openjdk version "21.0.2" 2024-01-16')
    assert check_tool("JDK", ["java", "-version"], 21, run).level == OK


def test_ga_java_version_without_minor_is_read() -> None:
    run = runner(
        stderr='openjdk version "21" 2023-09-19\nOpenJDK 64-Bit Server VM (build 21+35-2513)'
    )
    result = check_tool("JDK", ["java", "-version"], 21, run)
    assert result.level == OK
    assert result.detail == "21"


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


@pytest.mark.parametrize("error", [FileNotFoundError(), TimeoutExpired(["git"], 15)])
def test_git_setting_fails_instead_of_crashing_when_git_cannot_run(error: Exception) -> None:
    def run(_cmd: list[str]) -> CompletedProcess[str]:
        raise error

    result = check_git_setting("x", run)
    assert result.level == FAIL
    assert "git" in result.detail


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


def test_secrets_are_not_checked_without_an_openbao_service() -> None:
    def fetch(_url: str, _token: str) -> int:
        raise AssertionError("must not contact OpenBao")

    check = check_secrets(fetch, declared=False)
    assert check.level == OK
    assert "no openbao service" in check.detail


def test_ports_come_from_the_compose_file_plus_the_apps(project: Path) -> None:
    (project / "deploy" / "docker-compose.dev.yml").write_text(
        """
services:
  postgres:
    ports: ["127.0.0.1:5432:5432"]
  mailpit:
    ports: ["127.0.0.1:1025:1025", "8025:8025/tcp", "[::1]:9000:9000"]
  random:
    ports: [5000, "6000"]
  long:
    ports:
      - target: 80
        published: "8081"
  plain:
    image: busybox
"""
    )
    assert ports_to_check(project) == [5432, 1025, 8025, 9000, 8081, 8080, 5173]


def test_ports_fall_back_to_the_apps_when_compose_is_unreadable(project: Path) -> None:
    (project / "deploy" / "docker-compose.dev.yml").write_text("services: [")
    assert ports_to_check(project) == [8080, 8081, 5173]
