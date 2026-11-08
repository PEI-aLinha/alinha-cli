# alinha-cli

Development tooling for the [aLinha](https://github.com/PEI-aLinha/alinha) platform. It gives the team one command to check a machine, start the local stack and see the state of every plugin submodule.

The CLI is a thin layer over tools the project already uses: `git`, `docker compose`, Gradle and pnpm. It has no configuration file. It reads services from `deploy/docker-compose.dev.yml` and plugins from `.gitmodules` in your checkout, so those files stay the only source of truth.

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Commands](#commands)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Releasing](#releasing)

## Requirements

To install the CLI:

- [uv](https://docs.astral.sh/uv/), which also provides Python 3.12 or newer if you do not have it
- Git, with SSH access to the `PEI-aLinha` GitHub organization

To develop aLinha, as checked by `alinha doctor`:

- JDK 21
- Node 22
- pnpm 10
- Docker with the Compose plugin
- A checkout of the main repo with its submodules:

```bash
git clone --recurse-submodules git@github.com:PEI-aLinha/alinha.git
```

## Installation

Install uv if you do not have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# or, on macOS
brew install uv
```

Install the CLI at a released version:

```bash
uv tool install git+ssh://git@github.com/PEI-aLinha/alinha-cli.git@v0.1.0
```

Check that it works:

```bash
alinha --version
```

If your shell cannot find `alinha`, add uv's tool directory to your `PATH` and open a new terminal:

```bash
uv tool update-shell
```

### Update

Install the new tag over the current one. The `--force` flag replaces the installed version:

```bash
uv tool install --force git+ssh://git@github.com/PEI-aLinha/alinha-cli.git@v0.2.0
```

### Uninstall

```bash
uv tool uninstall alinha-cli
```

## Quick start

Run these from anywhere inside your aLinha checkout, including inside a plugin:

```bash
alinha doctor          # check that the machine is ready
alinha plugins status  # see the state of each plugin
alinha dev             # start everything, Ctrl-C to stop the apps
alinha down            # stop the containers when you are done
```

After `alinha dev` starts, the backend is on <http://localhost:8080> and the frontend on <http://localhost:5173>.

## Commands

Every command accepts `--help`. Show the installed version with `alinha --version`.

| Command | Purpose |
|---|---|
| [`alinha doctor`](#alinha-doctor) | Check tools, git settings, submodules, ports and dev secrets |
| [`alinha plugins status`](#alinha-plugins-status) | Show branch, local changes and pointer drift for each plugin |
| [`alinha dev`](#alinha-dev) | Start dependencies, then run the backend and frontend |
| [`alinha down`](#alinha-down) | Stop the dependency containers and keep their data |

### `alinha doctor`

Checks whether this machine and checkout are ready for development. It changes nothing.

```bash
alinha doctor
```

| Check | Passes when | If it does not pass |
|---|---|---|
| JDK | `java -version` reports major version 21 | `fail` |
| Node | `node --version` reports major version 22 | `fail` |
| pnpm | `pnpm --version` reports major version 10 | `fail` |
| Docker | `docker info` succeeds | `fail` |
| GitHub CLI | `gh auth status` succeeds | `warn` |
| `git submodule.recurse` | The setting is `true` | `warn` |
| Frontend deps | `node_modules` exists at the repo root | `warn` |
| Submodules | Every plugin is initialized | `fail` |
| Ports 5432, 6379, 8180, 8200, 8080, 5173 | The port is free, or used by the dev containers | `fail` |
| OpenBao dev secrets | `secret/alinha/app` can be read with the dev token | `warn` if OpenBao is not running, `fail` if it runs without the secrets |

Each line is `ok`, `warn` or `fail`. The command exits with `1` if any check is `fail`. Warnings do not change the exit code.

### `alinha plugins status`

Shows one line per submodule in `.gitmodules`.

```bash
alinha plugins status
alinha plugins status --fetch
```

| Option | Effect |
|---|---|
| `--fetch` | Runs `git fetch origin main` in each plugin first. Without it, `MAIN` uses the last fetched `origin/main`. |

Example output:

```text
PLUGIN                         BRANCH                   STATE  POINTER  MAIN
feature/assessment             feat/roadmap             dirty  +1/-0    NOT on main
feature/organization           (detached)               clean  +0/-0    on main
```

| Column | Meaning |
|---|---|
| `PLUGIN` | Submodule path without the `plugins/` prefix |
| `BRANCH` | Current branch, or `(detached)` |
| `STATE` | `dirty` if the plugin has uncommitted or untracked changes, otherwise `clean` |
| `POINTER` | Commits ahead and behind the commit recorded by the main repo, as `+ahead/-behind`. `?` means that commit is not available locally. |
| `MAIN` | Whether the plugin's `HEAD` is contained in `origin/main`. CI rejects pointers that are not. |

A plugin that is ahead of the pointer and clean is why `git status` in the main repo lists it as modified. To record the new commit, update the pointer as described in the main repo's README.

A submodule that was never checked out shows `not initialized`.

### `alinha dev`

Starts the full local stack and streams backend and frontend logs in one terminal.

```bash
alinha dev
alinha dev --no-deps
```

| Option | Effect |
|---|---|
| `--no-deps` | Skips the containers and seeding. Use it when they are already running. |

The command runs these steps:

1. Starts every long-running service in the compose file with `docker compose up -d --wait`.
2. Runs each one-shot service, such as `openbao-init`, with `docker compose run --rm`. This restores secrets that the in-memory OpenBao lost after a restart.
3. Runs `./gradlew :app:bootRun` with `BAO_TOKEN=dev-app-token`, with output prefixed `[api]`.
4. Runs `pnpm dev`, which also regenerates the plugin list, with output prefixed `[web]`.

Press Ctrl-C to stop. If either app exits, the other is stopped too. Each app gets `SIGTERM` and then `SIGKILL` after 10 seconds. The containers keep running, so the next start is faster.

### `alinha down`

Stops and removes the dependency containers with `docker compose down`. Named volumes are kept, so the Postgres data survives. OpenBao runs in memory, and `alinha dev` seeds it again on the next start.

```bash
alinha down
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | A `doctor` check failed, a Docker command failed, or a `git fetch` failed |
| `2` | Not inside an aLinha checkout, or this CLI version is not supported by the checkout |
| `127` | `alinha dev` could not find a program to start |
| `130` | `alinha dev` was stopped with Ctrl-C |

If an app exits on its own, `alinha dev` returns that app's exit code.

## How it works

**Finding the project.** Each command walks up from the current directory until it finds both `.gitmodules` and `deploy/docker-compose.dev.yml`. This is why commands work inside a plugin.

**Version compatibility.** The main repo can pin the CLI versions it supports in a `.alinha-cli-version` file at its root, using a [version specifier](https://packaging.python.org/en/latest/specifications/version-specifiers/):

```text
>=0.1.0,<0.2.0
```

If the installed CLI is outside that range, every command that needs the checkout stops and prints how to update. If the file does not exist, no check is made.

**Long-running and one-shot services.** A compose service with `restart: "no"` is treated as one-shot. All other services are long-running. New services added to the compose file are picked up automatically.

**Readiness.** `docker compose up --wait` waits for a service's healthcheck when it has one. A service without a healthcheck counts as ready as soon as it has started.

**Process supervision.** `alinha dev` starts each app in its own process group and signals the whole group when stopping, so child processes stop with their parent.

### Code layout

| Module | Responsibility |
|---|---|
| `src/alinha_cli/cli.py` | Commands, output and exit codes |
| `src/alinha_cli/project.py` | Finding the checkout, reading plugins, checking the version pin |
| `src/alinha_cli/plugins.py` | Submodule status |
| `src/alinha_cli/doctor.py` | Environment checks |
| `src/alinha_cli/dev.py` | Compose calls and process supervision |

## Troubleshooting

**`... is not inside an aLinha checkout.`** Run the command from inside your clone of the main repo.

**`alinha-cli X does not support this checkout`.** Install a version inside the range shown, using the [update command](#update). If you recently changed branch, the range may belong to that branch.

**`port N: already in use by another process`.** Another program holds a port the stack needs. A local Postgres on 5432 is the most common case. On macOS and Linux, find it with:

```bash
lsof -nP -iTCP:5432 -sTCP:LISTEN
```

**`OpenBao dev secrets: HTTP 403` or `HTTP 404`.** OpenBao restarted and lost its in-memory data. Run `alinha dev` to seed it again.

**`alinha: command not found`.** Run `uv tool update-shell` and open a new terminal.

**`uv tool install` fails with a permission error.** Confirm that `ssh -T git@github.com` works and that your account can see the `PEI-aLinha` organization.

**`ModuleNotFoundError: alinha_cli` when using `uv run alinha` in a clone of this repo.** macOS can mark files in `.venv` as hidden in some folders, and Python then ignores the editable install. `uv run pytest` is not affected. To run the CLI from source, use:

```bash
PYTHONPATH=src uv run python -c "from alinha_cli.cli import app; app()" --help
```

## Development

```bash
git clone git@github.com:PEI-aLinha/alinha-cli.git
cd alinha-cli
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```

Tests create real temporary git repositories, so they need `git` but not Docker. CI runs lint and tests, and requires at least 70% coverage.

To try local changes as an installed tool, run this from the repo root:

```bash
uv tool install --force .
```

To add a command, create or extend a module in `src/alinha_cli/`, keep decisions in plain functions that tests can call, and register the command in `cli.py`.

## Releasing

1. Update `version` in `pyproject.toml` and `__version__` in `src/alinha_cli/__init__.py`.
2. Merge the change to `main`.
3. Tag and push the release:

   ```bash
   git switch main && git pull
   git tag v0.2.0
   git push origin v0.2.0
   ```

4. If the main repo needs the new version, update `.alinha-cli-version` there.
