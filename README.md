# Ledgerline (5ling)

Compliance and filing management for chartered accountant firms. Python (FastAPI) API on MySQL 8, React web client that installs on Android as an app (PWA) and looks native there: Material Design 3, bottom navigation on phones, navigation rail on desktop, light and dark.

Status: chunks C00 (foundation), C01 (users and roles), C02 (two-step sign-in), C03 (audit log core), C04 (audit verification and viewer), C05 (packages and filing calendars), C06 (due-date engine), C07 (creating clients), C08 (changing, searching and reassigning clients), C09 (tickets and cycle stages), C10 (cycle generator) and C11 (task tickets and the work list) done. See `docs/DEVELOPMENT_PLAN.md`.

## Run it (Debian or Ubuntu, no Docker)

```bash
scripts/dev-setup     # once: installs MySQL, Redis, ClamAV, Mailpit; creates databases, two accounts and a private .env
scripts/dev-up        # every day: starts everything on 127.0.0.1, applies migrations
```

Then create the first Administrator (there is no sign-up page):

```bash
scripts/create-admin --email you@firm.in --name "Your Name"   # hidden password prompt
```

The first time you sign in you will be asked to set up two-step sign-in: scan the picture with an authenticator app (Google Authenticator, Microsoft Authenticator, Aegis) and keep the recovery codes safe.

Web http://localhost:5173 · API docs http://localhost:8000/api/docs · Mail http://localhost:8025

Needs Node 20+ and Python 3.12. `SKIP_CLAMAV=1 scripts/dev-setup` skips the virus scanner until chunk C16.

## Check it

```bash
cd api && . .venv/bin/activate
ruff check . && ruff format --check . && mypy && bandit -q -c pyproject.toml -r app migrations
pytest                                   # uses LL_TEST_DATABASE_URL (and LL_TEST_APP_DATABASE_URL) from .env; export them first
cd ../web && npm run lint && npm run typecheck && npm test && npm run build
```

## Where to look

| Path | What it is |
| --- | --- |
| `docs/Ledgerline-BRD.docx` | Business Requirements Document, draft v1.0 |
| `docs/CHANGE_REQUESTS.md` | Client ID, task tickets, manual state-change emails |
| `docs/DEVELOPMENT_PLAN.md` | 38 chunks, 151 tickets, in build order |
| `docs/tickets.csv` | The same tickets, ready to import as GitHub issues |
| `tools/gen_plan.py` | Generates the plan and CSV. Edit this, not the outputs. |
| `api/` | FastAPI app, migrations (Alembic), tests |
| `web/` | React app (Vite, TypeScript, MUI), service worker, manifest |
| `deploy/nginx/` | Production web server example with security headers |
| `deploy/systemd/` | Daily jobs (timers): audit log check (`python -m app.cli verify-audit`) and cycle generator (`python -m app.cli generate-cycles`) |
| `SECURITY.md` | What is protected now, what is not built yet, production checklist |

## Working rules

- One chunk at a time, in order; each ends with its "Done when" test passing.
- Branch per chunk (`c07-clients-create`), pull request references the LL- ticket IDs.
- Every request is authorised on the server. Every sensitive action writes an audit entry: `audit(db, "thing.done", ...)` (or `security_event(..., db=db)`) before `db.commit()`, so the entry and the change are saved together.
- Already have a `.env` from before C03? Re-run `scripts/dev-setup`: it splits the database account in two (migrations as owner, app with row rights only).
- Never commit `.env`. Never log identifiers (PAN, GSTIN, email, account numbers): the logger redacts them, but do not rely on that.
