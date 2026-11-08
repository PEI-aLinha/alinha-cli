import subprocess
from pathlib import Path

import pytest

GIT_ENV = [
    "-c",
    "user.name=Test",
    "-c",
    "user.email=test@example.com",
    "-c",
    "protocol.file.allow=always",
    "-c",
    "init.defaultBranch=main",
]


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *GIT_ENV, *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-m", name)


COMPOSE = """
services:
  postgres:
    image: postgres:17
    ports: ["5432:5432"]
  openbao:
    image: openbao/openbao:2.4.1
  openbao-init:
    image: openbao/openbao:2.4.1
    restart: "no"
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Main repo with one submodule, whose origin is a local bare-like repo."""
    origin = tmp_path / "origin-plugin"
    origin.mkdir()
    git(origin, "init")
    commit(origin, "first")

    root = tmp_path / "alinha"
    root.mkdir()
    git(root, "init")
    (root / "deploy").mkdir()
    (root / "deploy" / "docker-compose.dev.yml").write_text(COMPOSE)
    git(root, "submodule", "add", str(origin), "plugins/feature/demo")
    git(root, "add", "-A")
    git(root, "commit", "-m", "init")
    return root
