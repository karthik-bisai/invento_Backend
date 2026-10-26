# ADR-001: Page architecture and completing the missing screens

**Status:** Accepted. Updated 2026-10-26 for the Invento rename, code sign-in, password reset, legal pages and the 404 page ([ADR-002](ADR-002-auth-gateway.md), [ADR-003](ADR-003-email-codes-and-resilience.md)).
**Date:** 2026-10-23
**Deciders:** Project owner

Product name: **Invento**. Diagram: [inventory-architecture.html](inventory-architecture.html)

## Context

The app shipped with Dashboard and Inventory. Products, Warehouses, Reports and Users were placeholder routes. The backend already had CRUD for products, warehouses and inventory. Users had only "create". Reports had no endpoint of its own. Since then the app also gained a public landing page, sign-up with email verification and admin approval, profile and settings screens, and an authentication gateway.

## Decision

### Screens

| Route | Who | What |
| --- | --- | --- |
| `/` | Anyone (signed-in users are sent to `/dashboard`) | Landing page |
| `/login`, `/signup` | Signed out | Sign in with a password, create account |
| `/login/code` | Signed out | Sign in with an emailed 6-digit code |
| `/forgot-password`, `/reset-password` | Signed out | Ask for a reset code, then choose a new password |
| `/signup/verify` | Signed out | Enter the emailed 6-digit code |
| `/signup/pending` | Signed out | "Request sent": what happens next, with a progress timeline |
| `/dashboard`, `/inventory`, `/products` | All roles | Overview, stock by product and warehouse, product catalogue |
| `/warehouses`, `/reports` | Admin, Inventory Manager | Locations and their stock, summary and CSV exports |
| `/users` | Admin | **People** tab and **Requests** tab (approve with a role, or decline with a reason) |
| `/profile`, `/settings` | All roles | Reached from the account menu, not the dock |
| `/terms`, `/privacy` | Anyone | What the software stores and the rules of use, linked from the landing, sign-in and sign-up pages |
| anything else | Anyone | A 404 page with a way home (the overview if signed in) |

### Structure

**One slice per feature**, each under `src/features/<name>/`:

| File | Role |
| --- | --- |
| `hooks.ts` | TanStack Query hooks. Mutations invalidate their own family plus `['inventory']`, because every entity feeds stock rows and KPIs. |
| `<Name>Page.tsx` | Search, server pagination, `DataTable`, error banner with Retry. |
| `<Name>Drawer.tsx` | Create, edit or delete in the right-hand `Drawer`. |

**Navigation:** a floating dock at the bottom holds the six main destinations; the top bar holds the logo, theme toggle, alert bell and account menu (Profile, Settings, Sign out). `App.tsx` builds the routes from `NAV_ITEMS`, so the dock and the router read the same role rules and can't drift. Signed-in pages load on demand to keep the first load small.

**Design:** chalk neutrals with one green accent, Geist for the interface and Instrument Serif for titles and figures, both bundled. Motion is one consistent set (pages ease in, rows stagger, figures count up) and is switched off by the OS "reduce motion" setting or by **Settings → Reduce motion**. **Settings → Compact tables** changes row height through one CSS variable.

**Permissions mirror the API** (the UI hides what the API would refuse; the API stays the authority):

| Screen | Admin | Inventory Manager | Warehouse Staff |
| --- | --- | --- | --- |
| Products | create, edit all, delete | create, edit reorder level | read-only |
| Warehouses | create, edit, delete | create, view | no access |
| Inventory | add stock, edit counts | add stock, edit counts | edit counts |
| Reports | view, export | view, export | no access |
| Users and Requests | full | no access | no access |
| Profile, Settings | own | own | own |

**Products and warehouses are linked by stock records.** Each product can sit in many warehouses with a count in each. **Add stock** on Inventory creates that link, the product drawer lists the warehouses holding it, and the warehouse drawer lists the products it holds.

**Reports need no new endpoint.** They reuse `/inventory/summary` (Redis-cached) and `/inventory/low-stock`; CSV export is client-side.

**Server state:** the browser holds no token. The gateway keeps the session in a cookie ([ADR-002](ADR-002-auth-gateway.md)); the frontend keeps only a "probably signed in" flag and an in-memory CSRF token. Users' preferences (theme, density, motion, notifications) are saved on the account and applied by `PreferencesSync` after sign-in.

## Options considered

| Option | Why not |
| --- | --- |
| Separate `/reports/*` backend endpoints | Duplicates the summary logic and bypasses the cache. Revisit if reports need date ranges or history. |
| Full-page create/edit routes | Inconsistent with the drawer pattern; adds routes without adding value for 2–5 field forms. |
| Hard-delete users | Breaks audit trails later; deactivation ends sign-in and open sessions already. |
| Profile and Settings in the dock | Crowds the dock, which stays for the six work screens. They are personal pages, so they live in the account menu. |

## Consequences

- New entities are cheap: copy the slice shape and add a `NAV_ITEMS` and `PAGES` entry.
- Reports show only current state. Trends and history need stock-movement records (not modelled yet).
- Users cannot be deleted, only deactivated.
- Warehouse Staff cannot add stock to a new warehouse, only change counts that exist. A manager or admin has to create the record first.

## Action items

1. [x] Users, Products, Warehouses and Reports screens
2. [x] Redesign, bottom dock, landing, sign-in and sign-up pages
3. [x] Profile, Settings, Requests tab, notification bell
4. [x] Add stock, and the stocked-in and holds lists
5. [x] Component tests for every screen above
6. [ ] Stock-movement history, if trend reporting is wanted
