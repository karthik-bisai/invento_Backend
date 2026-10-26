# ADR-002: A Node authentication gateway in front of the API

**Status:** Accepted
**Date:** 2026-10-23
**Deciders:** Project owner

Diagram: [inventory-architecture.html](inventory-architecture.html). Screens and permissions: [ADR-001](ADR-001-page-architecture.md). Sign-in by code, password reset, Redis fallback and deployment: [ADR-003](ADR-003-email-codes-and-resilience.md).

## Context

The browser held a JWT in `localStorage` and called FastAPI directly. That has three weaknesses: any script on the page can steal the token, a token can't be revoked before it expires, and there was nowhere to put things that must happen outside the database transaction (emailing OTP codes and decisions, storing uploaded files).

New requirements: email-verified sign-up with admin approval, notifications by email and in-app, profile pictures stored locally now and in S3 later, per-user settings, and an authentication gateway in Node.

## Decision

Add `gateway/` (Fastify, TypeScript). **It is the only service the browser talks to.** FastAPI stays the system of record and becomes private.

| Concern | Owner | Why there |
| --- | --- | --- |
| Sessions (HTTP-only cookie, revocable, device list) | Gateway, in Redis | Server-side state gives instant sign-out. The browser never holds a token. |
| CSRF, origin checks, per-address rate limits | Gateway | Edge concerns. The API keeps its own limits too (defence in depth). |
| OTP codes and the email service | Gateway | Side effects belong outside the API's transactions. Only an HMAC of each code is stored. |
| Notification service | Gateway (email) and API (in-app) | In-app notifications live next to the data they describe and survive an email outage. |
| Avatar upload and storage (local / S3) | Gateway | File handling and cloud credentials stay out of the API. |
| Users, profiles, preferences, access requests, stock, RBAC | API | Single source of truth. Roles are enforced on every call, from the database, never from a token claim. |

**How the gateway acts as a user.** It asks FastAPI for a 2-minute token for the session's user (`POST /internal/users/{id}/token`, guarded by a shared `INTERNAL_API_KEY`) and caches it for 60 seconds. The gateway therefore never holds the JWT signing secret. If the API says the user is inactive, the session is ended.

**Sign-up flow.** `signup` creates an unverified, inactive account → emailed OTP → `verify-otp` opens an access request, notifies admins (in-app and, if they opted in, by email) → an admin approves or declines → the applicant is notified in-app and by email. Signing in at any earlier stage routes the user to the step they have left, using a machine-readable code (`email_unverified`, `pending_approval`) the API returns on a correct password.

**Everything else is a guarded pass-through** (`/api/v1/*`): session required, CSRF required for writes, and a blocklist (`/internal`, `/auth/register`, `/auth/login`, `/auth/me/avatar`) so nothing can bypass the OTP flow or point a profile at arbitrary files.

**Products and warehouses.** They were two separate catalogues with no way to relate them in the UI. Stock records already linked them in the API, so the UI now has **Add stock** (choose product, warehouse and counts), a "stocked in" list on each product and a "holds" list on each warehouse.

## Options considered

| Option | Why not |
| --- | --- |
| Keep JWT in `localStorage`, add the features to FastAPI | Doesn't meet the Node gateway requirement, and leaves the token-theft and revocation problems. |
| JWT in an HTTP-only cookie issued by FastAPI | Better, but still not revocable, and email/upload side effects would still live in the API. |
| Move users and sessions into Node (gateway owns the user table) | Splits identity across two services and duplicates bcrypt and RBAC. FastAPI already owns that data and has the tests. |
| Share the JWT signing secret with the gateway | A second place to leak it. Short-lived service tokens avoid that. |
| Send email from FastAPI | Works, but couples request latency to SMTP and spreads side effects across two services. |

## Consequences

- Sign-out, password change and admin deactivation take effect immediately.
- The frontend no longer touches a token; it keeps only a "probably signed in" flag and an in-memory CSRF token.
- One more service to run and deploy. Production needs Redis, SMTP and the same site for app and gateway (SameSite=Lax cookies).
- Every authenticated request costs an extra local hop, and a service-token lookup at most once a minute per user.
- Admin activation from the **Users** page settles a waiting request but does not send the applicant an email; only **Requests → Approve** does.

## Action items

1. [x] Gateway with sessions, CSRF, rate limits, OTP, email, uploads (95 tests)
2. [x] API: profile, preferences, avatar, password, access requests, notifications, internal routes (188 tests)
3. [x] Frontend: verify and pending pages, profile, settings, requests, bell, add stock (172 tests)
4. [x] End-to-end run of the full journey against real FastAPI and Postgres
5. [ ] Run the S3 driver against a real bucket
6. [ ] Containerise and deploy the gateway (deployment team; the repository has no Docker files)
7. [ ] Email the applicant when an admin activates them from the Users page
8. [x] Password reset by email (see ADR-003)
