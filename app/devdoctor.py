"""The checks behind `python doctor.py`: is every part of Invento running, and are the parts connected to each other?

Standard library only. The checks talk to the world through a `Probe`, so tests can run them against pretend services.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from app import devserver as ds

PASS, INFO, WARN, FAIL = "pass", "info", "warn", "fail"


@dataclass
class Check:
    level: str
    message: str
    fix: str = ""


@dataclass
class HttpResult:
    ok: bool  # False when nothing answered at all
    status: int = 0
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    error: str = ""


class Probe(Protocol):
    def port_open(self, host: str, port: int) -> bool: ...
    def http(self, method: str, url: str, headers: dict[str, str] | None = None, body: Any = None, timeout: float = 5) -> HttpResult: ...
    def migration_state(self) -> tuple[str | None, str | None] | None: ...
    def exists(self, path: Path) -> bool: ...


class RealProbe:
    def port_open(self, host: str, port: int) -> bool:
        return ds.port_open(host, port)

    def http(self, method: str, url: str, headers: dict[str, str] | None = None, body: Any = None, timeout: float = 5) -> HttpResult:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"Accept": "application/json", **({"Content-Type": "application/json"} if data else {}), **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return self._result(res.status, res.read(), res.headers)
        except urllib.error.HTTPError as err:
            return self._result(err.code, err.read(), err.headers)
        except (urllib.error.URLError, OSError, TimeoutError) as err:
            return HttpResult(ok=False, error=str(getattr(err, "reason", err)))

    @staticmethod
    def _result(status: int, raw: bytes, headers: Any) -> HttpResult:
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        return HttpResult(ok=True, status=status, body=body, headers={k.lower(): v for k, v in headers.items()})

    def migration_state(self) -> tuple[str | None, str | None] | None:
        def revision(args: list[str]) -> str | None:
            out = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ds.BACKEND_DIR, capture_output=True, text=True, timeout=25)
            if out.returncode != 0:
                raise RuntimeError(out.stderr)
            lines = [line for line in out.stdout.splitlines() + out.stderr.splitlines() if not line.startswith("INFO")]
            match = re.search(r"^(\d{4})", "\n".join(lines), re.M)
            return match.group(1) if match else None

        try:
            return revision(["current"]), revision(["heads"])
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            return None

    def exists(self, path: Path) -> bool:
        return path.exists()


@dataclass
class Settings:
    """Everything the checks need to know about how the project is configured."""

    backend_env: dict[str, str]
    gateway_env: dict[str, str]
    frontend_env: dict[str, str]
    project_dir: Path = ds.PROJECT_DIR

    @classmethod
    def load(cls) -> Settings:
        p = ds.PROJECT_DIR
        return cls(ds.read_env(p / "backend" / ".env"), ds.read_env(p / "gateway" / ".env"), {**ds.read_env(p / "frontend" / ".env"), **ds.read_env(p / "frontend" / ".env.local")}, p)

    @property
    def api_url(self) -> str:
        return self.gateway_env.get("UPSTREAM_URL", "http://localhost:8000").rstrip("/")

    @property
    def gateway_port(self) -> str:
        return self.gateway_env.get("PORT", "4000")

    @property
    def gateway_url(self) -> str:
        return f"http://localhost:{self.gateway_port}"

    @property
    def app_url(self) -> str:
        return self.gateway_env.get("APP_URL", "http://localhost:5173").rstrip("/")


_PLACEHOLDER = re.compile(r"your[.-]|your-16|example", re.I)


def run_checks(cfg: Settings, probe: Probe) -> list[Check]:
    out: list[Check] = []
    add = lambda level, msg, fix="": out.append(Check(level, msg, fix))  # noqa: E731
    p = cfg.project_dir

    # ---- configuration ------------------------------------------------------------------------------
    if probe.exists(p / "backend" / ".env"):
        add(PASS, "backend/.env found")
    else:
        add(WARN, "backend/.env is missing, so the API uses built-in defaults", "copy backend/.env.example to backend/.env and set DATABASE_URL")
    add(PASS, "gateway/.env found") if probe.exists(p / "gateway" / ".env") else add(INFO, "gateway/.env not found: the gateway uses development defaults")
    for folder in ("gateway", "frontend"):
        if probe.exists(p / folder / "node_modules"):
            add(PASS, f"{folder} dependencies installed")
        else:
            add(FAIL, f"{folder} dependencies are not installed", f"cd {folder}; npm install")

    gateway_key = cfg.gateway_env.get("INTERNAL_API_KEY", "dev-internal-key")
    api_key = cfg.backend_env.get("INTERNAL_API_KEY", "dev-internal-key")
    if gateway_key == api_key:
        add(PASS, "INTERNAL_API_KEY matches in backend/.env and gateway/.env")
    else:
        add(FAIL, "INTERNAL_API_KEY differs between backend/.env and gateway/.env", "set the same value in both files, then restart both")

    media = lambda env: env.get("MEDIA_PUBLIC_URL", "http://localhost:4000/media/").rstrip("/") + "/"  # noqa: E731
    if media(cfg.gateway_env) == media(cfg.backend_env):
        add(PASS, "MEDIA_PUBLIC_URL matches (profile pictures will save)")
    else:
        add(FAIL, "MEDIA_PUBLIC_URL differs between backend/.env and gateway/.env, so avatar uploads will be refused", "set the same value in both files")

    vite_url = cfg.frontend_env.get("VITE_API_URL", "http://localhost:4000/api/v1")
    target = urlsplit(vite_url)
    if target.hostname not in ("localhost", "127.0.0.1"):
        add(INFO, f"web app calls {vite_url} (not local, so not checked)")
    elif str(target.port) == cfg.gateway_port:
        add(PASS, f"web app calls the gateway ({vite_url})")
    else:
        add(FAIL, f"web app calls {vite_url}, but the gateway is on port {cfg.gateway_port}", f"set VITE_API_URL=http://localhost:{cfg.gateway_port}/api/v1 in frontend/.env.local (or change PORT in gateway/.env)")

    if cfg.gateway_env.get("SMTP_PROVIDER") or cfg.gateway_env.get("SMTP_HOST"):
        creds = f"{cfg.gateway_env.get('SMTP_USER', '')} {cfg.gateway_env.get('SMTP_PASS', '')}"
        if _PLACEHOLDER.search(creds):
            add(WARN, "SMTP_USER or SMTP_PASS still looks like a placeholder", "put your real Gmail address and app password in gateway/.env")
        else:
            add(PASS, f"email is configured ({cfg.gateway_env.get('SMTP_PROVIDER') or cfg.gateway_env.get('SMTP_HOST')})")
    else:
        add(INFO, "email is not configured: codes are printed in the gateway terminal and saved in gateway/.dev-mail/")

    # ---- services -----------------------------------------------------------------------------------
    db_url = ds.effective("DATABASE_URL", cfg.backend_env, ds.DEFAULT_DATABASE_URL) or ds.DEFAULT_DATABASE_URL
    db_host, db_port = ds.hostport(db_url, 5432)
    if probe.port_open(db_host, db_port):
        add(PASS, f"PostgreSQL is accepting connections ({db_host}:{db_port})")
    else:
        add(FAIL, f"PostgreSQL is not reachable at {db_host}:{db_port}", "run `python run.py` (it starts Postgres with Docker), or start your own Postgres")

    redis_url = cfg.gateway_env.get("REDIS_URL") or ds.effective("REDIS_URL", cfg.backend_env, ds.DEFAULT_REDIS_URL) or ds.DEFAULT_REDIS_URL
    redis_host, redis_port = ds.hostport(redis_url, 6379)
    if probe.port_open(redis_host, redis_port):
        add(PASS, f"Redis is accepting connections ({redis_host}:{redis_port})")
    else:
        add(
            WARN,
            f"Redis is not running at {redis_host}:{redis_port} ({ds.redact(redis_url)}). Sign-in still works, but sessions live in memory so you are signed out whenever the gateway restarts, and the API skips its cache",
            "`python run.py` starts it with Docker (Docker Desktop must be running), or install Redis/Memurai natively",
        )

    api = probe.http("GET", f"{cfg.api_url}/api/v1/health")
    if not api.ok:
        add(FAIL, f"API is not running at {cfg.api_url}", "cd backend; .venv\\Scripts\\python run.py")
    elif api.status == 200 and isinstance(api.body, dict) and api.body.get("postgres") == "ok":
        add(PASS, f"API is running ({cfg.api_url}), database ok, redis {api.body.get('redis', 'unknown')}")
    else:
        add(FAIL, f"API answered {api.status} on /health: {json.dumps(api.body)}", "check the API terminal for the error, and that DATABASE_URL in backend/.env is right")

    state = probe.migration_state()
    if state is None:
        add(WARN, "could not read the migration state (is Postgres reachable?)")
    elif state[0] and state[0] == state[1]:
        add(PASS, f"database migrations are up to date ({state[0]})")
    else:
        add(FAIL, f"database is at migration {state[0] or 'none'} but the code expects {state[1]}. Every sign-in will fail until this is fixed", "cd backend; .venv\\Scripts\\python -m alembic upgrade head   (python run.py does this for you)")

    gateway = probe.http("GET", f"{cfg.gateway_url}/healthz")
    if not gateway.ok:
        add(FAIL, f"gateway is not running at {cfg.gateway_url}", "cd gateway; npm run dev   (or `python run.py --all`)")
    elif isinstance(gateway.body, dict) and gateway.body.get("upstream") == "ok" and gateway.body.get("store") == "ok":
        add(PASS, f"gateway is running ({cfg.gateway_url}) and can reach the API and its session store")
    else:
        fix = f"the gateway cannot reach the API at {cfg.api_url}: start the API or fix UPSTREAM_URL in gateway/.env" if isinstance(gateway.body, dict) and gateway.body.get("upstream") == "down" else "check the gateway terminal"
        add(FAIL, f"gateway is up but reports {json.dumps(gateway.body)}", fix)

    web = probe.http("GET", cfg.app_url)
    if web.ok and web.status < 500:
        add(PASS, f"web app is running ({cfg.app_url})")
    else:
        add(FAIL, f"web app is not running at {cfg.app_url}", "cd frontend; npm run dev   (or `python run.py --all`)")

    # ---- the wiring between them --------------------------------------------------------------------
    if api.ok:
        probe_res = probe.http("POST", f"{cfg.api_url}/api/v1/internal/login/lookup", {"X-Internal-Key": gateway_key}, {"email": "doctor-probe@example.com"})
        if probe_res.status == 200:
            add(PASS, "the API accepts the gateway's internal key")
        elif probe_res.status == 403:
            add(FAIL, "the API rejected the gateway's internal key", "INTERNAL_API_KEY must be identical in backend/.env and gateway/.env; restart both")
        else:
            add(FAIL, f"the API's internal route answered {probe_res.status} (a 500 here usually means the database is missing a migration)", "run the migration command above, then restart the API")

    if gateway.ok:
        cors = probe.http("OPTIONS", f"{cfg.gateway_url}/api/v1/auth/login", {"Origin": cfg.app_url, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-csrf-token"})
        if cors.headers.get("access-control-allow-origin") == cfg.app_url and cors.headers.get("access-control-allow-credentials") == "true":
            add(PASS, f"the gateway allows the web app ({cfg.app_url}) to call it with cookies")
        else:
            add(FAIL, f"the gateway does not allow requests from {cfg.app_url}", "set APP_URL (or CORS_ORIGINS) in gateway/.env to the exact address you open in the browser, then restart the gateway")

        login = probe.http("POST", f"{cfg.gateway_url}/api/v1/auth/login", {"Origin": cfg.app_url}, {"email": "doctor-probe@example.com", "password": "not-a-real-password"}, timeout=25)
        detail = login.body.get("detail", "") if isinstance(login.body, dict) else ""
        code = login.body.get("code") if isinstance(login.body, dict) else None
        if login.status == 401:
            add(PASS, 'sign-in works end to end (gateway > API > database answered "wrong password" for a made-up user)')
        elif login.status == 429:
            add(INFO, "sign-in is rate limited right now (that means it is working); try again in a minute")
        elif login.status in (502, 503):
            fix = "Redis dropped out: start it, or remove REDIS_URL from gateway/.env and restart the gateway" if code == "session_store_unavailable" else "the API is down or unreachable from the gateway; see above"
            add(FAIL, f"sign-in failed with {login.status}: {detail}", fix)
        else:
            add(FAIL, f"sign-in returned {login.status or login.error}: {json.dumps(login.body)}", "look at the gateway and API terminals for the error")
    return out


def summarize(checks: list[Check]) -> tuple[int, int]:
    return sum(c.level == FAIL for c in checks), sum(c.level == WARN for c in checks)
