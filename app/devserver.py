"""Local-development helpers for `python run.py`: bring up Postgres and Redis alongside the API, apply migrations,
and run the API, the gateway and the web app together. Standard library only, so it works even when the app can't import.
"""

from __future__ import annotations

import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent

DEFAULT_DATABASE_URL = "postgresql+asyncpg://inventory:inventory_dev_password@localhost:5432/inventory"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


# ---- configuration ------------------------------------------------------------------------------------


def read_env(path: Path) -> dict[str, str]:
    """Parse a .env file. A missing file is just empty. Quotes are stripped; real environment variables are not applied."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def effective(name: str, env_file: Mapping[str, str], default: str | None = None) -> str | None:
    """A setting the way the app resolves it: a real environment variable wins over the .env file."""
    return os.environ.get(name) or env_file.get(name) or default


def hostport(url: str, default_port: int) -> tuple[str, int]:
    """Host and port from a connection string such as postgresql+asyncpg://u:p@localhost:5432/db."""
    parsed = urllib.parse.urlsplit(url.replace("postgresql+asyncpg:", "postgresql:", 1))
    host = parsed.hostname or "127.0.0.1"
    return ("127.0.0.1" if host == "localhost" else host), parsed.port or default_port


def is_local(host: str) -> bool:
    return host in LOCAL_HOSTS


def redact(url: str) -> str:
    """Hide a password so a connection string can go in a log line."""
    return re.sub(r"(//[^:@/]*:)[^@]*@", r"\g<1>***@", url)


def container_env(database_url: str, redis_url: str) -> dict[str, str]:
    """Settings for the Postgres and Redis containers, taken from the app's own settings so a container we create always matches
    the credentials, database name and ports the API will use."""
    db = urllib.parse.urlsplit(database_url.replace("postgresql+asyncpg:", "postgresql:", 1))
    _, db_port = hostport(database_url, 5432)
    _, redis_port = hostport(redis_url, 6379)
    return {
        "POSTGRES_USER": urllib.parse.unquote(db.username or "inventory"),
        "POSTGRES_PASSWORD": urllib.parse.unquote(db.password or "inventory_dev_password"),
        "POSTGRES_DB": (db.path or "/inventory").lstrip("/") or "inventory",
        "POSTGRES_PORT": str(db_port),
        "REDIS_PORT": str(redis_port),
    }


# ---- starting data services ----------------------------------------------------------------------------


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def docker_ready() -> bool:
    """True when the Docker CLI exists and its engine answers (Docker Desktop is running)."""
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def tolerate_any_output(stream) -> None:
    """Never let a character the console can't show (Vite prints arrows) crash the program: show a ? instead."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure:
        try:
            reconfigure(errors="replace")
        except (OSError, ValueError):
            pass


def can_print(text: str, stream=None) -> bool:
    """Whether the console's encoding can display `text` (old Windows code pages can't show check marks)."""
    encoding = getattr(stream or sys.stdout, "encoding", None) or "ascii"
    try:
        text.encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


class ServiceUnavailable(RuntimeError):
    """A required service could not be started or reached. The message says how to fix it."""


@dataclass
class ServiceResult:
    name: str
    status: str  # "already-running", "started" or "unavailable"
    detail: str = ""


def ensure_service(
    name: str,
    host: str,
    port: int,
    *,
    required: bool,
    is_open: Callable[[str, int], bool] = port_open,
    docker_ok: Callable[[], bool] = docker_ready,
    start_container: Callable[[str], None],
    sleep: Callable[[float], None],
    say: Callable[[str], None] = print,
    wait_seconds: int = 60,
) -> ServiceResult:
    """Make sure `name` accepts connections at host:port.

    Something already listening is left alone (a native Postgres, say), because starting a container beside it would
    fight over the port. Otherwise, if Docker is running, bring up the container and wait for it. A required service
    that can't be reached raises with a fix; an optional one is reported and skipped."""
    if is_open(host, port):
        say(f"{name}: already running at {host}:{port}")
        return ServiceResult(name, "already-running")

    problem: str
    if not is_local(host):
        problem = f"{name} is configured at {host}:{port}, which isn't this machine, so it can't be started from here"
    elif not docker_ok():
        problem = f"{name} isn't running and Docker isn't available to start it"
    else:
        say(f"{name}: starting with Docker...")
        try:
            start_container(name)
        except (subprocess.CalledProcessError, OSError) as err:
            problem = f"Docker couldn't start {name} ({_short(err)})"
        else:
            for _ in range(wait_seconds):
                if is_open(host, port):
                    say(f"{name}: ready at {host}:{port}")
                    return ServiceResult(name, "started")
                sleep(1)
            problem = f"the {name} container started but isn't answering at {host}:{port}"

    if required:
        raise ServiceUnavailable(f"{problem[0].upper()}{problem[1:]}.\n  Start {name} yourself, or open Docker Desktop and run this again.")
    say(f"{name}: {problem}. Carrying on without it.")
    return ServiceResult(name, "unavailable", problem)


def _short(err: BaseException) -> str:
    out = getattr(err, "stderr", None) or str(err)
    text = out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out)
    return text.strip().splitlines()[-1] if text.strip() else type(err).__name__


