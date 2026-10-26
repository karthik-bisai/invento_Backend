from pathlib import Path

from app import devdoctor as dd

PROJECT = Path("/project")
APP = "http://localhost:5173"
GW = "http://localhost:4000"
API = "http://localhost:8000"


class FakeProbe:
    """A pretend stack. Start from `healthy()` and break one thing per test."""

    def __init__(self):
        self.ports = {5432, 6379}
        self.files = {PROJECT / "backend/.env", PROJECT / "gateway/.env", PROJECT / "gateway/node_modules", PROJECT / "frontend/node_modules"}
        self.migrations: tuple[str | None, str | None] | None = ("0003", "0003")
        self.routes: dict[tuple[str, str], dd.HttpResult] = {}

    @classmethod
    def healthy(cls):
        p = cls()
        p.routes = {
            ("GET", f"{API}/api/v1/health"): dd.HttpResult(True, 200, {"status": "ok", "postgres": "ok", "redis": "ok"}),
            ("GET", f"{GW}/healthz"): dd.HttpResult(True, 200, {"status": "ok", "store": "ok", "upstream": "ok"}),
            ("GET", APP): dd.HttpResult(True, 200),
            ("POST", f"{API}/api/v1/internal/login/lookup"): dd.HttpResult(True, 200, {"status": "unknown"}),
            ("OPTIONS", f"{GW}/api/v1/auth/login"): dd.HttpResult(True, 204, headers={"access-control-allow-origin": APP, "access-control-allow-credentials": "true"}),
            ("POST", f"{GW}/api/v1/auth/login"): dd.HttpResult(True, 401, {"detail": "Incorrect email or password", "code": "invalid_credentials"}),
        }
        return p

    def port_open(self, host, port):
        return port in self.ports

    def http(self, method, url, headers=None, body=None, timeout=5):
        return self.routes.get((method, url), dd.HttpResult(False, error="connection refused"))

    def migration_state(self):
        return self.migrations

    def exists(self, path):
        return path in self.files


def settings(backend=None, gateway=None, frontend=None):
    return dd.Settings(backend or {}, gateway or {}, frontend or {}, PROJECT)


def verdict(checks, text):
    """The single check whose message contains `text`."""
    found = [c for c in checks if text in c.message]
    assert len(found) == 1, f"{text!r} matched {[c.message for c in found]}"
    return found[0]


def test_a_healthy_stack_has_no_problems():
    checks = dd.run_checks(settings(), FakeProbe.healthy())
    assert dd.summarize(checks) == (0, 0)
    assert verdict(checks, "sign-in works end to end").level == dd.PASS


def test_a_stack_with_nothing_running_lists_every_service_with_a_fix():
    p = FakeProbe()
    p.ports = set()
    checks = dd.run_checks(settings(), p)
    for text in ("PostgreSQL is not reachable", "API is not running", "gateway is not running", "web app is not running"):
        c = verdict(checks, text)
        assert c.level == dd.FAIL and c.fix
    assert verdict(checks, "Redis is not running").level == dd.WARN  # optional in development
    assert "run.py" in verdict(checks, "PostgreSQL is not reachable").fix


def test_redis_being_down_is_only_a_warning_and_explains_the_consequence():
    p = FakeProbe.healthy()
    p.ports = {5432}
    c = verdict(dd.run_checks(settings(), p), "Redis is not running")
    assert c.level == dd.WARN and "signed out whenever the gateway restarts" in c.message


def test_a_database_behind_the_code_is_the_headline_failure():
    p = FakeProbe.healthy()
    p.migrations = ("0002", "0003")
    c = verdict(dd.run_checks(settings(), p), "database is at migration 0002")
    assert c.level == dd.FAIL and "alembic upgrade head" in c.fix


def test_an_unreadable_migration_state_is_a_warning_not_a_crash():
    p = FakeProbe.healthy()
    p.migrations = None
    assert verdict(dd.run_checks(settings(), p), "could not read the migration state").level == dd.WARN


def test_different_internal_keys_are_caught_before_any_request():
    checks = dd.run_checks(settings(backend={"INTERNAL_API_KEY": "aaa"}, gateway={"INTERNAL_API_KEY": "bbb"}), FakeProbe.healthy())
    assert verdict(checks, "INTERNAL_API_KEY differs").level == dd.FAIL


def test_the_api_refusing_the_gateways_key_is_reported_as_such():
    p = FakeProbe.healthy()
    p.routes[("POST", f"{API}/api/v1/internal/login/lookup")] = dd.HttpResult(True, 403, {"detail": "Forbidden"})
    assert verdict(dd.run_checks(settings(), p), "rejected the gateway's internal key").level == dd.FAIL


def test_a_500_from_the_internal_route_points_at_the_missing_migration():
    p = FakeProbe.healthy()
    p.routes[("POST", f"{API}/api/v1/internal/login/lookup")] = dd.HttpResult(True, 500)
    assert "migration" in verdict(dd.run_checks(settings(), p), "internal route answered 500").message


