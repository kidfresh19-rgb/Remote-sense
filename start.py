"""One-command launcher for the remote-sense local stack.

`python start.py` brings the backend up with `docker compose`, smoothing over the three frictions
this dev box has: the Docker CLI is installed but not always on PATH, `.env` may be missing, and a
throwaway `rs-testpg` test container can be holding host port 5432 that compose's own postgres
needs to publish. Flags: `--frontend` also starts the Vite analyst workspace, `-d/--detach` runs
the backend in the background, `--down` stops the stack, `--logs` follows logs, `--build` forces an
image rebuild.

This is a developer convenience, not part of the deployed app: production runs the same
`docker compose` (or the per-service containers) directly.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
FRONTEND_DIR = REPO_ROOT / "frontend"

# Docker Desktop installs the CLI here but does not reliably put it on PATH on Windows.
_WINDOWS_DOCKER_BIN = Path(r"C:\Program Files\Docker\Docker\resources\bin")

# The throwaway PostGIS container used for host-side pytest. It publishes host 5432, which
# collides with compose's own `postgres` service; compose's postgres supersedes it, so it is safe
# to drop before bringing the stack up.
_TEST_DB_CONTAINER = "rs-testpg"

# Services that run to completion and legitimately end in `exited`; everything else is long-running
# and should be `running`. Used to tell a healthy stack from the wreckage of an interrupted `up`.
_ONE_SHOT_SERVICES = frozenset({"migrate", "createbuckets"})


def _fail(message: str) -> None:
    print(f"start.py: {message}", file=sys.stderr)
    raise SystemExit(1)


def find_docker() -> str:
    """Resolve the docker executable, falling back to Docker Desktop's bin on Windows and putting
    it on PATH so `docker compose` and the credential helper resolve from child processes too."""
    docker = shutil.which("docker")
    if docker:
        return docker
    win_docker = _WINDOWS_DOCKER_BIN / "docker.exe"
    if sys.platform == "win32" and win_docker.exists():
        os.environ["PATH"] = f"{_WINDOWS_DOCKER_BIN}{os.pathsep}{os.environ.get('PATH', '')}"
        return str(win_docker)
    _fail("docker CLI not found. Install Docker Desktop or add it to PATH.")
    raise AssertionError  # unreachable; _fail raises


def ensure_env() -> None:
    """The compose app services read `env_file: .env`; seed it from the checked-in contract so a
    fresh clone comes up without a manual copy. The example already targets the compose hostnames
    (postgres/redis/minio), so it works as-is for the dev/mock stack."""
    if ENV_FILE.exists():
        return
    if not ENV_EXAMPLE.exists():
        _fail(".env is missing and .env.example was not found to seed it from.")
    shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
    print(f"Created {ENV_FILE.name} from {ENV_EXAMPLE.name} (edit it to add real credentials).")


def free_db_port(docker: str) -> None:
    """Drop the throwaway test DB container if it is holding host port 5432, which compose's own
    postgres needs. Any other publisher is left alone with a warning rather than force-removed."""
    result = subprocess.run(
        [docker, "ps", "--filter", "publish=5432", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    # Ignore compose's own postgres (named "<project>-postgres-N"); it is allowed to hold 5432.
    others = [n for n in result.stdout.split() if n and not n.endswith("postgres-1")]
    if _TEST_DB_CONTAINER in others:
        print(f"Removing {_TEST_DB_CONTAINER} to free host port 5432 for compose postgres.")
        subprocess.run([docker, "rm", "-f", _TEST_DB_CONTAINER], cwd=REPO_ROOT, check=False)
        others = [n for n in others if n != _TEST_DB_CONTAINER]
    if others:
        print(
            f"Warning: host port 5432 also appears held by {others}; "
            "compose postgres may fail to start.",
            file=sys.stderr,
        )


def _parse_compose_ps(stdout: str) -> list[dict]:
    """Parse `docker compose ps --format json`, which is a JSON array on some Compose versions and
    newline-delimited objects on others. Unparseable lines are skipped rather than fatal."""
    text = stdout.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        containers: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                containers.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return containers


def clear_stale_containers(docker: str) -> None:
    """Self-heal the wreckage of an interrupted or failed `up`. A previous run can leave containers
    in `created` state or a long-running service `exited` (e.g. postgres losing the init-phase
    shutdown race on Docker Desktop for Windows), which makes the next `up` fail with a name
    conflict. Detect that and clear it with `down --remove-orphans`; named volumes are kept, so the
    already-initialized database survives. A healthy stack (long-running services `running`,
    one-shots `exited`) is left untouched, so this is a no-op on a normal re-run."""
    result = subprocess.run(
        [docker, "compose", "ps", "-a", "--format", "json"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        return
    stale: list[str] = []
    for container in _parse_compose_ps(result.stdout):
        service = container.get("Service", "")
        state = (container.get("State") or "").lower()
        name = container.get("Name") or service
        if state in {"created", "dead", "restarting", "paused", "removing"}:
            stale.append(name)
        elif state == "exited" and service not in _ONE_SHOT_SERVICES:
            stale.append(name)
    if stale:
        print(f"Clearing stale containers from a previous run ({', '.join(stale)}) before start.")
        subprocess.run([docker, "compose", "down", "--remove-orphans"], cwd=REPO_ROOT, check=False)


def compose(docker: str, *args: str) -> int:
    """Run `docker compose <args>` from the repo root so it finds the compose file and `.env`."""
    return subprocess.run([docker, "compose", *args], cwd=REPO_ROOT, check=False).returncode


def start_frontend() -> int:
    """Run the Vite analyst workspace in the foreground, installing deps on first use."""
    npm = shutil.which("npm")
    if not npm:
        _fail("npm not found; install Node.js to run the frontend.")
    if not (FRONTEND_DIR / "node_modules").exists():
        print("Installing frontend dependencies (npm install)...")
        code = subprocess.run([npm, "install"], cwd=FRONTEND_DIR, check=False).returncode
        if code != 0:
            return code
    print("Starting the analyst workspace at http://localhost:5173 (Ctrl+C to stop).")
    return subprocess.run([npm, "run", "dev"], cwd=FRONTEND_DIR, check=False).returncode


def print_targets(*, frontend: bool) -> None:
    print("\nOnce healthy:")
    print("  API health    http://localhost:8000/healthz")
    print("  API docs      http://localhost:8000/docs")
    print("  Tiler health  http://localhost:8001/healthz")
    print("  MinIO console http://localhost:9001  (minioadmin / minioadmin)")
    if frontend:
        print("  Workspace     http://localhost:5173")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Start (or stop) the remote-sense local stack with one command."
    )
    parser.add_argument(
        "-d",
        "--detach",
        action="store_true",
        help="run the backend in the background instead of streaming its logs",
    )
    parser.add_argument(
        "--frontend",
        action="store_true",
        help="also start the Vite analyst workspace (implies a detached backend)",
    )
    parser.add_argument("--build", action="store_true", help="rebuild the app image before start")
    parser.add_argument("--down", action="store_true", help="stop the stack and remove containers")
    parser.add_argument("--logs", action="store_true", help="follow the running stack's logs")
    args = parser.parse_args(argv)

    docker = find_docker()

    if args.down:
        return compose(docker, "down")
    if args.logs:
        return compose(docker, "logs", "-f")

    ensure_env()
    clear_stale_containers(docker)
    free_db_port(docker)

    detach = args.detach or args.frontend
    print_targets(frontend=args.frontend)

    up_args = ["up"]
    if args.build:
        up_args.append("--build")
    if detach:
        up_args.append("-d")

    code = compose(docker, *up_args)
    if code != 0 or not args.frontend:
        return code
    return start_frontend()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
