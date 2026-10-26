#!/usr/bin/env python
"""Start the Invento backend, with everything it needs.

    python run.py            Postgres + Redis (if not already running), migrations, then the API
    python run.py --all      the same, plus the gateway and the web app, all in this terminal

Postgres and Redis come up with the API: if they're already running (a native Postgres, say) they're left alone;
otherwise Docker starts them (docker run, no Docker files in the repo), using the credentials and ports in backend/.env.
Ctrl+C stops the API (and the gateway and web app with --all). The containers keep running so restarts are quick;
stop them with `docker stop inventory-postgres inventory-redis`.
"""

from __future__ import annotations

import argparse
import sys
import time

from app import devserver as ds


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Start Invento's backend with Postgres and Redis.")
    p.add_argument("--all", action="store_true", help="also start the gateway and the web app")
    p.add_argument("--mail", action="store_true", help="also start Mailpit, a local inbox at http://localhost:8025")
    p.add_argument("--no-services", action="store_true", help="don't start Postgres or Redis (use ones you already run)")
    p.add_argument("--no-migrate", action="store_true", help="don't apply database migrations")
    p.add_argument("--services-only", action="store_true", help="start Postgres and Redis, then exit")
    p.add_argument("--port", type=int, default=8000, help="API port (default 8000)")
    return p.parse_args(argv)


def say(message: str) -> None:
    print(f"invento  {message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ds.tolerate_any_output(sys.stdout)
    ds.tolerate_any_output(sys.stderr)
    args = parse(argv)
    env_file = ds.read_env(ds.BACKEND_DIR / ".env")
    gateway_env = ds.read_env(ds.PROJECT_DIR / "gateway" / ".env")
    database_url = ds.effective("DATABASE_URL", env_file, ds.DEFAULT_DATABASE_URL) or ds.DEFAULT_DATABASE_URL
    redis_url = ds.effective("REDIS_URL", env_file, ds.DEFAULT_REDIS_URL) or ds.DEFAULT_REDIS_URL

    try:
        if not args.no_services:
            start_container = ds.make_starter(ds.container_env(database_url, redis_url))
            db_host, db_port = ds.hostport(database_url, 5432)
            redis_host, redis_port = ds.hostport(redis_url, 6379)
            # Postgres is required. Redis only caches and holds sessions, and the gateway copes without it in development.
            ds.ensure_service("postgres", db_host, db_port, required=True, start_container=start_container, sleep=time.sleep, say=say)
            ds.ensure_service("redis", redis_host, redis_port, required=False, start_container=start_container, sleep=time.sleep, say=say)
            if args.mail and ds.docker_ready():
                start_container("mailpit")
                say("mailpit: inbox at http://localhost:8025 (SMTP on port 1025)")
        if args.services_only:
            return 0
        if not args.no_migrate:
            ds.run_migrations(say)
    except ds.ServiceUnavailable as err:
        print(f"\ninvento  {err}\n", file=sys.stderr)
        return 1

    plan = ds.service_plan(all_services=args.all, api_port=args.port, gateway_port=gateway_env.get("PORT", "4000"))
    say(("starting the API, gateway and web app" if args.all else "starting the API") + ". Press Ctrl+C to stop.")
    if args.all:
        say(f"open {gateway_env.get('APP_URL', 'http://localhost:5173')} in a moment, and run `python doctor.py` in another terminal if anything misbehaves")
    return ds.Supervisor(plan).run()


if __name__ == "__main__":
    sys.exit(main())
