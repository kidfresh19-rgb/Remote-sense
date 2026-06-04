"""One-command launcher for the remote-sense local stack.

`python start.py` brings the **whole system** up: the `docker compose` stack (postgres, redis and
minio as the external services, plus the api, worker, beat, tiler and nginx) and the Vite analyst
workspace. The compose stack does not include the frontend, so without this the UI at
http://localhost:5173 never comes up. It also smooths over the frictions this dev box has: the
Docker CLI is installed but not always on PATH, Docker Desktop may not be running yet, `.env` may
be missing, and a throwaway `rs-testpg` test container can be holding host port 5432 that compose's
own postgres needs to publish. The app images bake source in at build time with no bind-mount, so by
default this rebuilds them before start to pick up new code and migrations (skipping the rebuild is
the classic way to boot a stale image whose baked-in Alembic history lags the database).

Default run: the backend is brought up detached and the frontend dev server runs in the foreground,
so Ctrl+C stops the workspace while the backend keeps serving. Flags: `--no-frontend` brings up only
the docker stack (streaming its logs); `--no-build` skips the image rebuild; `-d/--detach` runs
everything (backend and frontend) in the background; `--down` stops everything, including a
backgrounded frontend; `--logs` follows the stack's logs.

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
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
FRONTEND_DIR = REPO_ROOT / "frontend"

# Records the PID of a detached (`-d`) Vite dev server so `--down` can stop it. The frontend is
# not a docker service, so compose cannot track it for us. Absent when the frontend runs in the
# foreground (it dies with this process) or was never started.
FRONTEND_PIDFILE = REPO_ROOT / ".frontend.pid"

# Docker Desktop installs the CLI here but does not reliably put it on PATH on Windows.
_WINDOWS_DOCKER_BIN = Path(r"C:\Program Files\Docker\Docker\resources\bin")

# Docker Desktop application executable — used to launch the daemon when it is not running.
_WINDOWS_DOCKER_DESKTOP = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")

# Node's Windows installer is the same story: npm.cmd lands here but is routinely off PATH.
_WINDOWS_NODE_BIN = Path(r"C:\Program Files\nodejs")

# The throwaway PostGIS container used for host-side pytest. It publishes host 5432, which
# collides with compose's own `postgres` service; compose's postgres supersedes it, so it is safe
# to drop before bringing the stack up.
_TEST_DB_CONTAINER = "rs-testpg"

# Services that run to completion and legitimately end in `exited`; everything else is long-running
# and should be `running`. Used to tell a healthy stack from the wreckage of an interrupted `up`.
_ONE_SHOT_SERVICES = frozenset({"migrate", "createbuckets"})

# How long to wait for the Docker daemon to become ready after launching Docker Desktop.
_DOCKER_WAIT_SECONDS = 120
_DOCKER_POLL_INTERVAL = 3


def _fail(message: str) -> None:
    print(f"start.py: {message}", file=sys.stderr)
    raise SystemExit(1)


def _docker_daemon_ready(docker: str) -> bool:
    """Return True if the Docker daemon is accepting connections."""
    result = subprocess.run(
        [docker, "info"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _ensure_docker_running(docker: str) -> None:
    """If the daemon is not up yet, launch Docker Desktop and wait for it to become ready.
    On non-Windows platforms this is a no-op (the daemon is managed by the OS service)."""
    if _docker_daemon_ready(docker):
        return

    if sys.platform != "win32":
        _fail("Docker daemon is not running. Start it with: sudo systemctl start docker")

    if not _WINDOWS_DOCKER_DESKTOP.exists():
        _fail(
            "Docker daemon is not running and Docker Desktop was not found at "
            f"{_WINDOWS_DOCKER_DESKTOP}. Start Docker Desktop manually or reinstall it."
        )

    print("Docker Desktop is not running — launching it now (this takes ~30 s on first start)...")
    subprocess.Popen(
        [str(_WINDOWS_DOCKER_DESKTOP)],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    deadline = time.monotonic() + _DOCKER_WAIT_SECONDS
    dots = 0
    while time.monotonic() < deadline:
        time.sleep(_DOCKER_POLL_INTERVAL)
        dots += 1
        print(f"\r  waiting for Docker daemon{'.' * (dots % 4):<4}", end="", flush=True)
        if _docker_daemon_ready(docker):
            print("\r  Docker daemon is ready.          ")
            return

    print()
    _fail(
        f"Docker daemon did not become ready within {_DOCKER_WAIT_SECONDS} s. "
        "Check Docker Desktop for errors, then re-run start.py."
    )


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
    _fail(
        "Docker CLI not found. Install Docker Desktop from https://www.docker.com/products/docker-desktop/"
    )
    raise AssertionError  # unreachable; _fail raises


def find_npm() -> str:
    """Resolve the npm executable, falling back to Node's default Windows install dir when it is
    off PATH (the same gap as the Docker CLI) and putting it on PATH so child npm/node processes
    resolve from here too."""
    npm = shutil.which("npm")
    if npm:
        return npm
    win_npm = _WINDOWS_NODE_BIN / "npm.cmd"
    if sys.platform == "win32" and win_npm.exists():
        os.environ["PATH"] = f"{_WINDOWS_NODE_BIN}{os.pathsep}{os.environ.get('PATH', '')}"
        return str(win_npm)
    _fail("npm not found; install Node.js from https://nodejs.org/ or add it to PATH.")
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


def _ensure_frontend_deps(npm: str) -> int:
    """Install node_modules on first use. Returns the install exit code (0 if already present)."""
    if (FRONTEND_DIR / "node_modules").exists():
        return 0
    print("Installing frontend dependencies (npm install)...")
    return subprocess.run([npm, "install"], cwd=FRONTEND_DIR, check=False).returncode


def _frontend_pid() -> int | None:
    """The PID recorded for a backgrounded frontend, or None if there is no valid pidfile. A
    malformed pidfile is treated as absent and removed."""
    if not FRONTEND_PIDFILE.exists():
        return None
    try:
        return int(FRONTEND_PIDFILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        FRONTEND_PIDFILE.unlink(missing_ok=True)
        return None


def _looks_like_frontend(pid: int) -> bool:
    """Best-effort check that `pid` is alive AND is our Vite/node dev server, so a recorded PID that
    has died and been reused by an unrelated process is never force-killed. Uses tasklist (Windows)
    / ps (POSIX); on any uncertainty it returns False (better to skip a kill than hit the wrong
    process)."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.lower()
            return str(pid) in out and any(n in out for n in ("node", "npm", "cmd.exe"))
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.lower()
        return any(token in out for token in ("vite", "node", "npm"))
    except OSError:
        return False