def container_spec(service: str, env: Mapping[str, str]) -> list[str]:
    """The `docker run` arguments for one local data service. Names and volumes are the ones earlier setups used, so a
    container or data volume you already have is picked up as it is. Nothing here is a file in the repo."""
    if service == "postgres":
        return [
            "--name", "inventory-postgres", "--restart", "unless-stopped",
            "-e", f"POSTGRES_USER={env['POSTGRES_USER']}", "-e", f"POSTGRES_PASSWORD={env['POSTGRES_PASSWORD']}", "-e", f"POSTGRES_DB={env['POSTGRES_DB']}",
            "-p", f"{env['POSTGRES_PORT']}:5432", "-v", "inventorymanagement_postgres_data:/var/lib/postgresql/data",
            "postgres:16-alpine",
        ]
    if service == "redis":
        return [
            "--name", "inventory-redis", "--restart", "unless-stopped",
            "-p", f"{env['REDIS_PORT']}:6379", "-v", "inventorymanagement_redis_data:/data",
            "redis:7-alpine", "redis-server", "--appendonly", "yes",
        ]
    if service == "mailpit":  # a local inbox: nothing sent to it leaves this machine
        return ["--name", "inventory-mailpit", "--restart", "unless-stopped", "-p", "1025:1025", "-p", "8025:8025", "axllent/mailpit:latest"]
    raise ValueError(f"unknown service {service!r}")


def make_starter(env_extra: Mapping[str, str]) -> Callable[[str], None]:
    """Returns a function that starts a service's container: `docker start` if it already exists, else `docker run`."""

    def up(service: str) -> None:
        args = container_spec(service, env_extra)
        name = args[1]
        exists = subprocess.run(["docker", "inspect", name], capture_output=True).returncode == 0
        cmd = ["docker", "start", name] if exists else ["docker", "run", "-d", *args]
        subprocess.run(cmd, check=True, capture_output=True)

    return up


def run_migrations(say: Callable[[str], None] = print) -> None:
    """Apply database migrations. Skipping this is the classic cause of "sign-in says temporarily unavailable"."""
    say("applying database migrations...")
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        raise ServiceUnavailable(f"Migrations failed:\n{result.stderr.strip() or result.stdout.strip()}\n  Usually this means DATABASE_URL in backend/.env is wrong.")


# ---- running everything together ----------------------------------------------------------------------

COLORS = {"api": 34, "gateway": 35, "web": 36}


def format_line(name: str, line: str, *, color: bool = False, width: int = 9) -> str:
    """One line of a child's output, tagged with which service said it."""
    tag = f"[{name}]".ljust(width)
    return f"\x1b[{COLORS.get(name, 37)}m{tag}\x1b[0m{line}" if color else f"{tag}{line}"


@dataclass
class Service:
    name: str
    command: Sequence[str] | str
    cwd: Path
    shell: bool = False
    env: Mapping[str, str] = field(default_factory=dict)


def service_plan(*, all_services: bool, api_port: int, gateway_port: str, project_dir: Path = PROJECT_DIR, backend_dir: Path = BACKEND_DIR) -> list[Service]:
    """What `run.py` starts: always the API; with --all also the gateway and the web app."""
    plan = [Service("api", [sys.executable, "-u", "-m", "uvicorn", "app.main:app", "--reload", "--port", str(api_port)], backend_dir)]
    if all_services:
        plan += [
            Service("gateway", "npm run dev", project_dir / "gateway", shell=True),
            Service("web", "npm run dev", project_dir / "frontend", shell=True, env={"VITE_API_URL": os.environ.get("VITE_API_URL", f"http://localhost:{gateway_port}/api/v1")}),
        ]
    return plan


def kill_tree(proc: subprocess.Popen) -> None:
    """Stop a process and everything it started (npm starts Node, which starts Vite)."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass


class Supervisor:
    """Runs services side by side with tagged output, and stops them all when any one exits or on Ctrl+C."""

    def __init__(self, services: Sequence[Service], out=None, color: bool | None = None) -> None:
        self.services = list(services)
        self.out = out or sys.stdout
        tolerate_any_output(self.out)
        self.color = self.out.isatty() if color is None else color
        self.procs: dict[str, subprocess.Popen] = {}
        self.events: queue.Queue[tuple[str, int]] = queue.Queue()

    def _pump(self, name: str, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                text = line.rstrip()
                if text:
                    self.out.write(format_line(name, text, color=self.color) + "\n")
                    self.out.flush()
        except Exception:  # noqa: BLE001  A display problem must never stop us noticing that the service exited.
            pass
        finally:
            self.events.put((name, proc.wait()))

    def start(self) -> None:
        for svc in self.services:
            proc = subprocess.Popen(
                svc.command if not svc.shell else str(svc.command),
                cwd=svc.cwd,
                env={**os.environ, "PYTHONUNBUFFERED": "1", **svc.env},
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=svc.shell,
                start_new_session=os.name != "nt",
            )
            self.procs[svc.name] = proc
            threading.Thread(target=self._pump, args=(svc.name, proc), daemon=True).start()

    def stop(self) -> None:
        for proc in self.procs.values():
            kill_tree(proc)

    def run(self) -> int:
        self.start()
        try:
            name, code = self.events.get()  # blocks until something exits
            self.out.write(f"\n{name} stopped (exit {code}). Stopping the rest.\n")
            return code or 1
        except KeyboardInterrupt:
            self.out.write("\nStopping…\n")
            return 0
        finally:
            self.stop()
