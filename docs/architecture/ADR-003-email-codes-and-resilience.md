# ADR-003: Email codes, a gateway that survives Redis being down, and how Invento is deployed

**Status:** Accepted
**Date:** 2026-10-26
**Deciders:** Project owner

Builds on [ADR-002](ADR-002-auth-gateway.md). Screens and routes: [ADR-001](ADR-001-page-architecture.md). Diagram: [inventory-architecture.html](inventory-architecture.html).

## Context

Three things went wrong or were missing once the gateway existed:

1. **People couldn't sign in locally.** The gateway was configured for a Redis that wasn't running (Docker was off). It waited 17 seconds per request and then failed every sign-in with a 500. The API's `/health` also reported "down" without Redis, although the API works without it.
2. **There was no way back into an account** other than an admin resetting it, and the product asked for sign-in by code and real email delivery through Gmail.
3. **The product wasn't ready to ship:** the default Vite favicon, the working name "Inventory Management", no 404, terms or privacy pages, and no way to deploy it.

## Decision

### Sign in by code, and password reset

Both reuse the OTP service from sign-up, with a **purpose** on every code (`signup`, `login`, `reset`), so one kind of code can never be spent on another.

| Rule | Why |
| --- | --- |
| Asking for a code gives the same answer whether or not the address has an account | Nobody can use it to find out who is registered |
| A code is only sent to an account that can actually sign in (verified, approved, active) | Pending, declined and deactivated accounts can't use a side door |
| Verifying a code re-checks the account's state before starting a session | The account may have changed in the ten minutes since the code was sent |
| Codes live 10 minutes, work once, allow 5 wrong tries, resend after 60 s, 5 per hour; only a keyed hash is stored | Limits guessing and replay |
| Reset checks the new password **before** spending the code, then ends every session for that user and emails a notice | A typo doesn't cost the code, and a stolen session dies with the old password |

The gateway asks the API two new questions over the internal key: `POST /internal/login/lookup` (can this email sign in, and as whom) and `POST /internal/users/{id}/password` (set a new password). The API keeps its single `account_block` rule, shared by password sign-in, the lookup and the sign-in messages, so they can't disagree.

### Email: Gmail, checked at startup

`SMTP_PROVIDER=gmail` plus `SMTP_USER` and an app password is the whole configuration: host, port 465 and TLS are filled in, the spaces Google shows in the password are ignored, and the From address defaults to the Gmail account (Gmail rewrites any other). The gateway verifies the login when it starts and logs either `SMTP ready` or the reason in plain words (a rejected login explains app passwords). `npm run mail:test -- you@example.com` sends one real message. Any other SMTP server works with `SMTP_HOST` and friends. With nothing set, emails go to the terminal and `gateway/.dev-mail/` so local development needs no account.

### Redis is optional in development, required in production

At startup the gateway tries Redis. In development, if it is configured but unreachable, it logs why and uses in-memory sessions; in production it refuses to start. If Redis dies while running, commands fail immediately (no queueing, 2-second timeout) and the person sees a quick "try again shortly" (503) instead of a hang and a 500. The API's `/health` now fails only when Postgres is down; Redis is reported but doesn't fail it. Empty settings (what Docker Compose passes for anything unset) are treated as not set.

### Dev tooling

`python run.py --all`, run from `backend/`, starts Postgres and Redis (Docker containers, only when nothing is already listening on their ports), applies migrations, then runs the API, the gateway and the web app. `python doctor.py` checks every piece **and every connection between them** (shared keys, CORS, the web app's target, a real sign-in through all layers) and prints the fix for anything wrong. The start-up tooling lives in `backend/` so that the project is exactly three repositories: `frontend/`, `gateway/`, `backend/`.

### Brand and assets

The product is **Invento**. The mark is an isometric box (the universal symbol for stock), generated from one definition by `frontend/scripts/make-icons.mjs` (`npm run icons`) into the SVG favicon (which follows the browser's light and dark setting), `.ico`, PNGs, an iOS icon, maskable app icons and a web manifest. An earlier three-bar mark was dropped because it read as a menu icon. Tests check that every link in `index.html` has a file with the right dimensions.

### Pages

Added a real 404, Terms and Privacy. The legal pages describe what the software actually stores and does and give no company details or legal wording: they need the operator's own review before a public launch.

### Deployment

One public address with HTTPS: the web app is served as static files and `/api` and `/media` are routed to the gateway, so there is no CORS and cookies stay same-site; the API, Postgres and Redis are not public. Interactive API docs are off in production and the services refuse to start with default secrets. **The repositories contain no Dockerfiles, Compose files or web-server config on purpose**: the deployment team containerises each service and builds the AWS setup. The backend README lists build and run commands and every production variable.

## Options considered

| Option | Why not |
| --- | --- |
| Require Redis in every environment | It's exactly what broke local sign-in. Production needs it; development doesn't. |
| Retry Redis forever on the first request | A 17-second wait and a 500 is the worst failure mode. Decide once at startup, then fail fast. |
| Sign-in code as a second factor after the password | A different product decision (it changes every sign-in). Possible later; the code service already supports it. |
| Send reset links instead of codes | A link in an email is a bearer credential that lives in mail archives; a short-lived code typed into the page keeps the flow in one place and reuses the sign-up UI. |
| Separate frontend and gateway origins in production | Needs CORS, cookie domains and SameSite exceptions. One address avoids all three. |
| A three-bar logo | Looks like a hamburger menu. |

## Consequences

- Sign-in works without Redis, but only as well as an in-memory session: gone on restart, and not shared between two gateways. Production must run Redis.
- Anyone with an inbox can sign in to their own account without a password, so the security of an account is the security of its email. Teams that want a password plus a code should ask for the second-factor option.
- Gmail limits sending (about 500 a day for personal accounts) and only sends as the signed-in address. High volume needs a transactional provider.
- Nothing here has been deployed. The deployment team writes the container and AWS setup, then runs the S3 driver against a real bucket and sends real Gmail.
- No Open Graph image yet (it needs a designed 1200 x 630 asset); links preview with the title and description only.

## Action items

1. [x] Sign in by code and password reset: gateway, API and screens, with tests (gateway 131, API 199, web 237 passing)
2. [x] Gmail preset, startup check, `mail:test`
3. [x] Redis fallback in development, fail-fast 503, healthier `/health`
4. [x] `python run.py` and `python doctor.py` in `backend/`
5. [x] Invento name, icon set, 404, Terms and Privacy
6. [x] Deployment handoff: per-service build, run and environment notes (no Docker files in the repositories)
7. [ ] Containerise and deploy on AWS (deployment team); run the S3 driver against a real bucket; send real Gmail
8. [ ] Optional second factor (password plus emailed code) at sign-in
9. [ ] Open Graph image
10. [ ] Legal review of Terms and Privacy
