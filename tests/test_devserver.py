import io
import subprocess
from pathlib import Path

import pytest

from app import devserver as ds


# ---- reading configuration ------------------------------------------------------------------------------


def test_read_env_handles_comments_quotes_and_missing_files(tmp_path: Path):
    f = tmp_path / ".env"
    f.write_text('# note\n\nA=1\nB="two words"\nC=\'x\'\nD=a=b\nBROKEN\n', encoding="utf-8")
    assert ds.read_env(f) == {"A": "1", "B": "two words", "C": "x", "D": "a=b"}
    assert ds.read_env(tmp_path / "missing.env") == {}


def test_a_real_environment_variable_beats_the_env_file(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "from-environment")
    assert ds.effective("DATABASE_URL", {"DATABASE_URL": "from-file"}) == "from-environment"
    monkeypatch.delenv("DATABASE_URL")
    assert ds.effective("DATABASE_URL", {"DATABASE_URL": "from-file"}) == "from-file"
    assert ds.effective("DATABASE_URL", {}, "fallback") == "fallback"


@pytest.mark.parametrize(
    ("url", "default", "expected"),
    [
        ("postgresql+asyncpg://u:p@localhost:5432/db", 5432, ("127.0.0.1", 5432)),
        ("postgresql+asyncpg://u:p@db.internal/db", 5432, ("db.internal", 5432)),
        ("redis://localhost:6380/1", 6379, ("127.0.0.1", 6380)),
        ("redis://:secret@cache.example.com:6379/0", 6379, ("cache.example.com", 6379)),
        ("redis://localhost", 6379, ("127.0.0.1", 6379)),
    ],
)
def test_host_and_port_come_from_connection_strings(url, default, expected):
    assert ds.hostport(url, default) == expected


def test_passwords_never_reach_a_log_line():
    assert ds.redact("postgresql+asyncpg://inventory:s3cret@localhost:5432/inventory") == "postgresql+asyncpg://inventory:***@localhost:5432/inventory"
    assert ds.redact("redis://localhost:6379/0") == "redis://localhost:6379/0"


def test_a_container_we_start_matches_the_credentials_the_api_will_use():
    env = ds.container_env("postgresql+asyncpg://me:p%40ss@localhost:5433/mydb", "redis://localhost:6390/0")
    assert env == {"POSTGRES_USER": "me", "POSTGRES_PASSWORD": "p@ss", "POSTGRES_DB": "mydb", "POSTGRES_PORT": "5433", "REDIS_PORT": "6390"}


def test_defaults_describe_the_documented_local_setup():
    env = ds.container_env(ds.DEFAULT_DATABASE_URL, ds.DEFAULT_REDIS_URL)
    assert env["POSTGRES_DB"] == "inventory" and env["POSTGRES_PORT"] == "5432" and env["REDIS_PORT"] == "6379"


# ---- starting Postgres and Redis alongside the API ---------------------------------------------------------


class World:
    """A pretend machine: which ports answer, whether Docker works, and what we were asked to start."""

    def __init__(self, open_ports=(), docker=True, comes_up=True, start_error: Exception | None = None):
        self.open_ports = set(open_ports)
        self.docker = docker
        self.comes_up = comes_up
        self.start_error = start_error
        self.started: list[str] = []
        self.said: list[str] = []
        self.sleeps = 0

    def is_open(self, host, port):
        return port in self.open_ports

    def docker_ok(self):
        return self.docker

    def start_container(self, name):
        if self.start_error:
            raise self.start_error
        self.started.append(name)
        if self.comes_up:
            self.open_ports.add({"postgres": 5432, "redis": 6379}[name])

    def sleep(self, _):
        self.sleeps += 1

    def ensure(self, name, port, *, required, host="127.0.0.1", wait_seconds=5):
        return ds.ensure_service(
            name, host, port, required=required, is_open=self.is_open, docker_ok=self.docker_ok,
            start_container=self.start_container, sleep=self.sleep, say=self.said.append, wait_seconds=wait_seconds,
        )


def test_a_service_that_is_already_running_is_left_alone():
    w = World(open_ports={5432})
    r = w.ensure("postgres", 5432, required=True)
    assert r.status == "already-running"
    assert w.started == []  # a container beside a native Postgres would fight over the port


def test_a_stopped_service_is_started_with_docker_and_waited_for():
    w = World(open_ports=set())
    r = w.ensure("postgres", 5432, required=True)
    assert r.status == "started" and w.started == ["postgres"]
    assert any("ready" in line for line in w.said)


def test_it_waits_for_a_slow_container_then_gives_up():
    w = World(open_ports=set(), comes_up=False)
    with pytest.raises(ds.ServiceUnavailable, match="isn't answering"):
        w.ensure("postgres", 5432, required=True, wait_seconds=3)
    assert w.sleeps == 3


def test_postgres_is_required_so_a_missing_one_stops_startup_with_a_fix():
    w = World(open_ports=set(), docker=False)
    with pytest.raises(ds.ServiceUnavailable) as err:
        w.ensure("postgres", 5432, required=True)
    assert "Docker isn't available" in str(err.value)
    assert "Start postgres yourself" in str(err.value)
    assert w.started == []


def test_redis_is_optional_so_a_missing_one_only_warns():
    w = World(open_ports=set(), docker=False)
    r = w.ensure("redis", 6379, required=False)
    assert r.status == "unavailable"
    assert any("Carrying on without it" in line for line in w.said)