def start_frontend_foreground() -> int:
    """Run the Vite analyst workspace in the foreground, installing deps on first use. Ctrl+C
    stops it; the detached backend keeps running, so `--down` is how you stop everything. Any
    previously-backgrounded frontend is stopped first, so the two never fight over port 5173 and no
    stale pidfile is left behind."""
    npm = find_npm()
    code = _ensure_frontend_deps(npm)
    if code != 0:
        return code
    stop_frontend()
    print("Starting the analyst workspace at http://localhost:5173 (Ctrl+C to stop).")
    print("The backend stays up; run `python start.py --down` to stop the whole stack.")
    return subprocess.run([npm, "run", "dev"], cwd=FRONTEND_DIR, check=False).returncode


def start_frontend_detached() -> int:
    """Launch the Vite dev server in the background and record its PID so `--down` can stop it.
    Detaches from this console (its own process group) so it outlives the launcher. Stops any
    already-backgrounded frontend first, so a repeated `-d` never orphans the earlier one."""
    npm = find_npm()
    code = _ensure_frontend_deps(npm)
    if code != 0:
        return code
    stop_frontend()
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        proc = subprocess.Popen(
            [npm, "run", "dev"],
            cwd=FRONTEND_DIR,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        proc = subprocess.Popen(
            [npm, "run", "dev"],
            cwd=FRONTEND_DIR,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    FRONTEND_PIDFILE.write_text(str(proc.pid), encoding="utf-8")
    print("Started the analyst workspace in the background at http://localhost:5173.")
    return 0


def stop_frontend() -> None:
    """Stop a backgrounded Vite dev server recorded by `start_frontend_detached`. No-op if none was
    started, the process already exited, or the recorded PID was reused by another process (it is
    identity-checked before any kill). The npm shim spawns a node child, so the whole tree is
    killed: `taskkill /T` on Windows; on POSIX the process group, escalating SIGTERM to SIGKILL if
    it does not exit promptly. (A docker compose service would avoid this hand-rolled PID tracking;
    kept lightweight because the dev server is a developer convenience, not a deployed service.)"""
    pid = _frontend_pid()
    if pid is None:
        return
    if not _looks_like_frontend(pid):
        FRONTEND_PIDFILE.unlink(missing_ok=True)  # dead or recycled: drop the stale record
        return
    print(f"Stopping the analyst workspace (pid {pid}).")
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        import signal
        import time

        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            FRONTEND_PIDFILE.unlink(missing_ok=True)
            return
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        for _ in range(20):  # up to ~2s for a graceful exit, then SIGKILL the group
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except OSError:
                break
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except OSError:
                pass
    FRONTEND_PIDFILE.unlink(missing_ok=True)


def print_targets(*, frontend: bool) -> None:
    print("\nOnce healthy:")
    if frontend:
        print("  Workspace     http://localhost:5173   <- the analyst UI")
    print("  API health    http://localhost:8000/healthz")
    print("  API docs      http://localhost:8000/docs")
    print("  Tiler health  http://localhost:8000/tiler/healthz")
    print("  MinIO console http://localhost:9001  (minioadmin / minioadmin)")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Start (or stop) the remote-sense local stack with one command."
    )
    parser.add_argument(
        "-d",
        "--detach",
        action="store_true",
        help="run everything (backend and frontend) in the background and return immediately",
    )
    parser.add_argument(
        "--frontend",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="start the Vite analyst workspace (default: on; use --no-frontend for the stack only)",
    )
    parser.add_argument(
        "--build",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="rebuild the app image before start (default: on; use --no-build to skip)",
    )
    parser.add_argument(
        "--down",
        action="store_true",
        help="stop everything: remove the compose containers and any backgrounded frontend",
    )
    parser.add_argument("--logs", action="store_true", help="follow the running stack's logs")
    args = parser.parse_args(argv)

    docker = find_docker()
    _ensure_docker_running(docker)

    if args.down:
        stop_frontend()
        return compose(docker, "down")
    if args.logs:
        return compose(docker, "logs", "-f")

    ensure_env()
    clear_stale_containers(docker)
    free_db_port(docker)

    # The backend must run detached whenever we also start the frontend, otherwise the two fight
    # for the foreground. So the default (frontend on) detaches the backend; --no-frontend without
    # -d keeps the old behavior of streaming the stack's logs in the foreground.
    backend_detached = args.detach or args.frontend
    print_targets(frontend=args.frontend)

    up_args = ["up"]
    if args.build:
        up_args.append("--build")
    if backend_detached:
        up_args.append("-d")

    code = compose(docker, *up_args)
    if code != 0 or not args.frontend:
        return code

    # Backend is up (detached). Bring up the frontend: in the foreground by default so its logs
    # stream and Ctrl+C stops it, or in the background under -d for a fully detached stack.
    if args.detach:
        return start_frontend_detached()
    return start_frontend_foreground()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
