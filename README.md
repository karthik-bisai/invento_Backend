# Invento

Stock control across warehouses. Role-based access (Administrator, Inventory Manager, Warehouse Staff), email-verified sign-up with admin approval, sign-in with a password or an emailed code, and per-user profile and settings.

```
Browser ──cookie + CSRF──▶ Node gateway :4000 ──service token──▶ FastAPI :8000 ──▶ PostgreSQL
 (React app :5173)           │  sessions, email codes, rate limits,   (private; the system of record)
                             │  email, picture uploads
                             └──▶ Redis · SMTP (Gmail) · local disk / S3
```

| Part | Folder | Stack |
| --- | --- | --- |
| Web app | `frontend/` | Vite, React, TypeScript, Tailwind, TanStack Query |
| **Auth gateway** | `gateway/` | Node 20.12+, Fastify, TypeScript |
| API | `backend/` | Python 3.11+, FastAPI, SQLAlchemy 2 (async), Alembic |
| Data | started by `backend/run.py` | PostgreSQL 16, Redis 7, Mailpit (optional local inbox) |

The project is three separate repositories side by side: `frontend/`, `gateway/` and `backend/`. Nothing lives outside them, and none contains Docker files: containerising and deploying each one is the deployment team's job (see [Handing over for deployment](#handing-over-for-deployment)).

The browser only ever talks to the gateway, and holds an HTTP-only session cookie, never a token. FastAPI is private. See [ADR-001](docs/architecture/ADR-001-page-architecture.md) (screens), [ADR-002](docs/architecture/ADR-002-auth-gateway.md) (the gateway), [ADR-003](docs/architecture/ADR-003-email-codes-and-resilience.md) (email codes, Redis fallback, deployment) and the [diagram](docs/architecture/inventory-architecture.html).

## Quick start