def test_a_docker_failure_is_explained_not_dumped():
    boom = subprocess.CalledProcessError(1, "docker", stderr=b"Bind for 0.0.0.0:5432 failed: port is already allocated\n")
    w = World(open_ports=set(), start_error=boom)
    with pytest.raises(ds.ServiceUnavailable, match="port is already allocated"):
        w.ensure("postgres", 5432, required=True)


def test_a_database_on_another_machine_is_checked_not_started():
    w = World(open_ports=set())
    with pytest.raises(ds.ServiceUnavailable, match="isn't this machine"):
        w.ensure("postgres", 5432, required=True, host="db.internal")
    assert w.started == []


def test_each_data_service_is_one_docker_run_with_the_names_and_volumes_earlier_setups_used():
    env = ds.container_env(ds.DEFAULT_DATABASE_URL, ds.DEFAULT_REDIS_URL)
    pg = ds.container_spec("postgres", env)
    assert pg[:2] == ["--name", "inventory-postgres"] and "5432:5432" in pg and "POSTGRES_DB=inventory" in pg
    assert "inventorymanagement_postgres_data:/var/lib/postgresql/data" in pg and pg[-1] == "postgres:16-alpine"
    assert "6379:6379" in ds.container_spec("redis", env)
    with pytest.raises(ValueError):
        ds.container_spec("nope", env)


def test_the_repo_carries_no_docker_files():
    banned = ("Dockerfile", ".dockerignore", "docker-compose.yml", "docker-compose.prod.yml")
    for folder in ("frontend", "gateway", "backend"):
        for name in banned:
            assert not (ds.PROJECT_DIR / folder / name).exists(), f"{folder}/{name}: the deploy team writes these"


# ---- running everything together -----------------------------------------------------------------------------


def test_the_project_has_three_services_and_the_launcher_knows_where_they_are():
    assert ds.BACKEND_DIR.name == "backend"
    for folder in ("frontend", "gateway", "backend"):
        assert (ds.PROJECT_DIR / folder).is_dir()


def test_run_py_starts_only_the_api_by_default():
    plan = ds.service_plan(all_services=False, api_port=8000, gateway_port="4000")
    assert [s.name for s in plan] == ["api"]
    assert plan[0].cwd == ds.BACKEND_DIR
    assert "uvicorn" in plan[0].command and "--reload" in plan[0].command


def test_all_starts_the_gateway_and_the_web_app_pointed_at_the_gateway():
    plan = ds.service_plan(all_services=True, api_port=8000, gateway_port="4100")
    assert [s.name for s in plan] == ["api", "gateway", "web"]
    gateway, web = plan[1], plan[2]
    assert gateway.cwd == ds.PROJECT_DIR / "gateway" and gateway.shell
    assert web.cwd == ds.PROJECT_DIR / "frontend"
    assert web.env["VITE_API_URL"] == "http://localhost:4100/api/v1"


def test_output_lines_say_which_service_spoke():
    assert ds.format_line("api", "started") == "[api]    started"
    assert "\x1b[35m" in ds.format_line("gateway", "hello", color=True)
    assert ds.format_line("web", "x").startswith("[web]")


def test_the_supervisor_runs_services_tags_their_output_and_reports_the_first_to_exit():
    out = io.StringIO()
    py = ["python", "-c"]
    services = [
        ds.Service("api", [*py, "print('hello from api')"], Path.cwd()),
        ds.Service("web", [*py, "import time; time.sleep(30)"], Path.cwd()),
    ]
    code = ds.Supervisor(services, out=out, color=False).run()
    text = out.getvalue()
    assert "[api]    hello from api" in text
    assert "api stopped (exit 0)" in text
    assert code == 1  # a service exiting by itself is a failure, and the others are stopped (no 30 s wait)


# ---- awkward console encodings (the Windows crash this was written for) ---------------------------------------


def _cp1252_stream() -> tuple[io.TextIOWrapper, io.BytesIO]:
    raw = io.BytesIO()
    return io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline=""), raw


def test_output_a_console_cannot_show_is_replaced_not_fatal():
    stream, raw = _cp1252_stream()
    ds.tolerate_any_output(stream)
    stream.write("Local: \u279c http://localhost:5173\n")  # Vite's arrow; cp1252 has no such character
    stream.flush()
    assert raw.getvalue() == b"Local: ? http://localhost:5173\n"


def test_the_supervisor_keeps_reporting_when_a_service_prints_something_unprintable():
    stream, raw = _cp1252_stream()
    script = "import sys; sys.stdout.buffer.write(('arrow ' + chr(0x279C) + ' ready' + chr(10)).encode('utf-8'))"
    services = [ds.Service("api", ["python", "-c", script], Path.cwd())]
    code = ds.Supervisor(services, out=stream, color=False).run()
    stream.flush()
    text = raw.getvalue().decode("cp1252")
    assert "arrow ? ready" in text
    assert "api stopped" in text  # the exit was still noticed, so the rest would be stopped
    assert code == 1


def test_it_knows_whether_the_console_can_show_check_marks():
    class Narrow:
        encoding = "cp1252"

    class Wide:
        encoding = "utf-8"

    assert not ds.can_print("\u2714", Narrow())
    assert ds.can_print("\u2714", Wide())
    assert ds.can_print("plain", Narrow())