def test_mismatched_media_urls_are_caught():
    checks = dd.run_checks(settings(backend={"MEDIA_PUBLIC_URL": "https://cdn.example.com/"}), FakeProbe.healthy())
    assert verdict(checks, "MEDIA_PUBLIC_URL differs").level == dd.FAIL
    same = dd.run_checks(settings(backend={"MEDIA_PUBLIC_URL": "http://localhost:4000/media"}), FakeProbe.healthy())
    assert verdict(same, "MEDIA_PUBLIC_URL matches").level == dd.PASS  # a trailing slash doesn't matter


def test_a_web_app_aimed_at_the_wrong_port_is_caught():
    checks = dd.run_checks(settings(frontend={"VITE_API_URL": "http://localhost:8000/api/v1"}), FakeProbe.healthy())
    c = verdict(checks, "web app calls")
    assert c.level == dd.FAIL and "VITE_API_URL=http://localhost:4000/api/v1" in c.fix


def test_a_remote_api_address_is_noted_not_failed():
    checks = dd.run_checks(settings(frontend={"VITE_API_URL": "https://api.example.com/v1"}), FakeProbe.healthy())
    assert verdict(checks, "web app calls").level == dd.INFO


def test_cors_must_allow_the_exact_address_in_the_browser():
    p = FakeProbe.healthy()
    p.routes[("OPTIONS", f"{GW}/api/v1/auth/login")] = dd.HttpResult(True, 204, headers={})
    c = verdict(dd.run_checks(settings(), p), "does not allow requests from")
    assert c.level == dd.FAIL and "APP_URL" in c.fix


def test_a_gateway_that_cannot_reach_the_api_says_so():
    p = FakeProbe.healthy()
    p.routes[("GET", f"{GW}/healthz")] = dd.HttpResult(True, 503, {"status": "degraded", "store": "ok", "upstream": "down"})
    c = verdict(dd.run_checks(settings(), p), "gateway is up but reports")
    assert c.level == dd.FAIL and "UPSTREAM_URL" in c.fix


def test_redis_dropping_out_mid_run_is_diagnosed_from_the_sign_in_probe():
    p = FakeProbe.healthy()
    p.routes[("POST", f"{GW}/api/v1/auth/login")] = dd.HttpResult(True, 503, {"detail": "Sign-in is temporarily unavailable.", "code": "session_store_unavailable"})
    c = verdict(dd.run_checks(settings(), p), "sign-in failed with 503")
    assert c.level == dd.FAIL and "Redis" in c.fix


def test_a_rate_limited_probe_counts_as_working():
    p = FakeProbe.healthy()
    p.routes[("POST", f"{GW}/api/v1/auth/login")] = dd.HttpResult(True, 429, {"detail": "Too many attempts."})
    assert verdict(dd.run_checks(settings(), p), "rate limited").level == dd.INFO


def test_placeholder_gmail_details_are_flagged_and_real_ones_are_accepted():
    placeholder = settings(gateway={"SMTP_PROVIDER": "gmail", "SMTP_USER": "your.address@gmail.com", "SMTP_PASS": "your-16-letter-app-password"})
    assert verdict(dd.run_checks(placeholder, FakeProbe.healthy()), "still looks like a placeholder").level == dd.WARN
    real = settings(gateway={"SMTP_PROVIDER": "gmail", "SMTP_USER": "team@gmail.com", "SMTP_PASS": "abcdwxyzabcdwxyz"})
    assert verdict(dd.run_checks(real, FakeProbe.healthy()), "email is configured").level == dd.PASS


def test_missing_node_dependencies_are_failures_with_the_command_to_run():
    p = FakeProbe.healthy()
    p.files.discard(PROJECT / "gateway/node_modules")
    c = verdict(dd.run_checks(settings(), p), "gateway dependencies are not installed")
    assert c.level == dd.FAIL and c.fix == "cd gateway; npm install"


def test_the_checks_read_the_gateways_own_port_and_addresses():
    gateway = {"PORT": "4100", "UPSTREAM_URL": "http://localhost:8100", "APP_URL": "http://localhost:5200"}
    p = FakeProbe()
    p.ports = {5432}
    p.routes = {
        ("GET", "http://localhost:8100/api/v1/health"): dd.HttpResult(True, 200, {"postgres": "ok", "redis": "down"}),
        ("GET", "http://localhost:4100/healthz"): dd.HttpResult(True, 200, {"store": "ok", "upstream": "ok"}),
        ("GET", "http://localhost:5200"): dd.HttpResult(True, 200),
    }
    checks = dd.run_checks(settings(gateway=gateway, frontend={"VITE_API_URL": "http://localhost:4100/api/v1"}), p)
    assert verdict(checks, "API is running").level == dd.PASS
    assert verdict(checks, "gateway is running (http://localhost:4100)").level == dd.PASS
    assert verdict(checks, "web app is running (http://localhost:5200)").level == dd.PASS