You need Python 3.11+, Node 20.12+, and either Docker Desktop or your own Postgres (and, optionally, Redis).

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env                  # set DATABASE_URL if yours differs
cd ..\gateway;  npm install
cd ..\frontend; npm install
```

Start everything from the backend folder and open http://localhost:5173:

```powershell
cd backend
.venv\Scripts\python run.py --all
```

`run.py` is the backend's start-up. It starts **Postgres and Redis with the API**: if something is already listening on their ports (a native Postgres, say) it leaves it alone, otherwise it starts a Docker container using the credentials in `backend/.env`. It then applies the database migrations and runs the API. With `--all` it also runs the gateway and the web app, with colour-coded output; `Ctrl+C` stops them. The containers keep running between restarts; stop them with `docker stop inventory-postgres inventory-redis`.

| Command (from `backend/`) | Does |
| --- | --- |
| `python run.py` | Postgres + Redis, migrations, then only the API |
| `python run.py --all` | The same, plus the gateway and the web app |
| `python run.py --mail` | Also starts Mailpit, a local inbox at http://localhost:8025 |
| `python run.py --no-services` | Don't start Postgres or Redis (you run your own) |
| `python run.py --services-only` | Start Postgres and Redis, then exit |

The first time, create the administrator:

```powershell
$env:FIRST_ADMIN_EMAIL = "you@yourcompany.com"; $env:FIRST_ADMIN_PASSWORD = "a-long-passphrase"
.venv\Scripts\python -m app.scripts.seed
```

### If something doesn't work: `python doctor.py`

```powershell
cd backend
.venv\Scripts\python doctor.py
```

It checks that every piece is running **and connected to the others**, and prints the exact fix for anything that isn't: Postgres and Redis reachable, database migrations current, the API and gateway healthy, the shared `INTERNAL_API_KEY` and `MEDIA_PUBLIC_URL` matching, the web app pointed at the gateway, the gateway allowing the web app's address, and a real sign-in attempt travelling browser → gateway → API → database.

The two most common causes of "I can't sign in" are a database that hasn't had `alembic upgrade head` (every sign-in fails with "temporarily unavailable") and a gateway that can't reach the API. The doctor tells you which.

### Running the pieces separately

```powershell
cd backend;  .venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
cd gateway;  npm run dev
cd frontend; npm run dev
```

## Redis

Redis holds sign-in sessions, email codes and rate-limit counters (database `1` for the gateway, `0` for the API's cache). **It is optional in development.** If `REDIS_URL` is set but Redis isn't running, the gateway says so in its log and falls back to in-memory sessions, so you can still sign in; you're just signed out whenever the gateway restarts. In production the gateway refuses to start without Redis.

`python run.py` starts Redis for you when Docker Desktop is running. Otherwise install it natively on Windows (for example Memurai), or use WSL. To skip it entirely, remove `REDIS_URL` from `gateway/.env`.

## Signing in, signing up, and email

| Page | Route | What it does |
| --- | --- | --- |
| Sign in | `/login` | Email and password. Links to the two options below. |
| Sign in with a code | `/login/code` | We email a 6-digit code; typing it signs you in. No password needed. |
| Forgot password | `/forgot-password`, `/reset-password` | Email → 6-digit code → new password. Signs out every other device. |
| Create account | `/signup` → `/signup/verify` → `/signup/pending` | Email code to prove the address, then an animated "request sent" page while an admin decides. |

Codes last 10 minutes, work once, allow 5 wrong tries, can be re-sent every 60 seconds (5 per hour), and are stored only as a keyed hash. Asking for a code looks the same whether or not the address has an account, and a code is only ever sent to an account that can actually sign in.

New accounts are created as Warehouse Staff and can't sign in until an administrator approves them under **Users → Requests**. Admins are notified in the app and by email; the applicant is emailed the decision. To skip the approval step (not the email check), set `SIGNUP_AUTO_ACTIVATE=true` in `backend/.env`. Admin accounts are created with the seed script above, or by an admin under **Users → New user**.

### Gmail

1. Turn on **2-Step Verification** for the Gmail account.
2. Create an **app password**: Google Account → Security → 2-Step Verification → App passwords. (Your normal password won't work.)
3. In `gateway/.env`, uncomment and fill in:
   ```
   SMTP_PROVIDER=gmail
   SMTP_USER=your.address@gmail.com
   SMTP_PASS=abcd efgh ijkl mnop
   ```
   That sets the host, port and TLS for you, ignores the spaces in the password, and sends as that Gmail address. Optionally set `MAIL_FROM="Invento <your.address@gmail.com>"` for a display name.
4. Restart the gateway. It checks the login at startup and logs `SMTP ready` or a plain-English reason it failed.
5. Prove it works: `cd gateway; npm run mail:test -- you@example.com`

Gmail allows about 500 messages a day on a personal account, and only sends as the signed-in address. For more volume, use a transactional provider (set `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURE`, `SMTP_USER`, `SMTP_PASS` instead).

With no SMTP configured, emails are printed in the gateway terminal and saved in `gateway/.dev-mail/` (including the 6-digit code), so everything works locally without sending anything. For a browser inbox: `python run.py --mail` (from `backend/`), set `SMTP_HOST=localhost` and `SMTP_PORT=1025`, and open http://localhost:8025.

## Pages

| Route | Who | What |
| --- | --- | --- |
| `/` | Anyone | Landing page (signed-in people go to `/dashboard`) |
| `/login`, `/login/code`, `/signup`, `/signup/verify`, `/signup/pending`, `/forgot-password`, `/reset-password` | Signed out | Account pages above |
| `/terms`, `/privacy` | Anyone | Terms of use and privacy notice |
| `/dashboard`, `/inventory`, `/products` | All roles | Overview, stock per product and warehouse (with **Add stock**), catalogue |
| `/warehouses`, `/reports` | Admin, Inventory Manager | Locations and their stock; summary and CSV exports |
| `/users` | Admin | **People** and **Requests** tabs |
| `/profile`, `/settings` | All roles | Name, job title, phone, picture; theme, density, motion, notifications, password, signed-in devices |
| anything else | Anyone | A proper 404 page |

Profile pictures (PNG, JPEG or WebP, up to 2 MB, checked by content) are saved to `gateway/uploads/` locally. For deployment set `STORAGE_DRIVER=s3` with your bucket in `gateway/.env`, and make `MEDIA_PUBLIC_URL` match in `gateway/.env` and `backend/.env`. The role needs `s3:PutObject` and `s3:DeleteObject` on `avatars/*`; objects must be readable through your bucket policy or CDN. The S3 driver is tested against a fake client but has not been run against a real bucket.

## Configuration

| Setting | Where | Default |
| --- | --- | --- |
| `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `APP_NAME` | `backend/.env` | local Postgres/Redis, `change-me`, `Invento` |
| `FIRST_ADMIN_EMAIL`, `FIRST_ADMIN_PASSWORD` | `backend/.env` or environment | `admin@example.com`, `ChangeMe123!` |
| `INTERNAL_API_KEY` | `backend/.env` **and** `gateway/.env` (must match) | `dev-internal-key` |
| `MEDIA_PUBLIC_URL` | `backend/.env` **and** `gateway/.env` (must match) | `http://localhost:4000/media/` |
| `SIGNUP_AUTO_ACTIVATE`, `TRUST_PROXY_HEADERS` | `backend/.env` | `false`, `false` |
| `RATE_LIMIT_ENABLED`, `LOGIN_ATTEMPTS_PER_MINUTE[_PER_IP]`, `REGISTRATIONS_PER_HOUR` | `backend/.env` | `true`, `10`/`30`, `5` |
| Gateway: ports, `REDIS_URL`, sessions, codes, `SMTP_*`, storage | `gateway/.env` | see [gateway/.env.example](../gateway/.env.example) |
| Web app API address | `frontend/.env.local` as `VITE_API_URL` | `http://localhost:4000/api/v1` |

The gateway reads `gateway/.env` at startup, so restart it after editing. Real environment variables override the file.

## Security notes

- **Sessions** are server-side, in an HTTP-only SameSite=Lax cookie (`__Host-` prefixed and Secure in production). Signing out, changing a password, or an admin deactivating or resetting someone ends their sessions immediately. Idle expiry 7 days, absolute 30.
- **CSRF:** every state-changing request needs an `X-CSRF-Token` bound to the session, and foreign origins are refused.
- **Rate limiting** at both the gateway and the API, returning `429` with `Retry-After`. Counters fall back to per-process if Redis is down, rather than switching off.
- **Production:** with `ENVIRONMENT`/`NODE_ENV=production` the services refuse to start with default secrets, and the gateway also requires SMTP, Redis and Secure cookies. The API's interactive docs are switched off.
- **Passwords:** 8 characters minimum, 72 bytes maximum (a bcrypt limit; accented letters and emoji count extra).
- **Same site:** cookies are SameSite=Lax, so serve the app and the gateway from the same site. The production setup below puts them on one address.

## Handing over for deployment

Each folder is its own repository and is deployed as its own service. There are deliberately no Dockerfiles, Compose files or web-server configs in them: the deployment team writes those for AWS.

| Service | Build | Run | Listens on |
| --- | --- | --- | --- |
| `frontend` | `npm ci && npm run build` (static files in `dist/`) | any static host or CDN; set `VITE_API_URL` at **build** time to the gateway's public `/api/v1` address | n/a |
| `gateway` | `npm ci && npm run build` | `npm start` (Node 20.12+) | `PORT` (4000) |
| `backend` | `pip install -r requirements.txt` | `alembic upgrade head`, then `uvicorn app.main:app --host 0.0.0.0 --port 8000` | 8000, **private** |

What the deployed system needs:

- **PostgreSQL and Redis** as managed services (RDS and ElastiCache, for example). Redis is required in production; the gateway refuses to start without it.
- **One public address, HTTPS.** The session cookie is Secure-only. Serve the web app, and route `/api` and `/media` to the gateway, from the same site (a load balancer path rule, for example), so there is no CORS and cookies stay same-site. The API must not be reachable from the internet; only the gateway calls it.
- **First administrator:** run `python -m app.scripts.seed` once with `FIRST_ADMIN_EMAIL` and `FIRST_ADMIN_PASSWORD` set.
- **Configuration** is all environment variables. The services refuse to start in production with default secrets. See [.env.example](.env.example) and [gateway/.env.example](../gateway/.env.example). In production set at least:

| Variable | Service | Notes |
| --- | --- | --- |
| `ENVIRONMENT=production`, `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL` | backend | Unique secret, e.g. `openssl rand -hex 32` |
| `INTERNAL_API_KEY` | backend **and** gateway | Must match |
| `MEDIA_PUBLIC_URL` | backend **and** gateway | Must match; the API refuses other avatar URLs |
| `TRUST_PROXY_HEADERS=true` | backend | Behind the gateway |
| `NODE_ENV=production`, `HOST=0.0.0.0`, `UPSTREAM_URL`, `APP_URL`, `GATEWAY_SECRET`, `REDIS_URL` | gateway | `UPSTREAM_URL` is the API's private address; `APP_URL` is the public https address |
| `TRUST_PROXY=true` | gateway | Only behind a proxy or load balancer you control |
| `SMTP_PROVIDER=gmail`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM` | gateway | Required in production. Gmail needs an app password; see above |
| `STORAGE_DRIVER=s3`, `S3_BUCKET`, `S3_REGION` | gateway | Use the task or instance role; leave the keys out. The role needs `s3:PutObject` and `s3:DeleteObject` on `avatars/*` |

Before launch:

- [ ] Real, unique secrets (generate with `node -e "console.log(require('crypto').randomBytes(36).toString('base64url'))"`)
- [ ] HTTPS in place and `APP_URL` is the https address
- [ ] The gateway log shows `SMTP ready`, and a real sign-up email arrives
- [ ] The first admin signs in and changes the seeded password under Settings → Security
- [ ] Database backups on a schedule
- [ ] Your own review of the Terms and Privacy pages (they describe what the software does; add your organization's details and legal wording)
- [ ] The S3 driver has been tested against a fake client only: try a real bucket

## Tests

Each repository tests itself:

```powershell
cd backend;  .venv\Scripts\python -m pytest -q
cd gateway;  npm test; npm run typecheck
cd frontend; npm test; npm run lint; npm run build
```

Regenerate the icons (logo, favicon, app icons) with `npm run icons` in `frontend/`.

## Project structure

```
frontend/   Vite + React app (src: api, components, features, hooks, lib, types)
gateway/    Node auth gateway (src: routes, mail, storage, sessions, otp, store, notify)
backend/    FastAPI API (app: api/v1/endpoints, core, crud, models, schemas, services)
            run.py (start Postgres + Redis + API), doctor.py (check the connections), docs/architecture/ (ADRs, diagram)
```

## Troubleshooting

Run `python doctor.py` (from `backend/`) first. Then:

- **Sign-in says "temporarily unavailable":** most often the database is behind the code. `cd backend; .venv\Scripts\python -m alembic upgrade head`, then restart the API.
- **"Your access request is waiting for an administrator":** sign in as an admin and approve it under **Users → Requests**.
- **No email arrives:** with no SMTP set, look in the gateway terminal or `gateway/.dev-mail/`. With Gmail, run `npm run mail:test` and read the reason it prints (usually: use an app password).
- **You're signed out every time the gateway restarts:** Redis isn't running (see Redis above).
- **Avatar upload fails with a 422:** `MEDIA_PUBLIC_URL` differs between `backend/.env` and `gateway/.env`.
- **A port is in use:** change it in `gateway/.env` (`PORT`) or run Vite on another port and set `APP_URL` to match.
- **Script execution blocked when activating the venv:** `Set-ExecutionPolicy -Scope Process Bypass`.
- **Reset the local database:** `docker rm -f inventory-postgres`, `docker volume rm inventorymanagement_postgres_data`, then `python run.py` again.
